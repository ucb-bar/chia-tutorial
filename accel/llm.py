"""What goes into and comes out of the LLM: the prompts, the failure
feedback, and parsing / logging its answers.

The LLM itself (one AntigravityLLM per iteration) and its calls live in
accel_loop.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from chia.base.llm_call import QueryResult
from chia.chipyard.state_def import BuildArtifact, RiscvBuildArtifact, RunResult

from results import BODY_RE, TEMPLATE_C_PATH, Design, IterDir, SimResults


def _load_prompt(name: str, **subs: str) -> str:
    """prompts/<name> with ${KEY} placeholders filled (so prompts can hold
    C / Scala braces unescaped)."""
    text = (Path(__file__).resolve().parent / "prompts" / name).read_text()
    for key, val in subs.items():
        text = text.replace("${" + key + "}", val)
    return text


def _truncate(text: str, max_chars: int = 30_000) -> str:
    """The last *max_chars* of *text*, so a long log fits in a prompt."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    return f"... [{len(text) - max_chars} chars cut] ...\n" + text[-max_chars:]


# ---------------------------------------------------------------------------
# LLM answers
# ---------------------------------------------------------------------------


def parse_body(cli: QueryResult) -> str | None:
    """The memcpy_rocc() body in an answer: the LAST marker-delimited block,
    without any ``` fence lines the model put inside it. None if absent."""
    bodies = [m.group(1) for m in BODY_RE.finditer(cli.result or "")]
    if not bodies:
        return None
    lines = [ln for ln in bodies[-1].splitlines() if not ln.strip().startswith("```")]
    return "\n".join(lines) + "\n"


def log_call(out: IterDir, name: str, prompt: str, cli: QueryResult) -> None:
    """Write logs/<timestamp>_<name>.md: the prompt, the answer and the
    transcript."""
    text = f"# {name}\n\nsuccess={cli.success}\n\n"
    if usage := getattr(cli, "usage", None):
        text += f"usage={json.dumps(usage)}\n\n"
    if cli.stderr:
        text += f"stderr:\n```\n{cli.stderr}\n```\n\n"
    text += f"## Prompt\n\n{prompt}\n\n## Answer\n\n{cli.result or ''}\n"
    if cli.stream_result:
        text += f"\n## Transcript\n\n{cli.stream_result}\n"
    out.log(f"{name}.md", text)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def software_prompt(parent: Design | None) -> str:
    if parent is None or parent.name == "baseline":
        ctx = ("## Starting point\n\nNo accelerator exists yet: this is the first "
               "design. The baseline is the software copy above.")
    else:
        diff = _truncate("\n".join(f"# {repo or 'chipyard root'}\n{text}"
                                   for repo, text in parent.diff.items()), 60_000)
        ctx = (f"## Starting point: the best design so far (`{parent.name}`, "
               f"average speedup {parent.avg_speedup:.3f}x)\n\n"
               f"Improve on it: raise the average speedup while keeping every test "
               f"passing. Its software body:\n\n```c\n{parent.body}```\n\n"
               f"Its eval results (speedup = baseline cycles / cycles):\n\n"
               f"```csv\n{parent.eval_csv}```\n\n"
               f"Its chipyard changes (git diff per repo):\n\n```diff\n{diff}```")
    return _load_prompt("software.md", HARNESS=TEMPLATE_C_PATH.read_text(), PARENT=ctx)


def hardware_prompt(parent: Design | None, applied: bool, apply_msg: str = "") -> str:
    if parent is None or parent.name == "baseline" or not any(parent.diff.values()):
        state = f"The chipyard tree is clean: `FastRTLSimRocketConfig` has no accelerator yet."
    elif applied:
        state = (f"The chipyard tree already contains `{parent.name}`'s changes (the diff "
                 "you were shown). Modify or replace that accelerator as your design needs.")
    else:
        state = (f"The chipyard tree is clean: `{parent.name}`'s diff did not apply "
                 f"({apply_msg}). Use it only as a reference.")
    return _load_prompt("hardware.md", TREE_STATE=state)


def debug_prompt(attempt: int, stage: str, feedback: str) -> str:
    return _load_prompt("debug.md", ATTEMPT=str(attempt), STAGE=stage, FEEDBACK=feedback)


# ---------------------------------------------------------------------------
# Failure feedback
# ---------------------------------------------------------------------------


def feedback_no_body() -> str:
    return ("Your answer did not contain a software body between the marker lines, so "
            "there is nothing to compile. Give the full body between the markers.")


def feedback_sw_build(art: RiscvBuildArtifact) -> str:
    return (f"The harness did not cross-compile (rc={art.returncode}).\n\n"
            f"## Compiler stderr\n```\n{_truncate(art.stderr)}\n```\n\n"
            f"## Compiler stdout\n```\n{_truncate(art.stdout, 10_000)}\n```\n")


def feedback_hw_build(art: BuildArtifact) -> str:
    lines = (art.stdout or "").splitlines()
    first = next((i for i, ln in enumerate(lines) if "error" in ln.lower()), None)
    stdout = "\n".join(lines[max(0, first - 5):]) if first is not None else art.stdout
    return (f"The `FastRTLSimRocketConfig` Verilator build failed (rc={art.returncode}).\n\n"
            f"## Build stdout (from the first error)\n```\n{_truncate(stdout)}\n```\n\n"
            f"## Build stderr\n```\n{_truncate(art.stderr, 15_000)}\n```\n")


def _dump_excerpt(dump: str) -> str:
    """memcpy_rocc's disassembly from the objdump (or the dump's tail)."""
    lines = dump.splitlines()
    start = next((i for i, ln in enumerate(lines) if "<memcpy_rocc>:" in ln), None)
    if start is None:
        return "\n".join(lines[-120:])
    return "\n".join(lines[start:start + 120])


def feedback_sim(run: RunResult, res: SimResults, problem: str, dump: str) -> str:
    rows = "\n".join(
        f"{r['name']}: {r['status']} ret={r['ret']} mismatches={r['mismatches']}"
        + (f" bytes={r['bytes']} cycles={r['cycles']}" if "cycles" in r else "")
        for r in res.tests + res.evals) or "(none)"
    return (f"The simulation did not pass: {problem}.\n\n"
            f"A test PASSes when memcpy_rocc returns 1 and `check_copy` finds no "
            f"mismatch (a wrong destination byte, a byte written outside the "
            f"destination, or a changed source byte).\n\n"
            f"## Results so far\n```\n{rows}\n```\n\n"
            f"## Simulator stdout\n```\n{_truncate(run.log)}\n```\n\n"
            f"## Simulator stderr (spike-dasm)\n```\n{_truncate(run.out, 10_000)}\n```\n\n"
            f"## Disassembly of memcpy_rocc\n```\n{_dump_excerpt(dump)}\n```\n")
