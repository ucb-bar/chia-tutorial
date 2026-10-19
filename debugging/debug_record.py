"""Record a full accel run — baseline + N iterations — into a CHIA cache.

Runs THEIR loop (accel_loop.run_baseline / run_iteration), with the four node
dispatches swapped for the tagged ones in debug_nodes.py. Their files are not
edited: the swap is done here, by rebinding the names accel_loop imported.

What it gives us:
  * how long each phase really takes, printed at the end
  * debugging/cache/ holding every build, compile, simulation and LLM answer
  * out/profile_record_<ts>/ for the profiling block

    chia job submit --chia-cluster cluster.yaml -- \\
        python "$(pwd)/debug_record.py" --iterations 3

Replay it afterwards with --replay (the same loop, served from the cache).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import chia
import ray

from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass, get_active_bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import sys
from pathlib import Path

# accel/ is not a package and accel_loop.py puts it on the workers' path via
# runtime_env["working_dir"]. Do the same for this process so "import nodes",
# "import results" and "import accel_loop" resolve from here.
ACCEL = Path(__file__).resolve().parents[1] / "accel"
sys.path.insert(0, str(ACCEL))

import accel_loop
import debug_nodes
from results import BASELINE_DIR, OUT_DIR

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"

# The four dispatches accel_loop imported by name. Rebinding them here is what
# puts a _chia_tag on every call without editing accel/nodes.py.
PATCHED = ("compile_software", "build_hardware", "run_simulation", "prompt_llm")


def patch_accel_loop() -> None:
    for name in PATCHED:
        setattr(accel_loop, name, getattr(debug_nodes, name))
    print(f"tagged dispatches installed: {', '.join(PATCHED)}")


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
    ap.add_argument("--replay", action="store_true",
                    help="serve the recorded builds, sims and LLM answers from the cache")
    ap.add_argument("--cache-size", type=int, default=64, help="cache budget in GB")
    args = ap.parse_args()

    config = HERE / ("debug_record_replay.yaml" if args.replay else "debug_record.yaml")

    ray.init(address="auto", runtime_env={
        "working_dir": str(ACCEL),
        "py_modules": [list(chia.__path__)[0]],
        "excludes": ["out/", "__pycache__", ".mypy_cache"],
    })

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = start_cache(size=args.cache_size, units="GB",
                        cache_dir_path=str(CACHE_DIR), yaml_path=str(config))
    Bypass(yaml_path=str(config))
    for func in ("ChiselBuildNode.build", "RiscvBuildNode.build",
                 "VerilatorRunNode.run", "AntigravityLLM.prompt"):
        get_active_bypass().set_provider(func, cache_provider)
        get_active_bypass().set_cond(func, cache_hit_cond)

    patch_accel_loop()
    out = OUT_DIR / f"profile_record_{time.strftime('%Y%m%d_%H%M%S')}"
    start_collector(log_dir=str(out))

    phases: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        phases.append((name, time.time()))
        get_profiler().log_event("phase", name=name)

    try:
        print(f"cache {CACHE_DIR}: {get(cache.size_bytes.chia_remote()) / 1e6:.0f} MB, "
              f"config {config.name}")
        mark("start")

        have_baseline = (BASELINE_DIR / "summary.json").exists() and \
            json.loads((BASELINE_DIR / "summary.json").read_text()).get("status") == "passed"
        if not have_baseline:
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
    print(f"\ncache now holds {size / 1e6:.0f} MB")
    print(f"profile: {out}")
    print(f"render:  python debug_render_profile.py {out}")


if __name__ == "__main__":
    main()
