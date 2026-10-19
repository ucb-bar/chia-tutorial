# Run the accelerator loop

Run commands from the `scale` folder.

## 1. Load the environment

```bash
source load-env.sh
```

## 2. Adjust values

Increase these starting values under `TUTORIAL` in `settings.yaml`:

```yaml
agents: 1       # Increase to run more agents in parallel (up to 5).
test_cases: 17  # Increase to run more test and benchmark cases.
```

For more agents, provide enough builders in `cluster.yaml`: update the
builder VM `count`, builder `num_workers`, and matching `compatible_ips` entries.
The provided cluster has three builders, supporting up to three agents as-is.
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
Before changing worker setup commands or VM sizes, run `chia down cluster.yaml`
using the old configuration, then edit and bring it up again. `--add` keeps existing workers.

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
