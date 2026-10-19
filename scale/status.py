"""Read saved loop progress locally or over SSH from the configured head; no writes."""
import argparse
import json
import math
from pathlib import Path
import re
import shlex
import subprocess


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None  # A running driver may still be writing the file.


def latest(directory, pattern):
    return max(directory.glob(pattern), key=lambda p: int(p.name.rsplit("_", 1)[1]), default=None)


def progress(directory):
    for profile in ("full", "quick"):
        target = directory / profile
        groups = read_json(target / "batches.json")
        if groups is None:
            continue
        expected = {name for group in groups for name in group}
        passed, failed = set(), set()
        for log in target.glob("batch_*.log"):
            for name, result in re.findall(r"^(?:TEST|EVAL) (\S+) (PASS|FAIL) [^\r\n]*\r?\n",
                                           log.read_text(), re.MULTILINE):
                if name in expected:
                    (passed if result == "PASS" else failed).add(name)
        return (f"{profile} {len(passed | failed)}/{len(expected)} cases reported "
                f"({len(passed - failed)} pass, {len(failed)} fail)")
    return "no case results yet"


def speedup(directory, baseline):
    for profile in ("full", "quick"):
        rows = read_json(directory / profile / "results.json")
        if not rows or any(row["status"] != "PASS" for row in rows):
            continue
        current = {row["name"]: row["cycles"] for row in rows if row["kind"] == "eval"}
        if not current or not current.keys() <= baseline.keys():
            continue
        if profile == "full" and current.keys() != baseline.keys():
            continue
        if any(not math.isfinite(cycles) or cycles <= 0 or
               not math.isfinite(baseline[name]) or baseline[name] <= 0 for name, cycles in current.items()):
            continue
        value = math.exp(math.fsum(math.log(baseline[name]) - math.log(cycles)
                                   for name, cycles in current.items()) / len(current))
        note = "; preliminary" if profile == "quick" else ""
        return f"{profile} geomean {value:.4f}x ({len(current)} benchmarks{note})"
    return ""


def report(root):
    print(f"Saved progress: {root}")
    settings = read_json(root / "settings.json")
    if settings is None:
        print("Waiting for settings.json")
        return
    baseline = root / "baseline"
    summary = read_json(baseline / "summary.json") or {}
    print(f"baseline | {summary.get('status', 'incomplete')} | {progress(baseline)}")
    baseline_cycles = {row["name"]: row["cycles"] for row in read_json(baseline / "full/results.json") or []
                       if row["kind"] == "eval" and row["status"] == "PASS"}
    iterations = settings.get("design_iterations_per_agent", settings.get("iterations_per_agent", "?"))
    for index in range(settings["agents"]):
        directory = latest(root / f"agent_{index}", "iter_[0-9]*")
        if directory is None:
            state = "waiting for builder" if (root / f"agent_{index}/waiting_for_builder").exists() else "not started"
            print(f"agent_{index} | {state}")
            continue
        iteration = int(directory.name.split("_")[-1]) + 1
        parts = [f"agent_{index}", f"iteration {iteration}/{iterations}"]
        summary = read_json(directory / "summary.json") or {}
        state = summary.get("status", "incomplete")
        if not summary:
            for prompt in directory.glob("*.txt"):
                if prompt.read_text().endswith("\n\nPENDING\n"):
                    state = f"model {prompt.stem} pending"
        parts.append(state)
        attempt = latest(directory, "attempt_[0-9]*")
        if attempt is not None:
            number = int(attempt.name.split("_")[-1]) + 1
            parts.extend([f"attempt {number}/{settings['max_attempts']}", progress(attempt)])
            value = speedup(attempt, baseline_cycles)
            if value:
                parts.append(value)
        print(" | ".join(parts))
    summary = read_json(root / "run_summary.json")
    if summary is not None:
        best = summary["best"]
        value = speedup(Path(best["directory"]), baseline_cycles) if best.get("directory") else ""
        print(f"Finished | {summary['validated_candidates']} validated candidates | best {best['name']} "
              f"{best['score']:.4f}x (arithmetic mean)" + (f" | {value}" if value else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--local", action="store_true", help="Read local files only; do not connect to the head")
    args = parser.parse_args()
    if args.run_directory.is_dir():
        report(args.run_directory)
    elif args.local:
        parser.error(f"Run directory not found: {args.run_directory}")
    else:
        try:
            from chia.cluster.config import build_config, load_raw_config
        except ImportError:
            parser.error("Run directory is not local. Run 'source load-env.sh' to enable SSH status from the head.")
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
        command = " && ".join([*config.head_env_commands, "cd ~/scale",
                               shlex.join(["python", "-", "--local", str(args.run_directory)])])
        # Stream this script over stdin: no syncing files or changing the running loop.
        result = subprocess.run(ssh + [f"{auth.ssh_user}@{config.head_ip}", command],
                                input=Path(__file__).read_text(), text=True)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
