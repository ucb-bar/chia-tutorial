from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# accel/out/iter_<n>/ per iteration; the software-only reference design in
# accel/out/baseline/. The harness template sits next to this file.
OUT_DIR = Path(__file__).resolve().parent / "out"
BASELINE_DIR = OUT_DIR / "baseline"
TEMPLATE_C_PATH = Path(__file__).resolve().parent / "memcpy_rocc.c"

# ---------------------------------------------------------------------------
# Harness source
# ---------------------------------------------------------------------------

# The memcpy_rocc() body: everything between the BEGIN line and the END line
# (group 1), in the harness and in LLM answers.
BODY_RE = re.compile(r"// ===== memcpy_rocc body: BEGIN =====\n(.*?)^[ \t]*"
                     r"// ===== memcpy_rocc body: END =====", re.DOTALL | re.MULTILINE)


def read_body(source: str) -> str:
    """The memcpy_rocc() body between the markers of a harness source."""
    m = BODY_RE.search(source)
    if not m:
        raise ValueError("memcpy_rocc body markers not found")
    return m.group(1)


def render_source(body: str) -> str:
    """The harness template with *body* in place of its memcpy_rocc() body."""
    template = TEMPLATE_C_PATH.read_text()
    m = BODY_RE.search(template)
    body = body if body.endswith("\n") else body + "\n"
    return template[:m.start(1)] + body + template[m.end(1):]


# ---------------------------------------------------------------------------
# Harness output
# ---------------------------------------------------------------------------

_PLAN_RE = re.compile(r"^PLAN tests=(\d+) evals=(\d+)", re.MULTILINE)
_TEST_RE = re.compile(r"^TEST (\S+) (PASS|FAIL) ret=(-?\d+) mismatches=(\d+)", re.MULTILINE)
_EVAL_RE = re.compile(
    r"^EVAL (\S+) (PASS|FAIL) bytes=(\d+) cycles=(\d+) ret=(-?\d+) mismatches=(\d+)", re.MULTILINE)


@dataclass
class SimResults:
    tests: list[dict] = field(default_factory=list)
    evals: list[dict] = field(default_factory=list)
    planned_tests: int | None = None
    planned_evals: int | None = None
    done: bool = False

    @property
    def failed_tests(self) -> list[str]:
        return [t["name"] for t in self.tests if t["status"] != "PASS"]

    @property
    def failed_evals(self) -> list[str]:
        return [e["name"] for e in self.evals if e["status"] != "PASS"]

    def problem(self, returncode: int) -> str | None:
        """Why this run does not count as correct, or None if it does."""
        if returncode != 0:
            return (f"simulator exited with rc={returncode} (a nonzero rc after the "
                    "cycle budget means the program hung)")
        if self.planned_tests is None:
            return "the program never printed its PLAN line"
        if not self.done:
            return "the program never reached DONE"
        if len(self.tests) != self.planned_tests or len(self.evals) != self.planned_evals:
            return (f"expected {self.planned_tests} TEST and {self.planned_evals} EVAL lines, "
                    f"got {len(self.tests)} and {len(self.evals)}")
        if self.failed_tests or self.failed_evals:
            return "failing: " + ", ".join(self.failed_tests + self.failed_evals)
        return None


def parse_sim_output(text: str) -> SimResults:
    res = SimResults(done=bool(re.search(r"^DONE\s*$", text, re.MULTILINE)))
    if m := _PLAN_RE.search(text):
        res.planned_tests, res.planned_evals = int(m.group(1)), int(m.group(2))
    for m in _TEST_RE.finditer(text):
        res.tests.append({"name": m.group(1), "status": m.group(2),
                          "ret": int(m.group(3)), "mismatches": int(m.group(4))})
    for m in _EVAL_RE.finditer(text):
        res.evals.append({"name": m.group(1), "status": m.group(2), "bytes": int(m.group(3)),
                          "cycles": int(m.group(4)), "ret": int(m.group(5)),
                          "mismatches": int(m.group(6))})
    return res


# ---------------------------------------------------------------------------
# Scoring and CSVs
# ---------------------------------------------------------------------------


