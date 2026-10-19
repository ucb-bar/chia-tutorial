"""The accel baseline, tagged, cached and timed.

Runs the same work as accel_loop.run_baseline() — their nodes, their harness,
their cases — but dispatches through debug_nodes.py so every call carries a
_chia_tag, and starts a CHIA cache so the results are written to disk.

    # run 1: everything real, cache fills
    chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/debug_baseline.py"

    # run 2: the Chisel build and the simulations replay from the cache
    chia job submit --chia-cluster cluster.yaml -- python "$(pwd)/debug_baseline.py" --replay

    # fewer cases, to check the plumbing without 17 of everything
    ... debug_baseline.py --cases 2

Prints a per-stage timing table at the end and leaves a profile under
out/profile_debug_<ts>/.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import chia
import ray

from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass, get_active_bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import sys
from pathlib import Path

# accel/ is not a package and accel_loop.py puts it on the workers' path via
# runtime_env["working_dir"]. Do the same for this process so "import nodes",
# "import results" and "import accel_loop" resolve from here.
ACCEL = Path(__file__).resolve().parents[1] / "accel"
sys.path.insert(0, str(ACCEL))

import debug_nodes
from nodes import git_reset
from results import CASES, OUT_DIR, TEMPLATE_C_PATH, read_body, render_source

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"


def cache_provider(tag, data_path, *args, **kwargs):
    """Replay: serve what the first real run wrote under this tag."""
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    """Only replay when the value is really there; otherwise run for real."""
    return get(get_active_cache().has.chia_remote(tag))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replay", action="store_true",
                    help="bypass the Chisel build and the simulations from the cache")
    ap.add_argument("--cases", type=int, default=0,
                    help="use only the first N cases (default: all)")
    ap.add_argument("--cache-size", type=int, default=32, help="cache budget in GB")
    args = ap.parse_args()

    cases = CASES[:args.cases] if args.cases else CASES
    config = HERE / ("debug_cache_replay.yaml" if args.replay else "debug_cache.yaml")

    ray.init(address="auto", runtime_env={
        "working_dir": str(ACCEL),
        "py_modules": [list(chia.__path__)[0]],
        "excludes": ["out/", "__pycache__", ".mypy_cache"],
    })

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = start_cache(size=args.cache_size, units="GB",
                        cache_dir_path=str(CACHE_DIR), yaml_path=str(config))
    Bypass(yaml_path=str(config))
    for func in ("ChiselBuildNode.build", "VerilatorRunNode.run"):
        get_active_bypass().set_provider(func, cache_provider)
        get_active_bypass().set_cond(func, cache_hit_cond)

    out = OUT_DIR / f"profile_debug_{time.strftime('%Y%m%d_%H%M%S')}"
    start_collector(log_dir=str(out))

    marks: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        marks.append((name, time.time()))
        get_profiler().log_event("phase", name=name)

    try:
        print(f"cache: {CACHE_DIR} ({get(cache.size_bytes.chia_remote())} bytes), config {config.name}")
        mark("start")

        print(f"chipyard reset: {get(git_reset())}")
        mark("reset_done")

        source = render_source(read_body(TEMPLATE_C_PATH.read_text()))

        elf_refs = {c.name: debug_nodes.compile_software(source, c.name, c.cflags) for c in cases}
        build_ref = debug_nodes.build_hardware("baseline")
        elfs = {name: get(r) for name, r in elf_refs.items()}
        mark("compiles_done")

        build = get(build_ref)
        print(f"hardware build success={build.success} rc={build.returncode} "
              f"binary={len(build.simulator_binary_content) / 1e6:.0f} MB")
        mark("build_done")

        run_refs = {name: debug_nodes.run_simulation(build, elf) for name, elf in elfs.items()}
        runs = {name: get(r) for name, r in run_refs.items()}
        mark("sims_done")

        ok = sum(1 for r in runs.values() if r.returncode == 0)
        print(f"simulations: {ok}/{len(runs)} exited 0")
    finally:
        stop_collector()
        size = get(cache.size_bytes.chia_remote())
        stop_cache()

    print(f"\n{'phase':<20}{'seconds':>10}")
    for (name, ts), (nxt_name, nxt) in zip(marks, marks[1:]):
        print(f"{nxt_name:<20}{nxt - ts:>10.1f}")
    print(f"{'TOTAL':<20}{marks[-1][1] - marks[0][1]:>10.1f}")
    print(f"\ncache now holds {size / 1e6:.0f} MB in {CACHE_DIR}")
    print(f"profile: {out}")


if __name__ == "__main__":
    main()
