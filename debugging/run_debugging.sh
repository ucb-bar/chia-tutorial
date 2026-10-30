#!/usr/bin/env bash
# The debugging section end to end: the commands people run on the slides, in order
# (the same list as the top of README.md). Slide numbers are the deck's as of v23.
# It needs the accel cluster up (slide 2, for people who just arrived:
#     cd ~/chia-tutorial/accel && chia up --yes cluster.yaml).
# It starts from the checked-in checkpoint (out/ up to iter_3) and each job adds the
# next iteration: iter_4 half bypassed, iter_5 profiling, iter_6 live status monitor,
# iter_7 logging.
set -e

# --- Setup (slide 16) --------------------------------------------------------------
source ~/miniforge3/etc/profile.d/conda.sh     # script only: lets conda activate work in a script
conda activate chia_env
cd ~/chia-tutorial/debugging
# exported at the start of the tutorial (cluster.yaml's head_ip); set here only if missing
export THIS_MACHINE=${THIS_MACHINE:-$(hostname -I | awk '{print $1}')}

# --- 1. Caching and Bypassing (slides 4-21) ----------------------------------------
# slide 17: what the recorded runs left in the cache
ls -lsa cache

# slides 18-20: bypass only the LLM and the Chisel build (on the slide they edit
# caching/replay.yaml by hand), then run iter_4
sed -i '/ChiselBuildNode\|AntigravityLLM/s/bypass: false/bypass: true/' caching/replay.yaml
./submit_job.sh caching
git checkout caching/replay.yaml               # script only: back to the checked-in file

# --- 2. Profiling (slides 22-36) ---------------------------------------------------
# slide 24: the profiles so far and the full run's table (slide 25: its HTML timeline)
ls -lsa profiling/out
chia viz-profile prof_full.log --format table

# slide 30: the half-bypassed run (iter_4), to compare
ls -lsa profiling/out
chia viz-profile prof_half.log --format table

# slides 34-35: the loop with the evaluation ChiaFunction (iter_5), then its table
# (slide 36: its HTML timeline)
./submit_job.sh profiling
chia viz-profile prof_eval.log --format table

# --- 3. Live Status Monitor (slides 37-41) -----------------------------------------
# slide 41: run iter_6 and watch the monitor
./submit_job.sh monitor

# --- 4. Logging (slides 42-46) -----------------------------------------------------
# slide 46: start the storage worker on chia-worker-1, run iter_7, find the logs it kept
chia up --add --yes logging/cluster_storage.yaml
./submit_job.sh logging
ssh chia-worker-1 "cd ~/chia_logs/sims && ls -lsa"   # on the slide: ssh in, cd, ls -lsa, exit

# --- 5. Cluster Debugging (slide 47) -----------------------------------------------
# the cheat sheet's first line; triage/cluster_state.sh runs its read-only lines at once
chia status --chia-cluster cluster.yaml

# --- At the very end (slide 49, not run here): every worker down, the storage one included
# chia down --yes cluster.yaml
