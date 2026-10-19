"""Sync scale/ to the existing head and run the loop, without copying secrets.

Examples: python scale/run.py --preflight
          python scale/run.py --baseline-only
          python scale/run.py --iterations 2
"""
import json
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    folder = Path(__file__).resolve().parent
    state = json.loads((folder / ".runtime/state.json").read_text())
    key = folder / ".runtime/chia-key"
    ssh = ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"]
    host = f"ubuntu@{state['public_ip']}"
    destination = "/home/ubuntu/scale"
    subprocess.run(ssh + [host, f"mkdir -p {destination}"], check=True)
    subprocess.run(["rsync", "-az", "--exclude=.runtime/", "--exclude=.venv/", "--exclude=out/", "--exclude=__pycache__/",
                    "-e", shlex.join(ssh), str(folder) + "/", f"{host}:{destination}/"], check=True)
    python = "/home/ubuntu/miniforge3/envs/chia_env/bin/python"
    command = f"cd {destination} && " + shlex.join([python, "accel_loop.py", "--address", f"{state['private_ip']}:6395", *sys.argv[1:]])
    subprocess.run(ssh + [host, command], check=True)


if __name__ == "__main__":
    main()
