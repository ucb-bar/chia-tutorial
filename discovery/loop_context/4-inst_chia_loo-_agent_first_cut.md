# CHIA Evolutionary Discovery Loop — RoCC DMA Accelerator

## 1. Objective

Implement a CHIA-based evolutionary hardware optimization loop for the existing bare-metal RoCC DMA memcpy accelerator in Chipyard.

The loop must use **Google Antigravity (`agy`) as the default coding agent**, while allowing Claude Code and Codex to be selected through configuration without changing the evolutionary algorithm or evaluation pipeline.

The goal is to optimize DMA performance across the full Embench-IoT benchmark suite and standalone DMA microbenchmarks while preserving functional correctness and the existing Rocket/SoC implementation.

**Reuse existing CHIA, SkyDiscover, and OpenEvolve functionality wherever possible. Do not reimplement existing algorithms, nodes, profilers, loggers, or database infrastructure.**

Chia can be found by sourcing env.sh in the Chipyard root.

This will be for a tutorial so ensure the code is readable and there is minimal code to parse.

### Reference implementations

- CHIA: https://docs.chialoops.ai/en/main/index.html
- CHIA architectural discovery: https://docs.chialoops.ai/en/main/case-studies/architectural-discovery-basic.html
- CHIA RoCC MemCpy example: https://docs.chialoops.ai/en/main/user_guides/memcpy_example.html
- evolve-flows: https://github.com/ucb-bar/evolve-flows
- SkyDiscover: https://github.com/ucb-bar/skydiscover
- OpenEvolve: https://github.com/algorithmicsuperintelligence/openevolve

Use the attached **Implementation Plan: Bare-Metal RoCC DMA Accelerator for Chipyard** as the authoritative reference for the existing DMA hardware architecture, shim, benchmark integration, and correctness constraints.

Implement the CHIA loop in the ${CY}/loop folder. This loop will be ran on a Google Cloud instance. AGY will use a google cloud project, the local one is authenticated already.

### Immutable Chipyard baseline and candidate isolation

Use the current local Chipyard checkout, including its existing RoCC DMA accelerator, Embench-IoT integration, memcpy shim, and configuration, as the authoritative starting implementation.

Do not replace it with a fresh upstream Chipyard checkout or modify the original implementation during evolutionary search.

Use CHIA's existing Docker-based worker infrastructure to isolate candidate generation, compilation, and evaluation from the original checkout.

Requirements:

- Capture the current working implementation as an immutable, reproducible seed snapshot, including relevant uncommitted changes and submodule revisions.
- Make this seed available to CHIA workers through a read-only mount, immutable artifact, or versioned container image.
- For each candidate, create a separate writable workspace from the selected parent candidate's immutable snapshot.
- Agents may modify only allowlisted accelerator implementation files within their assigned workspace.
- Use existing CHIA build and simulation nodes to evaluate candidate designs without modifying the original source tree.
- Multiple generation and evaluation tasks must operate concurrently without modifying each other's source trees or build directories.
- Preserve candidate source snapshots, patches, metadata, and evaluation results for future evolutionary sampling, replay, and checkpoint restoration.
- When restoring or forking an evolutionary experiment, materialize the exact historical candidate snapshot in a new isolated workspace.
- Never write generated RTL, compilation outputs, temporary files, or agent modifications back into the original Chipyard checkout.
- Enforce source isolation through filesystem permissions and automated verification, not merely instructions to the LLM.

Prefer CHIA's existing container lifecycle, artifact transfer, scheduling, and workspace facilities. Implement only the minimal candidate-isolation logic not already provided.

The original Chipyard checkout must remain unchanged even after thousands of evolutionary iterations, concurrent agent executions, failed builds, interrupted evaluations, or CHIA cluster shutdown and restart.

### Storing proposed designs
Each candidate should be saved persistently in an experiment directory associated with your CHIA discovery loop, rather than only inside the CHIA worker's Docker container.
For example:
cy-chia-tutorial/
├── chipyard/                    # Original, never modified
├── discovery/                   # CHIA evolutionary loop
│   ├── loop.py
│   ├── config.yaml
│   └── experiments/
│       └── run_001/
│           ├── checkpoints/     # Evolutionary state
│           ├── candidates/
│           │   ├── candidate_0001/
│           │   │   ├── rtl/     # Modified accelerator source
│           │   │   ├── patch.diff
│           │   │   └── metadata.json
│           │   ├── candidate_0002/
│           │   └── candidate_0003/
│           ├── metrics.db
│           └── artifacts/       # Build logs and reports
└── tests/
    └── embench-iot/

