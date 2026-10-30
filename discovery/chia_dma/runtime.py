"""Preserve the seed and attach CHIA cache and profiling."""

from pathlib import Path
import ray
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector
from .config_loader import ROOT, digest
from .profiling import export_profiles
from .source import (
    git,
    read_json,
    preserve_source,
    write_json,
)


def prepare(configuration, run):
    run.mkdir(parents=True, exist_ok=True)
    if (run / "run.json").exists():
        meta = read_json(run / "run.json")
    else:
        status = git(ROOT, "status", "--porcelain", "--", ".")
        meta = {
            "run_id": run.name,
            "seed": str(preserve_source(configuration)),
            "loop_revision": git(ROOT, "rev-parse", "HEAD"),
            "loop_dirty": None if status is None else bool(status),
        }
        write_json(run / "run.json", meta)
    seed = run.parent / "seeds" / Path(meta["seed"]).name
    meta["seed"] = str(seed if seed.is_dir() else meta["seed"])
    if not (Path(meta["seed"]) / "tree").is_dir():
        raise FileNotFoundError("Preserved Chipyard seed is missing: " + meta["seed"])
    return meta


def cache_hit(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def from_cache(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError("Cache entry evicted: " + tag)
    return value


def cache_provider(name):
    def provider(tag, data_path, *args, **kwargs):
        try:
            return from_cache(tag, data_path, *args, **kwargs)
        except KeyError:
            # Resolve task code only on the worker that needs to rebuild it.
            if name == "build_program":
                from chia.chipyard.riscv_build_node import RiscvBuildNode
                return RiscvBuildNode.build_program(*args, **kwargs)
            from . import nodes
            return getattr(nodes, name)(*args, **kwargs)
    return provider


class Runtime:
    """Attach to an existing native cluster; own cache/profiling, not worker lifecycle."""

    def __init__(self, configuration, run):
        self.configuration, self.run = configuration, Path(run)

    def __enter__(self):
        configuration = self.configuration
        ray.init(
            address=configuration["runtime"]["ray_address"],
            namespace="dma-" + digest(str(self.run))[:24],
        )
        start_collector(log_dir=str(self.run / "artifacts/profile"))
        start_cache(
            configuration["runtime"]["cache_gb"],
            str(self.run / "cache"),
            units="GB",
            yaml_path=configuration["_config_file"],
        )
        bypass = Bypass(configuration["_config_file"])
        for name in ("build", "build_program", "simulate"):
            bypass.set_cond(name, cache_hit)
            bypass.set_provider(name, cache_provider(name))
        return self

    def __exit__(self, *args):
        try:
            export_profiles(self.run / "artifacts/profile")
        finally:
            try:
                stop_collector()
                stop_cache()
            finally:
                ray.shutdown()
