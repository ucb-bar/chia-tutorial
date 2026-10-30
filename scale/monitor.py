"""Watch the running loop's live status monitor, locally or over SSH from the configured head; no writes.

accel_loop.py reports one row per agent (plus run, baseline and best) into CHIA's
live status monitor actor; this script reads that actor and prints its table.
The actor lives as long as the loop's driver, so for a finished run use status.py.
"""
import argparse
from pathlib import Path
import re
import shlex
import subprocess
import time

MONITOR = "scale"         # accel_loop.MONITOR
NAMESPACE = "chia-scale"  # accel_loop's ray.init namespace; the monitor actor is named within it


def watch_local(address, interval, once):
    import ray
    from chia.trace import live_status_monitor

    ray.init(address=address, namespace=NAMESPACE, log_to_driver=False, logging_level="ERROR")
    waiting = None
    try:
        while True:
            try:
                handle = ray.get_actor(live_status_monitor._NAME)
                rows = ray.get(handle.rows.remote(MONITOR))
            except (ValueError, ray.exceptions.RayActorError):
                rows = None  # No driver is running, or it just shut down.
            if rows:
                print(live_status_monitor._render(MONITOR, rows) + "\n", flush=True)
                waiting = None
            elif waiting is None or once:
                waiting = "no rows yet" if rows is not None else "no running loop"
                print(f"Waiting: {waiting}. Start one with 'python run.py'; "
                      "for a finished run use 'python status.py out/run_...'.", flush=True)
            if once:
                raise SystemExit(0 if rows else 1)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        ray.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=10, help="Seconds between tables (default 10)")
    parser.add_argument("--once", action="store_true", help="Print one table and exit")
    parser.add_argument("--local", action="store_true", help="Connect to Ray from this machine; do not SSH to the head")
    parser.add_argument("--address", help="Ray address (default: the head in cluster.yaml)")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    try:
        from chia.cluster.config import build_config, load_raw_config
    except ImportError:
        parser.error("CHIA is not installed here. Run 'source load-env.sh' first.")
    if args.local:
        address = args.address
        if address is None:
            address = build_config(load_raw_config(str(Path(__file__).with_name("cluster.yaml")))).head_ray_address
        watch_local(address, args.interval, args.once)
        return
    config = build_config(load_raw_config(str(Path(__file__).with_name("cluster.yaml"))))
    auth = config.get_ssh_auth(config.head_ip)
    missing = sorted(set(re.findall(r"\$\{([^}]+)\}", " ".join(filter(None, [
        config.head_ip, auth.ssh_user, auth.ssh_private_key, auth.ssh_proxy_command])))))
    if missing or not config.head_ip or re.search(r"\s", config.head_ip):
        parser.error(f"Head SSH environment is missing or invalid ({', '.join(missing) or 'head_ip'}). "
                     "In this terminal, run: source load-env.sh; then rerun this command.")
    ssh = ["ssh", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"]
    if auth.ssh_private_key:
        ssh += ["-i", str(Path(auth.ssh_private_key).expanduser())]
    if auth.ssh_proxy_command:
        ssh += ["-o", f"ProxyCommand={auth.ssh_proxy_command}"]
    remote = ["python", "-", "--local", "--address", args.address or config.head_ray_address,
              "--interval", str(args.interval), *(["--once"] if args.once else [])]
    command = " && ".join([*config.head_env_commands, "cd ~/scale", shlex.join(remote)])
    # Stream this script over stdin, as status.py does: nothing synced, the running loop untouched.
    try:
        result = subprocess.run(ssh + [f"{auth.ssh_user}@{config.head_ip}", command],
                                input=Path(__file__).read_text(), text=True)
    except KeyboardInterrupt:
        raise SystemExit(130)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
