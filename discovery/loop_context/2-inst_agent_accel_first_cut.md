# Implementation Plan: Bare-Metal RoCC DMA Accelerator for Chipyard

## Overarching goals
This will be used for a tutorial, so keep code impact small and simple. Source env.sh in the root directory of chipyard for the riscv toolchain.

Embench-IoT is cloned here: /scratch/jfx/cy-chia-tutorial/tests/embench-iot

Chipyard already defines certain linker scripts/flows for compiling binaries, try to reuse that when compiling Embench-IoT with the instructions below (when using --wrap=memcpy etc). Chipyard's existing binary compilation looks like:

```
riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c TestPackBitsDecompHello.c
riscv64-unknown-elf-gcc -static -specs=htif_nano.specs TestPackBitsDecompHello.o -o TestPackBitsDecompHello.riscv
```

But prioritize correctness, don't reuse and break correctness.

## 1. Project Objective

Implement a functional, coherent DMA accelerator using Rocket Chip's RoCC interface.

The accelerator will be integrated into Chipyard and evaluated using bare-metal Embench-IoT benchmarks running in cycle-accurate RTL simulation through Verilator or VCS.

The long-term goal is to investigate memory-access acceleration and optimizations across the cache hierarchy, including L1/L2 access policies, DMA scheduling, and cache-aware data movement.

**For this first iteration, the goal is correctness and functionality, not performance optimization.**

The implementation must:

- Use an RoCC accelerator attached to a Rocket core.
- Execute memory-to-memory DMA through the existing coherent TileLink memory hierarchy.
- Run with ordinary RISC-V GCC.
- Avoid modifying Embench benchmark source code.
- Avoid modifying the Rocket datapath, LSU, L1/L2 caches, or existing interconnect implementations.
- Support testing through software RTL simulation without an FPGA.
- Establish a foundation for future accelerator-only optimizations.

Do not introduce Linux, Buildroot, device drivers, or virtual-memory support.

## 2. Required Architecture

The accelerator should use the following architecture:

```text
             Bare-metal Embench-IoT
                      |
                memcpy() call
                      |
             Link-time DMA shim
                /           \
          CPU fallback    RoCC commands
                |              |
             CPU LSU      DMA Accelerator
                |              |
             L1 D$        TileLink Master
                |              |
                +-------+------+
                        |
                Coherent Memory
                   Hierarchy
                        |
                    L2 / DRAM
```

The RoCC accelerator must use Chipyard's existing `LazyRoCC` infrastructure.

Use `tlNode` to connect the DMA master to the existing coherent TileLink memory hierarchy.

The accelerator should issue ordinary TileLink read/write requests. It must not need to know whether a request hits in L2 or accesses DRAM.

Do not create separate L2 and DRAM DMA paths.

Do not add a separate system-bus connection unless the existing RoCC `tlNode` cannot support the required functionality in the repository version being used.

### L1 access

The accelerator should be structured so a future implementation can add the RoCC `io.mem` interface for direct L1 D-cache accesses.

However, **do not implement the L1 path in the first iteration**.

The first implementation will use coherent TileLink DMA exclusively.

This avoids unnecessary complexity while preserving the ability to introduce L1-versus-L2 access selection later.

## 3. Hardware Implementation

Implement a simple memory-to-memory DMA engine in Chisel.

The accelerator must support:

- Source memory address.
- Destination memory address.
- Transfer length in bytes.
- DMA read requests.
- DMA write requests.
- Completion reporting.
- Error reporting for unsupported operations or memory transaction failures.

### RoCC command interface

Use the existing RoCC custom instruction interface.

For the initial implementation, define two operations:

**CONFIG**

Accept two register operands:

- `rs1`: source physical address.
- `rs2`: destination physical address.

Store these in accelerator-local configuration registers.

**COPY**

Accept:

- `rs1`: transfer length in bytes.

Start the DMA transfer using the previously configured source and destination addresses.

Return a completion/status value through the RoCC response interface only after the DMA operation is complete.

Use one custom opcode with different `funct` values to distinguish the commands.

This avoids the need for a descriptor-fetch engine during initial development.

Document the exact opcode, function values, register usage, return codes, and completion semantics.

Keep the command interface isolated from the internal DMA datapath so future implementations can change scheduling, buffering, and memory-access policies without requiring changes to the C wrapper.

### DMA execution

Use the simplest correct DMA state machine:

