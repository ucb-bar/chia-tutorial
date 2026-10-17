## Your task: the hardware half

Now implement the RoCC accelerator your `memcpy_rocc` body drives, in Chisel,
in the chipyard checkout at `/home/ray/chipyard`, using the `chipyard_bash_run_command` tool
(your only tool: your built-in file and terminal tools cannot reach the checkout).

${TREE_STATE}

Requirements:

- It implements exactly the custom-instruction interface your software uses
  (opcode, functs, operands, which instructions write `rd` and when), and it
  copies through memory the core can see (e.g. the RoCC `io.mem` port into
  the L1 data cache, or a TileLink master via `atlNode`/`tlNode`). When the
  instruction your software waits on responds, every copied byte must be
  visible to the core's loads.
- Attach it to `FastRTLSimRocketConfig` itself (in `/home/ray/chipyard/generators/chipyard/src/main/scala/config/RocketConfigs.scala`), e.g.
  with a config fragment like `WithAccumulatorRoCC` in
  `RoCCFragments.scala`. The flow builds `FastRTLSimRocketConfig` and nothing else.
- Put new Scala sources under `generators/chipyard/src/main/scala/`.
- Do NOT build, run sbt, or simulate: the flow next cross-compiles the
  software, builds the Verilator simulator (`make CONFIG=FastRTLSimRocketConfig` in
  `sims/verilator`) and runs the harness, and sends you any failure.

If writing the hardware changed your mind about the interface, end your answer
with a revised body between the markers (same format as before); otherwise
leave the markers out and your previous body is kept.
