# Run at the start of the section with:  source ~/chia-tutorial/debugging/setup_script.sh
# (source, not bash: the exports and the conda env must stay in your shell)

cd ~/chia-tutorial
git fetch origin
git checkout debugging
git pull

export THIS_MACHINE=$(hostname -I | awk '{print $1}')
export GCP_PROJECT=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)

conda activate chia_env
