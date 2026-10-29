"""Native host-network workers: filesystem boundaries, reuse, and real CLI calls."""

from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import time
import pytest
import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia_dma.config_loader import HARDWARE_FILES, ROOT, SOFTWARE_FILES
from chia_dma.runtime import Runtime, render
from native_fixture import native_ssh

EXAMPLE_FILES = HARDWARE_FILES + SOFTWARE_FILES


def chia(configuration, action, path):
    """Tests invoke the upstream CLI, just as the documented procedure does."""
    result = subprocess.run(
        [sys.executable, "-m", "chia.cli.main", action, str(path), "-y"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-5000:] + result.stdout[-3000:]


def cluster(configuration, base, name, *, pools=False):
    configuration = copy.deepcopy(configuration)
    run = base / (name + "-" + base.name[-8:])
    run.mkdir()
    seed = run / "seed"
    for n in (*EXAMPLE_FILES, "build.sbt", "software/benchmark.c", "tests/memory-tests/src/copy-small/checksum.c"):
        p = seed / "tree" / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("FROZEN")
    (seed / "tree/frozen").write_text("preserved source")
    from conftest import commit_tree
    commit_tree(seed / "tree")
    if not pools:
        node = configuration["cluster"]["available_node_types"]["generation"]
        configuration["cluster"]["available_node_types"] = {"generation": node}
    path = render(configuration, run, seed)
    return configuration, run, seed, path


def attach(configuration):
    ray.init(
        address=configuration["runtime"]["ray_address"],
        namespace="native-proof",
    )


@ray.remote(num_cpus=0, max_concurrency=3)
class Ready:
    def __init__(self):
        self.count = 0

    def arrive(self):
        self.count += 1
        deadline = time.monotonic() + 30
        while self.count < 2:
            if time.monotonic() >= deadline:
                raise TimeoutError("Second generation worker did not reach BashTool")
            time.sleep(0.02)


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def boundary_probe(mark, parent_patch, ready, sql, delay=0.2):
    import os
    import shlex
    import time
    from pathlib import Path
    import ray
    from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
    from chia.base.tools.BashTool import BashTool
    from chia_dma.config_loader import HARDWARE_FILES, SOFTWARE_FILES
    EXAMPLE_FILES = HARDWARE_FILES + SOFTWARE_FILES

    # Match production's parent initialization before giving the agent its shell.
    from chia_dma.experiment import apply_candidate_diff
    apply_candidate_diff("/workspace", "/seed", parent_patch)
    assert all((Path("/workspace") / name).read_text() == mark for name in EXAMPLE_FILES)
    tool = BashTool(
        "boundary_bash",
        work_dir="/workspace",
        task_options={
            "scheduling_strategy": NodeAffinitySchedulingStrategy(
                ray.get_runtime_context().get_node_id(), soft=False
            )
        },
    )
    ray.get(ready.arrive.remote())
    started = time.time()
    try:
        script = (
            """from pathlib import Path
import errno
p=Path('/workspace/"""
            + EXAMPLE_FILES[0]
            + """')
print('PARENT='+p.read_text())
p.write_text(p.read_text()+' edited')
c=Path('/workspace/tests/memory-tests/src/copy-small/copy-small.c')
c.write_text(c.read_text()+' C edited')
for name in ['/workspace/build.sbt','/workspace/software/benchmark.c','/workspace/tests/memory-tests/src/copy-small/checksum.c']:
 Path(name).write_text('EDITED')
for name in ['/opt/loop/chia_dma/evaluation.py','/seed/tests/memory-tests/src/copy-small/checksum.c']:
 try: Path(name).write_text('FORBIDDEN')
 except OSError as error:
  assert error.errno in [errno.EROFS,errno.EACCES,errno.EPERM], str(error)
  print('DENIED='+name)
 else: raise RuntimeError('Forbidden write succeeded')
assert not Path('/var/run/docker.sock').exists()
assert not Path('/opt/loop/experiments').exists()
assert not Path('/opt/loop/chia_dma/correctness.py').exists()
assert not Path('/opt/loop/chia_dma/evaluation.py').exists()
"""
        )
        output = tool.run_command("python -c " + shlex.quote(script))
        time.sleep(delay)
        assert (Path("/workspace") / EXAMPLE_FILES[0]).read_text() == mark + " edited"
        assert (Path("/workspace") / EXAMPLE_FILES[1]).read_text() == mark
        from chia_dma.experiment import save_proposal
        from chia_dma.experiment import capture_patch

        assert not Path(sql.db_path).exists()
        save_proposal(
            sql,
            mark,
            capture_patch("/workspace", "/seed"),
            {"response": "BashTool boundary proof"},
            remote=True,
        )
        return {
            "node": ray.get_runtime_context().get_node_id(),
            "started": started,
            "ended": time.time(),
            "output": output,
        }
    finally:
        tool.stop()


def test_native_generation_parent_separation_forbidden_writes_and_serial_reuse(
    native_ssh, patch_for,
):
    configuration, base = native_ssh
    configuration = copy.deepcopy(configuration)
    credentials = base / "empty-credentials"
    credentials.mkdir()
    configuration["agent"]["credentials"] = str(credentials)
    configuration, run, seed, path = cluster(configuration, base, "boundaries")
    try:
        chia(configuration, "up", path)
        attach(configuration)
        from chia_dma.experiment import Experiment
        from skydiscover.search.base_database import Program

        experiment = Experiment(configuration, run)
        parents = {mark: patch_for({name: mark for name in EXAMPLE_FILES}, baseline=seed / "tree")
                   for mark in ("parent-A", "parent-B", "parent-C")}
        for mark, patch in parents.items():
            experiment.save_selection(
                Program(
                    id=mark,
                    solution=json.dumps({"patch": patch}),
                    metadata={"island": 0},
                ),
                [],
                candidate_id=mark,
            )
        ready = Ready.remote()
        results = [
            get(h)
            for h in [
                boundary_probe.chia_remote(mark, parents[mark], ready, experiment.sql, 1)
                for mark in ("parent-A", "parent-B", "parent-C")
            ]
        ]
        assert len({r["node"] for r in results}) == 2
        for mark, result in zip(("parent-A", "parent-B", "parent-C"), results):
            assert "PARENT=" + mark in result["output"]
            assert result["output"].count("DENIED=") == 2
            from chia_dma.experiment import initialize_checkout, apply_candidate_diff
            restored = run / ("verify-" + mark)
            initialize_checkout(restored, seed / "tree")
            apply_candidate_diff(restored, seed / "tree", experiment.load_proposal(mark)["patch"])
            assert (restored / EXAMPLE_FILES[0]).read_text() == mark + " edited"
            assert (restored / SOFTWARE_FILES[0]).read_text() == mark + " C edited"
        for a, b in [(results[i], results[j]) for i in range(3) for j in range(i)]:
            if a["node"] == b["node"]:
                assert a["started"] >= b["ended"] or b["started"] >= a["ended"]
        assert any(
            a["started"] < b["ended"] and b["started"] < a["ended"]
            for a, b in [
                (results[0], results[1]),
                (results[0], results[2]),
                (results[1], results[2]),
            ]
        )

        from chia_dma.agent import generate

        # A completed proposal returns before even reading the missing parent
        # field; reaching the agent path would fail this task.
        recovered = get(
            generate.chia_remote(configuration, experiment.sql, {"id": "parent-A"})
        )
        assert recovered["patch"] == experiment.load_proposal("parent-A")["patch"]
        experiment.close()
        assert all(
            (seed / "tree" / n).read_text() == "FROZEN"
            for n in (*EXAMPLE_FILES, "build.sbt", "software/benchmark.c", "tests/memory-tests/src/copy-small/checksum.c")
        )
    finally:
        ray.shutdown()
        chia(configuration, "down", path)


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def simulation_probe(configuration, mark):
    from chia.chipyard.state_def import BuildArtifact
    from chia_dma.observations import run_observed

    script = (
        "#!/bin/sh\necho BUILD_"
        + mark
        + '\nsleep 1\nfor arg; do last=$arg; done\ncat "$last"\n'
        + "python - <<'PY'\nimport sys,struct\n"
          "sys.stdout.buffer.write(b'CHIA-OUTPUT-0001'+struct.pack('<QQQ',1,0,123)+b'\\n')\nPY\n"
    ).encode()
    artifact = BuildArtifact(
        mark,
        script,
        "same-simulator",
        "fixture",
        "fixture",
        "verilator",
        True,
        "",
        "",
        0,
    )
    result = run_observed(
        artifact, ("INPUT_" + mark + "\n").encode(), "same-input",
        plusargs={"+loadmem": "same-input.riscv"},
        dram_settings={},
    )
    assert result.success and result.wall_seconds < 30
    assert "MEMORY_OBSERVATION words=1" in result.log
    return result.log.split("MEMORY_OBSERVATION")[0]


def test_native_build_simulation_worker_placement(native_ssh, base_tree):
    configuration, base = native_ssh
    configuration = copy.deepcopy(configuration)
    credentials = base / "placement-credentials"
    credentials.mkdir()
    configuration["agent"]["credentials"] = str(credentials)
    configuration, run, seed, path = cluster(configuration, base, "pools", pools=True)
    try:
        chia(configuration, "up", path)
        with Runtime(configuration, run):

            @ChiaFunction()
            def probe():
                import importlib
                import os
                import shutil
                from pathlib import Path

                p = Path("/home/ray/dma-build/frozen")
                if p.exists():
                    for executable in ("riscv64-unknown-elf-gcc", "sbt", "verilator"):
                        assert shutil.which(executable).startswith(
                            "/home/ray/chipyard/"
                        )
                return (
                    os.readlink("/proc/self/ns/mnt"),
                    (p.read_text() if p.exists() else None),
                    {
                        name: importlib.import_module(name).__file__
                        for name in (
                            "chia.base.ChiaFunction",
                            "chia_dma.agent",
                            "skydiscover.config",
                        )
                    },
                )

            build = get(probe.options(resources={"chipyard": 1}).chia_remote())
            simulation = get(
                probe.options(resources={"verilator_run": 1}).chia_remote()
            )
            generation = get(probe.options(resources={"generation": 1}).chia_remote())
            verification = get(probe.options(resources={"verification": 1}).chia_remote())
            software = get(probe.options(resources={"riscv_build": 1}).chia_remote())
            head = get(
                probe.options(resources={"node:__internal_head__": 0.001}).chia_remote()
            )
            assert (
                build[0] != simulation[0]
                and build[1] == "preserved source"
                and simulation[1] is None
            )
            assert head[0] == os.readlink("/proc/self/ns/mnt")
            assert all(
                worker[0] != head[0] for worker in (build, software, simulation, generation, verification)
            )
            assert verification[0] not in {build[0], simulation[0], generation[0]}
            assert head[2]["chia_dma.agent"] == str(ROOT / "chia_dma/agent.py")
            assert head[2]["chia.base.ChiaFunction"] == str(
                Path(configuration["chia"]) / "chia/base/ChiaFunction.py"
            )
            for worker in (build, software, simulation, generation, verification):
                assert worker[2]["chia_dma.agent"] == "/opt/loop/chia_dma/agent.py"
                assert (
                    worker[2]["chia.base.ChiaFunction"]
                    == "/opt/chia/chia/base/ChiaFunction.py"
                )
                assert (
                    worker[2]["skydiscover.config"]
                    == "/opt/skydiscover/skydiscover/config.py"
                )
            assert ray.cluster_resources()["generation"] == 2
            assert ray.cluster_resources()["riscv_build"] == 6
            from chia.chipyard.riscv_build_node import RiscvBuildNode
            from chia_dma.evaluation import software_inputs
            from chia_dma.experiment import read_json

            compiler = RiscvBuildNode(timeout_seconds=120)
            inputs = ray.put(software_inputs(base_tree, ""))
            workloads = read_json(base_tree / configuration["software"]["workloads"])
            compilations = {
                name: compiler.build_program.chia_remote(
                    compiler, inputs,
                    [*configuration["software"]["build_command"], "NAMES=" + name],
                    "/tmp/native-dma-software",
                    outputs=[configuration["software"]["elf"].format(benchmark=name)],
                )
                for name in workloads
            }
            for name, handle in compilations.items():
                compiled = get(handle)
                assert compiled.success, compiled.stderr
                content = compiled.files[configuration["software"]["elf"].format(benchmark=name)]
                assert content.startswith(b"\x7fELF") and content[18:20] == b"\xf3\x00"
            handles = [
                simulation_probe.chia_remote(configuration, mark) for mark in ("A", "B")
            ]
            assert [get(h).strip() for h in handles] == [
                "BUILD_A\nINPUT_A",
                "BUILD_B\nINPUT_B",
            ]
        job = subprocess.run(
            [
                sys.executable,
                "-m",
                "chia.cli.main",
                "job",
                "submit",
                "--chia-cluster",
                str(path),
                "--",
                "python",
                "-c",
                "import sys, chia_dma.agent; "
                f"assert sys.prefix == {sys.prefix!r}; "
                "print('HOST_HEAD_JOB_OK')",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert job.returncode == 0, job.stdout + job.stderr
        assert "HOST_HEAD_JOB_OK" in job.stdout
    finally:
        ray.shutdown()
        chia(configuration, "down", path)


@ChiaFunction(resources={"generation": 1}, max_retries=0)
def cli_checksum_probe(configuration, source):
    from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
    from chia.base.tools.BashTool import BashTool
    from chia_dma.agent import make_agent
    from chia_dma.experiment import capture_patch

    for name, text in source.items():
        (Path("/workspace") / name).write_text(text)
    os.chdir("/workspace")
    tool = BashTool(
        "dma_bash",
        work_dir="/workspace",
        timeout_seconds=120,
        task_options={
            "scheduling_strategy": NodeAffinitySchedulingStrategy(
                ray.get_runtime_context().get_node_id(), soft=False
            )
        },
    )
    try:
        prompt = (
            "Use dma_bash_run_command to run pwd and sha256sum /workspace/"
            + EXAMPLE_FILES[0]
            + ". Report the directory and checksum. Do not edit any files."
        )
        result = make_agent(configuration).prompt(prompt, [tool])
        assert result.success, result.stderr
        return {
            "response": result.result,
            "transcript": result.stream_result,
            "patch": capture_patch("/workspace", "/seed"),
        }
    finally:
        tool.stop()


@pytest.mark.skipif(
    os.environ.get("CHIA_LIVE_AGENTS") != "1",
    reason="Opt-in authenticated provider calls",
)
@pytest.mark.parametrize("backend", ["antigravity"])
def test_cli_backend_uses_native_bash_tool(native_ssh, backend):
    from chia_dma.config_loader import digest
    from chia_dma.experiment import write_json

    original, base = native_ssh
    configuration = copy.deepcopy(original)
    configuration["agent"]["backend"] = backend
    configuration, run, seed, path = cluster(configuration, base, "cli-" + backend)
    # New private content cannot be inferred from the checksum request.
    import uuid

    source = {n: uuid.uuid4().hex for n in EXAMPLE_FILES}
    try:
        chia(configuration, "up", path)
        attach(configuration)
        result = get(cli_checksum_probe.chia_remote(configuration, source))
        write_json(run / "result.json", result)
        assert digest(source[EXAMPLE_FILES[0]].encode()) in result["response"]
        from chia_dma.experiment import initialize_checkout, apply_candidate_diff
        restored = run / "verified-proposal"
        initialize_checkout(restored, seed / "tree")
        apply_candidate_diff(restored, seed / "tree", result["patch"])
        assert all((restored / name).read_text() == value for name, value in source.items())
        assert "dma_bash" in result["transcript"]
    finally:
        ray.shutdown()
        chia(configuration, "down", path)