discovery/
├── loop.py
├── config.yaml
└── experiments/
    └── run_001/
        │
        ├── evolution.db              # SQLite database
        │   ├── population state
        │   ├── islands
        │   ├── MAP-Elites archives
        │   ├── parent-child history
        │   ├── evaluation metrics
        │   ├── LLM generation state
        │   └── checkpoint records
        │
        ├── checkpoints/              # Optional state snapshots
        │   ├── iteration_0001/
        │   └── iteration_0002/
        │
        └── candidates/
            ├── candidate_0001/
            │   ├── rtl/
            │   │   └── MemcpyAccel.scala
            │   ├── changes.patch
            │   └── metadata.json
            │
            ├── candidate_0002/
            │   ├── rtl/
            │   ├── changes.patch
            │   └── metadata.json
            │
            └── candidate_0003/
                ├── rtl/
                ├── changes.patch
                └── metadata.json

Each candidate would retain its exact accelerator implementation, parent relationship, generation metadata, and metrics.
The workflow would be:
1. CHIA creates an isolated writable Chipyard workspace inside a worker.
2. Antigravity/Claude/Codex modifies the accelerator.
3. CHIA saves the modified accelerator RTL (or an exact reproducible patch/snapshot) into the persistent candidate directory.
4. The candidate is compiled and evaluated.
5. Evaluation results are linked to the saved candidate.
6. CHIA can later restore any candidate by combining the immutable baseline with that candidate's saved changes.
My recommendation: Keep the candidate archive logically under discovery/experiments/, but configure its actual storage path independently. For example, /scratch/chia-experiments/ could hold large artifacts while the discovery-loop source stays small.
The container working directories can be temporary. The experiment archive must be a persistent host directory or volume, so restarting or deleting a CHIA container doesn't erase your candidates.
Also, do not commit every evolutionary candidate into the discovery-loop Git repository. Keep the archive Git-ignored or external; use hashes and metadata for reproducibility, and explicitly promote successful designs into Git when desired.

## 2. Agent backend selection

Support three interchangeable CHIA coding-agent backends:

- `AntigravityLLM` — default, using `agy`.
- `ClaudeCodeLLM`.
- `CodexLLM`.

Use the existing `chia.models` implementations and their documented `prompt()` interfaces.

The agent backend, model ID, concurrency, and relevant execution settings must be configurable.

Do not duplicate generation logic for each provider.

### Parallel candidate generation

The evolutionary search must support multiple independent candidate-generation tasks running concurrently.

Each task must receive:

- Its selected parent design.
- Its associated island and any optimization guidance.
- The selected inspiration programs.
- Relevant previous fitness feedback.
- The immutable hardware/software interface constraints.

Each generation task must execute in an isolated workspace. Concurrent agents may not edit a shared working tree.

The same evolutionary database and selection algorithm must work regardless of whether the candidate was generated by Antigravity, Claude, or Codex.

Agent sessions may be resumed when appropriate, using CHIA's native session handling. Sessions must be isolated by evolutionary branch to prevent unrelated candidates or islands from sharing accidental conversational state.

Persist generation results before evaluation. An interrupted run must be able to reuse a completed LLM proposal without invoking the agent again.

When switching between different agent backends, reconstruct the required context from persisted candidate history rather than attempting to transfer provider-specific conversational state.

## 3. Evolutionary methodology

**First investigate reusing SkyDiscover's `openevolve_native` algorithm through CHIA's existing `EvolverNode` and `ChiaEvaluator` integration.**

Evaluate whether it already supports:

- Concurrent candidate generation.
- Multiple independent islands.
- MAP-Elites archives.
- Fitness-biased parent selection.
- Inspiration sampling.
- Migration between islands.
- Checkpoint and resume.
- Integration with CHIA's three coding-agent backends.

If any feature is unsupported, first look for equivalent functionality in the existing SkyDiscover or OpenEvolve codebase.

Implement only the minimal adapter or extension necessary.

Do not implement a new evolutionary database or search algorithm when an existing implementation can meet the requirements.

### Islands

Maintain multiple independent evolving populations.

Each island must track its own population, elite candidates, and search history.

Optionally assign different optimization emphases to different islands, such as:

- Reducing small-copy latency.
- Increasing throughput for large copies.
- Improving request pipelining and concurrency.
- Reducing buffer overhead.
- Optimizing transfer scheduling.

