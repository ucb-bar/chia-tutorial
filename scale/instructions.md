# Run the accelerator loop

Assumes GCP setup, model login, and the CHIA Python environment are already ready.
Run commands from the repository root. Keep the terminal open (or use `tmux`),
and do not launch a second loop while one is running.

## 1. Run

```bash
python scale/up.py       # Skip if the cluster is already running.
python scale/run.py
```

This measures the baseline, runs the agents configured in `scale/settings.yaml`,
builds and tests their designs, and selects the best passing result.
The console prints the remote results directory: `/home/ubuntu/scale/out/run_...`.

### Scaling knobs

Edit these before starting; allocation knobs are marked `TUTORIAL` in the YAML.

| File under `scale/` | Knob | What it scales |
|---|---|---|
| `settings.yaml` | `agents` | Parallel optimization searches |
| `settings.yaml` | `iterations_per_agent` | Designs explored per agent |
| `settings.yaml` | `case_limit` | Test/benchmark coverage; `0` selects all available cases |
| `settings.yaml` | `compile_slots` / `simulation_slots` | Concurrent compilations/simulations across all agents |
| `cluster.yaml` | `gcp_nodes.*.count` | Number of builder or simulator VMs |
| `cluster.yaml` | `gcp_nodes.*.machine_type` | CPU and RAM per VM |
| `cluster.yaml` | `disk_size_gb` inside each role's `disks` | Disk capacity per VM |

More agents need matching builders, strategy prompts, and model-client slots.
VM counts must match worker counts and IP references; loop slots must fit the
cluster's CPU/resource budgets. See `README.txt` for the coupled settings.
Changing coverage requires a new matching baseline. Benchmarks use one timed repetition.
Editing cluster YAML does not resize existing VMs; resource changes require
reprovisioning. Download results before replacing VMs or disks.

## 2. Download results

After the loop finishes:

```bash
SCALE_HEAD=$(python -c 'import json; print(json.load(open("scale/.runtime/state.json"))["public_ip"])')

rsync -az \
  -e "ssh -i scale/.runtime/chia-key -o IdentitiesOnly=yes" \
  "ubuntu@$SCALE_HEAD:/home/ubuntu/scale/out/" scale/out/
```

This copies all saved runs without copying credentials. Keep the complete run
directory so its settings, baseline, test cases, and logs stay with the results.

## 3. Read the results

Open the run directory printed by the loop, now under `scale/out/`:

- `best.json`: winning design, its directory, and speedup.
- `run_summary.json`: validated candidates and optimization throughput.
- `agent_*/iter_*/summary.json`: each agent's success or failure.
- Winning iteration's `hardware.patch` and `memcpy_body.c`: the implementation.
- `timing_summary.json`: wall time and failed simulations.
- `metrics_summary.json`: elapsed time and sampled peak disk usage.

A score of `6.36` means a **6.36x geometric-mean simulated-cycle speedup** across
the configured benchmarks, not faster wall-clock execution. A passing run does
not mean every agent succeeded. The current 1,024-case suite is a subset of the
4,488 available cases; do not report it as validation against all cases.

## 4. Stop cloud charges

After downloading and checking the results:

```bash
python scale/up.py down
```

This deletes the scale cluster VMs, their boot disks, and remote results.
It does not remove the separate dev VM or other cloud resources.