```text
IDLE
  |
  v
RECEIVE_COMMAND
  |
  v
VALIDATE_TRANSFER
  |
  v
ISSUE_READ
  |
  v
WAIT_READ_RESPONSE
  |
  v
BUFFER_DATA
  |
  v
ISSUE_WRITE
  |
  v
WAIT_WRITE_ACK
  |
  v
ADVANCE_ADDRESSES
  |
  +-- More bytes --> ISSUE_READ
  |
  v
COMPLETE
  |
  v
RoCC RESPONSE
```

For the first implementation:

- Only one DMA transfer may execute at a time.
- Only one TileLink memory request needs to be outstanding at a time.
- Use a small internal buffer sufficient for a single transfer unit.
- Handle TileLink ready/valid backpressure correctly.
- Do not assume reads or writes complete in a fixed number of cycles.
- Use legal TileLink transaction sizes, alignment, masks, and source IDs.
- Do not assume the TileLink beat size or cache-line size without checking the configured interface.
- Do not issue the next write until the corresponding read data is available.
- Do not report completion until all writes have been acknowledged.
- Ensure zero-length transfers complete correctly.

It is acceptable to start with naturally aligned, fixed-width transfers. Unsupported alignment and lengths must be reported to the wrapper so it can use the CPU fallback.

If straightforward, extend the engine to handle byte tails and unaligned transfers using legal TileLink operations. Do not let support for arbitrary alignment block the initial working design.

The implementation should not silently truncate transfers.

### Transfer semantics

For version 1, implement `memcpy` semantics only.

Source and destination must not overlap. Overlapping transfers are handled by ordinary CPU `memmove`, not the DMA engine.

Support valid cacheable main-memory addresses. Do not attempt DMA to MMIO peripherals or unsupported regions.

Use the physical-address mapping of the bare-metal environment. The software wrapper must only submit addresses valid for the chosen memory interface.

## 4. Cache Coherence and Memory Ordering

**Coherence and correctness are mandatory.**

The accelerator must access memory through the existing coherent TileLink path.

It must correctly support the following scenarios:

**CPU writes → DMA reads**

The CPU writes data into a source buffer. The data may remain dirty in the CPU's L1 D-cache.

The DMA engine must read the latest data, not stale contents from L2 or DRAM.

**DMA writes → CPU reads**

The accelerator writes data into a destination buffer.

After DMA completion and the required synchronization, the CPU must read the newly written data even if an older copy of the destination cache line was previously cached.

**Repeated accesses**

Repeated DMA transfers to the same buffers must remain correct, including when CPU and DMA operations alternate.

Use the existing memory hierarchy's coherence machinery.

Do not implement custom cache invalidation, cache flushing, or direct DRAM bypass mechanisms.

### Ordering requirements

Define and implement a clear CPU–accelerator synchronization protocol.

The C wrapper must use appropriate compiler barriers and memory fences around DMA submission and completion.

The hardware must guarantee that completion means all DMA writes have been acknowledged.

Verify that CPU stores preceding submission are ordered appropriately before the DMA reads them, and that CPU loads following completion observe the DMA results.

Do not rely on coherence alone to guarantee ordering.

Do not assume a RISC-V `fence` automatically provides all required ordering for custom RoCC commands without validating the behavior of the selected Rocket implementation.

Use a blocking DMA API initially.

## 5. Scope Restrictions

### Allowed modifications

You may modify or add:

- The RoCC accelerator's Chisel/Scala implementation.
- Accelerator-local DMA state machines and buffers.
- Accelerator-local TileLink client logic.
- Minimal Chipyard configuration code to instantiate the accelerator.
- A bare-metal RoCC command wrapper library.
- A `memcpy` interception library.
- Embench board-support code and linker/build configuration.
- Simulation scripts, tests, assertions, and instrumentation.

### Forbidden modifications

Do not modify:

- Rocket's execution pipeline.
- Rocket's load/store unit.
- Rocket's instruction decoder.
- Rocket's existing L1 D-cache implementation.
- The L2 cache implementation.
- Existing cache replacement or insertion policies.
- Existing TileLink arbitration or coherence implementations.
- Existing DRAM controller implementations.
- GCC or the RISC-V compiler backend.
- Embench benchmark source files.

Do not introduce a transparent LSU interception mechanism.

Do not attempt to automatically intercept normal `ld` or `sd` instructions.

Only intercept supported library-level memory-copy operations.

