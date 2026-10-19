"""The accel node dispatches, with a _chia_tag on each call.

accel/nodes.py builds the same three dispatches without a tag, and the cache
only writes a result when the call carries one (ChiaFunction._maybe_wrap_cache
returns the ref unchanged when the tag is None). These wrappers are that one
addition; everything else — the node classes, the resources, the arguments —
is theirs.

Tags are named so one regex selects a family:

    sw_<case>     a software compile      (RiscvBuildNode.build)
    hw_<label>    the Chisel build        (ChiselBuildNode.build)
    sim_<case>    one simulation          (VerilatorRunNode.run)

The sw_/hw_ split matters: both nodes' methods are called "build", so today a
yaml entry keyed "build" hits both. The tag regex is what separates them.
"""

from __future__ import annotations

from pathlib import Path

from ray import ObjectRef

from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.state_def import BuildArtifact, BuildTarget, RiscvBuildArtifact, RunResult
from chia.chipyard.verilator_run_node import VerilatorRunNode

from chia.base.llm_call import QueryResult
from chia.base.tools.BashTool import BashTool
from chia.models.antigravity import AntigravityLLM

import sys
from pathlib import Path

# accel/ is not a package and accel_loop.py puts it on the workers' path via
# runtime_env["working_dir"]. Do the same for this process so "import nodes",
# "import results" and "import accel_loop" resolve from here.
ACCEL = Path(__file__).resolve().parents[1] / "accel"
sys.path.insert(0, str(ACCEL))

from nodes import SIM_THREADS

HERE = Path(__file__).resolve().parent

# Call-order sequence numbers. The loop does not hand us an iteration index, so
# the tag records *which call this was*: replaying the same loop makes the same
# calls in the same order and so looks up the same tags. A diverging run simply
# misses the cache and runs for real (the bypass cond allows that).
_seq = {"sw": 0, "hw": 0, "sim": 0, "llm": 0}


def _tag(kind: str, label: str = "") -> str:
    _seq[kind] += 1
    return f"{kind}_{_seq[kind]:04d}" + (f"_{label}" if label else "")


def compile_software(source: str, name: str, cflags: str) -> ObjectRef[RiscvBuildArtifact]:
    node = RiscvBuildNode(timeout_seconds=300)
    return node.build.options(resources={"riscv_build": 1}).chia_remote(
        node, source.encode(), name, "/tmp/accel_sw",
        target="verilator", extra_cflags=cflags, include_dump=True,
        _chia_tag=_tag("sw", name),
    )


def build_hardware() -> ObjectRef[BuildArtifact]:
    node = ChiselBuildNode(
        chipyard_path="/home/ray/chipyard",
        config="FastRTLSimRocketConfig",
        target=BuildTarget.VERILATOR,
        make_jobs=8,
        timeout_seconds=4 * 3600,
        extra_make_args={"VERILATOR_THREADS": SIM_THREADS},
    )
    return node.build.options(resources={"chipyard": 0.9}).chia_remote(
        node, _chia_tag=_tag("hw"))


def run_simulation(artifact: BuildArtifact, elf: RiscvBuildArtifact) -> ObjectRef[RunResult]:
    dramsim_ini = {p.name: p.read_bytes() for p in sorted((ACCEL / "dramsim_ini").iterdir())}
    node = VerilatorRunNode()
    return node.run.options(resources={"verilator_run": SIM_THREADS}).chia_remote(
        node, artifact, elf.binary_content, elf.binary_name, "/home/ray/accel_sim",
        plusargs={"+loadmem": elf.binary_name},
        timeout_cycles=3_000_000,
        timeout_seconds=3600,
        dramsim_ini_files=dramsim_ini,
        verbose=False,
        _chia_tag=_tag("sim", elf.binary_name),
    )


def prompt_llm(llm: AntigravityLLM, prompt: str, chipyard_bash: BashTool) -> ObjectRef[QueryResult]:
    """Their prompt_llm, tagged. Bypass every llm_* or none: a replayed call
    does not advance the model's resumable session."""
    return llm.prompt.options(resources={"antigravity_creds": 1}).chia_remote(
        llm, prompt, [chipyard_bash], _chia_tag=_tag("llm"))
