"""RoCC memcpy accelerator: an agentic hardware/software co-design loop.

Flow
----

    baseline (once, no LLM): clean chipyard + the harness's software memcpy
        -> accel/out/baseline/   (the cycles every speedup is measured against)

    each iteration -> accel/out/iter_<n>/
        pick the best passing design in accel/out (highest average speedup)
        reset chipyard; apply its diff.json
        one Antigravity conversation, with chipyard_bash:
          1. software write   memcpy_rocc() body       (prompts/software.md)
          2. hardware write   RoCC accelerator, wired  (prompts/hardware.md)
                              into FastRTLSimRocketConfig
        attempt loop (4 debug calls shared by every stage):
          compile the harness  (RiscvBuildNode, chia-riscv-cross)
          build the simulator  (ChiselBuildNode, 4 Verilator threads;
                                skipped when the chipyard diff is unchanged)
          run the harness      (VerilatorRunNode)
          any failure -> debug (prompts/debug.md, same conversation) -> retry
        write diff.json, memcpy_rocc.c, tests.csv, eval.csv, summary.json

Run from the head (absolute path; see README.md)::

    chia job submit -- python "$(pwd)/accel/accel_loop.py" --iterations 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

import chia
import ray

from chia.base.tools.BashTool import BashTool
from chia.chipyard.state_def import BuildArtifact, RiscvBuildArtifact, RunResult
from chia.models.antigravity import AntigravityLLM

import llm as L
from nodes import (
    build_hardware,
    compile_software,
    git_apply,
    git_diff,
    git_reset,
    prompt_llm,
    run_simulation,
)
from results import (
    BASELINE_DIR,
    TEMPLATE_C_PATH,
    IterDir,
    SimResults,
    baseline_cycles,
    best_design,
    next_iter_dir,
    parse_sim_output,
    read_body,
    render_source,
    score_evals,
    write_diff_json,
    write_eval_csv,
    write_tests_csv,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("accel")


# ---------------------------------------------------------------------------
# One attempt: compile -> build -> run
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    stage: str                      # where it stopped; "passed" if it did not
    detail: str = ""
    feedback: str = ""              # what the debugger is told
    sim: SimResults | None = None


@dataclass
class BuildCache:
    """The last successful simulator build and the chipyard diff it was built
    from, so a software-only fix does not rebuild the hardware."""
    key: str = ""
    artifact: BuildArtifact | None = None


def _diff_key(diffs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(diffs, sort_keys=True).encode()).hexdigest()


def try_design(body: str | None, out: IterDir, tag: str, cache: BuildCache) -> Attempt:
    if body is None:
        return Attempt("software format", "no body between the markers", L.feedback_no_body())

    # Software
    source = render_source(body)
    out.log(f"{tag}_memcpy_rocc.c", source)
    elf: RiscvBuildArtifact = compile_software(source)
    out.log(f"{tag}_sw_build.txt", f"rc={elf.returncode}\n\n## stdout\n{elf.stdout}\n\n## stderr\n{elf.stderr}")
    if not elf.success:
        return Attempt("software compile", f"rc={elf.returncode}", L.feedback_sw_build(elf))
    out.log(f"{tag}_memcpy_rocc.dump", elf.dump)

    # Hardware
    key = _diff_key(git_diff())
    if cache.artifact is not None and cache.key == key:
        logger.info("[%s] chipyard unchanged since the last build: reusing the simulator", tag)
        artifact = cache.artifact
    else:
        logger.info("[%s] building FastRTLSimRocketConfig", tag)
        artifact = build_hardware()
        out.log(f"{tag}_hw_build.stdout.txt", artifact.stdout)
        out.log(f"{tag}_hw_build.stderr.txt", artifact.stderr)
        if not artifact.success:
            return Attempt("hardware build", f"rc={artifact.returncode}", L.feedback_hw_build(artifact))
        cache.key, cache.artifact = key, artifact

    # Simulation
    logger.info("[%s] running %s", tag, elf.binary_name)
    run: RunResult = run_simulation(artifact, elf)
    out.log(f"{tag}_sim.log", run.log)
    out.log(f"{tag}_sim.out", run.out)
    sim = parse_sim_output(run.log)
    problem = sim.problem(run.returncode)
    if problem:
        return Attempt("simulation", problem, L.feedback_sim(run, sim, problem, elf.dump), sim)
    return Attempt("passed", f"{len(sim.tests)} tests, {len(sim.evals)} evals passed", sim=sim)


# ---------------------------------------------------------------------------
# Writing an iteration's results
# ---------------------------------------------------------------------------


def finish(out: IterDir, summary: dict, body: str | None, last: Attempt | None) -> dict:
    """Write diff.json, memcpy_rocc.c, tests.csv, eval.csv and summary.json."""
    write_diff_json(out.path / "diff.json", git_diff())
    if body is not None:
        out.write_text("memcpy_rocc.c", render_source(body))
    sim = last.sim if last else None
    if sim is not None:
        write_tests_csv(out.path / "tests.csv", sim.tests)
        # The baseline is scored against itself (1.0 everywhere).
        base = baseline_cycles() or {e["name"]: e["cycles"] for e in sim.evals}
        rows, avg = score_evals(sim.evals, base)
        write_eval_csv(out.path / "eval.csv", rows, avg)
        summary["avg_speedup"] = avg
    summary["status"] = "passed" if last and last.stage == "passed" else "failed"
    out.write_json("summary.json", summary)
    logger.info("%s: %s (avg speedup %s)", out.path.name, summary["status"], summary.get("avg_speedup"))
    return summary


# ---------------------------------------------------------------------------
# Baseline and iterations
# ---------------------------------------------------------------------------


def run_baseline() -> dict:
    """Clean chipyard + the harness's own (software) body; no LLM."""
    out = IterDir(BASELINE_DIR)
    logger.info("Generating the baseline in %s", out.path)
    logger.info("chipyard reset: %s", git_reset())
    body = read_body(TEMPLATE_C_PATH.read_text())
    attempt = try_design(body, out, "attempt0", BuildCache())
    summary = {"name": "baseline", "config": "FastRTLSimRocketConfig",
               "attempts": [{"attempt": 0, "stage": attempt.stage, "detail": attempt.detail}]}
    summary = finish(out, summary, body, attempt)
    if summary["status"] != "passed":
        raise RuntimeError(f"baseline failed at {attempt.stage}: {attempt.detail} "
                           f"(see {out.logs}); fix the environment before iterating")
    return summary


