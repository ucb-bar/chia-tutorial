#!/usr/bin/env bash
# CHIA tutorial: one-time GCP setup. Run it in Google Cloud Shell
# (https://shell.cloud.google.com), which is already signed in to your account.
#
# Uses/creates a GCP project with billing, enables the APIs the tutorial needs,
# requests vCPU quota for CHIA clusters, and starts one VM:
#   chia-dev    c2d-standard-8  (AMD Milan, 8 vCPU / 32 GB) - your dev machine / CHIA head
# with Docker, conda (Miniforge) and the Antigravity CLI (agy), signed in. More machines are
# brought up later by CHIA clusters.
#
# Usage:
#   ./setup_gcp.sh [--project ID] [--billing-account ID]
#   ./setup_gcp.sh --teardown     # delete the VM
#   ./setup_gcp.sh --purge        # delete the VM (and optionally the project), remove the
#                                 # tutorial's files from Cloud Shell
#
# Project: --project, else the one this script used last time, else Cloud Shell's
# current project, else a new one. Safe to re-run: existing project, VM and installs
# are reused.
#
# Optional environment overrides:
#   DEV_ZONE=us-west4-c   TUT_HOME=~/.chia-tutorial
#   QUOTA_REGIONS="us-west4=100 us-central1=100"   vCPU targets per region
#   QUOTA_FAMILIES="C4"                             VM families to raise them for
set -euo pipefail

usage() { grep '^#   \./setup_gcp' "$0" | sed 's/^#   /usage: /'; exit 1; }
TEARDOWN="" PURGE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --project)         PROJECT_ID="$2"; shift 2 ;;
        --billing-account) BILLING_ACCOUNT="$2"; shift 2 ;;
        --teardown)        TEARDOWN=1; shift ;;
        --purge)           TEARDOWN=1 PURGE=1; shift ;;
        *) usage ;;
    esac
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null || die "gcloud not found. Run this script in Google Cloud Shell: https://shell.cloud.google.com"
ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
[ -n "$ACCOUNT" ] || die "gcloud isn't signed in. Run this script in Google Cloud Shell, or run 'gcloud auth login' first."

TUT_HOME="${TUT_HOME:-$HOME/.chia-tutorial}"
STATE="$TUT_HOME/state-$ACCOUNT.sh"
mkdir -p "$TUT_HOME"
# shellcheck disable=SC1090
if [ -z "${PROJECT_ID:-}" ] && [ -f "$STATE" ]; then
    . "$STATE"     # remembers this account's PROJECT_ID between runs
fi

DEV_ZONE="${DEV_ZONE:-us-west4-c}"
DEV_VM=chia-dev    DEV_TYPE=c2d-standard-8   DEV_CPU="AMD Milan"
IMAGE=(--image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud)

# --- Teardown / purge ---------------------------------------------------------
if [ -n "$TEARDOWN" ]; then
    if [ -n "${PROJECT_ID:-}" ]; then
        log "Deleting $DEV_VM in $PROJECT_ID"
        gcloud compute instances delete "$DEV_VM" --zone "$DEV_ZONE" --project "$PROJECT_ID" --quiet || true
    elif [ -z "$PURGE" ]; then
        die "no project recorded in $STATE"
    fi
    if [ -z "$PURGE" ]; then
        echo "To delete the whole project too: gcloud projects delete $PROJECT_ID"
        echo "To also remove the tutorial's files from Cloud Shell: $0 --purge"
        exit 0
    fi
    if [ -n "${PROJECT_ID:-}" ]; then
        read -r -p "Also delete the whole project $PROJECT_ID? [y/N] " answer
        case "$answer" in
            [yY]*) gcloud projects delete "$PROJECT_ID" --quiet || true ;;
            *) echo "Keeping project $PROJECT_ID." ;;
        esac
    fi
    log "Removing $TUT_HOME"
    rm -rf "$TUT_HOME"
    exit 0
fi

