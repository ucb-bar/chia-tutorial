"""The fixed image and bundled patch replace the host Chipyard checkout."""

from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from chia_dma.config_loader import HARDWARE_FILES, ROOT, SOFTWARE_FILES, digest, load
from chia_dma.source import preserve_source, read_json, capture_patch

EXAMPLE_FILES = HARDWARE_FILES + SOFTWARE_FILES


def test_native_riscv_builder_compiles_candidate_and_keeps_the_oracle_fixed(tmp_path, base_tree, patch_for):
    from chia.chipyard.riscv_build_node import RiscvBuildNode
    from chia_dma.evaluation import software_inputs

    elf = "tests/memory-tests/build/native/copy-small"
    def compile_candidate(changes):
        return RiscvBuildNode().build_program(
            software_inputs(base_tree, patch_for(changes)),
            ["make", "-s", "-C", "tests/memory-tests", "host", "NAMES=copy-small"],
            str(tmp_path / "build"), outputs=[elf],
        )
    original = compile_candidate({})
    assert original.success
    changed = {}
    changed["common/helper.h"] = "#define TUTORIAL_HELPER 1\n"
    changed[SOFTWARE_FILES[0]] = (
        '#include "../../../../common/helper.h"\n'
        "void initialise_benchmark(void) {}\nvoid benchmark(void) {}\n"
    )
    candidate = compile_candidate(changed)
    assert candidate.success and original.files[elf] != candidate.files[elf]
    executable = tmp_path / "captured-test"
    for result, expected_exit in ((original, 0), (candidate, 1)):
        executable.write_bytes(result.files[elf])
        executable.chmod(0o755)
        assert subprocess.run([str(executable)], capture_output=True).returncode == expected_exit
    checksum = "tests/memory-tests/src/copy-small/checksum.c"
    changed[checksum] = "#error replaced checksum oracle\n"
    changed["tests/memory-tests/support/main.c"] = "#error replaced measurements\n"
    changed["tests/memory-tests/Makefile"] = "$(error replaced build recipe)\n"
    changed["software/rocc-dma/boardsupport.c"] = "#error replaced measurement runtime\n"
    rejected = compile_candidate(changed)
    assert rejected.success
    executable.write_bytes(rejected.files[elf])
    assert subprocess.run([str(executable)], capture_output=True).returncode == 1
    inputs = software_inputs(base_tree, patch_for(changed))
    assert inputs[checksum] == (base_tree / checksum).read_bytes()
    runtime = "software/rocc-dma/boardsupport.c"
    assert inputs[runtime] == (base_tree / runtime).read_bytes()
    changed[SOFTWARE_FILES[0]] = "#error invalid candidate C\n"
    failed = compile_candidate(changed)
    assert not failed.success and "invalid candidate C" in failed.stderr


def test_unrestricted_source_changes_round_trip_and_clear_previous_parent(tmp_path):
    from chia_dma.source import initialize_checkout, apply_candidate_diff

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "build.sbt").write_text("baseline")
    (seed / "remove.c").write_text("remove me")
    (seed / "binary").write_bytes(b"\xff\x00")
    from conftest import commit_tree
    commit_tree(seed)
    work = tmp_path / "work"
    initialize_checkout(work, seed)
    apply_candidate_diff(work, seed, "")
    assert capture_patch(work, seed) == ""
    (work / "build.sbt").write_text("new build configuration")
    (work / "remove.c").unlink()
    (work / "new").mkdir()
    (work / "new/helper.c").write_text("int helper(void) { return 1; }")
    (work / "binary").write_bytes(b"\x00\xfe")
    (work / "binary").chmod(0o755)
    (work / "helper-link").symlink_to("new/helper.c")
    commit_tree(work)  # An agent commit must not become the next candidate's baseline.
    candidate = capture_patch(work, seed)
    assert "deleted file mode" in candidate
    assert "GIT binary patch" in candidate
    assert "new file mode 120000" in candidate
    restored = tmp_path / "restored"
    initialize_checkout(restored, seed)
    apply_candidate_diff(restored, seed, candidate)
    assert capture_patch(restored, seed) == candidate
    assert (restored / "binary").read_bytes() == b"\x00\xfe"
    assert (restored / "helper-link").read_text().startswith("int helper")
    metadata = restored / ".git/keep-this-metadata"
    metadata.write_text("Preserve the existing repository")
    initialize_checkout(restored, seed)
    assert capture_patch(restored, seed) == candidate
    apply_candidate_diff(restored, seed, "")
    assert metadata.read_text() == "Preserve the existing repository"
    assert capture_patch(restored, seed) == ""
    apply_candidate_diff(work, seed, "")
    assert capture_patch(work, seed) == ""
    assert not (restored / "new/helper.c").exists()
    assert (seed / "build.sbt").read_text() == "baseline"


