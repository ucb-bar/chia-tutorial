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
| `cluster.yaml` | Single-machine cluster: chipyard, riscv-cross, antigravity containers |
| `dramsim_ini/` | DRAMSim2 config for the simulator's SimDRAM |

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
