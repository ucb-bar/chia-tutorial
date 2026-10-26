## Additional requirement: Performance metrics and observability for CHIA evolutionary optimization

The accelerator will subsequently be optimized using a CHIA evolutionary discovery loop.

The initial implementation must expose sufficient metrics and observable hardware behavior for the evolutionary evaluator to measure performance and correctness without modifying the accelerator or benchmark integration later.

### 1. Do not introduce unnecessary hardware counters

Do not add performance-counter registers, MMIO interfaces, or profiling logic to the DMA datapath.

Use existing RoCC and TileLink protocol signals for passive measurement.

All instrumentation must remain outside the DMA critical path.

### 2. RoCC observability contract

The accelerator must provide unambiguous observable events for:

- CONFIG command acceptance.
- COPY command acceptance.
- Accelerator busy/idle state.
- COPY completion.
- Success/error status.

Completion must occur only after all required memory writes have been acknowledged.

Ensure RoCC ready/valid handshakes and the completion status are reliable so the simulator can measure COPY-to-completion latency without additional hardware instrumentation.

Keep the RoCC software-visible command interface fixed and independent of internal DMA architecture.

### 3. TileLink transaction observability

The simulator must be able to passively observe the accelerator's existing TileLink client interface.

At minimum, support collection of:

- Read request counts.
- Write request counts.
- Read/write traffic volume.
- Read-response latency.
- Write-acknowledgment latency.
- Outstanding transaction counts.
- TileLink backpressure cycles.
- Denied/corrupt responses and protocol failures.

Implement these measurements through the fixed simulation environment, not by inserting profiling logic into the accelerator datapath.

Where necessary, use existing testbench assertions or passive transaction monitors. These must remain unchanged during evolutionary optimization.

### 4. Embench execution-cycle measurements

Use the existing Embench board-support functions `start_trigger()` and `stop_trigger()`.

Measure the elapsed `mcycle` count for each benchmark's execution region.

Use an appropriate 64-bit cycle measurement and ensure the timing boundary is correctly defined for the chosen Rocket implementation.

Report the cycle count after the benchmark timing interval.

Do not modify Embench benchmark source files.

### 5. Memcpy shim statistics

The link-time `--wrap=memcpy` shim must provide the following statistics per benchmark:

- Total intercepted memcpy calls.
- Number of DMA attempts.
- Number of successfully completed DMA copies.
- Number of DMA errors.
- Number of CPU fallback copies.
- Total successfully DMA-copied bytes.
- Total end-to-end DMA execution cycles.
- Minimum and maximum DMA-copy latency.
- Histogram of DMA-offloaded copy sizes.

Use a small fixed in-memory statistics structure.

The collected statistics must be available to the evaluator after benchmark execution, preferably through a single machine-readable summary emitted after the benchmark's measured region.

Do not print per-copy diagnostic messages inside the timed benchmark region.

Report zero-valued statistics explicitly for benchmarks that do not invoke DMA.

Ensure the counters satisfy meaningful consistency checks, including:

`dma_attempts = dma_completed + dma_failed`

`memcpy_calls = dma_attempts + cpu_fallback_calls`

These identities assume each intercepted call takes exactly one of the specified paths. If additional paths exist, expose them explicitly.

### 6. Standalone DMA performance tests

The standalone bare-metal DMA tests must additionally measure:

- End-to-end submission-to-completion cycles.
- Hardware COPY-command-to-response cycles.
- Effective DMA bandwidth in useful bytes per cycle.
- Transfer length.
- Source/destination alignment.
- CPU memcpy baseline cycles where applicable.

These measurements must cover multiple transfer sizes and address alignments, including short copies and longer streaming transfers.

Keep software end-to-end latency separate from hardware execution latency.

### 7. Correctness reporting

Every standalone correctness test and Embench run must produce an unambiguous success/failure result.

Report failures involving:

- Incorrect copied data.
- Memory corruption outside the destination.
- Incorrect source modification.
- Cache-coherence or ordering violations.
- TileLink protocol violations.
- DMA completion or status failures.
- Simulation timeouts.

Any missing, malformed, or incomplete result must be treated as an evaluation failure, not a successful run with zero cycles.

### 8. Machine-readable metric output

Emit a stable, documented result format from the fixed bare-metal/testbench infrastructure.

The output must be parseable from CHIA's existing Verilator simulation results.

Avoid implementing custom logging or profiling frameworks.

The evolutionary CHIA evaluator will calculate speedups, effective bandwidth, fitness, and other derived metrics using these raw measurements.

Do not calculate evolutionary fitness inside the accelerator or bare-metal shim.

### 9. Validation before evolutionary optimization

Before declaring the accelerator implementation complete:

1. Run standalone DMA correctness tests and verify that correct results are reported.
2. Execute at least one Embench benchmark with actual DMA offloading.
3. Verify `mcycle`-based benchmark-cycle measurement.
4. Verify intercepted memcpy, DMA-offload, fallback, and byte counters.
5. Verify COPY command and completion timestamps can be captured from the simulator.
6. Verify TileLink activity is observable during a real DMA transfer.
7. Verify the machine-readable metrics can be extracted from simulator output.
8. Verify that an Embench benchmark without eligible DMA calls reports zero offloads correctly.
9. Verify CPU-only and DMA-enabled executions use comparable measurement instrumentation.
10. Verify the measurement infrastructure does not modify the DMA critical path.

The initial accelerator must provide all required observability before handing the design over to CHIA's evolutionary optimization loop.
