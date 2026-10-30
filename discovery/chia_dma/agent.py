"""Native CHIA CLI agent -> candidate BashTool. Runs on generation workers."""

import json
import os
from pathlib import Path
import time
import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction
from chia.base.tools.BashTool import BashTool
from .source import (
    save_proposal, load_proposal, capture_patch, apply_candidate_diff, initialize_checkout,
)

CONSTRAINTS = """Co-design the RV64 RoCC DMA hardware and the six baremetal C workloads.

OBJECTIVE
Minimize total cycles for each correct test. At the modeled fixed
clock, time = instructions * CPI / frequency, so minimize instructions * CPI.
The score is the equally weighted geometric mean of seed cycles / candidate cycles
across ALL six tests. Retired instructions and CPI explain the result; neither
IPC nor DMA throughput is the objective by itself.

CO-DESIGN
This is a hardware/software co-design exercise. Prefer proposals that change
the DMA hardware (RoCCDMA.scala, DMAController.scala, DMAConfigs.scala) together
with the C workloads that use it, so the software exploits what the new
hardware provides. Replacing DMA copies with CPU load/store loops is a known
software-only baseline, not the goal, it is highly discouraged; aim for a DMA that beats it. 
When a software-only change is still the best step, explain why hardware would not help.

CORRECTNESS
The six workloads are scored with fresh runtime inputs. A separate verification
node also checks additional runtime-varying inputs against an independent
reference. Every output buffer and guard region must remain correct for those
inputs. Hidden cases and expected outputs are not disclosed. Every memory
region the tests write, including guard regions around each output, is verified.
You may change algorithms, data movement, layout, batching, and DMA usage.
You need not preserve memcpy call counts, bytes transferred, intermediate
states, or DMA use. Preserve the computation for changing inputs; hardcoded
outputs fail verification. Do not bypass the
measurement harness or runtime input initialization.

EDITING
Use dma_bash_run_command to edit any source in /home/ray/workspace. You may create,
delete, rename, or replace files, including SoC configuration and build files.
The framework saves a Git patch relative to the seed and rebuilds the candidate.
The external evaluator, verification, and measurement harness are fixed;
changing their workspace copies does not change the evaluation contract.

CODE MAP (paths relative to /home/ray/workspace; about 530 lines in total)
Read these first. They are the whole design under optimization:
- generators/chipyard/src/main/scala/dma/RoCCDMA.scala: RoCC accelerator, command decode.
- generators/chipyard/src/main/scala/dma/DMAController.scala: copy engine and memory traffic.
- generators/chipyard/src/main/scala/config/DMAConfigs.scala: DMARocketConfig, the SoC built.
- tests/memory-tests/src/*/copy-*.c: the six workloads (small, stream, gather, scatter,
  chain, mixed).
Fixed driver, useful as reference only (the evaluator restores it):
- software/rocc-dma/: rocc_dma.h/.c driver API, memcpy_wrap.c (memcpy -> DMA above
  DMA_THRESHOLD).
The rest of tests/memory-tests/ and software/rocc-dma/ is fixed measurement and
verification harness; you do not need to read it.
The rest of Chipyard (rocket-chip, other generators, sims/) is unmodified upstream
infrastructure. Consult it when a design decision depends on it, such as an
interface or parameter your change touches, rather than surveying it broadly.

WORKFLOW
1. Read the selected parent's hardware, C code, and recorded cycle/instruction data.
2. Choose a focused change to the DMA hardware and the C code that drives it
   that could reduce total cycles.
3. Edit the hardware and the C workloads together, preserving callable interfaces.
4. Finish with a short description and the expected instruction-count/CPI tradeoff.

The framework rebuilds BOTH hardware and candidate C tests, runs all six, verifies
their outputs, and measures the results. Do not compile or run the evaluator
inside this generation worker. Do not claim an improvement without measurement.
"""


def make_agent(configuration):
    from chia.models.antigravity import AntigravityLLM
    args = dict(
        timeout_seconds=configuration["agent"]["timeout_seconds"],
        retries=configuration["agent"]["retries"],
        resume_session=False,
        work_dir="/home/ray/workspace",
        extra_cli_args=["--effort", configuration["agent"]["effort"]],
    )
    if configuration["agent"]["model"]:
        args["model"] = configuration["agent"]["model"]
    return AntigravityLLM(**args)


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def generate(configuration, sql, task):
    started = time.monotonic()
    saved = load_proposal(sql, task["id"], remote=True)
    if saved:
        return saved
    selected_parent = task["parent"]
    parent_solution = json.loads(selected_parent["solution"])
    parent_patch = parent_solution["patch"]
    baseline = Path(configuration["seed"]) / "tree"
    initialize_checkout("/home/ray/workspace", baseline)
    apply_candidate_diff("/home/ray/workspace", baseline, parent_patch)
    os.chdir("/home/ray/workspace")
    prompt = CONSTRAINTS + "\nSelected parent and inspirations:\n" + json.dumps(task)
    guidance = configuration["search"]["island_guidance"]
    if guidance:
        prompt += "\nIsland guidance: " + guidance[task["island"] % len(guidance)]
    tool = BashTool(
        "dma_bash",
        work_dir="/home/ray/workspace",
        timeout_seconds=120,
        task_options={
            "scheduling_strategy": NodeAffinitySchedulingStrategy(
                ray.get_runtime_context().get_node_id(), soft=False
            )
        },
    )
    try:
        agent = make_agent(configuration)
        result = agent.prompt(prompt, [tool])
        if not result.success:
            raise RuntimeError(
                "Agent failed: "
                + (result.stderr or result.result or result.stream_result)[-4000:]
            )
        generation = {
            "backend": configuration["agent"]["backend"],
            "model": configuration["agent"]["model"] or getattr(agent, "model", None),
            "response": result.result,
            "transcript": result.stream_result,
            "wall_seconds": time.monotonic() - started,
        }
        patch = capture_patch("/home/ray/workspace", baseline)
        return save_proposal(sql, task["id"], patch, generation, remote=True)
    finally:
        tool.stop()
