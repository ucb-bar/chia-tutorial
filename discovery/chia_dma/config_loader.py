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
    BASE_FILE_PATTERNS,
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
    text = path.read_text()
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
    configuration["storage"] = str((ROOT / configuration["storage"]).resolve())
    configuration["base"]["files"] = {
        str(path.relative_to(ROOT / "base")): digest(path.read_bytes())
        for pattern in BASE_FILE_PATTERNS
        for path in sorted((ROOT / "base").glob(pattern))
    }
    # Each mutable checkout needs one task at a time; preserve extra resources.
    workers = configuration["cluster"]["available_node_types"]
    for role, resource in (("generation", "generation"), ("build", "chipyard")):
        worker = workers[role]
        worker.setdefault("resources", {})[resource] = 1
        worker["num_workers"] = max(1, int(worker.get("num_workers", 1)))
    configuration["_config_file"] = str(path)
    return configuration