echo "Using gcloud signed in as $ACCOUNT"

# --- 1. Project + billing ----------------------------------------------------
if [ -z "${PROJECT_ID:-}" ]; then
    PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
fi
if [ -z "${PROJECT_ID:-}" ]; then
    PROJECT_ID="chia-tut-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
    log "Creating project $PROJECT_ID"
    gcloud projects create "$PROJECT_ID" --name="CHIA tutorial" \
        || die "project creation failed (your organization may require a folder or block it; re-run with --project <existing project>)"
fi
echo "Using project $PROJECT_ID"
echo "PROJECT_ID=$PROJECT_ID" > "$STATE"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

if [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" != "True" ]; then
    if [ -z "${BILLING_ACCOUNT:-}" ]; then
        accounts="$(gcloud billing accounts list --filter=open=true --format='value(name.basename())')"
        case "$(printf '%s\n' "$accounts" | grep -c .)" in
            0) die "no open billing account. Create one at https://console.cloud.google.com/billing (or redeem the tutorial credits), then re-run." ;;
            1) BILLING_ACCOUNT="$accounts" ;;
            *) gcloud billing accounts list --filter=open=true
               die "several billing accounts found; re-run with --billing-account <ACCOUNT_ID>" ;;
        esac
    fi
    log "Linking billing account $BILLING_ACCOUNT"
    gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
fi

# --- 2. APIs -----------------------------------------------------------------
# compute: VMs. cloudaicompanion: Gemini Code Assist (what agy signs in against).
# aiplatform: Vertex AI (Gemini via OpenCode, the alternative CHIA backend).
# cloudquotas: requesting the vCPU quota increases below.
log "Enabling APIs (the first time this can take several minutes with no output; please wait)"
until out="$(gcloud services enable compute.googleapis.com cloudaicompanion.googleapis.com \
                 aiplatform.googleapis.com cloudquotas.googleapis.com \
                 --project "$PROJECT_ID" 2>&1)"; do
    case "$out" in
        *UREQ_TOS_NOT_ACCEPTED*)
            # New accounts must accept the Google Cloud terms once, in the browser.
            cat <<EOF

This account hasn't accepted the Google Cloud Terms of Service yet. Signed in as
$ACCOUNT, open this page, accept the terms, and come back:

  https://console.developers.google.com/terms/cloud

EOF
            read -r -p "Press Enter once you've accepted them: " _ ;;
        *) printf '%s\n' "$out" >&2; die "enabling APIs failed" ;;
    esac
done

# --- 3. Pre-flight: network, machine type ------------------------------------
# New projects get a default network (with SSH/internal firewall rules) once the
# Compute API is on, but asynchronously: it can appear while we create our own.
if ! gcloud compute networks describe default >/dev/null 2>&1; then
    log "Creating the default network + SSH firewall rule"
    if out="$(gcloud compute networks create default --subnet-mode=auto 2>&1)"; then
        gcloud compute firewall-rules create default-allow-ssh --network=default \
            --allow=tcp:22 --source-ranges=0.0.0.0/0
        gcloud compute firewall-rules create default-allow-internal --network=default \
            --allow=tcp,udp,icmp --source-ranges=10.128.0.0/9
    elif gcloud compute networks describe default >/dev/null 2>&1; then
        echo "GCP created the default network itself in the meantime"
    else
        printf '%s\n' "$out" >&2; die "creating the default network failed"
    fi
fi

gcloud compute machine-types describe "$DEV_TYPE" --zone "$DEV_ZONE" >/dev/null 2>&1 || die \
    "$DEV_TYPE is not offered in $DEV_ZONE. Zones that have it: $(gcloud compute machine-types list \
     --filter="name=$DEV_TYPE" --format='value(zone)' | tr '\n' ' ')"

