"""Tutorial controls layered over fixed backend settings."""

from pathlib import Path
import copy
import hashlib
import json
import os
import yaml

ROOT = Path(__file__).resolve().parents[1]
HARDWARE_FILES = (
    "generators/chipyard/src/main/scala/dma/DMAController.scala",
    "generators/chipyard/src/main/scala/dma/RoCCDMA.scala",
)
SOFTWARE_FILES = tuple(
    f"tests/memory-tests/src/{name}/{name}.c"
    for name in (
        "copy-small", "copy-stream", "copy-gather",
        "copy-scatter", "copy-chain", "copy-mixed",
    )
)
ALLOWLIST = HARDWARE_FILES + SOFTWARE_FILES


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True).encode()
    )
    return hashlib.sha256(data).hexdigest()


def load(path=ROOT / "config.yaml"):
    path = Path(path).resolve()
    cpus = str(len(os.sched_getaffinity(0)))
    documents = []
    for config_path in (ROOT / "backend.yaml", path):
        text = config_path.read_text().replace("${CPUS}", cpus)
        documents.append(yaml.safe_load(os.path.expandvars(text)))
    configuration, cluster = documents
    settings = {key: cluster.pop(key) for key in ("agent", "search", "runtime")}
    settings["cluster"] = cluster
    merge(configuration, settings)
    for key in ("storage", "chia", "skydiscover", "evolve_flows"):
        configuration[key] = str((ROOT / configuration[key]).resolve())
    configuration["base"]["files"] = {
        str(path.relative_to(ROOT / "base")): digest(path.read_bytes())
        for pattern in (
            "chipyard.patch",
            "prepare.sh",
            "memory-tests/Makefile",
            "memory-tests/workloads.json",
            "memory-tests/check.py",
            "memory-tests/src/*/*.c",
            "memory-tests/support/*.[ch]",
        )
        for path in sorted((ROOT / "base").glob(pattern))
    }
    configuration["base"]["evaluator"] = digest(
        (ROOT / "chia_dma/evaluation.py").read_bytes()
    )
    for backend in configuration["agent"]["backends"].values():
        backend["credentials"] = str((ROOT / backend["credentials"]).resolve())
    workers = configuration["cluster"]["available_node_types"]
    generation = workers["generation"]
    if generation["resources"] != {"generation": 1} or generation["num_workers"] < 1:
        raise ValueError(
            "Each generation workspace needs one serial generation slot; increase num_workers for concurrency"
        )
    if workers["build"]["resources"].get("chipyard") != 1:
        raise ValueError(
            "Each private build checkout requires one serial chipyard slot; increase num_workers for parallel builds"
        )
    if tuple(configuration["hardware"]["allowlist"]) != HARDWARE_FILES:
        raise ValueError(
            "The DMA implementation allowlist is fixed; establish a new integration to expand it"
        )
    if tuple(configuration["software"]["allowlist"]) != SOFTWARE_FILES:
        raise ValueError("The six editable C workload files are fixed")
    if configuration["search"]["type"] != "openevolve_native":
        raise ValueError("This tutorial uses SkyDiscover openevolve_native")
    configuration["_config_file"] = str(ROOT / "backend.yaml")
    return configuration


def merge(base, updates):
    for key, value in updates.items():
        if isinstance(value, dict):
            merge(base.setdefault(key, {}), value)
        else:
            base[key] = value


def frozen(configuration):
    """Backend/model may change on resume; evaluation and search rules may not."""
    data = {
        key: copy.deepcopy(configuration[key])
        for key in (
            "dependencies",
            "hardware",
            "software",
            "fitness",
            "search",
            "base",
        )
    }
    worker_contract = {}
    for name, worker in configuration["cluster"]["available_node_types"].items():
        if name == "generation":
            continue
        worker_contract[name] = {
            "image": worker["docker"]["image"],
            "mounts_environment": worker["docker"]["run_options"],
            "environment": worker.get("worker_env_commands", []),
            "setup": worker.get("worker_setup_commands", []),
        }
    data["worker_contract"] = worker_contract
    data["search"].pop("iterations", None)
    data["search"].pop("max_in_flight", None)
    return data