These are exploration priorities, not restrictions on what an island can discover.

All islands must use a compatible global fitness definition so candidates can be compared and migrated.

Allow periodic migration of successful programs between islands.

### MAP-Elites

Maintain multiple elite designs within each island, divided into configurable behavioral or architectural feature bins.

Use meaningful, reproducibly measured or statically extracted features. Do not rely on unverified feature values reported by an LLM.

Preserve the best valid implementation in each occupied feature cell.

Also maintain a global best implementation across all islands.

Do not confuse the best design in an island with the collection of elites in its MAP-Elites archive.

### Parent selection

Reuse the evolutionary algorithm's existing selection mechanisms, including configurable random exploration, elite exploitation, and fitness-biased selection.

Support inspiration sampling from previously evaluated designs.

Only correct, successfully evaluated candidates may be admitted as valid elites.

A failed candidate may be retained as diagnostic feedback but must not displace a valid elite.

Support asynchronous completion of generations and evaluations. Population updates must remain consistent when results return out of order.

## 4. Hardware modification boundaries

The evolving agent may modify only the accelerator's explicitly allowlisted implementation files.

The accelerator is implemented using Chisel/Scala and Rocket's existing `LazyRoCC` infrastructure.

The initial fixed implementation uses the existing coherent TileLink path through `tlNode`.

The following must remain unchanged during evolution:

- Rocket datapath, instruction decoder, and LSU.
- L1/L2 cache implementations.
- Existing TileLink interconnect and coherence implementations.
- Outer SoC configuration and fixed integration harness.
- RoCC software-visible command interface.
- Bare-metal startup and runtime.
- DMA memcpy shim.
- Embench source and compilation configuration.
- Simulator/testbench instrumentation.
- Correctness tests and fitness calculations.

The implementation may improve accelerator-local buffering, pipelining, request scheduling, outstanding transactions, and related hardware techniques without altering the external protocol.

Do not introduce L1 cache tapping, additional SoC ports, or new bus topology modifications as part of normal evolution. Such architectural expansions require a separately established fixed integration configuration.

Use isolated Git worktrees or equivalent immutable source snapshots for each candidate. Enforce the changed-file allowlist automatically before compilation.

A candidate violating the file allowlist must be rejected.

## 5. Embench-IoT integration and evaluation

The existing bare-metal DMA implementation plan defines the benchmark integration.

Use the **link-time `--wrap=memcpy` interception shim** so eligible Embench memcpy calls invoke RoCC DMA operations.

Preserve:

- Existing benchmark sources.
- Standard RISC-V GCC toolchain.
- The defined Embench build flow.
- CPU fallback for unsupported transfers.
- Fixed DMA eligibility threshold and wrapper implementation.
- Coherence and ordering requirements.

The baseline CPU-only and DMA-enabled build configurations described in the attached plan must remain available.

The evolution loop must never alter the benchmark compilation configuration to improve its fitness.

### Evaluation pipeline

Use CHIA's existing `ChiselBuildNode`, `VerilatorRunNode`, and `ChiaEvaluator` mechanisms wherever compatible.

For each proposed accelerator:

1. Restore or materialize its immutable RTL snapshot.
2. Validate the modification allowlist.
3. Compile the SoC into one Verilator simulator executable.
4. Run standalone DMA correctness tests.
5. Dispatch every configured Embench-IoT benchmark as an independent Verilator task.
6. Dispatch standalone DMA performance microbenchmarks.
7. Collect the results through the existing CHIA evaluation interfaces.
8. Aggregate metrics into fitness.
9. Persist the candidate and evaluation results.
10. Update evolutionary archives and sampling state.

**All configured benchmarks must be dispatched as independent parallel tasks, with actual concurrency bounded by available CHIA resources.**

For example, approximately 20 Embench binaries may run in approximately 20 independent simulator processes if enough resources are available. Otherwise CHIA should queue them and execute as workers become available.

Do not compile the SoC separately for each benchmark.

Build and cache the benchmark ELF files independently of candidate RTL generation. Reuse them across candidate evaluations.

The candidate's simulator must perform real RTL DMA transactions; no host-side emulation may substitute for the accelerator.

### Benchmark coverage

Run the full configured Embench-IoT suite for each candidate.

Record which benchmarks actually invoke the DMA accelerator, including intercepted-copy counts, offloaded-copy counts, and transferred bytes.

For benchmarks without eligible DMA calls, continue checking correctness and total cycles, but do not interpret unchanged performance as evidence of DMA optimization.

