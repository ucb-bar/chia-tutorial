"""Sync scale/ to the head configured in cluster.yaml and run the loop.

Bring up the cluster with `chia up scale/cluster.yaml`; no runtime state file
or custom cloud bootstrap is needed. Credentials are never copied to the head.

Examples: python scale/run.py --baseline-only
          python scale/run.py --design-iterations 2
"""
from pathlib import Path
import shlex
import subprocess
import sys

from chia.cluster.config import build_config, load_raw_config


def main():
    folder = Path(__file__).resolve().parent
    config = build_config(load_raw_config(str(folder / "cluster.yaml")))
    auth = config.get_ssh_auth(config.head_ip)
    ssh = ["ssh", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"]
    if auth.ssh_private_key:
        ssh += ["-i", str(Path(auth.ssh_private_key).expanduser())]
    if auth.ssh_proxy_command:
        ssh += ["-o", f"ProxyCommand={auth.ssh_proxy_command}"]
    host = f"{auth.ssh_user}@{config.head_ip}"
    destination = "scale"  # Relative to the existing head user's home.
    subprocess.run(ssh + [host, f"mkdir -p {destination}"], check=True)
    subprocess.run(["rsync", "-az", "--exclude=.runtime/", "--exclude=.venv/", "--exclude=out/", "--exclude=__pycache__/",
                    "-e", shlex.join(ssh), str(folder) + "/", f"{host}:{destination}/"], check=True)
    command = " && ".join([*config.head_env_commands, f"cd {destination}",
                           shlex.join(["python", "accel_loop.py", "--address", config.head_ray_address, *sys.argv[1:]])])
    subprocess.run(ssh + [host, command], check=True)


if __name__ == "__main__":
    main()