def test_only_verification_mounts_the_hidden_correctness_tests():
    deployment = yaml.safe_load((ROOT / "cluster.yaml").read_text())
    workers = deployment["available_node_types"]
    for name, worker in workers.items():
        options = " ".join(worker["docker"].get("run_options", []))
        assert ("/private" in options) == (name == "verification")
    generation = " ".join(workers["generation"]["docker"]["run_options"])
    assert "experiments/seeds,readonly" in generation
    assert "${HOME}/.gemini:/tmp/gemini-login:ro" in generation
    assert "dst=/home/ray/workspace" not in generation
    # Ray skips these when uploading the working directory to workers.
    assert "private/" in (ROOT / ".rayignore").read_text().split()
    assert "experiments/" in (ROOT / ".gitignore").read_text().split()
    assert not (ROOT / "chia_dma/correctness.py").exists()


def test_generation_worker_setup_recreates_independent_checkouts(tmp_path, base_tree, patch_for):
    from chia_dma.source import apply_candidate_diff, initialize_checkout

    workspaces = [tmp_path / name / "home/ray/workspace" for name in ("container-a", "container-b")]

    def setup(workspace):
        # The first generation task in a container creates its checkout.
        initialize_checkout(workspace, base_tree)

    filename = SOFTWARE_FILES[0]
    for workspace, text in zip(workspaces, ("parent A", "parent B")):
        setup(workspace)
        patch = patch_for({filename: text})
        apply_candidate_diff(workspace, base_tree, patch)
    assert [(workspace / filename).read_text() for workspace in workspaces] == ["parent A", "parent B"]

    edited = workspaces[0]
    (edited / filename).write_text("candidate A")
    (edited / "new-source.c").write_text("candidate addition")
    patch = capture_patch(edited, base_tree)
    setup(edited)  # Reusing a running container preserves its checkout until parent reset.
    assert (edited / filename).read_text() == "candidate A"
    shutil.rmtree(edited)  # A replacement container has no previous workspace.
    setup(edited)
    apply_candidate_diff(edited, base_tree, patch)
    assert (edited / filename).read_text() == "candidate A"
    assert (edited / "new-source.c").read_text() == "candidate addition"
    assert (workspaces[1] / filename).read_text() == "parent B"
    assert (base_tree / filename).read_bytes() == (ROOT / "base/memory-tests/src/copy-small/copy-small.c").read_bytes()


def test_resume_ignores_old_fingerprints_and_reuses_the_saved_seed(tmp_path, monkeypatch):
    from chia_dma import config_loader as config, runtime
    from chia_dma.source import write_json

    shutil.copytree(ROOT / "base", tmp_path / "base",
                    ignore=shutil.ignore_patterns("build", "__pycache__"))
    shutil.copyfile(ROOT / "cluster.yaml", tmp_path / "cluster.yaml")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    initial = config.load(tmp_path / "cluster.yaml")
    source = tmp_path / "base/memory-tests/src/copy-small/copy-small.c"
    source.write_text(source.read_text() + "\n/* changed workload */\n")
    (tmp_path / "chia_dma").mkdir()
    (tmp_path / "chia_dma/nodes.py").write_text('# A harmless evaluator comment\n')
    monkeypatch.setattr(config.os, "sched_getaffinity", lambda pid: {0, 1})
    changed = config.load(tmp_path / "cluster.yaml")
    changed["search"]["random_seed"] += 1
    assert changed["base"]["files"] != initial["base"]["files"]
    assert changed["hardware"]["make_jobs"] == 2

    run = tmp_path / "runs/resumed"
    run.mkdir(parents=True)
    seed = run.parent / "seeds/existing"
    (seed / "tree").mkdir(parents=True)
    write_json(run / "run.json", {"seed": "/old/machine/seeds/existing", "frozen_hash": "old-hash"})
    monkeypatch.setattr(runtime, "preserve_source", lambda *args: pytest.fail("Do not replace a saved seed"))
    meta = runtime.prepare(changed, run)
    assert meta["seed"] == str(seed)
    (seed / "tree").rmdir()
    with pytest.raises(FileNotFoundError, match="Preserved Chipyard seed is missing"):
        runtime.prepare(changed, run)


