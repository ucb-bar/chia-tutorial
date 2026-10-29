# Calibrated baremetal memory workloads

Six original C workloads model data movement around Embench-style record
processing, packet assembly, tiled buffers, and pipelines, omitting their
sorting, compression, hashing, and matrix arithmetic. Each executable runs one
bounded workload with fixed sizes and repetition counts, without a cache warmup
pass. Runtime seeds vary the input data. There is no heap, OS dependency, or
direct DMA instruction in the workload functions:
ordinary `memcpy` calls reach the existing DMA wrapper at link time.

| Workload | Copy calls | Copy sizes | Payload copied | Purpose |
| --- | ---: | --- | ---: | --- |
| `copy-small` | 256 | 64 B | 16 KiB | Command/setup overhead; distinct guarded records |
| `copy-stream` | 2 | 16 KiB | 32 KiB | Cache-line-aligned streaming; 64 KiB source/output working set |
| `copy-gather` | 64 | 512 B | 32 KiB | Permuted, padded input records assembled into linear output |
| `copy-scatter` | 32 | 1 KiB | 32 KiB | Linear input scattered to permuted, padded output tiles |
| `copy-chain` | 512 | 512 B | 256 KiB | Dependent ping-pong transfers with a changing first word |
| `copy-mixed` | 48 | 0–4,096 B | 40,666 B | Zero length, sub-threshold copies, unaligned pointers, and tails |

Payload counts exclude initialization, checksums, verification, and padding.
Each payload byte entails a logical read and write; actual bus traffic depends
on caching and request width. `copy-chain` deliberately reuses a small working
set. The first five workloads use disjoint, 8-byte-aligned ranges and lengths
eligible for the current DMA wrapper. The mixed workload also exercises software
fallback; 14 calls, totaling 16,256 B, are eligible at the 64 B threshold.

The larger working sets span more cache lines and permuted records. The chain
checks 512 dependent state transitions. The mixed workload visits 24 distinct
lengths twice, including the values immediately below and above 128, 256, 512,
1024, and 2048 bytes, with several unaligned offsets. Full-buffer checks cover
each destination and its surrounding guards. The native 30-minute simulation timeout
and runtime-varying correctness checks apply to the entire expanded suite.

## Correctness and measurement

The six `src/NAME/NAME.c` files are editable co-design workloads. Each implements
`initialise_benchmark()` and `benchmark()`. It may change algorithms, copying,
data layout, batching, or DMA usage.

Each neighboring `checksum.c` is fixed during evaluation: it owns the output
storage, computes the checksum, and compares it with the original expected value.
The harness calls that reader after the candidate workload returns. The evaluator
stages the original checksum readers independently of workspace edits, so simply
returning the expected number from the workload does not pass verification.
The 31-bit checksum algorithm is unchanged; expected values were regenerated
for the calibrated tutorial sizes.

The loop validates full-buffer SHA-256 digests, guard regions, and the chain
witness for fresh runtime inputs against a private independent Python reference.
All six scored outputs are checked; two additional randomly selected workloads
run with different private seeds on the existing simulation workers alongside
the public runs, without contributing to fitness. The reference stays on the
verification node. Returning an expected checksum, crafting a collision,
or hardcoding seed-zero output does not satisfy this gate. Copy counts, payload
volume, and DMA usage remain unconstrained.

Seed zero matches the calibrated-suite checksums in `workloads.json`; `check.py` audits
that standalone baseline. The evaluator patches a fixed ELF input record after
compilation for both scored and private runs. The harness transfers a framed binary buffer through HTIF after timing stops.
The worker retains the native CHIA output files briefly, hashes the original
bytes on the host, and returns a compact `MEMORY_OBSERVATION` digest record.
Both execution paths use the same transfer mode; the simulated CPU performs
no cryptographic hashing or per-word text formatting.

The fixed harness times initialization, execution, and checksum calculation in
one interval. `MEMORY_METRICS` reports `benchmark_cycles` and
`benchmark_instructions`; CPI is their ratio. The score maximizes geometric-mean
cycle speedup across all six tests at the modeled fixed clock. DMA observations
are recorded for inspection. Reporting is outside the timed interval.

## Build and validate

The same C tests build for two different machines. Paths below are relative to
`base/memory-tests/`:

| Directory | Contents | Runs on |
| --- | --- | --- |
| `src/` | C source files for the six tests | Compiled for either target |
| `build/native/` | Host executables using software `memcpy`, with traffic auditing and fault injection | Development machine |
| `build/src/` | RISC-V baremetal ELFs linked with the DMA wrapper and HTIF runtime | Simulated SoC through CHIA |

Native checks validate the test logic and expected results. Running the
baremetal ELFs on RTL validates the accelerator and measures its performance.

Native checks require a C compiler, GNU-compatible linker, Make, and Python 3:

```sh
make -C base/memory-tests -j6 check
```

This checks all six results and exact copy call/byte counts, and requires each
baseline test to reject skipped and truncated copies. These are development
checks of the original workload, not fixed traffic constraints on evolved code. The traffic auditor and fault injection code are host-only.

Baremetal builds use the existing DMA wrapper, board instrumentation, exit
workaround, RV64GC ABI, and HTIF runtime. Set `DMA_DIR` to `software/rocc-dma`
inside a prepared Chipyard source tree:

```sh
make -C base/memory-tests -j6 baremetal \
  CROSS_CC=riscv64-unknown-elf-gcc \
  DMA_DIR=/path/to/prepared/tree/software/rocc-dma
```

ELFs appear at `base/memory-tests/build/src/NAME/NAME`. The worker can override `CROSS_CC`, `BUILD`,
`TARGET_CFLAGS`, and `TARGET_LDFLAGS`. The Makefile preserves real `memcpy` calls
with `-fno-builtin-memcpy` and disables LTO. Host fortification is disabled for
these builds because fortified inline copies would bypass the linker wrapper;
the native traffic audit checks that interception works.

The loop actively selects this suite in `cluster.yaml`. Source preparation
copies its build inputs into `tests/memory-tests/` in the preserved seed; their
hashes identify new source seeds. For every candidate, the hardware `build` node and six native
`RiscvBuildNode.build_program` calls run concurrently and return the simulator
and six ELF byte strings. Six `simulate` calls run fresh runtime inputs; `parse`
reads cycle measurements, and the private gate checks full-buffer
digests plus two additional seeded runs before `aggregate` scores cycle speedup.
Source hashes include both
hardware and C code, so a software-only edit gets a new build/evaluation key.

## Timing budget

Each public or private RTL simulation uses CHIA's native 30-minute timeout.
A timed-out simulation returns a failed result and the proposed design fails its test.
The eight tests are submitted in parallel across the five existing worker VMs. See [local timing measurements](../../LOCAL_TIMING.md). Recalibrate on GCP
under the intended parallel load; local measurements do not establish GCP wall time. Simulator compilation and agent generation remain separate costs.

All six ELFs cross-compile with GCC 16.1.0 and the existing HTIF/DMA runtime.
All six also pass on Spike with `DMA_THRESHOLD=UINT64_MAX`, using software
fallback because Spike lacks the custom accelerator. This validates RV64
correctness and board output. DMA hardware correctness and the runtime budget
still require RTL runs. Keep the normal threshold for accelerator runs.
