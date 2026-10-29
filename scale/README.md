# Run the accelerator loop

Run commands from the `scale` folder.

## 1. Load the environment

```bash
source load-env.sh
```

## 2. Adjust values

Increase these starting values under `TUTORIAL` in `settings.yaml`:

```yaml
agents: 1       # Increase to run more agents in parallel (for example, 3).
test_cases: 17  # Increase to run more test and benchmark cases (for example, 1024).
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

In a new terminal, first run `source load-env.sh`,
then watch the running loop's live status monitor:

```bash
python monitor.py            # a table every 10 s; Ctrl-C to stop
python monitor.py --once     # one table
```

Each row is one agent (plus `run`, `baseline` and `best`): its iteration, attempt,
current state, simulation batches done, and speedup once validated. The monitor exists only while the loop runs.
Results stay on the head in `~/scale/out/`.

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
python monitor.py
```
We should now see the run progressing faster!

