"""The flow's worker-side steps.

  * chipyard git ops (run on the chipyard container): ``reset_chipyard``,
    ``apply_diff``, ``collect_diff``.
  * ``compile_software``: cross-compile the harness on the riscv-cross
    container (chia's RiscvBuildNode).
  * ``build_hardware``: build the Verilator simulator for FastRTLSimRocketConfig
    (chia's ChiselBuildNode).
  * ``run_simulation``: run the harness ELF on it (chia's VerilatorRunNode).
  * ``prompt_llm``: one prompt on the iteration's AntigravityLLM, with
    chipyard_bash as its tool.

These wrappers dispatch and block, returning chia's result dataclasses; none
of them raise on a failed build, run or LLM call.

Resources match cluster.yaml. The chipyard container has one ``chipyard``
token, and chipyard_bash holds 0.01 of it for the whole job, so the build
takes 0.9 and the git ops 0.05.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.llm_call import QueryResult
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.state_def import BuildArtifact, BuildTarget, RiscvBuildArtifact, RunResult
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.models.antigravity import AntigravityLLM

logger = logging.getLogger("accel.nodes")

# ---------------------------------------------------------------------------
# Software compile node
# ---------------------------------------------------------------------------


def compile_software(source: str) -> RiscvBuildArtifact:
    """Cross-compile the harness into memcpy_rocc.riscv (+ memcpy_rocc.dump)."""
    node = RiscvBuildNode(timeout_seconds=300)
    compile_software_result = get(
        node.build.options(resources={"riscv_build": 1}).chia_remote(
            node, source.encode(), "memcpy_rocc", "/tmp/accel_sw",
            target="verilator", include_dump=True,
        )
    )
    return compile_software_result

# ---------------------------------------------------------------------------
# Hardware build node
# ---------------------------------------------------------------------------

def build_hardware() -> BuildArtifact:
    """Build FastRTLSimRocketConfig's Verilator simulator (4 threads) from the
    current chipyard tree."""
    node = ChiselBuildNode(
        chipyard_path="/home/ray/chipyard",
        config="FastRTLSimRocketConfig",
        target=BuildTarget.VERILATOR,
        make_jobs=8,
        timeout_seconds=4 * 3600,
        extra_make_args={"VERILATOR_THREADS": 4},
    )
    build_hardware_result = get(node.build.options(resources={"chipyard": 0.9}).chia_remote(node))
    return build_hardware_result

# ---------------------------------------------------------------------------
# Hardware simulation node
# ---------------------------------------------------------------------------

def run_simulation(artifact: BuildArtifact, elf: RiscvBuildArtifact) -> RunResult:
    """Run the harness ELF on the simulator, with all 4 verilator_run tokens
    (one per simulator thread).

    The DRAMSim2 config is for the SimDRAM backing memory. No +verbose: the
    per-instruction trace of a 16 KiB copy would dwarf the program output. A
    hung accelerator stops at 30M cycles (the baseline needs a few million).
    """
    dramsim_ini = {p.name: p.read_bytes() for p in sorted((Path(__file__).resolve().parent / "dramsim_ini").iterdir())}
    node = VerilatorRunNode()
    simulation_result = get(
        node.run.options(resources={"verilator_run": 4}).chia_remote(
            node, artifact, elf.binary_content, elf.binary_name, "/home/ray/accel_sim",
            plusargs={"+loadmem": elf.binary_name},
            timeout_cycles=30_000_000,
            timeout_seconds=3600,
            dramsim_ini_files=dramsim_ini,
            verbose=False,
        )
    )
    return simulation_result


# ---------------------------------------------------------------------------
# LLM node
# ---------------------------------------------------------------------------


def prompt_llm(llm: AntigravityLLM, prompt: str, chipyard_bash: BashTool) -> QueryResult:
    """Send *prompt* on *llm*'s conversation, on an antigravity_creds worker.
    A call that raises (rate limit, auth, a lost worker) comes back as a
    failed QueryResult with the exception in ``stderr``."""
    llm_result = get(
        llm.prompt.options(resources={"antigravity_creds": 1})
        .chia_remote(llm, prompt, [chipyard_bash])
    )
    # agy reports a run that ended without an answer (e.g. after auto-denying a
    # tool in headless mode) as a success; count it as a failed call.
    if llm_result.success and not (llm_result.result or "").strip():
        llm_result.success = False
        llm_result.stderr = f"[accel] empty answer\n{llm_result.stderr or ''}"
    return llm_result

# ---------------------------------------------------------------------------
# Chipyard git nodes
# ---------------------------------------------------------------------------

SUBMODULES = ["generators/rocket-chip", "generators/rocket-chip-blocks"]

def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


@ChiaFunction(resources={"chipyard": 0.05})
def reset_chipyard(chipyard_path: str, submodules: list[str]) -> str:
    """Revert tracked edits and delete untracked files chipyard root"""
    out = []
    for repo in ["", *submodules]:
        path = os.path.join(chipyard_path, repo)
        for args in (("checkout", "--", "."), ("clean", "-fd")):
            r = _git(path, *args)
            if r.returncode != 0:
                out.append(f"[{repo or 'root'}] git {' '.join(args)}: {r.stderr.strip()}")
    return "\n".join(out) or "clean"

def git_reset() -> str:
    return get(reset_chipyard.chia_remote("/home/ray/chipyard", SUBMODULES))

@ChiaFunction(resources={"chipyard": 0.05})
def apply_diff(chipyard_path: str, diffs: dict[str, str]) -> tuple[bool, str]:
    """git apply each repo's diff"""
    for repo, text in diffs.items():
        if not text.strip():
            continue
        path = os.path.join(chipyard_path, repo)
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as f:
            f.write(text if text.endswith("\n") else text + "\n")
        r = _git(path, "apply", "--whitespace=nowarn", f.name)
        os.unlink(f.name)
        if r.returncode != 0:
            return False, f"[{repo or 'root'}] git apply failed: {r.stderr.strip()}"
    return True, "applied"

def git_apply(diffs: dict[str, str]) -> tuple[bool, str]:
    return get(apply_diff.chia_remote("/home/ray/chipyard", diffs))

@ChiaFunction(resources={"chipyard": 0.05})
def collect_diff(chipyard_path: str, submodules: list[str]) -> dict[str, str]:
    """collects tracked edits plus new files, per repo ("" = chipyard root)"""
    diffs = {}
    for repo in ["", *submodules]:
        path = os.path.join(chipyard_path, repo)
        text = _git(path, "diff", "--ignore-submodules=all").stdout
        for f in _git(path, "ls-files", "--others", "--exclude-standard").stdout.split():
            text += _git(path, "diff", "--no-index", "--", "/dev/null", f).stdout
        diffs[repo] = text
    return diffs

def git_diff() -> dict[str, str]:
    return get(collect_diff.chia_remote("/home/ray/chipyard", SUBMODULES))

