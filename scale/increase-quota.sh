#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $0 [-c CLUSTER_YAML] [-p PROJECT] [-r REGION=VCPUS]... [-e EMAIL] [-n]

Raises the GCP vCPU quotas for the machine families used by the gcp_nodes in
a CHIA cluster.yaml: vCPUs per VM family in each region, and vCPUs across all
regions (the sum of the regional targets). Only limits below the target are
requested.

  -c CLUSTER_YAML  cluster config (default: cluster.yaml next to this script)
  -r REGION=VCPUS  regional target, repeatable
                   (default: -r us-west4=100 -r us-central1=100)
  -p PROJECT       GCP project (default: gcloud config, then this VM's project,
                   then gcp_nodes.project)
  -e EMAIL         contact email, required by GCP for any increase (default:
                   QUOTA_EMAIL, then the gcloud account unless it is a
                   service account, then the project's devstar####@gcplab.me
                   lab user)
  -n               dry run: show what would be requested

JUSTIFICATION in the environment overrides the request justification.
EOF
}

die() { echo "$*" >&2; exit 1; }

CLUSTER_YAML="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cluster.yaml"
PROJECT=""
declare -A REGION_TARGETS
EMAIL="${QUOTA_EMAIL:-}"
DRY_RUN=false

while getopts "c:p:r:e:nh" opt; do
    case "$opt" in
        c) CLUSTER_YAML="$OPTARG" ;;
        p) PROJECT="$OPTARG" ;;
        r) [[ "$OPTARG" =~ ^([a-z]+-[a-z]+[0-9]+)=([0-9]+)$ ]] ||
               die "-r needs REGION=VCPUS, e.g. us-west4=100, not $OPTARG"
           REGION_TARGETS[${BASH_REMATCH[1]}]="${BASH_REMATCH[2]}" ;;
        e) EMAIL="$OPTARG" ;;
        n) DRY_RUN=true ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
done

[[ -f "$CLUSTER_YAML" ]] || die "No such file: $CLUSTER_YAML"
if [[ -z "${REGION_TARGETS[*]:-}" ]]; then
    REGION_TARGETS=([us-west4]=100 [us-central1]=100)
fi

{
    read -r CLUSTER
    read -r YAML_PROJECT
    mapfile -t NODES
} < <(python3 -I - "$CLUSTER_YAML" <<'EOF'
import os, re, sys, yaml
text = re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), ""), open(sys.argv[1]).read())
raw = yaml.safe_load(text)
gcp = raw.get("gcp_nodes") or {}
print(raw.get("cluster_name", "default"))
print(gcp.get("project", ""))
zone = gcp.get("zone", "us-central1-a")
for name, cfg in gcp.items():
    if isinstance(cfg, dict) and cfg.get("count"):
        print(name, cfg["machine_type"], cfg["count"], cfg.get("zone", zone))
EOF
)

