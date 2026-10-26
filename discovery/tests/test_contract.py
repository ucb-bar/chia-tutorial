"""Fixed-output, isolation, and recovery tests; fixture data comes from real RTL."""

from pathlib import Path
import copy
import json
import pytest

from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.experiment import validate_accelerator_files
from chia_dma.evaluation import aggregate, parse


@pytest.fixture
def configuration(tmp_path):
    c = load()
    c["storage"] = str(tmp_path / "storage")
    return c


@pytest.fixture
def records(configuration):
    data = json.loads(
        (Path(__file__).parent / "fixtures/rtl-measurements.json").read_text()
    )
    return {row["benchmark"]: row for row in data["embench"]}


def test_embench_reference_and_weighted_geomean(configuration, records):
    score, vector = aggregate(
        records, records, configuration, {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == 1
    assert len(vector) == len(records)
    faster = copy.deepcopy(records)
    for row in faster.values():
        row["benchmark_cycles"] /= 4
    score, _ = aggregate(
        faster, records, configuration, {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == pytest.approx(4)
    active = [r for k, r in records.items() if r["region_offloaded"]]
    assert score["dma_bytes_per_cycle"] == pytest.approx(
        sum(r["region_bytes"] for r in active)
        / sum(r["benchmark_cycles"] / 4 for r in active)
    )
    key = next(k for k, r in records.items() if r["region_offloaded"])
    configuration["fitness"]["workload_weights"] = {key: 2}
    faster[key]["benchmark_cycles"] *= 4
    score, _ = aggregate(
        faster, records, configuration, {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == pytest.approx(
        4 ** ((len(active) - 1) / (len(active) + 1))
    )


def test_activity_change_missing_workload_and_wrong_output_rejected(
    configuration, records
):
    key = next(k for k, r in records.items() if r["offloaded"])
    for field in ("bytes", "offloaded", "benchmark_result"):
        bad = copy.deepcopy(records)
        bad[key][field] += 1
        with pytest.raises(ValueError):
            aggregate(bad, records, configuration, {p: "source" for p in ALLOWLIST})
    bad = copy.deepcopy(records)
    del bad[key]
    with pytest.raises(ValueError, match="Full Embench"):
        aggregate(bad, records, configuration, {p: "source" for p in ALLOWLIST})


def test_failed_or_incomplete_simulation_never_yields_score(configuration, base_tree):
    for result in (
        {"success": False, "returncode": -1},
        {"success": True, "returncode": 0, "log": "EMBENCH result=0", "out": ""},
    ):
        with pytest.raises(ValueError):
            parse(result, configuration, base_tree)


def test_passive_hardware_metrics_are_required_by_chia(
    configuration, tmp_path, base_tree
):
    fixture = Path(__file__).parent / "fixtures/embench-observation.txt"
    parser = tmp_path / "tree/software/rocc-dma/scripts/metrics.py"
    parser.parent.mkdir(parents=True)
    parser.write_text(
        (base_tree / configuration["software"]["metrics_parser"]).read_text()
    )
    text = fixture.read_text()
    result = {"success": True, "returncode": 0, "log": text, "out": ""}
    row = parse(result, configuration, tmp_path / "tree")
    assert row["hardware"]["write_acks"] == 1
    assert row["copy_events"][0]["hw_cycles"] == 34
    for original, invalid in (
        ("outstanding=0", "outstanding=1"),
        ("benchmark_result=", "missing_benchmark_result="),
        ("verification_result=1", "verification_result=0"),
        ("verification_result=1", "verification_result=-1"),
        ("write_acks=1", "write_acks=0"),
        ("status=0", "status=1"),
    ):
        with pytest.raises(ValueError):
            parse(
                {**result, "log": text.replace(original, invalid)},
                configuration,
                tmp_path / "tree",
            )


def test_proposal_commit_is_immutable_and_has_exact_allowlist(tmp_path, configuration):
    from chia_dma.experiment import Experiment
    from skydiscover.search.base_database import Program

    import ray

    ray.init(
        address="local",
        num_cpus=1,
        include_dashboard=False,
        object_store_memory=128 * 1024 * 1024,
    )
    experiment = Experiment(configuration, tmp_path)
    try:
        bundle = {p: "original\n" for p in ALLOWLIST}
        modified = {**bundle, ALLOWLIST[0]: "original\n// proposal\n"}
        experiment.save_selection(
            Program(
                id="seed",
                solution=json.dumps({"source": bundle}),
                metadata={"island": 0},
            ),
            [],
            candidate_id="candidate",
        )
        saved = experiment.save_proposal(
            "candidate", modified, {"response": "generated", "backend": "codex"}
        )
        assert saved["source_hash"] == digest(modified) and saved["source"] == modified
        assert saved["generation"]["response"] == "generated"
        assert not (tmp_path / "candidates").exists()
        with pytest.raises(ValueError):
            experiment.save_proposal("candidate", bundle, {})
        with pytest.raises(ValueError):
            validate_accelerator_files({**bundle, "tests/cheat.c": "bad"})
    finally:
        experiment.close()
        ray.shutdown()


def test_all_three_real_chia_agent_classes_are_configurable(configuration):
    from chia_dma.agent import make_agent

    names = {
        "antigravity": "AntigravityLLM",
        "claude": "ClaudeCodeLLM",
        "codex": "CodexLLM",
    }
    for backend, classname in names.items():
        configuration["agent"]["backend"] = backend
        agent = make_agent(configuration)
        assert type(agent).__name__ == classname
        assert agent.timeout_seconds == configuration["agent"]["timeout_seconds"]


def test_allowlisted_file_cannot_change_fixed_soc_mixin_or_l1():
    from chia_dma.experiment import validate_integration

    source = {p: "source" for p in ALLOWLIST}
    source[ALLOWLIST[1]] = (
        "class Accelerator {\n  io.mem.req.valid := false.B\n}\nclass WithRoCCDMA(x: Int) extends Config(fixed)\n"
    )
    candidate = dict(source)
    candidate[ALLOWLIST[1]] = candidate[ALLOWLIST[1]].replace(
        "Config(fixed)", "Config(cheating)"
    )
    with pytest.raises(ValueError, match="integration"):
        validate_integration(candidate, source)
    candidate[ALLOWLIST[1]] = source[ALLOWLIST[1]].replace("false.B", "true.B")
    with pytest.raises(ValueError, match="L1"):
        validate_integration(candidate, source)
    validate_integration(source, source)
