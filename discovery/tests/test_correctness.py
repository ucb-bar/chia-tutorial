"""Seeded full-output validation, including the former one-word checksum exploit."""

from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile

import pytest
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia_dma import correctness
from chia_dma.evaluation import software_inputs


@pytest.fixture(scope="module")
def native_binaries(base_tree, tmp_path_factory):
    folder = tmp_path_factory.mktemp("seeded-native")
    names = tuple(correctness.reference_outputs(0))
    paths = [f"tests/memory-tests/build/native/{name}" for name in names]
    result = RiscvBuildNode().build_program(
        software_inputs(base_tree, ""),
        ["make", "-s", "-C", "tests/memory-tests", "-j6", "host"],
        str(folder), outputs=paths,
    )
    assert result.success, result.stderr
    return {name: result.files[path] for name, path in zip(names, paths)}


def execute(content):
    with tempfile.TemporaryDirectory() as directory:
        binary = Path(directory) / "benchmark"
        binary.write_bytes(content)
        binary.chmod(0o755)
        result = subprocess.run([str(binary)], capture_output=True, timeout=30, cwd=directory)
    from chia_dma.observations import FRAME, decode_observation
    log = decode_observation(result.stdout) if FRAME in result.stdout else result.stdout.decode()
    return SimpleNamespace(success=result.returncode == 0, returncode=result.returncode,
                           log=log, out=result.stderr.decode())


@pytest.mark.parametrize("seed", [1, 0x8000000000000000, 0xFFFFFFFFFFFFFFFF])
def test_all_six_workloads_match_the_independent_reference_for_runtime_seeds(native_binaries, seed):
    references = correctness.reference_outputs(seed)
    for name, content in native_binaries.items():
        seeded = correctness.seeded_binary(content, seed)
        assert len(seeded) == len(content)
        result = execute(seeded)
        assert result.success, result.log + result.out
        assert correctness.matches_output(result, references[name]), name


def test_reference_preserves_public_seed_zero_checksums():
    from chia_dma.config_loader import ROOT
    from chia_dma.experiment import read_json
    manifest = read_json(ROOT / "base/memory-tests/workloads.json")
    for name, (words, witness) in correctness.reference_outputs(0).items():
        value = 0x6D2B79F5
        for word in words:
            value = correctness.fold(value, word)
        assert ((value ^ witness) & 0x7FFFFFFF) == manifest[name]["checksum"]


def test_former_one_word_checksum_hack_fails_full_output_verification(base_tree, tmp_path, patch_for):
    state = 0x6D2B79F5
    words = len(correctness.reference_outputs(0)["copy-small"][0])
    for _ in range(words):
        state = ((state << 5) | (state >> 27)) & 0xFFFFFFFF
    from chia_dma.config_loader import ROOT
    from chia_dma.experiment import read_json
    correction = state ^ read_json(ROOT / "base/memory-tests/workloads.json")["copy-small"]["checksum"]
    source = {
        "tests/memory-tests/src/copy-small/copy-small.c":
        f'#include "memory_test.h"\nextern uint64_t destination[{words}];\n'
        'void initialise_benchmark(void) {}\n'
        f'void benchmark(void) {{ destination[{words - 1}] = {correction}ULL; }}\n'
    }
    output = "tests/memory-tests/build/native/copy-small"
    result = RiscvBuildNode().build_program(
        software_inputs(base_tree, patch_for(source)),
        ["make", "-s", "-C", "tests/memory-tests", "host", "NAMES=copy-small"],
        str(tmp_path), outputs=[output],
    )
    assert result.success, result.stderr
    assert execute(result.files[output]).success
    observed = execute(correctness.seeded_binary(result.files[output], 0))
    assert not correctness.matches_output(observed, correctness.reference_outputs(0)["copy-small"])
    seeded = execute(correctness.seeded_binary(result.files[output], 1234))
    assert not correctness.matches_output(seeded, correctness.reference_outputs(1234)["copy-small"])


