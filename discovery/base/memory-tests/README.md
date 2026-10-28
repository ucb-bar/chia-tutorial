# Short baremetal memory workloads

Six original C workloads model data movement around Embench-style record
processing, packet assembly, tiled buffers, and pipelines, omitting their
sorting, compression, hashing, and matrix arithmetic. Each executable runs one
bounded pass, with no repetition multiplier or cache warmup pass. There is no
heap, OS dependency, random input, or DMA instruction in the test sources:
ordinary `memcpy` calls reach the existing DMA wrapper at link time.

| Workload | Copy calls | Copy sizes | Payload copied | Purpose |
| --- | ---: | --- | ---: | --- |
| `copy-small` | 2,048 | 64 B | 128 KiB | Command/setup overhead; distinct guarded records |
| `copy-stream` | 16 | 16 KiB | 256 KiB | Cache-line-aligned streaming; 512 KiB source/output working set |
| `copy-gather` | 256 | 512 B | 128 KiB | Permuted, padded input records assembled into linear output |
| `copy-scatter` | 128 | 1 KiB | 128 KiB | Linear input scattered to permuted, padded output tiles |
| `copy-chain` | 128 | 1 KiB | 128 KiB | Dependent ping-pong transfers with a changing first word |
| `copy-mixed` | 256 | 0–4,096 B | 147,120 B | Zero length, sub-threshold copies, unaligned pointers, and tails |

Payload counts exclude initialization, checksums, verification, and padding.
Each payload byte entails a logical read and write; actual bus traffic depends
on caching and request width. `copy-chain` deliberately reuses a small working
set. The first five workloads use disjoint, 8-byte-aligned ranges and lengths
eligible for the current DMA wrapper. The mixed workload also exercises software
fallback; 112 calls, totaling 130,048 B, are eligible at the 64 B threshold.

## Correctness and measurement

The six `src/NAME/NAME.c` files are editable co-design workloads. Each implements
`initialise_benchmark()` and `benchmark()`. It may change algorithms, copying,
data layout, batching, or DMA usage.

Each neighboring `checksum.c` is fixed: it owns the output storage, computes the
checksum, and compares it with the original expected value. The harness calls
that reader after the candidate workload returns. Returning a constant from the
editable workload cannot replace the checksum of its actual output buffers.
The original 31-bit checksum algorithm and expected values are unchanged.

The only functional acceptance condition is matching all six expected checksums.
Full output equality, source preservation, unchanged intermediate states, copy
counts, payload volume, and DMA use are not required. Fixed-input precomputation
and checksum collisions are therefore admissible under this objective.
`workloads.json` holds the fixed expected values and original traffic counts.
`check.py` independently derives the baseline checksums in Python.

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

The loop actively selects this suite in `backend.yaml`. Source preparation
copies its build inputs into `tests/memory-tests/` in the preserved seed; their
hashes enter the frozen evaluation contract. For every candidate, the serial `build`
CHIA node applies the hardware and C sources and captures its simulator and all
six ELFs. Six `simulate` calls run them, `parse` checks each fixed checksum, and
`aggregate` scores cycle speedup against the seed. Source hashes include both
hardware and C code, so a software-only edit gets a new build/evaluation key.

## Timing budget

The loop enforces a 300-second wall-time limit and a 10-million-cycle limit
per test, with tests distributed across host cores. Calibrate on the tutorial
machine under the
intended parallel load: native execution and ISA simulation do not establish
RTL wall time. Simulator compilation and agent generation remain separate costs.

All six ELFs cross-compile with GCC 16.1.0 and the existing HTIF/DMA runtime.
All six also pass on Spike with `DMA_THRESHOLD=UINT64_MAX`, using software
fallback because Spike lacks the custom accelerator. This validates RV64
correctness and board output. DMA hardware correctness and the 3–5 minute
budget still require RTL runs. Keep the normal threshold for accelerator runs.
