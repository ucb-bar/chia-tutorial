"""Ephemeral localhost SSH for testing the real CHIA CLI; all files stay in discovery/."""

import os
from pathlib import Path
import pwd
import socket
import subprocess
import time
import sys
import uuid
import pytest
from chia_dma.config_loader import ROOT, load


@pytest.fixture(scope="module")
def native_ssh():
    folder = ROOT / "experiments/refactor-checks" / ("ssh-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    for key in ("host", "client"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(folder / key)],
            check=True,
        )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = folder / "sshd_config"
    config.write_text(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {folder/'host'}
PidFile {folder/'sshd.pid'}
AuthorizedKeysFile {folder/'client.pub'}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowUsers {pwd.getpwuid(os.getuid()).pw_name}
LogLevel ERROR
""")
    log = (folder / "sshd.log").open("w")
    proc = subprocess.Popen(
        ["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError((folder / "sshd.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        configuration = load()
        configuration["cluster"]["provider"]["head_ip"] = "127.0.0.1"
        configuration["cluster"]["head_env_commands"] = [
            f'source "$(conda info --base)/etc/profile.d/conda.sh" && conda activate {sys.prefix}',
            f"export PYTHONPATH={ROOT}",
        ]
        for worker in configuration["cluster"]["available_node_types"].values():
            worker["compatible_ips"] = ["127.0.0.1"]
        # Local integration tests emulate the VM pool with separate containers.
        configuration["cluster"]["available_node_types"]["simulation"]["num_workers"] = 1
        for worker in configuration["cluster"]["available_node_types"].values():
            worker["worker_env_commands"].append("export PYTHONPATH=$PYTHONPATH:/tmp/dma-python")
            worker.setdefault("worker_setup_commands", []).append(
                "python -m pip install --target /tmp/dma-python pytest==9.1.1"
            )
        with socket.socket() as ray_socket, socket.socket() as dashboard_socket:
            ray_socket.bind(("127.0.0.1", 0))
            dashboard_socket.bind(("127.0.0.1", 0))
            ray_port = ray_socket.getsockname()[1]
            dashboard_port = dashboard_socket.getsockname()[1]
        configuration["runtime"]["ray_address"] = f"127.0.0.1:{ray_port}"
        for key in ("head_start_ray_commands", "worker_start_ray_commands"):
            configuration["cluster"][key] = [
                command.replace("6387", str(ray_port)).replace(
                    "8287", str(dashboard_port)
                )
                for command in configuration["cluster"][key]
            ]
        configuration["cluster"]["auth"] = {
            "ssh_user": pwd.getpwuid(os.getuid()).pw_name,
            "ssh_private_key": str(folder / "client"),
            "ssh_proxy_command": f"nc 127.0.0.1 {port}",
        }
        yield configuration, folder
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()
