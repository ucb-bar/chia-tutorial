Scaled RoCC memcpy loop (CHIA / chialoops 2.0.0)
================================================

HOW TO SCALE THE LOOP: EDIT THESE NUMBERS
=======================================
Start here. The two files to edit are scale/settings.yaml (loop settings) and
scale/cluster.yaml (cloud resources). Search for "TUTORIAL" in either file.
Replace the existing numbers with your own; <N> below means fill in a number,
not a literal YAML value. The defaults are a starting point, not requirements.

What you want          File / setting                       Current value
---------------------  -----------------------------------  -------------
More parallel agents   settings.yaml: agents                 3 (cluster capacity: 3)
More search iterations settings.yaml: iterations_per_agent   1 per agent
Different AI model     settings.yaml: model                  gemini-4-argon-medium (Enterprise)
AI reasoning effort    settings.yaml: model_effort           medium
More hardware builders cluster.yaml: parallel_gcp.count      3 VMs
Larger builder VMs     cluster.yaml: parallel_gcp.machine_type c4-standard-32
Faster hardware builds settings.yaml: build_jobs             24 per builder
More simulator VMs     cluster.yaml: benchmark_gcp.count      2 VMs
More concurrent sims   settings.yaml: simulation_slots       48 total
Larger JVM heap        settings.yaml: java_heap_gb           8 GiB per builder
More test cases        settings.yaml: test_cases             1024; 0 = all 4488
Timing samples         settings.yaml: benchmark_repeats      1 timed copy
Longer sim timeout     settings.yaml: simulation_timeout_seconds 1200 (20 min per batch)
Shorter debug feedback settings.yaml: debug_feedback_max_chars 4000 characters
More disk space        cluster.yaml: disk_size_gb            builder 150 / sim 100 GB

IMPORTANT: some numbers MUST change together.

1. To run <N> parallel agents, set settings.yaml agents: <N> and provide at
   least <N> strategies. In cluster.yaml, provide at least <N> builders:
   parallel_gcp.count and available_node_types.chipyard.num_workers must match,
   with one compatible_ips entry per builder (@parallel_gcp:0, :1, ...).
   Set available_node_types.antigravity.resources.antigravity_creds to at
   least <N>. Increasing --agents alone cannot exceed settings.yaml agents.

2. To use <M> simulator VMs, update benchmark_gcp.count, verilator_run.num_workers,
   and its compatible_ips list together. Each VM currently supplies 24 CPU and
   verilator_run slots. Set simulation_slots <= <M> * 24. With the default
   simulation_threads: 1, 48 slots permits at most 48 simultaneous simulations
   across ALL agents, not 48 per agent. More threads consume more slots per run.

3. Larger VM types do not automatically increase loop parallelism. Adjust the
   role's SCALE_WORKER_CPUS and resource tokens too. Keep build_jobs <= builder
   CPU budget; compile_slots <= riscv_build tokens. On the shared head, budget
   for Ray's 1 CPU plus compiler and model-container CPUs; leave OS headroom.

4. Check GCP regional CPU/disk quotas and model-provider limits BEFORE scaling
   up. More local model slots do not increase your account's quota. Resource
   changes require reprovisioning/restarting the affected infrastructure; editing
   YAML alone does not resize running VMs. Smaller disks require replacement.
   Cluster teardown deletes boot disks: preserve needed results first.

Changing case counts, repeat counts, or the timing protocol requires a matching
new baseline. More cases/repeats improve coverage but take longer; extra VMs
do not guarantee that every individual simulation will finish within 20 minutes.
See "Resource/settings reference (all editable knobs)" below for the full list.

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
test_cases: 0, keep benchmark_repeats: 1, and recompute the matching baseline.
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

Use the GCP/CHIA environment already configured by setup_gcp.sh (including its
CLOUDSDK_CONFIG). Its existing chia-dev VM is the head; no new head VM is created.
Fill in provider.head_ip with that VM's internal IP and update auth's gcloud SSH
alias for your project/zone. The dev user matches the client USER; its model
login is mounted from /home/<user>/.gemini. The existing google_compute_engine
key stays on the client; the setup script's SSH alias supplies the public route.
Head environment setup lives in cluster.yaml's native head_setup_commands.
  chia up scale/cluster.yaml               # Creates five paid worker VMs.
  chia up --add scale/cluster.yaml         # Add workers to an already-running cluster.

  $PY scale/accel_loop.py --list-cases       # Offline suite counts.
  $PY -m unittest discover -s scale/tests -v
  $PY scale/run.py --preflight              # Read-only cluster checks.
  $PY scale/run.py --placement-check        # Real concurrent MCP placement check.
  $PY scale/run.py --baseline-only          # Real build + quick/full simulation, no model.
  $PY scale/run.py --agents 1 --iterations 1 # Single-agent integration run first.
  $PY scale/run.py --agents 3 --iterations 1 # Three asynchronous optimization agents.

After a FULL baseline passes, avoid repeating its simulations in later runs:
  $PY scale/run.py --agents 1 --baseline-run /home/<head-user>/scale/out/run_<id>
The loop rechecks every saved full-suite log and the harness/settings/source/
compiler fingerprint. A quick-only, incomplete, or incompatible run is rejected.
Only baseline measurements are reused; candidates still need their own builds
and complete quick/full validation. Model login state and identical-builder
counts do not affect measurement compatibility.

run.py reads connection/auth/port settings from cluster.yaml and uploads only
tutorial source to ~/scale on that head. No runtime state file is required.
It excludes .runtime/, .venv/, out/, and __pycache__/. No GCP/SSH/model credentials are
copied. The loop must execute on the head, not over a public Ray connection.
The entire scale driver is locked to avoid concurrent baseline/agent runs.

