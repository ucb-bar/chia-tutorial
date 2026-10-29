"""Required CHIA tasks: build, simulate, parse measurements, and score."""

import math
from pathlib import Path
import re
import tempfile

from chia.base.ChiaFunction import ChiaFunction
from chia.chipyard.chisel_build_node import ChiselBuildNode

from .config_loader import digest
from .experiment import materialize_candidate, git_output
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
        materialize_candidate(tree, baseline, patch, git_metadata=False)
        paths = {path for root in roots for path in (tree / root).rglob("*") if path.is_file()}
        if patch:
            changes = git_output(tree, "apply", "--numstat", "-z", input=patch)
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
    materialize_candidate(tree, baseline, patch)
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
def parse(result, expected_checksum):
    """Parse one run and check the checksum expected for its sampled input."""
    text = result.get("log", "") + "\n" + result.get("out", "")
    require(
        result.get("success") and result.get("returncode") == 0,
        "Simulator failed",
    )
    require(
        not re.search(
            r"Assertion failed|%Error|\*\*\* FAILED|DMA_PROTOCOL_FAIL|\bFAIL\b", text
        ),
        "Correctness/protocol failure",
    )
    software = single_record(text, "MEMORY_METRICS")
    try:
        require(
            software["result"] == 0
            and software["verification_result"] == 1
            and software["benchmark_result"] == expected_checksum,
            "Benchmark checksum verification failed",
        )
        require(software["benchmark_cycles"] > 0, "Invalid benchmark cycle count")
        require(software["benchmark_instructions"] > 0, "Invalid retired instruction count")
    except KeyError as error:
        raise ValueError("Missing metric: " + str(error)) from error
    # DMA activity is diagnostic: a valid co-design may use fewer copies or none.
    return {
        **software,
        "rtl_wall_seconds": result.get("wall_seconds", 0.0),
        "cpi": software["benchmark_cycles"] / software["benchmark_instructions"],
    }


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def aggregate(records, reference, workloads, patch):
    """Maximize geometric-mean cycle speedup for all correctly checksummed tests."""
    vector = {}
    log_speedups = []
    total_cycles = total_instructions = 0
    if not workloads or set(reference) != set(records) or set(records) != set(workloads):
        raise ValueError("Full memory-test suite results are required")
    for name in sorted(reference):
        row, seed = records[name], reference[name]
        if (
            row["benchmark_result"] != workloads[name]["checksum"]
            or row["verification_result"] != 1
        ):
            raise ValueError(f"{name}: memory-test checksum verification failed")
        cycles = row["benchmark_cycles"]
        instructions = row["benchmark_instructions"]
        require(cycles > 0 and instructions > 0, "Invalid iron-law counters")
        speedup = seed["benchmark_cycles"] / cycles
        vector[name] = {**row, "speedup": speedup, "cpi": cycles / instructions}
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
        "cpi": total_cycles / total_instructions,
        "patch_hash": digest(patch.encode()),
    }, vector


def metric_records(text, tag):
    result = []
    for line in text.splitlines():
        if not line.startswith(tag + " "):
            continue
        record = {}
        for token in line.split()[1:]:
            require(re.fullmatch(r"\w+=-?\d+", token), "Malformed metric record")
            name, value = token.split("=")
            require(name not in record, "Duplicate metric: " + name)
            record[name] = int(value)
        require(record.get("metrics_version") == 1, "Unsupported metrics version")
        result.append(record)
    return result


def single_record(text, tag):
    found = metric_records(text, tag)
    require(len(found) == 1, "Expected one " + tag + " completion record")
    return found[0]


def require(condition, message):
    if not condition:
        raise ValueError(message)
