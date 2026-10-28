"""The fixed image and bundled patch replace the host Chipyard checkout."""

from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from chia_dma.config import ALLOWLIST, ROOT, SOFTWARE_FILES, digest, frozen, load
from chia_dma.experiment import preserve_source, read_json, read_candidate_files


def test_build_recompiles_candidate_c_and_captures_its_elf(tmp_path, base_tree, monkeypatch):
    from types import SimpleNamespace
    from chia_dma import evaluation

    tree = tmp_path / "worker"
    seed = tmp_path / "seed"
    shutil.copytree(base_tree, tree)
    shutil.copytree(base_tree, seed / "tree")
    source = read_candidate_files(base_tree)
    configuration = load()
    configuration["software"]["build_command"] = [
        "make", "-s", "-C", "tests/memory-tests", "-j2", "host",
    ]
    configuration["software"]["elf"] = "tests/memory-tests/build/native/{benchmark}"
    monkeypatch.setattr(evaluation, "Path", lambda p: tree if p == "/home/ray/dma-build" else Path(p))
    simulator = SimpleNamespace(success=True)
    monkeypatch.setattr(evaluation, "ChiselBuildNode",
                        lambda *args, **kwargs: SimpleNamespace(build=lambda: simulator))
    original = evaluation.build._chia_original(configuration, seed, source)
    changed = dict(source)
    changed[SOFTWARE_FILES[0]] = "void initialise_benchmark(void) {}\nvoid benchmark(void) {}\n"
    candidate = evaluation.build._chia_original(configuration, seed, changed)
    assert original["software"]["copy-small"] != candidate["software"]["copy-small"]
    for result, expected_exit in ((original, 0), (candidate, 1)):
        executable = tmp_path / "captured-test"
        executable.write_bytes(result["software"]["copy-small"])
        executable.chmod(0o755)
        assert subprocess.run([str(executable)], capture_output=True).returncode == expected_exit
    checksum = "tests/memory-tests/src/copy-small/checksum.c"
    assert (tree / checksum).read_bytes() == (base_tree / checksum).read_bytes()
    changed[SOFTWARE_FILES[0]] = "#error invalid candidate C\n"
    failed = evaluation.build._chia_original(configuration, seed, changed)
    assert failed["simulator"] is None and "invalid candidate C" in failed["software_log"]


def test_generation_mounts_all_candidate_files_but_not_checksum_readers(tmp_path, base_tree):
    from chia_dma.runtime import render

    seed = tmp_path / "seed"
    shutil.copytree(base_tree, seed / "tree")
    run = tmp_path / "run"
    run.mkdir()
    deployment = yaml.safe_load(render(load(), run, seed).read_text())
    for index in (0, 1):
        options = deployment["available_node_types"][f"generation-{index}"]["docker"]["run_options"]
        writable = [option for option in options if f"src={run}/work/" in option]
        assert len(writable) == 8
        assert all(any(f"dst=/workspace/{name}'" in option for option in writable) for name in ALLOWLIST)
        assert not any("checksum.c" in option for option in writable)


def test_c_sources_and_evaluator_change_the_frozen_contract(tmp_path, monkeypatch):
    from chia_dma import config

    shutil.copytree(ROOT / "base", tmp_path / "base",
                    ignore=shutil.ignore_patterns("build", "__pycache__"))
    (tmp_path / "chia_dma").mkdir()
    shutil.copyfile(ROOT / "chia_dma/evaluation.py", tmp_path / "chia_dma/evaluation.py")
    for name in ("backend.yaml", "config.yaml"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    before = frozen(config.load(tmp_path / "config.yaml"))
    source = tmp_path / "base/memory-tests/src/copy-small/copy-small.c"
    source.write_text(source.read_text() + "\n/* changed workload */\n")
    after = frozen(config.load(tmp_path / "config.yaml"))
    assert digest(before) != digest(after)
    evaluator = tmp_path / "chia_dma/evaluation.py"
    evaluator.write_text(evaluator.read_text() + "\n# changed verifier\n")
    assert digest(after) != digest(frozen(config.load(tmp_path / "config.yaml")))


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
    assert len(list((seed / "tree/tests/memory-tests/src").iterdir())) == 6
    assert not (seed / "tree/tests/embench-iot").exists()
    restored = tmp_path / "restored"
    shutil.copytree(seed / "tree", restored)
    parent = read_candidate_files(seed / "tree")
    candidate = {name: text + "// candidate\n" for name, text in parent.items()}
    for name, text in candidate.items():
        (restored / name).chmod(0o644)
        (restored / name).write_text(text)
    assert read_candidate_files(restored) == candidate
    test = "tests/memory-tests/src/copy-small/checksum.c"
    assert (restored / test).read_bytes() == (base_tree / test).read_bytes()


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
            ".deps", ".venv", "build", "experiments", "__pycache__", ".pytest_cache", ".git"
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
