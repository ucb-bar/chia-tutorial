"""Exercise the shell handoff with real SQL/Git and mocked Docker/tunnel access."""

import json
import os
import shlex
import sqlite3
import subprocess
import sys

import pytest

from chia_dma.config_loader import ROOT, load
from chia_dma.source import seed_revisions, write_json


@pytest.fixture
def handoff(tmp_path, base_tree, patch_for):
    run = tmp_path / "tutorial"
    recipe = {
        "image": "ghcr.io/ucb-bar/chia-chisel-build@sha256:" + "a" * 64,
        "manifest": {"files": load()["base"]["files"]},
        "repositories": seed_revisions(base_tree),
    }
    write_json(run / "run.json", {"seed": "/instructor/missing-seed", "seed_recipe": recipe})
    with sqlite3.connect(run / "metrics.db") as db:
        db.executescript("""CREATE TABLE proposals
            (id TEXT, patch TEXT, metrics TEXT, sequence INTEGER);""")
        for sequence, (name, score, validity) in enumerate([
            ("inherited-best", 2.5, 1), ("attendee", 2.0, 1), ("invalid", 100, 0),
        ]):
            db.execute("INSERT INTO proposals VALUES(?,?,?,?)", (
                name, patch_for({"selected.txt": name + "\n"}),
                json.dumps({"combined_score": score, "validity": validity}), sequence,
            ))
        db.execute("INSERT INTO proposals VALUES('incomplete',NULL,?,9)",
                   ('{"combined_score":200,"validity":1}',))

    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    docker = binary / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''
import json, os, shutil, subprocess, sys
from pathlib import Path
root = Path(os.environ["FAKE_DOCKER"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "cp":
    shutil.copyfile(args[1], root / args[2].rsplit("/", 1)[1])
elif args[0] == "exec" and "-i" in args:
    if os.environ.get("FAIL_SETUP"):
        sys.exit(7)
    # Emulate the image's prepared source, then run the script's actual patch code.
    from chia_dma import source
    original_prepare = source.prepare_checkout
    def prepare(tree, commit):
        if source.seed_revisions(Path(os.environ["FAKE_IMAGE"]))[""] != commit:
            raise RuntimeError("Build image does not reproduce the run's baseline")
        shutil.copytree(os.environ["FAKE_IMAGE"], tree)
        original_prepare(tree, commit)
    source.prepare_checkout = prepare
    original_run = subprocess.run
    def run(command, **kwargs):
        if command[0] == "chown":
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, **kwargs)
    subprocess.run = run
    body = sys.stdin.read()
    for old, new in {
        "/home/ray/dma-build": str(root / "workspace"),
        "/home/ray/chipyard": os.environ["FAKE_IMAGE"],
        "/tmp/selection.json": str(root / "selection.json"),
        "/tmp/best.patch": str(root / "best.patch"),
    }.items():
        body = body.replace(old, new)
    exec(compile(body, "container-setup", "exec"))
elif args[0] == "exec" and "-it" in args:
    print("Mock tunnel connected")
''')
    docker.chmod(0o755)
    environment = {
        **os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "FAKE_DOCKER": str(tmp_path), "FAKE_IMAGE": str(base_tree),
    }

    def invoke(**extra):
        return subprocess.run(["bash", str(ROOT / "open-best.sh"), str(run)],
                              env={**environment, **extra}, capture_output=True, text=True)

    return run, tmp_path, recipe, invoke


def test_best_overall_is_applied_in_a_separate_container(handoff):
    run, temporary, recipe, invoke = handoff
    result = invoke()
    assert result.returncode == 0, result.stderr
    assert "inherited-best (score 2.5)" in result.stdout
    assert (temporary / "workspace/selected.txt").read_text() == "inherited-best\n"
    assert not (run / "selected.txt").exists()
    selection = json.loads((temporary / "selection.json").read_text())
    assert selection["id"] == "inherited-best"
    calls = [json.loads(line) for line in (temporary / "calls.jsonl").read_text().splitlines()]
    assert recipe["image"] in calls[0]
    assert all(str(run) not in value for value in calls[0])
    assert "--name" in calls[0]
    assert calls[-1][:2] == ["exec", "-it"]
    assert "vscode.dev/tunnel/" in calls[-1][-3]


@pytest.mark.parametrize("problem", [
    "no_results", "changed_base", "different_seed", "bad_patch", "setup_failure",
])
def test_handoff_stops_before_opening_a_tunnel_on_failure(handoff, problem):
    run, temporary, recipe, invoke = handoff
    if problem == "no_results":
        with sqlite3.connect(run / "metrics.db") as db:
            db.execute("UPDATE proposals SET metrics=NULL")
    elif problem == "changed_base":
        recipe["manifest"]["files"]["chipyard.patch"] = "different"
        write_json(run / "run.json", {"seed_recipe": recipe})
    elif problem == "different_seed":
        recipe["repositories"][""] = "0" * 40
        write_json(run / "run.json", {"seed_recipe": recipe})
    elif problem == "bad_patch":
        with sqlite3.connect(run / "metrics.db") as db:
            db.execute("UPDATE proposals SET patch='broken diff' WHERE id='inherited-best'")
    result = invoke(**({"FAIL_SETUP": "1"} if problem == "setup_failure" else {}))
    assert result.returncode != 0
    log = temporary / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert not any("-it" in args for args in calls)
    if problem in {"no_results", "changed_base"}:
        assert calls == []
