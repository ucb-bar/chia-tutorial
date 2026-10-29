"""CHIA DAG: build the DMA simulator, run six memory tests, verify, then score."""

import asyncio
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time

import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.trace.profiler import get_profiler
from skydiscover.evaluation.evaluation_result import EvaluationResult

from chia_dma.config_loader import digest
from chia_dma.experiment import read_json, write_json
from chia_dma.nodes import build, simulate, parse, aggregate, software_inputs, require

class CorrectnessFailure(ValueError):
    """A deliberately generic failure; private cases never enter feedback."""


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
        hardware = dict(configuration["hardware"])
        hardware.pop("make_jobs", None)
        hardware.pop("build_timeout_seconds", None)
        software = dict(configuration["software"])
        software.pop("build_timeout_seconds", None)
        images = [configuration["cluster"]["available_node_types"][role]["docker"]["image"]
                  for role in ("build", "software")]
        self.cache_prefix = digest([Path(seed).name, hardware, software, images])

    async def evaluate_program(self, solution, program_id=""):
        try:
            return await asyncio.to_thread(self.evaluate, solution, program_id)
        except asyncio.CancelledError:
            self.stopped.set()
            raise

    @ChiaFunction()
    def evaluate(self, solution, program_id=""):
        """Read the saved proposal, then follow build → simulate → parse → score."""
        candidate = json.loads(solution)
        candidate_id = candidate["proposal_id"]
        recorded_metrics = self.experiment.evaluation_result(candidate_id)
        if recorded_metrics is not None:
            get_profiler().add_info({"candidate_id": candidate_id, "cached_evaluation": True,
                                     **recorded_metrics})
            return EvaluationResult(metrics=recorded_metrics)

        proposal = self.experiment.load_proposal(candidate_id)
        patch = proposal["patch"]
        cache_tag = f"{self.cache_prefix}/{proposal['patch_hash']}"
        log_directory = self.experiment.run / "artifacts" / candidate_id
        log_directory.mkdir(parents=True, exist_ok=True)
        (log_directory / "candidate.patch").write_text(patch)
        measurements = {}
        started = time.monotonic()
        timing = {"public": {}, "hidden": {}}
        hidden_task = None

        try:
            software = {}
            with closing(self.collect(self.build_binaries(patch, cache_tag))) as completed:
                for name, artifact in completed:
                    output = artifact.stdout + "\n" + artifact.stderr
                    (log_directory / (name + "-build.log")).write_text(output)
                    if name == "hardware":
                        simulator = artifact
                        require(simulator.success, "Simulator build failed")
                    else:
                        elf = self.configuration["software"]["elf"].format(benchmark=name)
                        require(artifact.success and artifact.files.get(elf),
                                name + ": candidate C compilation failed")
                        software[name] = artifact.files[elf]

            timing["build_seconds"] = time.monotonic() - started
            from chia_dma.correctness import prepare_scored_inputs, run_hidden_tests, verify_correctness
            challenge = self.resolve(prepare_scored_inputs.chia_remote(software))
            raw_results = {}
            rtl_started = time.monotonic()
            hidden_task = run_hidden_tests.chia_remote(
                self.configuration, ray.put(simulator), software,
                self.dram_settings, challenge["seeds"],
            )
            # Seeds are chosen after generation and compilation. No seed or
            # private observation enters the generation request or its mounts.
            simulations = self.run_simulations(
                ray.put(simulator), challenge["software"], cache_tag,
                challenge_tag=digest(challenge["seeds"]),
            )
            with closing(self.collect(simulations)) as completed:
                for workload, result in completed:
                    timing["public"][workload] = {
                        "wall_seconds": getattr(result, "wall_seconds", 0.0),
                        "worker_host": getattr(result, "worker_host", ""),
                    }
                    log_file = log_directory / (workload + ".log")
                    log_file.write_text(result.log + "\n" + result.out)
                    try:
                        measurements[workload] = self.resolve(parse.chia_remote(
                            asdict(result)
                        ))
                    except ValueError:
                        raise CorrectnessFailure("Correctness tests failed") from None
                    raw_results[workload] = result

            hidden_result = self.resolve(hidden_task)
            hidden_task = None
            timing["hidden"] = hidden_result
            timing["parallel_rtl_seconds"] = time.monotonic() - rtl_started
            if not self.resolve(verify_correctness.chia_remote(
                raw_results, challenge["seeds"], hidden_result,
            )):
                raise CorrectnessFailure("Hidden correctness tests failed")

            reference_id = self.experiment.sql.query_value(
                "SELECT id FROM proposals WHERE json_extract(metrics, '$.validity')=1 "
                "ORDER BY sequence LIMIT 1"
            )
            reference = self.experiment.benchmarks(reference_id) if reference_id else measurements
            metrics, benchmarks = self.resolve(aggregate.chia_remote(
                measurements, reference, patch,
            ))
            metrics["correctness"] = 1
        except CorrectnessFailure:
            metrics = {"validity": 0, "combined_score": 0.0, "correctness": 0}
            benchmarks = {"error": "Hidden correctness tests failed"}
        except InterruptedError:
            raise
        except Exception as error:
            metrics = {"validity": 0, "combined_score": 0.0}
            benchmarks = {"error": str(error)}
        finally:
            if hidden_task is not None:
                chia_cancel(getattr(hidden_task, "ref", hidden_task), force=True)
            timing["evaluation_seconds"] = time.monotonic() - started
            timing["generation_seconds"] = proposal["generation"].get("wall_seconds", 0.0)
            selected_at = self.experiment.selection(candidate_id).get("selected_at")
            timing["round_seconds"] = (time.time() - selected_at if selected_at is not None
                                       else timing["generation_seconds"] + timing["evaluation_seconds"])
            write_json(log_directory / "timing.json", timing)

        profile = self.experiment.save_evaluation(
            candidate_id, metrics, {**measurements, **benchmarks}, program_id
        )
        get_profiler().add_info(profile or {})
        return EvaluationResult(
            metrics=metrics, artifacts={"proposal_id": candidate_id}
        )

    def build_binaries(self, patch, cache_tag):
        """Submit hardware and six independent native RISC-V builds together."""
        inputs = ray.put(software_inputs(self.tree, patch))
        compiler = RiscvBuildNode(
            timeout_seconds=self.configuration["software"]["build_timeout_seconds"]
        )
        tasks = {}
        try:
            tasks["hardware"] = build.chia_remote(
                self.configuration, self.seed, patch, _chia_tag=cache_tag + "/build"
            )
            for name in self.expected:
                elf = self.configuration["software"]["elf"].format(benchmark=name)
                tasks[name] = compiler.build_program.chia_remote(
                    compiler, inputs,
                    [*self.configuration["software"]["build_command"], "NAMES=" + name],
                    "/tmp/dma-software", outputs=[elf],
                    _chia_tag=cache_tag + "/software/" + name,
                )
        except BaseException:
            for task in tasks.values():
                chia_cancel(getattr(task, "ref", task), force=True)
            raise
        return tasks

    def run_simulations(self, simulator, software, cache_tag, challenge_tag=""):
        simulations = {}
        workers = sorted(
            node["NodeID"] for node in ray.nodes()
            if node["Alive"] and node["Resources"].get("verilator_run", 0) >= 1
        )
        if not workers:
            raise RuntimeError("No existing simulation workers have joined Ray")
        try:
            for index, workload in enumerate(self.expected):
                # Run each test once, distributed across the existing VM workers.
                simulations[workload] = simulate.options(
                    scheduling_strategy=NodeAffinitySchedulingStrategy(
                        workers[index % len(workers)], soft=False
                    )
                ).chia_remote(
                    self.configuration,
                    simulator,
                    software[workload],
                    workload,
                    self.dram_settings,
                    _chia_tag=cache_tag + "/" + challenge_tag + "/" + workload,
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
        try:
            while pending:
                if self.stopped.is_set():
                    raise InterruptedError("Evaluation interrupted")
                ready, _ = ray.wait(
                    list(pending), num_returns=1, timeout=1
                )
                for reference in ready:
                    workload, task = pending.pop(reference)
                    yield workload, get(task)
        finally:
            for reference in pending:
                chia_cancel(reference, force=True)

    def resolve(self, task):
        """Keep single-node waits interruptible when search is canceled."""
        with closing(self.collect({"result": task})) as completed:
            return next(completed)[1]

    def close(self):
        self.stopped.set()


def evaluate(program_path):
    raise RuntimeError("Initialize the DMA experiment through loop.py")
