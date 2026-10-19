"""Run accel's own loop with a CHIA cache attached.

No new loop and no wrappers: this calls accel_loop.run_baseline() and
accel_loop.run_iteration(), the same two calls their main() makes. The only
additions are the four lines of cache/bypass setup and tagging.install(),
which puts a _chia_tag on their dispatches (see tagging.py).

    # record: everything runs for real, the cache fills
    chia job submit --chia-cluster ../../accel/cluster.yaml -- \\
        python "$(pwd)/run.py" --iterations 3

    # replay: builds, simulations and LLM answers come from the cache
    ... python "$(pwd)/run.py" --iterations 3 --replay
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEL = HERE.parents[1] / "accel"
sys.path.insert(0, str(ACCEL))
sys.path.insert(0, str(HERE))

import chia
import ray

from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass, get_active_bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import accel_loop
import tagging
from results import BASELINE_DIR, OUT_DIR

CACHE_DIR = HERE.parents[0] / "cache"
FUNCS = ("ChiselBuildNode.build", "RiscvBuildNode.build",
         "VerilatorRunNode.run", "AntigravityLLM.prompt")


def cache_provider(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--cache-size", type=int, default=64, help="budget in GB")
    args = ap.parse_args()
    config = HERE / ("replay.yaml" if args.replay else "cache.yaml")

    ray.init(address="auto", runtime_env={
        "working_dir": str(ACCEL),
        "py_modules": [list(chia.__path__)[0]],
        "excludes": ["out/", "__pycache__", ".mypy_cache"],
    })

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = start_cache(size=args.cache_size, units="GB",
                        cache_dir_path=str(CACHE_DIR), yaml_path=str(config))
    Bypass(yaml_path=str(config))
    for func in FUNCS:
        get_active_bypass().set_provider(func, cache_provider)
        get_active_bypass().set_cond(func, cache_hit_cond)
    for line in tagging.install():
        print(f"  tagging {line}")

    out = OUT_DIR / f"profile_{time.strftime('%Y%m%d_%H%M%S')}"
    start_collector(log_dir=str(out))

    phases: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        phases.append((name, time.time()))
        get_profiler().log_event("phase", name=name)

    try:
        print(f"cache {CACHE_DIR}: {get(cache.size_bytes.chia_remote()) / 1e6:.0f} MB "
              f"({config.name})")
        mark("start")

        summary_path = BASELINE_DIR / "summary.json"
        if not (summary_path.exists()
                and json.loads(summary_path.read_text()).get("status") == "passed"):
            accel_loop.run_baseline()
        mark("baseline")

        chipyard_bash = BashTool(name="chipyard_bash", work_dir="/home/ray/chipyard",
                                 timeout_seconds=300,
                                 task_options={"resources": {"chipyard": 0.01}})
        for n in range(args.iterations):
            accel_loop.run_iteration(chipyard_bash)
            mark(f"iteration_{n + 1}")
    finally:
        stop_collector()
        size = get(cache.size_bytes.chia_remote())
        stop_cache()

    print(f"\n{'phase':<16}{'seconds':>10}")
    for (_, ts), (name, nxt) in zip(phases, phases[1:]):
        print(f"{name:<16}{nxt - ts:>10.1f}")
    print(f"{'TOTAL':<16}{phases[-1][1] - phases[0][1]:>10.1f}")
    print(f"\ncache holds {size / 1e6:.0f} MB in {CACHE_DIR}")
    print(f"profile: {out}")


if __name__ == "__main__":
    main()