def test_image_seed_is_immutable_reusable_and_restores_candidates(
    tmp_path, base_tree, monkeypatch
):
    configuration = load()
    configuration["storage"] = str(tmp_path / "storage")
    assert "source" not in configuration
    calls = []
    run_command = subprocess.run

    def materialize_image(command, **kwargs):
        if command[0] != "docker":
            return run_command(command, **kwargs)
        calls.append(command)
        mount = next(
            value
            for value in command
            if value.startswith("type=bind,src=") and value.endswith("dst=/seed")
        )
        destination = Path(mount.split(",")[1].removeprefix("src="))
        shutil.copytree(base_tree, destination / "tree")

    monkeypatch.setattr("chia_dma.source.subprocess.run", materialize_image)
    seed = preserve_source(configuration)
    manifest = read_json(seed / "manifest.json")
    assert (
        manifest["image"]
        == configuration["cluster"]["available_node_types"]["build"]["docker"]["image"]
    )
    assert seed.name == digest(manifest)
    assert preserve_source(configuration) == seed and len(calls) == 1
    assert not (seed / "tree" / EXAMPLE_FILES[0]).stat().st_mode & 0o222
    assert len(list((seed / "tree/tests/memory-tests/src").iterdir())) == 6
    assert not (seed / "tree/tests/embench-iot").exists()
    from chia_dma.source import initialize_checkout, apply_candidate_diff
    restored = tmp_path / "restored"
    initialize_checkout(restored, seed / "tree")
    apply_candidate_diff(restored, seed / "tree", "")
    assert (restored / ".git").is_dir()
    assert subprocess.check_output(["git", "-C", str(restored), "rev-parse", "HEAD"])
    for name in EXAMPLE_FILES:
        path = restored / name
        path.write_text(path.read_text() + "// candidate\n")
    candidate = capture_patch(restored, seed / "tree")
    replayed = tmp_path / "replayed"
    initialize_checkout(replayed, seed / "tree")
    apply_candidate_diff(replayed, seed / "tree", candidate)
    assert capture_patch(replayed, seed / "tree") == candidate
    test = "tests/memory-tests/src/copy-small/checksum.c"
    assert (replayed / test).read_bytes() == (base_tree / test).read_bytes()


@pytest.mark.parametrize("repository", ["none", "unborn", "committed"])
@pytest.mark.parametrize("bundled", [False, True])
def test_copied_loop_prepares_and_relocates_without_requiring_a_commit(
    tmp_path, base_tree, monkeypatch, repository, bundled
):
    from chia_dma import config_loader as config, runtime

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
        subprocess.run(["git", "-C", str(copied), "add", "cluster.yaml"], check=True)
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
    configuration = config.load(copied / "cluster.yaml")
    run = copied / "experiments/test"

    meta = runtime.prepare(configuration, run)

    assert bool(meta["loop_revision"]) == (repository == "committed")
    assert meta["loop_dirty"] is (None if repository == "none" else True)
    assert runtime.prepare(configuration, run) == meta

    relocated = tmp_path / "relocated-loop"
    shutil.copytree(copied, relocated, symlinks=True)
    monkeypatch.setattr(config, "ROOT", relocated)
    monkeypatch.setattr(runtime, "ROOT", relocated)
    configuration = config.load(relocated / "cluster.yaml")
    run = relocated / "experiments/test"
    restored = runtime.prepare(configuration, run)
    expected = relocated / "experiments/seeds/frozen-seed" if bundled else seed
    assert restored["seed"] == str(expected)
    assert read_json(run / "run.json") == meta
    shutil.rmtree(copied)
    assert runtime.prepare(configuration, run) == restored


def test_patch_round_trip_preserves_submodule_metadata_and_edits(tmp_path):
    from conftest import commit_tree
    from chia_dma.source import initialize_checkout, apply_candidate_diff, capture_patch
    component = tmp_path / "component"
    component.mkdir()
    (component / "helper.c").write_text("int value = 1;\n")
    commit_tree(component)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "build.sbt").write_text("baseline\n")
    commit_tree(baseline)
    subprocess.run([
        "git", "-c", "protocol.file.allow=always", "-C", str(baseline),
        "submodule", "add", "--quiet", str(component), "generators/component",
    ], check=True)
    commit_tree(baseline)
    work = tmp_path / "work"
    initialize_checkout(work, baseline)
    apply_candidate_diff(work, baseline, "")
    (work / "generators/component/helper.c").write_text("int value = 2;\n")
    (work / "generators/component/new.h").write_text("#define NEW 1\n")
    patch = capture_patch(work, baseline)
    assert "diff --git a/generators/component/helper.c" in patch
    assert "diff --git a/generators/component/new.h" in patch
    restored = tmp_path / "restored"
    initialize_checkout(restored, baseline)
    apply_candidate_diff(restored, baseline, patch)
    assert (restored / "generators/component/.git").exists()
    assert (restored / "generators/component/helper.c").read_text() == "int value = 2;\n"
    assert (restored / "generators/component/new.h").read_text() == "#define NEW 1\n"
    assert capture_patch(restored, baseline) == patch

    commit_tree(restored / "generators/component")
    apply_candidate_diff(restored, baseline, "")
    assert (restored / "generators/component/helper.c").read_text() == "int value = 1;\n"
    assert not (restored / "generators/component/new.h").exists()
    assert capture_patch(restored, baseline) == ""
