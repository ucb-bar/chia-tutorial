"""Real CHIA cache/profile and native controller, using inexpensive fixture tasks."""

import asyncio
import json
import os
from pathlib import Path
import time
import pytest
import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.cache import start_cache, stop_cache
from chia.base.bypass import Bypass
from chia.trace.profiler import start_collector, stop_collector, reset_profiler
from chia.chipyard.state_def import BuildArtifact, RunResult
from skydiscover.search.base_database import Program
from chia_dma.config import load, ALLOWLIST, SOFTWARE_FILES, digest
from chia_dma.experiment import (
    read_json,
    write_json,
    validate_integration,
)
from chia_dma.experiment import Experiment, save_proposal, load_proposal
from loop import create_search_controller, replay_candidate, run_search
from chia_dma.agent import CLIClient, ParentContext
from chia_dma.evaluation import DMAEvaluator
from chia_dma.runtime import cache_hit, from_cache


@ray.remote(num_cpus=0)
class Counts:
    def __init__(self):
        self.counts = {}
        self.active = 0
        self.peak = 0
        self.barrier = False

    def add(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1

    def parallel(self):
        self.barrier = True

    def enter(self):
        self.active += 1
        self.peak = max(self.peak, self.active)
        return self.barrier

    def leave(self):
        self.active -= 1

    def read(self):
        return self.counts, self.peak


@pytest.fixture
def flow(tmp_path, monkeypatch, request, base_tree):
    from chia_dma import agent, evaluation

    configuration = load()
    configuration["storage"] = str(tmp_path)
    root = Path(__file__).resolve().parents[1]
    ray.init(
        address="local",
        num_cpus=6,
        resources={"generation": 2, "chipyard": 1, "verilator_run": 2},
        include_dashboard=False,
        namespace="controller-proof",
        _node_ip_address="127.0.0.1",
        runtime_env={"env_vars": {"PYTHONPATH": str(root) + ":" + str(root / "tests")}},
    )
    request.addfinalizer(ray.shutdown)
    run = tmp_path / "run"
    run.mkdir()
    seed = tmp_path / "seed"
    tree = seed / "tree"
    source = {
        ALLOWLIST[0]: "class Controller {}\n",
        ALLOWLIST[
            1
        ]: "class Adapter {\n io.mem.req.valid := false.B\n}\nclass WithRoCCDMA() extends Config(fixed)\n",
    }
    source.update({name: "/* candidate C workload */\n" for name in SOFTWARE_FILES})
    for name, text in source.items():
        p = tree / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    fixture = root / "tests/fixtures/memory-observation.txt"
    specifications = read_json(base_tree / configuration["software"]["workloads"])
    write_json(tree / configuration["software"]["workloads"], specifications)
    output = fixture.read_text()
    elf = b"fixture ELF"
    counts = Counts.remote()

    @ChiaFunction(resources={"generation": 1})
    def generate(configuration, sql, task):
        saved = load_proposal(sql, task["id"], remote=True)
        if saved:
            return saved
        ray.get(counts.add.remote("generate"))
        if ray.get(counts.enter.remote()):
            for _ in range(250):
                if ray.get(counts.read.remote())[1] >= 2:
                    break
                time.sleep(0.02)
        time.sleep(0.1)
        parent = json.loads(task["parent"]["solution"])["source"]
        child = dict(parent)
        child[ALLOWLIST[0]] += (
            "// " + task["id"] + " " + configuration["agent"]["backend"] + "\n"
        )
        if configuration["agent"]["backend"] == "antigravity" and task["island"] == 1:
            child[ALLOWLIST[1]] += "// forbidden integration change\n"
        saved = save_proposal(
            sql,
            task["id"],
            child,
            {
                "backend": configuration["agent"]["backend"],
                "model": "fixture",
                "response": "fixture",
            },
            remote=True,
        )
        ray.get(counts.leave.remote())
        return saved

    @ChiaFunction(resources={"chipyard": 1})
    def build(configuration, seed, child):
        ray.get(counts.add.remote("build"))
        validate_integration(child, source)
        simulator = BuildArtifact(
            "fixture",
            b"ELF",
            "fixture",
            "DMARocketConfig",
            "chipyard",
            "verilator",
            True,
            "",
            "",
            0,
        )
        return {
            "simulator": simulator,
            "software": {name: elf for name in specifications},
            "software_log": "fixture C compiler output",
        }

    @ChiaFunction(resources={"verilator_run": 1})
    def simulate(configuration, artifact, content, name, ini):
        ray.get(counts.add.remote("simulate"))
        log = output.replace(
            "benchmark_result=1",
            "benchmark_result=" + str(specifications[name]["checksum"]),
        )
        return RunResult(name, log, "", 0, True)

    monkeypatch.setattr(agent, "generate", generate)
    monkeypatch.setattr(evaluation, "build", build)
    monkeypatch.setattr(evaluation, "simulate", simulate)

    @ChiaFunction(num_cpus=1)
    def aggregate(records, reference, workloads, weights, source):
        assert set(reference) == set(records)
        return (
            {
                "validity": 1,
                "combined_score": 1.0,
                "source_bytes": sum(map(len, source.values())),
                "cycles": 480,
                "instructions": 240,
                "cpi": 2.0,
            },
            {},
        )

    monkeypatch.setattr(evaluation, "aggregate", aggregate)
    start_collector(log_dir=str(tmp_path / "profile"))
    reset_profiler()
    cache = start_cache(
        2, str(tmp_path / "cache"), units="MB", yaml_path=configuration["_config_file"]
    )
    bypass = Bypass(configuration["_config_file"])
    for name in ("build", "simulate"):
        bypass.set_cond(name, cache_hit)
        bypass.set_provider(name, from_cache)
    experiment = Experiment(configuration, run)
    parent = Program(
        id="seed-dma",
        solution=json.dumps({"proposal_id": "seed-dma", "source": source}),
        language="json",
        metadata={"island": 0},
    )
    experiment.save_selection(parent, [], candidate_id="seed-dma", kind="seed")
    experiment.save_proposal("seed-dma", source, {"backend": None})
    evaluator = DMAEvaluator(configuration, experiment, seed)
    result = evaluator.evaluate(parent.solution)
    assert result.metrics["validity"] == 1, experiment.sql.query(
        "SELECT * FROM benchmarks"
    )
    assert not (run / "reference.json").exists()
    parent.metrics = result.metrics
    for island in (0, 1):
        p = Program(**parent.to_dict())
        p.id = f"seed-{island}"
        experiment.population.add(p, iteration=0, target_island=island)
    experiment.save_checkpoint()
    yield configuration, experiment, seed, evaluator, counts, cache, tmp_path
    experiment.close()
    stop_cache()
    stop_collector()
    reset_profiler()
    ray.shutdown()


def test_tutorial_flow_evaluates_seed_before_search(flow):
    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    configuration["search"]["iterations"] = 0
    fresh_run = tmp_path / "fresh-run"
    fresh = Experiment(configuration, fresh_run)
    try:
        evaluator = DMAEvaluator(configuration, fresh, seed)
        best = asyncio.run(run_search(configuration, fresh, evaluator, seed))
        assert best.metrics["validity"] == 1
        assert fresh.admitted == {"seed-dma"}
        assert all(fresh.population.islands)
        assert fresh.load_proposal("seed-dma") is not None
        assert get(counts.read.remote())[0].get("generate", 0) == 0
    finally:
        fresh.close()


def test_full_controller_concurrent_context_invalid_cache_profile_and_resume(flow):
    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    get(counts.parallel.remote())
    controller = create_search_controller(configuration, experiment, evaluator)
    asyncio.run(
        controller.run_discovery(
            1, 2, checkpoint_callback=experiment.save_checkpoint, retry_times=1
        )
    )
    values, peak = get(counts.read.remote())
    assert peak == 2
    assert values == {"build": 3, "simulate": 12, "generate": 2}
    requests = [r for r in experiment.selections() if r["kind"] == "search"]
    assert {r["island"] for r in requests} == {0, 1}
    valid = next(r for r in requests if r["island"] == 0)
    invalid = next(r for r in requests if r["island"] == 1)
    assert (
        valid["id"] in experiment.admitted and invalid["id"] not in experiment.admitted
    )
    assert experiment.evaluation_result(invalid["id"])["validity"] == 0
    assert all(
        p.metrics["validity"] == 1 for p in experiment.population.programs.values()
    )
    saved = read_json(experiment.run / "latest.json")["checkpoint"]
    native = read_json(
        experiment.run
        / "checkpoints"
        / saved
        / "population/openevolve_native_metadata.json"
    )
    assert len(native["islands"]) == 2
    p = experiment.load_proposal(valid["id"])
    # New query identity, identical source: numerical history is cold, CHIA is warm.
    for flush in (False, True):
        if flush:
            get(cache.flush.chia_remote())
        r = experiment.save_selection(Program(**valid["parent"]), [], kind="check")
        experiment.save_proposal(r["id"], p["source"], {})
        result = evaluator.evaluate(
            json.dumps({"proposal_id": r["id"], "source": p["source"]})
        )
        assert result.metrics["validity"] == 1
        current, _ = get(counts.read.remote())
        assert current["build"] == values["build"] + int(flush)
        assert current["simulate"] == values["simulate"] + 6 * int(flush)
        assert current["generate"] == 2
    experiment.close()
    restored = Experiment(configuration, experiment.run)
    try:
        assert (
            restored.admitted == experiment.admitted
            and restored.pending_proposals() == []
        )
        assert (
            restored.population.island_feature_maps
            == experiment.population.island_feature_maps
        )
    finally:
        restored.close()
    stop_collector()
    logs = list((tmp_path / "profile").glob("**/*log"))
    assert logs and any(
        "build" in p.read_text() and "simulate" in p.read_text() for p in logs
    )


def test_generation_survives_interruption_without_another_llm_call(flow):
    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    parent, _ = experiment.population.sample()
    prompt = ParentContext().build_prompt(parent, {})
    client = CLIClient(configuration, experiment)
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    before, _ = get(counts.read.remote())
    assert before["generate"] == 1
    request = experiment.selection(parent.metadata["request_id"])
    experiment.close()
    configuration["agent"]["backend"] = "codex"
    restored = Experiment(configuration, experiment.run)
    resumed = DMAEvaluator(configuration, restored, seed)
    controller = create_search_controller(configuration, restored, resumed)
    try:
        asyncio.run(
            controller.run_discovery(
                1, 1, checkpoint_callback=restored.save_checkpoint, retry_times=1
            )
        )
        after, _ = get(counts.read.remote())
        assert after["generate"] == 1
        assert request["id"] in restored.admitted
        child = next(
            p
            for p in restored.population.programs.values()
            if p.metadata["candidate_id"] == request["id"]
        )
        assert (
            child.parent_id == request["parent"]["id"]
            and child.metadata["island"] == request["island"]
        )
        original = restored.load_proposal(request["id"])
        assert original["backend"] == "antigravity"
        # A newly sampled proposal uses the changed backend.
        asyncio.run(
            controller.run_discovery(
                2, 1, checkpoint_callback=restored.save_checkpoint, retry_times=1
            )
        )
        latest = max(restored.selections(), key=lambda r: r["sequence"])
        assert restored.load_proposal(latest["id"])["backend"] == "codex"
    finally:
        restored.close()


def test_historical_fork_retains_pending_context_excludes_future_and_replay_is_independent(
    flow,
):
    from chia_dma.experiment import fork

    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    write_json(
        experiment.run / "run.json",
        {"run_id": experiment.run.name, "seed": str(seed)},
    )
    parent, _ = experiment.population.sample()
    selected = experiment.selection(parent.metadata["request_id"])
    # Checkpoint a selection whose generation has not yet completed.
    cp = experiment.save_checkpoint()
    configuration["agent"]["backend"] = "codex"
    client = CLIClient(configuration, experiment)
    prompt = ParentContext().build_prompt(parent, {})
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    controller = create_search_controller(configuration, experiment, evaluator)
    experiment.recovery = experiment.pending_proposals()
    asyncio.run(
        controller.run_discovery(
            1, 1, checkpoint_callback=experiment.save_checkpoint, retry_times=1
        )
    )
    assert selected["id"] in experiment.admitted
    original_latest = (experiment.run / "latest.json").read_bytes()
    child_path = tmp_path / "fork"
    fork(experiment.run, cp, child_path)
    child = Experiment(configuration, child_path)
    try:
        assert child.admitted == {"seed-dma"}
        assert [r["id"] for r in child.pending_proposals()] == [selected["id"]]
        assert child.selection(selected["id"]) == selected
        assert child.load_proposal(selected["id"]) is None
        assert (
            child.sql.query_value(
                "SELECT backend FROM proposals WHERE id=?", (selected["id"],)
            )
            == selected["backend"]
        )
        assert child.evaluation_result(selected["id"]) is None
        before_resume, _ = get(counts.read.remote())
        branch_evaluator = DMAEvaluator(configuration, child, seed)
        branch_controller = create_search_controller(
            configuration, child, branch_evaluator
        )
        asyncio.run(
            branch_controller.run_discovery(
                1, 1, checkpoint_callback=child.save_checkpoint, retry_times=1
            )
        )
        assert get(counts.read.remote())[0]["generate"] == before_resume["generate"] + 1
        assert selected["id"] in child.admitted
        # A historical replay uses a new tag and never calls the generator/add.
        before, _ = get(counts.read.remote())
        replay = DMAEvaluator(configuration, child, seed)
        result = asyncio.run(replay_candidate(child, replay, selected["id"]))
        after, _ = get(counts.read.remote())
        assert (
            result.metrics["validity"] == 1 and after["generate"] == before["generate"]
        )
        assert (
            after["build"] == before["build"] + 1
            and after["simulate"] == before["simulate"] + 6
        )
        assert child.admitted == {"seed-dma", selected["id"]}
        assert (experiment.run / "latest.json").read_bytes() == original_latest
    finally:
        child.close()


def test_completed_evaluation_before_admission_recovers_from_sql_without_generation(
    flow,
):
    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    parent, _ = experiment.population.sample()
    prompt = ParentContext().build_prompt(parent, {})
    client = CLIClient(configuration, experiment)
    response = asyncio.run(
        client.generate(prompt["system"], [{"role": "user", "content": prompt["user"]}])
    )
    from skydiscover.utils.code_utils import parse_full_rewrite

    payload = parse_full_rewrite(response.text, "json")
    result = evaluator.evaluate(payload, "lost-program-id")
    assert result.metrics["validity"] == 1
    before, _ = get(counts.read.remote())
    experiment.close()
    restored = Experiment(configuration, experiment.run)
    try:
        resumed = DMAEvaluator(configuration, restored, seed)
        controller = create_search_controller(configuration, restored, resumed)
        asyncio.run(
            controller.run_discovery(
                1, 1, checkpoint_callback=restored.save_checkpoint, retry_times=1
            )
        )
        assert get(counts.read.remote())[0] == before
        candidate_id = json.loads(payload)["proposal_id"]
        assert candidate_id in restored.admitted
        actual = restored.sql.query_value(
            "SELECT program_id FROM proposals WHERE id=?", (candidate_id,)
        )
        assert actual != "lost-program-id" and actual in restored.population.programs
    finally:
        restored.close()


def test_online_candidate_fork_restores_full_state_and_continues_independently(flow):
    from chia_dma.experiment import RunLock
    from chia_dma.experiment import fork

    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    configuration["agent"]["backend"] = "codex"
    controller = create_search_controller(configuration, experiment, evaluator)
    target = tmp_path / "other-copy/loop/experiments/branch"
    write_json(
        experiment.run / "run.json", {"run_id": experiment.run.name, "seed": str(seed)}
    )

    async def branch_while_running():
        await controller.run_discovery(
            1, 1, checkpoint_callback=experiment.save_checkpoint, retry_times=1
        )
        selected = experiment.selections()[-1]["id"]
        checkpoint = read_json(experiment.run / "latest.json")["checkpoint"]
        continuing = asyncio.create_task(
            controller.run_discovery(
                2, 3, checkpoint_callback=experiment.save_checkpoint, retry_times=1
            )
        )
        for _ in range(500):
            if get(counts.read.remote())[0]["generate"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert not continuing.done()
        await asyncio.to_thread(fork, experiment.run, None, target, candidate=selected)
        await continuing
        return selected, checkpoint

    with RunLock(experiment.run):
        selected, checkpoint = asyncio.run(branch_while_running())
    parent_requests = experiment.selections()
    parent_latest = (experiment.run / "latest.json").read_bytes()
    child = Experiment(configuration, target)
    try:
        assert read_json(target / "latest.json")["checkpoint"] == checkpoint
        assert (
            child.admitted == {"seed-dma", selected} and not child.pending_proposals()
        )
        assert child.benchmarks("seed-dma") == experiment.benchmarks("seed-dma")
        assert set(child.population.programs) == {
            p.stem
            for p in (
                experiment.run / "checkpoints" / checkpoint / "population/programs"
            ).glob("*.json")
        }
        assert len(child.selections()) == 2 and len(parent_requests) == 5
        native = Path("population/openevolve_native_metadata.json")
        assert (target / "checkpoints" / checkpoint / native).read_bytes() == (
            experiment.run / "checkpoints" / checkpoint / native
        ).read_bytes()
        resumed = create_search_controller(
            configuration, child, DMAEvaluator(configuration, child, seed)
        )
        asyncio.run(
            resumed.run_discovery(
                child.population.last_iteration + 1,
                1,
                checkpoint_callback=child.save_checkpoint,
                retry_times=1,
            )
        )
        assert len(child.selections()) == 3
        assert experiment.selections() == parent_requests
        assert (experiment.run / "latest.json").read_bytes() == parent_latest
    finally:
        child.close()


def test_native_migration_preserves_original_candidate_ids(flow):
    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    configuration["agent"]["backend"] = "codex"
    # Prefer unique children over the seed already present on both islands.
    for program in experiment.population.programs.values():
        program.metrics["combined_score"] = 0.5
    controller = create_search_controller(configuration, experiment, evaluator)
    asyncio.run(
        controller.run_discovery(
            1, 8, checkpoint_callback=experiment.save_checkpoint, retry_times=1
        )
    )
    assert experiment.population.last_migration_generation > 0
    assert len(experiment.selections()) == 9 and len(experiment.admitted) == 9
    assert all(
        p.metadata["candidate_id"] in experiment.admitted
        for p in experiment.population.programs.values()
    )
    assert any(
        p.metadata.get("migrant") for p in experiment.population.programs.values()
    )


def test_cached_evaluation_timeout_cancels_raw_ref_and_preserves_completed_proposal(
    flow, monkeypatch
):
    from chia_dma import evaluation

    configuration, experiment, seed, evaluator, counts, cache, tmp_path = flow
    parent = next(iter(experiment.population.programs.values()))
    request = experiment.save_selection(parent, [], kind="search")
    source = json.loads(parent.solution)["source"]
    source[ALLOWLIST[0]] += "// timeout case\n"
    experiment.save_proposal(request["id"], source, {})

    @ChiaFunction(resources={"chipyard": 1}, max_retries=0)
    def build(configuration, seed, source):
        time.sleep(60)

    monkeypatch.setattr(evaluation, "build", build)
    configuration["runtime"]["task_timeout_seconds"] = 0
    original_cancel = evaluation.chia_cancel
    cancelled = []

    def cancel(ref, **kwargs):
        assert isinstance(ref, ray.ObjectRef)
        cancelled.append(ref)
        return original_cancel(ref, **kwargs)

    monkeypatch.setattr(evaluation, "chia_cancel", cancel)
    with pytest.raises(TimeoutError, match="CHIA task deadline"):
        evaluator.evaluate(json.dumps({"proposal_id": request["id"], "source": source}))
    assert len(cancelled) == 1 and experiment.evaluation_result(request["id"]) is None
    assert experiment.load_proposal(request["id"])["source_hash"] == digest(source)
