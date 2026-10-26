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

For comparison, the saved Embench RTL measurements in
`../../tests/fixtures/rtl-measurements.json` include 1,686 intercepted calls in
`nettle-sha256`, 249,600 offloaded bytes in `matmult-int`, 83,184 in `wikisort`,
and 12,400 in `sglib-combined`. These are comparisons of **copy traffic**; total
Embench CPU loads and stores were not measured. `/scratch/embench-iot` was
inspected for workload shapes; these tests do not depend on that checkout.

## Correctness and measurement

The tests expose Embench's `initialise_benchmark`, `warm_caches`, `benchmark`,
and `verify_benchmark` entry points. Initialization creates position-dependent
64-bit data and destination canaries. `benchmark()` performs the transfers and
returns an order-sensitive, positive 31-bit checksum of the entire output,
including padding. `verify_benchmark()` checks every output word or boundary
byte against an expected layout, the fixed checksum, source preservation, and
canaries. `copy-chain` checks both final buffers, accumulates observations of
every intermediate copy, and latches transient canary failures.

`workloads.json` records expected checksums and traffic. `check.py` independently
derives the expected final layouts in Python without executing the C copy loops.
Copies and checksum computation are inside `start_trigger`/`stop_trigger`;
initialization, full verification, and reporting are outside that interval but
still contribute to simulation wall time. Success returns status 0 and prints
`MEMORY_TEST`; a failed check returns 1. The existing board runtime also prints
the normal `EMBENCH` record for the existing metrics parser. The RTL simulator
supplies its usual `DMA_HW` and `DMA_COPY` records separately.

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
test to reject skipped copies, truncated copies, one-byte overruns, and source
corruption. The traffic auditor and fault injection code are host-only.

Baremetal builds use the existing DMA wrapper, board instrumentation, exit
workaround, RV64GC ABI, and HTIF runtime. Set `DMA_DIR` to `software/rocc-dma`
inside a prepared Chipyard source tree:

```sh
make -C base/memory-tests -j6 baremetal \
  CROSS_CC=riscv64-unknown-elf-gcc \
  DMA_DIR=/path/to/prepared/tree/software/rocc-dma
```

ELFs appear at `base/memory-tests/build/src/NAME/NAME`, matching Embench's
directory convention. The worker can override `CROSS_CC`, `BUILD`,
`TARGET_CFLAGS`, and `TARGET_LDFLAGS`. The Makefile preserves real `memcpy` calls
with `-fno-builtin-memcpy` and disables LTO. Host fortification is disabled for
these builds because fortified inline copies would bypass the linker wrapper;
the native traffic audit checks that interception works.

This change stages the suite under `base/`; the active workload inventory and
`backend.yaml` are unchanged. Before selecting it in the loop, include the suite
in the preserved seed and its content hash, use this Makefile as the software
build command, and set the inventory/ELF templates to its `src` and `build/src`
directories. Each executable can then run as an independent CHIA task.

## Timing budget

Defaults target a 3–5 minute **per-test RTL simulation** budget, with tests
distributed across host cores. Calibrate on the tutorial machine under the
intended parallel load: native execution and ISA simulation do not establish
RTL wall time. Simulator compilation and agent generation remain separate costs.

All six ELFs cross-compile with GCC 16.1.0 and the existing HTIF/DMA runtime.
All six also pass on Spike with `DMA_THRESHOLD=UINT64_MAX`, using software
fallback because Spike lacks the custom accelerator. This validates RV64
correctness and board output. DMA hardware correctness and the 3–5 minute
budget still require RTL runs. Keep the normal threshold for accelerator runs.
