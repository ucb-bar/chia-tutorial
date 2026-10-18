from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field

import chia
import ray
from ray import ObjectRef

from chia.base.ChiaFunction import get
from chia.base.tools.BashTool import BashTool
from chia.trace.profiler import start_collector, stop_collector
from chia.chipyard.state_def import BuildArtifact, RiscvBuildArtifact, RunResult
from chia.models.antigravity import AntigravityLLM

import agent_prompt
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
    CASES,
    OUT_DIR,
    TEMPLATE_C_PATH,
    IterDir,
    SimResults,
    baseline_cycles,
    best_design,
    next_iter_dir,
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
# One evaluation_result: compile + build -> run
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    stage: str                      # where it stopped; "passed" if it did not
    detail: str = ""
    feedback: str = ""              # what the debugger is told
    sim: SimResults | None = None


@dataclass
class BuildCache:
    """The last software compiles and simulator build, and the body / chipyard
    diff hashes they were built from, so an unchanged half is not rebuilt."""
    software_body_hash: str = ""
    elfs: dict[str, ObjectRef[RiscvBuildArtifact]] = field(default_factory=dict)
    rtl_diff_hash: str = ""
    build: ObjectRef[BuildArtifact] | None = None
    logged: bool = False            # its stdout/stderr are in the logs

def evaluate_design(body: str | None, iter_out_dir: IterDir, tag: str, build_cache: BuildCache) -> EvaluationResult:
    if body is None:
        return EvaluationResult("software format", "no body between the markers", agent_prompt.feedback_no_body())

    source = render_source(body)
    iter_out_dir.log(f"{tag}_memcpy_rocc.c", source)

    # 1. Dispatch every sw compile if the body changed
    software_body_hash = hashlib.sha256(body.encode()).hexdigest()
    if build_cache.elfs and build_cache.software_body_hash == software_body_hash:
        logger.info("[%s] body unchanged since the last compile: reusing the ELFs", tag)
    else:
        build_cache.software_body_hash = software_body_hash
        build_cache.elfs = {c.name: compile_software(source, c.name, c.cflags) for c in CASES}
    elf_refs = build_cache.elfs

    # 2. Dispatch hardware build if the RTL diff changed
    rtl_diff_hash = hashlib.sha256(json.dumps(get(git_diff()), sort_keys=True).encode()).hexdigest()
    if build_cache.build is not None and build_cache.rtl_diff_hash == rtl_diff_hash:
        logger.info("[%s] chipyard unchanged since the last build: reusing the simulator", tag)
    else:
        logger.info("[%s] building FastRTLSimRocketConfig", tag)
        build_cache.rtl_diff_hash = rtl_diff_hash
        build_cache.logged = False
        build_cache.build = build_hardware()

    # 3. Get the compiled software artifacts
    elfs: dict[str, RiscvBuildArtifact] = {name: get(ref) for name, ref in elf_refs.items()}

    # 4. Get the hardware build artifact
    hw_build_artifact: BuildArtifact = get(build_cache.build)
    if not build_cache.logged:
        iter_out_dir.log(f"{tag}_hw_build.stdout.txt", hw_build_artifact.stdout)
        iter_out_dir.log(f"{tag}_hw_build.stderr.txt", hw_build_artifact.stderr)
        build_cache.logged = True
    
    # 5. If sw compile or hardware build failed, return early
    sw_compile_failed = [name for name, elf in elfs.items() if not elf.success]
    for name in sw_compile_failed:
        elf = elfs[name]
        iter_out_dir.log(f"{tag}_{name}_sw_build.txt",
                f"rc={elf.returncode}\n\n## stdout\n{elf.stdout}\n\n## stderr\n{elf.stderr}")
    dump = next((elf.dump for elf in elfs.values() if elf.success), "")
    if sw_compile_failed:
        build_cache.elfs = {}
        first = elfs[sw_compile_failed[0]]
        detail = f"{len(sw_compile_failed)}/{len(elfs)} cases, first {sw_compile_failed[0]}: rc={first.returncode}"
        feedback = agent_prompt.feedback_sw_build(first)
        if not hw_build_artifact.success:
            build_cache.build = None
            detail += f"; hardware build rc={hw_build_artifact.returncode}"
            feedback += "\n" + agent_prompt.feedback_hw_build(hw_build_artifact)
        return EvaluationResult("software compile", detail, feedback)

    if not hw_build_artifact.success:
        build_cache.build = None
        return EvaluationResult("hardware build", f"rc={hw_build_artifact.returncode}", agent_prompt.feedback_hw_build(hw_build_artifact))

    # 6. Simulation: every case on the same simulator
    logger.info("[%s] running %d cases", tag, len(CASES))
    run_refs = {name: run_simulation(hw_build_artifact, elf) for name, elf in elfs.items()}
    runs: dict[str, RunResult] = {name: get(ref) for name, ref in run_refs.items()}
    sim = SimResults()
    for case in CASES:
        run = runs[case.name]
        iter_out_dir.log(f"{tag}_{case.name}_sim.log", run.log)
        iter_out_dir.log(f"{tag}_{case.name}_sim.out", run.out)
        sim.add(case, run.log, run.returncode)
    problem = sim.problem()
    if problem:
        return EvaluationResult("simulation", problem, agent_prompt.feedback_sim(runs, sim, problem, dump), sim)
    return EvaluationResult("passed", f"{len(sim.tests)} tests, {len(sim.evals)} evals passed", sim=sim)


