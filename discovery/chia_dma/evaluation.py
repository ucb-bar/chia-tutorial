"""Build the candidate, run Embench, verify the results, and score it."""

import asyncio
from contextlib import closing
from dataclasses import asdict
import importlib.util
import json
import math
from pathlib import Path
import shutil
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
    read_accelerator_files,
    read_json,
    validate_accelerator_files,
    validate_integration,
    write_json,
)


class DMAEvaluator:
    def __init__(self, configuration, experiment, seed):
        self.configuration = configuration
        self.experiment = experiment
        self.seed = str(seed)
        self.tree = Path(seed) / "tree"
        self.stopped = threading.Event()
        software_manifest = read_json(experiment.run / "software/manifest.json")
        self.workloads = software_manifest["workloads"]
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
            simulator = completed_builds["build"]
            (log_directory / "build.log").write_text(
                simulator.stdout + "\n" + simulator.stderr
            )
            if not simulator.success:
                raise ValueError("Simulator build failed")

            simulations = self.submit_benchmarks(ray.put(simulator), cache_tag)
            with closing(self.collect(simulations)) as completed:
                for workload, result in completed:
                    log_file = log_directory / (workload + ".log")
                    log_file.write_text(result.log + "\n" + result.out)
                    measurements[workload] = parse(
                        asdict(result), self.configuration, self.tree
                    )

            if candidate_id == "seed-dma":
                reference = measurements
            else:
                reference = self.experiment.benchmarks("seed-dma")
            metrics, benchmarks = aggregate(
                measurements, reference, self.configuration, source
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

    def submit_benchmarks(self, simulator, cache_tag):
        simulations = {}
        try:
            for workload, specification in self.workloads.items():
                elf_file = self.experiment.run / "software" / specification["file"]
                simulations[workload] = simulate.chia_remote(
                    self.configuration,
                    simulator,
                    elf_file.read_bytes(),
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
    validate_accelerator_files(source)
    validate_integration(source, baseline)
    for name, text in source.items():
        (tree / name).write_text(text)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def build_benchmarks(configuration, run, seed):
    tree = Path("/home/ray/dma-build")
    baseline = read_accelerator_files(Path(seed) / "tree")
    apply_source(tree, baseline, baseline)
    with open(Path(run) / "software-build.log", "w") as log:
        subprocess.run(
            configuration["software"]["build_command"],
            cwd=tree,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    target = Path(run) / "software"
    target.mkdir(exist_ok=True)
    benchmarks = sorted(
        p.name
        for p in (tree / configuration["software"]["benchmark_inventory"]).iterdir()
        if p.is_dir()
    )
    workloads = {}
    for benchmark in benchmarks:
        elf_path = configuration["software"]["embench_elf"].format(benchmark=benchmark)
        target_elf = target / (benchmark + ".elf")
        shutil.copyfile(tree / elf_path, target_elf)
        workloads[benchmark] = {
            "file": target_elf.name,
            "sha256": digest(target_elf.read_bytes()),
        }
    manifest = {"workloads": workloads}
    write_json(target / "manifest.json", manifest)
    return manifest


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def build(configuration, seed, source):
    tree = Path("/home/ray/dma-build")
    apply_source(tree, source, read_accelerator_files(Path(seed) / "tree"))
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


def parse(result, configuration, seed):
    """Delegate validation to the existing parser from the immutable seed."""
    root = Path(seed)
    path = root / configuration["software"]["metrics_parser"]
    spec = importlib.util.spec_from_file_location("frozen_dma_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.parse(
        result.get("log", "") + "\n" + result.get("out", ""),
        success=result.get("success", False),
        returncode=result.get("returncode"),
    )
    return {
        **data["software"],
        "hardware": data["hardware"],
        "copy_events": data["copies"],
    }


def aggregate(records, reference, configuration, source):
    vector = {}
    values = []
    timed_bytes = timed_cycles = 0
    weights = configuration["fitness"]["workload_weights"]
    if not reference or set(reference) != set(records):
        raise ValueError("Full Embench suite results are required")
    for name in sorted(reference):
        row, seed = records[name], reference[name]
        if (row["benchmark_result"], row["verification_result"]) != (
            seed["benchmark_result"],
            seed["verification_result"],
        ):
            raise ValueError(f"{name}: deterministic benchmark output changed")
        activity = (
            "intercepted",
            "offloaded",
            "fallback",
            "bytes",
            "region_offloaded",
            "region_bytes",
        )
        if any(row[k] != seed[k] for k in activity):
            raise ValueError(
                f"{name}: DMA activity changed; investigate before admission"
            )
        speedup = seed["benchmark_cycles"] / row["benchmark_cycles"]
        vector[name] = {
            **row,
            "speedup": speedup,
        }
        if seed["region_offloaded"]:
            values.append((speedup, weights.get(name, 1.0)))
            timed_bytes += row["region_bytes"]
            timed_cycles += row["benchmark_cycles"]
    if not values:
        raise ValueError("No Embench workload exercises DMA in its timed region")
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
        "dma_bytes_per_cycle": timed_bytes / timed_cycles,
        "source_hash": digest(source),
    }, vector


def evaluate(program_path):
    raise RuntimeError("Initialize the DMA experiment through loop.py")
