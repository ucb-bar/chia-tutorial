"""Private, unscored correctness runs. Never mounted in generation containers."""

import hashlib
import re
import secrets
import struct
import time

import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel

MASK64 = (1 << 64) - 1
GUARD = 0xD15EA5EDCAFEF00D
MARKER = b"CHIA-MEMORY-0001"


def pattern(index, seed):
    value = (((index + 1) * 0x9E3779B97F4A7C15) & MASK64) ^ 0x123456789ABCDEF0
    if seed:
        mixed = seed ^ (((index + 1) * 0x9E3779B97F4A7C15) & MASK64)
        mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & MASK64
        value ^= mixed ^ (mixed >> 31)
    return value


def fold(value, word):
    rotated = ((value << 5) | (value >> 27)) & 0xFFFFFFFF
    return (rotated ^ (word & 0xFFFFFFFF) ^ (word >> 32)) & 0xFFFFFFFF


def reference_outputs(seed):
    """Independent final layouts, derived without running candidate C or RTL."""
    p = lambda index: pattern(index, seed)
    small = [
        GUARD if i % 10 in (0, 9) else p(i // 10 * 8 + i % 10 - 1) for i in range(2560)
    ]
    stream = [GUARD] * 8 + [p(i) for i in range(4096)] + [GUARD] * 8
    gather = (
        [GUARD]
        + [p(((i // 64 * 73) % 64) * 66 + 1 + i % 64) for i in range(4096)]
        + [GUARD]
    )
    scatter = [
        p(((i // 136 * 45) % 32) * 128 + i % 136 - 4) if 4 <= i % 136 < 132 else GUARD
        for i in range(4352)
    ]
    # The even final step updates the second buffer before copying to the first.
    final = [(p(i) + (131328 if i == 0 else 0)) & MASK64 for i in range(64)]
    chain = ([GUARD] + final + [GUARD]) * 2
    witness = 0
    for stage in range(1, 513):
        witness = fold(witness, ((p(0) + stage * (stage + 1) // 2) & MASK64) ^ p(63))
    lengths = (
        0, 7, 8, 31, 63, 64, 65, 127, 128, 129, 255, 256,
        257, 511, 512, 513, 1023, 1024, 1025, 2047, 2048, 2049, 4095, 4096,
    )
    offsets = (
        0, 1, 0, 3, 0, 0, 5, 7, 0, 1, 1, 0, 3, 3, 0, 5, 7, 0, 1, 3, 0, 5, 1, 0,
    )
    source = b"".join(p(i).to_bytes(8, "little") for i in range(514))
    mixed = bytearray()
    for _ in range(2):
        for length, offset in zip(lengths, offsets):
            slot = bytearray(GUARD.to_bytes(8, "little") * ((length + 39) // 8))
            slot[8 + offset : 8 + offset + length] = source[offset : offset + length]
            mixed.extend(slot)
    mixed_words = [
        int.from_bytes(mixed[i : i + 8], "little") for i in range(0, len(mixed), 8)
    ]
    return {
        "copy-small": (small, 0),
        "copy-stream": (stream, 0),
        "copy-gather": (gather, 0),
        "copy-scatter": (scatter, 0),
        "copy-chain": (chain, witness),
        "copy-mixed": (mixed_words, 0),
    }


def seeded_binary(content, seed):
    """Set the trusted runtime record after compilation, without rebuilding C."""
    if content.count(MARKER) != 1:
        raise ValueError("Missing or ambiguous runtime input record")
    start = content.index(MARKER) + len(MARKER)
    return (
        content[:start]
        + seed.to_bytes(8, "little")
        + (1).to_bytes(8, "little")
        + content[start + 16 :]
    )


def matches_output(result, reference):
    """Check a strong digest covering every output word and the chain witness."""
    if not result.success or result.returncode != 0:
        return False
    text = result.log + "\n" + result.out
    if re.search(
        r"Assertion failed|%Error|\*\*\* FAILED|DMA_PROTOCOL_FAIL|\bFAIL\b", text
    ):
        return False
    records = re.findall(
        r"^MEMORY_OBSERVATION words=(\d+) witness=(\d+) digest=([0-9a-f]{64})$",
        text,
        re.MULTILINE,
    )
    if len(records) != 1:
        return False
    words, witness = reference
    count, observed_witness, digest = records[0]
    content = struct.pack("<QQ", len(words), witness) + b"".join(
        struct.pack("<Q", word) for word in words
    )
    return (
        int(count) == len(words)
        and int(observed_witness) == witness
        and digest == hashlib.sha256(content).hexdigest()
    )


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def prepare_scored_inputs(software):
    """Give scored and hidden runs indistinguishable runtime input records."""
    binaries, seeds, checksums = {}, {}, {}
    for name, content in software.items():
        seed = secrets.randbits(64) or 1
        words, witness = reference_outputs(seed)[name]
        value = 0x6D2B79F5
        for word in words:
            value = fold(value, word)
        checksums[name] = ((value & 0x7FFFFFFF) ^ witness) & 0x7FFFFFFF
        binaries[name] = seeded_binary(content, seed)
        seeds[name] = seed
    return {"software": binaries, "seeds": seeds, "checksums": checksums}


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def run_hidden_tests(configuration, simulator, software, dram_settings, scored_seeds):
    """Run two private cases on the existing RTL workers, beside public runs.

    Return only pass/fail and anonymous execution times. Case names, seeds,
    expected data, and simulator output never enter controller/agent feedback.
    """
    from .nodes import simulate
    started = time.monotonic()
    tasks, timings = [], []
    try:
        names = secrets.SystemRandom().sample(sorted(software), 2)
        seeds = set()
        while len(seeds) < 2:
            seed = secrets.randbits(64)
            if seed and seed not in scored_seeds.values():
                seeds.add(seed)
        workers = sorted(node["NodeID"] for node in ray.nodes()
                         if node["Alive"] and node["Resources"].get("verilator_run", 0) >= 1)
        if not workers:
            raise ValueError("No RTL workers")
        cases = list(zip(names, seeds))
        for index, (name, seed) in enumerate(cases):
            task = simulate.options(scheduling_strategy=NodeAffinitySchedulingStrategy(
                workers[(len(software) + index) % len(workers)], soft=False,
            )).chia_remote(configuration, simulator, seeded_binary(software[name], seed),
                           name, dram_settings)
            tasks.append(task)
        passed = True
        # Tests have already been submitted together; retrieval does not serialize them.
        for (name, seed), task in zip(cases, tasks):
            result = get(task)
            timings.append({"wall_seconds": getattr(result, "wall_seconds", 0.0),
                            "worker_host": getattr(result, "worker_host", "")})
            passed &= matches_output(result, reference_outputs(seed)[name])
        return {"passed": bool(passed), "tests": timings,
                "wall_seconds": time.monotonic() - started}
    except Exception:
        return {"passed": False, "tests": timings,
                "wall_seconds": time.monotonic() - started}
    finally:
        for task in tasks:
            chia_cancel(getattr(task, "ref", task), force=True)


@ChiaFunction(resources={"verification": 1}, num_cpus=1, max_retries=0)
def verify_correctness(scored_results, scored_seeds, hidden_result):
    """Check scored outputs independently and combine with the private verdict."""
    try:
        expected = set(reference_outputs(0))
        if not hidden_result["passed"] or set(scored_results) != expected or set(scored_seeds) != expected:
            return False
        return all(matches_output(result, reference_outputs(scored_seeds[name])[name])
                   for name, result in scored_results.items())
    except Exception:
        return False
