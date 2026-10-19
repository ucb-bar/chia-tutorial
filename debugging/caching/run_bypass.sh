cd ~/chia-tutorial/debugging/caching
chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay --replay-iteration 2
