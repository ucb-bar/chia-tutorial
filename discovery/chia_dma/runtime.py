"""Prepare CHIA deployment, cache, and profiling."""

from pathlib import Path
from string import Template
import os
import ray
import yaml
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector
from .config import ROOT, digest, frozen
from .experiment import (
    git,
    read_json,
    preserve_source,
    read_candidate_files,
    write_json,
)


def prepare(configuration, run):
    run.mkdir(parents=True, exist_ok=True)
    if (run / "run.json").exists():
        meta = read_json(run / "run.json")
        if meta["frozen_hash"] != digest(frozen(configuration)):
            raise ValueError("Frozen evaluation/search configuration changed")
    else:
        status = git(ROOT, "status", "--porcelain", "--", ".")
        meta = {
            "run_id": run.name,
            "seed": str(preserve_source(configuration)),
            "frozen_hash": digest(frozen(configuration)),
            "loop_revision": git(ROOT, "rev-parse", "HEAD"),
            "loop_dirty": None if status is None else bool(status),
        }
        write_json(run / "run.json", meta)
    seed = run.parent / "seeds" / Path(meta["seed"]).name
    meta["seed"] = str(seed if seed.is_dir() else meta["seed"])
    render(configuration, run, meta["seed"])
    return meta


def expand(value, bindings):
    if isinstance(value, str):
        return Template(value).safe_substitute(bindings)
    if isinstance(value, list):
        return [expand(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, bindings) for k, v in value.items()}
    return value


def render(configuration, run, seed):
    """Expand run paths and generation mounts."""
    agent = configuration["agent"]
    backend = agent["backends"][agent["backend"]]
    bindings = dict(
        LOOP=str(ROOT),
        RUN=str(run),
        SEED=str(seed),
        CHIA=configuration["chia"],
        SKY=configuration["skydiscover"],
        NAME=Path(run).name,
        UID=str(os.getuid()),
        GID=str(os.getgid()),
        AGENT_IMAGE=backend["image"],
        CREDENTIALS=backend["credentials"],
        CREDENTIAL_TARGET=backend["credential_target"],
    )
    deployment = expand(configuration["cluster"], bindings)
    generation_template = deployment["available_node_types"].pop("generation")
    source = read_candidate_files(Path(seed) / "tree")
    for worker_index in range(generation_template["num_workers"]):
        workspace = Path(run) / "work" / f"generation-{worker_index}"
        workspace.mkdir(parents=True, exist_ok=True)
        for name, text in source.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(text)
        worker = expand(
            generation_template, {"INDEX": str(worker_index), "WORK": str(workspace)}
        )
        worker["num_workers"] = 1
        worker["docker"]["run_options"] += expand(backend.get("mounts", []), bindings)
        worker["worker_setup_commands"] += expand(
            backend.get("setup_commands", []), bindings
        )
        deployment["available_node_types"][f"generation-{worker_index}"] = worker
    path = Path(run) / "deployment/cluster.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(deployment, sort_keys=False))
    return path


def cache_hit(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))


def from_cache(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError("Cache entry evicted: " + tag)
    return value


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
        for name in ("build", "simulate"):
            bypass.set_cond(name, cache_hit)
            bypass.set_provider(name, from_cache)
        return self

    def __exit__(self, *args):
        try:
            stop_collector()
            stop_cache()
        finally:
            ray.shutdown()
