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
        parse._chia_original(asdict(result))


def test_saved_unfinished_requests_do_not_abort_search_completion(monkeypatch):
    import asyncio
    import loop
    best = SimpleNamespace(id="best")
    async def discover(*args, **kwargs):
        return best
    controller = SimpleNamespace(run_discovery=discover, close=lambda: None)
    monkeypatch.setattr(loop, "create_search_controller", lambda *args: controller)
    experiment = SimpleNamespace(
        population=SimpleNamespace(programs={"seed": object()}, last_iteration=0),
        selections=lambda: [], recovery=[], pending_proposals=lambda: ["pending"],
        save_checkpoint=lambda: None,
    )
    assert asyncio.run(loop.run_search(load(), experiment, None, "seed")) is best


def test_cache_eviction_reexecutes_the_original_task(monkeypatch):
    from chia_dma import runtime, nodes
    cache = SimpleNamespace(read=SimpleNamespace(chia_remote=lambda tag: (False, None)))
    monkeypatch.setattr(runtime, "get_active_cache", lambda: cache)
    monkeypatch.setattr(runtime, "get", lambda value: value)
    calls = []
    def rebuild(value, *, output):
        calls.append((value, output))
        return "rebuilt artifact"
    monkeypatch.setattr(nodes, "build", rebuild)
    assert runtime.cache_provider("build")("tag", "unused", 1, output="elf") == "rebuilt artifact"
    assert calls == [(1, "elf")]


@pytest.mark.parametrize("generation_only", [False, True])
def test_workers_restore_bypass_state_without_skydiscover(tmp_path, generation_only):
    from ray import cloudpickle
    import os
    import subprocess
    import sys
    from chia_dma import runtime
    run = tmp_path / "run"
    runtime.render(load(), run, tmp_path / "seed")
    package = run / "deployment/generation-python/chia_dma"
    state = tmp_path / "provider.pkl"
    state.write_bytes(cloudpickle.dumps(runtime.cache_provider("build")))
    code = """
import importlib.abc
import importlib.util
import sys

class NoSkyDiscover(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "skydiscover" or fullname.startswith("skydiscover."):
            raise ModuleNotFoundError("SkyDiscover is not installed on workers")

sys.meta_path.insert(0, NoSkyDiscover())
from ray import cloudpickle
import chia_dma.agent
if sys.argv[2] == "generation":
    assert importlib.util.find_spec("chia_dma.nodes") is None
else:
    import chia_dma.nodes
    import chia_dma.correctness
provider = cloudpickle.loads(open(sys.argv[1], "rb").read())
assert callable(provider) and provider.__name__
assert "skydiscover" not in sys.modules
"""
    source = str(package.parent if generation_only else runtime.ROOT)
    role = "generation" if generation_only else "evaluation"
    subprocess.run([sys.executable, "-c", code, str(state), role], check=True, cwd=tmp_path,
                   env={**os.environ, "PYTHONPATH": source}, capture_output=True, text=True)
