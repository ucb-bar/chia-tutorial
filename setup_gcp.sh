#!/usr/bin/env bash
# CHIA tutorial: one-time GCP setup. Run it in Google Cloud Shell
# (https://shell.cloud.google.com), which is already signed in to your account.
#
# Uses/creates a GCP project with billing, enables the APIs the tutorial needs,
# requests vCPU quota for CHIA clusters, and starts these VMs (all c4-highcpu-32,
# 32 vCPU / 64 GB):
#   chia-dev                     your dev machine / CHIA head: Docker, conda (Miniforge),
#                                the chia-tutorial repo + its conda env, the Antigravity
#                                CLI (agy, signed in), and an SSH key (~/.ssh/chia-key)
#   chia-worker-1 .. -5          workers: Docker, conda, the chia-tutorial repo + its conda env, and
#                                chia-dev's key in authorized_keys, so chia-dev can ssh in
# chia-dev and workers 1-2 go in europe-west1, workers 3-5 in europe-west4. Every VM
# also starts pulling ghcr.io/ucb-bar/chia-chisel-build:latest in the background.
#
# Usage:
#   ./setup_gcp.sh [--project ID] [--billing-account ID] [--from-step N]
#   ./setup_gcp.sh --teardown     # delete the VMs
#   ./setup_gcp.sh --purge        # delete the VMs (and optionally the project), remove the
#                                 # tutorial's files from Cloud Shell
#
# Project: --project, else the one this script used last time, else Cloud Shell's
# current project, else a new one. Safe to re-run: existing project, VM and installs
# are reused.
#
# Steps: 1 project + billing, 2 APIs, 3 network + machine-type check, 4 quota requests,
# 5 VMs, 6 VM setup, 7 agy sign-in, 8 workers' SSH access. --from-step N skips steps
# 1 to N-1: the project must already exist, and from step 6 on so must the VMs.
#
# Optional environment overrides:
#   DEV_REGION=europe-west1   TUT_HOME=~/.chia-tutorial   (Google picks the VMs' zones)
#   WORKER_REGIONS="europe-west1=2 europe-west4=3"   chia-worker VMs per region
#   QUOTA_REGIONS="europe-west1=100 europe-west4=100"   vCPU targets per region
#   QUOTA_FAMILIES="C4"                             VM families to raise them for
#   QUOTA_HDB_GB=1000                               Hyperdisk Balanced GB per region
#   BOOT_DISK_GB=150                                boot disk size of every VM
set -euo pipefail

usage() { grep '^#   \./setup_gcp' "$0" | sed 's/^#   /usage: /'; exit 1; }
TEARDOWN="" PURGE="" FROM_STEP=1
while [ $# -gt 0 ]; do
    case "$1" in
        --project)         PROJECT_ID="$2"; shift 2 ;;
        --billing-account) BILLING_ACCOUNT="$2"; shift 2 ;;
        --from-step)       FROM_STEP="$2"; shift 2 ;;
        --teardown)        TEARDOWN=1; shift ;;
        --purge)           TEARDOWN=1 PURGE=1; shift ;;
        *) usage ;;
    esac
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
case "$FROM_STEP" in [1-8]) ;; *) die "--from-step takes a step number from 1 to 8" ;; esac
do_step() { [ "$FROM_STEP" -le "$1" ]; }  # step number -> true unless --from-step skips it

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

DEV_REGION="${DEV_REGION:-europe-west1}"
# C4 rejects --min-cpu-platform, so the CPU generation isn't pinned.
DEV_VM=chia-dev    DEV_TYPE=c4-highcpu-32
WORKER_REGIONS="${WORKER_REGIONS:-europe-west1=2 europe-west4=3}"   WORKER_TYPE=c4-highcpu-32
# Workers are numbered across the regions in order: chia-worker-1..2 in the first,
# chia-worker-3..5 in the second. region_of_worker maps a name back to its region.
WORKERS="" i=0
for spec in $WORKER_REGIONS; do
    for _ in $(seq 1 "${spec#*=}"); do i=$((i + 1)); WORKERS="$WORKERS chia-worker-$i"; done
