We would like to optimize load/store times using a load/store or DMA accelerator that pull from different levels of the memory hierarhcy.

The goal is to produce a first cut implementation of this accelerator. It should be a RoCC accelerator (which is tightly coupled to the core by property of a RoCC accelerator) which will make it be able to see memory traffic from the core.

In the implementation, ensure we maintain coherency and consistency. 

This accelerator will be tested on a suite of benchmarks (ex: CloudSuite 4.0). Do not overfit to any particular test.

You can only modify the accelerator implementation. Do not touch the Core's implementation, or the rest of the SoC system (ex: L2 cache implementations, or bus impelmentations).

You are allowed to make the RoCC accelerator access parts out of the core (ex: opening a separate TileLink port onto the System Bus).

<!-- We can pull directly from the L2 cache via a separate TileLink port.  -->

<!-- 1) whether the RoCC accelerator should perform the load/store directly from the L1$ -->
<!-- 2) whether the RoCC accelerator should pull from the L2 cache via a separate TileLink port -->