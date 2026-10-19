cd ~/chia-tutorial/debugging/profiling
chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay-iteration 2

chia viz-profile ~/chia-tutorial/debugging/profiling/out/profile_iter_6/ChiaProfileCollector.log --format html
