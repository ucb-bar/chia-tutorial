

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.tools.ChiaTool import ChiaTool
from chia.base.tools.BashTool import BashTool

from chia.simulators.gem5 import Gem5Node, Gem5SourceState
from chia.models.antigravity import AntigravityLLM

import ray
from pathlib import Path
import os
import subprocess
import sys
import time

# Paths on the Gem5 Worker
RAY_ROOT = Path("/home/ray")
GEM5_ROOT = RAY_ROOT.joinpath("gem5")
WORKLOAD_DIR = RAY_ROOT.joinpath("crypto_workloads")
ELF_DIR = Path(__file__).resolve().parent / "riscv-crypto-elfs"
SE_PY = f"{GEM5_ROOT}/configs/deprecated/example/se.py"
GEM5_BIN = f"{GEM5_ROOT}/build/RISCV/gem5.opt"

# Path on head machine (development machine)
OUT = Path.home() / "gem5tutorial" / "runs" / time.strftime("%Y%m%d-%H%M%S")
OUT.mkdir(parents=True, exist_ok=True)

CPU_ARGS = ["--cpu-type=RiscvO3CPU", "--caches", "--l2cache"]

# Puts elfs on Gem5 worker
@ChiaFunction()
def stage_workloads(elfs: dict[str, bytes]):
    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for fname, data in elfs.items():
        p = WORKLOAD_DIR.joinpath(fname)
        p.write_bytes(data)
        os.chmod(p, 0o755)

# Check's success of simulation on Gem5 worker
@ChiaFunction(resources={"gem5": 0.05})
def check_success(elf):
    out = f"/home/ray/verify_runs/{Path(elf).stem}"
    rc = subprocess.run([sys.executable, f"{out}/program.out"]).returncode
    return rc

def main():

    # Use the passed version of CHIA (most up to date)
    ray.init(address="auto", runtime_env={
        "py_modules": [str(Path(__file__).resolve().parent.parent / "chia" / "chia")],
        "excludes": ["**/__pycache__", "**/*.pyc"],
    })

    # Setup objects
    g5 = Gem5Node(require_colocated=True, reserve_bundle={"CPU": 10, "gem5": 1})
    g5bash = BashTool("gem5_bash_tool", GEM5_ROOT, task_options=g5.task_options)
    ag = AntigravityLLM("gemini-4-argon-low", timeout_seconds=3600 * 2, mcp_timeout_seconds=3600)

    # Patch and build Gem5 workspace
    VLEFF_FIX = (Path(__file__).resolve().parent / "riscv-vleff-reset.patch").read_text()
    get(g5.restore_gem5_source_state.chia_remote(str(GEM5_ROOT), Gem5SourceState(base_rev="origin/HEAD", source_diff=VLEFF_FIX)))
    build = g5.build_gem5.chia_remote(GEM5_ROOT, timeout_s=7200, jobs=24)

    # Prepare the gem5 workspace with the workloads we want
    elfs: dict[str, bytes] = {p.name: p.read_bytes() for p in ELF_DIR.glob("*.elf")}
    get(stage_workloads.options(**g5.task_options).chia_remote(elfs))

    b = get(build)
    assert b.success, b.stderr_tail

    print()
    print("Gem5 built and workloads shipped.")
    print("Starting Antigravity...")
    print()

    # Ask An LLM to implement the vector crypto instruction set extension in Gem5
    # Save the changes it made, it's transcript, and a summary of the changes it made
    prompt = Path(Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
    g5Tool : ChiaTool = g5.spawn_tool("gem5_build_run_tool", gem5_root=GEM5_ROOT, workloads=WORKLOAD_DIR,
                                      config_script=SE_PY, kernel_flag="--cmd", config_args=CPU_ARGS + ["--output=program.out"],)
    res = get(ag.prompt.chia_remote(ag, prompt, [g5Tool, g5bash]))
    if not res.success: print(res.stderr)
    (OUT / Path("agent_transcript.txt")).write_text(res.stream_result)
    (OUT / Path("agent_summary.txt")).write_text(res.result)

    state = get(g5.capture_gem5_source_state.chia_remote(str(GEM5_ROOT), base_rev="origin/HEAD"))
    (OUT / Path("gem5_source_state.diff")).write_text(state.source_diff)

    print()
    print("Antigravity Finished.")
    print("Re-building and simulating on workloads...")
    print()

    # Simulate on benchmarks which have vector crypto instructions when they compile.
    b = get(g5.build_gem5.chia_remote(str(GEM5_ROOT), jobs=24, timeout_s=7200))
    assert b.success, b.stderr_tail

    sims = []
    for elf in elfs.keys():
        out = f"/home/ray/verify_runs/{Path(elf).stem}"
        sims.append(g5.run_gem5.options(**g5.task_options, resources={"gem5": 0.05}).chia_remote(GEM5_BIN, SE_PY, out,
                    config_args=["--cmd", str(WORKLOAD_DIR / elf), *CPU_ARGS, f"--output={out}/program.out"]))

    for sim, elf in zip(sims, elfs):
        res = get(sim)
        if res.status == "ok":
            rc = get(check_success.options(**g5.task_options).chia_remote(elf))
        else:
            rc = -1
        if rc != 0:
            print("Test " + Path(elf).stem + " Failed")

    print("Done simulating!")

if __name__ == "__main__":
    main()