cd ~/chia-tutorial/debugging/logging
chia job submit --chia-cluster cluster_storage.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay-iteration 2
