"""CHIA DAG: build the DMA simulator, run six memory tests, verify, then score."""

import asyncio
from contextlib import closing
from dataclasses import asdict
import json
import math
import re
from pathlib import Path
import subprocess
import threading
import time

import ray
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode
from skydiscover.evaluation.evaluation_result import EvaluationResult

from chia_dma.config import digest, frozen
from chia_dma.experiment import (
    read_candidate_files,
    read_json,
    validate_candidate_files,
    validate_integration,
)


class DMAEvaluator:
    def __init__(self, configuration, experiment, seed):
        self.configuration = configuration
        self.experiment = experiment
        self.seed = str(seed)
        self.tree = Path(seed) / "tree"
        self.stopped = threading.Event()
        self.expected = read_json(self.tree / configuration["software"]["workloads"])
        dram_directory = self.tree / configuration["software"]["dram_ini"]
        self.dram_settings = {
            path.name: path.read_bytes() for path in dram_directory.glob("*.ini")
        }
        self.cache_prefix = digest([Path(seed).name, frozen(configuration)])

    async def evaluate_program(self, solution, program_id=""):
        try:
            return await asyncio.to_thread(self.evaluate, solution, program_id)
        except asyncio.CancelledError:
            self.stopped.set()
            raise

    def evaluate(self, solution, program_id=""):
        """Read the saved proposal, then follow build → simulate → parse → score."""
        candidate = json.loads(solution)
        candidate_id = candidate["proposal_id"]
        recorded_metrics = self.experiment.evaluation_result(candidate_id)
        if recorded_metrics is not None:
            return EvaluationResult(metrics=recorded_metrics)

        proposal = self.experiment.load_proposal(candidate_id)
        source = proposal["source"]
        cache_key = candidate_id if candidate.get("replay") else proposal["source_hash"]
        cache_tag = f"{self.cache_prefix}/{cache_key}"
        log_directory = self.experiment.run / "artifacts" / candidate_id
        log_directory.mkdir(parents=True, exist_ok=True)
        measurements = {}

        try:
            build_task = build.chia_remote(
                self.configuration, self.seed, source, _chia_tag=cache_tag + "/build"
            )
            completed_builds = dict(self.collect({"build": build_task}))
            built = completed_builds["build"]
            (log_directory / "software-build.log").write_text(built["software_log"])
            simulator = built["simulator"]
            if simulator is None:
                raise ValueError("Candidate C compilation failed")
            (log_directory / "build.log").write_text(
                simulator.stdout + "\n" + simulator.stderr
            )
            if not simulator.success:
                raise ValueError("Simulator build failed")

            simulations = self.submit_benchmarks(ray.put(simulator), built["software"], cache_tag)
            with closing(self.collect(simulations)) as completed:
                for workload, result in completed:
                    log_file = log_directory / (workload + ".log")
                    log_file.write_text(result.log + "\n" + result.out)
                    measurements[workload] = parse.chia_remote_blocking(
                        asdict(result), self.expected[workload]["checksum"]
                    )

            if candidate_id == "seed-dma":
                reference = measurements
            else:
                reference = self.experiment.benchmarks("seed-dma")
            metrics, benchmarks = aggregate.chia_remote_blocking(
                measurements,
                reference,
                self.expected,
                self.configuration["fitness"]["workload_weights"],
                source,
            )
        except ValueError as error:
            metrics = {"validity": 0, "combined_score": 0.0}
            benchmarks = {"error": str(error)}

        self.experiment.save_evaluation(
            candidate_id, metrics, {**measurements, **benchmarks}, program_id
        )
        return EvaluationResult(
            metrics=metrics, artifacts={"proposal_id": candidate_id}
        )

    def submit_benchmarks(self, simulator, software, cache_tag):
        simulations = {}
        try:
            for workload in self.expected:
                simulations[workload] = simulate.chia_remote(
                    self.configuration,
                    simulator,
                    software[workload],
                    workload,
                    self.dram_settings,
                    _chia_tag=cache_tag + "/" + workload,
                )
        except BaseException:
            for task in simulations.values():
                chia_cancel(getattr(task, "ref", task), force=True)
            raise
        return simulations

    def collect(self, tasks):
        pending = {}
        for workload, task in tasks.items():
            reference = getattr(task, "ref", task)
            pending[reference] = (workload, task)
        deadline = (
            time.monotonic() + self.configuration["runtime"]["task_timeout_seconds"]
        )
        try:
            while pending:
                if self.stopped.is_set():
                    raise InterruptedError("Evaluation interrupted")
                seconds_left = deadline - time.monotonic()
                if seconds_left <= 0:
                    raise TimeoutError("CHIA task deadline")
                ready, _ = ray.wait(
                    list(pending), num_returns=1, timeout=min(1, seconds_left)
                )
                for reference in ready:
                    workload, task = pending.pop(reference)
                    yield workload, get(task)
        finally:
            for reference in pending:
                chia_cancel(reference, force=True)

    def close(self):
        self.stopped.set()


