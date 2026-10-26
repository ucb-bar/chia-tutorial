ls -lsa ~/chia-tutorial/debugging/profiling/out

cd ~/chia-tutorial/debugging
chia viz-profile prof_full/ChiaProfileCollector.log --format table

# if we have time: the interactive timeline, copied to your machine
# chia viz-profile prof_full/ChiaProfileCollector.log --format html
# scp -i key user@ip:chia-tutorial/debugging/prof_full/ChiaProfileCollector_profile.html .
