"""Memory-test correctness, complete coverage, DMA observations, and recovery."""

from pathlib import Path
import copy
import json
import pytest

from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.experiment import validate_candidate_files
from chia_dma.evaluation import aggregate as aggregate_node, parse as parse_node

# Unit-test the pure node bodies without starting Ray or its profiler.
aggregate = aggregate_node._chia_original
parse = parse_node._chia_original


@pytest.fixture
def configuration(tmp_path):
    c = load()
    c["storage"] = str(tmp_path / "storage")
    return c


@pytest.fixture
def workloads():
    from chia_dma.config import ROOT
    return json.loads((ROOT / "base/memory-tests/workloads.json").read_text())


@pytest.fixture
def records(workloads):
    # Synthetic score inputs, not claimed RTL measurements of the new suite.
    result = {}
    for name, spec in workloads.items():
        offloaded = 112 if name == "copy-mixed" else spec["calls"]
        count = 130048 if name == "copy-mixed" else spec["payload_bytes"]
        result[name] = {
            "benchmark_result": spec["checksum"], "verification_result": 1,
            "benchmark_cycles": 1000, "benchmark_instructions": 500,
            "intercepted": spec["calls"],
            "offloaded": offloaded, "fallback": spec["calls"] - offloaded,
            "bytes": count, "region_offloaded": offloaded, "region_bytes": count,
        }
    return result


def test_memory_reference_and_weighted_geomean(configuration, records, workloads):
    score, vector = aggregate(
        records, records, workloads, {}, {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == 1
    assert len(vector) == len(records)
    faster = copy.deepcopy(records)
    for row in faster.values():
        row["benchmark_cycles"] /= 4
    score, _ = aggregate(
        faster, records, workloads, configuration["fitness"]["workload_weights"],
        {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == pytest.approx(4)
    active = [r for k, r in records.items() if r["region_offloaded"]]
    assert score["cycles"] == 250 * len(records)
    assert score["instructions"] == 500 * len(records)
    assert score["cpi"] == pytest.approx(0.5)
    key = next(k for k, r in records.items() if r["region_offloaded"])
    configuration["fitness"]["workload_weights"] = {key: 2}
    faster[key]["benchmark_cycles"] *= 4
    score, _ = aggregate(
        faster, records, workloads, configuration["fitness"]["workload_weights"],
        {p: "source" for p in ALLOWLIST}
    )
    assert score["combined_score"] == pytest.approx(
        4 ** ((len(active) - 1) / (len(active) + 1))
    )


def test_changed_dma_activity_is_allowed_but_wrong_checksum_or_missing_test_is_not(
    configuration, records, workloads
):
    key = next(k for k, r in records.items() if r["offloaded"])
    for field in ("bytes", "offloaded", "fallback", "intercepted", "region_bytes", "region_offloaded"):
        bad = copy.deepcopy(records)
        bad[key][field] = 0
        score, _ = aggregate(bad, records, workloads, {}, {p: "source" for p in ALLOWLIST})
        assert score["validity"] == 1
    bad = copy.deepcopy(records)
    del bad[key]
    with pytest.raises(ValueError, match="Full memory-test"):
        aggregate(bad, records, workloads, {}, {p: "source" for p in ALLOWLIST})

    # Even an incorrect seed cannot redefine the fixed expected checksums.
    bad = copy.deepcopy(records)
    bad[key]["benchmark_result"] += 1
    with pytest.raises(ValueError, match="checksum"):
        aggregate(bad, bad, workloads, {}, {p: "source" for p in ALLOWLIST})


def test_failed_or_incomplete_simulation_never_yields_score():
    for result in (
        {"success": False, "returncode": -1},
        {"success": True, "returncode": 0, "log": "MEMORY_METRICS result=0", "out": ""},
    ):
        with pytest.raises(ValueError):
            parse(result, 1)


def test_fixed_checksum_is_required_and_dma_activity_is_diagnostic():
    fixture = Path(__file__).parent / "fixtures/memory-observation.txt"
    text = fixture.read_text()
    result = {"success": True, "returncode": 0, "log": text, "out": ""}
    row = parse(result, 1)
    assert row["hardware"][0]["write_acks"] == 1
    assert row["copy_events"][0]["hw_cycles"] == 34
    with pytest.raises(ValueError, match="verification"):
        parse(result, 2)
    for original, invalid in (
        ("benchmark_result=", "missing_benchmark_result="),
        ("verification_result=1", "verification_result=0"),
        ("verification_result=1", "verification_result=-1"),
        ("benchmark_cycles=80", "benchmark_cycles=0"),
        ("benchmark_instructions=40", "benchmark_instructions=0"),
    ):
        with pytest.raises(ValueError):
            parse(
                {**result, "log": text.replace(original, invalid)},
                1,
            )


def test_cycle_score_accepts_zero_dma_and_does_not_reward_ipc_alone(records, workloads):
    source = {path: "source" for path in ALLOWLIST}
    slower = copy.deepcopy(records)
    for row in slower.values():
        row["benchmark_cycles"] *= 2
        row["benchmark_instructions"] *= 4  # Better CPI, worse total execution time.
        row["region_offloaded"] = row["region_bytes"] = 0
    score, _ = aggregate(slower, records, workloads, {}, source)
    assert score["combined_score"] == pytest.approx(0.5)
    assert score["cpi"] == pytest.approx(1)
    faster = copy.deepcopy(records)
    for row in faster.values():
        row["benchmark_cycles"] /= 2
        row["benchmark_instructions"] /= 4  # Worse CPI, better total execution time.
        row["region_offloaded"] = row["region_bytes"] = 0
    score, _ = aggregate(faster, records, workloads, {}, source)
    assert score["combined_score"] == pytest.approx(2)
    assert score["cpi"] == pytest.approx(4)


def test_candidate_contains_both_hardware_and_c_but_no_checksum_reader():
    from chia_dma.config import HARDWARE_FILES, SOFTWARE_FILES
    source = {path: "source" for path in ALLOWLIST}
    assert len(HARDWARE_FILES) == 2 and len(SOFTWARE_FILES) == 6
    validate_candidate_files(source)
    with pytest.raises(ValueError):
        validate_candidate_files({path: "source" for path in HARDWARE_FILES})
    with pytest.raises(ValueError):
        validate_candidate_files({**source, "tests/memory-tests/src/copy-small/checksum.c": "changed"})


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
            validate_candidate_files({**bundle, "tests/cheat.c": "bad"})
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
