# Run the accelerator loop

Run commands from the `scale` folder.

## 1. Load the environment

```bash
source load-env.sh
```

Increase vCPU quota by running:
```bash
./increase-quota.sh
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
There should be a out/run_... value printed out, save this! We will use this to check our progress.

## 4. Check progress

From your local `scale` folder, use the run directory printed by the loop.
In a new terminal, first run `source load-env.sh` (step 1) to load the head's SSH address.

```bash
python status.py out/run_...
```

The script reads results from the head over SSH when the directory is not local.
Results stay on the head in `~/scale/out/`.
Status shows geometric-mean benchmark speedup versus baseline when results are available;
quick-suite speedups are marked preliminary.

## 5. Scale up cluster

After increasing cluster counts, add workers without tearing down the cluster:
In a new terminal, first run `source load-env.sh` (step 1), then run:

```bash
chia up --add cluster.yaml
```

Do not start a second loop while one is running.
Before changing worker setup commands or VM sizes, run `chia down cluster.yaml`
using the old configuration, then edit and bring it up again. `--add` keeps existing workers.

## 6. Check progress

```bash
python status.py out/run_...
```
We should now see the run progressing faster!

## 7. Shut down

```bash
chia down cluster.yaml
```

Run after the loop finishes. Worker VMs are deleted; the existing head remains.
