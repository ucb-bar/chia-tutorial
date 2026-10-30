"""Optional weighted scoring and DMA-event parsing; not used by the tutorial."""

import json
import math
import re

from chia_dma.config_loader import digest


def aggregate_weighted(records, reference, workloads, weights, source):
    """Maximize geometric-mean cycle speedup for all correctly checksummed tests."""
    vector = {}
    values = []
    total_cycles = total_instructions = 0
    if not workloads or set(reference) != set(records) or set(records) != set(workloads):
        raise ValueError("Full memory-test suite results are required")
    for name in sorted(reference):
        row, seed = records[name], reference[name]
        if (
            row["benchmark_result"] != workloads[name]["checksum"]
            or row["verification_result"] != 1
        ):
            raise ValueError(f"{name}: memory-test checksum verification failed")
        cycles = row["benchmark_cycles"]
        instructions = row["benchmark_instructions"]
        require(cycles > 0 and instructions > 0, "Invalid iron-law counters")
        speedup = seed["benchmark_cycles"] / cycles
        vector[name] = {**row, "speedup": speedup, "cpi": cycles / instructions}
        values.append((speedup, weights.get(name, 1.0)))
        total_cycles += cycles
        total_instructions += instructions
    weighted_log_speedup = 0
    total_weight = 0
    for speedup, weight in values:
        if weight < 0 or not math.isfinite(weight):
            raise ValueError("Invalid workload weights")
        total_weight += weight
        weighted_log_speedup += weight * math.log(speedup)
    if total_weight <= 0:
        raise ValueError("Invalid workload weights")
    score = math.exp(weighted_log_speedup / total_weight)
    if not math.isfinite(score) or score <= 0:
        raise ValueError("Invalid aggregate score")
    return {
        "combined_score": score,
        "validity": 1,
        "source_bytes": len(json.dumps(source).encode()),
        "cycles": total_cycles,
        "instructions": total_instructions,
        "cpi": total_cycles / total_instructions,
        "source_hash": digest(source),
    }, vector


def metric_records(text, tag):
    result = []
    for line in text.splitlines():
        if not line.startswith(tag + " "):
            continue
        record = {}
        for token in line.split()[1:]:
            require(re.fullmatch(r"\w+=-?\d+", token), "Malformed metric record")
            name, value = token.split("=")
            require(name not in record, "Duplicate metric: " + name)
            record[name] = int(value)
        require(record.get("metrics_version") == 1, "Unsupported metrics version")
        result.append(record)
    return result


def dma_diagnostics(text):
    return {
        "hardware": metric_records(text, "DMA_HW"),
        "copy_events": metric_records(text, "DMA_COPY"),
    }


def require(condition, message):
    if not condition:
        raise ValueError(message)
