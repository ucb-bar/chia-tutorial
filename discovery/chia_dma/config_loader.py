"""Load tutorial controls and the native CHIA cluster from one YAML file."""

from pathlib import Path
import copy
import hashlib
import json
import os
import yaml
from .defaults import (
    DEFAULTS,
    HARDWARE_FILES,
    SOFTWARE_FILES,
    RELATIVE_PATHS,
    BASE_FILE_PATTERNS,
    EVALUATOR_FILES,
    FROZEN_SECTIONS,
)

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True).encode()
    )
    return hashlib.sha256(data).hexdigest()


def load(path=ROOT / "cluster.yaml"):
    path = Path(path).resolve()
    cpus = str(len(os.sched_getaffinity(0)))
    text = path.read_text().replace("${CPUS}", cpus)
    document = yaml.safe_load(os.path.expandvars(text))
    controls = document.pop("tutorial")
    configuration = copy.deepcopy(DEFAULTS)
    for section, values in controls.items():
        configuration[section].update(values)
    configuration["runtime"]["ray_address"] = os.path.expandvars(
        configuration["runtime"]["ray_address"]
    )
    configuration["hardware"]["make_jobs"] = int(cpus)
    configuration["cluster"] = document
    for key in RELATIVE_PATHS:
        configuration[key] = str((ROOT / configuration[key]).resolve())
    configuration["base"]["files"] = {
        str(path.relative_to(ROOT / "base")): digest(path.read_bytes())
        for pattern in BASE_FILE_PATTERNS
        for path in sorted((ROOT / "base").glob(pattern))
    }
    configuration["base"]["evaluator"] = digest([
        digest((ROOT / "chia_dma" / name).read_bytes())
        for name in EVALUATOR_FILES
    ])
    configuration["agent"]["credentials"] = str(
        (ROOT / os.path.expandvars(configuration["agent"]["credentials"])).resolve()
    )
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
    if configuration["search"]["type"] != "openevolve_native":
        raise ValueError("This tutorial uses SkyDiscover openevolve_native")
    configuration["_config_file"] = str(path)
    return configuration


def frozen(configuration):
    """Backend/model may change on resume; evaluation and search rules may not."""
    data = {
        key: copy.deepcopy(configuration[key])
        for key in FROZEN_SECTIONS
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
