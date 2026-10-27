# accel: an agentic RoCC memcpy accelerator

An LLM co-designs a memcpy RoCC accelerator for a Rocket SoC. CHIA cross-compiles the software, builds the hardware into a Verilator simulator, runs a test-and-benchmark harness on it, and hands any failure back to the LLM to debug. Every iteration starts from
the best design so far.

## Up the cluster

```bash
conda activate chia_env
cd ~/chia-tutorial/accel
chia up --yes cluster.yaml
```

## Launch a job

```bash
conda activate chia_env
cd ~/chia-tutorial/accel
chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/accel_loop.py"
```

## Files

| File | Role |
|---|---|
| `accel_loop.py` | Chia orchestration of the nodes  |
| `agent_prompt.py` | prompts, failure feedback, parsing and logging LLM answers |
| `nodes.py` | Chia nodes: chipyard reset / apply / diff, compile, build, simulate, prompt the LLM |
| `results.py` | results processing helpers, output parsing, scoring, CSVs, best-design selection |
| `memcpy_rocc.c` | The software harness, test + eval, and baseline memcpy |
| `prompts/` | `software.md`, `hardware.md`, `debug.md` |
| `cluster.yaml` | Single-machine cluster: chipyard, verilator-run, riscv-cross, antigravity containers |
| `dramsim_ini/` | DRAMSim2 config for the simulator's SimDRAM |

## TODO solutions

Parallelizing SW compile:

```python
            # TODO: make each software build launch in parallel
            build_cache.elfs[case.name] = compile_software(source, case)
            # END TODO
```

Getting the results of parallel SW compile:

```python
        # TODO: get the result of the parallelized software build
        elfs[build_cache_name] = get(build_cache_ref)
        # END TODO
```

Parallel simulation dispatch:

```python
        # TODO: run each simulation in parallel
        run_refs[name] = run_simulation(hw_build_artifact, elf)
        # END TODO
```

Getting the simulation results:

```python
        # TODO: get the result of the parallelized simulation
        run_results[name] = get(ref)
        # END TODO
```

Fix a dispatch TODO and its collect TODO together. Calling `get` on an
artifact that was already fetched raises a `ValueError`.

## Outputs

```
out/
  baseline/        the reference design (software memcpy, no accelerator)
  profile_<ts>/     ChiaProfileCollector.log: the chia profiler's timing of every
                   compile, build, simulation and LLM call in the job
  iter_<n>/
    summary.json   status, avg_speedup, parent, attempts (stage + detail each)
    memcpy_rocc.c  the harness with this design's body
    diff.json      chipyard diff: {repo: [diff lines]}, "" = chipyard root
    tests.csv      name, status, ret, mismatches
    eval.csv       name, status, bytes, cycles, bytes_per_cycle, speedup
                   (baseline cycles / cycles); last row: average speedup
    logs/          <timestamp>_<call>: software.md, hardware.md, debug_<k>.md
                   (prompt, answer, transcript), and per attempt the source,
                   compile output, disassembly, build stdout/stderr, sim log
```

The best design is the passing directory with the highest `avg_speedup` in `summary.json`.
