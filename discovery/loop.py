#!/usr/bin/env python3
"""DMA discovery with native CHIA workers and SkyDiscover search."""

import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import time

from skydiscover.config import (
    Config,
    EvaluatorConfig,
    LLMConfig,
    LLMModelConfig,
    SearchConfig,
)
from skydiscover.search.base_database import Program
from skydiscover.search.default_discovery_controller import (
    DiscoveryController,
    DiscoveryControllerInput,
)

from chia_dma.config_loader import ROOT, load
from chia_dma.evaluation import DMAEvaluator
from chia_dma.experiment import Experiment, RunLock, fork
from chia_dma.runtime import Runtime, prepare
from chia_dma.search import CLIClient, ParentContext
from chia_dma.source import read_json, write_json


def run_experiment(configuration, run_directory, action):
    started = time.monotonic()
    with RunLock(run_directory):
        metadata = prepare(configuration, run_directory)
        seed_directory = metadata["seed"]
        # Generation workers read the parent's baseline from this mounted seed.
        configuration["seed"] = seed_directory
        if action == "prepare":
            print("Prepared " + str(run_directory) + " with seed " + seed_directory)
            return

        status = "interrupted"
        try:
            with Runtime(configuration, run_directory), closing(
                Experiment(configuration, run_directory)
            ) as experiment:
                with closing(
                    DMAEvaluator(configuration, experiment, seed_directory)
                ) as evaluator:
                    best = asyncio.run(
                        run_search(configuration, experiment, evaluator, seed_directory)
                    )
                    print("Completed; best=" + str(best.id if best else None))
            status = "complete"
        except Exception:
            status = "failed"
            raise
        finally:
            write_json(run_directory / "artifacts/exercise-timing.json", {
                "wall_seconds": time.monotonic() - started,
                "status": status,
            })


async def run_search(configuration, experiment, evaluator, seed_directory):
    """Evaluate the baseline and run the requested optimization iterations."""
    if not experiment.population.programs:
        await evaluate_seed(configuration, experiment, evaluator, seed_directory)

    search_requests = [
        request for request in experiment.selections() if request["kind"] == "search"
    ]
    proposal_limit = configuration["search"]["iterations"]
    new_proposals = max(0, proposal_limit - len(search_requests))
    remaining_iterations = len(experiment.recovery) + new_proposals
    first_iteration = experiment.population.last_iteration + 1

    with closing(
        create_search_controller(configuration, experiment, evaluator)
    ) as controller:
        best = await controller.run_discovery(
            first_iteration,
            remaining_iterations,
            checkpoint_callback=experiment.save_checkpoint,
            retry_times=1,
        )
        if experiment.pending_proposals():
            print("Unfinished proposals were saved; resume to continue them.")
        return best


def create_search_controller(configuration, experiment, evaluator):
    agent = CLIClient(configuration, experiment)
    model = LLMModelConfig(name="chia-cli", init_client=lambda _: agent)
    search_config = Config(
        language="json",
        file_suffix=".json",
        diff_based_generation=False,
        max_solution_length=2_000_000,
        max_parallel_iterations=configuration["search"]["max_in_flight"],
        checkpoint_interval=1,
        evaluator=EvaluatorConfig(cascade_evaluation=False),
        llm=LLMConfig(models=[model]),
        search=SearchConfig(
            type=configuration["search"]["type"],
            num_context_programs=configuration["search"]["inspirations"],
        ),
    )
    controller = DiscoveryController(
        DiscoveryControllerInput(
            config=search_config,
            evaluation_file=str(ROOT / "chia_dma/evaluation.py"),
            database=experiment.population,
            output_dir=str(experiment.run),
        )
    )
    controller.evaluator.close()
    controller.evaluator = evaluator
    controller.context_builder = ParentContext()
    return controller


async def evaluate_seed(configuration, experiment, evaluator, seed_directory):
    patch = ""  # An empty Git patch selects the prepared tutorial baseline.
    seed_solution = json.dumps({"proposal_id": "seed-dma", "patch": patch})
    seed_program = Program(
        id="seed-dma", solution=seed_solution, language="json", metadata={"island": 0}
    )
    already_reserved = experiment.sql.query_value(
        "SELECT COUNT(*) FROM proposals WHERE id='seed-dma'"
    )
    if not already_reserved:
        experiment.save_selection(
            seed_program, [], candidate_id="seed-dma", kind="seed"
        )
    experiment.save_proposal("seed-dma", patch, {"backend": None})

    result = await evaluator.evaluate_program(seed_solution, "seed-dma")
    if result.metrics.get("validity") != 1:
        print("Baseline evaluation failed; continuing so the agent can propose fixes.")

    for island in range(configuration["search"]["num_islands"]):
        island_seed = Program(
            id=f"seed-{island}",
            solution=seed_solution,
            language="json",
            metrics=result.metrics,
            metadata={"island": island},
        )
        experiment.population.add(island_seed, iteration=0, target_island=island)
    experiment.save_checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "run", "resume", "status", "fork", "scores"),
    )
    parser.add_argument("--config", default=str(ROOT / "cluster.yaml"))
    parser.add_argument("--run", default="run-001")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--candidate")
    selection.add_argument("--checkpoint")
    parser.add_argument("--new-run", help="New run name or absolute experiment path")
    args = parser.parse_args()

    for name in (args.candidate, args.checkpoint):
        if name and (Path(name).name != name or name in (".", "..")):
            parser.error("Use simple run/candidate/checkpoint names")
    if args.action == "fork" and not args.new_run:
        parser.error("fork requires --new-run")

    configuration = load(args.config)
    run_directory = Path(configuration["storage"]) / args.run
    if args.action == "scores":
        from chia_dma.score_viz import render_scores
        try:
            print(render_scores(run_directory))
        except FileNotFoundError:
            parser.error("No profile log yet for run " + repr(args.run))
    elif args.action == "fork":
        destination = run_directory.parent / Path(args.new_run).expanduser()
        fork(run_directory, args.checkpoint, destination, candidate=args.candidate)
    elif args.action == "status":
        latest_path = run_directory / "latest.json"
        if not latest_path.exists():
            if not (run_directory / "run.json").exists():
                parser.error(f"Run {args.run!r} has not been prepared")
            print(f"No checkpoint yet for run {args.run!r}.")
            print("The first checkpoint is saved after the baseline evaluation finishes.")
            print("Check the submitted job with chia job status or chia job logs.")
            return
        latest = read_json(latest_path)
        checkpoint_directory = run_directory / "checkpoints" / latest["checkpoint"]
        state = read_json(checkpoint_directory / "state.json")
        # Proposals count toward tutorial.search.iterations; the baseline does not.
        proposals = sum(1 for name in state["requests"] if name != "seed-dma")
        print(json.dumps({"proposals": proposals, **state}, indent=2))
    else:
        if (
            args.action not in ("prepare", "run")
            and not (run_directory / "run.json").exists()
        ):
            parser.error("Prepare a new experiment first")
        run_directory.mkdir(parents=True, exist_ok=True)
        run_experiment(configuration, run_directory, args.action)


if __name__ == "__main__":
    main()
