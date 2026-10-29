"""Required CHIA tasks: build, simulate, parse measurements, and score."""

import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from chia.base.ChiaFunction import ChiaFunction
from chia.chipyard.chisel_build_node import ChiselBuildNode

from .config_loader import digest
from .experiment import apply_candidate_diff, run_git_command
from .observations import run_observed


def software_inputs(baseline, patch):
    """Apply the candidate patch, then restore the trusted C evaluation harness."""
    baseline = Path(baseline)
    suite, runtime = "tests/memory-tests/", "software/rocc-dma/"
    roots = (suite, runtime)
    original = {
        str(path.relative_to(baseline)): path.read_bytes()
        for root in roots for path in (baseline / root).rglob("*")
        if path.is_file() and "build" not in path.relative_to(baseline / root).parts
    }
    exit_source = "tests/atexit-workaround.c"
    if (baseline / exit_source).exists():
        original[exit_source] = (baseline / exit_source).read_bytes()
    with tempfile.TemporaryDirectory(prefix="dma-software-") as directory:
        tree = Path(directory)
        # Compiler inputs are plain files; this temporary copy needs no Git history.
        shutil.copytree(baseline, tree, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git"))
        subprocess.run(["chmod", "-R", "u+w", str(tree)], check=True)
        if patch:
            run_git_command(tree, "apply", "--whitespace=nowarn", input=patch)
        paths = {path for root in roots for path in (tree / root).rglob("*") if path.is_file()}
        if patch:
            changes = run_git_command(tree, "apply", "--numstat", "-z", input=patch)
            for entry in changes.split("\0"):
                if entry:
                    added, removed, name = entry.split("\t", 2)
                    paths.add(tree / name)
        inputs = {}
        for path in paths:
            if path.is_file() and "build" not in path.relative_to(tree).parts:
                if not path.resolve().is_relative_to(tree):
                    raise ValueError("Source symlink leaves its checkout: " + str(path))
                inputs[str(path.relative_to(tree))] = path.read_bytes()
    # The agent can edit workspace copies, but evaluation uses the preserved harness.
    for name, content in original.items():
        if (name.startswith((runtime, suite + "support/")) or name == exit_source
                or name.endswith("/checksum.c")
                or name in {suite + "Makefile", suite + "workloads.json", suite + "check.py"}):
            inputs[name] = content
    return inputs


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def build(configuration, seed, patch):
    tree = Path("/home/ray/dma-build")
    baseline = Path(seed) / "tree"
    apply_candidate_diff(tree, baseline, patch)
    for name in (".sbt", ".ivy2"):
        if not (tree / name).exists():
            (tree / name).symlink_to(Path("/home/ray/chipyard") / name)
    hw = configuration["hardware"]
    return ChiselBuildNode(
        str(tree),
        hw["config"],
        config_package=hw["config_package"],
        make_jobs=hw["make_jobs"],
        timeout_seconds=hw["build_timeout_seconds"],
        extra_make_args=hw["extra_make_args"],
        clean=hw["clean"],
        bundle_runtime_libs=True,
    ).build()


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def simulate(configuration, artifact, content, name, ini):
    return run_observed(
        artifact,
        content,
        name,
        plusargs={
            **configuration["software"]["plusargs"],
            f"+verilator+seed+{configuration['software']['random_seed']}": "",
            "+loadmem": name + ".riscv",
        },
        dram_settings=ini,
    )


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def parse(result):
    """Read cycle measurements; the separate SHA-256 gate checks output correctness."""
    text = result.get("log", "") + "\n" + result.get("out", "")
    require(result.get("success") and result.get("returncode") == 0, "Simulator failed")
    require(
        not re.search(r"Assertion failed|%Error|\*\*\* FAILED|DMA_PROTOCOL_FAIL", text),
        "Correctness/protocol failure",
    )
    software = single_record(text, "MEMORY_METRICS")
    require(software.get("result", 0) == 0, "Benchmark failed")
    cycles = software.get("benchmark_cycles", 0)
    require(cycles > 0, "Invalid benchmark cycle count")
    instructions = max(0, software.get("benchmark_instructions", 0))
    return {
        **software,
        "benchmark_instructions": instructions,
        "rtl_wall_seconds": result.get("wall_seconds", 0.0),
        "cpi": cycles / instructions if instructions else 0.0,
    }


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def aggregate(records, reference, patch):
    """Score correct results by the equally weighted geometric mean of speedups.

    Each speedup is baseline cycles / candidate cycles. For the six workloads:
        combined_score = (speedup_1 * ... * speedup_6) ** (1 / 6)
    We compute this as exp(mean(log(speedup))). Three tests at 2x speedup and
    three unchanged give (2 * 2 * 2 * 1 * 1 * 1) ** (1 / 6) = ~1.414.

    Above 1 is faster overall, 1 is unchanged, and below 1 is slower. Each test
    contributes equally, regardless of runtime. Instructions, CPI, and patch
    size are reported separately and do not enter this score. Missing baseline
    rows use the current cycles, contributing a neutral 1x speedup.
    """
    vector = {}
    log_speedups = []
    total_cycles = total_instructions = 0
    require(bool(records), "No benchmark results")
    for name in sorted(records):
        row = records[name]
        cycles = row["benchmark_cycles"]
        require(cycles > 0, "Invalid benchmark cycle count")
        instructions = max(0, row.get("benchmark_instructions", 0))
        baseline_cycles = reference.get(name, {}).get("benchmark_cycles", cycles)
        if baseline_cycles <= 0:
            baseline_cycles = cycles
        speedup = baseline_cycles / cycles
        vector[name] = {**row, "speedup": speedup, "cpi": cycles / instructions if instructions else 0.0}
        log_speedups.append(math.log(speedup))
        total_cycles += cycles
        total_instructions += instructions
    score = math.exp(sum(log_speedups) / len(log_speedups))
    if not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid aggregate score")
    return {
        "combined_score": score,
        "validity": 1,
        "source_bytes": len(patch.encode()),
        "cycles": total_cycles,
        "instructions": total_instructions,
        "cpi": total_cycles / total_instructions if total_instructions else 0.0,
        "patch_hash": digest(patch.encode()),
    }, vector


def metric_records(text, tag):
    result = []
    for line in text.splitlines():
        if not line.startswith(tag + " "):
            continue
        record = {
            name: int(value)
            for name, value in re.findall(r"(\w+)=(-?\d+)(?=\s|$)", line)
        }
        result.append(record)
    return result


def single_record(text, tag):
    found = metric_records(text, tag)
    require(bool(found), "Missing " + tag + " completion record")
    return found[-1]


def require(condition, message):
    if not condition:
        raise ValueError(message)