Configuration-level wiring through existing Chipyard integration interfaces is permitted.

## 6. Bare-Metal Software Interface

Implement a small C library that controls the DMA accelerator.

Expose at least:

```c
int rocc_dma_copy(
    void *dst,
    const void *src,
    size_t length
);
```

The function should:

1. Validate the transfer's supported size and alignment.
2. Apply the required synchronization.
3. Issue RoCC CONFIG.
4. Issue RoCC COPY.
5. Wait for completion.
6. Apply the required post-completion synchronization.
7. Return success or an error.

The custom instructions may be emitted using existing Chipyard RoCC instruction macros or a small inline-assembly wrapper.

The toolchain must remain standard RISC-V GCC/binutils.

Do not require custom compiler passes, new GCC machine descriptions, or changes to benchmark compilation units.

The API is synchronous.

### `memcpy` interception

Implement a separate compatibility shim that intercepts eligible calls to `memcpy`.

Use GNU ld's `--wrap=memcpy` mechanism.

Conceptually:

```c
void *__wrap_memcpy(
    void *dst,
    const void *src,
    size_t n
)
{
    if (!dma_eligible(dst, src, n))
        return cpu_memcpy(dst, src, n);

    if (rocc_dma_copy(dst, src, n) != 0) {
        // Only use CPU fallback if DMA has not
        // partially modified the destination.
        handle_dma_error();
    }

    return dst;
}
```

The actual implementation must preserve standard `memcpy` semantics.

Requirements:

- Keep the original CPU-based `memcpy` available.
- Avoid recursion between the wrapper and its fallback implementation.
- Do not wrap `memmove` into DMA.
- Use CPU copying for unsupported sizes and alignments.
- Use CPU copying for transfers smaller than a fixed initial threshold.
- Keep the threshold configurable through a compile-time parameter.
- Do not perform workload-specific threshold tuning.
- Do not silently restart a partially completed failed DMA transfer using CPU copying unless correctness is guaranteed.

A simple, conservative fallback implementation is acceptable.

The wrapper should include a compiler memory clobber around custom-instruction submission as necessary to prevent the compiler from incorrectly reordering ordinary memory operations.

### Important limitation

Linker wrapping only intercepts eligible external symbol references.

Compiler-inlined copies and manually written load/store loops will not automatically invoke DMA.

Inspect the generated binaries to determine which memory-copy operations remain as external `memcpy` calls.

If required to preserve those calls, use `-fno-builtin-memcpy` consistently across CPU-baseline and DMA-enabled builds.

Do not implement custom compiler transformations.

## 7. Embench-IoT Integration

Integrate the accelerator with the existing bare-metal Embench-IoT build flow.

Do not modify benchmark source code.

Use Embench's existing SCons configuration interface, including `ldflags`, `user_libs`, and board-support files.

Add the DMA library and linker wrapping through the build configuration.

The intended approach is equivalent to:

```text
Normal Embench source
        |
   Existing GCC
        |
   Object files
        |
 Standard linker
   + --wrap=memcpy
   + DMA shim library
        |
 Bare-metal RISC-V ELF
```

### Build configurations

Support two configurations.

**CPU baseline**

- DMA disabled.
- Ordinary CPU `memcpy`.
- Same target architecture and compiler optimization flags as DMA mode.
- Existing benchmark functionality preserved.

**DMA enabled**

- Same benchmark source.
- Same compiler and optimization settings.
- Link-time `memcpy` interception enabled.
- Eligible transfers submitted to the RoCC accelerator.

Where practical, support a CPU-only mode through the same wrapper to distinguish library interception overhead from DMA execution overhead.

Maintain separate build directories so the baseline and DMA-enabled binaries cannot accidentally overwrite each other.

### Board support and runtime

Provide the Embench board-support functions required by its current SCons version.

Integrate the built ELF files with Chipyard's existing bare-metal RTL simulation harness.

Ensure that:

- Programs start normally.
- The configured Rocket hart executes the benchmark.
- Program exit and benchmark completion are detected.
- A failed benchmark returns a detectable failure status.
- Cycle counts can be collected without significantly perturbing the timed region.

Use the existing Chipyard startup/linker environment wherever practical rather than creating an unrelated bare-metal runtime.

### Benchmark coverage

Start with `wikisort`, which contains explicit `memcpy` calls.

Then build and run the broader Embench-IoT suite using the same configuration.

Do not assume that every benchmark invokes `memcpy` or that every invocation is large enough to benefit from DMA.