# ---------------------------------------------------------------------------
# Writing an iteration's results
# ---------------------------------------------------------------------------


def finish(iter_out_dir: IterDir, summary: dict, body: str | None, last: EvaluationResult | None) -> dict:
    """Write diff.json, memcpy_rocc.c, tests.csv, eval.csv and summary.json."""
    write_diff_json(iter_out_dir.path / "diff.json", get(git_diff()))
    if body is not None:
        iter_out_dir.write_text("memcpy_rocc.c", render_source(body))
    sim = last.sim if last else None
    if sim is not None:
        write_tests_csv(iter_out_dir.path / "tests.csv", sim.tests)
        # The baseline is scored against itself (1.0 everywhere).
        base = baseline_cycles() or {e["name"]: e["cycles"] for e in sim.evals}
        rows, avg = score_evals(sim.evals, base)
        write_eval_csv(iter_out_dir.path / "eval.csv", rows, avg)
        summary["avg_speedup"] = avg
    summary["status"] = "passed" if last and last.stage == "passed" else "failed"
    iter_out_dir.write_json("summary.json", summary)
    logger.info("%s: %s (avg speedup %s)", iter_out_dir.path.name, summary["status"], summary.get("avg_speedup"))
    return summary


# ---------------------------------------------------------------------------
# Baseline and iterations
# ---------------------------------------------------------------------------


def run_baseline() -> dict:
    """Clean chipyard + the harness's own (software) body; no LLM."""
    iter_out_dir = IterDir(BASELINE_DIR)
    logger.info("Generating the baseline in %s", iter_out_dir.path)
    logger.info("chipyard reset: %s", get(git_reset()))
    body = read_body(TEMPLATE_C_PATH.read_text())
    evaluation_result = evaluate_design(body, iter_out_dir, "attempt0", BuildCache())
    summary = {"name": "baseline", "config": "FastRTLSimRocketConfig",
               "attempts": [{"evaluation_result": 0, "stage": evaluation_result.stage, "detail": evaluation_result.detail}]}
    summary = finish(iter_out_dir, summary, body, evaluation_result)
    if summary["status"] != "passed":
        raise RuntimeError(f"baseline failed at {evaluation_result.stage}: {evaluation_result.detail} "
                           f"(see {iter_out_dir.logs}); fix the environment before iterating")
    return summary


