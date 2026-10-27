# The export of the setup slide. After the git lines, run it with:
#     source ~/chia-tutorial/debugging/setup_script.sh
# (source, not bash: the exports must stay in your shell)
# Then everybody: cd ~/chia-tutorial/debugging  (every command in the section runs from there)

export THIS_MACHINE=$(hostname -I | awk '{print $1}')