def test_private_gate_runs_two_additional_seeds_and_returns_only_boolean(native_binaries, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, wait
    from chia_dma import nodes
    calls, placements = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        class Simulation:
            def options(self, **kwargs):
                placements.append(kwargs["scheduling_strategy"].node_id)
                return self
            def chia_remote(self, configuration, artifact, content, name, ini):
                offset = content.index(correctness.MARKER) + len(correctness.MARKER)
                calls.append((int.from_bytes(content[offset:offset + 8], "little"),
                              int.from_bytes(content[offset + 8:offset + 16], "little")))
                return pool.submit(execute, content)
        def get(task):
            assert len(calls) == 2  # Both tests were dispatched before waiting.
            return task.result()
        monkeypatch.setattr(nodes, "simulate", Simulation())
        monkeypatch.setattr(correctness, "get", get)
        monkeypatch.setattr(correctness, "chia_cancel", lambda task, **kwargs: task.cancel())
        monkeypatch.setattr(correctness.ray, "nodes", lambda: [
            {"NodeID": f"{index:056x}", "Alive": True, "Resources": {"verilator_run": 2}}
            for index in range(1, 6)])
        monkeypatch.setattr(correctness.ray, "wait", lambda refs, **kwargs: (list(wait(refs).done), []))
        challenge = correctness.prepare_scored_inputs._chia_original(native_binaries)
        scored = {name: execute(content) for name, content in challenge["software"].items()}
        hidden = correctness.run_hidden_tests._chia_original({}, None, native_binaries, {}, challenge["seeds"])
    assert hidden["passed"] is True and len(hidden["tests"]) == 2
    assert set(hidden) == {"passed", "tests", "wall_seconds"}
    assert len(set(placements)) == 2
    assert len(calls) == 2 and len({seed for seed, _ in calls}) == 2
    assert all(mode == 1 and seed != 0 for seed, mode in calls)
    assert {seed for seed, _ in calls}.isdisjoint(challenge["seeds"].values())
    assert correctness.verify_correctness._chia_original(scored, challenge["seeds"], hidden) is True
    scored["copy-small"].log = "forged or incomplete output"
    assert correctness.verify_correctness._chia_original(scored, challenge["seeds"], hidden) is False


def test_missing_runtime_record_and_duplicate_observations_fail(native_binaries):
    with pytest.raises(ValueError):
        correctness.seeded_binary(b"not an input-bearing ELF", 1)
    reference = correctness.reference_outputs(1)["copy-chain"]
    result = execute(correctness.seeded_binary(native_binaries["copy-chain"], 1))
    assert correctness.matches_output(result, reference)
    result.log += result.log
    assert not correctness.matches_output(result, reference)


def test_valid_outputs_are_accepted_regardless_of_wall_time(native_binaries):
    from chia_dma.evaluation import parse
    reference = correctness.reference_outputs(1)["copy-chain"]
    result = execute(correctness.seeded_binary(native_binaries["copy-chain"], 1))
    result.wall_seconds = 3600
    assert correctness.matches_output(result, reference)
    metrics = {
        "success": True, "returncode": 0, "wall_seconds": 3600, "out": "",
        "log": (Path(__file__).parent / "fixtures/memory-observation.txt").read_text(),
    }
    assert parse._chia_original(metrics, 1)["rtl_wall_seconds"] == 3600


@pytest.mark.parametrize("words", [0, 1, 5, 6, 7, 14, 15, 16, 17, 30, 127, 128])
def test_bulk_observation_transfer_preserves_all_words_across_varied_lengths(tmp_path, words):
    import hashlib
    import struct
    from chia_dma.config_loader import ROOT
    source = tmp_path / "digest.c"
    source.write_text('''#include <stdio.h>
#include <stdlib.h>
#include "memory_test.h"
int main(int argc, char **argv) {
    size_t n = (size_t)strtoul(argv[1], 0, 10);
    uint64_t data[128];
    for (size_t i = 0; i < n; ++i)
        data[i] = (UINT64_C(0x9e3779b97f4a7c15) * (i + 1)) ^ UINT64_C(0xabcdef0123456789);
    memory_test_configuration.observe = 1;
    memory_test_observe(data, n, (uint32_t)(n * 17));
    memory_test_report();
    return 0;
}
''')
    binary = tmp_path / "digest"
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-DMEMORY_NATIVE", "-I" + str(ROOT / "base/memory-tests/support"),
                    str(source), str(ROOT / "base/memory-tests/support/memory_test.c"), "-o", str(binary)], check=True, capture_output=True)
    result = subprocess.run([str(binary), str(words)], capture_output=True, check=True)
    values = [(((i + 1) * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)) ^ 0xABCDEF0123456789
              for i in range(words)]
    content = struct.pack("<QQ", words, words * 17) + b"".join(struct.pack("<Q", v) for v in values)
    from chia_dma.observations import decode_observation
    decoded = decode_observation(result.stdout)
    expected = hashlib.sha256(content).hexdigest()
    assert decoded.endswith(f"MEMORY_OBSERVATION words={words} witness={words * 17} digest={expected}\n")


@pytest.mark.parametrize("raw", [b"missing", b"CHIA-OUTPUT-0001short", b"CHIA-OUTPUT-0001" + b"\x00" * 16])
def test_bulk_transfer_rejects_missing_or_truncated_frames(raw):
    from chia_dma.observations import decode_observation
    with pytest.raises(ValueError):
        decode_observation(raw)


def test_bulk_transfer_handles_embedded_marker_and_rejects_duplicate_frames():
    import hashlib
    import struct
    from chia_dma.observations import FRAME, decode_observation
    payload = FRAME
    header = struct.pack("<QQ", 2, 17)
    frame = FRAME + header + payload + b"\n"
    decoded = decode_observation(b"before\n" + frame + b"after\n")
    assert decoded.startswith("before\nafter\n")
    assert FRAME.decode() not in decoded
    assert hashlib.sha256(header + payload).hexdigest() in decoded
    with pytest.raises(ValueError, match="Duplicate"):
        decode_observation(frame + frame)


def test_run_retains_raw_output_until_hashing_then_removes_temporary_files(monkeypatch):
    import struct
    from chia_dma import observations
    folders = []
    class Runner:
        def run(self, artifact, content, filename, directory, **kwargs):
            assert kwargs["cleanup_task_dir"] is False
            assert kwargs["timeout_seconds"] == 30 * 60
            assert "timeout_cycles" not in kwargs
            root = Path(directory)
            folders.append(root)
            task = root / "abcdefgh"
            task.mkdir()
            (task / "case.log").write_bytes(observations.FRAME + struct.pack("<QQQ", 1, 0, 123) + b"\n")
            from chia.chipyard.state_def import RunResult
            return RunResult(filename, "lossy raw bytes", "", 0, True)
    monkeypatch.setattr(observations, "VerilatorRunNode", Runner)
    result = observations.run_observed(None, b"ELF", "case", {}, {})
    assert result.success and "MEMORY_OBSERVATION words=1" in result.log
    assert all(not path.exists() for path in folders)