Use the DMA-enabled seed accelerator, under the same fixed software configuration, as the primary evolutionary performance reference. Preserve CPU-only results as a separate comparison.

A change in DMA-offload counts or transferred bytes for the same deterministic workload must be treated as a diagnostic condition requiring investigation.

## 6. Fitness and metrics

Collect at least the following:

| Metric | Description | Purpose |
|---|---|---|
| Embench cycles | Measured execution cycles for each benchmark | Primary |
| Embench speedup | Seed DMA baseline cycles / candidate cycles | Primary |
| DMA copy cycles | End-to-end copy submission-to-completion latency | Primary |
| DMA bandwidth | Useful copied bytes / measured DMA duration | Primary for DMA microbenchmarks |
| DMA operation counts | Number of intercepted and offloaded copies | Validation and analysis |
| DMA transferred bytes | Amount of data actually processed by DMA | Validation |
| Correctness | Benchmark outputs, memory contents, and protocol checks | Hard constraint |
| Hardware cost | Area/timing estimates when available | Constraint or secondary objective |

### Fitness aggregation

Use one deterministic higher-is-better scalar fitness score for evolutionary selection.

Make the fitness aggregation configurable, with separate weights for:

- Embench benchmarks that actually exercise DMA.
- Standalone DMA microbenchmarks.

Prefer geometric-mean speedup aggregation over relevant workloads to prevent one exceptional speedup from dominating the overall fitness.

Preserve the complete metric vector rather than only its aggregate score.

Correctness is mandatory. Compilation failures, simulation timeouts, memory corruption, protocol errors, and benchmark validation failures must make a candidate ineligible for elite selection.

Include configurable area and timing constraints if reliable hardware estimates are available.

Do not alter benchmark weights, thresholds, or evaluation rules during an experiment.

### Measurement methodology

Collect hardware-performance measurements through existing testbench and simulation mechanisms.

Do not add profiling counters to the DMA critical path.

Use the original benchmark timing boundaries and passive simulation instrumentation where appropriate.

Distinguish simulation wall-clock time from simulated hardware cycles.

Track DMA setup overhead, active transfer time, and completion overhead separately wherever observable.

Record every benchmark's raw cycles, speedup, correctness, DMA activity, and aggregate fitness.

## 7. Profiling and logging

Use CHIA's existing profiling, tracing, and logging infrastructure.

Do not create a new profiler or logging system.

Use existing APIs such as `get_profiler().add_info()` and `get_profiler().log_event()` where appropriate.

Attach metadata including:

- Experiment/run ID.
- Candidate/design ID.
- Iteration and island ID.
- Agent backend and model.
- Parent and inspiration IDs.
- Build and simulation task IDs.
- Benchmark name.
- Simulated cycles and DMA measurements.
- Fitness and correctness outcomes.
- Build, evaluation, and generation wall-clock times.

Use the CHIA profiler for execution diagnostics, while persisting numerical benchmark and evolutionary history in the database.

## 8. Persistent database, cache, and checkpoints

Use CHIA's existing database nodes and APIs rather than implementing a new database service.

SQLite is acceptable for a single-host deployment with durable storage. PostgreSQL may be selected for a larger multi-worker deployment.

Use CHIA's caching and bypass functionality for replaying expensive completed ChiaFunction outputs.

Use stable call tags that identify the run, candidate, operation, and configuration version.

### Persistence requirements

CHIA's LRU cache must not be the sole authoritative store for evolutionary checkpoints.

Persist immutable design snapshots and candidate-generation outputs separately from the eviction-managed cache.

The database must link together:

- Evolutionary runs.
- Parent-child relationships.
- Islands and archives.
- Candidate snapshots.
- LLM calls and results.
- Generation attempts and agent session state.
- Build artifacts and results.
- Benchmark results and raw metrics.
- Fitness values.
- Checkpoint records.

Reuse any existing SkyDiscover/OpenEvolve checkpoint representation rather than duplicating its population-state serialization.

### Checkpoint stages

Persist recoverable state at important transitions:

1. Parent/inspiration selection.
2. Before dispatching an LLM generation request.
3. After a successful LLM proposal, before build/evaluation.
4. After a completed build.
5. After completed evaluation.
6. After evolutionary archive/population updates.

The post-generation checkpoint must allow the exact generated design to be restored and evaluated without another LLM call.

Checkpoint records must include enough state to restore the evolutionary database and selection behavior, not just a single candidate.

