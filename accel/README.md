# accel: an agentic RoCC memcpy accelerator

An LLM co-designs a memcpy RoCC accelerator for a Rocket SoC. CHIA cross-compiles the software, builds the hardware into a Verilator simulator, runs a test-and-benchmark harness on it, and hands any failure back to the LLM to debug. Every iteration starts from
the best design so far.

## Running

```bash
export THIS_MACHINE=$(hostname -I | awk '{print $1}')
cd chia-tutorial/accel
chia up --yes cluster.yaml
chia job submit --chia-cluster cluster.yaml -- \
    python "$(pwd)/accel_loop.py" --iterations 3
chia down --yes accel/cluster.yaml
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

Step 1, software compile dispatch:

```python
            # TODO: make each software build launch in parallel
            build_cache.elfs[case.name] = compile_software(source, case)
            # END TODO
```

Step 2, hardware build dispatch (with step 1 fixed, this now runs while the
compiles do):

```python
        # TODO: have the hardware build launch in parallel with software builds
        build_cache.build = build_hardware()
        # END TODO
```

Step 3, collect the compiled software:

```python
        # TODO: get the result of the parallelized software build
        elfs[build_cache_name] = get(build_cache_ref)
        # END TODO
```

Step 4, collect the hardware build:

```python
    # TODO: get the result of the parallelized hardware build
    hw_build_artifact: BuildArtifact = get(build_cache.build)
    # END TODO
```

Step 6, simulation dispatch:

```python
        # TODO: run each simulation in parallel
        run_refs[name] = run_simulation(hw_build_artifact, elf)
        # END TODO
```

Step 6, simulation results:

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