def apply_source(tree, source, baseline):
    validate_candidate_files(source)
    validate_integration(source, baseline)
    for name, text in source.items():
        path = tree / name
        if path.read_text() != text:
            path.write_text(text)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def build(configuration, seed, source):
    tree = Path("/home/ray/dma-build")
    apply_source(tree, source, read_candidate_files(Path(seed) / "tree"))
    # Keep hardware and C compilation in the same serial build node: another
    # candidate must not overwrite this checkout before its ELFs are captured.
    compiled = subprocess.run(
        configuration["software"]["build_command"], cwd=tree,
        capture_output=True, text=True,
    )
    software_log = compiled.stdout + "\n" + compiled.stderr
    if compiled.returncode:
        return {"simulator": None, "software": {}, "software_log": software_log}
    workloads = read_json(Path(seed) / "tree" / configuration["software"]["workloads"])
    software = {
        name: (tree / configuration["software"]["elf"].format(benchmark=name)).read_bytes()
        for name in workloads
    }
    hw = configuration["hardware"]
    simulator = ChiselBuildNode(
        str(tree),
        hw["config"],
        config_package=hw["config_package"],
        make_jobs=hw["make_jobs"],
        timeout_seconds=hw["build_timeout_seconds"],
        extra_make_args=hw["extra_make_args"],
        clean=hw["clean"],
        bundle_runtime_libs=True,
    ).build()
    return {"simulator": simulator, "software": software, "software_log": software_log}


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def simulate(configuration, artifact, content, name, ini):
    return VerilatorRunNode().run(
        artifact,
        content,
        name + ".riscv",
        "/tmp/dma-sim",
        plusargs={
            **configuration["software"]["plusargs"],
            f"+verilator+seed+{configuration['software']['random_seed']}": "",
            "+loadmem": name + ".riscv",
        },
        timeout_cycles=configuration["software"]["timeout_cycles"],
        timeout_seconds=configuration["software"]["timeout_seconds"],
        dramsim_ini_files=ini,
        verbose=False,
    )


@ChiaFunction(num_cpus=1, max_retries=0)
def parse(result, expected_checksum):
    """Verify one simulation using only its output and the fixed expected checksum."""
    text = result.get("log", "") + "\n" + result.get("out", "")
    require(
        result.get("success") and result.get("returncode") == 0,
        "Simulator failed or timed out",
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
        "cpi": software["benchmark_cycles"] / software["benchmark_instructions"],
        "hardware": metric_records(text, "DMA_HW"),
        "copy_events": metric_records(text, "DMA_COPY"),
    }


@ChiaFunction(num_cpus=1, max_retries=0)
def aggregate(records, reference, workloads, weights, source):
    """Maximize geometric-mean cycle speedup for all correctly checksummed tests."""
    vector = {}
    values = []
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
        if (row["benchmark_result"], row["verification_result"]) != (
            seed["benchmark_result"],
            seed["verification_result"],
        ):
            raise ValueError(f"{name}: deterministic benchmark output changed")
        cycles = row["benchmark_cycles"]
        instructions = row["benchmark_instructions"]
        require(cycles > 0 and instructions > 0, "Invalid iron-law counters")
        speedup = seed["benchmark_cycles"] / cycles
        vector[name] = {**row, "speedup": speedup, "cpi": cycles / instructions}
        values.append((speedup, weights.get(name, 1.0)))
        total_cycles += cycles
        total_instructions += instructions
    weighted_log_speedup = 0
    total_weight = 0
    for speedup, weight in values:
        if weight < 0 or not math.isfinite(weight):
            raise ValueError("Invalid workload weights")
        total_weight += weight
        weighted_log_speedup += weight * math.log(speedup)
    if total_weight <= 0:
        raise ValueError("Invalid workload weights")
    score = math.exp(weighted_log_speedup / total_weight)
    if not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid aggregate score")
    return {
        "combined_score": score,
        "validity": 1,
        "source_bytes": sum(len(s.encode()) for s in source.values()),
        "cycles": total_cycles,
        "instructions": total_instructions,
        "cpi": total_cycles / total_instructions,
        "source_hash": digest(source),
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


def evaluate(program_path):
    raise RuntimeError("Initialize the DMA experiment through loop.py")
