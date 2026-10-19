cd ~/chia-tutorial/debugging/logging
chia up --add --yes cluster_storage.yaml

chia job submit --chia-cluster cluster_storage.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay-iteration 2

# on the storage VM: list the logs, then stay in a shell in that folder (exit to come back)
ssh -t -i ~/.ssh/chia-key chia-accel-memcpy-storage-0 "cd ~/chia_logs/sims && ls -lsa && exec bash -l"
