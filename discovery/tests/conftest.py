"""Tutorial inputs come from the bundled patch, never the enclosing checkout."""

import subprocess
import pytest
from chia_dma.config import ROOT


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
    return tree
