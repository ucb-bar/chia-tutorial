#!/usr/bin/env python3
"""Independent data oracle, native traffic audit, and incorrect-copy rejection."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
MASK64 = (1 << 64) - 1
GUARD = 0xD15EA5EDCAFEF00D


def pattern(index):
    return (((index + 1) * 0x9E3779B97F4A7C15) & MASK64) ^ 0x123456789ABCDEF0


def fold(hash_value, word):
    rotated = ((hash_value << 5) | (hash_value >> 27)) & 0xFFFFFFFF
    return rotated ^ (word & 0xFFFFFFFF) ^ (word >> 32)


def checksum(words):
    value = 0x6D2B79F5
    for word in words:
        value = fold(value, word)
    return value & 0x7FFFFFFF


def references():
    # Describe final layouts directly, without executing the C copy loops.
    small = [GUARD if i % 10 in (0, 9) else pattern(i // 10 * 8 + i % 10 - 1)
             for i in range(2048 * 10)]
    stream = [GUARD] * 8 + [pattern(i) for i in range(32768)] + [GUARD] * 8
    gather = [GUARD] + [pattern(((i // 64 * 73) % 256) * 66 + 1 + i % 64)
                        for i in range(16384)] + [GUARD]
    scatter = [pattern(((i // 136 * 45) % 128) * 128 + i % 136 - 4)
               if 4 <= i % 136 < 132 else GUARD for i in range(128 * 136)]
    final = [pattern(i) + (8256 if i == 0 else 0) for i in range(128)]
    chain = ([GUARD] + final + [GUARD]) * 2
    witness = 0
    for stage in range(1, 129):
        witness = fold(witness, (pattern(0) + stage * (stage + 1) // 2) ^ pattern(127))
    lengths = [0, 7, 8, 31, 63, 64, 65, 127, 128, 255, 256, 511, 512, 1024, 2048, 4096]
    offsets = [0, 1, 0, 3, 0, 0, 5, 7, 0, 1, 0, 3, 0, 0, 0, 0]
    source = b"".join(pattern(i).to_bytes(8, "little") for i in range(514))
    mixed = bytearray()
    for _ in range(16):
        for length, offset in zip(lengths, offsets):
            slot = bytearray(GUARD.to_bytes(8, "little") * ((length + 39) // 8))
            slot[8 + offset:8 + offset + length] = source[offset:offset + length]
            mixed.extend(slot)
    mixed_words = [int.from_bytes(mixed[i:i + 8], "little") for i in range(0, len(mixed), 8)]
    return {
        "copy-small": (checksum(small), 2048, 131072),
        "copy-stream": (checksum(stream), 16, 262144),
        "copy-gather": (checksum(gather), 256, 131072),
        "copy-scatter": (checksum(scatter), 128, 131072),
        "copy-chain": ((checksum(chain) ^ witness) & 0x7FFFFFFF, 128, 131072),
        "copy-mixed": (checksum(mixed_words), 256, sum(lengths) * 16),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/native")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "workloads.json").read_text())
    environment = dict(os.environ)
    environment.pop("MEMORY_COPY_FAULT", None)
    for name, (expected, calls, count) in references().items():
        spec = manifest[name]
        assert (spec["checksum"], spec["calls"], spec["payload_bytes"]) == (expected, calls, count), name
        executable = str((args.build_dir / name).resolve())
        result = subprocess.run([executable], capture_output=True, text=True,
                                timeout=30, check=True, env=environment)
        match = re.search(r"checksum=(\d+) verification_result=1", result.stdout)
        assert match and int(match[1]) == expected, (name, result.stdout)
        assert f"MEMORY_TRAFFIC calls={calls} bytes={count}\n" in result.stdout, (name, result.stdout)
        for fault in ("skip", "short", "overrun", "source"):
            bad = subprocess.run([executable], capture_output=True, text=True, timeout=30,
                                 env={**environment, "MEMORY_COPY_FAULT": fault})
            assert bad.returncode == 1 and "verification_result=0" in bad.stdout, (name, fault, bad)
        print(f"{name}: checksum={expected}, {calls} copies, {count} bytes; four copy faults rejected")


if __name__ == "__main__":
    main()
