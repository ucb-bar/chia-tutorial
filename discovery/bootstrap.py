#!/usr/bin/env python3
"""Install the native head's Python integrations in discovery/.venv."""

from pathlib import Path
import subprocess
import sys
from chia_dma.config_loader import load

configuration = load()
git = ["git", "-c", "url.https://github.com/.insteadOf=git@github.com:"]
pip = [sys.executable, "-m", "pip", "install"]
for name in ("chia", "evolve_flows"):
    target = Path(configuration[name])
    if not (target / ".git").exists():
        subprocess.run(
            git + ["clone", configuration["dependencies"][name + "_url"], str(target)],
            check=True,
        )
    subprocess.run(
        git
        + [
            "-C",
            str(target),
            "checkout",
            configuration["dependencies"][name + "_revision"],
        ],
        check=True,
    )
subprocess.run(
    git + ["-C", str(target), "submodule", "update", "--init", "--recursive"],
    check=True,
)
subprocess.run(
    pip
    + ["-e", configuration["chia"], *configuration["dependencies"]["python_packages"]],
    check=True,
)
subprocess.run(
    pip + ["--no-deps", "-e", configuration["skydiscover"]],
    check=True,
)
print("Ready: prepare, chia up, then chia job submit from this host")
