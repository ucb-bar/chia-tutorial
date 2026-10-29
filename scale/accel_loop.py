"""Independent memcpy co-design loops, sharing only validated designs.

Run on the head, or use `python scale/run.py ...` from this machine.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import fcntl
import hashlib
import json
import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
import time

import ray
import yaml
from chia.base.ChiaFunction import get
from chia.base.tools.BashTool import BashTool
from chia.trace import live_status_monitor
from chia.trace.live_status_monitor import report
from chia.trace.profiler import start_collector, stop_collector

from coordinator import Coordinator, Design
from model_client import ScaleAntigravityLLM, with_backoff
import nodes
import prompts
from suite import body_from, load_cases, parse_results, render, score, validate_offload

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("scale")
MONITOR = "scale"  # Live status monitor read by monitor.py; one row per agent, plus baseline and best.


def show(label, **fields):
    """Set fields on the live status row of label's agent (agent_0/iter_000/... -> agent_0)."""
    report(MONITOR, label.split("/")[0], **fields)


class EvaluationError(RuntimeError):
    def __init__(self, stage, message):
        self.stage = stage
        super().__init__(message)


def load_settings(path):
    settings = yaml.safe_load(Path(path).read_text())
    if settings.get("model_effort") not in ("default", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("model_effort must be default, low, medium, high, xhigh, or max")
    for name in ("agents", "design_iterations_per_agent"):
        if type(settings[name]) is not int or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    test_cases = settings.get("test_cases")
    if type(test_cases) is not int or test_cases < 0 or 0 < test_cases < 4:
        raise ValueError("test_cases must be 0 (all) or an integer >= 4")
    if len(settings["strategies"]) < settings["agents"]:
        raise ValueError("Need one strategy per agent")
    return settings


def batches(cases, size, work_limit=None, repeats=1, warmups=1):
    groups, group, work = [], [], 0
    for case in cases:
        cost = (case.length + 128) * (repeats + warmups if case.kind == "eval" else 1)
        if work_limit is not None and cost > work_limit:
            raise ValueError(f"Individual case exceeds batch_work_bytes: {case.name}")
        if group and (len(group) >= size or (work_limit is not None and work + cost > work_limit)):
            groups.append(group)
            group, work = [], 0
        group.append(case)
        work += cost
    if group:
        groups.append(group)
    return groups


def case_batches(cases, settings):
    return batches(cases, settings["cases_per_binary"], settings["batch_work_bytes"],
                   settings["benchmark_repeats"], settings["benchmark_warmups"])


def json_file(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def collect_tasks(tasks, finish):
    """Submit everything to Ray, then drain it even if submission/evaluation fails.

    Ray alone limits concurrency. Draining prevents stale work overlapping a
    debugger's next attempt and preserves the other batches' diagnostic logs.
    """
    pending, results, first_error = [], [], None
    try:
        for task in tasks:
            pending.append(task)
    except Exception as error:
        first_error = error
    for task in pending:
        try:
            results.append(finish(task))
        except Exception as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error
    return results


def prepare_builders(settings, known=None):
    """Validate new builder VMs only; never inspect an active agent's checkout."""
    known = known or {}
    alive = {n["NodeManagerAddress"]: n for n in ray.nodes() if n["Alive"]
             and n["Resources"].get("chipyard", 0) >= 1 and n["Resources"].get("CPU", 0) >= 1}
    builders = [alive[ip] for ip in sorted(alive) if ip not in known][:max(0, settings["agents"] - len(known))]
    if not builders:
        if not known:
            raise RuntimeError("Need at least one builder worker")
        return []
    checks = [nodes.inspect_builder.options(scheduling_strategy=nodes.pinned(n["NodeID"])).chia_remote(settings["chipyard_path"]) for n in builders]
    info = [get(ref) for ref in checks]
    for value in info:
        if value["dirty"]:
            raise RuntimeError("Builder workspace is not clean; refusing to discard existing changes: " + value["dirty"])
    revision = next(iter(known.values()))["revision"] if known else (info[0]["commit"], info[0]["submodules"])
    if any((i["commit"], i["submodules"]) != revision for i in info):
        raise RuntimeError("Builders have different source revisions")
    return [dict(n, revision=revision) for n in builders]


def evaluate_design(settings, body, cases, build, cache, directory, label):
    groups = case_batches(cases, settings)
    profile = label.rsplit("/", 1)[-1]
    show(label, state=f"{profile} compile + build", batches=f"0/{len(groups)}")
    json_file(directory / "batches.json", [[c.name for c in group] for group in groups])
    def compile_tasks():
        for index, group in enumerate(groups):
            source = render(body, group, settings)
            digest = hashlib.sha256(source.encode()).hexdigest()
            sources.append((index, group, digest))
            if digest not in submitted:
                submitted.add(digest)
                yield index, digest, nodes.compile_software(source, f"batch_{index:04d}", settings)
    def finish_compile(task):
        index, digest, ref = task
        elf = get(ref)
        if not elf.success:
            (directory / f"batch_{index:04d}.compile.stdout.txt").write_text(elf.stdout)
            (directory / f"batch_{index:04d}.compile.stderr.txt").write_text(elf.stderr)
            raise EvaluationError("software compile", "Software compile failed:\n" + elf.stderr[-20000:] + elf.stdout[-10000:])
        cache[digest] = elf
    sources, submitted = [], set(cache)
    compiled, compile_error = [], None
    try:
        collect_tasks(compile_tasks(), finish_compile)
        compiled = [(index, group, cache[digest]) for index, group, digest in sources]
    except Exception as error:
        compile_error = error
    # A debugger must NEVER edit this checkout while its previous build runs.
    try:
        artifact = get(build)
    except Exception as error:
        raise EvaluationError("hardware build", str(error) + (f"\nSoftware also failed: {compile_error}" if compile_error else "")) from error
    if not artifact.success:
        (directory / "hardware.stdout.txt").write_text(artifact.stdout)
        (directory / "hardware.stderr.txt").write_text(artifact.stderr)
        raise EvaluationError("hardware build", "Hardware build failed:\n" + artifact.stderr[-15000:] + artifact.stdout[-20000:])
    if compile_error is not None:
        raise compile_error
    artifact_ref = ray.put(artifact)
    show(label, state=f"{profile} simulate")
    simulated = 0
    def simulation_tasks():
        for index, group, elf in compiled:
            yield index, group, nodes.simulate(artifact_ref, elf, settings)
    def finish_simulation(task):
        nonlocal simulated
        index, group, ref = task
        result = get(ref)
        simulated += 1
        show(label, batches=f"{simulated}/{len(groups)}")
        (directory / f"batch_{index:04d}.log").write_text(result.log)
        if result.returncode:
            (directory / f"batch_{index:04d}.stderr.txt").write_text(result.out)
        try:
            return parse_results(group, result.log, result.returncode)
        except ValueError as error:
            raise EvaluationError("simulation", str(error) + "\n" + result.log[-20000:] + result.out[-5000:]) from error
    LOG.info("%s: evaluating %d cases in %d batches", label, len(cases), len(groups))
    rows = [row for group in collect_tasks(simulation_tasks(), finish_simulation) for row in group]
    json_file(directory / "results.json", rows)
    return rows


def run_baseline(settings, run_directory, builder, quick, full):
    directory = run_directory / "baseline"
    directory.mkdir()
    body = body_from((ROOT / "memcpy_rocc.c").read_text())
    build = nodes.build_hardware(settings, builder)
    cache = {}
    quick_dir, full_dir = directory / "quick", directory / "full"
    quick_dir.mkdir(); full_dir.mkdir()
    evaluate_design(settings, body, quick, build, cache, quick_dir, "baseline/quick")
    rows = evaluate_design(settings, body, full, build, cache, full_dir, "baseline/full")
    json_file(directory / "summary.json", dict(status="passed", validated_profile="full", score=1.0,
              case_count=len(full), available_case_count=len(load_cases(settings, "all")),
              note="Full means the entire configured suite; a test_cases subset is not all available upstream cases."))
    (directory / "memcpy_body.c").write_text(body)
    (directory / "hardware.patch").write_text("")
    show("baseline", state="done", speedup="1.0000x")
    return Design("baseline", str(directory), body, "", 1.0), build, rows


def ask(llm, tool, prompt, directory, label, settings):
    prompt = prompt.replace("FastRTLSimRocketConfig", settings["hardware_config"])
    name = label.split('/')[-1]
    (directory / f"{name}.txt").write_text(prompt + "\n\nPENDING\n")
    show(label, state=f"model {name}")
    def call():
        return get(llm.prompt.options(num_cpus=0, resources={"antigravity_creds": nodes.ROLE_TAG},
                   scheduling_strategy=nodes.pinned(llm._scale_node_id), max_retries=0).chia_remote(llm, prompt, [tool]))
    def log_retry(attempt, seconds, error):
        json_file(directory / f"{name}.retry_{attempt}.json", dict(attempt=attempt, wait_seconds=seconds, error=str(error)))
        LOG.warning("%s: model throttled; retry %d after %ds", label, attempt, seconds)
        show(label, state=f"model {name} retry {attempt}")
    answer = with_backoff(call, settings, log_retry)
    (directory / f"{name}.txt").write_text(prompt + "\n\nANSWER\n" + (answer.result or "") + "\n\nSTDERR\n" + (answer.stderr or "") + "\n\nTRANSCRIPT\n" + (answer.stream_result or ""))
    return answer


def run_agent(index, builder, coordinator, settings, run_directory, quick, full, baseline_build, baseline_rows):
    node_id = builder["NodeID"]
    strategy = settings["strategies"][index]
    tool = BashTool(name="chipyard_bash", work_dir=settings["chipyard_path"], timeout_seconds=300,
                    task_options=dict(num_cpus=0, resources={"chipyard": 0.01}, scheduling_strategy=nodes.pinned(node_id)))
    cache, cached_patch, build = {}, "", baseline_build
    baseline = {r["name"]: r["cycles"] for r in baseline_rows if r["kind"] == "eval"}
    passed = 0
    try:
        for iteration in range(settings["design_iterations_per_agent"]):
            iteration_start = time.monotonic()
            label = f"agent_{index}/iter_{iteration:03d}"
            directory = run_directory / label
            directory.mkdir(parents=True)
            parent = coordinator.snapshot()  # Adopt best only at an iteration boundary.
            summary = dict(agent=index, iteration=iteration, parent=parent.name, strategy=strategy,
                           builder_node_id=node_id, status="failed", attempts=[])
            show(label, iteration=f"{iteration + 1}/{settings['design_iterations_per_agent']}",
                 attempt="", batches="", parent=parent.name, state="reset workspace")
            try:
                get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "reset"))
                get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "apply", parent.patch))
                llm = ScaleAntigravityLLM(model=settings["model"], system_message=prompts.RULES,
                    timeout_seconds=settings["model_timeout_seconds"], resume_session=True,
                    extra_cli_args=[] if settings["model_effort"] == "default" else ["--effort", settings["model_effort"]],
                    work_dir=f"/tmp/scale_agy/{run_directory.name}/{label}", logging_name=label)
                llm._scale_node_id = get(nodes.prepare_model_workspace.chia_remote(llm.work_dir))
                body = prompts.answer_body(ask(llm, tool, prompts.software(parent, strategy, dict(quick=len(quick), full=len(full))), directory, label + "/software", settings))
                body = prompts.answer_body(ask(llm, tool, prompts.hardware(), directory, label + "/hardware", settings), body)
                for attempt in range(settings["max_attempts"]):
                    attempt_dir = directory / f"attempt_{attempt:02d}"
                    attempt_dir.mkdir()
                    show(label, attempt=f"{attempt + 1}/{settings['max_attempts']}", batches="", state="capture patch")
                    try:
                        patch = get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "capture"))
                        if not patch.strip():
                            raise RuntimeError("Optimization must include hardware and a RoCC software interface, not a software-only fallback")
                        validate_offload(body)
                        render(body, quick[:1], settings)  # Reject invalid bodies BEFORE starting a hardware build.
                        if patch != cached_patch:
                            cached_patch = patch
                            build = nodes.build_hardware(settings, builder)
                        (attempt_dir / "memcpy_body.c").write_text(body)
                        (attempt_dir / "hardware.patch").write_text(patch)
                        for profile, cases in [("quick", quick), ("full", full)]:
                            profile_dir = attempt_dir / profile
                            profile_dir.mkdir()
                            rows = evaluate_design(settings, body, cases, build, cache, profile_dir, label + "/" + profile)
                        value = score(rows, baseline)
                        summary.update(status="passed", validated_profile="full", score=value, winning_attempt=attempt_dir.name)
                        summary["attempts"].append(dict(attempt=attempt, status="passed"))
                        design = Design(label, str(attempt_dir), body, patch, value)
                        improved = coordinator.publish(design)
                        LOG.info("%s full-validated %.4fx; promoted=%s", label, value, improved)
                        show(label, state="passed", speedup=f"{value:.4f}x")
                        if improved:
                            report(MONITOR, "best", design=label, speedup=f"{value:.4f}x", state="done")
                        passed += 1
                        break
                    except Exception as error:
                        if isinstance(error, EvaluationError) and error.stage == "hardware build":
                            cached_patch, build = None, None
                        summary["status"] = "failed"
                        summary.pop("validated_profile", None)
                        summary.pop("score", None)
                        feedback = str(error)
                        summary["attempts"].append(dict(attempt=attempt, status="failed", error=feedback))
                        (attempt_dir / "failure.txt").write_text(feedback)
                        show(label, state=f"attempt failed: {getattr(error, 'stage', 'evaluation')}")
                        if attempt + 1 < settings["max_attempts"]:
                            body = prompts.answer_body(ask(llm, tool, prompts.debug(getattr(error, "stage", "evaluation"), feedback, attempt + 1, settings["debug_feedback_max_chars"]), directory, label + f"/debug_{attempt + 1}", settings), body)
            except Exception as error:
                summary["error"] = str(error)
                LOG.exception("%s stopped", label)
                show(label, state="failed", error=str(error).splitlines()[0][:80] if str(error) else type(error).__name__)
                return passed
            finally:
                summary["wall_seconds"] = time.monotonic() - iteration_start
                json_file(directory / "summary.json", summary)
        show(f"agent_{index}", state="done", validated=passed)
    finally:
        tool.stop()
        try:
            get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "reset"))
        except Exception:
            LOG.exception("Agent %s workspace cleanup refused; inspect it before reuse", index)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", default=str(ROOT / "settings.yaml"))
    parser.add_argument("--address")
    parser.add_argument("--agents", type=int)
    parser.add_argument("--design-iterations", "--iterations", dest="design_iterations", type=int,
                        help="Design iterations per agent (--iterations is an alias)")
    parser.add_argument("--list-cases", action="store_true", help="Offline suite counts")
    parser.add_argument("--baseline-only", action="store_true", help="Compile/build/simulate frozen baseline without a model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.settings)
    if args.agents is not None:
        if not 1 <= args.agents <= len(settings["strategies"]):
            parser.error("--agents must be between 1 and the number of configured strategies")
        settings["agents"] = args.agents
    if args.design_iterations is not None:
        if args.design_iterations < 1:
            parser.error("--design-iterations must be positive")
        settings["design_iterations_per_agent"] = args.design_iterations
    quick, full = load_cases(settings, "quick"), load_cases(settings, "full")
    print(json.dumps(dict(quick=len(quick), configured_suite=len(full), available_cases=len(load_cases(settings, "all")), tests=sum(c.kind == "test" for c in full), benchmarks=sum(c.kind == "eval" for c in full), quick_batches=len(case_batches(quick, settings)), suite_batches=len(case_batches(full, settings)))), flush=True)
    if args.list_cases:
        return
    driver_lock = open("/tmp/chia-scale-driver.lock", "a")
    fcntl.flock(driver_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    connection = ray.init(address=args.address or settings["ray_address"], namespace="chia-scale", log_to_driver=False,
             runtime_env={"working_dir": str(ROOT), "excludes": ["out/", ".runtime/", ".venv/", "__pycache__/"]})
    settings["ray_address"] = connection.address_info["gcs_address"]
    # LIVE STATUS MONITOR: create the monitor actor (it lives as long as this driver) without
    # watch() printing tables into this output; monitor.py reads it from another terminal.
    live_status_monitor._monitor()
    start = time.monotonic()
    directory = ROOT / "out" / time.strftime("run_%Y%m%d_%H%M%S")
    try:
        directory.mkdir(parents=True)
        start_collector(log_dir=str(directory / "profile"))
        builders = prepare_builders(settings)
        json_file(directory / "settings.json", settings)
        json_file(directory / "cases.json", [asdict(c) for c in full])
        LOG.info("Results: %s", directory)
        report(MONITOR, "run", directory=directory.name, state="running")
        baseline, build, rows = run_baseline(settings, directory, builders[0], quick, full)
        coordinator = Coordinator(directory, baseline)
        report(MONITOR, "best", design=baseline.name, speedup=f"{baseline.score:.4f}x", state="done")
        count = 0
        if not args.baseline_only:
            available_builders = Queue()
            known = {n["NodeManagerAddress"]: n for n in builders}
            discovery_lock = Lock()
            for builder in builders:
                available_builders.put(builder)
            LOG.info("%d agents sharing %d builders", settings["agents"], len(builders))

            def start_agent(index):
                waiting = directory / f"agent_{index}" / "waiting_for_builder"
                waiting.parent.mkdir(parents=True, exist_ok=True)
                waiting.touch()
                LOG.info("agent_%d waiting for builder", index)
                show(f"agent_{index}", state="waiting for builder")
                while True:
                    try:
                        builder = available_builders.get(timeout=10)
                        break
                    except Empty:
                        with discovery_lock:
                            try:
                                new = prepare_builders(settings, known)
                            except Exception as error:
                                LOG.warning("Builder discovery: %s", error)
                                continue
                            for node in new:
                                known[node["NodeManagerAddress"]] = node
                                available_builders.put(node)
                                LOG.info("Discovered builder %s", node["NodeManagerAddress"])
                try:
                    waiting.unlink()
                    LOG.info("agent_%d acquired builder %s", index, builder["NodeID"])
                    return run_agent(index, builder, coordinator, settings, directory, quick, full, build, rows)
                finally:
                    available_builders.put(builder)

            with ThreadPoolExecutor(max_workers=settings["agents"]) as pool:
                futures = [pool.submit(start_agent, i) for i in range(settings["agents"])]
                count = sum(f.result() for f in as_completed(futures))
        best = coordinator.snapshot()
        json_file(directory / "run_summary.json", dict(validated_candidates=count, wall_seconds=time.monotonic() - start,
                  score_metric="arithmetic_mean_speedup", best=asdict(best)))
        LOG.info("Best configured-suite-validated design: %s (%.4fx)", best.name, best.score)
        report(MONITOR, "run", state="done")
        if not args.baseline_only and count == 0:
            raise RuntimeError("No agent produced a fully validated candidate; see per-agent summaries")
    finally:
        stop_collector()
        ray.shutdown()
        driver_lock.close()


if __name__ == "__main__":
    main()