def run_iteration(chipyard_bash: BashTool) -> dict:
    parent = best_design()
    iter_out_dir = next_iter_dir()
    logger.info("=== %s: starting from %s (avg speedup %.3f) ===",
                iter_out_dir.path.name, parent.name, parent.avg_speedup)
    summary: dict = {"name": iter_out_dir.path.name, "parent": parent.name,
                     "parent_avg_speedup": parent.avg_speedup, "config": "FastRTLSimRocketConfig",
                     "attempts": []}

    logger.info("chipyard reset: %s", get(git_reset()))
    applied, apply_msg = False, ""
    if any(parent.diff.values()):
        applied, apply_msg = get(git_apply(parent.diff))
        logger.info("apply %s's diff: %s", parent.name, apply_msg)
        if not applied:
            get(git_reset())
    summary["parent_diff_applied"] = applied

    llm = AntigravityLLM(
        model="gemini-4-argon-low",   # an id from `agy models` (plain gemini-4-argon is not one)
        system_message=("You are an expert RISC-V hardware/software co-designer: bare-metal C, "
                        "Chisel, and RoCC accelerators in Chipyard / Rocket Chip."),
        timeout_seconds=3600,
        logging_name="accel",
        resume_session=True,
        work_dir="/home/ray/agy_workspace",
    )
    summary["llm_model"] = llm.model

    # 1. Software write
    prompt = agent_prompt.software_prompt(parent)
    llm_sw_write_response = get(prompt_llm(llm, prompt, chipyard_bash))
    cli = agent_prompt.check_answer(llm_sw_write_response)
    agent_prompt.log_call(iter_out_dir, "software", prompt, cli)
    if not cli.success:
        summary["stopped"] = "software write: LLM call failed"
        return finish(iter_out_dir, summary, None, None)
    body = agent_prompt.parse_body(cli)

    # 2. Hardware write (a revised body in the answer replaces the first)
    prompt = agent_prompt.hardware_prompt(parent, applied, apply_msg)
    llm_hw_write_response = get(prompt_llm(llm, prompt, chipyard_bash))
    cli = agent_prompt.check_answer(llm_hw_write_response)
    agent_prompt.log_call(iter_out_dir, "hardware", prompt, cli)
    if not cli.success:
        summary["stopped"] = "hardware write: LLM call failed"
        return finish(iter_out_dir, summary, body, None)
    body = agent_prompt.parse_body(cli) or body

    # 3. Compile -> build -> run, debugging failures
    build_cache = BuildCache()
    evaluation_result = None
    for n in range(5):   # the first evaluation_result, then up to 4 debug calls
        if n > 0:
            prompt = agent_prompt.debug_prompt(n, evaluation_result.stage, evaluation_result.feedback)
            llm_debug_response = get(prompt_llm(llm, prompt, chipyard_bash))
            cli = agent_prompt.check_answer(llm_debug_response)
            agent_prompt.log_call(iter_out_dir, f"debug_{n}", prompt, cli)
            if not cli.success:
                summary["stopped"] = f"debug {n}: LLM call failed"
                break
            body = agent_prompt.parse_body(cli) or body
        evaluation_result = evaluate_design(body, iter_out_dir, f"evaluation_result{n}", build_cache)
        summary["attempts"].append({"evaluation_result": n, "stage": evaluation_result.stage, "detail": evaluation_result.detail})
        logger.info("[evaluation_result%d] %s: %s", n, evaluation_result.stage, evaluation_result.detail)
        if evaluation_result.stage == "passed":
            break
        iter_out_dir.log(f"evaluation_result{n}_feedback.md", evaluation_result.feedback)
    return finish(iter_out_dir, summary, body, evaluation_result)


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
    # Every ChiaFunction call (compiles, builds, simulations, LLM calls, git
    # ops) is timed into out/profile_<timestamp>/ChiaProfileCollector.log.
    start_collector(log_dir=str(OUT_DIR / f"profile_{time.strftime('%Y%m%d_%H%M%S')}"))
    try:
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
    finally:
        stop_collector()


if __name__ == "__main__":
    main()
