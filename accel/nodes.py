from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from ray import ObjectRef

from chia.base.ChiaFunction import ChiaFunction
from chia.base.llm_call import QueryResult
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.state_def import BuildArtifact, BuildTarget, RiscvBuildArtifact, RunResult
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.models.antigravity import AntigravityLLM

from results import Case

logger = logging.getLogger("accel.nodes")

# ---------------------------------------------------------------------------
# Software compile node
# ---------------------------------------------------------------------------


def compile_software(source: str, case: Case) -> ObjectRef[RiscvBuildArtifact]:
    # TODO: Implement software compilation using RiscvBuildNode
    return None


# ---------------------------------------------------------------------------
# Hardware build node
# ---------------------------------------------------------------------------

# Verilator threads per simulation; each simulation holds this many
# verilator_run tokens.
SIM_THREADS = 1


def build_hardware() -> ObjectRef[BuildArtifact]:
    # TODO: Implement hardware build using ChiselBuildNode
    return None

# ---------------------------------------------------------------------------
# Hardware simulation node
# ---------------------------------------------------------------------------

def run_simulation(artifact: ObjectRef[BuildArtifact], elf: RiscvBuildArtifact) -> ObjectRef[RunResult]:
    dramsim_ini = {p.name: p.read_bytes() for p in sorted((Path(__file__).resolve().parent / "dramsim_ini").iterdir())}
    # TODO: Implmenent simulation using VerilatorRunNode
    return None

# ---------------------------------------------------------------------------
# LLM node
# ---------------------------------------------------------------------------


def prompt_llm(llm: AntigravityLLM, prompt: str, chipyard_bash: BashTool) -> ObjectRef[QueryResult]:
    # TODO: Implement LLM prompt handling using AntigravityLLM
    return None

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

def git_reset() -> ObjectRef[str]:
    return reset_chipyard.chia_remote("/home/ray/chipyard", SUBMODULES)

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

def git_apply(diffs: dict[str, str]) -> ObjectRef[tuple[bool, str]]:
    return apply_diff.chia_remote("/home/ray/chipyard", diffs)

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

def git_diff() -> ObjectRef[dict[str, str]]:
    return collect_diff.chia_remote("/home/ray/chipyard", SUBMODULES)