# --- 4. vCPU quota increases --------------------------------------------------
# Same requests as scale/increase-quota.sh: vCPUs per VM family in each region, and
# vCPUs across all regions (the sum of the regional targets). C4 is what the
# tutorial's CHIA clusters use (scale/cluster.yaml). Only limits below the target
# are requested. Google reviews requests asynchronously, so this doesn't wait for
# approval. (Cloud Shell's gcloud has the beta commands preinstalled.)
QUOTA_REGIONS="${QUOTA_REGIONS:-us-west4=100 us-central1=100}"
QUOTA_FAMILIES="${QUOTA_FAMILIES:-C4}"

quota_limit() {  # quota-id region family -> current limit ("" region/family = none)
    local out           # on failure, prints gcloud's error and returns non-zero
    out="$(gcloud beta quotas info describe "$1" --service=compute.googleapis.com \
        --project="$PROJECT_ID" --flatten=dimensionsInfos \
        --format='csv[no-heading](dimensionsInfos.dimensions.region,dimensionsInfos.dimensions.vm_family,dimensionsInfos.details.value)' \
        2>&1 </dev/null)" || { printf '%s\n' "$out"; return 1; }
    printf '%s\n' "$out" | awk -F, -v r="$2" -v f="$3" '$1==r && $2==f { v=$3 } END { print v+0 }'
}

request_quota() {  # quota-id value region family
    local id=$1 value=$2 region=$3 family=$4 current pref label dims=() tries=0
    label="$id${region:+ ($region${family:+, $family})}"
    until current="$(quota_limit "$id" "$region" "$family")"; do
        case "$current" in
            # A just-enabled API takes a minute or two to start answering.
            *SERVICE_DISABLED*|*"has not been used"*|*"it is disabled"*)
                tries=$((tries + 1))
                if [ "$tries" -lt 12 ]; then
                    echo "  (the Cloud Quotas API is still switching on; retrying in 10 s)"
                    sleep 10
                    continue
                fi ;;
        esac
        echo "WARNING: couldn't read the current limit for $label:"
        printf '%s\n' "$current" | tail -5 | sed 's/^/    /'
        echo "  Request it by hand at https://console.cloud.google.com/iam-admin/quotas"
        return 0
    done
    if [ "$current" -ge "$value" ]; then
        echo "OK       $label: limit $current >= $value"
        return
    fi
    echo "REQUEST  $label: limit $current -> $value"
    pref="chia-tut${region:+-$region}$(printf '%s' "${family:+-$family}" | tr 'A-Z' 'a-z')-cpus"
    [ -n "$region" ] && dims=(--dimensions="region=$region${family:+,vm_family=$family}")
    gcloud beta quotas preferences update "$pref" --project="$PROJECT_ID" \
        --service=compute.googleapis.com --quota-id="$id" --preferred-value="$value" \
        ${dims[@]+"${dims[@]}"} --email="$ACCOUNT" --allow-missing --quiet \
        --justification="CHIA tutorial: Compute Engine vCPUs for the participant's CHIA cluster workers" \
        >/dev/null </dev/null \
        || echo "WARNING: the request for $label failed; request it at https://console.cloud.google.com/iam-admin/quotas"
}

log "Requesting vCPU quota (regions: $QUOTA_REGIONS; families: $QUOTA_FAMILIES)"
total=0
for target in $QUOTA_REGIONS; do
    region="${target%%=*}" value="${target#*=}"
    total=$((total + value))
    for family in $QUOTA_FAMILIES; do
        request_quota CPUS-PER-VM-FAMILY-per-project-region "$value" "$region" "$family"
    done
done
request_quota CPUS-ALL-REGIONS-per-project "$total" "" ""
echo "Requests are reviewed by Google; check their status at https://console.cloud.google.com/iam-admin/quotas"

# --- 5. VM -------------------------------------------------------------------
STARTUP="$TUT_HOME/startup.sh"
cat > "$STARTUP" <<'EOF'
#!/bin/bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
EOF

if gcloud compute instances describe "$DEV_VM" --zone "$DEV_ZONE" >/dev/null 2>&1; then
    echo "$DEV_VM already exists"