def baseline_cycles() -> dict[str, int]:
    """Eval name -> baseline cycles (empty before the baseline exists)."""
    path = BASELINE_DIR / "eval.csv"
    if not path.exists():
        return {}
    with open(path) as f:
        return {r["name"]: int(r["cycles"]) for r in csv.DictReader(f)
                if r["name"] != "average" and r["cycles"]}


def score_evals(evals: list[dict], baseline: dict[str, int]) -> tuple[list[dict], float | None]:
    """Add bytes_per_cycle and speedup (baseline cycles / cycles) to each eval.
    Returns the rows and their mean speedup (None without a baseline)."""
    rows, speedups = [], []
    for e in evals:
        row = dict(e)
        row["bytes_per_cycle"] = round(e["bytes"] / e["cycles"], 4) if e["cycles"] else 0.0
        base = baseline.get(e["name"]) if baseline else None
        row["speedup"] = round(base / e["cycles"], 4) if base and e["cycles"] else None
        if row["speedup"] is not None:
            speedups.append(row["speedup"])
        rows.append(row)
    avg = round(sum(speedups) / len(speedups), 4) if speedups and len(speedups) == len(evals) else None
    return rows, avg


def write_tests_csv(path: Path, tests: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "status", "ret", "mismatches"])
        w.writeheader()
        w.writerows(tests)


EVAL_FIELDS = ["name", "status", "bytes", "cycles", "bytes_per_cycle", "speedup"]


def write_eval_csv(path: Path, rows: list[dict], avg_speedup: float | None) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EVAL_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        w.writerow({"name": "average", "speedup": "" if avg_speedup is None else avg_speedup})


# ---------------------------------------------------------------------------
# accel/out bookkeeping
# ---------------------------------------------------------------------------


class IterDir:
    """One iteration's output directory."""

    def __init__(self, path: Path):
        self.path = path
        self.logs = path / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def log(self, name: str, content: str | bytes) -> Path:
        """Write logs/<timestamp>_<name>."""
        p = self.logs / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content or "", errors="replace")
        return p

    def write_json(self, name: str, obj) -> None:
        (self.path / name).write_text(json.dumps(obj, indent=2) + "\n")

    def write_text(self, name: str, text: str) -> None:
        (self.path / name).write_text(text)


def write_diff_json(path: Path, diffs: dict[str, str]) -> None:
    """Write {repo: diff text} with each diff as a list of its lines, so the
    file reads like a diff (one line per line) instead of one escaped string."""
    as_lines = {repo: text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
                for repo, text in diffs.items() if text}
    path.write_text(json.dumps(as_lines, indent=2) + "\n")


def read_diff_json(path: Path) -> dict[str, str]:
    """{repo: diff text} from a diff.json written by write_diff_json."""
    return {repo: "\n".join(lines) + "\n" for repo, lines in json.loads(path.read_text()).items()}


def next_iter_dir() -> IterDir:
    nums = [int(m.group(1)) for p in OUT_DIR.glob("iter_*")
            if (m := re.fullmatch(r"iter_(\d+)", p.name))]
    return IterDir(OUT_DIR / f"iter_{max(nums, default=0) + 1}")


@dataclass
class Design:
    """A finished design in accel/out that an iteration can start from."""
    name: str
    path: Path
    avg_speedup: float

    @property
    def diff(self) -> dict[str, str]:
        p = self.path / "diff.json"
        return read_diff_json(p) if p.exists() else {}

    @property
    def body(self) -> str:
        return read_body((self.path / "memcpy_rocc.c").read_text())

    @property
    def eval_csv(self) -> str:
        return (self.path / "eval.csv").read_text()


def best_design() -> Design | None:
    """The passing design in accel/out with the highest average speedup
    (the baseline counts, at 1.0)."""
    best = None
    for summary_path in OUT_DIR.glob("*/summary.json"):
        s = json.loads(summary_path.read_text())
        if s.get("status") != "passed" or s.get("avg_speedup") is None:
            continue
        d = Design(summary_path.parent.name, summary_path.parent, float(s["avg_speedup"]))
        if best is None or d.avg_speedup > best.avg_speedup:
            best = d
    return best
