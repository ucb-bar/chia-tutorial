# Reused infrastructure and validation

The tutorial uses the CHIA installed in `chia_env` by `setup_gcp.sh` and pins
SkyDiscover `78833ea6`. Tutorial integrations stay under `discovery/`. CHIA is unpatched.
Native HTML profiles display score metadata; native CSV tables summarize
execution durations by function.

| Responsibility | Owner |
| --- | --- |
| Ray head, search controller, SQL, cache, profiling | Current host's Python environment |
| Worker deployment/networking | Native CHIA YAML and `chia up/down` |
| Search concurrency/iteration processing | SkyDiscover `DiscoveryController` |
| Sampling, islands, MAP-Elites, migration, population save/load | Native `openevolve_native` |
| Build and simulation artifacts | CHIA nodes and cache/bypass |
| Numerical history and pending/completed proposals | CHIA `SQLiteNode` |
| Fixed base Chipyard/evaluator inputs | Build image + bundled patch, materialized as one immutable seed |
| Materialized accelerator sources | Separate native worker workspaces |

## Operating the tutorial

[README.md](README.md) contains the runnable procedures:

Commands run from the directory containing `loop.py` and `cluster.yaml`: the
repository's `discovery/` directory or the root of a copied tutorial folder.

- [Setup and run](README.md#set-up-and-run): `source load-env.sh`,
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
  the supplied code/state/seed archive, `source load-env.sh`,
  then `prepare`, `chia up`, and submit a `resume` job. Attendees use their own
  provider credentials; no Git checkout, rsync, fork, or metadata edits are needed.
- [Inspect logs and metrics](README.md#logs-and-consolidated-metrics): `chia job logs`,
  raw candidate logs, native CHIA profiling, and read-only consolidated SQL queries.

`run` and `resume` use the same recovery path. There is no special restore action:
selecting an older snapshot creates an independent fork, whose `latest.json`
points at that snapshot.

## Images and environment

`cluster.yaml` starts the Ray head directly on the current machine and uses Linux
amd64 upstream CHIA worker images tagged `:latest`. Native `chia up/down`
manages that head and the hardware-build, RISC-V-build, simulation, generation, and verification containers. There is
no top-level Docker configuration or head-container wrapper. The host supplies
Python 3.10.19, Git, Bash, Docker, SSH/Docker access, state storage, and credential
files. The existing `chia-dev` head runs generation/build/verification containers;
one simulation container runs on each of the five pre-provisioned
`chia-worker-N` VMs. The configuration describes existing hosts only.
Native `file_mounts` synchronize Python packages to each host; simulation
artifacts and inputs move through Ray, without shared bind paths.
Worker names pass directly to CHIA/SSH; this tutorial performs no cloud inventory lookup.

| Location | Tools and integration |
| --- | --- |
| Host head | `chia_env` from `setup_gcp.sh` (Python 3.10.19/Ray 2.54.0 and CHIA). `load-env.sh` adds SkyDiscover and its OpenEvolve dependencies, omitting the unused AlphaEvolve backend. SkyDiscover is head-only. |
| Build: CHIA chisel-build | Image Java/sbt/Verilator/RISC-V/C++ tools, Make, and dependency caches. Ray uses the image's base Python 3.10.19 to match the head exactly. |
| Software: CHIA riscv-cross | `RiscvBuildNode.build_program` receives inputs and builds each ELF in an isolated task directory; six slots permit parallel compilation. |
| Simulation: CHIA verilator-run | Image Python/Ray, spike-dasm, and runtime support; simulator/shared libraries arrive in the build artifact. |
| Generation: CHIA Antigravity | Published CLI with the setup.md login copied into a private worker home. |
| Verification: CHIA verilator-run | Private reference, parsing, and scoring; private RTL tasks run on simulation workers alongside public tasks. |

Workers use the CHIA installed in each image; only `chia_dma` is synchronized and
mounted read-only. Worker modules import no SkyDiscover. No host environment,
Conda tools, agent executable, sbt cache, or JVM is mounted as a dependency.
Builds use the image's dependency caches and explicit image C++ compiler path.
There is no derived image, host build-tool installation, job-level import
override, or CHIA package patch. Native YAML activates the host head's `chia_env` and
owns worker import environments. No host agent CLI is invoked.

The tutorial-facing `cluster.yaml` contains native infrastructure and four loop
knobs: model, effort, total iterations, and concurrent candidate rounds. Python
`defaults.py` supplies credential paths, retries, fixed search
policy, and build/runtime defaults. Native cache/bypass settings remain in YAML.
Host CPU affinity supplies hardware compilation jobs and simulation slots;
software has six native RISC-V build slots. Build heap settings are 32 GiB.
[base/chipyard.patch](base/chipyard.patch) carries DMA hardware/configuration,
observer, and DMA/HTIF runtime. The six C tests come from `base/memory-tests/`. `base/prepare.sh` runs in
the build image, copies tracked Chipyard/submodule inputs together with
their existing Git metadata, and applies the patch. Preparation records the
tutorial additions as baseline commits in those copied repositories. Image
name, patch/script hashes, and C build-input hashes
identify new seeds; they are not a resume compatibility gate. No host Chipyard source is read.
Builds reuse a private `/home/ray/dma-build` Git checkout; generation reuses a
private container-local `/home/ray/workspace` checkout and reads the preserved
baseline at `/seed`. Native CHIA `num_workers` creates the generation containers;
each initializes its own checkout during worker setup without a host workspace mount.
Startup copies the preserved repositories and Git metadata once. Each
candidate starts with Git reset/clean to the seed commits before its patch is applied. Candidate source changes are unrestricted. Git captures a
single binary-capable patch across the root and initialized submodule repositories;
additions, deletions, symlinks, and executable bits round-trip through SQL and
candidate restoration. The evaluator supplies
the original checksum, measurement runtime, and build harness as source bytes to
`RiscvBuildNode.build_program`; changing their workspace copies has no scoring effect.
Parsing and scoring require a dedicated verification worker resource. Each generation worker has one serial scheduling slot.

Native BashTool uses public node affinity to stay inside its caller's generation
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
It lives in evolve-flows, which the tutorial does not install; `load-env.sh`
installs SkyDiscover directly. At the pinned revision,
EvolverNode accepts a custom evaluator, but creates its own population/controller
and does not expose our CLI initializer, preserved population, context-builder, and
checkpoint callback. The tutorial therefore constructs the full upstream
DiscoveryController directly. `agent.py` contains generation; `evaluation.py`
keeps the CHIA build call, benchmark fan-out, verification, and SQL save in
execution order. `nodes.py` contains the required build/simulation, parsing, and
scoring tasks. Optional weighted scoring and DMA-event parsing live under
`archive/` and are not imported by the tutorial. The class no longer wraps the build in a second local build method.
`source.py` holds source preservation; `experiment.py` holds recovery. The refactor retains native controller iteration handling
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
submits the hardware build and six native RISC-V build tasks together,
collects their simulator and six ELFs, submits simulation tasks, then calls `parse` and
`aggregate` CHIA nodes with explicit data inputs. No parser is dynamically
loaded from the seed. Public Ray node affinity assigns successive tests to all
live simulation workers, then wraps around. Ray owns task queuing and execution;
there is no cloud lifecycle operation in the loop. Runtime-seeded checksums,
full-buffer digest verification, and simulator assertions remain enabled. Changed DMA activity
is allowed; fitness scores all six tests by cycle speedup. Retired instructions
and CPI explain the iron-law result at the modeled fixed clock. Compilation
failure and invalid measurements reject a candidate; infrastructure timeout or
interruption cancels unfinished tasks and leaves completed proposals recoverable.

The verification node samples runtime seeds after compilation and returns the
seeded binaries for the six scored runs. After collecting those results, it checks
a SHA-256 digest of every output word and the chain witness against its
independent Python reference,
while dispatching two randomly selected binaries with different private seeds
to the existing simulation workers alongside public tests. Both execution
paths use identical observation mode. Bulk HTIF transfer occurs outside the
measured region; native CHIA retains raw stdout until host-side hashing and
a temporary-directory context removes the transfer files afterwards.
Only pass/fail and anonymous runtimes escape the private gate. Failure yields validity/fitness zero,
prevents admission, consumes the proposal, and adds generic next-attempt feedback.
Private seeds and case outputs are never included in generation requests. The
private evaluator/reference files are excluded from the generation code mount.
This uses the native trusted Ray model, not a security boundary against arbitrary
malicious Ray clients or a compromised simulator executable.

## SQL recovery and checkpoint boundary

A `proposals` row records a selection before generation. A conditional SQL update
commits the complete Git patch/hash/response once, before generation returns. SQLite
writes are pinned to the head through CHIA; the database file is not mounted in
generation workers. SQL is authoritative; evaluation also writes a readable
`artifacts/ID/candidate.patch` copy.

Final numerical metrics repair the evaluation-complete/admission-pending window.
The first successful evaluation supplies the fitness reference; a failed initial
baseline remains a zero-score starting point so search can continue. Generation
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
completed proposals retained at the selected recovery boundary.
Cache eviction never deletes SQL proposals. There are no checkpoint
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
| `run.json` | Preserved-seed path (bundled `experiments/seeds/HASH/` preferred), optional loop Git revision/dirty flag, and fork provenance. |
| `metrics.db` | CHIA SQLite proposal/source archive and consolidated numerical history. Keep WAL/SHM companions while a writer is live. |
| `latest.json` | Name of the latest committed checkpoint, used by resume. |
| `checkpoints/SEQUENCE-SUFFIX/state.json` | Admitted candidate IDs and generated/evaluated flags for SQL requests at that boundary. |
| `checkpoints/SEQUENCE-SUFFIX/population/` | SkyDiscover's native program and population checkpoint files. |
| `deployment/cluster.yaml` | Rendered native CHIA configuration with absolute paths. Regenerate after relocation; retain the old copy until its cluster is down. |
| `cache/` | CHIA's evictable expensive-task outputs. Not the authoritative proposal or population archive. |
| `artifacts/ID/BENCHMARK.log` | Collected DMA-mode simulator output. |
| `artifacts/profile/ChiaProfileCollector.log` | Complete native CHIA JSONL task events and score metadata, appended across resumes. |
| `artifacts/profile/prof_full.log` | Task graph plus best-so-far geomean score metadata in native HTML; SVG dependencies and CSV execution timings. Exported at job exit. |
| `artifacts/profile/prof_candidates.log` | Native HTML of every completed evaluation and failed generation attempt, with failures scoring zero. Exported at job exit. |
| `artifacts/ID/candidate.patch` | Readable Git patch copied from the authoritative SQL proposal. |
| `artifacts/ID/NAME-build.log` | Individual hardware or software build output. |
| `driver.log`, `cluster-up.log` | Optional terminal captures created by the README's `tee` commands. |
| `../seeds/HASH/manifest.json` and `tree/` | Complete image-plus-patch source, Git/submodule metadata, and provenance hashes. Shared read-only across forks. |

Each generation container keeps its transient checkout at `/home/ray/workspace`;
it is reset to the chosen parent before each invocation. Patches and generation
transcripts are saved to SQL before returning, so replacing a container needs no
checkout recovery from the host. Unfinished generations may run again.

The `proposals` table is keyed by candidate `id`. It holds `parent`, `island`,
`sequence`, `kind` (`seed` or `search`), selected request context, the
plain-text `patch` relative to the seed commits, `patch_hash`, generation response/transcript,
provider/model, final numerical `metrics`, and native `program_id` linkage.
A NULL `patch` means generation is unfinished; a saved patch with missing
`metrics` means evaluation is unfinished. `metrics.validity = 0` means rejection.
`combined_score`, `source_bytes`, `cycles`, `instructions`, and `cpi` live in that metrics
JSON. `source_bytes` measures the UTF-8 patch size. An empty patch selects the
baseline and counts as completed generation. A program ID by itself does not prove committed admission: check the
checkpoint's `admitted` set.

The `benchmarks` table is keyed by `(candidate, name)`. Its JSON metrics retain
per-workload software cycles/results and summary activity counters. Detailed
hardware observer and copy-event records remain in raw simulator logs. Valid scored DMA rows additionally include speedup and correctness
fields. A row named `error` contains the rejection reason. The first successful evaluation
supplies the fitness reference; no `reference.json` is used. The README provides
queries using Python's standard-library SQLite client, so no separate database
CLI or metrics service is required. These queries can run alongside a live
writer. Raw logs are written when task results are collected; Ray logs/dashboard
provide worker diagnostics while tasks are still running.

## Restore contract and compatibility

A standalone native population directory is insufficient for this tutorial:
its program payloads refer to proposal IDs, and evaluation also needs SQL, the
preserved Git source seed. Candidate ELFs are rebuilt from the
saved hardware/C Git patch or reused from the evictable CHIA build cache. Preserve the following:

1. The selected committed checkpoint, including its `population/` and `state.json`.
2. Consistent `metrics.db` history, `run.json`, and `latest.json`. Use `fork` to collect/filter this set while the source
   is running; do not copy a live SQLite main file without its WAL or a backup.
3. The entire referenced seed directory, including `manifest.json`, the full
   tree, executable permissions, and symlinks. The seed is fixed and self-contained;
   restoring sources recreates a private checkout and applies all recorded changes.
4. A loop implementation that can read the saved SQL/checkpoint format, plus
   the published images, credentials, Docker, and SSH access.

The archive includes the loop implementation/configuration as well as saved
state; attendees run `source load-env.sh` after extraction.
No host Chipyard checkout, toolchain, or Git metadata is required.

`prepare` prefers the bundled seed under `experiments/seeds/HASH/` over the
original absolute path in `run.json`, even if the original seed still exists.
If no local seed is present, same-host forks can use the original shared seed.
No metadata edits are needed after extracting the archive. Keep seed contents
available; `prepare` reuses that seed and regenerates deployment with local
paths and current settings. It does not enforce a code/configuration fingerprint
or audit the seed contents against its manifest.

Historical forks remain optional: `fork` copies the selected native checkpoint,
filtered SQL, retaining a reference to the source seed. Raw
logs, task caches, deployments, and workspaces remain in the source run.

Tutorial resume policy:

- Resume trusts the saved seed and uses current settings. Code comments, changed
  evaluator files, dependency settings, CPU counts, worker setup, and search
  parameters do not block it. Existing `frozen_hash` fields are ignored.
- Existing scores and populations are retained when settings change; there is no
  comparison or automatic reconciliation of old measurements.
- Source fingerprints identify new seeds only. A missing saved seed, unreadable
  checkpoint/SQL data, or a patch that Git cannot apply still needs to be fixed.
- Checkout scheduling slots are normalized to one per build/generation worker;
  additional resources are accepted. A missing cache artifact is rebuilt.
- Metric parsing accepts extra fields and version changes, and missing instruction
  counters default to zero. SHA-256 output correctness and the native 30-minute
  simulation timeout still reject failed proposals. Simulation/build errors do
  not abort search. A failed initial baseline may seed search with zero fitness;
  the first successful evaluation becomes its performance reference.
- Builds use the available cores and a fixed 32 GiB heap. CPU counts need not
  match between runs and do not invalidate build-cache keys.
- Native checkpoints restore full populations, islands, valid archives, feature
  maps/statistics, best/iteration metadata, and migration counters. They do not
  save RNG state. Pending selections preserve their original parent/island and
  inspirations, but future sampling and asynchronous completion order can differ.
- A fork preserves work visible at its selected boundary. A normal latest-state
  resume additionally uses durable SQL completions made since that checkpoint to
  repair interruption before admission.

## Validation

The current checks cover the six-test workload contract, runtime-seeded outputs,
equal-weight scoring, incomplete/changed results, passive DMA observations, source
preservation, candidate C recompilation, captured ELF/checksum isolation,
relocation, permissive resume, and source-seed hashes.
Baseline native C checks audit original copy counts/bytes and reject skipped
and truncated copies. Evolved candidates must match the independent full-buffer
reference for fresh runtime inputs; copy counts remain diagnostic.

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

Public and private RTL simulations use CHIA's native 30-minute timeout; an
expired simulation returns a failed test result. All eight tests are submitted in parallel. Search runs
for the requested iterations, and per-round and command timings are saved under
`artifacts/`. See [LOCAL_TIMING.md](LOCAL_TIMING.md). Calibrate the baseline under
the tutorial machine's intended parallel load to estimate exercise duration.
Simulator compilation and agent generation have separate limits.

The previous Embench bundle, SCons build target/setup, dynamic parser loader,
and old benchmark measurement fixture have been removed. Its checkpoints require
the previous code and evaluation contract; create a new run for this suite.
