"""Run the accel baseline with the profiler on, then report what it cost.

Uses accel's own nodes and its own loop: it calls ``accel_loop.run_baseline()``
(the deterministic path — no LLM) and marks each phase with ``log_event``, the
one profiler call you can add from the driver without touching a node.

Everything else in the profile comes from the library nodes themselves, which
already call ``add_info``: ChiselBuildNode records the config and the verilator
thread count, VerilatorRunNode records simulation_cycles.

    chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/debug_timing.py"

Writes out/profile_timing_<ts>/ and prints a per-function timing table; render
the log with chia viz-profile.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import chia
import ray

from chia.trace.profiler import get_profiler, start_collector, stop_collector

import sys
from pathlib import Path

# accel/ is not a package and accel_loop.py puts it on the workers' path via
# runtime_env["working_dir"]. Do the same for this process so "import nodes",
# "import results" and "import accel_loop" resolve from here.
ACCEL = Path(__file__).resolve().parents[1] / "accel"
sys.path.insert(0, str(ACCEL))

import accel_loop
from results import OUT_DIR

HERE = Path(__file__).resolve().parent


def summarize(log: Path) -> None:
    """Per-function count and total exec time, from the profile the run wrote."""
    totals: dict[str, list[float]] = defaultdict(list)
    marks: list[tuple[str, float]] = []
    for line in log.read_text().splitlines():
        e = json.loads(line)
        if e.get("type") in ("complete", "local_end") and "exec_time_s" in e:
            totals[e.get("func", "?")].append(e["exec_time_s"])
        if e.get("type") == "phase":
            marks.append((e.get("name", "?"), e["ts"]))

    print(f"\n{'function':<28}{'calls':>7}{'total s':>12}{'max s':>10}")
    for func, times in sorted(totals.items(), key=lambda kv: -sum(kv[1])):
        print(f"{func:<28}{len(times):>7}{sum(times):>12.1f}{max(times):>10.1f}")

    print(f"\n{'phase':<28}{'wall s':>12}")
    for (name, ts), (_, nxt) in zip(marks, marks[1:]):
        print(f"{name:<28}{nxt - ts:>12.1f}")
    if marks:
        print(f"{'TOTAL':<28}{marks[-1][1] - marks[0][1]:>12.1f}")


def main() -> None:
    ray.init(address="auto", runtime_env={
        "working_dir": str(ACCEL),
        "py_modules": [list(chia.__path__)[0]],
        "excludes": ["out/", "__pycache__", ".mypy_cache"],
    })
    out = OUT_DIR / f"profile_timing_{time.strftime('%Y%m%d_%H%M%S')}"
    start_collector(log_dir=str(out))
    try:
        get_profiler().log_event("phase", name="baseline_start")
        summary = accel_loop.run_baseline()
        get_profiler().log_event("phase", name="baseline_done",
                                 status=summary.get("status"))
    finally:
        stop_collector()

    summarize(out / "ChiaProfileCollector.log")
    print(f"\nprofile: {out}")
    print(f"render it:  chia viz-profile {out}/ChiaProfileCollector.log --format html")


if __name__ == "__main__":
    main()
