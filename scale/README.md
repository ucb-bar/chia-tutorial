# Run the accelerator loop

Assumes GCP setup, model login, and the CHIA Python environment are already ready.
Run commands from the repository root. Keep the terminal open (or use `tmux`),
and do not launch a second loop while one is running.

## 1. Run

```bash
chia up scale/cluster.yaml       # Skip if the cluster is already running.
python scale/run.py
```

This measures the baseline, runs the agents configured in `scale/settings.yaml`,
builds and tests their designs, and selects the best passing result.
By default, three agents each explore one design against a 1,024-case suite.
The existing `chia-dev` VM is the head; CHIA creates only builder/simulator VMs.
In `scale/cluster.yaml`, set its internal IP and the SSH alias printed by
`setup_gcp.sh`. Use the existing configured GCP/CHIA environment.
The console prints the results directory on the head: `~/scale/out/run_...`.

### Scaling knobs

Editable scaling knobs and strategy prompts are at the top of `settings.yaml`.
Model settings have their own section; leave the parameters marked `FIXED`
unchanged. VM allocation knobs are marked `TUTORIAL` in `cluster.yaml`.

| File under `scale/` | Knob | What it scales |
|---|---|---|
| `settings.yaml` | `agents` | Parallel optimization searches |
| `settings.yaml` | `strategies` | One optimization prompt per agent; edit to explore different approaches |
| `settings.yaml` | `design_iterations_per_agent` | Designs explored per agent, not debug retries (formerly `iterations_per_agent`) |
| `settings.yaml` | `test_cases` | Total test + benchmark cases; `0` selects all available cases |
| `cluster.yaml` | `gcp_nodes.*.count` | Number of builder or simulator VMs |
| `cluster.yaml` | `gcp_nodes.*.machine_type` | CPU and RAM per VM |
| `cluster.yaml` | Worker `resources` and `SCALE_WORKER_CPUS` | Shared task slots and Ray CPU capacity; keep these within the VM's capacity |

More agents need matching builders, strategy prompts, and model-client slots.
Five strategy prompts are supplied; the default remains three agents. Agent 0
uses the first prompt, agent 1 the second, and so on; only the first `agents`
entries are used.
Use `python scale/run.py --agents 5` only after providing five builders and
five model-client slots with sufficient CPU budgets and cloud quota.
Keep `parallel_gcp.count`, `chipyard.num_workers`, and its `compatible_ips`
list in sync; do the same for `benchmark_gcp` and `verilator_run`. Model-client
slots are `antigravity.resources.antigravity_creds`.

All agents share the compiler and simulation workers: Ray schedules queued work
using the CPU and resource capacities in `cluster.yaml`, with no extra per-run
or per-agent concurrency caps. Hardware builds retain isolated per-agent workspaces.
There are no `compile_slots` or `simulation_slots` settings to keep in sync.
Each design gets one initial evaluation plus up to four debug retries
(`max_attempts: 5`); these are separate from `design_iterations_per_agent`.

Changing coverage requires a new matching baseline. Benchmarks use one timed repetition.
Editing cluster YAML does not resize existing VMs; resource changes require
reprovisioning. Download results before replacing VMs or disks.

## 2. Download results

After the loop finishes:

```bash
rsync -az chia-dev.us-west4-c.chia-codesign26oak-1967:scale/out/ scale/out/
```

Use your setup script's SSH alias if different. This copies all saved runs
without copying credentials. Keep the complete run directory so its settings,
baseline, test cases, and logs stay with the results.

## 3. Read the results

Open the run directory printed by the loop, now under `scale/out/`:

- `best.json`: winning design, its directory, and speedup.
- `run_summary.json`: validated candidates and optimization throughput.
- `agent_*/iter_*/summary.json`: each agent's success or failure.
- Winning iteration's `hardware.patch` and `memcpy_body.c`: the implementation.
- `timing_summary.json`: wall time and failed simulations; batch timings include queue wait and result collection.
- `metrics_summary.json`: elapsed time and sampled peak disk usage.

A score of `6.36` means a **6.36x geometric-mean simulated-cycle speedup** across
the configured benchmarks, not faster wall-clock execution. A passing run does
not mean every agent succeeded. The current 1,024-case suite is a subset of the
4,488 available cases; do not report it as validation against all cases.

## 4. Stop worker charges

After downloading and checking the results:

```bash
chia down scale/cluster.yaml
```

This deletes the managed worker VMs and their boot disks. The existing dev head
and its saved results remain; that VM continues to incur charges until stopped
separately. Never tear down a cluster while its loop is running.

For an older deployment with a separate scale head, archive its results before
teardown: native `chia down` deletes all VMs carrying the same cluster label,
including that legacy head. Existing runs are not automatically migrated.
