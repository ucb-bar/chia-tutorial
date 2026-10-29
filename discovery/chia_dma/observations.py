"""Collect bulk output through HTIF, then hash it on the simulation worker host."""

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
import socket
import struct
import tempfile
import time

from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.chipyard.state_def import RunResult

FRAME = b"CHIA-OUTPUT-0001"


@dataclass
class TimedRunResult(RunResult):
    wall_seconds: float = 0.0
    worker_host: str = ""


def decode_observation(raw):
    """Remove one framed output buffer and replace it with a compact digest."""
    start = raw.find(FRAME)
    if start < 0 or start + len(FRAME) + 16 > len(raw):
        raise ValueError("Missing observation frame")
    header = start + len(FRAME)
    words, witness = struct.unpack_from("<QQ", raw, header)
    end = header + 16 + words * 8
    if words > 1_000_000 or end >= len(raw) or raw[end:end + 1] != b"\n":
        raise ValueError("Truncated observation frame")
    if FRAME in raw[end + 1:]:
        raise ValueError("Duplicate observation frame")
    digest = hashlib.sha256(raw[header:end]).hexdigest()
    clean = raw[:start] + raw[end + 1:]
    record = f"MEMORY_OBSERVATION words={words} witness={witness} digest={digest}\n"
    return clean.decode(errors="replace") + "\n" + record


def run_observed(simulator, content, name, plusargs, dram_settings):
    """Run RTL, hash its output on the host, and return logs and elapsed time."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dma-execution-") as directory:
        result = VerilatorRunNode().run(
            simulator,
            content,
            name + ".riscv",
            directory,
            plusargs=plusargs,
            dramsim_ini_files=dram_settings,
            timeout_seconds=30 * 60,
            verbose=False,
            cleanup_task_dir=False,  # Read raw output before the directory is removed.
        )
        try:
            logs = list(Path(directory).glob("*/" + name + ".log"))
            if len(logs) != 1:
                raise ValueError("Missing run output")
            result.log = decode_observation(logs[0].read_bytes())
        except ValueError:
            result.success = False
            result.log = "Observation transfer failed"
    return TimedRunResult(
        **asdict(result),
        wall_seconds=time.monotonic() - started,
        worker_host=socket.gethostname(),
    )
