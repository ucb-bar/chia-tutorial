"""Read saved loop progress; no Ray connection, model calls, or writes."""
import argparse
import json
from pathlib import Path
import re


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


def report(root):
    print(f"Saved progress: {root}")
    settings = read_json(root / "settings.json")
    if settings is None:
        print("Waiting for settings.json")
        return
    baseline = root / "baseline"
    summary = read_json(baseline / "summary.json") or {}
    print(f"baseline | {summary.get('status', 'incomplete')} | {progress(baseline)}")
    iterations = settings.get("design_iterations_per_agent", settings.get("iterations_per_agent", "?"))
    for index in range(settings["agents"]):
        directory = latest(root / f"agent_{index}", "iter_[0-9]*")
        if directory is None:
            print(f"agent_{index} | not started")
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
        print(" | ".join(parts))
    summary = read_json(root / "run_summary.json")
    if summary is not None:
        best = summary["best"]
        print(f"Finished | {summary['validated_candidates']} validated candidates | best {best['name']} {best['score']:.4f}x")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    if not args.run_directory.is_dir():
        parser.error(f"Run directory not found: {args.run_directory}")
    report(args.run_directory)


if __name__ == "__main__":
    main()
