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

## Copy-ready TODO solutions

Add `from results import Case` to the `nodes.py` imports.

### `nodes.py`

`compile_software` body:

```python
    # TODO: Implement software compilation using RiscvBuildNode
    node = RiscvBuildNode(timeout_seconds=300)
    return node.build.options(resources={"riscv_build": 1}).chia_remote(
        node,
        source.encode(),
        case.name,
        "/tmp/accel_sw",
        target="verilator",
        extra_cflags=case.cflags,
        include_dump=True,
    )
```

`build_hardware` body:

```python
    # TODO: Implement hardware build using ChiselBuildNode
    node = ChiselBuildNode(
        chipyard_path="/home/ray/chipyard",
        config="FastRTLSimRocketConfig",
        target=BuildTarget.VERILATOR,
        make_jobs=8,
        timeout_seconds=4 * 3600,
        extra_make_args={"VERILATOR_THREADS": SIM_THREADS},
    )
    return node.build.options(resources={"chipyard": 0.9}).chia_remote(node)
```

`run_simulation` body:

```python
    # TODO: Implmenent simulation using VerilatorRunNode
    node = VerilatorRunNode()
    return node.run.options(resources={"verilator_run": SIM_THREADS}).chia_remote(
        node,
        artifact,
        elf.binary_content,
        elf.binary_name,
        "/home/ray/accel_sim",
        plusargs={"+loadmem": elf.binary_name},
        timeout_cycles=3_000_000,
        timeout_seconds=3600,
        dramsim_ini_files=dramsim_ini,
        verbose=False,
    )
```

`prompt_llm` body:

```python
    # TODO: Implement LLM prompt handling using AntigravityLLM
    return llm.prompt.options(resources={"antigravity_creds": 1}).chia_remote(
        llm,
        prompt,
        [chipyard_bash],
    )
```

### `accel_loop.py`

Inside the software compile `else` branch:

```python
        # TODO: Call the software compile nodes in parallel
        build_cache.elfs = {
            case.name: compile_software(source, case)
            for case in CASES
        }
```

Inside the hardware build `else` branch:

```python
        # TODO: Call the hardware build node
        build_cache.build = build_hardware()
```

In `evaluate_design`, resolve the dispatched compile and build references:

```python
    # TODO: Implement handling for the compiled software artifacts
    elfs: dict[str, RiscvBuildArtifact] = {
        name: get(ref) for name, ref in elf_refs.items()
    }

    # TODO: Implement handling for the hardware build artifact
    hw_build_artifact: BuildArtifact = get(build_cache.build)
```

In `evaluate_design`, dispatch simulations first, then collect their results:

```python
    # TODO: Launch all simulations in parallel
    run_refs = {
        name: run_simulation(hw_build_artifact, elf)
        for name, elf in elfs.items()
    }

    # TODO: Implement handling for the simulation results
    runs: dict[str, RunResult] = {
        name: get(ref) for name, ref in run_refs.items()
    }
```

LLM setup in `run_iteration`:

```python
    # TODO: create a AntigravityLLM instance for LLM interactions
    llm = AntigravityLLM(
        model="gemini-4-argon-low",
        system_message=(
            "You are an expert RISC-V hardware/software co-designer: bare-metal C, "
            "Chisel, and RoCC accelerators in Chipyard / Rocket Chip."
        ),
        timeout_seconds=3600,
        logging_name="accel",
        resume_session=True,
        work_dir="/home/ray/agy_workspace",
    )
```

Software write in `run_iteration`:

```python
    # TODO: Implement software write using the LLM
    llm_sw_write_response = get(prompt_llm(llm, prompt, chipyard_bash))
```

Hardware write in `run_iteration`:

```python
    # TODO: Implement hardware write using the LLM
    llm_hw_write_response = get(prompt_llm(llm, prompt, chipyard_bash))
```

Debug call inside the `if n > 0` branch:

```python
            # TODO: Implement debug handling using the LLM
            llm_debug_response = get(prompt_llm(llm, prompt, chipyard_bash))
```

In `main()`:

```python
        # TODO: implement the chipyard_bash tool using BashTool
        chipyard_bash = BashTool(
            name="chipyard_bash",
            work_dir="/home/ray/chipyard",
            timeout_seconds=300,
            task_options={"resources": {"chipyard": 0.01}},
        )
```

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
