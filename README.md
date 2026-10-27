# chia-tutorial

## Tutorial Infrastructure Setup

All participants will be provided with Google Cloud (GCP) accounts to develop
and run CHIA loops.

To get started, download and run this setup script. It accepts the terms,
enables the APIs, and starts a machine in GCP that you can use for developing
during the tutorial.

In the commands below, replace `<email>` and `<project-name>` with the GCP
account and project we gave you.

**Linux, macOS**

```bash
curl -L -O "https://raw.githubusercontent.com/ucb-bar/chia-tutorial/refs/heads/main/setup_gcp.sh"
chmod +x setup_gcp.sh
```
```bash
./setup_gcp.sh --account <email> --project <project-name>
```

**Windows** (PowerShell)

```powershell
curl.exe -L -O "https://raw.githubusercontent.com/ucb-bar/chia-tutorial/refs/heads/main/setup_gcp.ps1"
```
```powershell
powershell -ExecutionPolicy Bypass -File .\setup_gcp.ps1 -Account <email> -Project <project-name>
```

When you run the setup script:

1. A Google sign-in opens in your browser: log in with the GCP credentials we
   gave you.
2. Open and accept the GCP terms of service (make sure you're logged into the
   correct account).
3. Antigravity login:
   - Choose "Use a Google Cloud project" -> "Continue with Google Cloud", then
     log in in the browser.
   - Type in your project name, choose `global` for the location, and choose
     Agent Platform.
   - Go through the prompts, then exit by pressing `CTRL+D` twice.

### If you leave early: clean up

Rerun the setup script with the purge option (`--purge` on Linux/macOS,
`-Purge` on Windows). It deletes the VM and offers to remove the rest. Replace
`<email>` and `<project-name>` as above.

**Linux, macOS**

```bash
./setup_gcp.sh --account <email> --project <project-name> --purge
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_gcp.ps1 -Account <email> -Project <project-name> -Purge
```

## Developing on the Remote Machine

Feel free to use any remote development environment you like.

The setup script finishes by printing ways to SSH in from a terminal, e.g.:

```bash
gcloud compute ssh chia-dev --zone us-west4-c
```

or `ssh chia-dev.us-west4-c.<project-name>` (the setup script added this
host to your `~/.ssh/config`).

You can also use [VS Code](https://code.visualstudio.com/) with the
[Remote - SSH extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh).
With the extension installed:

1. Press `CTRL+SHIFT+P`.
2. Search for `Remote-SSH: Connect to Host`.
3. Select `chia-dev.us-west4-c.<project-name>` in the dropdown (the setup
   script added it to your `~/.ssh/config`).
4. Step through the prompts (the OS is Linux; choose Yes / Accept).

## On-Machine Setup (once you're SSH'd in)

Edit your ~/.bashrc file

```bash
nano ~/.bashrc
```

Comment out the following lines:

```bash
# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac
```

And add the following lines to the bottom 

```bash
export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/ssh-agent.socket"
if [ ! -S "$SSH_AUTH_SOCK" ]; then
    eval "$(ssh-agent -a "$SSH_AUTH_SOCK")" > /dev/null
fi
```

Clone the GitHub repo for the tutorial:

```bash
cd ~
git clone https://github.com/ucb-bar/chia-tutorial
cd chia-tutorial
git submodule update --init
```

Create a conda environment and install CHIA:

```bash
conda env create -f environment.yml
conda activate chia_env
```

Create an SSH key, add it to the ssh-agent, and authorize it on this machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/chia-key
ssh-add ~/.ssh/chia-key
cat ~/.ssh/chia-key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```
