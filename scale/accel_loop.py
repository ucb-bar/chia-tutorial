"""Three independent memcpy co-design loops, sharing only validated designs.

Run on the head, or use `python scale/run.py ...` from this machine.
No submodule edits/resets or revision changes are permitted in any mode.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4

import ray
import yaml
from chia.base.ChiaFunction import get
from chia.trace.profiler import start_collector, stop_collector

from coordinator import Coordinator, Design
from model_client import ScaleAntigravityLLM, with_backoff
import nodes
import prompts
from suite import body_from, fingerprint, load_cases, parse_results, render, score, validate_offload

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("scale")


class EvaluationError(RuntimeError):
    def __init__(self, stage, message):
        self.stage = stage
        super().__init__(message)


def load_settings(path):
    settings = yaml.safe_load(Path(path).read_text())
    if settings.get("model_effort") not in ("default", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("model_effort must be default, low, medium, high, xhigh, or max")
    positive = ["agents", "iterations_per_agent", "build_jobs", "simulation_threads", "compile_slots", "simulation_slots", "quick_cases_per_kind", "max_case_bytes", "cases_per_binary", "batch_work_bytes", "benchmark_repeats", "max_attempts", "compile_timeout_seconds", "build_timeout_seconds", "simulation_timeout_seconds", "simulation_timeout_cycles", "model_timeout_seconds", "metrics_interval_seconds"]
    for name in positive:
        if type(settings[name]) is not int or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if type(settings["java_heap_gb"]) is not int or settings["java_heap_gb"] < 1:
        raise ValueError("java_heap_gb must be a positive integer")
    if type(settings["benchmark_warmups"]) is not int or settings["benchmark_warmups"] < 0:
        raise ValueError("benchmark_warmups must be a nonnegative integer")
    if type(settings["case_limit"]) is not int or settings["case_limit"] < 0 or 0 < settings["case_limit"] < 4:
        raise ValueError("case_limit must be 0 (all) or an integer >= 4")
    largest = (settings["max_case_bytes"] + 128) * (settings["benchmark_repeats"] + settings["benchmark_warmups"])
    if settings["batch_work_bytes"] < largest:
        raise ValueError("batch_work_bytes cannot fit the largest individual benchmark")
    if settings["quick_cases_per_kind"] < 2 or len(settings["strategies"]) < settings["agents"]:
        raise ValueError("Need >= 2 quick cases per kind and one strategy per agent")
    if settings["simulation_slots"] < settings["simulation_threads"]:
        raise ValueError("Simulation CPU budget cannot fit one simulation")
    if type(settings["model_rate_limit_retries"]) is not int or settings["model_rate_limit_retries"] < 0:
        raise ValueError("model_rate_limit_retries must be a nonnegative integer")
    for name in ["model_backoff_seconds", "model_max_backoff_seconds"]:
        if type(settings[name]) is not int or settings[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if settings["model_backoff_seconds"] > settings["model_max_backoff_seconds"]:
        raise ValueError("model_max_backoff_seconds must fit the initial backoff")
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


def preflight(settings):
    if version("chialoops") != "2.0.0":
        raise RuntimeError("The loop requires chialoops==2.0.0")
    alive = [n for n in ray.nodes() if n["Alive"]]
    builders = sorted((n for n in alive if n["Resources"].get("chipyard", 0) >= 1), key=lambda n: n["NodeManagerAddress"])
    compiler = next((n for n in alive if n["Resources"].get("riscv_build", 0) >= 1), None)
    model = next((n for n in alive if n["Resources"].get("antigravity_creds", 0) >= 1), None)
    if len(builders) < settings["agents"] or compiler is None or model is None:
        raise RuntimeError("Missing builder, compiler, or model-client workers")
    resources = ray.cluster_resources()
    for name, required in [("riscv_build", settings["compile_slots"]), ("verilator_run", settings["simulation_slots"]), ("antigravity_creds", settings["agents"])]:
        if resources.get(name, 0) < required:
            raise RuntimeError(f"Requested {required} {name} slots, available {resources.get(name, 0)}")
    builders = builders[:settings["agents"]]
    if any(n["Resources"].get("CPU", 0) < settings["build_jobs"] for n in builders):
        raise RuntimeError("build_jobs exceeds a builder's Ray CPU budget")
    for n in alive:
        if "verilator_run" in n["Resources"] and settings["simulation_threads"] > min(n["Resources"]["CPU"], n["Resources"]["verilator_run"]):
            raise RuntimeError("A simulation cannot fit on a simulator worker")
    checks = [nodes.inspect_builder.options(scheduling_strategy=nodes.pinned(n["NodeID"])).chia_remote(settings["chipyard_path"]) for n in builders]
    info = [get(ref) for ref in checks]
    for value in info:
        if value["dirty"]:
            raise RuntimeError("Builder workspace is not clean; refusing to discard existing changes: " + value["dirty"])
        if value["landlock_abi"] < 3:
            raise RuntimeError("Builder lacks required write sandbox; no hardware builds will run")
        if any(line and line[0] == "U" for line in value["submodules"].splitlines()) or not value["required_sources"]:
            raise RuntimeError("Required sources missing or submodules conflicted; refusing to update them")
    if any((i["commit"], i["submodules"], i["protected_state"]) != (info[0]["commit"], info[0]["submodules"], info[0]["protected_state"]) for i in info):
        raise RuntimeError("Builders have different source revisions")
    environment = {}
    for role, worker in [("compiler", compiler), ("model", model)]:
        value = get(nodes.environment_info.options(scheduling_strategy=nodes.pinned(worker["NodeID"])).chia_remote(role))
        if value["chia"] != "2.0.0" or value["ray"] != ray.__version__:
            raise RuntimeError(f"Version mismatch on {role}: {value}")
        environment[role] = value
    environment["builders"] = info
    if not environment["compiler"]["harness"] or not environment["model"]["agy"]:
        raise RuntimeError("Compiler harness or agy executable missing")
    # One physical-VM sample, not three duplicate samples of the head machine.
    physical = {n["NodeManagerAddress"]: n["NodeID"] for n in alive}
    return builders, info, environment, physical


def placement_check(settings, builders):
    """Real concurrent MCP calls, without model calls or source modifications."""
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    tools = []
    try:
        for node in builders:
            tool = nodes.SourceTool(settings["chipyard_path"], dict(num_cpus=0, resources={"chipyard": 0.01}, scheduling_strategy=nodes.pinned(node["NodeID"])))
            tools.append(tool)
            assert tool.node_id == node["NodeID"]
        async def check(tool):
            url = f"http://{tool.hostname}:{tool.port}/{tool.name}/mcp"
            async with streamable_http_client(url) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool("read_file", dict(path="generators/chipyard/src/main/scala/config/RocketConfigs.scala", lines=2))
                    assert not result.isError
                    denied = await session.call_tool("write_file", dict(path="generators/rocket-chip/PermissionCheck.scala", content="must not be written"))
                    assert denied.isError, "Submodule write was not rejected"
            print(f"PASS pinned builder/tool {tool.node_id} at {tool.hostname}; submodule write rejected", flush=True)
        async def all_checks():
            await asyncio.wait_for(asyncio.gather(*(check(tool) for tool in tools)), timeout=60)
        asyncio.run(all_checks())
    finally:
        for tool in tools:
            tool.stop()


class Engine:
    def __init__(self, settings, directory, physical):
        self.settings, self.directory, self.physical = settings, directory, physical
        self.compiler_slots = threading.BoundedSemaphore(settings["compile_slots"])
        self.simulator_slots = threading.BoundedSemaphore(settings["simulation_slots"] // settings["simulation_threads"])
        self.events_lock = threading.Lock()
        self.stop_metrics = threading.Event()
        self.reused_baseline = False
        self.metric_thread = threading.Thread(target=self.sample_metrics, daemon=True)

    @contextmanager
    def stage(self, agent, stage):
        start = time.monotonic()
        success = False
        try:
            yield
            success = True
        finally:
            row = dict(time=time.time(), agent=agent, stage=stage, elapsed_seconds=time.monotonic() - start, success=success)
            with self.events_lock, (self.directory / "stages.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")

    def sample_metrics(self):
        previous = {}
        while not self.stop_metrics.is_set():
            try:
                refs = [(ip, nodes.machine_metrics.options(scheduling_strategy=nodes.pinned(node)).chia_remote()) for ip, node in self.physical.items()]
                row = dict(time=time.time(), available=ray.available_resources(), total=ray.cluster_resources(), machines={})
                for ip, ref in refs:
                    if self.stop_metrics.is_set():
                        return
                    value = get(ref, timeout=10)
                    old = previous.get(ip)
                    delta = value["cpu_total"] - old["cpu_total"] if old else 0
                    value["cpu_percent"] = 100 * (value["cpu_busy"] - old["cpu_busy"]) / delta if delta > 0 else None
                    previous[ip] = value
                    row["machines"][ip] = value
                with (self.directory / "utilization.jsonl").open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
            except Exception as error:
                LOG.warning("Metrics sample failed: %s", error)
            self.stop_metrics.wait(self.settings["metrics_interval_seconds"])

    def evaluate(self, body, cases, build, cache, directory, label):
        groups = case_batches(cases, self.settings)
        json_file(directory / "batches.json", [[c.name for c in group] for group in groups])
        def compile_one(item):
            index, group = item
            source = render(body, group, self.settings)
            digest = hashlib.sha256(source.encode()).hexdigest()
            if digest not in cache:
                with self.compiler_slots:
                    elf = get(nodes.compile_software(source, f"batch_{index:04d}", self.settings))
                if not elf.success:
                    raise EvaluationError("software compile", "Software compile failed:\n" + elf.stderr[-20000:] + elf.stdout[-10000:])
                cache[digest] = elf
            return index, group, cache[digest]
        compiled, compile_error = [], None
        try:
            with self.stage(label, "software_compile"), ThreadPoolExecutor(max_workers=min(len(groups), self.settings["compile_slots"])) as pool:
                compiled = list(pool.map(compile_one, enumerate(groups)))
        except Exception as error:
            compile_error = error
        # A debugger must NEVER edit this checkout while its previous build runs.
        with self.stage(label, "wait_for_hardware"):
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
        def run_one(item):
            index, group, elf = item
            with self.simulator_slots:
                started = time.monotonic()
                result = get(nodes.simulate(artifact_ref, elf, self.settings))
                elapsed = time.monotonic() - started
            (directory / f"batch_{index:04d}.log").write_text(result.log)
            json_file(directory / f"batch_{index:04d}.timing.json", dict(cases=len(group), wall_seconds=elapsed, returncode=result.returncode))
            if result.returncode:
                (directory / f"batch_{index:04d}.stderr.txt").write_text(result.out)
            try:
                return parse_results(group, result.log, result.returncode)
            except ValueError as error:
                raise EvaluationError("simulation", str(error) + "\n" + result.log[-20000:] + result.out[-5000:]) from error
        workers = min(len(compiled), self.settings["simulation_slots"] // self.settings["simulation_threads"])
        with self.stage(label, "simulation"), ThreadPoolExecutor(max_workers=workers) as pool:
            rows = [row for group in pool.map(run_one, compiled) for row in group]
        json_file(directory / "results.json", rows)
        return rows

    def summarize(self, elapsed):
        samples = [json.loads(line) for line in (self.directory / "utilization.jsonl").read_text().splitlines()] if (self.directory / "utilization.jsonl").exists() else []
        peak = {}
        for sample in samples:
            for ip, value in sample["machines"].items():
                peak[ip] = max(peak.get(ip, 0), value["disk_used_bytes"])
        json_file(self.directory / "metrics_summary.json", dict(wall_seconds=elapsed, peak_disk_used_bytes=peak,
                  note="Cycle speedup is separate from wall-clock optimization throughput. CPU samples are VM-wide; disk peaks are sampled."))
        timings = [json.loads(p.read_text()) for p in self.directory.rglob("batch_*.timing.json")
                   if not (self.reused_baseline and p.is_relative_to(self.directory / "baseline"))]
        json_file(self.directory / "timing_summary.json", dict(wall_seconds=elapsed,
                  simulations=len(timings), slowest_simulation_seconds=max((r["wall_seconds"] for r in timings), default=None),
                  failed_simulations=sum(r["returncode"] != 0 for r in timings)))


def run_baseline(engine, builder, quick, full, identity):
    directory = engine.directory / "baseline"
    directory.mkdir()
    body = body_from((ROOT / "memcpy_rocc.c").read_text())
    build = nodes.hardware.options(num_cpus=engine.settings["build_jobs"], scheduling_strategy=nodes.pinned(builder["NodeID"]), max_retries=0).chia_remote(engine.settings)
    cache = {}
    quick_dir, full_dir = directory / "quick", directory / "full"
    quick_dir.mkdir(); full_dir.mkdir()
    engine.evaluate(body, quick, build, cache, quick_dir, "baseline/quick")
    rows = engine.evaluate(body, full, build, cache, full_dir, "baseline/full")
    json_file(directory / "summary.json", dict(status="passed", validated_profile="full", fingerprint=identity, score=1.0,
              case_count=len(full), available_case_count=len(load_cases(engine.settings, "all")),
              note="Full means the entire configured suite; a case_limit subset is not all available upstream cases."))
    (directory / "memcpy_body.c").write_text(body)
    (directory / "hardware.patch").write_text("")
    return Design("baseline", str(directory), body, "", 1.0, identity), build, rows


def reuse_baseline(engine, previous, full, identity, environment):
    """Explicit reuse of a completed full baseline; never reuse candidate results."""
    previous = Path(previous).resolve()
    if not previous.is_relative_to((ROOT / "out").resolve()):
        raise ValueError("--baseline-run must point to a scale/out run on the head")
    old_settings = json.loads((previous / "settings.json").read_text())
    old_environment = json.loads((previous / "environment.json").read_text())
    summary = json.loads((previous / "baseline/summary.json").read_text())
    if (summary.get("status") != "passed" or summary.get("validated_profile") != "full"
            or summary.get("fingerprint") != fingerprint(old_settings, full, old_environment)):
        raise ValueError("Previous baseline is incomplete or its suite/harness identity differs")
    if old_settings["cases_per_binary"] != engine.settings["cases_per_binary"]:
        raise ValueError("Previous baseline uses different case batching")
    def measured_environment(value):
        # Login state and the NUMBER of identical builders do not affect cycles.
        return dict(compiler=value["compiler"], builder=value["builders"][0])
    if fingerprint(old_settings, full, measured_environment(old_environment)) != fingerprint(engine.settings, full, measured_environment(environment)):
        raise ValueError("Previous baseline uses different measurement settings or source/compiler state")
    body = body_from((ROOT / "memcpy_rocc.c").read_text())
    if (previous / "baseline/memcpy_body.c").read_text() != body or (previous / "baseline/hardware.patch").read_text():
        raise ValueError("Previous baseline is not the frozen software memcpy baseline")
    rows = []
    for index, group in enumerate(case_batches(full, old_settings)):
        rows.extend(parse_results(group, (previous / f"baseline/full/batch_{index:04d}.log").read_text(), 0))
    directory = engine.directory / "baseline"
    shutil.copytree(previous / "baseline", directory)
    json_file(directory / "summary.json", dict(status="passed", validated_profile="full", fingerprint=identity, score=1.0, reused_from=str(previous)))
    LOG.info("Reused full-validated baseline: %s", previous)
    engine.reused_baseline = True
    # Candidates always require a nonempty hardware patch and build it themselves.
    return Design("baseline", str(directory), body, "", 1.0, identity), None, rows


def ask(llm, tool, prompt, directory, label, engine):
    prompt = prompt.replace("FastRTLSimRocketConfig", engine.settings["hardware_config"])
    name = label.split('/')[-1]
    (directory / f"{name}.txt").write_text(prompt + "\n\nPENDING\n")
    def call():
        return get(llm.prompt.options(num_cpus=1, resources={"antigravity_creds": 1},
                   scheduling_strategy=nodes.pinned(llm._scale_node_id), max_retries=0).chia_remote(llm, prompt, [tool]))
    def log_retry(attempt, seconds, error):
        json_file(directory / f"{name}.retry_{attempt}.json", dict(attempt=attempt, wait_seconds=seconds, error=str(error)))
        LOG.warning("%s: model throttled; retry %d after %ds", label, attempt, seconds)
    with engine.stage(label, "model"):
        answer = with_backoff(call, engine.settings, log_retry)
    (directory / f"{name}.txt").write_text(prompt + "\n\nANSWER\n" + (answer.result or "") + "\n\nSTDERR\n" + (answer.stderr or "") + "\n\nTRANSCRIPT\n" + (answer.stream_result or ""))
    return answer


def run_agent(index, builder, source_info, coordinator, engine, quick, full, baseline_build, baseline_rows):
    settings = engine.settings
    node_id = builder["NodeID"]
    strategy = settings["strategies"][index]
    tool = nodes.SourceTool(settings["chipyard_path"], dict(num_cpus=0, resources={"chipyard": 0.01}, scheduling_strategy=nodes.pinned(node_id)))
    cache, cached_patch, build = {}, "", baseline_build
    baseline = {r["name"]: r["cycles"] for r in baseline_rows if r["kind"] == "eval"}
    passed = 0
    try:
        for iteration in range(settings["iterations_per_agent"]):
            iteration_start = time.monotonic()
            label = f"agent_{index}/iter_{iteration:03d}"
            directory = engine.directory / label
            directory.mkdir(parents=True)
            parent = coordinator.snapshot()  # Adopt best only at an iteration boundary.
            summary = dict(agent=index, iteration=iteration, parent=parent.name, strategy=strategy,
                           builder_node_id=node_id, fingerprint=parent.fingerprint, status="failed", attempts=[])
            try:
                get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "reset", source_info))
                get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "apply", source_info, parent.patch))
                llm = ScaleAntigravityLLM(model=settings["model"], system_message=prompts.RULES,
                    timeout_seconds=settings["model_timeout_seconds"], resume_session=True,
                    extra_cli_args=[] if settings["model_effort"] == "default" else ["--effort", settings["model_effort"]],
                    work_dir=f"/tmp/scale_agy/{engine.directory.name}/{label}", logging_name=label)
                llm._scale_node_id = get(nodes.prepare_model_workspace.chia_remote(llm.work_dir))
                body = prompts.answer_body(ask(llm, tool, prompts.software(parent, strategy, dict(quick=len(quick), full=len(full))), directory, label + "/software", engine))
                body = prompts.answer_body(ask(llm, tool, prompts.hardware(), directory, label + "/hardware", engine), body)
                for attempt in range(settings["max_attempts"]):
                    attempt_dir = directory / f"attempt_{attempt:02d}"
                    attempt_dir.mkdir()
                    try:
                        patch = get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "capture", source_info))
                        if not patch.strip():
                            raise RuntimeError("Optimization must include hardware and a RoCC software interface, not a software-only fallback")
                        validate_offload(body)
                        render(body, quick[:1], settings)  # Reject invalid bodies BEFORE starting a hardware build.
                        if patch != cached_patch:
                            cached_patch = patch
                            build = nodes.hardware.options(num_cpus=settings["build_jobs"], scheduling_strategy=nodes.pinned(node_id), max_retries=0).chia_remote(settings)
                        (attempt_dir / "memcpy_body.c").write_text(body)
                        (attempt_dir / "hardware.patch").write_text(patch)
                        for profile, cases in [("quick", quick), ("full", full)]:
                            profile_dir = attempt_dir / profile
                            profile_dir.mkdir()
                            rows = engine.evaluate(body, cases, build, cache, profile_dir, label + "/" + profile)
                        value = score(rows, baseline)
                        summary.update(status="passed", validated_profile="full", score=value)
                        summary["attempts"].append(dict(attempt=attempt, status="passed"))
                        (directory / "memcpy_body.c").write_text(body)
                        (directory / "hardware.patch").write_text(patch)
                        json_file(directory / "summary.json", summary)
                        design = Design(label, str(directory), body, patch, value, parent.fingerprint)
                        improved = coordinator.publish(design)
                        LOG.info("%s full-validated %.4fx; promoted=%s", label, value, improved)
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
                        if attempt + 1 < settings["max_attempts"]:
                            body = prompts.answer_body(ask(llm, tool, prompts.debug(getattr(error, "stage", "evaluation"), feedback, attempt + 1), directory, label + f"/debug_{attempt + 1}", engine), body)
            except Exception as error:
                summary["error"] = str(error)
                LOG.exception("%s stopped", label)
                return passed
            finally:
                summary["wall_seconds"] = time.monotonic() - iteration_start
                json_file(directory / "summary.json", summary)
    finally:
        tool.stop()
        try:
            get(nodes.workspace.options(scheduling_strategy=nodes.pinned(node_id)).chia_remote(settings["chipyard_path"], "reset", source_info))
        except Exception:
            LOG.exception("Agent %s workspace cleanup refused; inspect it before reuse", index)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", default=str(ROOT / "settings.yaml"))
    parser.add_argument("--address")
    parser.add_argument("--agents", type=int)
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--preflight", action="store_true", help="Read-only cluster/environment checks; no model calls or source edits")
    parser.add_argument("--placement-check", action="store_true", help="Verify parallel builder/tool placement and rejection of submodule writes")
    parser.add_argument("--list-cases", action="store_true", help="Offline suite counts")
    parser.add_argument("--baseline-only", action="store_true", help="Compile/build/simulate frozen baseline without a model")
    parser.add_argument("--baseline-run", help="Reuse a full-passed scale/out run after checking source/compiler/suite identity")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.settings)
    if args.agents is not None:
        if not 1 <= args.agents <= settings["agents"]:
            parser.error("--agents must be between 1 and settings.yaml agents")
        settings["agents"] = args.agents
    if args.iterations is not None:
        if args.iterations < 1:
            parser.error("--iterations must be positive")
        settings["iterations_per_agent"] = args.iterations
    quick, full = load_cases(settings, "quick"), load_cases(settings, "full")
    print(json.dumps(dict(quick=len(quick), configured_suite=len(full), available_cases=len(load_cases(settings, "all")), tests=sum(c.kind == "test" for c in full), benchmarks=sum(c.kind == "eval" for c in full), quick_batches=len(case_batches(quick, settings)), suite_batches=len(case_batches(full, settings)))), flush=True)
    if args.list_cases:
        return
    driver_lock = open("/tmp/chia-scale-driver.lock", "a")
    fcntl.flock(driver_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    connection = ray.init(address=args.address or settings["ray_address"], namespace="chia-scale", log_to_driver=False,
             runtime_env={"working_dir": str(ROOT), "excludes": ["out/", ".runtime/", ".venv/", "__pycache__/"]})
    settings["ray_address"] = connection.address_info["gcs_address"]
    leases, engine = [], None
    collector = False
    start = time.monotonic()
    try:
        builders, info, environment, physical = preflight(settings)
        print(json.dumps(dict(builders=[n["NodeManagerAddress"] for n in builders], versions=dict(compiler=environment["compiler"], model=environment["model"]), preexisting_changes=[i["preexisting_changes"] for i in info]), indent=2), flush=True)
        if args.preflight:
            return
        if not args.baseline_only and not args.placement_check and not environment["model"]["auth_files_present"]:
            raise RuntimeError("Model login is missing. On the head: docker exec -it scale-antigravity-0 agy. No optimization has started.")
        for n in builders:
            lease = nodes.BuilderLease.options(scheduling_strategy=nodes.pinned(n["NodeID"])).remote()
            ray.get(lease.ready.remote())
            leases.append(lease)
        if args.placement_check:
            placement_check(settings, builders)
            return
        directory = ROOT / "out" / (time.strftime("run_%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6])
        directory.mkdir(parents=True)
        identity = fingerprint(settings, full, environment)
        json_file(directory / "settings.json", settings)
        json_file(directory / "environment.json", environment)
        json_file(directory / "cases.json", [asdict(c) for c in full])
        engine = Engine(settings, directory, physical)
        start_collector(log_dir=str(directory / "profile"))
        collector = True
        engine.metric_thread.start()
        LOG.info("Results: %s", directory)
        try:
            if args.baseline_run:
                baseline, build, rows = reuse_baseline(engine, args.baseline_run, full, identity, environment)
            else:
                baseline, build, rows = run_baseline(engine, builders[0], quick, full, identity)
            coordinator = Coordinator(directory, baseline)
            if not args.baseline_only:
                optimization_start = time.monotonic()
                with ThreadPoolExecutor(max_workers=settings["agents"]) as pool:
                    futures = [pool.submit(run_agent, i, node, info[i], coordinator, engine, quick, full, build, rows) for i, node in enumerate(builders)]
                    count = sum(f.result() for f in as_completed(futures))
                optimization_elapsed = time.monotonic() - optimization_start
                json_file(directory / "run_summary.json", dict(validated_candidates=count, optimization_wall_seconds=optimization_elapsed,
                          validated_candidates_per_hour=3600 * count / optimization_elapsed,
                          best=asdict(coordinator.snapshot()), promotions=coordinator.history))
                if count == 0:
                    raise RuntimeError("No agent produced a fully validated candidate; see per-agent summaries")
            LOG.info("Best configured-suite-validated design: %s (%.4fx)", coordinator.snapshot().name, coordinator.snapshot().score)
        except Exception as error:
            json_file(directory / "failure.json", dict(error=str(error)))
            raise
    finally:
        if engine:
            engine.stop_metrics.set()
            engine.metric_thread.join(timeout=45)
            engine.summarize(time.monotonic() - start)
        if collector:
            stop_collector()
        for lease in leases:
            ray.kill(lease, no_restart=True)
        ray.shutdown()
        driver_lock.close()


if __name__ == "__main__":
    main()
