# Debug section — 30 minutes, on top of `accel/`

Base: the `roccaccel` branch (`accel/`). Our branch adds files only; it never
edits theirs.

---

## 1. What `accel/` already gives us

| Already there | What we do with it |
|---|---|
| `start_collector(...)` in `accel_loop.py` — every run writes `out/profile_<ts>/` | **Profiling block needs no code.** We just read the profile. |
| `IterDir.log(...)` — rich per-iteration logs in `out/iter_N/logs/` | The driver-side logging path. We add the **node-side** one. |
| `BuildCache` — reuses ELFs / simulator inside one run | The contrast: in-run memoisation vs CHIA cache, which survives runs. |
| 12 tests + 5 evals, `verilator_run: 8` | Real parallelism to show in the timeline and on a board. |
| `avg_speedup` per iteration | A ready-made scalar for the board. |

| Missing | Our block |
|---|---|
| no cache, no bypass, no `_chia_tag` | **Cache + bypass** |
| nodes never call `chia_log`; LLM `log_dir` unset | **Logging** |
| no board | **Boards** |

---

## 2. Timeline

| min | block | why this order |
|-----|-------|----------------|
| 0–2 | The 4 questions | — |
| 2–8 | **Profiling** | zero code: their loop already wrote a profile |
| 8–13 | **Logging** | read one stage; where each log actually lands |
| 13–23 | **Cache + bypass** | the big one: replay a 20-minute build in seconds |
| 23–28 | **Boards** | watch many iterations |
| 28–30 | Ray triage cheat sheet | — |

---

## 3. Blocks

### A. Profiling (2–8) — three slides

**A1. Look at what already ran.** No code. Angela's run left
`accel/out/profile_<ts>/ChiaProfileCollector.log`; we render it:

```bash
LOG=profiling/out/profile_20261019_030358/ChiaProfileCollector.log
chia viz-profile $LOG --format html                                   # timeline
chia viz-profile $LOG --format svg                                    # dependency graph
chia viz-profile $LOG --format table                                  # time per function
```

* `..._profile.html` — the timeline: 17 compiles at `riscv_build: 4`, the
  Chisel build as the one long bar, 17 sims at `verilator_run: 8`, the LLM
  calls between them, each bar naming its worker.
* `..._profile.svg` — the dependency graph: what waited for what.
* `profile_table.csv` — count and total seconds per function. This is the
  slide that justifies the bypass block: the build is the bill.

**A2. Add your own data.** Two calls, nothing else:

```python
get_profiler().add_info({"case": name, "bytes_per_cycle": bpc})  # inside a node
get_profiler().log_event("iteration_end", iteration=n, avg_speedup=s)  # driver
```

`add_info` merges into that call's event (visible in the timeline tooltip and
the CSV); `log_event` is a standalone record. The library nodes already do
this — `VerilatorRunNode` reports `simulation_cycles`, `ChiselBuildNode` the
config — so participants see the pattern before they write it.

**A3. Add a bar of your own.** A `@ChiaFunction` called **directly** (not
`.chia_remote`) is profiled as a local span, so a function that dispatches
several others becomes one bar with the children underneath:

```python
@ChiaFunction()
def build_everything(source):
    hw = build_hardware()                                    # dispatch
    elfs = [compile_software(source, c.name, c.cflags) for c in CASES]
    return get(hw), [get(e) for e in elfs]                   # wait

build_everything(source)        # called locally -> one bar over the whole phase
```

Now the timeline answers "how long did getting a testable design take",
not just "how long did each piece take".

### B. Logging (8–13)

Three paths, one example of each:

```
driver     IterDir.log(...)            -> out/iter_N/logs/...      (accel does this)
node       chia_log(text, path)        -> a file on the HEAD       (we add)
node opt   AntigravityLLM(log_dir=...) -> transcript on the HEAD   (one argument)
```

Point: `BuildArtifact.stdout` only reaches disk because the driver writes it.
`chia_log` lets the node write it itself, so it survives the worker dying.

### C. Cache + bypass (13–23) — the core

Why here: one `accel` iteration = a Chisel build + 17 sims + 3–6 LLM calls.
Nobody debugs orchestration at that price.

