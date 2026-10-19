cd ~/chia-tutorial/debugging
LOG=profiling/out/profile_iter_5/ChiaProfileCollector.log
chia viz-profile $LOG --format html
chia viz-profile $LOG --format svg
chia viz-profile $LOG --format table