else
    # chia-dev gets the cloud-platform scope so CHIA running on it can create more VMs.
    log "Creating $DEV_VM ($DEV_TYPE, $DEV_CPU) in $DEV_ZONE"
    gcloud compute instances create "$DEV_VM" --zone "$DEV_ZONE" --machine-type "$DEV_TYPE" \
        --min-cpu-platform="$DEV_CPU" "${IMAGE[@]}" \
        --boot-disk-type=pd-balanced --boot-disk-size=200GB \
        --metadata-from-file=startup-script="$STARTUP" --scopes=cloud-platform
fi

# --- 6. Per-user setup on the VM (docker group, conda) ----------------------
vm_ssh() { gcloud compute ssh "$DEV_VM" --zone "$DEV_ZONE" --quiet --command "${1:-bash -s}"; }

# The docker command appears partway through Docker's install, before its group
# and service exist, so wait for those instead.
log "Waiting for SSH + Docker on $DEV_VM (the VM installs Docker on first boot, a few minutes)"
tries=0
until vm_ssh "getent group docker && systemctl is-active --quiet docker" >/dev/null 2>&1 </dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 60 ] || die "Docker still isn't running on $DEV_VM after 10 minutes. See the install log with:
  gcloud compute ssh $DEV_VM --zone $DEV_ZONE --command 'sudo journalctl -u google-startup-scripts --no-pager | tail -50'"
    sleep 10
done
vm_ssh 'sudo usermod -aG docker "$USER"' </dev/null
echo "$DEV_VM: $(vm_ssh "lscpu | sed -n 's/^Model name: *//p'" </dev/null)"

log "Installing conda (Miniforge) on $DEV_VM"
vm_ssh <<'EOF'
set -e
if [ ! -x ~/miniforge3/bin/conda ]; then
    curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh
    bash /tmp/miniforge.sh -b -p ~/miniforge3 >/dev/null
    ~/miniforge3/bin/conda init bash >/dev/null
fi
mkdir -p ~/.gemini
~/miniforge3/bin/conda --version
EOF

# --- 7. Gemini / Antigravity -------------------------------------------------
AGY_TOKEN=.gemini/antigravity-cli/antigravity-oauth-token
if vm_ssh "test -s ~/$AGY_TOKEN" </dev/null; then
    echo "agy is already signed in on $DEV_VM (~/$AGY_TOKEN exists)"
else
    # The login lands in ~/.gemini on $DEV_VM, which CHIA mounts into its LLM containers.
    log "Installing agy on $DEV_VM"
    vm_ssh '[ -x ~/.local/bin/agy ] || curl -fsSL https://antigravity.google/cli/install.sh | bash >/dev/null' </dev/null
    log "Signing agy in on $DEV_VM"
    cat <<EOF
agy will print a sign-in URL. Open it, pick $ACCOUNT, then choose project
$PROJECT_ID and location 'global'. Once agy shows its prompt you're signed in:
type /exit (or press Ctrl-C) to leave agy and let this script continue.

EOF
    # Leaving agy with Ctrl-C exits non-zero; the token check below is what counts.
    gcloud compute ssh "$DEV_VM" --zone "$DEV_ZONE" --quiet -- -t '~/.local/bin/agy' || true
    vm_ssh "test -s ~/$AGY_TOKEN" </dev/null \
        || echo "WARNING: no agy login found in ~/.gemini on $DEV_VM; re-run this script to try again."
fi

# --- Done ---------------------------------------------------------------------
log "Done"
cat <<EOF
Project:  $PROJECT_ID
VM:       $DEV_VM ($DEV_ZONE)

SSH to your dev machine (from Cloud Shell):   gcloud compute ssh $DEV_VM --zone $DEV_ZONE
Tear down the development machine:            $0 --teardown
Remove everything (VM, tutorial files):       $0 --purge
EOF
