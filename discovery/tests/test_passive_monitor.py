"""Check the actual bundled observer with accepted and rejected handshake traces."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def monitor_binary(base_tree, tmp_path_factory):
    verilator = shutil.which("verilator")
    if not verilator:
        pytest.skip("Verilator is required for observer regressions")
    build = tmp_path_factory.mktemp("rocc-handshakes")
    subprocess.run(
        [
            verilator, "--binary", "--timing", "--assert", "-Wno-fatal",
            "--top-module", "RoCCHandshakes", "--Mdir", str(build),
            str(base_tree / "software/rocc-dma/sim/DMAPassiveMonitor.sv"),
            str(Path(__file__).parent / "fixtures/rocc-handshakes.sv"),
        ],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return build / "VRoCCHandshakes"


@pytest.mark.parametrize("scenario,reason", [
    ("immediate-config", None),
    ("immediate-copy", None),
    ("backpressure", None),
    ("turnover", None),
    ("turnover-to-copy", None),
    ("memory-on-command", None),
    ("unsolicited", "RoCC_response_without_command"),
    ("unaccepted", "RoCC_response_without_command"),
    ("no-destination", "RoCC_response_without_command"),
    ("overlap", "overlapping_RoCC_commands"),
    ("backpressured-overlap", "overlapping_RoCC_commands"),
    ("missing-traffic", "successful_COPY_traffic_length_mismatch"),
    ("unacknowledged", "COPY_completed_before_memory_ack"),
])
def test_rocc_handshakes(monitor_binary, scenario, reason, tmp_path):
    result = subprocess.run(
        [str(monitor_binary), "+case=" + scenario],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    output = result.stdout + result.stderr
    if reason:
        assert result.returncode != 0, output
        assert "reason=" + reason in output, output
    else:
        assert result.returncode == 0, output
        assert "ROCC_HANDSHAKES PASS" in output, output
