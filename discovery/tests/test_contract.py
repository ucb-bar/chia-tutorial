"""Memory-test correctness, complete coverage, DMA observations, and recovery."""

from pathlib import Path
import copy
import json
import pytest

from chia_dma.config_loader import HARDWARE_FILES, SOFTWARE_FILES, digest, load
from chia_dma.evaluation import aggregate as aggregate_node, parse as parse_node

EXAMPLE_FILES = HARDWARE_FILES + SOFTWARE_FILES

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
    from chia_dma.config_loader import ROOT
    return json.loads((ROOT / "base/memory-tests/workloads.json").read_text())


@pytest.fixture
def records(workloads):
    # Synthetic score inputs, not claimed RTL measurements of the new suite.
    result = {}
    for name, spec in workloads.items():
        offloaded = 14 if name == "copy-mixed" else spec["calls"]
        count = 16256 if name == "copy-mixed" else spec["payload_bytes"]
        result[name] = {
            "benchmark_result": spec["checksum"], "verification_result": 1,
            "benchmark_cycles": 1000, "benchmark_instructions": 500,
            "intercepted": spec["calls"],
            "offloaded": offloaded, "fallback": spec["calls"] - offloaded,
            "bytes": count, "region_offloaded": offloaded, "region_bytes": count,
        }
    return result


def test_memory_reference_and_geomean(records, workloads):
    score, vector = aggregate(
        records, records, workloads, "candidate patch"
    )
    assert score["combined_score"] == 1
    assert len(vector) == len(records)
    faster = copy.deepcopy(records)
    for row in faster.values():
        row["benchmark_cycles"] /= 4
    score, _ = aggregate(
        faster, records, workloads,
        "candidate patch"
    )
    assert score["combined_score"] == pytest.approx(4)
    assert score["cycles"] == 250 * len(records)
    assert score["instructions"] == 500 * len(records)
    assert score["cpi"] == pytest.approx(0.5)
    key = next(k for k, r in records.items() if r["region_offloaded"])
    faster[key]["benchmark_cycles"] *= 4
    score, _ = aggregate(
        faster, records, workloads,
        "candidate patch"
    )
    assert score["combined_score"] == pytest.approx(
        4 ** ((len(records) - 1) / len(records))
    )


def test_changed_dma_activity_is_allowed_but_wrong_checksum_or_missing_test_is_not(
    configuration, records, workloads
):
    key = next(k for k, r in records.items() if r["offloaded"])
    for field in ("bytes", "offloaded", "fallback", "intercepted", "region_bytes", "region_offloaded"):
        bad = copy.deepcopy(records)
        bad[key][field] = 0
        score, _ = aggregate(bad, records, workloads, "candidate patch")
        assert score["validity"] == 1
    bad = copy.deepcopy(records)
    del bad[key]
    with pytest.raises(ValueError, match="Full memory-test"):
        aggregate(bad, records, workloads, "candidate patch")

    # Even an incorrect seed cannot redefine the fixed expected checksums.
    bad = copy.deepcopy(records)
    bad[key]["benchmark_result"] += 1
    with pytest.raises(ValueError, match="checksum"):
        aggregate(bad, bad, workloads, "candidate patch")


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
    assert row["benchmark_cycles"] == 80
    assert "hardware" not in row and "copy_events" not in row
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
    source = "candidate patch"
    slower = copy.deepcopy(records)
    for row in slower.values():
        row["benchmark_cycles"] *= 2
        row["benchmark_instructions"] *= 4  # Better CPI, worse total execution time.
        row["region_offloaded"] = row["region_bytes"] = 0
    score, _ = aggregate(slower, records, workloads, source)
    assert score["combined_score"] == pytest.approx(0.5)
    assert score["cpi"] == pytest.approx(1)
    faster = copy.deepcopy(records)
    for row in faster.values():
        row["benchmark_cycles"] /= 2
        row["benchmark_instructions"] /= 4  # Worse CPI, better total execution time.
        row["region_offloaded"] = row["region_bytes"] = 0
    score, _ = aggregate(faster, records, workloads, source)
    assert score["combined_score"] == pytest.approx(2)
    assert score["cpi"] == pytest.approx(4)


def test_candidates_accept_arbitrary_paths_and_reject_checkout_escapes(tmp_path, base_tree, patch_for):
    from chia_dma.experiment import materialize_candidate
    patch = patch_for({"build.sbt": "changed", "new/module.scala": "new"})
    work = tmp_path / "restored"
    materialize_candidate(work, base_tree, patch)
    assert (work / "new/module.scala").read_text() == "new"
    for name in ("../outside", "/tmp/outside", "a/../../outside", ".git/config"):
        bad = (f"diff --git a/{name} b/{name}\nnew file mode 100644\n"
               f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+bad\n")
        with pytest.raises(ValueError):
            materialize_candidate(work, base_tree, bad)


def test_proposal_commit_is_immutable_with_unrestricted_source_paths(tmp_path, configuration, patch_for):
    from chia_dma.experiment import Experiment
    from skydiscover.search.base_database import Program

    import ray

    ray.shutdown()
    ray.init(
        address="local",
        num_cpus=1,
        include_dashboard=False,
        object_store_memory=128 * 1024 * 1024,
    )
    experiment = Experiment(configuration, tmp_path)
    try:
        bundle = patch_for({p: "original\n" for p in EXAMPLE_FILES})
        modified = patch_for({EXAMPLE_FILES[0]: "original\n// proposal\n"})
        experiment.save_selection(
            Program(
                id="seed",
                solution=json.dumps({"patch": bundle}),
                metadata={"island": 0},
            ),
            [],
            candidate_id="candidate",
        )
        saved = experiment.save_proposal(
            "candidate", modified, {"response": "generated", "backend": "antigravity"}
        )
        assert saved["patch_hash"] == digest(modified.encode()) and saved["patch"] == modified
        assert saved["generation"]["response"] == "generated"
        assert not (tmp_path / "candidates").exists()
        with pytest.raises(ValueError):
            experiment.save_proposal("candidate", bundle, {})
    finally:
        experiment.close()
        ray.shutdown()


def test_tutorial_uses_antigravity_with_the_requested_effort(configuration, monkeypatch):
    from chia_dma.agent import make_agent
    from chia.models.antigravity import AntigravityQueryResult
    import ray
    agent = make_agent(configuration)
    assert type(agent).__name__ == "AntigravityLLM"
    assert agent.timeout_seconds == configuration["agent"]["timeout_seconds"]
    assert agent.extra_cli_args == ["--effort", "high"]
    calls = []
    def cli(*args):
        calls.append(True)
        return AntigravityQueryResult(result="candidate", returncode=0, stderr="",
                                      stream_result="", success=True)
    monkeypatch.setattr(agent, "_run_antigravity", cli)
    try:
        assert agent.prompt("Optimize the workload").success
        assert calls == [True]  # The native API counts attempts in `retries`.
    finally:
        ray.shutdown()
