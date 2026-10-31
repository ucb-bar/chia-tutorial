# Implementing and Deploying an Agentic Architecture Discovery Loop with CHIA

## Setup

### In a _new_ terminal, please run the following:

```
cd ~/chia-tutorial/discovery/
source load-env.sh
wget -P checkpoints/ https://tutorial-files.ucb.bar/tutorial.tar.gz
tar -xzf checkpoints/tutorial.tar.gz
ls experiments/tutorial/
python loop.py prepare --run tutorial
```

This block switches into the discovery directory, loads the environment for this loop, installs dependenices, downloads a checkpoint from the loop, reconstructs the baseline source seed, and verifies the baseline design.

Next, we bring up the chia cluster for this loop with:

```
chia up -y cluster.yaml
```

Finally, we launch a chia job that resumes the loop from the provided checkpoint with:

```
chia job submit --chia-cluster cluster.yaml --submission-id tutorial-live --working-dir . -- python "$PWD/loop.py" resume --run tutorial
```

This terminal will stream execution logs of this chia job. 

## Observing the Loop

### In _another new_ terminal, please run the following:

```
cd ~/chia-tutorial/discovery/
source load-env.sh
```

```
python loop.py scores --run tutorial --format table
```

This command presents a ranked table of candidates in the loop's evolutionary database along with scores and validity. You can run it **repeatedly** to observe the loop's progression.

### Optional - Plotting a Graph

Once you have a few newly admitted candidates, run:

```
python loop.py scores --run tutorial --format html
```

This will plot a time v. score graph with each candidate in the evolutionary database as an HTML file. It can be found at `~/chia-tutorial/discovery/experiments/tutorial/artifacts/profile/scores.html`.

**If you are using VS Code for this tutorial**, you can download the above html file and view it in a web browser.

**If you are using a terminal for this tutorial**, you can run the following command in your current `chia-dev` terminal.

```
./print_scp.sh ~/chia-tutorial/discovery/experiments/tutorial/artifacts/profile/scores.html 
```

It will print a command. Copy this command, replace the path to your already downloaded GCP ssh key, and run it on your local machine. It will use `scp` (secure copy) to download the html file to your current local directory. Then, you can view it in a web browser.

**If you are using Cloud Shell for this tutorial**, run the following in a _new_ Cloud Shell terminal:
```
# Find chia-dev's zone
ZONE=$(gcloud compute instances list --filter="name=chia-dev" --format="value(zone)")

# 2. Copy the file from chia-dev into Cloud Shell
gcloud compute scp --zone "$ZONE" \
  chia-dev:~/chia-tutorial/discovery/experiments/tutorial/artifacts/profile/scores.html .

# 3. Download it from Cloud Shell to your local machine (opens a browser download)
cloudshell download scores.html
```
Then, you can view the downloaded html file in a web browser.

## Terminating the Loop

**Note:** You are welcome to leave this loop running for a while and then come back to this section. 

In your current terminal (inside the `~/chia-tutorial/discovery` directory with the environment sourced), run:

```
chia job stop tutorial-live --chia-cluster cluster.yaml
chia down -y cluster.yaml
```

This stops the job and brings down the cluster.
