"""Render a profile three ways, into one folder.

    python debug_render_profile.py out/profile_20261018_120000

Writes next to the log:
    ChiaProfileCollector.html   interactive timeline (open in a browser)
    ChiaProfileCollector.svg    dependency graph (needs graphviz)
    profile_table.csv           exec time aggregated per function

With no argument it takes the newest out/profile_* directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def newest_log() -> Path:
    dirs = sorted(OUT.glob("profile_*"), key=lambda p: p.stat().st_mtime)
    if not dirs:
        sys.exit(f"no profile_* directory under {OUT}; run a loop first")
    return dirs[-1] / "ChiaProfileCollector.log"


def main() -> None:
    arg = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else newest_log()
    log = arg / "ChiaProfileCollector.log" if arg.is_dir() else arg
    if not log.exists():
        sys.exit(f"no such log: {log}")

    for fmt in ("html", "svg"):
        subprocess.run([sys.executable, "-m", "chia.cli.main", "viz-profile",
                        str(log), "--format", fmt], check=True)
    subprocess.run([sys.executable, "-m", "chia.cli.main", "viz-profile",
                    str(log), "--format", "table",
                    "--output", str(log.parent / "profile_table.csv")], check=True)

    print(f"\nwrote into {log.parent}:")
    for p in sorted(log.parent.iterdir()):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
