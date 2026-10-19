# Run the accelerator loop

Run commands from the repository root.

## 1. Load the environment

```bash
conda activate chia_env
source "${TUT_HOME:-$HOME/.chia-tutorial}/env.sh"
export CHIA_HEAD_IP="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(networkInterfaces[0].networkIP)')"
export CHIA_HEAD_ZONE="$(gcloud compute instances list --project "$CHIA_TUT_PROJECT" --filter='name=chia-dev' --format='value(zone.basename())')"
```

## 2. Adjust values

Increase these starting values under `TUTORIAL` in `scale/settings.yaml`:

```yaml
agents: 1       # Increase to run more agents in parallel (up to 5).
test_cases: 17  # Increase to run more test and benchmark cases.
```

For more agents, provide enough builders in `scale/cluster.yaml`: update the
builder VM `count`, builder `num_workers`, and matching `compatible_ips` entries.
Leave `FIXED` settings unchanged.

## 3. Start and run

```bash
chia up scale/cluster.yaml
python scale/run.py
```

After increasing cluster counts, add workers without tearing down the cluster:

```bash
chia up --add scale/cluster.yaml
```

Do not start a second loop while one is running.

## 4. Check progress

On the head, use the run directory printed by the loop:

```bash
python ~/scale/status.py ~/scale/out/run_...
```

## 5. Shut down

```bash
chia down scale/cluster.yaml
```

Run after the loop finishes. Worker VMs are deleted; the existing head remains.
