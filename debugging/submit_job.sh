#!/usr/bin/env bash
# ./submit_job.sh <folder>: launches that folder's loop as a chia job (caching, profiling, monitor, logging)
cd ~/chia-tutorial/debugging
chia job submit --chia-cluster cluster.yaml -- \
    python $(pwd)/$1/accel_loop.py --replay-iteration 2
