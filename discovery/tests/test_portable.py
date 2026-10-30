"""Portable forks retain search history while rebuilding source outside the archive."""

import copy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from chia_dma.config_loader import digest, load
from chia_dma.experiment import fork
from chia_dma.runtime import prepare
from chia_dma.score_viz import load_scores, render_scores
from chia_dma.source import (
    apply_candidate_diff,
    capture_patch,
    initialize_checkout,
    preserve_source,
    read_json,
    seed_revisions,
    write_json,
)
from conftest import commit_tree


IMAGE = "ghcr.io/ucb-bar/chia-chisel-build@sha256:" + "a" * 64


def image_capture(monkeypatch, original, latest=None):
    """Emulate Docker materializing source; all Git and archive operations are real."""
    calls = []
    run = subprocess.run

    def capture(command, **kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([IMAGE]))
        if command[:2] != ["docker", "run"]:
            return run(command, **kwargs)
        mount = next(value for value in command if value.startswith("type=bind,src=") and value.endswith("dst=/seed"))
        destination = Path(mount.split(",")[1].removeprefix("src="))
        image = command[command.index("bash") + 1]
        calls.append(image)
        source = original if image == IMAGE or latest is None else latest
        shutil.copytree(source, destination / "tree")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("chia_dma.source.subprocess.run", capture)
    return calls


