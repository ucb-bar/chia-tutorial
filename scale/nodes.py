"""CHIA nodes: every hardware operation is pinned to ONE agent's builder."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import re
import shutil
import signal
import subprocess
import sys
import tempfile

import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction
from chia.base.tools.ChiaTool import ChiaTool
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode

EDIT_ROOT = "generators/chipyard/src/main/scala/"
# Role resources select the right image; CPU requests, not these tags, limit work.
ROLE_TAG = 0.001


def editable(path):
    return (path.startswith(EDIT_ROOT) and path.endswith(".scala")
            and re.fullmatch(r"[A-Za-z0-9_./-]+", path) is not None
            and ".." not in PurePosixPath(path).parts)


def source_path(root, path, write=False):
    base = Path(root).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or (write and not editable(path)):
        raise ValueError("Only Chipyard's own Scala generator sources are editable; all submodules are read-only")
    if write and not target.is_relative_to((base / EDIT_ROOT).resolve()):
        raise ValueError("Editable path escapes generator directory")
    return target


class SourceTool(ChiaTool):
    """Deliberately no unrestricted shell: submodule writes cannot be requested."""
    def __init__(self, root, task_options):
        super().__init__("chipyard_source", task_options=task_options)
        self.root = root
        for method in [self.read_file, self.list_files, self.search, self.write_file]:
            self.mcp.add_tool(method)
        super().__post_init__()

    def read_file(self, path: str, first_line: int = 1, lines: int = 200) -> str:
        """Read source, including submodules. Paths are relative to Chipyard."""
        if first_line < 1 or not 1 <= lines <= 1000:
            raise ValueError("Invalid line range")
        content = source_path(self.root, path).read_text().splitlines()
        return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(content) if first_line <= i + 1 < first_line + lines)

    def list_files(self, path: str = "generators/chipyard/src/main/scala") -> str:
        """List one directory, without following arbitrary shell commands."""
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in source_path(self.root, path).iterdir()))

    def search(self, pattern: str, path: str = "generators/chipyard/src/main/scala") -> str:
        """Search Scala source; literal pattern, no shell expansion."""
        target = source_path(self.root, path)
        files = target.rglob("*.scala") if target.is_dir() else [target]
        rows = []
        for filename in files:
            if not filename.resolve().is_relative_to(Path(self.root).resolve()):
                continue
            for i, line in enumerate(filename.read_text(errors="replace").splitlines(), 1):
                if pattern in line:
                    rows.append(f"{filename.relative_to(self.root)}:{i}:{line}")
                    if len(rows) >= 200:
                        return "\n".join(rows) + "\n[truncated]"
        return "\n".join(rows)

    def write_file(self, path: str, content: str) -> str:
        """Write ONLY generators/chipyard/src/main/scala/*.scala, never a submodule."""
        destination = source_path(self.root, path, write=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        return f"Wrote {path}"


def git(root, *args, check=True, input=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=input, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


@ChiaFunction(num_cpus=0)
def inspect_builder(root):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    submodules = git(root, "submodule", "status", "--recursive")
    return dict(commit=git(root, "rev-parse", "HEAD").strip(), submodules=submodules,
                dirty=git(root, "status", "--porcelain", "--untracked-files=all", "--", EDIT_ROOT),
                preexisting_changes=git(root, "status", "--porcelain", "--untracked-files=all"),
                protected_state=protected_state(root),
                required_sources=all((Path(root) / path).is_file() for path in ["generators/chipyard/src/main/scala/config/RocketConfigs.scala", "generators/rocket-chip/src/main/scala/tile/LazyRoCC.scala"]),
                landlock_abi=libc.syscall(444, 0, 0, 1))


def protected_state(root):
    """Fingerprint pre-existing image changes; never reset someone else's files."""
    exclude = ":(exclude)" + EDIT_ROOT
    data = git(root, "diff", "--binary", "--submodule=diff", "--no-ext-diff", "--", ".", exclude)
    data += git(root, "diff", "--cached", "--binary", "--submodule=diff", "--no-ext-diff", "--", ".", exclude)
    for path in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if not editable(path):
            data += path + ":" + hashlib.sha256(source_path(root, path).read_bytes()).hexdigest()
    return hashlib.sha256(data.encode()).hexdigest()


def changed_files(root):
    if git(root, "diff", "--cached", "--name-only", "--", EDIT_ROOT):
        raise RuntimeError("Staged changes are not allowed in a loop workspace")
    tracked = git(root, "diff", "--name-only", "--no-renames", "--ignore-submodules=all", "--", EDIT_ROOT).splitlines()
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", EDIT_ROOT).splitlines()
    for path in tracked + untracked:
        if not editable(path):
            raise RuntimeError(f"Unexpected edit outside the allowed Scala directory: {path}")
        source_path(root, path, write=True)
    return tracked, untracked


@ChiaFunction(num_cpus=0, resources={"chipyard": 0.02})
def workspace(root, action, expected, patch=""):
    if git(root, "rev-parse", "HEAD").strip() != expected["commit"]:
        raise RuntimeError("Builder HEAD changed; refusing to change checked-out revisions")
    if git(root, "submodule", "status", "--recursive") != expected["submodules"]:
        raise RuntimeError("Submodule revisions/state changed; refusing to reset them")
    if protected_state(root) != expected["protected_state"]:
        raise RuntimeError("Protected/pre-existing files changed; refusing to reset or overwrite them")
    tracked, untracked = changed_files(root)
    if action == "capture":
        diff = git(root, "diff", "--binary", "--no-ext-diff", "--no-renames", "--ignore-submodules=all", "--", EDIT_ROOT)
        for path in untracked:
            diff += git(root, "diff", "--no-index", "--binary", "--", "/dev/null", path, check=False)
        return diff
    if action == "reset":
        if tracked:
            git(root, "restore", "--worktree", "--source=HEAD", "--", *tracked)
        for path in untracked:
            source_path(root, path, write=True).unlink()  # Individual agent-created Scala files only.
        return "Reset only agent-edited Chipyard Scala sources; no submodule reset"
    if action == "apply":
        if tracked or untracked:
            raise RuntimeError("Parent patch requires a clean agent workspace")
        validate_patch(patch)
        if patch:
            git(root, "apply", "--check", "--whitespace=nowarn", "-", input=patch)
            git(root, "apply", "--whitespace=nowarn", "-", input=patch)
        return "Applied parent patch"
    raise ValueError(action)


def validate_patch(patch):
    if patch and not patch.startswith("diff --git "):
        raise ValueError("Invalid patch")
    headers = re.findall(r"^diff --git a/(\S+) b/(\S+)$", patch, re.MULTILINE)
    if patch and len(headers) != len(re.findall(r"^diff --git ", patch, re.MULTILINE)):
        raise ValueError("Unsupported patch paths")
    for left, right in headers:
        if left != right or not editable(left):
            raise ValueError("Patch would change a submodule or another forbidden path")


@ray.remote(num_cpus=0, resources={"chipyard": 0.01})
class BuilderLease:
    def __init__(self):
        self.lock = open("/tmp/chia-scale-builder.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def ready(self):
        return ray.get_runtime_context().get_node_id()


@ChiaFunction(num_cpus=0, resources={"chipyard": 0.9})
def hardware(settings):
    # The sandbox runs in a NEW process, not in a reusable Ray worker.
    with tempfile.TemporaryDirectory(prefix="scale-build-") as directory:
        destination = Path(directory) / "artifact.pkl"
        # Ray ships working_dir, not the head's absolute __file__ path.
        script = Path.cwd() / "build_worker.py"
        if not script.is_file():
            raise RuntimeError("build_worker.py is missing from Ray's packaged working_dir")
        with subprocess.Popen([sys.executable, str(script), json.dumps(settings), str(destination)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=settings["build_timeout_seconds"] + 1500)
            finally:
                # Kill lingering make/JVM/compiler descendants, including on timeout,
                # BEFORE a debugger can edit this builder's next candidate.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if process.returncode:
                raise RuntimeError("Protected hardware build failed; submodule writes remain forbidden.\n" + stderr[-20000:] + stdout[-10000:])
        with destination.open("rb") as stream:
            return pickle.load(stream)  # Our just-created child-process artifact, never user input.


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
    return hardware.options(num_cpus=jobs, scheduling_strategy=pinned(builder["NodeID"]), max_retries=0).chia_remote(
        dict(settings, build_jobs=jobs))


@ChiaFunction(num_cpus=0)
def machine_metrics():
    import psutil
    times = psutil.cpu_times()
    total = sum(times[:8])  # Exclude guest/guest_nice already counted in user/nice.
    busy = total - times.idle - getattr(times, "iowait", 0)
    disk = shutil.disk_usage("/")
    return dict(cpu_total=total, cpu_busy=busy, disk_used_bytes=disk.used,
                disk_total_bytes=disk.total, memory_used_bytes=psutil.virtual_memory().used)


@ChiaFunction(num_cpus=0)
def environment_info(role):
    import platform
    from importlib.metadata import version
    result = dict(chia=version("chialoops"), ray=ray.__version__, python=platform.python_version())
    if role == "compiler":
        result["compiler"] = subprocess.run(["riscv64-unknown-elf-gcc", "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        result["harness"] = Path("/opt/riscv-harness/Makefile").is_file()
    if role == "model":
        directory = Path.home() / ".gemini"
        result["auth_files_present"] = any(p.is_file() and any(s in p.name.lower() for s in ("token", "oauth", "credential", "auth")) for p in directory.rglob("*"))
        result["agy"] = shutil.which("agy") is not None
    return result


@ChiaFunction(num_cpus=0, resources={"antigravity_creds": 0.01})
def prepare_model_workspace(directory):
    # CHIA passes work_dir to subprocess.run but does not create it.
    path = Path(directory).resolve()
    if not path.is_relative_to(Path("/tmp/scale_agy")) or path == Path("/tmp/scale_agy"):
        raise ValueError("Model workspace must be a run-specific /tmp/scale_agy directory")
    path.mkdir(parents=True, exist_ok=True)
    return ray.get_runtime_context().get_node_id()
