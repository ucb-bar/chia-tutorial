"""Tutorial inputs come from the bundled patch, never the enclosing checkout."""

import shutil
import subprocess
import pytest
from chia_dma.config_loader import ROOT


@pytest.fixture(scope="session")
def base_tree(tmp_path_factory):
    tree = tmp_path_factory.mktemp("base-source")
    subprocess.run(
        [
            "git",
            "apply",
            "--exclude=sims/verilator/Makefile",
            str(ROOT / "base/chipyard.patch"),
        ],
        cwd=tree,
        check=True,
    )
    shutil.copytree(
        ROOT / "base/memory-tests", tree / "tests/memory-tests",
        ignore=shutil.ignore_patterns("build", "__pycache__"),
    )
    commit_tree(tree)
    return tree


def commit_tree(tree):
    """Give small test fixtures the same Git baseline as the build image."""
    subprocess.run(["git", "init", "--quiet", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "--all"], check=True)
    subprocess.run([
        "git", "-C", str(tree), "-c", "user.name=Tutorial Test",
        "-c", "user.email=tutorial@example.test", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "--allow-empty", "-m", "Tutorial baseline",
    ], check=True)


@pytest.fixture
def patch_for(tmp_path, base_tree):
    """Make a real Git patch from convenient test source edits."""
    import uuid
    from chia_dma.experiment import initialize_checkout, apply_candidate_diff, capture_patch

    def create(changes, baseline=base_tree, parent=""):
        work = tmp_path / ("patch-" + uuid.uuid4().hex)
        initialize_checkout(work, baseline)
        apply_candidate_diff(work, baseline, parent)
        for name, content in changes.items():
            path = work / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    path.write_bytes(content)
                else:
                    path.write_text(content)
        return capture_patch(work, baseline)

    return create
