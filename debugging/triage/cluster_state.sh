#!/usr/bin/env bash
# A snapshot of the cluster: machines, resources, jobs, what is running now,
# and what is still alive. Read-only: it changes nothing.
cd ~/chia-tutorial/debugging/triage
C="--chia-cluster cluster.yaml"

echo "### machines and resources (free / total)"
chia status $C

echo "### the machines Ray knows (alive or dead)"
chia list nodes $C

echo "### jobs: which ran, which failed, which are still running"
chia list jobs $C

echo "### tasks running right now"
chia list tasks $C --filter "state=RUNNING"

echo "### actors by class (alive and dead)"
chia ray summary actors $C

echo "### actors still alive (leftovers from finished jobs show up here)"
chia list actors $C --filter "state=ALIVE"

echo "### object store memory"
chia ray memory --stats-only $C
