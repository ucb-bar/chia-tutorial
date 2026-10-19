"""Bootstrap the GCP head, then let CHIA 2.0.0 manage scale/cluster.yaml.

Usage: python scale/up.py [up|down]
Resource sizes/counts live in cluster.yaml; .runtime contains a local SSH key.
No GCP credentials are copied to the VMs. Run with chialoops==2.0.0 installed.
"""

import copy
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys

from google.api_core.exceptions import NotFound
from google.cloud import compute_v1
from chia.cluster.config import load_raw_config, parse_gcp_nodes
from chia.cluster.gcp_nodes import provision_gcp_nodes
from chia.cluster.ssh import SSHClient


def main():
    if version("chialoops") != "2.0.0":
        raise SystemExit("Use the Python environment with chialoops==2.0.0.")
    action = sys.argv[1] if len(sys.argv) > 1 else "up"
    if action not in {"up", "down"}:
        raise SystemExit("Usage: python scale/up.py [up|down]")
    folder = Path(__file__).resolve().parent
    runtime = folder / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    key = runtime / "chia-key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    os.environ["SCALE_SSH_KEY"] = str(key)
    sdk_config = Path.home() / ".chia-tutorial/gcloud-config"
    if sdk_config.exists():
        os.environ.setdefault("CLOUDSDK_CONFIG", str(sdk_config))

    # Addresses are resolved below, before CHIA reads this configuration again.
    os.environ.setdefault("SCALE_HEAD_IP", "127.0.0.1")
    os.environ.setdefault("SCALE_HEAD_PUBLIC_IP", "127.0.0.1")
    config_path = folder / "cluster.yaml"
    raw = load_raw_config(str(config_path))
    nodes, project, zone, network, subnet = parse_gcp_nodes(copy.deepcopy(raw))
    head_cfg = nodes["head_gcp"]
    head_zone = head_cfg.zone or zone
    head_name = f"chia-{raw['cluster_name']}-head-gcp-0"
    client = compute_v1.InstancesClient()
    try:
        head = client.get(project=project, zone=head_zone, instance=head_name)
    except NotFound:
        if action == "down":
            raise SystemExit("Head VM is absent; inspect remaining chia-cluster=scale VMs before cleanup.")
        print(f"Creating head {head_name} in {head_zone} ...", flush=True)
        provision_gcp_nodes(raw["cluster_name"], {"head_gcp": head_cfg}, project,
                            zone, network, subnet)
        head = client.get(project=project, zone=head_zone, instance=head_name)
    if head.machine_type.rsplit("/", 1)[-1] != head_cfg.machine_type:
        raise SystemExit("Existing head has a different VM size; refusing to overwrite it.")
    nic = head.network_interfaces[0]
    os.environ["SCALE_HEAD_IP"] = nic.network_i_p
    os.environ["SCALE_HEAD_PUBLIC_IP"] = nic.access_configs[0].nat_i_p
    state = dict(project=project, head=head_name, zone=head_zone,
                 private_ip=nic.network_i_p, public_ip=nic.access_configs[0].nat_i_p)
    (runtime / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    print(f"Head: {state['public_ip']} (VPC {state['private_ip']})", flush=True)

    if action == "up":
        ssh = SSHClient(state["public_ip"], head_cfg.ssh_user, str(key), identities_only=True)
        ssh.wait_for_ssh(timeout=head_cfg.ssh_timeout)
        print("Installing head dependencies (first boot can take several minutes) ...", flush=True)
        ssh.run_script(head_cfg.setup_commands, timeout=head_cfg.setup_timeout)
        raw = load_raw_config(str(config_path))
        environment = raw["head_env_commands"]
        status = ssh.run_script(environment + [f"timeout 10 ray status --address={nic.network_i_p}:6395"], check=False)
        if status.returncode:
            ssh.run_script(environment + raw["head_start_ray_commands"])
        print("Starting workers through CHIA 2.0.0 ...", flush=True)

    command = [str(Path(sys.executable).parent / "chia"), action]
    if action == "up":
        command += ["--add"]
    command += ["--yes", str(config_path)]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
