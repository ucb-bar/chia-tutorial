You are co-designing a hardware memcpy for a RISC-V SoC: a bare-metal C
function now, then (in your next task) the RoCC accelerator it drives. This is
one conversation; you will keep everything you decide here.

## The system

- SoC: Chipyard `FastRTLSimRocketConfig` (one Rocket core, L1 I/D caches, L2,
  SimDRAM backing memory), simulated in Verilator. Bare metal, no virtual
  memory: pointers are physical addresses.
- Your one tool is `chipyard_bash_run_command`: a shell on the chipyard
  checkout at `/home/ray/chipyard`. Do everything through it (`ls`, `cat`,
  `grep`, editing files). Your built-in file and terminal tools run on a
  different machine that has no chipyard checkout, and terminal commands
  there are blocked. Use it to read code (Rocket's RoCC interface in
  `generators/rocket-chip/src/main/scala/tile/LazyRoCC.scala`, the example
  accelerators, `generators/chipyard/src/main/scala/config/fragments/RoCCFragments.scala`).
  Do NOT build or simulate anything; a later stage of the flow does that.

## Your task: the software half

Write the body of

```c
// Copy len bytes from src to dst. Return 1 for success.
__attribute__((noinline))
int memcpy_rocc(const void *src, void *dst, int len)
```

It must copy exactly `len` bytes (`len >= 0`, any alignment of `src` and
`dst`, regions do not overlap), write nothing outside `dst[0..len)`, leave
`src` unchanged, be finished (all copied bytes visible to the core's ordinary
loads) when it returns, and return 1.

The body drives the accelerator you will build next, using the custom
instructions from `rocc.h` (included; e.g. `ROCC_INSTRUCTION_DSS(X, rd, rs1, rs2, funct)`,
`ROCC_INSTRUCTION_DS`, `ROCC_INSTRUCTION_SS`, `ROCC_INSTRUCTION_S`, with
`X` the custom opcode 0-3). You choose the instruction interface (opcode,
functs, what goes in rs1/rs2/rd) and the split of work between software and
hardware — e.g. software may handle unaligned heads/tails and leave the
aligned bulk to the accelerator. Use opcode `custom0`..`custom3`
consistently; you will implement exactly this interface in hardware.

You may only change the body: no new includes, globals or helper
functions outside it (statement expressions and inline asm inside it are
fine). `string.h`, `stdint.h`, `stddef.h` and `rocc.h` are already included.

The harness the body is pasted into — correctness tests, then evaluations
whose cycles (`rdcycle` around the call) are compared against the software
baseline (newlib `memcpy`) — is:

```c
${HARNESS}
```

${PARENT}

## Answer format

Explain your interface and design briefly, then give the body between these
two marker lines, exactly as written (the flow pastes everything between them
into the function):

```
// ===== memcpy_rocc body: BEGIN =====
    ... your C statements ...
// ===== memcpy_rocc body: END =====
```
