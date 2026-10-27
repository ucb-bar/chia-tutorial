#!/usr/bin/env bash
# The whole debugging section, in order, from the checkpoint (out/ up to iter_3).
# Each run makes the next iteration: iter_4 (fully bypassed), iter_5 (half
# bypassed), iter_6 (profiling loop), iter_7 (live status monitor), iter_8 (logging).
# Everything runs from ~/chia-tutorial/debugging. ./submit_job.sh <folder> launches
# that folder's loop as a chia job.
set -e

# 0. setup (the setup slide): chia_env, the debugging branch, THIS_MACHINE / GCP_PROJECT, cd debugging
source ~/miniforge3/etc/profile.d/conda.sh
conda activate chia_env
cd ~/chia-tutorial
git fetch origin
git checkout debugging
git pull
source ~/chia-tutorial/debugging/setup_script.sh
cd ~/chia-tutorial/debugging

# 1. caching: fully bypassed run (iter_4)
sed -i 's/bypass: false/bypass: true/' caching/replay.yaml
./submit_job.sh caching

# 2. caching: half bypassed, LLM and Chisel build only (iter_5)
git checkout caching/replay.yaml
sed -i '/ChiselBuildNode\|AntigravityLLM/s/bypass: false/bypass: true/' caching/replay.yaml
./submit_job.sh caching
git checkout caching/replay.yaml

# 3. profiling: list the profiles and render the real run
ls -lsa profiling/out
chia viz-profile prof_full/ChiaProfileCollector.log --format table

# 4. profiling: render iter_5
chia viz-profile prof_half/ChiaProfileCollector.log --format table

# 5. profiling: run the loop with the evaluation ChiaFunction (iter_6) and render it
./submit_job.sh profiling
chia viz-profile prof_eval/ChiaProfileCollector.log --format html

# 6. live status monitor (iter_7)
./submit_job.sh monitor

# 7. logging on the storage VM (iter_8): add the VM, run, then a shell in its log folder (exit to come back)
chia up --add --yes logging/cluster_storage.yaml
./submit_job.sh logging
ssh -t -i ~/.ssh/chia-key chia-accel-memcpy-storage-0 "cd ~/chia_logs/sims && ls -lsa && exec bash -l"

# 8. at the very end (not run here): every worker down and the storage VM deleted
# chia down --yes cluster.yaml
