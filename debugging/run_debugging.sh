#!/usr/bin/env bash
# The debugging section end to end: the commands people run on the slides, in order.
# Slide numbers are the deck's as of v22. It starts from the checked-in checkpoint
# (out/ up to iter_3) and each job adds the next iteration: iter_4 fully bypassed,
# iter_5 half bypassed, iter_6 profiling, iter_7 live status monitor, iter_8 logging.
set -e

# --- Setup (slide 15) ------------------------------------------------------------
source ~/miniforge3/etc/profile.d/conda.sh     # script only: lets conda activate work in a script
conda activate chia_env
cd ~/chia-tutorial/debugging
# exported at the start of the tutorial (cluster.yaml's head_ip); set here only if missing
export THIS_MACHINE=${THIS_MACHINE:-$(hostname -I | awk '{print $1}')}

# --- 1. Caching and Bypassing (slides 3-20) --------------------------------------
# slide 16: what the recorded runs left in the cache
ls -lsa cache

# slides 17-19: bypass every node (on the slide they edit caching/replay.yaml), run iter_4
sed -i 's/bypass: false/bypass: true/' caching/replay.yaml
./submit_job.sh caching

# slide 20: bypass only the LLM and the Chisel build, run iter_5
git checkout caching/replay.yaml
sed -i '/ChiselBuildNode\|AntigravityLLM/s/bypass: false/bypass: true/' caching/replay.yaml
./submit_job.sh caching
git checkout caching/replay.yaml               # script only: back to the checked-in file

# --- 2. Profiling (slides 21-35) -------------------------------------------------
# slide 23: the profiles so far and the full run's table (slide 24: its HTML timeline)
ls -lsa profiling/out
chia viz-profile prof_full.log --format table

# slide 29: the half-bypassed run (iter_5), to compare
ls -lsa profiling/out
chia viz-profile prof_half.log --format table

# slides 33-34: the loop with the evaluation ChiaFunction (iter_6), then its timeline (slide 35)
./submit_job.sh profiling
chia viz-profile prof_eval.log --format html

# --- 3. Live Status Monitor (slides 36-40) ---------------------------------------
# slide 40: run iter_7 and watch the monitor
./submit_job.sh monitor

# --- 4. Logging (slides 41-45) ---------------------------------------------------
# slide 45: start the storage worker on chia-worker-1, run iter_8, find the logs it kept
chia up --add --yes logging/cluster_storage.yaml
./submit_job.sh logging
ssh chia-worker-1 "cd ~/chia_logs/sims && ls -lsa"   # on the slide: ssh in, cd, ls -lsa, exit

# --- 5. Cluster Debugging (slide 46) ---------------------------------------------
chia status --chia-cluster cluster.yaml

# --- At the very end (not run here): every worker down, the storage one included --
# chia down --yes cluster.yaml
