Implement a CHIA discovery loop that uses the antigravity (agy) backend and Gemini 4 OR a claude backend OR a codex backend (implement all 3 as options such that the agent can be hotswapped) to optimize the DMA memcpy accelerator in an evolutionary loop.

Do not reinvent the wheel or reimplement any existing chia functions if they already exist. Use the defined APIs for existing functions. Do not define unencessary new functions.

Documentaton is here: https://docs.chialoops.ai/en/main/index.html which outlines what functions are avilable

Particularly surrodungin profiling and logging: do not create new profilers or loggers, attach custom metadata as needed to profiling for the metrics we need.

On evolutionary methodology:
* we want to track different islands (which represent different optimzation areas)
* with in those islands, we want to preserve the best impelmentation within each island (MAP elites)
* Fitness-biased parent selection, pulling from MAP elites

^ something like this exists in openevolve, but do not exist here because we are justing a pure antigravity (agy) backend. 

Evolutioanry modification requirements:

* only touch accelerator RTL
* do not touch Core (Rocket) impelmentation, outer SoC implementation
* Do not change Embench-IoT soruce code or compilation flow
* You can have tilelink ports in the accelerator that accesses system bus, or taps L1$ traffic.


Use Chia's database API caching and bypassing functionality in order to allow us to checkpoint and then restore an arbitary proposed design (this means, checkpointing the proposed design, then stopping the chia cluster, maybe the machine even shuts down, then being able to chia up the cluster again, then restore from the latest result)
* this means likely presisting the last LLM call & results, ie set the restore point before the LLM call, then checkpoint the LLM call result (ie new proposed design). Then using bypass to restore the LLM proposal, which then gets sent thru the eval loop.
* allow this checkpoint/restore functionality for every iteration, such that we can pick a checkpoint to restore from after the fact (as the loop runs forward, we can cherry pick out a previous checkpoint, then duplicate the current chia cluster setup and restore there)

For evaluation:
* use Embench-IoT -- all benchmarks, all benchmarks should be evaluated in parallel (ie 20 embench binaries should run in parallel in 20 different verilator instances) for any proposed design.

Metrics:
* embench per benchmark measurement
Metric|	Description|	Fitness use
Embench total cycles	mcycle delta for each benchmark's measured execution	Primary
Embench speedup	Baseline cycles / candidate cycles	Primary
DMA copy cycles	End-to-end latency of a DMA copy, including submission and completion	Primary
DMA bandwidth	Useful copied bytes / DMA cycles	Primary for microbenchmarks
Correctness	Benchmark results, memory contents, protocol checks	Hard constraint


Metrics should be collected passively in testbench, without changing embench or placing counters on the dma critical path. Metrics should be kept in a database as well (separate from that of the actual checkpoints, but linked to a particular checkpoint/design)