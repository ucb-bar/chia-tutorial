#!/usr/bin/env bash
# Load into your current shell with: source load-env.sh
source "$(conda info --base)/etc/profile.d/conda.sh" || return
conda activate chia_env || return
python -m pip install --upgrade 'chialoops==2.0.0' || return
source "${TUT_HOME:-$HOME/.chia-tutorial}/env.sh" || return
CHIA_HEAD_IP="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(networkInterfaces[0].networkIP)')" || return
CHIA_HEAD_ZONE="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(zone.basename())')" || return
export CHIA_HEAD_IP CHIA_HEAD_ZONE
