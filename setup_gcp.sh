#!/usr/bin/env bash
# CHIA tutorial: one-time GCP setup.
#
# Installs a private copy of the gcloud CLI (your existing gcloud install and
# its config are not touched), signs you in, creates/uses a GCP project with
# billing, enables the APIs the tutorial needs, and starts one VM:
#   chia-dev    c2d-standard-8  (AMD Milan, 8 vCPU / 32 GB) - your dev machine / CHIA head
# with Docker, conda (Miniforge) and the Antigravity CLI (agy), signed in. More machines are
# brought up later by CHIA clusters.
#
# Usage:
#   ./setup_gcp.sh --account you@gmail.com [--project ID] [--billing-account ID]
#   ./setup_gcp.sh --account you@gmail.com --teardown     # delete the VM
#   ./setup_gcp.sh --account you@gmail.com --purge        # delete the VM (and optionally the
#                                                         # project), sign out, remove local files
#
# Sign-in happens in the browser (Google OAuth); the script never sees your password.
# Safe to re-run: existing project, VMs and installs are reused.
#
# Optional environment overrides:
#   DEV_ZONE=us-west4-c   TUT_HOME=~/.chia-tutorial
set -euo pipefail

usage() { grep '^#   \./setup_gcp' "$0" | sed 's/^#   /usage: /'; exit 1; }
ACCOUNT="" TEARDOWN="" PURGE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --account)         ACCOUNT="$2"; shift 2 ;;
        --project)         PROJECT_ID="$2"; shift 2 ;;
        --billing-account) BILLING_ACCOUNT="$2"; shift 2 ;;
        --teardown)        TEARDOWN=1; shift ;;
        --purge)           TEARDOWN=1 PURGE=1; shift ;;
        *) usage ;;
    esac
done
[ -n "$ACCOUNT" ] || usage

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

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. Private gcloud --------------------------------------------------------
# CLOUDSDK_CONFIG keeps this install's accounts/projects separate from ~/.config/gcloud.
export CLOUDSDK_CONFIG="$TUT_HOME/gcloud-config"
SDK="$TUT_HOME/google-cloud-sdk"
if [ -n "$PURGE" ] && [ ! -x "$SDK/bin/gcloud" ]; then
    echo "The tutorial's gcloud isn't installed in $TUT_HOME; nothing to sign out of."
    rm -f "$STATE"
    exit 0
fi
if [ ! -x "$SDK/bin/gcloud" ]; then
    log "Installing a private gcloud CLI into $SDK"
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64)  pkg=linux-x86_64 ;;
        Linux-aarch64) pkg=linux-arm ;;
        Darwin-arm64)  pkg=darwin-arm ;;
        Darwin-x86_64) pkg=darwin-x86_64 ;;
        *) die "unsupported platform $(uname -s)-$(uname -m)" ;;
    esac
    curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-$pkg.tar.gz" \
        | tar -xz -C "$TUT_HOME"
    "$SDK/install.sh" --quiet --usage-reporting=false --path-update=false \
        --command-completion=false >/dev/null
fi
gcloud() { "$SDK/bin/gcloud" "$@"; }

gcloud config set account "$ACCOUNT" >/dev/null 2>&1

if [ -n "$TEARDOWN" ]; then
    if [ -n "${PROJECT_ID:-}" ]; then
        log "Deleting $DEV_VM in $PROJECT_ID"
        gcloud compute instances delete "$DEV_VM" --zone "$DEV_ZONE" --project "$PROJECT_ID" --quiet || true
    elif [ -z "$PURGE" ]; then
        die "no project recorded in $STATE"
    fi
    if [ -z "$PURGE" ]; then
        echo "To delete the whole project too: $SDK/bin/gcloud projects delete $PROJECT_ID"
        echo "To also sign out and remove the tutorial's local files: $0 --account $ACCOUNT --purge"
        exit 0
    fi

    if [ -n "${PROJECT_ID:-}" ]; then
        read -r -p "Also delete the whole project $PROJECT_ID? [y/N] " answer
        case "$answer" in
            [yY]*) gcloud projects delete "$PROJECT_ID" --quiet || true ;;
            *) echo "Keeping project $PROJECT_ID." ;;
        esac
    fi

    log "Signing $ACCOUNT out of the tutorial's gcloud"
    gcloud auth revoke "$ACCOUNT" --quiet >/dev/null 2>&1 || true
    gcloud auth application-default revoke --quiet >/dev/null 2>&1 || true

    # Other accounts may share TUT_HOME (and the SSH key); only clean up when this
    # is the last one.
    rm -f "$STATE"
    others="$(ls "$TUT_HOME"/state-*.sh 2>/dev/null || true)"
    if [ -z "$others" ]; then
        # The SSH key is shared with any other gcloud on this machine: only remove
        # it if this tutorial created it.
        if [ "$(cat "$TUT_HOME/ssh-key-preexisting" 2>/dev/null)" = no ]; then
            log "Removing the SSH key the tutorial created"
            rm -f ~/.ssh/google_compute_engine ~/.ssh/google_compute_engine.pub \
                  ~/.ssh/google_compute_known_hosts
        fi
        log "Removing $TUT_HOME"
        rm -rf "$TUT_HOME"
    else
        echo "Keeping $TUT_HOME: it still holds state for: $others"
    fi
    exit 0