The accelerator must be evaluated as a general implementation, not specialized for `wikisort`.

## 8. Correctness Tests

Implement standalone bare-metal DMA tests before running Embench.

The tests should exercise the accelerator directly without relying on linker interception.

### Required test cases

**Basic transfers**

- Zero-length copies.
- One supported transfer unit.
- Multiple transfer units.
- Transfers smaller than a cache line.
- Transfers equal to a cache line.
- Transfers spanning multiple cache lines.
- Transfers larger than the L1 cache.
- Transfers larger than the L2 cache when practical.

**Alignment and boundaries**

- Aligned source and destination.
- Unsupported alignment handled correctly.
- Lengths that are not multiples of the transfer unit.
- Transfers across cache-line and page-sized address boundaries.

**Coherence**

- CPU-written source data still dirty in L1.
- CPU-cached destination overwritten by DMA.
- CPU reads immediately following completed DMA.
- Multiple CPU–DMA alternating writes and reads.
- Repeated copies between the same buffers.

**Protocol correctness**

- TileLink backpressure.
- Delayed read responses.
- Delayed write acknowledgments.
- Unsupported address/transfer errors.
- Commands issued while the accelerator is busy.
- Reset and idle behavior.
- Completion signaling.

**Memory integrity**

- Bytes outside the destination range must remain unchanged.
- Source data must not be modified.
- No bytes may be silently skipped or duplicated.
- Overlapping regions must be rejected or handled by CPU fallback.

Use deterministic data patterns and compare against a known-correct CPU implementation.

Add Chisel assertions where useful for detecting invalid DMA state transitions or TileLink protocol violations.

## 9. Simulation

All testing must run without FPGA hardware.

Use the repository's existing Verilator or VCS flow.

First confirm that the baseline Rocket configuration can run existing bare-metal software.

Then build the new Chipyard configuration with the accelerator instantiated.

Use Chipyard's existing `run-binary` infrastructure.

For example, the final workflow should support something equivalent to:

```bash
cd chipyard/sims/verilator

make CONFIG=<DMA_CONFIG>

make CONFIG=<DMA_CONFIG> \
  run-binary BINARY=<DMA_TEST_ELF>

make CONFIG=<DMA_CONFIG> \
  run-binary BINARY=<EMBENCH_ELF>
```

Determine the exact configuration and simulator arguments from the local repository version.

Do not assume the source tree uses the same configuration class names or build commands as upstream Chipyard.

Use waveforms selectively for debugging protocol or coherence failures.

The resulting RTL simulation must exercise the actual RoCC DMA hardware rather than replacing the DMA operation with a host-side software copy.

## 10. Performance Measurement

Performance tuning is out of scope, but basic measurement infrastructure is required.

Collect:

- Total benchmark execution cycles.
- Number of intercepted `memcpy` calls.
- Number of DMA-offloaded copies.
- Number of copies handled by the CPU fallback.
- Total DMA-transferred bytes.
- Total DMA operation cycles.
- DMA setup and completion overhead where practical.

For the standalone microbenchmark, compare CPU `memcpy` and DMA copy over several transfer sizes.

For Embench, compare complete program execution time using the same workload configuration.

Record both absolute cycles and relative performance.

It is acceptable, and expected, that the first accelerator may be slower than a CPU implementation.

**Do not optimize the hardware or change thresholds to improve benchmark results during this milestone.**

Also distinguish between benchmarks that used DMA and those where no eligible operations occurred.

A benchmark with zero actual DMA transfers does not demonstrate accelerator speedup or slowdown; it only validates compatibility with the DMA-enabled software environment.

## 11. Future Optimization Compatibility

Although no optimization is required initially, maintain clean boundaries between the RoCC command interface and the DMA datapath.

The design should permit future experimentation with:

- Accelerator requests through the L1 D-cache interface.
- L1-versus-TileLink memory access selection.
- Multiple outstanding TileLink transactions.
- DMA request pipelining.
- Larger DMA transfers.
- Internal data buffering and scratchpads.
- Cache allocation and bypass policies.
- Request scheduling.
- Accelerator-aware cache access policies.

Do not implement these features in the first iteration.

Keep the software-visible command contract independent of future memory-access decisions whenever possible.

The future accelerator should ideally be able to select an internal access policy without changes to the benchmark or wrapper library.

## 12. Required Implementation Sequence

Proceed in the following order.