After downloading results (see instructions.md), stop the cluster with:
  chia down scale/cluster.yaml
This removes the managed worker VMs/disks, not the setup-created chia-dev VM.
Results under the dev user's ~/scale/out remain. The dev VM still incurs charges.
Do not tear down or change heads during an active loop. Older deployments have
a separate scale-labeled head: archive its /home/ubuntu/scale/out first, because
native chia down deletes ALL scale-labeled VMs, including that legacy head.
This source change does not migrate running clusters or their saved results.

Model login is separate from VM provisioning credentials. The current run uses
the Enterprise project login for chia-codesign26oak-1967, location global, and
Gemini 4 Argon at medium effort. Verify model access in your own project
before using it; requests can incur project-billed token charges. The older
personal Starter login's weekly allowance is not an Enterprise allowance.
On the head, interactively run:
  stty rows 55 cols 120
  docker exec -it -e TERM=xterm-256color -e LINES=55 -e COLUMNS=120 scale-antigravity-0 agy
Select Google OAuth, open the displayed link on a separate browser-equipped
machine, and paste the returned authorization code into the terminal (not chat).
The larger terminal dimensions expose a code field that small terminals clip.
If restarting login, use the new link/code, not an earlier session's code.
Verify your available model and edit settings.yaml:model if needed. File-presence
preflight is not proof that OAuth tokens are valid; the model call surfaces real
authentication, billing and rate-limit errors without inventing an answer.
The cluster has three client/builder slots; the default loop uses three agents.
Local client slots do not guarantee account-side concurrency or quota.

Model throttling and retries
----------------------------
The default model is Enterprise Gemini 4 Argon (gemini-4-argon-medium),
with model_effort: medium. Its model ID and --effort both select medium.
model_effort: default omits --effort on every call, including debugging/retries.
The previous personal Sonnet 4.6 endpoint had a fixed Thinking preset and
rejected --effort; that restriction does not apply to the configured Gemini model.
For models that support adjustment, low/medium/high/xhigh/max pass --effort
explicitly. Effort is not a token cap; provider support must be checked.
Debug feedback selects compiler errors, failed cases, assertions, and nearby
source context rather than sending build-command boilerplate. It is capped at
debug_feedback_max_chars (4,000 by default), not a model-token budget. If no
recognized diagnostic is found, it sends the first line and a short log tail.
The cap applies only to the failure excerpt, not to the whole prompt/history.
Uncondensed failure.txt and raw build/simulation logs remain in each attempt;
failed software compilations also save batch_*.compile.stdout/stderr.txt.
Verify available IDs with agy models; model-group quota belongs to the login.
Baseline-only builds/simulations make ZERO model calls. Optimization uses AI to
design the software interface, implement hardware through source tools, and
debug failures. Each active agent has its own conversation. One loop-level
prompt can involve many model/tool turns, with growing conversation context;
three agents does NOT mean three model requests. With max_attempts: 5, each
agent can make two design prompts plus four debug prompts, before rate-limit
retries or CLI-internal tool turns. Tests/benchmarks themselves do not call AI.
Changing settings.yaml does not change the model of an already-running driver;
it takes effect on the next run. Reuse a compatible baseline to avoid redoing it.

model_client.py is a LOCAL adapter around CHIA 2.0.0; the installed package and
third-party submodules are not edited. An explicit nonempty final SUCCESS plus
exit code 0 wins over earlier recovered 429 warnings. Diagnostics remain logged.
A genuinely failed 429 gets at most two additional calls after 30s and 60s,
respecting a longer provider retry delay. If that delay exceeds 120s, stop rather
than retrying early. Explicit daily/weekly limits and auth/billing errors stop.
Each backoff is recorded next to the model transcript in *.retry_<n>.json.
Model timeouts and missing/empty/failed final outputs are never accepted as answers.

All agents use the same login and therefore share its model-group quota.
Weekly budget remaining is NOT a promise of short-term rate/capacity availability.
The CLI's /usage panel refreshes quota status without generating a model answer.
No automatic model switch, paid-credit opt-in, or quota purchase is performed.
Candidates that send a dummy RoCC command then call software memcpy are rejected;
the software baseline remains available only as the frozen comparison reference.

Resource/settings reference (all editable knobs)
-----------------------------------------------
cluster.yaml:
  provider.head_ip / auth             Existing dev head IP and setup SSH alias/key.
  gcp_nodes.project / zone            Your project and per-role zones.
  gcp_nodes.*.machine_type / count    Physical VM sizes and counts.
  gcp_nodes.*.disks                   Hyperdisk size and zone-specific type.
    Builders 3 x 150 GB west = 450 GB; sims 2 x 100 GB central = 200 GB.
    The existing dev head is outside gcp_nodes and is not resized or deleted.
    Existing deployed 300 GB disks are NOT shrunk automatically.
  available_node_types.*.num_workers / compatible_ips
    Match VM counts and explicit placement references.
  available_node_types.*.resources    Custom build/compiler/sim/model slots.
  worker_env_commands                Ray CPU budget for each container.
  docker.run_options                 Shared-memory sizing and limits.
settings.yaml:
  agents / iterations_per_agent / strategies / model / model_effort
    Agent count, per-agent iteration budget, independent search strategies.
    Model ID and reasoning effort (default = no CLI override).
  build_jobs                         Build CPU parallelism, <= each builder's Ray CPUs.
  java_heap_gb                       Maximum Java/SBT heap per builder.
  compile_slots / simulation_slots    GLOBAL budgets shared by all agents.
  simulation_threads                 CPU + custom tokens consumed per simulation.
  max_case_bytes / test_cases / quick_cases_per_kind
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
  debug_feedback_max_chars           Maximum failure excerpt characters sent to AI.
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
