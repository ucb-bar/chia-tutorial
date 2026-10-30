# Local tutorial timing

Measured on 2026-10-28 on this workspace's AMD EPYC 9R45 host with
32 available CPU cores. All measurements used local Docker; GCP timing remains
to be checked after moving the repository. No cloud instances were provisioned.

The final expanded suite was captured from the pinned build image and the
current `base/chipyard.patch`, then compiled from a fresh source tree. Container
images and their supplied dependency caches were already present. The simulator
and six RISC-V binaries were newly built. Full-buffer digests, guard checks,
and the chain witness remained enabled for fresh runtime inputs.

## Expanded coverage

| Workload | Previous copy calls | Current copy calls | Current payload |
| --- | ---: | ---: | ---: |
| `copy-small` | 64 | 256 | 16 KiB |
| `copy-stream` | 1 | 2 | 32 KiB |
| `copy-gather` | 16 | 64 | 32 KiB |
| `copy-scatter` | 8 | 32 | 32 KiB |
| `copy-chain` | 16 | 512 | 256 KiB |
| `copy-mixed` | 16 | 48 | 40,666 B |

Small, gather, and scatter working sets grew fourfold; streaming doubled and
now has a 64 KiB combined source/destination working set. The chain exercises
512 dependent transfers. Mixed copies cover 24 distinct lengths twice, adding
values immediately below and above the 128-, 256-, 512-, 1024-, and 2048-byte
boundaries with several unaligned offsets. Native reference tests also cover
seeds `1`, `0x8000000000000000`, and `0xffffffffffffffff`.

The 31-bit standalone checksums were regenerated for these sizes. The external
runtime-seeded full-buffer checks remain authoritative for loop correctness.
These timings describe the expanded source suite. Resumed runs keep their saved source seed.

## Measured runtimes at default concurrency

Two overlapping suites were submitted together: **16 RTL processes**, comprising
12 public tests and four private tests with fresh seeds. Each duration includes
binary staging, runner startup, RTL execution, HTIF output transfer, host hashing,
and runner cleanup. Private case identities, seeds, and outputs are omitted.

| Test | Runs | Wall seconds, min–max | Correctness |
| --- | ---: | ---: | --- |
| `copy-small` | 2 | 118.1–119.3 | Pass |
| `copy-stream` | 2 | 168.9–184.8 | Pass |
| `copy-gather` | 2 | 173.0–177.7 | Pass |
| `copy-scatter` | 2 | 177.3–185.9 | Pass |
| `copy-chain` | 2 | 102.1–102.7 | Pass |
| `copy-mixed` | 2 | 199.0–216.5 | Pass |
| `hidden-1` | 2 | 153.3–184.3 | Pass |
| `hidden-2` | 2 | 198.5–213.4 | Pass |

All 16 runs passed. The slowest test took **216.5 seconds**.
The complete parallel RTL phase took **216.5 seconds**
(218.8 seconds including local runner setup).

| Calibration stage | Measured seconds |
| --- | ---: |
| Capture current source from the pinned image | 80.2 |
| Hardware checkout staging | 8.9 |
| Fresh hardware compilation | 116.6 |
| Six software builds in parallel | 2.9 |
| Concurrent hardware/software stage, including container overhead | 131.5 |
| Sixteen RTL runs in parallel | 216.5 |
| **Complete fresh calibration round** | **432.5 (7 min 13 sec)** |

Build-stage rows overlap and are not additive. The complete-round measurement
includes source capture, one fresh hardware build, six C builds, **two overlapping
RTL suites**, correctness checks, container startup, and result collection.
A normal initial baseline evaluation runs one eight-test suite; this calibration
used two to check the default candidate concurrency. Agent generation was not
run during calibration.

The previous compact suite completed 16 concurrent RTL tests in 102.0 seconds.
Its smaller dimensions are superseded by the current suite.

## Estimated exercise duration

Defaults retain two optimization proposals with two agents running concurrently.
This **projection** combines measured baseline costs with the configured agent
timeout. It budgets the candidate hardware builds serially and their RTL phase
afterwards, leaving any build/simulation overlap as margin. The measured two-suite
calibration is used conservatively as the initial baseline allowance.

| Component | Seconds | Basis |
| --- | ---: | --- |
| Initial baseline allowance, including source preparation | 432.5 | Measured fresh calibration with two suites |
| Two concurrent AGY calls | 630.0 | 600-second print timeout plus 30-second exit grace; one attempt each |
| Two candidate build stages, serially | 263.0 | Twice the measured fresh build stage |
| Two parallel RTL rounds | 218.8 | Measured, 16 concurrent tests |
| Workspace replay, SQL, checkpoints, and cleanup | 60.0 | Allowance |
| **Projected complete exercise** | **1604.4 (26.7 minutes)** | Estimate using the rows above |

Actual AGY duration and changed candidate hardware build costs remain unmeasured.
Public and private RTL simulations use CHIA's native 30-minute timeout. Search
runs for the requested iterations. The exercise duration above is a planning estimate;
correctness determines validity, while simulated cycles determine performance.

Regular loop evaluations write `artifacts/CANDIDATE/timing.json`, including public
and anonymous private runtimes, builds, generation, evaluation, and selection-to-
completion round wall time with queueing. Resumed rounds include the interval
since their original selection. `artifacts/exercise-timing.json` records total
command wall time, including preparation and cleanup, plus completion status.

Normal CHIA placement still compiles on `chia-dev` and distributes public and
private RTL tests across existing `chia-worker-1` through `chia-worker-5`.
The local calibration uses one machine; use regular loop timing artifacts to
confirm the same budgets on GCP.
