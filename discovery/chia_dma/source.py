"""Seed source, candidate Git patches, and saved proposals.

Workers import this module, so it must not import SkyDiscover.
"""

from pathlib import Path
import json
import os
import shutil
import subprocess
import uuid
from chia.base.ChiaFunction import get
from .config_loader import ROOT, digest


def load_proposal(sql, candidate_id, *, remote=False):
    query = sql.query_one
    args = (
        "SELECT patch,patch_hash,generation,backend,model FROM proposals WHERE id=?",
        (candidate_id,),
    )
    row = get(query.chia_remote(*args)) if remote else query(*args)
    if not row or row["patch"] is None:
        return None
    return {
        "id": candidate_id,
        **row,
        "generation": json.loads(row["generation"]),
    }


def save_proposal(sql, candidate_id, patch, generation, *, remote=False):
    """Save the proposal before generation returns; never replace it."""
    if not isinstance(patch, str):
        raise ValueError("Candidate must be a Git patch string")
    args = (
        "UPDATE proposals SET patch=?,patch_hash=?,generation=?,backend=?,model=? WHERE id=? AND patch IS NULL",
        (
            patch,
            digest(patch.encode()),
            json.dumps(generation),
            generation.get("backend"),
            generation.get("model"),
            candidate_id,
        ),
    )
    if remote:
        get(sql.execute.chia_remote(*args))
    else:
        sql.execute(*args)
    saved = load_proposal(sql, candidate_id, remote=remote)
    if saved is None:
        raise ValueError("Missing proposal request: " + candidate_id)
    return saved


def preserve_source(configuration):
    image = configuration["cluster"]["available_node_types"]["build"]["docker"]["image"]
    manifest = {"image": image, **configuration["base"]}
    seed_store = Path(configuration["storage"]) / "seeds"
    seed_store.mkdir(parents=True, exist_ok=True)
    seed_directory = seed_store / digest(manifest)
    if seed_directory.exists():
        return seed_directory

    capture_directory = seed_store / (".capture-" + uuid.uuid4().hex)
    capture_directory.mkdir()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            # Root reads the image's owner-only Chipyard files; the seed is then
            # handed back to the host user and made readable by worker containers.
            "--user",
            "root",
            "--mount",
            f"type=bind,src={ROOT / 'base'},dst=/base,readonly",
            "--mount",
            f"type=bind,src={capture_directory},dst=/seed",
            "--entrypoint",
            "bash",
            image,
            "-c",
            f"bash /base/prepare.sh "
            f"&& chown -R {os.getuid()}:{os.getgid()} /seed && chmod -R a+rX /seed",
        ],
        check=True,
    )
    write_json(capture_directory / "manifest.json", manifest)
    subprocess.run(["chmod", "-R", "a-w", str(capture_directory)], check=True)
    capture_directory.rename(seed_directory)
    return seed_directory


def run_git_command(tree, *arguments, input=None):
    """Run Git in a candidate checkout and keep its patch output unchanged."""
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(tree), *arguments],
        input=input, capture_output=True, text=True,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout


def capture_patch(tree, baseline):
    """Capture one Git patch, including new files and submodule edits."""
    tree, baseline = Path(tree), Path(baseline)
    repositories = [""] + run_git_command(
        baseline, "submodule", "foreach", "--quiet", "--recursive",
        'printf "%s\\n" "$displaypath"',
    ).splitlines()
    patches = []
    # Prefix submodule paths so every diff applies from the Chipyard root.
    for relative in repositories:
        original = run_git_command(baseline / relative, "rev-parse", "HEAD").strip()
        run_git_command(tree / relative, "add", "--all")
        prefix = relative + "/" if relative else ""
        patches.append(run_git_command(
            tree / relative, "diff", "--cached", "--binary", "--full-index",
            "--no-renames", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all",
            "--src-prefix=a/" + prefix, "--dst-prefix=b/" + prefix, original, "--",
        ))
    return "".join(patches)


def initialize_checkout(tree, baseline):
    """Prepare a writable checkout once, retaining the seed's Git metadata."""
    tree = Path(tree)
    if (tree / ".git").exists():
        return
    shutil.copytree(baseline, tree, symlinks=True, dirs_exist_ok=True)
    subprocess.run(["chmod", "-R", "u+w", str(tree)], check=True)


def apply_candidate_diff(tree, baseline, patch):
    """Reset the existing repositories to the seed and apply the candidate patch."""
    tree, baseline = Path(tree).resolve(), Path(baseline).resolve()
    repositories = [""] + run_git_command(
        baseline, "submodule", "foreach", "--quiet", "--recursive",
        'printf "%s\\n" "$displaypath"',
    ).splitlines()
    for relative in repositories:
        checkout = tree / relative
        original = run_git_command(baseline / relative, "rev-parse", "HEAD").strip()
        run_git_command(checkout, "reset", "--hard", original)
        run_git_command(checkout, "clean", "-ffdx")
    if patch:
        run_git_command(tree, "apply", "--whitespace=nowarn", input=patch)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None
