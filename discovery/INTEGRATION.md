# Reused infrastructure and validation

The tutorial pins CHIA `a4cedf24`, evolve-flows `62d27130`, and SkyDiscover
`78833ea6`. All code changes stay under `discovery/`. Upstream CHIA is unpatched.

| Responsibility | Owner |
| --- | --- |
| Ray head, search controller, SQL, cache, profiling | Current host's Python environment |
| Worker deployment/networking | Native CHIA YAML and `chia up/down` |
| Search concurrency/iteration processing | SkyDiscover `DiscoveryController` |
| Sampling, islands, MAP-Elites, migration, population save/load | Native `openevolve_native` |
| Build and simulation artifacts | CHIA nodes and cache/bypass |
| Numerical history and pending/completed proposals | CHIA `SQLiteNode` |
| Fixed base Chipyard/evaluator inputs | Pinned build image + bundled patch, materialized as one immutable seed |
| Materialized accelerator sources | Separate native worker workspaces |

## Operating the tutorial

[README.md](README.md) contains the runnable procedures:

Commands run from the directory containing `loop.py` and `config.yaml`: the
repository's `discovery/` directory or the root of a copied tutorial folder.

- [Setup and run](README.md#set-up-and-run): bootstrap the host Python environment,
  `prepare` the bundled baseline inside the build image, native `chia up`,
  then `chia job submit` with the tutorial entrypoint. Routine commands run on the host.
- [Stop and resume](README.md#stop-and-resume): use `chia job stop` for the submitted
  job, then `chia down` with the original rendered deployment YAML. Ctrl-C while
  following logs only detaches the viewer. Submit a new job ID when resuming.
  `resume --run NAME` reads `latest.json`; increase the YAML proposal total to
  extend the search.
- [Choose a checkpoint](README.md#find-a-candidate-or-checkpoint): inspect committed
  `state.json` envelopes, then `fork --checkpoint NAME --new-run OTHER` or
  `fork --candidate ID --new-run OTHER`. No checkpoint selector lives in YAML.
- [Restore the tutorial archive](README.md#restore-the-tutorial-archive): extract
  the supplied code/state/seed archive, bootstrap the host Python environment,
  then `prepare`, `chia up`, and submit a `resume` job. Attendees use their own
  provider credentials; no Git checkout, rsync, fork, or metadata edits are needed.
- [Inspect logs and metrics](README.md#logs-and-consolidated-metrics): `chia job logs`,
  raw candidate logs, native CHIA profiling, and read-only consolidated SQL queries.

`run` and `resume` use the same recovery path. There is no special restore action:
selecting an older snapshot creates an independent fork, whose `latest.json`
points at that snapshot. `replay` is a separate evaluation command and is not
needed for a historical continuation.

## Images and environment

`config.yaml` starts the Ray head directly on the current machine and pins Linux
amd64 upstream CHIA worker image manifests by digest. Native `chia up/down`
manages that head and the build, simulation, and generation containers. There is
no top-level Docker configuration or head-container wrapper. The host supplies
Python 3.10.19, Git, Bash, Docker, SSH/Docker access, state storage, and credential
files. This configuration is single-host; bind paths are local to its Docker daemon.

| Location | Tools and integration |
| --- | --- |
| Host head | `.venv` beside `loop.py`: Python 3.10.19/Ray 2.54.0, pinned CHIA, and SkyDiscover from the evolve-flows submodule. Bootstrap installs the selected OpenEvolve dependencies, omitting the unused AlphaEvolve backend. |
| Build: CHIA chisel-build | Image Java/sbt/Verilator/RISC-V/C++ tools, Make, and dependency caches. Ray uses the image's base Python 3.10.19 to match the head exactly. |
| Simulation: CHIA verilator-run | Image Python/Ray, spike-dasm, and runtime support; simulator/shared libraries arrive in the build artifact. |
| Generation: CHIA Antigravity or Claude Code | Published provider executable; Codex uses the Claude image's Node/npm to install its pinned CLI in private scratch. |

Missing worker Python packages are installed inside each container's private
`/tmp/dma-python`; code checkouts are mounted read-only. No host venv,
Conda tools, agent executable, sbt cache, or JVM is mounted as a dependency.
Builds use the image's dependency caches and explicit image C++ compiler path.
There is no derived image, host build-tool installation, job-level import
override, or CHIA package patch. Native YAML activates the host head's venv and
owns worker import environments. No host agent CLI is invoked.

The tutorial-facing `config.yaml` shows native cluster placement, worker resources
and counts, Ray startup commands, and provider/search controls. A small recursive
merge overlays these on the fixed backend settings. Cache/bypass read their fixed settings directly from
the backend YAML. Host CPU affinity supplies compilation jobs and simulation
slots automatically; both build heap settings are fixed at 32 GiB.
[base/chipyard.patch](base/chipyard.patch) carries DMA hardware/configuration,
observer, and DMA/HTIF runtime. The six C tests come from `base/memory-tests/`. `base/prepare.sh` runs in
the pinned build image, copies tracked Chipyard/submodule inputs, and applies the
patch. The image's Chipyard revision is checked before applying it. Image digest,
base revision, patch/script hashes, evaluator hash, and C build-input hashes
identify the immutable seed and enter the frozen experiment contract. No host Chipyard source is read.
Builds use a fresh `/home/ray/dma-build` initialized from that preserved seed. Generation mounts the
same seed read-only at `/workspace`, with its two Scala and six C workload
files writable. Checksum readers and instrumentation remain read-only. Each generation worker has one serial scheduling slot.

Native BashTool uses public node affinity to stay inside its caller's restricted
worker. CLI credentials/scratch live in a private temporary home. Ordinary host
networking follows the CHIA examples' trusted-agent model; no private network,
firewall rules, private Ray cluster, or compatibility patches are installed.

## Search and evaluation

The top-level flow is `run_experiment()` followed by `run_search()` in `loop.py`.
Controller construction is `create_search_controller()` in that same file. Its public LLM initializer
provides the CHIA CLI adapter; its evaluator/context-builder hooks supply DMA
semantics. The database subclass wraps only public sample/add to preserve a
pending selection and its target island. No controller methods are copied.
[EvolverNode wraps SkyDiscover](https://docs.chialoops.ai/en/main/case-studies/architectural-discovery-basic.html#evolvernode-pluggable-evolutionary-agents).
It lives in evolve-flows; the same documentation's setup section installs both
evolve-flows and its SkyDiscover submodule separately. Bootstrap uses that one
submodule, without a second SkyDiscover checkout. At the pinned revision,
EvolverNode accepts a custom evaluator, but creates its own population/controller
and does not expose our CLI initializer, preserved population, context-builder, and
checkpoint callback. The tutorial therefore constructs the full upstream
DiscoveryController directly. `agent.py` contains generation; `evaluation.py`
keeps the CHIA build call, benchmark fan-out, parsing, scoring, and SQL save in
execution order. The class no longer wraps the build in a second local build method.
`experiment.py` holds source preservation and recovery. The refactor retains native controller iteration handling
and native population save/load; it does not implement another search loop.

The inspected upstream `ChiaEvaluator` uses `ray.get` on build/run handles,
including lists. CHIA cache-enabled handles carry callbacks that require
CHIA `get`; that function does not accept lists of callback handles. The upstream
evaluator also discards the build result before invoking `run_fn(workload=...)`,
requiring additional state or per-evaluation wrappers to transport our immutable
artifact safely across concurrent candidates. Replacing the small DMA adapter
would add code and lose callback/cancellation behavior.

The retained collector uses standard Ray wait, CHIA get, and CHIA cancellation.
It holds the callback handle until collection and passes only raw refs to
wait/cancel. There is no second scheduler. The pipeline loads a SQL proposal,
compiles each candidate’s hardware and C sources in one serial build node,
captures its six ELFs, submits their simulation tasks, then calls `parse` and
`aggregate` CHIA nodes with explicit data inputs. No parser is dynamically
loaded from the seed. Fixed checksums and simulator assertions remain enabled. Changed DMA activity
is allowed; fitness scores all six tests by cycle speedup. Retired instructions
and CPI explain the iron-law result at the modeled fixed clock. Compilation
failure and invalid measurements reject a candidate; infrastructure timeout or
interruption cancels unfinished tasks and leaves completed proposals recoverable.

## SQL recovery and checkpoint boundary

A `proposals` row records a selection before generation. A conditional SQL update
commits complete sources/hash/response once, before generation returns. SQLite
writes are pinned to the head through CHIA; the database file is not mounted in
generation workers. No proposal/source/diff file archive remains.

Final numerical metrics repair the evaluation-complete/admission-pending window.
The original seed's benchmark rows are also the sole fitness reference. Generation
recovers through its initial SQL lookup; the duplicate CHIA generation bypass
callbacks have been removed. Build/simulation cache and bypass remain native.
Native checkpoints own programs, best/iteration, islands, elite archives, feature
maps/statistics, and migration counters. Their small companion envelope records
admitted IDs and generated/evaluated flags from one SQL snapshot. It supplies
only the information missing from native checkpoints, not another population
serializer or per-stage task journal.

Fork copies the selected native checkpoint and a filtered SQLite backup. Later
proposals, generations, and evaluations are removed from the fork; pending
requests retain their original context. Consequently, a proposal completed
*after* a historical snapshot is not a completed proposal in that fork, even if
it exists in the source run now. The no-regeneration guarantee applies to
completed proposals retained at the selected recovery boundary. Replay
materializes the same source from SQL with fresh expensive-task tags and makes no LLM call or population
admission. Cache eviction never deletes SQL proposals. There are no checkpoint
migrations or format-version branches; create new runs after changing the loop.
Native RNG state is not checkpointed, so only already selected work is promised
exact context recovery.

Committed checkpoint directories use a monotonically increasing sequence plus a
unique suffix. A hidden temporary directory becomes visible through atomic
rename; only then is `latest.json` replaced. Sequence order, not filesystem
modification time, determines history. Candidate selection chooses the earliest
committed checkpoint whose admission set contains that candidate. This supports
full historical continuation while the source is live: checkpoint directories are immutable after atomic rename,
and SQLite's online backup captures history before the fork filters later work.
An absolute destination permits another loop checkout's experiment directory.
Separate live native clusters need distinct head/dashboard ports and a matching
`runtime.ray_address`; no custom deployment or networking controller is added.

## Experiment files and queries

Paths below are relative to `experiments/NAME/` beside `loop.py`, except for the shared seed.

| Path | Contents and use |
| --- | --- |
| `run.json` | Preserved-seed path (bundled `experiments/seeds/HASH/` preferred), frozen settings hash, optional loop Git revision/dirty flag, and fork provenance. |
| `metrics.db` | CHIA SQLite proposal/source archive and consolidated numerical history. Keep WAL/SHM companions while a writer is live. |
| `latest.json` | Name of the latest committed checkpoint, used by resume. |
| `checkpoints/SEQUENCE-SUFFIX/state.json` | Frozen settings hash, admitted candidate IDs, and generated/evaluated flags for SQL requests at that boundary. |
| `checkpoints/SEQUENCE-SUFFIX/population/` | SkyDiscover's native program and population checkpoint files. |
| `deployment/cluster.yaml` | Rendered native CHIA configuration with absolute paths. Regenerate after relocation; retain the old copy until its cluster is down. |
| `work/generation-N/` | Transient eight-file writable workspace for one serial generation worker. Reinitialized from the chosen parent before each invocation. |
| `cache/` | CHIA's evictable expensive-task outputs. Not the authoritative proposal or population archive. |
| `artifacts/ID/build.log` | Collected build stdout/stderr for a candidate. |
| `artifacts/ID/BENCHMARK.log` | Collected DMA-mode simulator output. |
| `artifacts/profile/ChiaProfileCollector.log` | Native CHIA JSONL task profiling; `chia viz-profile ... --format table` aggregates task durations. |
| `artifacts/ID/software-build.log` | Candidate C compiler output. |
| `driver.log`, `cluster-up.log` | Optional terminal captures created by the README's `tee` commands. |
| `../seeds/HASH/manifest.json` and `tree/` | Complete image-plus-patch implementation/evaluation source and provenance hashes. Shared read-only across forks. |

The `proposals` table is keyed by candidate `id`. It holds `parent`, `island`,
`sequence`, `kind` (`seed`, `search`, or `replay`), selected request context, the
eight complete Scala/C sources in JSON, `source_hash`, generation response/transcript,
provider/model, final numerical `metrics`, and native `program_id` linkage.
A missing `source` means generation is unfinished; a present source with missing
`metrics` means evaluation is unfinished. `metrics.validity = 0` means rejection.
`combined_score`, `source_bytes`, `cycles`, `instructions`, and `cpi` live in that metrics
JSON. A program ID by itself does not prove committed admission: check the
checkpoint's `admitted` set.

The `benchmarks` table is keyed by `(candidate, name)`. Its JSON metrics retain
per-workload software cycles/results, activity, hardware observer counters, and
copy events. Valid scored DMA rows additionally include speedup and correctness
fields. A row named `error` contains the rejection reason. Seed benchmark rows
are the sole fitness reference; no `reference.json` is used. The README provides
queries using Python's standard-library SQLite client, so no separate database
CLI or metrics service is required. These queries can run alongside a live
writer. Raw logs are written when task results are collected; Ray logs/dashboard
provide worker diagnostics while tasks are still running.

## Restore contract and compatibility

A standalone native population directory is insufficient for this tutorial:
its program payloads refer to proposal IDs, and evaluation also needs SQL, the
fixed source seed and matching settings. Candidate ELFs are rebuilt from the
saved hardware/C source bundle or reused from the evictable CHIA build cache. Preserve the following:

1. The selected committed checkpoint, including its `population/` and `state.json`.
2. Consistent `metrics.db` history, `run.json`, and `latest.json`. Use `fork` to collect/filter this set while the source
   is running; do not copy a live SQLite main file without its WAL or a backup.
3. The entire referenced seed directory, including `manifest.json`, the full
   tree, executable permissions, and symlinks. The seed is fixed and self-contained;
   restoring sources overwrites the two Scala and six C files in a private checkout.
4. The matching loop implementation/configuration and pinned dependencies, plus
   the pinned published images, credentials, Docker, and SSH access.

The archive includes the loop implementation/configuration as well as saved
state; attendees bootstrap `.venv` and pinned dependencies after extraction.
No host Chipyard checkout, toolchain, or Git metadata is required.

`prepare` prefers the bundled seed under `experiments/seeds/HASH/` over the
original absolute path in `run.json`, even if the original seed still exists.
If no local seed is present, same-host forks can use the original shared seed.
No metadata edits are needed after extracting the archive. Keep seed contents
and frozen evaluation/search settings unchanged; `prepare` checks those settings
and regenerates the deployment with local paths. It does not audit the seed's
contents against its manifest.

Historical forks remain optional: `fork` copies the selected native checkpoint,
filtered SQL, retaining a reference to the source seed. Raw
logs, task caches, deployments, and workspaces remain in the source run.

Compatibility is deliberately limited:

- No old checkpoint-format migrations or compatibility branches are supported.
  Use matching loop code, native dependency pins, and evaluation/search rules.
  `run.json` records a Git revision and dirty flag when available, not an archive
  of loop code. A copied loop needs no repository or commits: revision is null
  without a commit, and the dirty flag is null outside a repository.
- `prepare` rejects a changed frozen configuration. Fixed baseline patch/script, memory-test build inputs, evaluator code, hardware/software,
  fitness, native search policy, dependency pins, and build/simulation worker
  image/mount/environment/setup contracts are part of that hash. It is not a
  complete toolchain binary fingerprint.
- Provider/model, total proposal count, in-flight limit, placement, resource
  counts, and Ray connection ports can change between resumes. Redeploy changed
  workers with native `chia down/up`. Generation workspace serialization remains
  enforced.
- Automatic host CPU count is included as `hardware.make_jobs` in frozen settings.
  Tutorial machines must use matching CPU-affinity counts; a different count
  fails the restore check. Builds use all available cores and a fixed 32 GiB heap.
- Native checkpoints restore full populations, islands, valid archives, feature
  maps/statistics, best/iteration metadata, and migration counters. They do not
  save RNG state. Pending selections preserve their original parent/island and
  inspirations, but future sampling and asynchronous completion order can differ.
- A fork preserves work visible at its selected boundary. A normal latest-state
  resume additionally uses durable SQL completions made since that checkpoint to
  repair interruption before admission. Replay uses a new ID/fresh task tags,
  records new metrics, and never adds a population member.

## Validation

The current checks cover the six-test workload contract, fixed checksums,
weighted scoring, incomplete/changed results, passive DMA observations, source
preservation, candidate C recompilation, captured ELF/checksum isolation,
relocation, and the frozen-input hashes.
Baseline native C checks audit original copy counts/bytes and reject skipped
and truncated copies. Evolved candidates need only match the fixed checksums.

```sh
make -C base/memory-tests -j6 check
python -m pytest -q tests/test_contract.py tests/test_preservation.py
```

The six baremetal ELFs compile from the reduced Chipyard patch plus the staged
memory-test sources, with the existing RV64GC/HTIF runtime. Software-fallback
Spike runs check RV64 execution and the new `MEMORY_METRICS` output. They do not
validate custom DMA hardware or RTL timing. Distributed controller/worker tests
require local sockets, Docker, and SSH; observer regression tests require
Verilator.

Each simulation is limited to 300 seconds and 10 million cycles. Calibrate the
baseline under the tutorial machine's intended parallel load before relying on
that budget. Simulator compilation and agent generation have separate limits.

The previous Embench bundle, SCons build target/setup, dynamic parser loader,
and old benchmark measurement fixture have been removed. Its checkpoints require
the previous code and evaluation contract; create a new run for this suite.
