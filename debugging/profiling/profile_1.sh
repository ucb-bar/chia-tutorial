ls -lsa ~/chia-tutorial/debugging/profiling/out

cd ~/chia-tutorial/debugging
chia viz-profile prof_full/ChiaProfileCollector.log --format html
chia viz-profile prof_full/ChiaProfileCollector.log --format svg
chia viz-profile prof_full/ChiaProfileCollector.log --format table