def test_portable_tar_retains_history_and_restores_root_and_submodule_patches(
    tmp_path, base_tree, patch_for, monkeypatch,
):
    image = tmp_path / "image"
    shutil.copytree(base_tree, image)
    component = tmp_path / "component"
    component.mkdir()
    (component / "helper.c").write_text("int value = 1;\n")
    commit_tree(component)
    subprocess.run([
        "git", "-c", "protocol.file.allow=always", "-C", str(image),
        "submodule", "add", "--quiet", str(component), "generators/component",
    ], check=True)
    commit_tree(image)
    new_image = tmp_path / "new-image"
    shutil.copytree(image, new_image)
    (new_image / "new-version.txt").write_text("A different latest image\n")
    commit_tree(new_image)
    calls = image_capture(monkeypatch, image)
    configuration = load()
    # cluster.yaml pins a digest; this test covers recording one for a moving tag.
    configuration["cluster"]["available_node_types"]["build"]["docker"]["image"] = (
        "ghcr.io/ucb-bar/chia-chisel-build:latest"
    )
    instructor = tmp_path / "instructor"
    configuration["storage"] = str(instructor / "experiments")
    seed = preserve_source(configuration)
    origin = instructor / "experiments/original"
    write_json(origin / "run.json", {"run_id": "original", "seed": str(seed)})
    patches = {
        "seed-dma": "",
        "candidate-1": patch_for({"root-helper.c": "int root = 1;\n"}, baseline=image),
        "candidate-2": patch_for({"generators/component/helper.c": "int value = 2;\n"}, baseline=image),
    }
    state = {
        "admitted": list(patches),
        "requests": {name: {"generated": True, "evaluated": True} for name in patches},
    }
    checkpoint = origin / "checkpoints/00000002-test"
    write_json(checkpoint / "state.json", state)
    write_json(checkpoint / "population/programs/candidate-2.json", {"patch": patches["candidate-2"]})
    write_json(origin / "latest.json", {"checkpoint": checkpoint.name})
    with sqlite3.connect(origin / "metrics.db") as db:
        db.executescript("""CREATE TABLE proposals
            (id TEXT PRIMARY KEY,patch TEXT,generation TEXT,metrics TEXT,program_id TEXT,
             request TEXT,backend TEXT,model TEXT);
            CREATE TABLE benchmarks(candidate TEXT,name TEXT,metrics TEXT);""")
        for name, patch in {**patches, "future-candidate": "later patch"}.items():
            db.execute("INSERT INTO proposals(id,patch,generation,metrics,program_id) VALUES(?,?,?,?,?)",
                       (name, patch, json.dumps({"response": name}), '{"validity":1}', name))
    profile = origin / "artifacts/profile/ChiaProfileCollector.log"
    profile.parent.mkdir(parents=True)
    events = [{"type": "dispatch", "func": "build", "ts": 100}]
    for index, name in enumerate([*patches, "future-candidate"]):
        events.extend([
            {"type": "local_start", "func": "score_point", "call_id": str(index), "ts": 110 + index},
            {"type": "local_end", "func": "score_point", "call_id": str(index), "ts": 110.1 + index,
             "extra": {"metric_series": "candidates", "candidate_id": name,
                       "combined_score": 1 + index / 10, "validity": 1}},
        ])
    profile.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    (origin / "artifacts/build.log").write_text("Build logs are not distributed")
    fork(origin, None, instructor / "experiments/tutorial", candidate="candidate-2")
    meta = read_json(instructor / "experiments/tutorial/run.json")
    assert meta["seed_recipe"]["image"] == IMAGE
    assert "generators/component" in meta["seed_recipe"]["repositories"]
    archive = tmp_path / "tutorial.tar.gz"
    subprocess.run(["tar", "-czf", str(archive), "experiments/tutorial"], cwd=instructor, check=True)
    files = subprocess.check_output(["tar", "-tzf", str(archive)], text=True).splitlines()
    assert all(name.startswith("experiments/tutorial") for name in files)
    assert not any("/seeds/" in name or "/.git/" in name for name in files)
    assert any(name.endswith("artifacts/profile/ChiaProfileCollector.log") for name in files)
    assert not any(name.endswith("artifacts/build.log") for name in files)

    # The instructor's filesystem is unavailable to the attendee.
    subprocess.run(["chmod", "-R", "u+w", str(seed)], check=True)
    shutil.rmtree(seed)
    attendee = tmp_path / "attendee"
    attendee.mkdir()
    subprocess.run(["tar", "-xzf", str(archive)], cwd=attendee, check=True)
    # A fresh attendee can plot the checkpoint before rebuilding its seed or resuming.
    run = attendee / "experiments/tutorial"
    scores = load_scores(run / "artifacts/profile/ChiaProfileCollector.log")
    assert {point["candidate_id"] for point in scores["points"]} == set(patches)
    assert render_scores(run).exists()
    configuration["storage"] = str(attendee / "experiments")
    calls = image_capture(monkeypatch, image, latest=new_image)
    different_seed = preserve_source(configuration)
    run = attendee / "experiments/tutorial"
    restored = prepare(configuration, run)
    rebuilt = Path(restored["seed"])
    assert rebuilt != different_seed and different_seed.exists()
    assert calls == [configuration["cluster"]["available_node_types"]["build"]["docker"]["image"], IMAGE]
    assert seed_revisions(rebuilt / "tree") == meta["seed_recipe"]["repositories"]
    assert prepare(configuration, run)["seed"] == str(rebuilt)
    assert len(calls) == 2  # Reuse the reconstructed seed on later preparation.
    with sqlite3.connect(run / "metrics.db") as db:
        saved = db.execute("SELECT id,patch,generation FROM proposals ORDER BY id").fetchall()
    assert {name: patch for name, patch, _ in saved} == patches
    assert {json.loads(generation)["response"] for _, _, generation in saved} == set(patches)
    for name, patch, _ in saved:
        work = tmp_path / ("restored-" + name)
        initialize_checkout(work, rebuilt / "tree")
        apply_candidate_diff(work, rebuilt / "tree", patch)
        assert capture_patch(work, rebuilt / "tree") == patch
    assert read_json(run / "checkpoints/00000002-test/state.json") == state


@pytest.mark.parametrize("problem", ["base_files", "image", "cache"])
def test_portable_restore_rejects_a_different_baseline(tmp_path, base_tree, monkeypatch, problem):
    configuration = load()
    configuration["storage"] = str(tmp_path / "experiments")
    recipe = {
        "image": IMAGE,
        "manifest": {"image": "example:latest", **copy.deepcopy(configuration["base"])},
        "repositories": seed_revisions(base_tree),
    }
    different = tmp_path / "different"
    shutil.copytree(base_tree, different)
    (different / "different.txt").write_text("Different source\n")
    commit_tree(different)
    calls = image_capture(monkeypatch, different)
    if problem == "base_files":
        configuration["base"]["files"]["chipyard.patch"] = "changed"
    elif problem == "cache":
        shutil.copytree(different, Path(configuration["storage"]) / "seeds" / digest(recipe) / "tree")
    with pytest.raises(ValueError, match="base/ files|differs from the checkpoint baseline"):
        preserve_source(configuration, recipe=recipe)
    assert len(calls) == int(problem == "image")