### Milestone 1 — Inspect repository and establish baseline

- Identify the installed Chipyard and Rocket Chip versions.
- Identify the available RoCC examples and TileLink utilities.
- Identify the existing bare-metal test infrastructure.
- Confirm that an unmodified Rocket configuration passes a simple bare-metal test.
- Inspect Embench's build configuration and board-support requirements.

Do not modify foundational Rocket or TileLink source files.

### Milestone 2 — RoCC command bring-up

- Create the accelerator and Chipyard configuration.
- Implement CONFIG and a placeholder COPY command.
- Verify the accelerator receives commands and returns correct responses.
- Verify no illegal-instruction or RoCC handshake failures occur.

### Milestone 3 — Functional DMA datapath

- Implement the TileLink master.
- Implement the DMA read/write state machine.
- Support one transaction at a time.
- Implement correct completion and error reporting.
- Pass basic memory-copy tests.

### Milestone 4 — Coherence and correctness

- Test dirty CPU cache lines.
- Test DMA writes to CPU-cached destinations.
- Test synchronization and ordering.
- Test boundary conditions and backpressure.
- Resolve all observed data corruption and deadlocks.

Do not proceed to benchmark integration until the accelerator passes the correctness suite.

### Milestone 5 — Bare-metal DMA library

- Implement standard-GCC-compatible RoCC instruction wrappers.
- Implement synchronous `rocc_dma_copy()`.
- Implement CPU fallback.
- Implement `--wrap=memcpy` interception.
- Verify correct wrapping in a standalone C application.

### Milestone 6 — Embench integration

- Add the Embench board-support/build configuration.
- Build CPU-baseline and DMA-enabled variants.
- Confirm which benchmark binaries contain interceptable `memcpy` calls.
- Run `wikisort` first.
- Run the remaining Embench-IoT benchmarks.
- Verify correctness against CPU-baseline execution.

### Milestone 7 — Results and documentation

- Collect functional test results.
- Collect DMA operation counts.
- Collect benchmark cycle counts.
- Compare CPU-baseline and DMA-enabled performance.
- Document any tests that could not be executed.
- Document current implementation limitations.

## 13. Required Deliverables

Deliver the following:

1. RoCC DMA accelerator RTL implementation.
2. Minimal Chipyard configuration for instantiating the accelerator.
3. Documented RoCC command interface.
4. Bare-metal DMA C library.
5. `memcpy` interception shim with CPU fallback.
6. Automated standalone DMA correctness tests.
7. Embench-IoT build integration.
8. Embench baseline and DMA-enabled simulation scripts.
9. Performance and operation-count report.
10. README with reproducible build, simulation, and verification instructions.

Include explicit references to any modified files outside the accelerator's own source directory.

Explain why each such modification was necessary.

Do not leave unimplemented placeholders presented as completed functionality.

## 14. Completion Criteria

The implementation is considered complete when:

- The accelerator elaborates and builds successfully in Chipyard.
- The Rocket core and existing cache/memory-system implementations are unchanged.
- RoCC commands work in bare-metal simulation.
- The accelerator performs actual coherent memory-to-memory DMA.
- Required standalone DMA correctness tests pass.
- `memcpy` interception works without benchmark-source modifications.
- Embench-IoT compiles using the existing GCC-based build system.
- CPU-baseline and DMA-enabled benchmark runs produce equivalent correct results.
- At least one standalone integration test demonstrably executes DMA hardware.
- Actual DMA usage during Embench execution is measured and reported, even if it is zero.
- All implementation and verification steps are reproducible.

**Do not require a performance improvement for completion.**

The objective is a correct, reproducible baseline that can support subsequent DMA and memory-hierarchy optimization research.

## 15. Final Constraints

Prioritize a working implementation over architectural sophistication.

Do not modify the Rocket datapath, LSU, cache RTL, or existing memory-system components.

Do not introduce Linux-specific infrastructure.

Do not modify GCC or Embench benchmark sources.

Do not implement advanced cache policies prematurely.

Do not replace RTL DMA behavior with software emulation in the final validation flow.

If an implementation detail depends on the installed Chipyard version, inspect the actual source and use the compatible interface rather than assuming an upstream API.

If a requested feature conflicts with the restrictions, document the issue and implement the smallest safe alternative.

The final deliverable should be a functional bare-metal RoCC DMA accelerator, integrated into Chipyard and benchmarked with Embench-IoT under software RTL simulation.