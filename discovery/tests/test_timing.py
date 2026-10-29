"""Native RTL timeout, temporary-file cleanup, and elapsed-time reporting."""

import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest
from chia.chipyard.state_def import RunResult
from chia_dma import observations
from chia_dma.config_loader import load


def test_run_records_elapsed_time_and_removes_temporary_files(monkeypatch):
    folders = []
    class Runner:
        def run(self, artifact, content, filename, directory, **kwargs):
            folders.append(Path(directory))
            task = Path(directory) / "abcdefgh"
            task.mkdir()
            (task / "case.log").write_bytes(
                observations.FRAME + struct.pack("<QQ", 0, 0) + b"\n"
            )
            return RunResult(filename, "", "", 0, True)
    monkeypatch.setattr(observations, "VerilatorRunNode", Runner)
    timestamps = iter((100.0, 3700.0))
    monkeypatch.setattr(observations, "time", SimpleNamespace(monotonic=lambda: next(timestamps)))
    result = observations.run_observed(None, b"ELF", "case", {}, {})
    assert result.success and result.wall_seconds == 3600
    assert all(not folder.exists() for folder in folders)


def test_interrupted_rtl_removes_temporary_files(monkeypatch):
    folders = []
    class Runner:
        def run(self, artifact, content, filename, directory, **kwargs):
            folders.append(Path(directory))
            (Path(directory) / "partial.log").write_text("partial output")
            raise KeyboardInterrupt()
    monkeypatch.setattr(observations, "VerilatorRunNode", Runner)
    with pytest.raises(KeyboardInterrupt):
        observations.run_observed(None, b"ELF", "case", {}, {})
    assert all(not folder.exists() for folder in folders)


def test_search_can_exceed_30_minutes_and_still_reports_runtime(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import loop
    configuration = load()
    assert "exercise_timeout_seconds" not in configuration["runtime"]
    calls = []
    async def search(configuration, experiment, evaluator, seed_directory):
        calls.append(seed_directory)
        return SimpleNamespace(id="best")
    monkeypatch.setattr(loop, "run_search", search)
    monkeypatch.setattr(loop, "prepare", lambda *args: {"seed": "seed"})
    monkeypatch.setattr(loop, "RunLock", lambda *args: nullcontext())
    monkeypatch.setattr(loop, "Runtime", lambda *args: nullcontext())
    monkeypatch.setattr(loop, "Experiment", lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(loop, "DMAEvaluator", lambda *args: SimpleNamespace(close=lambda: None))
    timestamps = iter((100.0, 3700.0))
    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=lambda: next(timestamps)))
    loop.run_experiment(configuration, tmp_path, "run")
    timing = json.loads((tmp_path / "artifacts/exercise-timing.json").read_text())
    assert calls == ["seed"]
    assert timing == {"status": "complete", "wall_seconds": 3600.0}


def test_native_simulation_timeout_is_reported_as_a_failed_test(monkeypatch):
    from dataclasses import asdict
    from chia_dma.nodes import parse

    class Runner:
        def run(self, artifact, content, filename, directory, **kwargs):
            assert kwargs["timeout_seconds"] == 30 * 60
            assert "timeout_cycles" not in kwargs
            task = Path(directory) / "abcdefgh"
            task.mkdir()
            (task / "case.log").write_bytes(
                observations.FRAME + struct.pack("<QQ", 0, 0) + b"\n"
            )
            return RunResult(filename, "", "", -9, False)

    monkeypatch.setattr(observations, "VerilatorRunNode", Runner)
    result = observations.run_observed(None, b"ELF", "case", {}, {})
    assert not result.success and result.returncode == -9
    with pytest.raises(ValueError, match="Simulator failed"):
        parse._chia_original(asdict(result), 0)
