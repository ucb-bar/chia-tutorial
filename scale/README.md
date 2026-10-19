# Run the accelerator loop

Start from the repository root, then enter `scale` below. Run the remaining
commands from that folder.

## 1. Load the environment

```bash
cd scale
conda activate chia_env
python -m pip install --upgrade 'chialoops==2.0.0'
source "${TUT_HOME:-$HOME/.chia-tutorial}/env.sh"
export CHIA_HEAD_IP="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(networkInterfaces[0].networkIP)')"
export CHIA_HEAD_ZONE="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(zone.basename())')"
```

## 2. Adjust values

Increase these starting values under `TUTORIAL` in `settings.yaml`:

```yaml
agents: 1       # Increase to run more agents in parallel (up to 5).
test_cases: 17  # Increase to run more test and benchmark cases.
```

For more agents, provide enough builders in `cluster.yaml`: update the
builder VM `count`, builder `num_workers`, and matching `compatible_ips` entries.
Leave `FIXED` settings unchanged.

## 3. Start and run

```bash
chia up cluster.yaml
python run.py
```

After increasing cluster counts, add workers without tearing down the cluster:

```bash
chia up --add cluster.yaml
```

Do not start a second loop while one is running.

## 4. Check progress

On the head, from its `~/scale` folder, use the run directory printed by the loop:

```bash
python status.py out/run_...
```

## 5. Shut down

```bash
chia down cluster.yaml
```

Run after the loop finishes. Worker VMs are deleted; the existing head remains.
