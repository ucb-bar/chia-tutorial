"""accel's own loop, with a CHIA cache attached.

This runs THEIR entry point — accel_loop.main() — so the flow, the arguments,
the profiler and the teardown are all theirs:

    python run.py --iterations 3        ==  python ../../accel/accel_loop.py --iterations 3
    python run.py --baseline-only       ==  ... --baseline-only

Two things are added around it:

  tagging.install()   puts a _chia_tag on their dispatches, which is what the
                      cache keys on (accel/nodes.py passes none)
  _attach_cache()     starts the cache + bypass, on the first call into the
                      loop — i.e. after their ray.init() and start_collector(),
                      which must come first (get_profiler() latches whether a
                      collector exists the first time anything calls it)

    --replay serves the recorded builds, simulations and LLM answers back.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEL = HERE.parents[1] / "accel"
sys.path.insert(0, str(ACCEL))
sys.path.insert(0, str(HERE))

from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass, get_active_bypass
from chia.base.cache import get_active_cache, start_cache

import accel_loop
import tagging

CACHE_DIR = HERE.parents[0] / "cache"
CACHE_SIZE_GB = 64
FUNCS = ("ChiselBuildNode.build", "RiscvBuildNode.build",
         "VerilatorRunNode.run", "AntigravityLLM.prompt")

_attached = False


def cache_provider(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def _attach_cache(config: Path) -> None:
    """Start the cache and the bypass. Called from inside their loop, so Ray is
    up and their collector is already running."""
    global _attached
    if _attached:
        return
    _attached = True
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = start_cache(size=CACHE_SIZE_GB, units="GB",
                        cache_dir_path=str(CACHE_DIR), yaml_path=str(config))
    Bypass(yaml_path=str(config))
    for func in FUNCS:
        get_active_bypass().set_provider(func, cache_provider)
        get_active_bypass().set_cond(func, cache_hit_cond)
    print(f"cache {CACHE_DIR}: {get(cache.size_bytes.chia_remote()) / 1e6:.0f} MB "
          f"({config.name})", flush=True)


def main() -> None:
    replay = "--replay" in sys.argv
    if replay:
        sys.argv.remove("--replay")          # their argparse does not know it
    config = HERE / ("replay.yaml" if replay else "cache.yaml")

    for line in tagging.install():
        print(f"  tagging {line}")

    # Attach the cache on the way into the loop, then hand over to their code.
    for name in ("run_baseline", "run_iteration"):
        original = getattr(accel_loop, name)

        def attached(*args, _original=original, **kwargs):
            _attach_cache(config)
            return _original(*args, **kwargs)

        setattr(accel_loop, name, attached)

    accel_loop.main()


if __name__ == "__main__":
    main()
