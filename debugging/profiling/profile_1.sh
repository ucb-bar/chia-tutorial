ls -lsa ~/chia-tutorial/debugging/profiling/out

cd ~/chia-tutorial/debugging
LOG=profiling/out/profile_20261019_030358/ChiaProfileCollector.log
chia viz-profile $LOG --format html
chia viz-profile $LOG --format svg
chia viz-profile $LOG --format table
