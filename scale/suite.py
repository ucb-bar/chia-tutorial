"""Frozen upstream vectors, quick/full profiles, and strict result checking."""
import csv
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
BODY_RE = re.compile(r"// ===== memcpy_rocc body: BEGIN =====\n(.*?)^[ \t]*// ===== memcpy_rocc body: END =====", re.DOTALL | re.MULTILINE)


@dataclass(frozen=True)
class Case:
    name: str
    kind: str
    length: int
    src_offset: int
    dst_offset: int
    reverse: int
    upstream: str


def category(case):
    """Keep size bands, byte tails, alignment, order, and page crossings."""
    return (case.length.bit_length(), case.length % 8,
            bool(case.src_offset % 8), bool(case.dst_offset % 8), case.reverse,
            case.src_offset + case.length > 4096, case.dst_offset + case.length > 4096)


def representative(cases, count):
    if count >= len(cases):
        return cases
    buckets = defaultdict(list)
    for case in sorted(cases, key=lambda c: (c.length, c.src_offset, c.dst_offset, c.reverse)):
        buckets[category(case)].append(case)
    # One from each category before taking extras; alternate bucket endpoints.
    selected, turn = set(), 0
    while len(selected) < count:
        for key in sorted(buckets):
            group = buckets[key]
            if turn < len(group):
                index = turn // 2 if turn % 2 == 0 else len(group) - 1 - turn // 2
                selected.add(group[index])
                if len(selected) == count:
                    break
        turn += 1
    return [case for case in cases if case in selected]


def load_cases(settings, profile="full"):
    lock = json.loads((ROOT / "benchmarks/sources.json").read_text())
    for name, digest in lock["sha256"].items():
        assert hashlib.sha256((ROOT / "benchmarks/upstream" / name).read_bytes()).hexdigest() == digest, name
    content = (ROOT / "benchmarks/cases.csv").read_bytes()
    assert hashlib.sha256(content).hexdigest() == lock["cases_sha256"], "case manifest changed"
    cases = []
    for row in csv.DictReader(content.decode().splitlines()):
        for field in ("length", "src_offset", "dst_offset", "reverse"):
            row[field] = int(row[field])
        if row["length"] <= settings["max_case_bytes"]:
            cases.append(Case(**row))
    if profile == "all":
        return cases
    limit = settings.get("test_cases", 0)
    if limit and limit < len(cases):
        groups = {kind: [c for c in cases if c.kind == kind] for kind in ("test", "eval")}
        counts = {kind: min(len(group), limit // 2) for kind, group in groups.items()}
        for kind, group in groups.items():
            counts[kind] += min(len(group) - counts[kind], limit - sum(counts.values()))
        chosen = {case for kind, group in groups.items() for case in representative(group, counts[kind])}
        cases = [case for case in cases if case in chosen]
    if profile == "full":
        return cases  # Entire CONFIGURED suite, which can be a disclosed subset.
    if profile != "quick":
        raise ValueError(profile)
    selected = []
    for kind in ("test", "eval"):
        group = sorted((c for c in cases if c.kind == kind), key=lambda c: (c.length, c.src_offset, c.dst_offset, c.reverse))
        count = min(settings["quick_cases_per_kind"], len(group))
        if count < 2:
            raise ValueError("quick_cases_per_kind must be >= 2")
        selected.extend(group[round(i * (len(group) - 1) / (count - 1))] for i in range(count))
    return selected


def body_from(source):
    bodies = list(BODY_RE.finditer(source or ""))
    if not bodies:
        raise ValueError("Missing memcpy_rocc body markers")
    return "\n".join(line for line in bodies[-1][1].splitlines() if not line.strip().startswith("```")) + "\n"


def body_code(body):
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", body, flags=re.DOTALL)


def validate_offload(body):
    code = body_code(body)
    if not re.search(r"\bROCC_INSTRUCTION_[A-Z_]+\s*\(", code):
        raise ValueError("Candidate needs an actual RoCC software interface")
    if re.search(r"\b(?:memcpy|memmove|bcopy|__builtin_memcpy|__builtin_memmove)\s*\(", code):
        raise ValueError("Software memcpy/memmove fallback is not an accelerator: implement data movement in hardware, not a dummy RoCC instruction followed by a CPU copy")


def render(body, cases, settings):
    # Keep the generated function body from redefining/escaping the frozen harness.
    code = body_code(body)
    if "#" in code:
        raise ValueError("Preprocessor directives are not allowed in the software body")
    depth = 0
    for char in code:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            raise ValueError("Software body escapes its function")
    if depth:
        raise ValueError("Software body has unbalanced braces")
    source = (ROOT / "memcpy_rocc.c").read_text()
    match = BODY_RE.search(source)
    source = source[:match.start(1)] + body.rstrip() + "\n" + source[match.end(1):]
    rows = [f'{{"{c.name}",{int(c.kind == "eval")},{c.length},{c.src_offset},{c.dst_offset},{c.reverse}}}' for c in cases]
    source = source.replace("/* CASE_TABLE */", ",\n".join(rows))
    return source.replace("/* MAX_BYTES */", str(max(c.length for c in cases))).replace("/* REPEATS */", str(settings["benchmark_repeats"])).replace("/* WARMUPS */", str(settings["benchmark_warmups"]))


RESULT = re.compile(r"^(TEST|EVAL) (\w+) (PASS|FAIL) bytes=(\d+) cycles=(\d+) ret=(-?\d+) mismatches=(\d+)$", re.MULTILINE)


def parse_results(cases, log, returncode):
    if returncode != 0 or not re.search(r"^DONE\s*$", log, re.MULTILINE):
        raise ValueError(f"Simulator incomplete or failed (rc={returncode})")
    rows = []
    expected = {c.name: c for c in cases}
    for match in RESULT.finditer(log):
        kind, name, status, size, cycles, ret, mismatches = match.groups()
        if name not in expected or any(r["name"] == name for r in rows):
            raise ValueError(f"Unexpected/duplicate case: {name}")
        case = expected[name]
        if kind != case.kind.upper() or int(size) != case.length or status != "PASS" or int(ret) != 1 or int(mismatches) != 0:
            raise ValueError(f"Incorrect case: {match[0]}")
        if case.kind == "eval" and int(cycles) <= 0:
            raise ValueError(f"Nonpositive cycles: {name}")
        rows.append(dict(name=name, kind=case.kind, bytes=int(size), cycles=int(cycles), status=status))
    if len(rows) != len(cases):
        raise ValueError(f"Missing results: {len(rows)} / {len(cases)}")
    return rows


def score(rows, baseline):
    current = {r["name"]: r["cycles"] for r in rows if r["kind"] == "eval"}
    if current.keys() != baseline.keys() or not current:
        raise ValueError("Candidate and baseline benchmark sets differ")
    ratios = [baseline[name] / cycles for name, cycles in current.items() if cycles > 0]
    if len(ratios) != len(current) or any(value <= 0 for value in ratios):
        raise ValueError("Invalid benchmark cycles")
    return sum(ratios) / len(ratios)
