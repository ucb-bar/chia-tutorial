cd ~/chia-tutorial/debugging/monitor
chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay-iteration 2
