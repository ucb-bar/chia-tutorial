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
# Cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One test or eval: the harness compiled with this case's -D flags is its
    own program."""
    name: str       # a C identifier; the harness stringifies it
    kind: str       # "test" | "eval"
    len: int
    src_off: int    # byte offset from a 64-byte-aligned address
    dst_off: int
    seed: int

    @property
    def cflags(self) -> str:
        return (f"-DCASE_NAME={self.name} -DCASE_IS_EVAL={int(self.kind == 'eval')} "
                f"-DCASE_LEN={self.len} -DCASE_SRC_OFF={self.src_off} "
                f"-DCASE_DST_OFF={self.dst_off} -DCASE_SEED={self.seed}")


_TESTS = [  # name, len, src_off, dst_off
    ("zero_bytes",           0,    0, 0),
    ("one_byte",             1,    0, 0),
    ("seven_bytes",          7,    0, 0),
    ("one_word",             8,    0, 0),
    ("one_word_src_unalign", 8,    3, 0),
    ("one_word_dst_unalign", 8,    0, 5),
    ("nine_bytes",           9,    0, 0),
    ("same_misalign",        24,   3, 3),
    ("diff_misalign",        61,   1, 6),
    ("cache_line",           64,   0, 0),
    ("cache_line_plus_one",  65,   0, 0),
    ("odd_large",            1021, 7, 2),
]
_EVALS = [
    ("copy_256B",            256,   0, 0),
    ("copy_1KiB",            1024,  0, 0),
    ("copy_4KiB",            4096,  0, 0),
    ("copy_16KiB",           16384, 0, 0),
    ("copy_4KiB_unaligned",  4093,  3, 5),
]

# Every test, then every eval. Tests are seeded by index, evals by 100 + index.
CASES = ([Case(n, "test", *c, seed=i) for i, (n, *c) in enumerate(_TESTS)]
         + [Case(n, "eval", *c, seed=100 + i) for i, (n, *c) in enumerate(_EVALS)])


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

_TEST_RE = re.compile(r"^TEST (\S+) (PASS|FAIL) ret=(-?\d+) mismatches=(\d+)", re.MULTILINE)
_EVAL_RE = re.compile(
    r"^EVAL (\S+) (PASS|FAIL) bytes=(\d+) cycles=(\d+) ret=(-?\d+) mismatches=(\d+)", re.MULTILINE)


@dataclass
class SimResults:
    """Every case's result line, and why each case that does not count as
    correct does not."""
    tests: list[dict] = field(default_factory=list)
    evals: list[dict] = field(default_factory=list)
    problems: dict[str, str] = field(default_factory=dict)

    def add(self, case: Case, text: str, returncode: int) -> None:
        """Parse one case's simulator output into its result line, or a
        problem when the run did not produce a correct one."""
        if case.kind == "test":
            m = _TEST_RE.search(text)
            row = m and {"name": m.group(1), "status": m.group(2),
                         "ret": int(m.group(3)), "mismatches": int(m.group(4))}
        else:
            m = _EVAL_RE.search(text)
            row = m and {"name": m.group(1), "status": m.group(2), "bytes": int(m.group(3)),
                         "cycles": int(m.group(4)), "ret": int(m.group(5)),
                         "mismatches": int(m.group(6))}
        if row:
            (self.tests if case.kind == "test" else self.evals).append(row)

        if returncode != 0:
            self.problems[case.name] = (f"simulator exited with rc={returncode} (a nonzero rc "
                                        "after the cycle budget means the program hung)")
        elif not row:
            self.problems[case.name] = f"the program never printed its {case.kind.upper()} line"
        elif not re.search(r"^DONE\s*$", text, re.MULTILINE):
            self.problems[case.name] = "the program never reached DONE"
        elif row["status"] != "PASS":
            self.problems[case.name] = f"FAIL ret={row['ret']} mismatches={row['mismatches']}"

    def problem(self) -> str | None:
        """Why this run does not count as correct, or None if it does."""
        if not self.problems:
            return None
        return "failing: " + "; ".join(f"{name} ({why})" for name, why in self.problems.items())


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
