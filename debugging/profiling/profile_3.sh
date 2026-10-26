cd ~/chia-tutorial/debugging/profiling
chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/accel_loop.py" --iterations 1 --replay-iteration 2

cd ~/chia-tutorial/debugging
chia viz-profile prof_eval/ChiaProfileCollector.log --format html