fi

# --- 2. Sign in --------------------------------------------------------------
if ! gcloud auth print-access-token "$ACCOUNT" >/dev/null 2>&1; then
    log "Signing in as $ACCOUNT (a browser window or URL will appear)"
    browser=()
    [ "$(uname -s)" = Linux ] && [ -z "${DISPLAY:-}" ] && browser=(--no-launch-browser)
    gcloud auth login "$ACCOUNT" --update-adc ${browser[@]+"${browser[@]}"}
fi
active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
[ "$active" = "$ACCOUNT" ] || die "signed in as '$active', expected '$ACCOUNT' (pick that account in the browser)"
echo "Signed in as $ACCOUNT"

# --- 3. Project + billing ----------------------------------------------------
if [ -z "${PROJECT_ID:-}" ]; then
    PROJECT_ID="chia-tut-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
    log "Creating project $PROJECT_ID"
    gcloud projects create "$PROJECT_ID" --name="CHIA tutorial" \
        || die "project creation failed (your organization may require a folder or block it; set PROJECT_ID=<existing project> and re-run)"
fi
echo "PROJECT_ID=$PROJECT_ID" > "$STATE"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1
gcloud auth application-default set-quota-project "$PROJECT_ID" >/dev/null 2>&1 || true

if [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" != "True" ]; then
    if [ -z "${BILLING_ACCOUNT:-}" ]; then
        accounts="$(gcloud billing accounts list --filter=open=true --format='value(name.basename())')"
        case "$(printf '%s\n' "$accounts" | grep -c .)" in
            0) die "no open billing account. Create one at https://console.cloud.google.com/billing (or redeem the tutorial credits), then re-run." ;;
            1) BILLING_ACCOUNT="$accounts" ;;
            *) gcloud billing accounts list --filter=open=true
               die "several billing accounts found; re-run with BILLING_ACCOUNT=<ACCOUNT_ID>" ;;
        esac
    fi
    log "Linking billing account $BILLING_ACCOUNT"
    gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
fi

# --- 4. APIs -----------------------------------------------------------------
# compute: VMs. cloudaicompanion: Gemini Code Assist (what agy signs in against).
# aiplatform: Vertex AI (Gemini via OpenCode, the alternative CHIA backend).
log "Enabling APIs (takes a minute the first time)"
until out="$(gcloud services enable compute.googleapis.com cloudaicompanion.googleapis.com \
                 aiplatform.googleapis.com --project "$PROJECT_ID" 2>&1)"; do
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

# --- 5. Pre-flight: network, machine types, CPU quota ------------------------
if ! gcloud compute networks describe default >/dev/null 2>&1; then
    log "Creating the default network + SSH firewall rule"
    gcloud compute networks create default --subnet-mode=auto
    gcloud compute firewall-rules create default-allow-ssh --network=default \
        --allow=tcp:22 --source-ranges=0.0.0.0/0
    gcloud compute firewall-rules create default-allow-internal --network=default \
        --allow=tcp,udp,icmp --source-ranges=10.128.0.0/9
fi

gcloud compute machine-types describe "$DEV_TYPE" --zone "$DEV_ZONE" >/dev/null 2>&1 || die \
    "$DEV_TYPE is not offered in $DEV_ZONE. Zones that have it: $(gcloud compute machine-types list \
     --filter="name=$DEV_TYPE" --format='value(zone)' | tr '\n' ' ')"

check_quota() {  # region metric needed
    gcloud compute regions describe "$1" --flatten=quotas \
        --format='csv[no-heading](quotas.metric,quotas.limit,quotas.usage)' \
    | awk -F, -v m="$2" -v n="$3" -v r="$1" '$1==m && $2-$3<n {
        printf "WARNING: %s quota in %s is %d (in use %d); need %d more. Request an increase at\n  https://console.cloud.google.com/iam-admin/quotas\n", m, r, $2, $3, n }'
}
check_quota "${DEV_ZONE%-*}" C2D_CPUS 8
check_quota "${DEV_ZONE%-*}" CPUS 8

# --- 6. VMs ------------------------------------------------------------------
STARTUP="$TUT_HOME/startup.sh"
cat > "$STARTUP" <<'EOF'
#!/bin/bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
EOF

create_vm() {  # name zone type cpu-platform disk-type disk-size [extra flags...]
    local name=$1 zone=$2 type=$3 cpu=$4 dtype=$5 dsize=$6; shift 6
    if gcloud compute instances describe "$name" --zone "$zone" >/dev/null 2>&1; then
        echo "$name already exists"
        return
    fi
    log "Creating $name ($type${cpu:+, $cpu}) in $zone"
    gcloud compute instances create "$name" --zone "$zone" --machine-type "$type" \
        ${cpu:+--min-cpu-platform="$cpu"} "${IMAGE[@]}" \
        --boot-disk-type="$dtype" --boot-disk-size="$dsize" \
        --metadata-from-file=startup-script="$STARTUP" "$@"
}
# chia-dev gets the cloud-platform scope so CHIA running on it can create more VMs.
create_vm "$DEV_VM" "$DEV_ZONE" "$DEV_TYPE" "$DEV_CPU" pd-balanced 200GB --scopes=cloud-platform

