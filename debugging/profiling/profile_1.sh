ls -lsa ~/chia-tutorial/debugging/profiling/out

cd ~/chia-tutorial/debugging
chia viz-profile prof_full.log --format table

# if we have time: the interactive timeline, copied to your machine
# chia viz-profile prof_full.log --format html
# scp -i key user@ip:chia-tutorial/debugging/prof_full_profile.html .
