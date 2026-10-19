"""One-shot hardware builder with kernel-enforced, fail-closed write limits.

All submodules remain read-only, INCLUDING ignored build outputs. SBT targets
are redirected to /tmp instead. Never relax this policy to fix a build error.
"""
import ctypes
import json
import os
from pathlib import Path
import pickle
import platform
import re
import shlex
import shutil
import sys


def protect_writes(paths):
    if platform.machine() != "x86_64":
        raise RuntimeError("Write sandbox currently supports Linux x86_64 only")
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)  # landlock_create_ruleset(VERSION)
    if abi < 3:
        raise RuntimeError("Landlock ABI >= 3 is required; refusing an unprotected build")
    # Handle every write/create/remove/rename/truncate right through ABI 4.
    rights = sum(1 << bit for bit in [1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    attr = Ruleset(rights)
    fd = libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "Cannot create Landlock ruleset")
    try:
        for path in paths:
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                allowed = rights if Path(path).is_dir() else ((1 << 1) | (1 << 14))
                rule = Beneath(allowed, path_fd)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0):
                    raise OSError(ctypes.get_errno(), f"Cannot allow writes to {path}")
            finally:
                os.close(path_fd)
        if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, fd, 0):
            raise OSError(ctypes.get_errno(), "Cannot enforce write sandbox")
    finally:
        os.close(fd)


def target_settings(output):
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    names = sorted(set(re.findall(r"^\[info\]\s+\*?\s*([A-Za-z_][A-Za-z0-9_-]*)\s*$", output, re.MULTILINE)))
    if not names:
        raise RuntimeError("Cannot discover SBT projects; refusing shared/unsafe target paths")
    # `set every target := thisProject.value...` evaluates in the root scope,
    # incorrectly sharing one directory. Give EACH explicit project its own path.
    return "set Seq(" + ", ".join(f'LocalProject({json.dumps(name)}) / target := file({json.dumps("/tmp/chia-scale-sbt-projects/" + name)})' for name in names) + ")"


def main(settings, destination):
    # Import first, before restricting this short-lived process and its children.
    from chia.chipyard.chisel_build_node import ChiselBuildNode
    import ray
    # CHIA's local-call profiler looks up a Ray actor. Attach to the EXISTING
    # cluster explicitly, so it cannot auto-start another local Ray cluster.
    ray.init(address=settings["ray_address"], namespace="chia-scale", log_to_driver=False)
    root = Path(settings["chipyard_path"]).resolve()
    import subprocess
    modules = subprocess.run(["git", "-C", str(root), "config", "--file", ".gitmodules", "--get-regexp", "path"], capture_output=True, text=True, check=True)
    forbidden = [(root / line.split(maxsplit=1)[1]).resolve() for line in modules.stdout.splitlines()]
    targets = [Path("/tmp"), Path("/dev/null")]
    for name in ["sims/verilator", "project", "target", ".classpath_cache", ".sbt", ".ivy2", ".bloop", "generators/chipyard/target"]:
        path = root / name
        if any(path.resolve().is_relative_to(module) or module.is_relative_to(path.resolve()) for module in forbidden):
            raise RuntimeError(f"Build write path overlaps a submodule: {path}")
        path.mkdir(parents=True, exist_ok=True)
        targets.append(path)
    for name in [".cache", ".sbt", ".ivy2", ".coursier", ".java"]:
        path = Path.home() / name
        if any(path.resolve().is_relative_to(module) or module.is_relative_to(path.resolve()) for module in forbidden):
            raise RuntimeError(f"Home cache overlaps a submodule: {path}")
        path.mkdir(exist_ok=True)
        targets.append(path)
    if any(module.is_relative_to(Path("/tmp").resolve()) for module in forbidden):
        raise RuntimeError("A submodule lives under /tmp; refusing a writable /tmp policy")
    java = ["java", f"-Xmx{settings['java_heap_gb']}G", "-Xss8M", "-jar", str(root / "scripts/sbt-launch.jar")]
    java += [f"-Dsbt.{key}={root / value}" for key, value in [("ivy.home", ".ivy2"), ("global.base", ".sbt"), ("boot.directory", ".sbt/boot")]]
    java += ["-Dsbt.supershell=false", "-Dsbt.server.forcestart=true", "-Dsbt.color=false"]
    java_tmp = Path("/tmp/chia-scale-java")
    java_tmp.mkdir(exist_ok=True)
    options = re.sub(r"-Djava\.io\.tmpdir=\S+", "", os.environ.get("JAVA_TOOL_OPTIONS", ""))
    os.environ["JAVA_TOOL_OPTIONS"] = options + f" -Djava.io.tmpdir={java_tmp}"
    # DRAMSim's Makefile is in-tree-only. Build a NON-GIT scratch copy instead
    # of creating .o/.a files in the original, read-only third-party submodule.
    dramsim = Path("/tmp/chia-scale-dramsim")
    if not dramsim.exists():
        shutil.copytree(root / "tools/DRAMSim2", dramsim, ignore=shutil.ignore_patterns(".git", "*.o", "*.a", "*.so"))
    cache = "/tmp/chia-scale-classpath-cache"
    protect_writes(targets)
    projects = subprocess.run(java + ["projects"], cwd=root, capture_output=True, text=True, timeout=600)
    if projects.returncode:
        raise RuntimeError("Protected SBT project discovery failed:\n" + projects.stderr + projects.stdout)
    command = shlex.join(java + [target_settings(projects.stdout)])
    node = ChiselBuildNode(chipyard_path=str(root), config=settings["hardware_config"],
                          make_jobs=settings["build_jobs"], timeout_seconds=settings["build_timeout_seconds"],
                          extra_make_args={"VERILATOR_THREADS": settings["simulation_threads"], "SBT": command,
                                           "CLASSPATH_CACHE": cache, "JAVA_TMP_DIR": str(java_tmp), "dramsim_dir": str(dramsim)},
                          bundle_runtime_libs=True, clean=False)
    # Unlike CHIA's warning-only cleanup, a failed clean must not reuse stale RTL.
    clean = subprocess.run(["make", "clean", f"CLASSPATH_CACHE={cache}"], cwd=root / "sims/verilator", capture_output=True, text=True, timeout=600)
    if clean.returncode:
        raise RuntimeError("Protected clean failed; refusing stale hardware artifacts.\n" + clean.stderr + clean.stdout)
    artifact = node.build()
    with open(destination, "wb") as stream:
        pickle.dump(artifact, stream)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]), sys.argv[2])