Record in-flight tasks so they can be safely retried after failure.

Use atomic state transitions and immutable artifacts to avoid partially committed candidates.

### Required restore capabilities

**Replay:** Select a historical candidate and reevaluate its exact RTL.

**Resume:** Shut down CHIA, restart the cluster, restore the full evolutionary state, and continue.

**Fork:** Select an arbitrary historical checkpoint, create a separate independent evolutionary run from it, and continue without modifying the original experiment.

Support the ability to start another CHIA cluster using the same preserved checkpoint, provided it receives a separate run identity, workspace, and writable database namespace.

A historical checkpoint must remain recoverable even if it is no longer in CHIA's LRU cache.

Preserve the original experiment when forking. Shared immutable artifacts may be reused.

Checkpointing must survive both CHIA cluster shutdown and host reboot, assuming the configured persistent storage remains available.

## 9. Reproducibility and isolation

Record the exact software and hardware configuration associated with every candidate:

- Accelerator RTL source hash.
- Parent candidate hash.
- Chipyard/Rocket source revision.
- SoC configuration.
- Toolchain and Verilator versions.
- Benchmark ELF hashes.
- DMA shim configuration.
- Agent backend/model/version.
- Generation prompts and relevant responses.
- Fitness configuration.
- Random seeds and archive state.

Each candidate must be reproducible from its stored artifacts and recorded configuration.

Agent workspaces and build directories must be isolated.

Agent permissions must not permit modification of benchmark results, evaluator logic, historical checkpoints, or unrelated candidate workspaces.

Limit the number of concurrent generations, builds, and simulations independently through existing CHIA scheduling/resource mechanisms.

## 10. Implementation sequence

### Phase 1 — Inspect existing code

Inspect the installed CHIA, evolve-flows, SkyDiscover, and DMA implementation.

Determine which existing components satisfy each required feature.

Document actual API names and any missing integration points.

Do not assume that SkyDiscover's API-based LLM support automatically supports CHIA's CLI coding agents.

### Phase 2 — Integrate the evaluator

Implement the smallest required integration around `ChiaEvaluator`.

Validate a baseline RTL build, standalone DMA tests, full Embench fan-out, metric extraction, and fitness calculation.

### Phase 3 — Integrate the coding agents

Use the existing CHIA Antigravity, Claude Code, and Codex implementations.

Verify that each backend can generate an accelerator-only modification in an isolated workspace.

Verify multiple generation tasks can operate concurrently.

### Phase 4 — Connect evolutionary search

Use an existing SkyDiscover/OpenEvolve evolutionary algorithm.

Connect parent/inspiration selection, candidate generation, and fitness feedback.

Validate island separation, MAP-Elites updates, parent selection, and migration.

### Phase 5 — Persistence and recovery

Connect evolutionary checkpoints to persistent storage.

Use CHIA caching and bypass to avoid repeating completed expensive stages.

Implement and test replay, resume, and fork operations.

### Phase 6 — End-to-end validation

Run an experiment containing multiple islands and multiple concurrently generated proposals.

Confirm that invalid candidates are rejected, all metrics persist, previous iterations are recoverable, and backend swapping does not reset evolutionary history.

## 11. Completion criteria

The implementation is complete when:

1. The original RoCC DMA design builds and passes the existing bare-metal tests.
2. The existing Embench memcpy shim is used without benchmark-source modifications.
3. All configured Embench binaries can execute through CHIA-managed parallel Verilator tasks.
4. Antigravity, Claude Code, and Codex can be selected via configuration.
5. Parallel candidate generation uses isolated workspaces.
6. Existing evolutionary search functionality supplies islands, MAP-Elites, parent sampling, and migration.
7. The same evaluator works for all three coding-agent backends.
8. Fitness includes actual Embench and DMA performance measurements.
9. Incorrect candidates never replace valid elites.
10. Per-candidate metrics and evolutionary lineage are durably recorded.
11. A historical LLM proposal can be restored without regenerating it.
12. The cluster can be completely shut down and restarted with evolutionary state preserved.
13. Any historical checkpoint can be forked into a separate experiment.
14. CHIA's existing profiling and logging infrastructure is reused.
15. No evolutionary proposal modifies Rocket, the SoC, the Embench source, the frozen software configuration, or evaluator code.

Keep implementation scope small. Prefer composition of documented APIs over new abstractions, and do not modify CHIA, SkyDiscover, or OpenEvolve internals unless a demonstrated missing extension point makes that unavoidable.