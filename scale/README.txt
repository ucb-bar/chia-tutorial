Scaled RoCC memcpy loop (CHIA / chialoops 2.0.0)
================================================

Scope
-----
All tutorial implementation and outputs are under scale/. accel/ is unchanged.
The phase sequence remains software -> hardware -> evaluate -> debug.
No startup-time optimization is included in this change.

Each agent owns one builder, SourceTool server, model conversation, build cache,
and results directory. Every hardware operation is hard-pinned by Ray node ID.
Builder leases prevent two scale drivers from sharing a mutable checkout.
Agents use different strategy prompts and adopt a shared best design only at
iteration boundaries. Only a complete, matching-fingerprint FULL-suite pass may
replace the shared best. Failed and slower candidates remain in the logs.
FULL here means the entire CONFIGURED suite, not necessarily all available
upstream cases. Current iteration settings select 1,024 of 4,488 available
vectors (512 tests + 512 benchmarks), with one warmup and one timed copy.
Treat improvements on this subset as provisional. For final validation, set
case_limit: 0 and benchmark_repeats: 3, then recompute the matching baseline.
Do not describe a 1,024-case pass as a full 4,488-case pass.

No third-party submodule changes
-------------------------------
The Chipyard MCP tools expose no builder shell. Models can read/list/search
Chipyard and submodules, but write_file only permits its own Scala sources:
  generators/chipyard/src/main/scala/*.scala (including nested directories).
No submodule edits, submodule resets, git revision changes, commits or pushes.
Reset removes/restores ONLY agent-created/edited files in that allowed directory.
Pre-existing image modifications outside it are fingerprinted and preserved.
Uninitialized, unused image submodules remain uninitialized.
The CLI also has built-in tools, which run in the MODEL container, not on the
builders. The MCP allowlist is not a general sandbox for untrusted code with
cluster access; do not expose the cluster or its tools to untrusted clients.

Hardware builds run in a short-lived Landlock-sandboxed process (Linux x86_64,
ABI >= 3 required). Only explicit build/cache/temp directories are writable.
All submodules, INCLUDING their ignored files, remain read-only. SBT targets
are redirected outside submodules to /tmp/chia-scale-sbt-projects/<project>.
DRAMSim builds use a non-Git scratch copy in /tmp/chia-scale-dramsim;
the original submodule remains read-only.
If protection or cleanup fails, the build stops rather than using stale RTL
or relaxing the policy. No permission-bypass flag is provided.

Commands from the repository root
---------------------------------
Prerequisites: Python 3.10.19, SSH, rsync, and a C compiler for the host tests.
Use an existing chialoops==2.0.0 environment, or create one:
  python3.10 -m venv scale/.venv
  scale/.venv/bin/python -m pip install 'chialoops==2.0.0'
  PY=scale/.venv/bin/python

Before provisioning, authenticate your GCP account (see setup_gcp.sh in the
repository root) and fill in your project/regions/resources in cluster.yaml.
That script is the broader tutorial bootstrap and also creates a separate dev
VM; it is not necessary to run the whole script just to authenticate for scale.
The example project ID is not a credential and must be replaced for your setup.
  $PY scale/up.py                         # Creates paid GCP resources.

  $PY scale/accel_loop.py --list-cases       # Offline suite counts.
  $PY -m unittest discover -s scale/tests -v
  $PY scale/run.py --preflight              # Read-only cluster checks.
  $PY scale/run.py --placement-check        # Real concurrent MCP placement check.
  $PY scale/run.py --baseline-only          # Real build + quick/full simulation, no model.
  $PY scale/run.py --agents 1 --iterations 1 # Single-agent integration run first.
  $PY scale/run.py --agents 3 --iterations 1 # Three asynchronous optimization agents.

After a FULL baseline passes, avoid repeating its simulations in later runs:
  $PY scale/run.py --agents 1 --baseline-run /home/ubuntu/scale/out/run_<id>
The loop rechecks every saved full-suite log and the harness/settings/source/
compiler fingerprint. A quick-only, incomplete, or incompatible run is rejected.
Only baseline measurements are reused; candidates still need their own builds
and complete quick/full validation. Model login state and identical-builder
counts do not affect measurement compatibility.

run.py uploads only tutorial source to /home/ubuntu/scale on the existing head.
It excludes .runtime/, .venv/, out/, and __pycache__/. No GCP/SSH/model credentials are
copied. The loop must execute on the head, not over a public Ray connection.
The entire scale driver is locked to avoid concurrent baseline/agent runs.

Model login is separate from GCP authentication. On the head, interactively run:
  stty rows 55 cols 120
  docker exec -it -e TERM=xterm-256color -e LINES=55 -e COLUMNS=120 scale-antigravity-0 agy
Select Google OAuth, open the displayed link on a separate browser-equipped
machine, and paste the returned authorization code into the terminal (not chat).
The larger terminal dimensions expose a code field that small terminals clip.
If restarting login, use the new link/code, not an earlier session's code.
Verify your available model and edit settings.yaml:model if needed. File-presence
preflight is not proof that OAuth tokens are valid; the model call surfaces real
authentication, billing and rate-limit errors without inventing an answer.
Three local client slots do not guarantee account-side concurrency or quota.

Model throttling and retries
----------------------------
model_client.py is a LOCAL adapter around CHIA 2.0.0; the installed package and
third-party submodules are not edited. An explicit nonempty final SUCCESS plus
exit code 0 wins over earlier recovered 429 warnings. Diagnostics remain logged.
A genuinely failed 429 gets at most two additional calls after 30s and 60s,
respecting a longer provider retry delay. If that delay exceeds 120s, stop rather
than retrying early. Explicit daily/weekly limits and auth/billing errors stop.
Each backoff is recorded next to the model transcript in *.retry_<n>.json.
Model timeouts and missing/empty/failed final outputs are never accepted as answers.

All three agents use the same login and therefore share its model-group quota.
Weekly budget remaining is NOT a promise of short-term rate/capacity availability.
The CLI's /usage panel refreshes quota status without generating a model answer.
No automatic model switch, paid-credit opt-in, or quota purchase is performed.
Candidates that send a dummy RoCC command then call software memcpy are rejected;
the software baseline remains available only as the frozen comparison reference.

Resource/settings change log
---------------------------
cluster.yaml:
  gcp_nodes.project / zone            Your project and per-role zones.
  gcp_nodes.*.machine_type / count    Physical VM sizes and counts.
  gcp_nodes.*.disks                   Hyperdisk size and zone-specific type.
    Builders 3 x 150 GB west = 450 GB; sims 2 x 100 + head 50 central = 250 GB.
    Existing deployed 300 GB disks are NOT shrunk automatically.
  available_node_types.*.num_workers / compatible_ips
    Match VM counts and explicit placement references.
  available_node_types.*.resources    Custom build/compiler/sim/model slots.
  worker_env_commands                Ray CPU budget for each container.
  docker.run_options                 Shared-memory sizing and limits.
settings.yaml:
  agents / iterations_per_agent / strategies / model
    Agent count, per-agent iteration budget, independent search strategies.
  build_jobs                         Build CPU parallelism, <= each builder's Ray CPUs.
  java_heap_gb                       Maximum Java/SBT heap per builder.
  compile_slots / simulation_slots    GLOBAL budgets shared by all agents.
  simulation_threads                 CPU + custom tokens consumed per simulation.
  max_case_bytes / case_limit / quick_cases_per_kind
    Available length cap, configured case count (0 = all), and quick samples.
    Sampling is deterministic and balanced across correctness/benchmark kinds;
    the default 1,024 preserves all represented size-band, length-tail,
    aligned/unaligned, address-order, and page-crossing categories.
  cases_per_binary / batch_work_bytes
    Adaptive batches: at most 8 cases AND at most 96 KiB of estimated work.
    Work = sum((copy length + 128 guard bytes) * invocation count).
    Benchmark invocations = warmups + repeats; correctness cases run once.
    Large copies get smaller batches. This is a runtime heuristic, not a guarantee.
  benchmark_repeats / benchmark_warmups
    Frozen timing protocol; baseline and candidates use identical values.
  max_attempts / *_timeout_*          Retry, wall-time and simulator-cycle limits.
  metrics_interval_seconds           Resource/peak-disk sampling frequency.
  model_rate_limit_retries            Additional attempts for genuinely failed 429s.
  model_backoff_seconds / model_max_backoff_seconds
    Initial exponential wait and maximum acceptable provider/backoff delay.

Keep the cluster large initially. Measure a COMPLETE build's disk peak before
reducing disks further; 150 GB builder disks have not yet been proven sufficient
for every candidate. Smaller disks require replacement, not an in-place shrink.

Results and interpretation
--------------------------
out/run_<timestamp>_<id>/ contains settings, source/environment fingerprints,
frozen cases, baseline results, per-agent attempts/prompts/patches/software,
best.json, run_summary.json, CHIA profiling, stages.jsonl, utilization.jsonl,
metrics_summary.json, and timing_summary.json. Each evaluation saves batches.json
and each simulator run's wall time and exit code in batch_*.timing.json.
Independent directory names avoid output races.
Hardware artifacts are cached only for the exact patch inside this run;
software ELFs are keyed by their COMPLETE rendered source, including cases.
No old accel/out baseline is reused with the larger suite.

Report separately: simulated cycle speedup, wall time per iteration, validated
candidates per hour, resource use by phase, and sampled peak disk usage.
More infrastructure does not establish an accelerator speedup. A successful
preflight, placement check, mock or host-mode test is NOT an end-to-end hardware
correctness/performance result. See benchmarks/ADAPTATION.txt for suite limits.
