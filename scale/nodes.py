"""CHIA nodes: every hardware operation is pinned to ONE agent's builder."""
from pathlib import Path
import subprocess

import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction
from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode

EDIT_ROOT = "generators/chipyard/src/main/scala/"
# Role resources select the right image; CPU requests, not these tags, limit work.
ROLE_TAG = 0.001


def git(root, *args, check=True, input=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=input, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


@ChiaFunction(num_cpus=0)
def inspect_builder(root):
    submodules = git(root, "submodule", "status", "--recursive")
    return dict(commit=git(root, "rev-parse", "HEAD").strip(), submodules=submodules,
                dirty=git(root, "status", "--porcelain", "--untracked-files=all", "--", EDIT_ROOT))


@ChiaFunction(num_cpus=0, resources={"chipyard": 0.02})
def workspace(root, action, patch=""):
    """Save/reset only the agent's Scala sources, not the whole checkout."""
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", EDIT_ROOT).splitlines()
    if action == "capture":
        diff = git(root, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-renames", "--ignore-submodules=all", "--", EDIT_ROOT)
        for path in untracked:
            diff += git(root, "diff", "--no-index", "--binary", "--", "/dev/null", path, check=False)
        return diff
    if action == "reset":
        git(root, "restore", "--staged", "--worktree", "--source=HEAD", "--", EDIT_ROOT)
        for path in untracked:
            (Path(root) / path).unlink()
        return "Reset agent Scala sources"
    if action == "apply":
        if patch:
            git(root, "apply", "--whitespace=nowarn", "-", input=patch)
        return "Applied parent patch"
    raise ValueError(action)


def compile_software(source, name, settings):
    node = RiscvBuildNode(timeout_seconds=settings["compile_timeout_seconds"])
    return node.build.options(num_cpus=1, resources={"riscv_build": ROLE_TAG}, max_retries=0).chia_remote(
        node, source.encode(), name, "/tmp/scale_sw", target="verilator", include_dump=True,
        extra_cflags="-fno-builtin-memcpy")


def simulate(artifact, elf, settings):
    node = VerilatorRunNode()
    ini = {p.name: p.read_bytes() for p in (Path(__file__).parent / "dramsim_ini").iterdir()}
    return node.run.options(num_cpus=settings["simulation_threads"], resources={"verilator_run": ROLE_TAG}, max_retries=0).chia_remote(
        node, artifact, elf.binary_content, elf.binary_name, "/tmp/scale_sim",
        plusargs={"+loadmem": elf.binary_name}, timeout_cycles=settings["simulation_timeout_cycles"],
        timeout_seconds=settings["simulation_timeout_seconds"], dramsim_ini_files=ini, verbose=False)


def pinned(node_id):
    return NodeAffinitySchedulingStrategy(node_id, soft=False)


def build_hardware(settings, builder):
    jobs = int(builder["Resources"]["CPU"])
    node = ChiselBuildNode(chipyard_path=settings["chipyard_path"], config=settings["hardware_config"],
                          make_jobs=jobs, timeout_seconds=settings["build_timeout_seconds"],
                          extra_make_args={"VERILATOR_THREADS": settings["simulation_threads"]},
                          bundle_runtime_libs=True, clean=True)
    return node.build.options(num_cpus=jobs, resources={"chipyard": 0.9},
                              scheduling_strategy=pinned(builder["NodeID"]), max_retries=0).chia_remote(node)


@ChiaFunction(num_cpus=0, resources={"antigravity_creds": 0.01})
def prepare_model_workspace(directory):
    # CHIA passes work_dir to subprocess.run but does not create it.
    Path(directory).mkdir(parents=True, exist_ok=True)
    return ray.get_runtime_context().get_node_id()