(( ${#NODES[@]} > 0 )) || { echo "No gcp_nodes with count > 0 in $CLUSTER_YAML"; exit 0; }

[[ -n "$PROJECT" ]] || PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
[[ -n "$PROJECT" ]] || PROJECT="$(curl -sf -m 3 -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/project/project-id || true)"
[[ -n "$PROJECT" ]] || PROJECT="$YAML_PROJECT"
[[ -n "$PROJECT" ]] || die "No GCP project: pass -p PROJECT"
if [[ -n "$YAML_PROJECT" && "$YAML_PROJECT" != "$PROJECT" ]]; then
    echo "WARNING: using project $PROJECT, but $CLUSTER_YAML launches into $YAML_PROJECT" >&2
fi

if [[ -z "$EMAIL" ]]; then
    account="$(gcloud config get-value account 2>/dev/null || true)"
    [[ "$account" == *.gserviceaccount.com ]] || EMAIL="$account"
fi
if [[ -z "$EMAIL" ]]; then
    lab_users="$(gcloud projects get-iam-policy "$PROJECT" --flatten='bindings[].members' \
        --format='value(bindings.members)' 2>/dev/null |
        grep -oE '^user:devstar[0-9]+@gcplab\.me$' | sort -u | cut -d: -f2 || true)"
    if [[ -n "$lab_users" && "$(wc -l <<<"$lab_users")" -eq 1 ]]; then
        EMAIL="$lab_users"
    fi
fi
if [[ -n "$EMAIL" ]]; then
    echo "Contact email: $EMAIL"
fi

JUSTIFICATION="${JUSTIFICATION:-CHIA cluster '$CLUSTER' needs more Compute Engine vCPUs for its workers}"

declare -A FAMILIES
for line in "${NODES[@]}"; do
    read -r name machine count zone <<<"$line"
    family="${machine%%-*}"
    FAMILIES[${family^^}]=1
    echo "$name: ${count}x $machine in $zone"
    if [[ -z "${REGION_TARGETS[${zone%-*}]:-}" ]]; then
        echo "WARNING: $name is in ${zone%-*}, which has no -r target" >&2
    fi
done

GLOBAL_TARGET=0
for region in "${!REGION_TARGETS[@]}"; do
    GLOBAL_TARGET=$(( GLOBAL_TARGET + REGION_TARGETS[$region] ))
done

echo
echo "Project $PROJECT: families ${!FAMILIES[*]}, all regions $GLOBAL_TARGET vCPUs"
echo

current_limit() {
    local quota_id="$1" dims="$2"
    gcloud beta quotas info describe "$quota_id" --service=compute.googleapis.com \
        --project="$PROJECT" --format=json | python3 -I -c '
import json, sys
want = dict(kv.split("=") for kv in sys.argv[1].split(",") if kv)
infos = json.load(sys.stdin).get("dimensionsInfos", [])
print(next((i.get("details", {}).get("value", 0) for i in infos if i.get("dimensions", {}) == want), 0))
' "$dims"
}

PLANNED=()
plan() {
    local id="$1" quota_id="$2" value="$3" dims="${4:-}"
    local label="$quota_id${dims:+ ($dims)}"
    local current
    current="$(current_limit "$quota_id" "$dims")"

    if (( current >= value )); then
        echo "OK       $label: limit $current >= $value"
    else
        echo "REQUEST  $label: limit $current -> $value"
        PLANNED+=("$id|$quota_id|$value|$dims")
    fi
}

plan "$CLUSTER-global-cpus" CPUS-ALL-REGIONS-per-project "$GLOBAL_TARGET"
for region in "${!REGION_TARGETS[@]}"; do
    for family in "${!FAMILIES[@]}"; do
        plan "$CLUSTER-$region-${family,,}-cpus" CPUS-PER-VM-FAMILY-per-project-region \
            "${REGION_TARGETS[$region]}" "region=$region,vm_family=$family"
    done
done

if (( ${#PLANNED[@]} == 0 )) || $DRY_RUN; then
    exit 0
fi
[[ -n "$EMAIL" ]] || die "
GCP needs a contact email to increase quota, and the gcloud account is a
service account. Rerun with -e you@example.com (or set QUOTA_EMAIL)."

for entry in "${PLANNED[@]}"; do
    IFS='|' read -r id quota_id value dims <<<"$entry"
    args=(
        "$id"
        --project="$PROJECT"
        --service=compute.googleapis.com
        --quota-id="$quota_id"
        --preferred-value="$value"
        --justification="$JUSTIFICATION"
        --email="$EMAIL"
        --allow-missing
        --quiet
    )
    if [[ -n "$dims" ]]; then args+=(--dimensions="$dims"); fi
    gcloud beta quotas preferences update "${args[@]}"
done

echo
gcloud beta quotas preferences list --project="$PROJECT" \
    --format="table(name.basename(),quotaId,dimensions,quotaConfig.preferredValue,quotaConfig.grantedValue,reconciling)"
