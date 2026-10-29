#!/usr/bin/env python3
"""Measure a fresh baseline build and one or two parallel RTL suites locally."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import hashlib
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import time

from chia_dma.defaults import DEFAULTS

ROOT = Path(__file__).resolve().parent
IMAGES = {
    "build": "ghcr.io/ucb-bar/chia-chisel-build@sha256:9eca4dffdce51835005d5971864306d84fe46cc2dd2feb440521b98a14d84300",
    "software": "ghcr.io/ucb-bar/chia-riscv-cross@sha256:4a1987d1cb7998ad4c430dd4434e19843344dd73b88d5ec2bdbe5f1dbcbf5429",
    "rtl": "ghcr.io/ucb-bar/chia-verilator-run@sha256:188e6ce07df32b8acaefede77fd4691e44da889a318527bf9eff047bb4c655c1",
}


def save(filename, value):
    Path(filename).write_text(json.dumps(value, indent=2) + "\n")


def build():
    from chia.chipyard.chisel_build_node import ChiselBuildNode
    started = time.monotonic()
    tree = Path("/tmp/timing-chipyard")
    shutil.copytree("/seed/tree", tree, symlinks=True)
    for folder, _, files in os.walk(tree, followlinks=False):
        Path(folder).chmod(Path(folder).stat().st_mode | 0o700)
        for name in files:
            target = Path(folder) / name
            if not target.is_symlink():
                target.chmod(target.stat().st_mode | 0o600)
    for name in (".sbt", ".ivy2"):
        if not (tree / name).exists():
            (tree / name).symlink_to(Path("/home/ray/chipyard") / name)
    materialized = time.monotonic()
    hw = DEFAULTS["hardware"]
    result = ChiselBuildNode(
        str(tree), hw["config"], config_package=hw["config_package"],
        make_jobs=len(os.sched_getaffinity(0)),
        timeout_seconds=hw["build_timeout_seconds"],
        extra_make_args=hw["extra_make_args"], clean=False, bundle_runtime_libs=True,
    ).build()
    Path("/measure/hardware.log").write_text(result.stdout + "\n" + result.stderr)
    save("/measure/hardware-timing.json", {
        "materialize_seconds": materialized - started,
        "compile_seconds": time.monotonic() - materialized,
        "wall_seconds": time.monotonic() - started, "success": result.success,
    })
    with open("/measure/hardware.pkl", "wb") as stream:
        pickle.dump(result, stream)
    if not result.success:
        raise RuntimeError("Baseline hardware build failed; see hardware.log")


def software():
    from chia.chipyard.riscv_build_node import RiscvBuildNode
    started = time.monotonic()
    suite = Path("/base/memory-tests")
    names = list(json.loads((suite / "workloads.json").read_text()))
    inputs = {"tests/memory-tests/" + str(path.relative_to(suite)): path.read_bytes()
              for path in suite.rglob("*") if path.is_file() and "build" not in path.relative_to(suite).parts}
    seed = Path("/seed/tree")
    runtime = seed / "software/rocc-dma"
    inputs.update({str(path.relative_to(seed)): path.read_bytes()
                   for path in runtime.rglob("*") if path.is_file()})
    inputs["tests/atexit-workaround.c"] = (seed / "tests/atexit-workaround.c").read_bytes()
    outputs = [f"tests/memory-tests/build/src/{name}/{name}" for name in names]
    result = RiscvBuildNode(timeout_seconds=None).build_program(
        inputs, ["make", "-s", "-j6", "-C", "tests/memory-tests", "baremetal"],
        "/tmp/timing-software", outputs=outputs,
    )
    Path("/measure/software.log").write_text(result.stdout + "\n" + result.stderr)
    save("/measure/software-timing.json", {
        "wall_seconds": time.monotonic() - started, "success": result.success,
    })
    if not result.success:
        raise RuntimeError("Baseline software build failed; see software.log")
    with open("/measure/software.pkl", "wb") as stream:
        pickle.dump({name: result.files[path] for name, path in zip(names, outputs)}, stream)


def rtl():
    import ray
    import secrets
    from chia_dma.correctness import seeded_binary, reference_outputs, matches_output
    from chia_dma.observations import run_observed
    started = time.monotonic()
    ray.init(address="local", num_cpus=8, include_dashboard=False, logging_level="ERROR")
    with open("/measure/hardware.pkl", "rb") as stream:
        simulator = pickle.load(stream)
    with open("/measure/software.pkl", "rb") as stream:
        binaries = pickle.load(stream)
    ini = Path("/seed/tree") / DEFAULTS["software"]["dram_ini"]
    dram = {path.name: path.read_bytes() for path in ini.glob("*.ini")}
    cases = [(name, name, secrets.randbits(64) or 1) for name in binaries]
    # Calibration includes two substantial private cases, with fresh seeds.
    cases += [("hidden-1", "copy-stream", secrets.randbits(64) or 1),
              ("hidden-2", "copy-mixed", secrets.randbits(64) or 1)]
    rounds = int(os.environ.get("CHIA_TIMING_ROUNDS", "1"))
    if rounds > 1:
        cases = [(f"round-{index + 1}-" + label, name, secrets.randbits(64) or 1)
                 for index in range(rounds) for label, name, _ in cases]
    records = {}
    batch = time.monotonic()
    def run(case):
        label, name, seed = case
        result = run_observed(
            simulator, seeded_binary(binaries[name], seed), name,
            {"+loadmem": name + ".riscv", "+verilator+seed+42": "", "+cycle-count": ""},
            dram,
        )
        passed = matches_output(result, reference_outputs(seed)[name])
        # Private contents are discarded; only anonymous times enter the report.
        if "hidden-" not in label:
            Path("/measure/" + label + ".log").write_text(result.log + "\n" + result.out)
        return label, {"wall_seconds": result.wall_seconds,
                       "success": passed, "worker_host": result.worker_host}
    try:
        with ThreadPoolExecutor(max_workers=8 * rounds) as pool:
            for future in as_completed([pool.submit(run, case) for case in cases]):
                label, record = future.result()
                records[label] = record
                print(label, record, flush=True)
        save("/measure/rtl-timing.json", {
            "tests": records, "parallel_rtl_seconds": time.monotonic() - batch,
            "parallel_rounds": rounds,
            "wall_seconds": time.monotonic() - started,
            "success": all(row["success"] for row in records.values()),
        })
    finally:
        ray.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments" / ("timing-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--chia", type=Path, default=ROOT / ".deps/chia")
    parser.add_argument("--seed", type=Path, help="Reuse a prepared immutable Chipyard seed")
    parser.add_argument("--parallel-rounds", type=int, choices=(1, 2), default=1,
                        help="Measure one suite or the default two overlapping candidate suites")
    args = parser.parse_args()
    output, chia = args.output.resolve(), args.chia.resolve()
    if not (chia / "chia/base/ChiaFunction.py").is_file():
        parser.error("Run bootstrap.py first, or pass --chia with the pinned CHIA checkout")
    if args.seed:
        manifest = json.loads((args.seed / "manifest.json").read_text())
        patch_digest = hashlib.sha256((ROOT / "base/chipyard.patch").read_bytes()).hexdigest()
        if manifest.get("files", {}).get("chipyard.patch") != patch_digest:
            parser.error("The supplied seed does not match the current base patch; omit --seed")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    seed = args.seed.resolve() if args.seed else output / "seed"
    if args.seed is None:
        seed.mkdir()
        subprocess.run([
            "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{ROOT / 'base'}:/base:ro", "-v", f"{seed}:/seed",
            "--entrypoint", "bash", IMAGES["build"], "/base/prepare.sh", DEFAULTS["base"]["chipyard_revision"],
        ], check=True)
        save(seed / "manifest.json", {
            "files": {"chipyard.patch": hashlib.sha256((ROOT / "base/chipyard.patch").read_bytes()).hexdigest()},
            "chipyard_revision": DEFAULTS["base"]["chipyard_revision"],
        })
    prepared = time.monotonic()
    def phase(name):
        cidfile = output / (name + ".cid")
        command = ["docker", "run", "--rm", "--cidfile", str(cidfile), "--entrypoint", "bash",
                   "-e", "PYTHONPATH=/opt/loop:/opt/chia",
                   "-e", f"CHIA_TIMING_ROUNDS={args.parallel_rounds}",
                   "-v", f"{output}:/measure", "-v", f"{seed}:/seed:ro",
                   "-v", f"{ROOT / 'base'}:/base:ro",
                   "-v", f"{ROOT}:/opt/loop:ro", "-v", f"{chia / 'chia'}:/opt/chia/chia:ro",
                   IMAGES[name]]
        setup = ""
        if name == "build":
            setup = ("source /home/ray/chipyard/env.sh; "
                     "export PATH=/home/ray/anaconda3/bin:/home/ray/chipyard/.conda-env/riscv-tools/bin:/home/ray/chipyard/.conda-env/bin:/usr/local/bin:/usr/bin:/bin; "
                     "export JAVA_TOOL_OPTIONS='-Xmx32G -Xss8M -Djava.io.tmpdir=/tmp -Duser.home=/home/ray'; ")
        try:
            with (output / (name + "-container.log")).open("w") as log:
                subprocess.run(command + ["-c", setup + "python /opt/loop/measure_local.py " + name],
                               check=True, stdout=log, stderr=subprocess.STDOUT,
                               timeout=900 if name == "build" else None)
        finally:
            if cidfile.exists():
                subprocess.run(["docker", "rm", "-f", cidfile.read_text().strip()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(phase, ("build", "software")))
    built = time.monotonic()
    phase("rtl")
    report = {
        "measured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": f"fresh baseline evaluation; source staging, concurrent hardware/software builds, {8 * args.parallel_rounds} parallel RTL tests and full-output correctness; no agent generation",
        "placement": "single local host using Docker; not a GCP measurement",
        "host": os.uname().nodename, "cpus": len(os.sched_getaffinity(0)),
        "images": IMAGES, "base_revision": DEFAULTS["base"]["chipyard_revision"],
        "patch_sha256": hashlib.sha256((ROOT / "base/chipyard.patch").read_bytes()).hexdigest(),
        "source_prepare_seconds": prepared - started,
        "parallel_build_stage_seconds": built - prepared,
        "round_wall_seconds": time.monotonic() - started,
        "parallel_rounds": args.parallel_rounds,
        "workloads": json.loads((ROOT / "base/memory-tests/workloads.json").read_text()),
    }
    for name in ("hardware", "software", "rtl"):
        report[name] = json.loads((output / (name + "-timing.json")).read_text())
    report["success"] = all(report[name]["success"] for name in ("hardware", "software", "rtl"))
    save(output / "timing.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    phases = {"build": build, "software": software, "rtl": rtl}
    if len(sys.argv) == 2 and sys.argv[1] in phases:
        phases[sys.argv[1]]()
    else:
        raise SystemExit(main())
