#!/usr/bin/env bash
# The whole debugging section, in order, from the checkpoint (out/ up to iter_3).
# Each run makes the next iteration: iter_4 (fully bypassed), iter_5 (half
# bypassed), iter_6 (profiling loop), iter_7 (boards), iter_8 (logging).
# Every <section>/run_job.sh just launches that section's loop as a chia job.
set -e

# 0. setup: branch, THIS_MACHINE / GCP_PROJECT, chia_env
source ~/miniforge3/etc/profile.d/conda.sh
source ~/chia-tutorial/debugging/setup_script.sh

# 1. caching: fully bypassed run (iter_4)
sed -i 's/bypass: false/bypass: true/' ~/chia-tutorial/debugging/caching/replay.yaml
~/chia-tutorial/debugging/caching/run_job.sh

# 2. caching: half bypassed, LLM and Chisel build only (iter_5)
git -C ~/chia-tutorial checkout debugging/caching/replay.yaml
sed -i '/ChiselBuildNode\|AntigravityLLM/s/bypass: false/bypass: true/' ~/chia-tutorial/debugging/caching/replay.yaml
~/chia-tutorial/debugging/caching/run_job.sh
git -C ~/chia-tutorial checkout debugging/caching/replay.yaml

# 3. profiling: list the profiles and render the real run
~/chia-tutorial/debugging/profiling/profile_1.sh

# 4. profiling: render iter_5
~/chia-tutorial/debugging/profiling/profile_2.sh

# 5. profiling: run the loop with the evaluation ChiaFunction (iter_6) and render it
~/chia-tutorial/debugging/profiling/run_job.sh
cd ~/chia-tutorial/debugging
chia viz-profile prof_eval/ChiaProfileCollector.log --format html

# 6. boards (iter_7)
~/chia-tutorial/debugging/boards/run_job.sh

# 7. logging on the storage VM (iter_8): add the VM, run, then a shell in its log folder (exit to come back)
cd ~/chia-tutorial/debugging/logging
chia up --add --yes cluster_storage.yaml
~/chia-tutorial/debugging/logging/run_job.sh
ssh -t -i ~/.ssh/chia-key chia-accel-memcpy-storage-0 "cd ~/chia_logs/sims && ls -lsa && exec bash -l"
