"""The fixed image and bundled patch replace the host Chipyard checkout."""

from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from chia_dma.config import ALLOWLIST, ROOT, digest, load
from chia_dma.experiment import preserve_source, read_json, read_accelerator_files


def test_image_seed_is_immutable_reusable_and_restores_candidates(
    tmp_path, base_tree, monkeypatch
):
    configuration = load()
    configuration["storage"] = str(tmp_path / "storage")
    assert "source" not in configuration
    calls = []
    run_command = subprocess.run

    def materialize_image(command, check):
        if command[0] != "docker":
            return run_command(command, check=check)
        calls.append(command)
        mount = next(
            value
            for value in command
            if value.startswith("type=bind,src=") and value.endswith("dst=/seed")
        )
        destination = Path(mount.split(",")[1].removeprefix("src="))
        shutil.copytree(base_tree, destination / "tree")

    monkeypatch.setattr("chia_dma.experiment.subprocess.run", materialize_image)
    seed = preserve_source(configuration)
    manifest = read_json(seed / "manifest.json")
    assert (
        manifest["image"]
        == configuration["cluster"]["available_node_types"]["build"]["docker"]["image"]
    )
    assert seed.name == digest(manifest)
    assert preserve_source(configuration) == seed and len(calls) == 1
    assert not (seed / "tree" / ALLOWLIST[0]).stat().st_mode & 0o222
    assert len(list((seed / "tree/tests/embench-iot/src").iterdir())) == 19
    restored = tmp_path / "restored"
    shutil.copytree(seed / "tree", restored)
    parent = read_accelerator_files(seed / "tree")
    candidate = {name: text + "// candidate\n" for name, text in parent.items()}
    for name, text in candidate.items():
        (restored / name).chmod(0o644)
        (restored / name).write_text(text)
    assert read_accelerator_files(restored) == candidate
    parser = configuration["software"]["metrics_parser"]
    assert (restored / parser).read_bytes() == (base_tree / parser).read_bytes()


@pytest.mark.parametrize("repository", ["none", "unborn", "committed"])
@pytest.mark.parametrize("bundled", [False, True])
def test_copied_loop_prepares_and_relocates_without_requiring_a_commit(
    tmp_path, base_tree, monkeypatch, repository, bundled
):
    from chia_dma import config, runtime

    copied = tmp_path / "loop"
    shutil.copytree(
        ROOT,
        copied,
        ignore=shutil.ignore_patterns(
            ".deps", ".venv", "experiments", "__pycache__", ".pytest_cache", ".git"
        ),
    )
    if repository != "none":
        subprocess.run(["git", "init", "--quiet", str(copied)], check=True)
    if repository == "committed":
        subprocess.run(["git", "-C", str(copied), "add", "config.yaml"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(copied),
                "-c",
                "user.name=Tutorial Test",
                "-c",
                "user.email=tutorial@example.test",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "Initial tutorial config",
            ],
            check=True,
        )
    seed = copied / "experiments/seeds/frozen-seed" if bundled else tmp_path / "seed"
    shutil.copytree(base_tree, seed / "tree")
    monkeypatch.setattr(config, "ROOT", copied)
    monkeypatch.setattr(runtime, "ROOT", copied)
    monkeypatch.setattr(runtime, "preserve_source", lambda configuration: seed)
    configuration = config.load(copied / "config.yaml")
    run = copied / "experiments/test"

    meta = runtime.prepare(configuration, run)

    assert bool(meta["loop_revision"]) == (repository == "committed")
    assert meta["loop_dirty"] is (None if repository == "none" else True)
    assert runtime.prepare(configuration, run) == meta
    deployment = yaml.safe_load((run / "deployment/cluster.yaml").read_text())
    assert deployment["head_env_commands"][0] == f"source {copied}/.venv/bin/activate"

    relocated = tmp_path / "relocated-loop"
    shutil.copytree(copied, relocated, symlinks=True)
    monkeypatch.setattr(config, "ROOT", relocated)
    monkeypatch.setattr(runtime, "ROOT", relocated)
    configuration = config.load(relocated / "config.yaml")
    run = relocated / "experiments/test"
    restored = runtime.prepare(configuration, run)
    expected = relocated / "experiments/seeds/frozen-seed" if bundled else seed
    assert restored["seed"] == str(expected)
    assert read_json(run / "run.json") == meta
    assert str(copied) not in (run / "deployment/cluster.yaml").read_text()
    shutil.rmtree(copied)
    assert runtime.prepare(configuration, run) == restored