# --- 7. Per-user setup on the VM (docker group, conda) ----------------------
# Before the first `gcloud compute ssh` creates it, note whether an SSH key
# already existed so --purge knows whether it may delete it.
if [ ! -f "$TUT_HOME/ssh-key-preexisting" ]; then
    if [ -f ~/.ssh/google_compute_engine ]; then echo yes; else echo no; fi > "$TUT_HOME/ssh-key-preexisting"
fi
vm_ssh() { gcloud compute ssh "$1" --zone "$2" --quiet --command "${3:-bash -s}"; }

# The docker command appears partway through Docker's install, before its group
# and service exist, so wait for those instead.
log "Waiting for SSH + Docker on $DEV_VM (the VM installs Docker on first boot, a few minutes)"
tries=0
until vm_ssh "$DEV_VM" "$DEV_ZONE" \
        "getent group docker && systemctl is-active --quiet docker" >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -lt 60 ] || die "Docker still isn't running on $DEV_VM after 10 minutes. See the install log with:
  $SDK/bin/gcloud compute ssh $DEV_VM --zone $DEV_ZONE --command 'sudo journalctl -u google-startup-scripts --no-pager | tail -50'"
    sleep 10
done
vm_ssh "$DEV_VM" "$DEV_ZONE" 'sudo usermod -aG docker "$USER"'
echo "$DEV_VM: $(vm_ssh "$DEV_VM" "$DEV_ZONE" "lscpu | sed -n 's/^Model name: *//p'")"

log "Installing conda (Miniforge) on $DEV_VM"
vm_ssh "$DEV_VM" "$DEV_ZONE" <<'EOF'
set -e
if [ ! -x ~/miniforge3/bin/conda ]; then
    curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh
    bash /tmp/miniforge.sh -b -p ~/miniforge3 >/dev/null
    ~/miniforge3/bin/conda init bash >/dev/null
fi
mkdir -p ~/.gemini
~/miniforge3/bin/conda --version
EOF

# --- 8. Gemini / Antigravity -------------------------------------------------
log "Gemini Code Assist license (needed by the Antigravity CLI)"
cat <<EOF
Open this page, start the Gemini Code Assist trial (Standard or Enterprise) for
project $PROJECT_ID, and assign a license to $ACCOUNT:

  https://console.cloud.google.com/gemini-admin?project=$PROJECT_ID

EOF
read -r -p "Press Enter once the license is assigned (or type 'skip'): " answer
AGY_TOKEN=.gemini/antigravity-cli/antigravity-oauth-token
if [ "$answer" = skip ]; then
    :
elif vm_ssh "$DEV_VM" "$DEV_ZONE" "test -s ~/$AGY_TOKEN"; then
    echo "agy is already signed in on $DEV_VM (~/$AGY_TOKEN exists)"
else
    # The login lands in ~/.gemini on $DEV_VM, which CHIA mounts into its LLM containers.
    log "Installing agy on $DEV_VM"
    vm_ssh "$DEV_VM" "$DEV_ZONE" \
        '[ -x ~/.local/bin/agy ] || curl -fsSL https://antigravity.google/cli/install.sh | bash >/dev/null'
    log "Signing agy in on $DEV_VM"
    cat <<EOF
agy will print a sign-in URL. Open it, pick $ACCOUNT, then choose project
$PROJECT_ID and location 'global'. Once agy shows its prompt you're signed in:
type /exit (or press Ctrl-C) to leave agy and let this script continue.

EOF
    # Leaving agy with Ctrl-C exits non-zero; the token check below is what counts.
    gcloud compute ssh "$DEV_VM" --zone "$DEV_ZONE" --quiet -- -t '~/.local/bin/agy' || true
    vm_ssh "$DEV_VM" "$DEV_ZONE" "test -s ~/$AGY_TOKEN" \
        || echo "WARNING: no agy login found in ~/.gemini on $DEV_VM; re-run this script to try again."
fi

# --- Done ---------------------------------------------------------------------
cat > "$TUT_HOME/env.sh" <<EOF
export CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG"
export PATH="$SDK/bin:\$PATH"
export CHIA_TUT_PROJECT="$PROJECT_ID"
EOF
log "Done"
cat <<EOF
Project:  $PROJECT_ID
VM:       $DEV_VM ($DEV_ZONE)

Use the tutorial's gcloud in a new shell:   source $TUT_HOME/env.sh
SSH to your dev machine:                    gcloud compute ssh $DEV_VM --zone $DEV_ZONE
Stop paying when you're done:               $0 --account $ACCOUNT --teardown
Remove everything (VM, sign-in, local files): $0 --account $ACCOUNT --purge
EOF