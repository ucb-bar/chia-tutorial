"""SkyDiscover LLM interface -> native CHIA CLI agent -> candidate BashTool."""

import asyncio
import json
import os
import time
import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.base.tools.BashTool import BashTool
from skydiscover.llm.base import LLMInterface, LLMResponse
from .experiment import save_proposal, load_proposal, capture_patch, materialize_candidate

CONSTRAINTS = """Co-design the RV64 RoCC DMA hardware and the six baremetal C workloads.

OBJECTIVE
Minimize total cycles for each correctly checksummed test. At the modeled fixed
clock, time = instructions * CPI / frequency, so minimize instructions * CPI.
The score is the equally weighted geometric mean of seed cycles / candidate cycles
across ALL six tests. Retired instructions and CPI explain the result; neither
IPC nor DMA throughput is the objective by itself.

CORRECTNESS
The six workloads are scored with fresh runtime inputs. A separate verification
node also checks additional runtime-varying inputs against an independent
reference. Every output buffer and guard region must remain correct for those
inputs. Hidden cases and expected outputs are not disclosed. The harness times
initialization, benchmark execution, and checksum calculation together.
You may change algorithms, data movement, layout, batching, and DMA usage.
You need not preserve memcpy call counts, bytes transferred, intermediate
states, or DMA use. Preserve the computation for changing inputs; hardcoded
outputs and checksum collisions fail correctness checks. Do not bypass the
measurement harness or runtime input initialization.

EDITING
Use dma_bash_run_command to edit any source in /workspace. You may create,
delete, rename, or replace files, including SoC configuration and build files.
The framework saves a Git patch relative to the seed and rebuilds the candidate.
The external evaluator, expected checksums, and measurement harness are fixed;
changing their workspace copies does not change the evaluation contract.

WORKFLOW
1. Read the selected parent's hardware, C code, and recorded cycle/instruction data.
2. Choose a focused hardware/software change that could reduce total cycles.
3. Edit hardware, C workloads, or both, preserving their callable interfaces.
4. Finish with a short description and the expected instruction-count/CPI tradeoff.

The framework rebuilds BOTH hardware and candidate C tests, runs all six, checks
their checksums, and measures the results. Do not compile or run the evaluator
inside this generation worker. Do not claim an improvement without measurement.
"""


def make_agent(configuration):
    from chia.models.antigravity import AntigravityLLM
    args = dict(
        timeout_seconds=configuration["agent"]["timeout_seconds"],
        retries=configuration["agent"]["retries"],
        resume_session=False,
        work_dir="/workspace",
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
    materialize_candidate("/workspace", "/seed", parent_patch)
    os.chdir("/workspace")
    prompt = CONSTRAINTS + "\nSelected parent and inspirations:\n" + json.dumps(task)
    guidance = configuration["search"]["island_guidance"]
    if guidance:
        prompt += "\nIsland guidance: " + guidance[task["island"] % len(guidance)]
    tool = BashTool(
        "dma_bash",
        work_dir="/workspace",
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
        patch = capture_patch("/workspace", "/seed")
        return save_proposal(sql, task["id"], patch, generation, remote=True)
    finally:
        tool.stop()


class CLIClient(LLMInterface):
    def __init__(self, configuration, experiment):
        self.configuration, self.experiment = configuration, experiment

    async def generate(self, system_message, messages, **kwargs):
        user_message = messages[-1]
        selection = json.loads(user_message["content"])
        candidate_id = selection["request_id"]
        request = self.experiment.selection(candidate_id)
        handle = generate.chia_remote(
            self.configuration,
            self.experiment.sql,
            request,
            _chia_tag="proposal/" + candidate_id,
        )
        try:
            saved = await asyncio.to_thread(get, handle)
        except asyncio.CancelledError:
            chia_cancel(getattr(handle, "ref", handle), force=True)
            raise
        candidate = {"proposal_id": candidate_id, "patch": saved["patch"]}
        return LLMResponse(text="```json\n" + json.dumps(candidate) + "\n```")


class ParentContext:
    def build_prompt(self, current_program, context):
        return {
            "system": CONSTRAINTS,
            "user": json.dumps({"request_id": current_program.metadata["request_id"]}),
        }
