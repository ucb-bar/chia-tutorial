"""Bootstrap reuses installed dependencies without resetting local source edits."""

from pathlib import Path
import runpy
import subprocess

from chia_dma import config_loader


def test_existing_dependency_checkouts_are_reused(tmp_path, monkeypatch):
    configuration = config_loader.load()
    for name in ("chia", "evolve_flows"):
        checkout = tmp_path / name
        (checkout / ".git").mkdir(parents=True)
        (checkout / "local-edit.txt").write_text("keep this local change")
        configuration[name] = str(checkout)
    skydiscover = Path(configuration["evolve_flows"]) / "skydiscover"
    (skydiscover / ".git").mkdir(parents=True)
    configuration["skydiscover"] = str(skydiscover)
    monkeypatch.setattr(config_loader, "load", lambda: configuration)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    runpy.run_path(str(config_loader.ROOT / "bootstrap.py"), run_name="__main__")
    assert not any(command[0] == "git" for command in commands)
    assert all((tmp_path / name / "local-edit.txt").read_text() == "keep this local change"
               for name in ("chia", "evolve_flows"))