def run_iteration(chipyard_bash: BashTool) -> dict:
    parent = best_design()
    out = next_iter_dir()
    logger.info("=== %s: starting from %s (avg speedup %.3f) ===",
                out.path.name, parent.name, parent.avg_speedup)
    summary: dict = {"name": out.path.name, "parent": parent.name,
                     "parent_avg_speedup": parent.avg_speedup, "config": "FastRTLSimRocketConfig",
                     "attempts": []}

    logger.info("chipyard reset: %s", git_reset())
    applied, apply_msg = False, ""
    if any(parent.diff.values()):
        applied, apply_msg = git_apply(parent.diff)
        logger.info("apply %s's diff: %s", parent.name, apply_msg)
        if not applied:
            git_reset()
    summary["parent_diff_applied"] = applied

    # One LLM instance = one conversation for this whole iteration:
    # resume_session=True carries agy's conversation from call to call.
    llm = AntigravityLLM(
        model="gemini-4-argon-high",   # an id from `agy models` (plain gemini-4-argon is not one)
        system_message=("You are an expert RISC-V hardware/software co-designer: bare-metal C, "
                        "Chisel, and RoCC accelerators in Chipyard / Rocket Chip."),
        timeout_seconds=3600,
        logging_name="accel",
        resume_session=True,
        work_dir="/home/ray/agy_workspace",
    )
    summary["llm_model"] = llm.model

    # 1. Software write
    prompt = L.software_prompt(parent)
    cli = prompt_llm(llm, prompt, chipyard_bash)
    L.log_call(out, "software", prompt, cli)
    if not cli.success:
        summary["stopped"] = "software write: LLM call failed"
        return finish(out, summary, None, None)
    body = L.parse_body(cli)

    # 2. Hardware write (a revised body in the answer replaces the first)
    prompt = L.hardware_prompt(parent, applied, apply_msg)
    cli = prompt_llm(llm, prompt, chipyard_bash)
    L.log_call(out, "hardware", prompt, cli)
    if not cli.success:
        summary["stopped"] = "hardware write: LLM call failed"
        return finish(out, summary, body, None)
    body = L.parse_body(cli) or body

    # 3. Compile -> build -> run, debugging failures
    cache = BuildCache()
    attempt = None
    for n in range(1 + 4):   # the first attempt, then up to 4 debug calls
        if n > 0:
            prompt = L.debug_prompt(n, attempt.stage, attempt.feedback)
            cli = prompt_llm(llm, prompt, chipyard_bash)
            L.log_call(out, f"debug_{n}", prompt, cli)
            if not cli.success:
                summary["stopped"] = f"debug {n}: LLM call failed"
                break
            body = L.parse_body(cli) or body
        attempt = try_design(body, out, f"attempt{n}", cache)
        summary["attempts"].append({"attempt": n, "stage": attempt.stage, "detail": attempt.detail})
        logger.info("[attempt%d] %s: %s", n, attempt.stage, attempt.detail)
        if attempt.stage == "passed":
            break
        out.log(f"attempt{n}_feedback.md", attempt.feedback)
    return finish(out, summary, body, attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description="RoCC memcpy co-design loop")
    parser.add_argument("--iterations", type=int, default=1,
                        help="iterations to run after the baseline exists (default 1)")
    parser.add_argument("--baseline-only", action="store_true",
                        help="(re)generate accel/out/baseline and stop")
    args = parser.parse_args()

    # working_dir makes this folder's modules importable on the workers;
    # py_modules ships the head's chia over the images' own.
    ray.init(address="auto", runtime_env={
        "working_dir": str(Path(__file__).resolve().parent),
        "py_modules": [list(chia.__path__)[0]],
        "excludes": ["out/", "__pycache__", ".mypy_cache"],
    })

    have_baseline = (BASELINE_DIR / "summary.json").exists() and \
        json.loads((BASELINE_DIR / "summary.json").read_text()).get("status") == "passed"
    if args.baseline_only or not have_baseline:
        run_baseline()
    if args.baseline_only:
        return

    # The LLM's shell on the chipyard checkout, for the whole job.
    chipyard_bash = BashTool(
        name="chipyard_bash",
        work_dir="/home/ray/chipyard",
        timeout_seconds=300,
        task_options={"resources": {"chipyard": 0.01}},   # see nodes.py
    )
    for _ in range(args.iterations):
        run_iteration(chipyard_bash)
    best = best_design()
    logger.info("Best design: %s (avg speedup %.3f)", best.name, best.avg_speedup)


if __name__ == "__main__":
    main()
