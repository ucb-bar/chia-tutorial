"""Fixed settings and declarations; participant knobs live in cluster.yaml."""

DEFAULTS = {
    "agent": {
        "backend": "antigravity",
        "model": None,
        "effort": "high",
        "timeout_seconds": 600,
        "retries": 1,  # CHIA counts total attempts: one call, no retry.
        "credentials": "${HOME}/.gemini",
        "credential_target": "/tmp/agent-home/.gemini",
    },
    "search": {
        "random_seed": 42,
        "inspirations": 3,
        "num_islands": 2,
        "population_size": 40,
        "archive_size": 100,
        "feature_dimensions": ["source_bytes", "instructions"],
        "feature_bins": 8,
        "exploration_ratio": 0.2,
        "exploitation_ratio": 0.7,
        "elite_selection_ratio": 0.1,
        "migration_interval": 2,
        "migration_rate": 0.1,
        "island_guidance": [
            "Reduce instruction count through C algorithm and data-layout changes.",
            "Reduce cycles and memory stalls through coordinated C and DMA changes.",
        ],
        "type": "openevolve_native",
        "iterations": 2,
        "max_in_flight": 2,
    },
    "hardware": {
        "config": "DMARocketConfig",
        "config_package": "chipyard",
        "make_jobs": "${CPUS}",
        "build_timeout_seconds": 600,
        "extra_make_args": {
            "CXX": "/home/ray/chipyard/.conda-env/bin/x86_64-conda-linux-gnu-c++",
            "JAVA_HEAP_SIZE": "32G",
        },
        "clean": False,
    },
    "software": {
        "build_timeout_seconds": None,
        "build_command": ["make", "-C", "tests/memory-tests", "baremetal"],
        "workloads": "tests/memory-tests/workloads.json",
        "elf": "tests/memory-tests/build/src/{benchmark}/{benchmark}",
        "dram_ini": "generators/testchipip/src/main/resources/dramsim2_ini",
        "random_seed": 42,
        "plusargs": {"+cycle-count": "", "+loadmem": "/test.elf"},
    },
    "runtime": {
        "cache_gb": 4,
        "ray_address": "${CHIA_HEAD}:6387",
    },
    "storage": "./experiments",
    "base": {"chipyard_revision": "4ab72313087580a44d647b52923389a06ec0712f"},
    "chia": "./.deps/chia",
    "skydiscover": "./.deps/evolve-flows/skydiscover",
    "evolve_flows": "./.deps/evolve-flows",
    "dependencies": {
        "evolve_flows_url": "https://github.com/ucb-bar/evolve-flows.git",
        "evolve_flows_revision": "62d271304d4675c21b946e327bf542a1f62c1012",
        "chia_url": "https://github.com/ucb-bar/chia.git",
        "chia_revision": "a4cedf24b131c459d7f176453d8e0cea97dda796",
        "python_packages": [
            "openai==3.35.1",
            "pydantic==2.12.4",
            "numpy==1.26.4",
            "python-dotenv==1.2.1",
            "tqdm==4.67.1",
        ],
    },
}

# Copy the setup.md login into a private agent home.
AGENT_SETUP = [
    "mkdir -p ${CREDENTIAL_TARGET}",
    "rsync -a --exclude conversations --exclude sessions --exclude projects --exclude logs "
    '--exclude "*.log" --exclude tmp --exclude history.jsonl --exclude shell_snapshots '
    '--exclude "*.sock" --exclude app-server-control --exclude app-server-daemon '
    "/credentials/ ${CREDENTIAL_TARGET}/",
]

# Tutorial example paths, not an edit allowlist.
HARDWARE_FILES = (
    "generators/chipyard/src/main/scala/dma/DMAController.scala",
    "generators/chipyard/src/main/scala/dma/RoCCDMA.scala",
)

SOFTWARE_FILES = (
    "tests/memory-tests/src/copy-small/copy-small.c",
    "tests/memory-tests/src/copy-stream/copy-stream.c",
    "tests/memory-tests/src/copy-gather/copy-gather.c",
    "tests/memory-tests/src/copy-scatter/copy-scatter.c",
    "tests/memory-tests/src/copy-chain/copy-chain.c",
    "tests/memory-tests/src/copy-mixed/copy-mixed.c",
)

RELATIVE_PATHS = ("storage", "chia", "skydiscover", "evolve_flows")

BASE_FILE_PATTERNS = (
    "chipyard.patch",
    "prepare.sh",
    "memory-tests/Makefile",
    "memory-tests/workloads.json",
    "memory-tests/check.py",
    "memory-tests/src/*/*.c",
    "memory-tests/support/*.[ch]",
)
