"""Native CHIA configuration/runner tests: no cloud writes or model calls."""
import copy
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chia.cluster.config import (assign_nodes, build_config, load_raw_config,
                                 parse_gcp_nodes, _expand_node_placeholders)
from chia.cluster.node_setup import build_head_script, build_worker_script
import run as runner


class NativeClusterTests(unittest.TestCase):
    HEAD_ENV = dict(CHIA_HEAD_IP="10.0.0.10", CHIA_HEAD_ZONE="us-east1-b", CHIA_TUT_PROJECT="scale-offline-test")

    def config(self):
        with patch.dict(os.environ, self.HEAD_ENV):
            return load_raw_config(str(ROOT / "cluster.yaml"))

    def test_head_address_project_and_ssh_alias_come_from_each_users_environment(self):
        for ip, zone, project in [("10.0.0.10", "us-east1-b", "student-one"),
                                  ("10.1.2.3", "us-west4-c", "student-two")]:
            with self.subTest(project=project), patch.dict(os.environ, dict(
                    CHIA_HEAD_IP=ip, CHIA_HEAD_ZONE=zone, CHIA_TUT_PROJECT=project)):
                raw = load_raw_config(str(ROOT / "cluster.yaml"))
                self.assertEqual(raw["provider"]["head_ip"], ip)
                self.assertEqual(raw["gcp_nodes"]["project"], project)
                self.assertIn(f"chia-dev.{zone}.{project}", raw["auth"]["ssh_proxy_command"])
                for role in ("riscv_cross", "antigravity"):
                    self.assertEqual(raw["available_node_types"][role]["compatible_ips"], [ip])

    def test_existing_head_is_not_provisioned_as_a_cloud_worker(self):
        raw = self.config()
        nodes, project, zone, network, subnet = parse_gcp_nodes(copy.deepcopy(raw))
        self.assertEqual(set(nodes), {"parallel_gcp", "benchmark_gcp"})
        self.assertEqual(sum(node.count for node in nodes.values()), 6)
        head = raw["provider"]["head_ip"]
        for name in ("riscv_cross", "antigravity"):
            self.assertEqual(raw["available_node_types"][name]["compatible_ips"], [head])
        self.assertNotIn(".runtime", str(raw))
        self.assertNotIn("SCALE_HEAD", str(raw))
        self.assertFalse((ROOT / "up.py").exists())

    def test_native_head_setup_and_all_eight_worker_assignments(self):
        raw = self.config()
        parse_gcp_nodes(raw)
        raw = _expand_node_placeholders(raw, {
            "parallel_gcp": ["10.182.0.21", "10.182.0.22", "10.182.0.23"],
            "benchmark_gcp": ["10.128.0.21", "10.128.0.22", "10.128.0.23"],
        })
        config = build_config(raw)
        assignments = assign_nodes(config)
        self.assertEqual(len(assignments), 8)
        self.assertEqual(len({node.ip for node in assignments}), 7)
        self.assertEqual(config.head_ray_address, f"{config.head_ip}:6395")
        script = "\n".join(build_head_script(config))
        self.assertIn("create -y -n chia_env python=3.10.19", script)
        self.assertIn("python -m pip install 'chialoops==2.0.0'", script)
        self.assertIn("ray start --head --port=6395", script)
        self.assertNotIn("/home/ubuntu", script)
        self.assertNotIn("--num-cpus", script)
        for assignment in assignments:
            worker_script = "\n".join(build_worker_script(config, assignment))
            self.assertNotIn("SCALE_WORKER_CPUS", worker_script)
            self.assertNotIn("--num-cpus", worker_script)
            self.assertIn("--resources=", worker_script)
            self.assertEqual(list(assignment.resources.values()), [1])

    def test_plain_vm_and_worker_counts_agree(self):
        text = (ROOT / "cluster.yaml").read_text()
        self.assertNotRegex(text, r"(?:^|\s)[&*][A-Za-z_]")
        self.assertNotIn("<<:", text)
        raw = self.config()
        for role, group in (("chipyard", "parallel_gcp"), ("verilator_run", "benchmark_gcp")):
            worker = raw["available_node_types"][role]
            self.assertEqual(worker["num_workers"], raw["gcp_nodes"][group]["count"])
            self.assertEqual(len(worker["compatible_ips"]), worker["num_workers"])
        self.assertEqual(raw["available_node_types"]["antigravity"]["resources"], {"antigravity_creds": 1})

    def test_native_up_dry_run_needs_no_cloud_credentials_or_runtime_state(self):
        chia = Path(sys.executable).parent / "chia"
        result = subprocess.run([str(chia), "up", "--dry-run", str(ROOT / "cluster.yaml")],
                                capture_output=True, text=True,
                                env=dict(os.environ, **self.HEAD_ENV, CLOUDSDK_CONFIG="/nonexistent/scale-native-test"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry run complete. No changes made.", result.stderr)
        self.assertNotIn("head_gcp", result.stdout)

    def test_runner_uses_yaml_ssh_auth_and_head_environment(self):
        raw = self.config()
        raw["provider"]["head_ip"] = "10.0.0.42"
        raw["auth"] = dict(ssh_user="student", ssh_private_key="/tmp/native-key",
                           ssh_proxy_command="ssh dev-alias -W %h:%p")
        with patch.object(runner, "load_raw_config", return_value=raw), \
                patch.object(runner.subprocess, "run") as execute, \
                patch.object(sys, "argv", ["scale/run.py", "--agents", "2", "--design-iterations", "3"]):
            runner.main()
        self.assertEqual(execute.call_count, 3)
        mkdir, sync, launch = [call.args[0] for call in execute.call_args_list]
        self.assertIn("student@10.0.0.42", mkdir)
        self.assertIn("ProxyCommand=ssh dev-alias -W %h:%p", mkdir)
        self.assertIn("/tmp/native-key", mkdir)
        self.assertIn("student@10.0.0.42:scale/", sync)
        for excluded in (".runtime/", ".venv/", "out/", "__pycache__/"):
            self.assertIn(f"--exclude={excluded}", sync)
        ssh_command = shlex.split(sync[sync.index("-e") + 1])
        self.assertEqual(ssh_command, mkdir[:-2])
        self.assertIn('export PATH="$HOME/miniforge3/envs/chia_env/bin:$PATH"', launch[-1])
        self.assertIn("cd scale && python accel_loop.py --address 10.0.0.42:6395 --agents 2 --design-iterations 3", launch[-1])
        self.assertNotIn("state.json", str(execute.call_args_list))

    def test_runner_does_not_require_a_jump_host_or_explicit_key(self):
        raw = self.config()
        raw["auth"] = dict(ssh_user="student")
        with patch.object(runner, "load_raw_config", return_value=raw), \
                patch.object(runner.subprocess, "run") as execute, \
                patch.object(sys, "argv", ["scale/run.py", "--preflight"]):
            runner.main()
        command = execute.call_args.args[0]
        self.assertNotIn("-i", command)
        self.assertFalse(any(arg.startswith("ProxyCommand=") for arg in command))
        self.assertTrue(command[-1].endswith("--preflight"))


if __name__ == "__main__":
    unittest.main()