done
region_of_worker() {  # chia-worker-N -> region
    local n="${1#chia-worker-}" i=0 spec
    for spec in $WORKER_REGIONS; do
        i=$((i + ${spec#*=}))
        [ "$n" -le "$i" ] && { echo "${spec%%=*}"; return; }
    done
}
CHISEL_IMAGE=ghcr.io/ucb-bar/chia-chisel-build:latest
LOGS="$TUT_HOME/logs"
# The VMs are created by region and Google picks the zone, so look zones up (this
# also finds a chia-dev made by an older version of this script in another zone).
zone_of() {  # vm name -> its zone, or nothing if it doesn't exist
    gcloud compute instances list --project "$PROJECT_ID" --filter="name~^$1\$" \
        --format='value(zone.basename())' 2>/dev/null </dev/null | head -1
}
vm_zone() { zone_of "$DEV_VM"; }
IMAGE=(--image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud)

# --- Teardown / purge ---------------------------------------------------------
if [ -n "$TEARDOWN" ]; then
    if [ -n "${PROJECT_ID:-}" ]; then
        # One list call finds every tutorial VM and its zone. Then one delete per zone
        # (gcloud deletes the VMs in a call in parallel), with all zones at once.
        found="$(gcloud compute instances list --project "$PROJECT_ID" \
            --filter="name~^($(echo $DEV_VM $WORKERS | tr ' ' '|'))\$" \
            --format='value(name,zone.basename())' 2>/dev/null </dev/null || true)"
        for vm in $DEV_VM $WORKERS; do
            printf '%s\n' "$found" | awk -v n="$vm" '$1 == n { f = 1 } END { exit !f }' \
                || echo "No $vm found in $PROJECT_ID"
        done
        pids=""
        for zone in $(printf '%s\n' "$found" | awk '{ print $2 }' | sort -u); do
            vms="$(printf '%s\n' "$found" | awk -v z="$zone" '$2 == z { print $1 }' | tr '\n' ' ')"
            log "Deleting ${vms}($zone) in $PROJECT_ID"
            # shellcheck disable=SC2086
            gcloud compute instances delete $vms --zone "$zone" --project "$PROJECT_ID" --quiet </dev/null &
            pids="$pids $!"
        done
        for pid in $pids; do wait "$pid" || true; done
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
[ "$FROM_STEP" -eq 1 ] || echo "Starting at step $FROM_STEP (--from-step); skipping the steps before it"

# --- 1. Project + billing ----------------------------------------------------
# Finding the project always runs, since every later step needs it; creating one
# and linking billing are step 1.
if [ -z "${PROJECT_ID:-}" ]; then
    PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
fi
if [ -z "${PROJECT_ID:-}" ]; then
    do_step 1 || die "no project to continue with; pass --project ID, or run without --from-step to create one"
    PROJECT_ID="chia-tut-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
    log "Creating project $PROJECT_ID"
    gcloud projects create "$PROJECT_ID" --name="CHIA tutorial" \
        || die "project creation failed (your organization may require a folder or block it; re-run with --project <existing project>)"
fi
echo "Using project $PROJECT_ID"
echo "PROJECT_ID=$PROJECT_ID" > "$STATE"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

if do_step 1 && [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" != "True" ]; then
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
APIS="compute.googleapis.com cloudaicompanion.googleapis.com aiplatform.googleapis.com cloudquotas.googleapis.com"
if do_step 2; then
    # Enabling counts against Service Usage's per-project limit on changes (120 a
    # minute, shared by everyone using the project), even for APIs that are already
    # on. Listing is a read, which has its own limit, so only enable what's missing.
    # If the list fails (e.g. terms not accepted yet), every API counts as missing.
    enabled="$(gcloud services list --enabled --project "$PROJECT_ID" \
                   --format='value(config.name)' 2>/dev/null </dev/null || true)"
    missing=""
    for api in $APIS; do
        printf '%s\n' "$enabled" | grep -qxF "$api" || missing="$missing $api"
    done
    if [ -z "$missing" ]; then
        echo "APIs already enabled: $APIS"
    else
        log "Enabling APIs:$missing (the first time this can take several minutes with no output; please wait)"
        # shellcheck disable=SC2086
        until out="$(gcloud services enable $missing --project "$PROJECT_ID" 2>&1)"; do
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
    fi
fi

# --- 3. Pre-flight: network, machine type ------------------------------------
# New projects get a default network (with SSH/internal firewall rules) once the
# Compute API is on, but asynchronously: it can appear while we create our own.
if do_step 3; then
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

    for region in $DEV_REGION $(for spec in $WORKER_REGIONS; do echo "${spec%%=*}"; done | sort -u); do
        [ -n "$(gcloud compute machine-types list --filter="name=$DEV_TYPE AND zone~^$region-" \
                  --format='value(zone)' 2>/dev/null)" ] || die \
            "$DEV_TYPE is not offered in $region. Regions that have it: $(gcloud compute machine-types list \
             --filter="name=$DEV_TYPE" --format='value(zone.basename())' | sed 's/-[a-z]$//' | sort -u | tr '\n' ' ')"
    done
fi

# --- 4. vCPU quota increases --------------------------------------------------
# Same requests as scale/increase-quota.sh: vCPUs per VM family in each region, and
# vCPUs across all regions (the sum of the regional targets). C4 is what the
# tutorial's CHIA clusters use (scale/cluster.yaml). Only limits below the target
# are requested. Google reviews requests asynchronously, so this doesn't wait for
# approval. (Cloud Shell's gcloud has the beta commands preinstalled.)
# chia-dev + 2 workers in europe-west1 and 3 workers in europe-west4 are 96 C4 vCPUs each.
QUOTA_REGIONS="${QUOTA_REGIONS:-europe-west1=100 europe-west4=100}"
QUOTA_FAMILIES="${QUOTA_FAMILIES:-C4}"
# Every VM's boot disk is Hyperdisk Balanced (C4 needs it), which has its own storage
# quota: new projects get 500 GB per region. 3 x 150 GB fits in that; 3 x 200 GB didn't.
QUOTA_HDB_GB="${QUOTA_HDB_GB:-1000}"
BOOT_DISK_GB="${BOOT_DISK_GB:-150}"

quota_limit() {  # quota-id region family -> current limit ("" region/family = none)
    local out           # on failure, prints gcloud's error and returns non-zero
    out="$(gcloud beta quotas info describe "$1" --service=compute.googleapis.com \
        --project="$PROJECT_ID" --billing-project="$PROJECT_ID" --flatten=dimensionsInfos \
        --format='csv[no-heading](dimensionsInfos.dimensions.region,dimensionsInfos.dimensions.vm_family,dimensionsInfos.details.value)' \
        2>&1 </dev/null)" || { printf '%s\n' "$out"; return 1; }
    printf '%s\n' "$out" | awk -F, -v r="$2" -v f="$3" '$1==r && $2==f { v=$3 } END { print v+0 }'
}

request_quota() {  # quota-id value region family [name-suffix]
    local id=$1 value=$2 region=$3 family=$4 kind=${5:-cpus} current pref label dims=() tries=0
    label="$id${region:+ ($region${family:+, $family})}"
    until current="$(quota_limit "$id" "$region" "$family")"; do
        case "$current" in
            # A just-enabled API takes a minute or two to start answering.
            *SERVICE_DISABLED*|*"has not been used"*|*"it is disabled"*)
                tries=$((tries + 1))
                if [ "$tries" -lt 12 ]; then
                    [ "$tries" -gt 1 ] || echo "  (the Cloud Quotas API is still switching on; retrying for up to 2 minutes)"
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
    pref="chia-tut${region:+-$region}$(printf '%s' "${family:+-$family}" | tr 'A-Z' 'a-z')-$kind"
    [ -n "$region" ] && dims=(--dimensions="region=$region${family:+,vm_family=$family}")
    gcloud beta quotas preferences update "$pref" --project="$PROJECT_ID" \
        --billing-project="$PROJECT_ID" --service=compute.googleapis.com --quota-id="$id" --preferred-value="$value" \
        ${dims[@]+"${dims[@]}"} --email="$ACCOUNT" --allow-missing --quiet \
        --justification="CHIA tutorial: Compute Engine capacity for the participant's CHIA cluster workers" \
        >"$TUT_HOME/quota-$pref.log" 2>&1 </dev/null \
        || { echo "WARNING: the request for $label failed; request it at https://console.cloud.google.com/iam-admin/quotas"
             tail -5 "$TUT_HOME/quota-$pref.log" | sed 's/^/    /'; }
}

if do_step 4; then
    log "Requesting quota (vCPUs: $QUOTA_REGIONS, families $QUOTA_FAMILIES; Hyperdisk: $QUOTA_HDB_GB GB per region)"
    total=0
    for target in $QUOTA_REGIONS; do
        region="${target%%=*}" value="${target#*=}"
        total=$((total + value))
        for family in $QUOTA_FAMILIES; do
            request_quota CPUS-PER-VM-FAMILY-per-project-region "$value" "$region" "$family"
        done
        request_quota HDB-TOTAL-GB-per-project-region "$QUOTA_HDB_GB" "$region" "" disk
    done
    request_quota CPUS-ALL-REGIONS-per-project "$total" "" ""
    echo "Requests are reviewed by Google; check their status at https://console.cloud.google.com/iam-admin/quotas"
fi

# --- 5. VMs ------------------------------------------------------------------
mkdir -p "$LOGS"
if do_step 5; then
    STARTUP="$TUT_HOME/startup.sh"
    cat > "$STARTUP" <<'EOF'
#!/bin/bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
EOF

    # bulk create takes a region and places VMs in whichever zone has capacity.
    # chia-dev and each region's workers are created at the same time, by parallel calls.
    DEV_ZONE="$(vm_zone)" dev_pid="" CREATE_JOBS=""
    if [ -n "$DEV_ZONE" ]; then
        echo "$DEV_VM already exists in $DEV_ZONE"
    else
        # chia-dev gets the cloud-platform scope so CHIA running on it can create more VMs.
        log "Creating $DEV_VM ($DEV_TYPE) in $DEV_REGION (Google picks the zone)"
        gcloud compute instances bulk create --predefined-names="$DEV_VM" --count=1 \
            --region="$DEV_REGION" --machine-type="$DEV_TYPE" "${IMAGE[@]}" \
            --boot-disk-type=hyperdisk-balanced --boot-disk-size="${BOOT_DISK_GB}GB" \
            --metadata-from-file=startup-script="$STARTUP" --scopes=cloud-platform \
            >"$LOGS/create-$DEV_VM.log" 2>&1 </dev/null &
        dev_pid=$!
    fi
    for spec in $WORKER_REGIONS; do
        region="${spec%%=*}" missing="" n_missing=0
        for w in $WORKERS; do
            [ "$(region_of_worker "$w")" = "$region" ] || continue
            if [ -n "$(zone_of "$w")" ]; then echo "$w already exists"
            else missing="$missing${missing:+,}$w"; n_missing=$((n_missing + 1)); fi
        done
        [ "$n_missing" -gt 0 ] || continue
        # --min-count=1: if quota or capacity only allows some, create those now; a
        # re-run creates the rest.
        log "Creating $n_missing worker(s) ($WORKER_TYPE) in $region: $missing"
        gcloud compute instances bulk create --predefined-names="$missing" \
            --count="$n_missing" --min-count=1 \
            --region="$region" --machine-type="$WORKER_TYPE" "${IMAGE[@]}" \
            --boot-disk-type=hyperdisk-balanced --boot-disk-size="${BOOT_DISK_GB}GB" \
            --metadata-from-file=startup-script="$STARTUP" \
            >"$LOGS/create-workers-$region.log" 2>&1 </dev/null &
        CREATE_JOBS="$CREATE_JOBS $region:$!"
    done
    # A partial bulk create doesn't say why the rest failed (often: that zone ran out of
    # this machine type). For each VM still missing, try the region's zones one at a time
    # with a plain create, which does report the reason if every zone fails.
    create_in_zones() {  # vm region [extra flags...] -> 0 once created in some zone
        local vm=$1 region=$2 zone err=""; shift 2
        for zone in $(gcloud compute machine-types list --filter="name=$DEV_TYPE AND zone~^$region-" \
                          --format='value(zone.basename())' </dev/null | sort); do
            if out="$(gcloud compute instances create "$vm" --zone "$zone" --machine-type "$DEV_TYPE" \
                          "${IMAGE[@]}" --boot-disk-type=hyperdisk-balanced --boot-disk-size="${BOOT_DISK_GB}GB" \
                          --metadata-from-file=startup-script="$STARTUP" "$@" 2>&1 </dev/null)"; then
                echo "$vm created in $zone"
                return 0
            fi
            err="$err
    $zone: $(printf '%s\n' "$out" | grep -vE '^WARNING|^ *$' | tail -2 | tr -s ' ' | tr '\n' ' ')"
        done
        echo "WARNING: couldn't create $vm in any $region zone:$err"
        return 1
    }

    for job in $CREATE_JOBS; do
        region="${job%%:*}"
        wait "${job#*:}" || true
        for w in $WORKERS; do
            [ "$(region_of_worker "$w")" = "$region" ] && [ -z "$(zone_of "$w")" ] || continue
            echo "$w wasn't created by the bulk request; trying $region's zones one by one"
            create_in_zones "$w" "$region" \
                || echo "  Re-run this script later to try $w again."
        done
    done
    if [ -n "$dev_pid" ]; then
        wait "$dev_pid" || true
        if [ -z "$(vm_zone)" ]; then
            echo "$DEV_VM wasn't created by the bulk request; trying $DEV_REGION's zones one by one"
            create_in_zones "$DEV_VM" "$DEV_REGION" --scopes=cloud-platform || die "couldn't create $DEV_VM"
        fi
        DEV_ZONE="$(vm_zone)"
        [ -n "$DEV_ZONE" ] || die "created $DEV_VM, but can't find which zone it's in"
        echo "$DEV_VM is in $DEV_ZONE"
    fi
else
    DEV_ZONE="$(vm_zone)"
    [ -n "$DEV_ZONE" ] || die "$DEV_VM doesn't exist in $PROJECT_ID; use --from-step 5 or lower to create it"
    echo "$DEV_VM is in $DEV_ZONE"
fi

# --- 6. Per-user setup on the VM (docker group, conda) ----------------------
ssh_on() { gcloud compute ssh "$1" --zone "$2" --quiet --command "${3:-bash -s}"; }  # vm zone [cmd]
vm_ssh() { ssh_on "$DEV_VM" "$DEV_ZONE" "$@"; }

# Starts pulling the chisel-build image on a VM, detached so it keeps going after
# this SSH session ends (it's large). Progress: ~/chisel-pull.log on the VM.
start_chisel_pull() {  # vm zone
    # flock -n makes a second run exit at once while a pull still holds the lock.
    ssh_on "$1" "$2" "echo \"\$(date) starting pull\" >> ~/chisel-pull.log;
        sudo nohup setsid flock -n /tmp/chisel-pull.lock docker pull $CHISEL_IMAGE \
            >> ~/chisel-pull.log 2>&1 </dev/null &" </dev/null
}

# Environment variables the tutorial uses, and activating its conda env, at the end
# of ~/.bashrc on a VM. Only the head (chia-dev) gets CHIA_HEAD. They're rewritten
# each run, so GCP_PROJECT follows the project this script is using.
add_bashrc_env() {  # vm zone [head]
    local head=""
    [ "${3:-}" = head ] && head="export CHIA_HEAD=\$(hostname -I | awk '{print \$1}')"
    ssh_on "$1" "$2" <<EOF
set -e
sed -i '/^export \(THIS_MACHINE\|CHIA_HEAD\|GCP_PROJECT\)=/d; /^conda activate chia_env$/d' ~/.bashrc
cat >> ~/.bashrc <<'RC'
export THIS_MACHINE=\$(hostname -I | awk '{print \$1}')
${head}${head:+
}export GCP_PROJECT=$PROJECT_ID
conda activate chia_env
RC
EOF
}

wait_for_docker() {  # vm zone
    local tries=0
    until ssh_on "$1" "$2" "getent group docker && systemctl is-active --quiet docker" >/dev/null 2>&1 </dev/null; do
        tries=$((tries + 1))
        if [ "$tries" -ge 60 ]; then
            echo "Docker still isn't running on $1 after 10 minutes. See the install log with:" >&2
            echo "  gcloud compute ssh $1 --zone $2 --command 'sudo journalctl -u google-startup-scripts --no-pager | tail -50'" >&2
            return 1
        fi
        sleep 10
    done
}

# Workers: Docker group, conda, the chia-tutorial repo and its conda env. Runs in the background
# (one job per worker, output in $LOGS/<worker>.log); chia-dev's key is added at the end.
setup_worker() {  # vm zone
    set -e
    wait_for_docker "$1" "$2"
    ssh_on "$1" "$2" 'sudo usermod -aG docker "$USER"' </dev/null
    start_chisel_pull "$1" "$2"
    ssh_on "$1" "$2" <<'EOF'
set -e
if [ ! -x ~/miniforge3/bin/conda ]; then
    curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh
    bash /tmp/miniforge.sh -b -p ~/miniforge3 >/dev/null
    ~/miniforge3/bin/conda init bash >/dev/null
fi
# Same as on chia-dev: drop ~/.bashrc's interactive-only guard so commands run over
# plain `ssh chia-worker-N <cmd>` (as CHIA does) also get conda's setup.
sed -i '/^# If not running interactively, don.t do anything$/d' ~/.bashrc
sed -i '/^case \$- in$/,/^esac$/d' ~/.bashrc
sed -i '/^\[ -z "\$PS1" \] && return$/d' ~/.bashrc
if [ ! -d ~/chia-tutorial/.git ]; then
    git clone -q https://github.com/ucb-bar/chia-tutorial.git ~/chia-tutorial
fi
cd ~/chia-tutorial
git submodule update --init
env_name="$(sed -n 's/^name: *//p' environment.yml)"
if ~/miniforge3/bin/conda env list | awk '{print $1}' | grep -qxF "$env_name"; then
    echo "conda env $env_name already exists"
else
    echo "Creating conda env $env_name"
    ~/miniforge3/bin/conda env create -q -f environment.yml
fi
echo "setup done"
EOF
    add_bashrc_env "$1" "$2" </dev/null
}

# WORKER_JOBS lists each worker as name:zone:pid of its background setup. When this
# step is skipped, the pid is empty and step 8 only gives chia-dev SSH access.
WORKER_JOBS=""
if do_step 6; then
    # The docker command appears partway through Docker's install, before its group
    # and service exist, so wait for those instead.
    log "Waiting for SSH + Docker on $DEV_VM (the VM installs Docker on first boot, a few minutes)"
    wait_for_docker "$DEV_VM" "$DEV_ZONE" || die "Docker isn't running on $DEV_VM"
    start_chisel_pull "$DEV_VM" "$DEV_ZONE"
    echo "Pulling $CHISEL_IMAGE in the background on every VM (log: ~/chisel-pull.log on each)"

    # Start the workers' setup now, after the first SSH to chia-dev has put this Cloud
    # Shell's key in the project metadata (parallel first-time SSHs would race on that).
    for w in $WORKERS; do
        zone="$(zone_of "$w")"
        [ -n "$zone" ] || continue
        setup_worker "$w" "$zone" >"$LOGS/$w.log" 2>&1 </dev/null &
        WORKER_JOBS="$WORKER_JOBS $w:$zone:$!"
    done
    [ -z "$WORKER_JOBS" ] || echo "Setting up the workers in the background (logs: $LOGS/chia-worker-*.log)"
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

    log "Setting up the shell, the chia-tutorial repo and SSH keys on $DEV_VM"
    vm_ssh <<'EOF'
set -e
# Ubuntu's ~/.bashrc stops early for non-interactive shells. Remove that guard so
# commands CHIA runs over SSH also get conda and the ssh-agent settings below.
sed -i '/^# If not running interactively, don.t do anything$/d' ~/.bashrc
sed -i '/^case \$- in$/,/^esac$/d' ~/.bashrc
sed -i '/^\[ -z "\$PS1" \] && return$/d' ~/.bashrc

# One ssh-agent per user at a fixed socket, shared by all shells.
if ! grep -qF 'ssh-agent.socket' ~/.bashrc; then
    cat >> ~/.bashrc <<'RC'
export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/ssh-agent.socket"
if [ ! -S "$SSH_AUTH_SOCK" ]; then
    eval "$(ssh-agent -a "$SSH_AUTH_SOCK")" > /dev/null
fi
RC
fi

# The tutorial repo and its conda environment.
if [ ! -d ~/chia-tutorial/.git ]; then
    git clone -q https://github.com/ucb-bar/chia-tutorial.git ~/chia-tutorial
fi
cd ~/chia-tutorial
git submodule update --init
env_name="$(sed -n 's/^name: *//p' environment.yml)"
if ~/miniforge3/bin/conda env list | awk '{print $1}' | grep -qxF "$env_name"; then
    echo "conda env $env_name already exists"
else
    echo "Creating conda env $env_name (takes several minutes)"
    ~/miniforge3/bin/conda env create -q -f environment.yml
fi

# SSH key CHIA uses to reach this machine (and the cluster VMs it starts).
# Lingering keeps $XDG_RUNTIME_DIR (and so the agent's socket and its loaded key)
# alive after this session ends, instead of being deleted at logout.
sudo loginctl enable-linger "$USER"
export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/ssh-agent.socket"
[ -S "$SSH_AUTH_SOCK" ] || eval "$(ssh-agent -a "$SSH_AUTH_SOCK")" > /dev/null
[ -f ~/.ssh/chia-key ] || ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/chia-key
ssh-add -q ~/.ssh/chia-key
touch ~/.ssh/authorized_keys
grep -qxF "$(cat ~/.ssh/chia-key.pub)" ~/.ssh/authorized_keys \
    || cat ~/.ssh/chia-key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
EOF
    add_bashrc_env "$DEV_VM" "$DEV_ZONE" head
else
    for w in $WORKERS; do
        zone="$(zone_of "$w")"
        [ -z "$zone" ] || WORKER_JOBS="$WORKER_JOBS $w:$zone:"
    done
fi

# --- 7. Gemini / Antigravity -------------------------------------------------
AGY_TOKEN=.gemini/antigravity-cli/antigravity-oauth-token
if ! do_step 7; then
    :
elif vm_ssh "test -s ~/$AGY_TOKEN" </dev/null; then
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

# --- 8. Workers: finish, and let chia-dev SSH into them ------------------------
READY="" FIRST_READY=""
if [ -n "$WORKER_JOBS" ]; then
    log "Waiting for the workers' setup to finish"
    PUBKEY="$(vm_ssh 'cat ~/.ssh/chia-key.pub' </dev/null)"
    case "$PUBKEY" in ssh-*) ;; *) die "couldn't read ~/.ssh/chia-key.pub from $DEV_VM" ;; esac
    for job in $WORKER_JOBS; do
        w="${job%%:*}" rest="${job#*:}"; zone="${rest%%:*}" pid="${rest#*:}"
        if [ -n "$pid" ] && ! wait "$pid"; then
            echo "WARNING: setting up $w failed; see $LOGS/$w.log. Re-run this script to retry."
            tail -5 "$LOGS/$w.log" | sed 's/^/    /'
            continue
        fi
        # chia-dev's public key (not a new one) goes in the worker's authorized_keys.
        ssh_on "$w" "$zone" "mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys &&
            { grep -qxF '$PUBKEY' ~/.ssh/authorized_keys || echo '$PUBKEY' >> ~/.ssh/authorized_keys; } &&
            chmod 600 ~/.ssh/authorized_keys" </dev/null \
            || { echo "WARNING: couldn't add chia-dev's key on $w"; continue; }
        ip="$(gcloud compute instances describe "$w" --zone "$zone" \
              --format='value(networkInterfaces[0].networkIP)' </dev/null)"
        echo "$w ($zone, $ip): ready"
        READY="$READY  $w  $zone  $ip
"
        FIRST_READY="${FIRST_READY:-$w}"
    done
fi

# --- Done ---------------------------------------------------------------------
# chia-dev's external IP, and the username gcloud created on the VMs (gcloud's own
# ssh command line has both), for SSHing in from a laptop with Cloud Shell's key.
DEV_IP="$(gcloud compute instances describe "$DEV_VM" --zone "$DEV_ZONE" \
          --format='value(networkInterfaces[0].accessConfigs[0].natIP)' </dev/null)"
SSH_USER="$(gcloud compute ssh "$DEV_VM" --zone "$DEV_ZONE" --dry-run 2>/dev/null </dev/null \
            | grep -oE '[^ @]+@[0-9]+(\.[0-9]+){3}' | tail -1 | cut -d@ -f1 || true)"
SSH_USER="${SSH_USER:-$USER}"

# Commands to copy are printed in bold cyan, when the output is a terminal.
C="" R=""
[ -t 1 ] && C=$'\033[1;36m' R=$'\033[0m'

log "Done"
cat <<EOF
Project:  $PROJECT_ID
VM:       $DEV_VM ($DEV_ZONE), external IP ${DEV_IP:-unknown}
Workers (name, zone, internal IP):
${READY:-  (none ready yet; re-run this script)}
SSH to your dev machine (from Cloud Shell):   ${C}gcloud compute ssh $DEV_VM --zone $DEV_ZONE${R}
SSH to your dev machine (from your laptop):   ${C}ssh -o IdentitiesOnly=yes -i <path to the key> $SSH_USER@${DEV_IP:-EXTERNAL_IP}${R}
  (first download the key with: ${C}cloudshell download ~/.ssh/google_compute_engine${R}
   Save it anywhere on your laptop and use that path for <path to the key>. Don't
   overwrite a google_compute_engine key you already have in ~/.ssh. Stopping and
   starting the VM changes the IP.)
From chia-dev, SSH to a worker:               ${C}ssh ${FIRST_READY:-chia-worker-N}${R}   (or its internal IP)
Tear down all the VMs:                        $0 --teardown
Remove everything (VMs, tutorial files):      $0 --purge
EOF
