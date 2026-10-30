#!/usr/bin/env bash
# Load into your current shell with: source load-env.sh
# Uses the chia_env from setup_gcp.sh; CHIA itself is already installed there.
source "$(conda info --base)/etc/profile.d/conda.sh" || return
conda activate chia_env || return
# SkyDiscover runs the search on the head only; workers use CHIA from their images.
# --no-deps skips SkyDiscover's unused AlphaEvolve backend; its other deps are listed.
python -m pip install --quiet openai==3.35.1 numpy==1.26.4 tqdm==4.67.1 || return
python -m pip install --quiet --no-deps \
    'skydiscover @ git+https://github.com/ucb-bar/skydiscover.git@78833ea6ad4ee1efffe5781112da4ceb95ff5535' || return
# cluster.yaml mounts the seeds and private/ from this directory.
export DMA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