1. Tag the calls (in **our** wrappers, `debug_nodes.py`).
2. `debug_bypass.yaml`: `cache:` everything, `bypass:` the build and the LLM.
3. Run 1 populates (we pre-ship it), run 2 replays in seconds.
4. **Fault injection:** serve a recorded *failed* `BuildArtifact` for one tag
   and their debug path runs on demand, deterministically.

### D. Boards (23–28)

* `MetricsLogger` — `avg_speedup` per iteration, the loop's own objective; or
* `chia.trace.status` — `watch("sims")` on the driver, `report(...)` from each
  simulation, a live table of the 17 cases.

(Which one — open decision below.)

---

## 4. Files we add

```
debugging/README.md        this plan
accel/debug_timing.py          run their baseline with phase markers     [A2]
accel/debug_phase.py           the composite-bar ChiaFunction            [A3]
accel/debug_nodes.py           tagged wrappers around accel/nodes.py     [C]
accel/debug_loop.py            driver: cache + bypass + board            [C,D]
accel/debug_bypass.yaml        cache: / bypass: config                   [C]
```

The Python lives **in `accel/`**, as new files: `accel_loop.py` sets
`working_dir` to that folder, so `import nodes` only resolves there. Files in
`debugging/` would not be importable on the workers without changing their
`ray.init` — which we are not allowed to touch.

---

## 5. Findings that shape the plan

1. **Caching needs a tag.** `_maybe_wrap_cache` returns the ref unchanged when
   `_chia_tag` is None. `accel/nodes.py` passes no tags and we cannot edit it,
   so our own tagged wrappers are not a nicety — they are required.
2. **Two different `build`s collide.** `ChiselBuildNode.build` and
   `RiscvBuildNode.build` both key as `build` today, so one yaml entry hits
   both. Fix: separate them by tag regex (`hw_*` vs `sw_*`), or merge the
   qualified-key PR (`ucb-bar/chia` branch `qualname-keys`) and write
   `ChiselBuildNode.build`.
3. **The LLM is the other expensive, non-deterministic call.** Bypassing
   `prompt` with a recorded answer makes the whole section repeatable.

---

## 6. Prep before the tutorial

1. Run `accel_loop.py` once for real with caching on: keep a good run **and**
   a failed build.
2. Measure: the simulator binary's size (sets the cache budget) and one
   simulation's wall time (decides whether sims stay real in the demo).
3. Ship the cache directory + recorded LLM answers in the VM image.
4. Hand out the finished `debug_bypass.yaml` as a backup.

---

## 7. Open decisions

1. Boards: `MetricsLogger` (convergence) or `chia.trace.status` (live table)?
2. Do we merge `qualname-keys` first, or stay on tag regexes?
3. Does the section run on a pre-shipped cache only, or do we leave the 17
   simulations real so something still executes live?

---

## 8. Where our code lives

One subfolder per block. `accel/` is never edited: we copy the two driver
files and add the caching to the copies.

```
debugging/
  README.md
  cache/                  recorded results (git-ignored)
  caching/
    accel_loop.py         their driver + the cache setup and the tags
    nodes.py              their nodes + a _chia_tag on each dispatch
    cache.yaml            record: write every tagged call
    replay.yaml           replay: serve them back
  profiling/
    out/                  profiles this section records (git-ignored)
    debug_timing.py
```

Run ours exactly as you run theirs:

```bash
chia job submit --chia-cluster ../../accel/cluster.yaml -- \
    python "$(pwd)/accel_loop.py" --iterations 3            # record
    python "$(pwd)/accel_loop.py" --iterations 3 --replay   # replay
```

What the copies change (56 lines, `diff accel/accel_loop.py
debugging/caching/accel_loop.py`):

* `nodes.py` — each dispatch takes a `tag` and passes `_chia_tag=tag`
* `accel_loop.py` — each call site passes a tag built from the iteration and
  attempt, e.g. `hw_iter_3_evaluation_result1`
* `accel_loop.py` — `start_cache` + `Bypass` + provider/cond in `main()`,
  right after their `start_collector()`, and a `--replay` flag

`accel/` is still the source of `results`, `agent_prompt`, `dramsim_ini` and
`out/`, so the hill climb keeps reading the same designs.
