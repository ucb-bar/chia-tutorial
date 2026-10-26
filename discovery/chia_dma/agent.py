"""SkyDiscover LLM interface -> native CHIA CLI agent -> restricted BashTool."""

import asyncio
import json
import os
from pathlib import Path
import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.base.tools.BashTool import BashTool
from skydiscover.llm.base import LLMInterface, LLMResponse
from .experiment import save_proposal, load_proposal, read_accelerator_files

CONSTRAINTS = """You are optimizing an existing, functional bare-metal RV64 RoCC DMA accelerator implemented in Chisel/Scala.

Your objective is to make ONE focused microarchitectural improvement per iteration that could reduce DMA latency, improve throughput, reduce stalls, or improve hardware efficiency.

The evolutionary evaluator will independently compile, test, benchmark, and measure your changes. Your responsibility is only to produce a valid candidate implementation.

HARD CONSTRAINTS:
- Preserve the existing RV64 custom0 instruction ABI, including command encoding, arguments, responses, and completion behavior.
- Preserve all validation, status, error, and unsupported-request semantics.
- Preserve memory ordering, cache coherence, and DMA correctness for all supported transfers.
- Maintain correct TileLink ready/valid handshakes, request/response tracking, backpressure handling, and transaction completion.
- Continue using only the existing coherent tlNode memory interface. Do not introduce additional TileLink ports or memory paths.
- Keep WithRoCCDMA and the disabled io.mem assignments unchanged.
- Do not modify Rocket's datapath, caches, memory hierarchy, interconnect, or SoC topology.
- Do not modify software, benchmarks, tests, instrumentation, counters, evaluator logic, or fitness calculations.
- Do not specialize behavior for particular benchmarks, input patterns, memory addresses, or evaluation conditions.

EDITING RULES:
- Use dma_bash_run_command to inspect and edit files under /workspace.
- ONLY the two existing accelerator Scala files are writable. Do not modify any other files.
- Modify the existing files in place. They are bind-mounted: never delete, rename, move, or replace the mounted files or their inodes.
- Preserve existing public interfaces and integration points.
- Avoid unrelated refactoring, formatting changes, or large rewrites.
- Make exactly ONE small, concrete, logically coherent optimization per iteration.

OPTIMIZATION GUIDANCE:
- Inspect the existing implementation and identify a specific opportunity for improvement before editing.
- Prefer changes with a clear hardware-performance rationale, such as eliminating unnecessary FSM cycles, reducing stalls, improving request scheduling, reducing redundant logic, or improving datapath utilization.
- Consider hardware cost and critical-path impact. More parallelism or buffering is not automatically better.
- Do not introduce speculative operations that could violate correctness, ordering, or coherence.
- Preserve functionality across different transfer sizes, alignments, cache states, and memory response latencies.
- Ensure the change is compatible with the existing Chisel/Scala interfaces and remains synthesizable.
- Do not add artificial shortcuts, hardcoded results, benchmark detection, or other mechanisms intended to manipulate performance measurements.
- If an optimization requires changing a protected interface or file, choose a different optimization.

EXECUTION:
1. Inspect the relevant accelerator source code.
2. Identify one specific microarchitectural bottleneck or inefficiency.
3. Implement one small optimization directly in the permitted files.
4. Review the change for correctness and compatibility.
5. Finish immediately after the edit.

Do NOT compile, elaborate, synthesize, run simulations, execute benchmarks, or invoke the evaluator. The evolutionary framework handles all validation and measurement.

Do not claim a performance improvement without measurement.

FINAL RESPONSE:
Briefly report:
- Which file(s) and logic were changed.
- What single optimization was implemented.
- Why it might improve latency, throughput, or hardware efficiency.
- Any correctness or performance tradeoffs the evaluator should check.

Do not make additional changes after completing the selected optimization.
"""


def make_agent(configuration):
    from chia.models.antigravity import AntigravityLLM
    from chia.models.claude import ClaudeCodeLLM
    from chia.models.codex import CodexLLM

    backend = configuration["agent"]["backend"]
    classes = {
        "antigravity": AntigravityLLM,
        "claude": ClaudeCodeLLM,
        "codex": CodexLLM,
    }
    args = dict(
        timeout_seconds=configuration["agent"]["timeout_seconds"],
        retries=configuration["agent"]["retries"],
        resume_session=False,
        **configuration["agent"]["backends"][backend]["options"]
    )
    if configuration["agent"]["model"]:
        args["model"] = configuration["agent"]["model"]
    if backend in ("antigravity", "codex"):
        args["work_dir"] = "/workspace"
    return classes[backend](**args)


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def generate(configuration, sql, task):
    saved = load_proposal(sql, task["id"], remote=True)
    if saved:
        return saved
    selected_parent = task["parent"]
    parent_solution = json.loads(selected_parent["solution"])
    parent_source = parent_solution["source"]
    for name, text in parent_source.items():
        (Path("/workspace") / name).write_text(text)
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
        }
        source = read_accelerator_files("/workspace")
        return save_proposal(sql, task["id"], source, generation, remote=True)
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
        candidate = {"proposal_id": candidate_id, "source": saved["source"]}
        return LLMResponse(text="```json\n" + json.dumps(candidate) + "\n```")


class ParentContext:
    def build_prompt(self, current_program, context):
        return {
            "system": CONSTRAINTS,
            "user": json.dumps({"request_id": current_program.metadata["request_id"]}),
        }
