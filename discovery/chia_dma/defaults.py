"""Fixed settings and declarations; participant knobs live in cluster.yaml."""

DEFAULTS = {
    "agent": {
        "backend": "antigravity",
        "model": None,
        "effort": "medium",
        "timeout_seconds": 600,
        "retries": 1,  # CHIA counts total attempts: one call, no retry.
    },
    "search": {
        "random_seed": 42,
        "inspirations": 2,
        "num_islands": 3,
        "population_size": 40,
        "archive_size": 9,
        "feature_dimensions": ["source_bytes", "instructions"],
        "feature_bins": 3,
        "exploration_ratio": 0.3,
        "exploitation_ratio": 0.6,
        "elite_selection_ratio": 0.1,
        "migration_interval": 5,
        "migration_rate": 0.1,
        "island_guidance": [
            "Raise DMA throughput in hardware and adapt the C workloads to exploit it.",
            "Cut per-copy DMA setup and synchronization cost through coordinated hardware and C changes.",
            "Experiment boldly: invent a new DMA capability or way of using it that the other directions would not try, and rewrite the C to exploit it; higher-risk ideas are welcome.",
        ],
        "type": "openevolve_native",
        "iterations": 100,
        "max_in_flight": 6,
    },
    "hardware": {
        "config": "DMARocketConfig",
        "config_package": "chipyard",
        "make_jobs": 32,
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
    "base": {},
}

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

# prepare.sh is the recipe, not an input: its output is checked by baseline
# revisions, so editing it does not invalidate seeds or checkpoints.
BASE_FILE_PATTERNS = (
    "chipyard.patch",
    "memory-tests/Makefile",
    "memory-tests/workloads.json",
    "memory-tests/check.py",
    "memory-tests/src/*/*.c",
    "memory-tests/support/*.[ch]",
)
