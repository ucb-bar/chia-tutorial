# Base implementation

`chipyard.patch` contains the DMA accelerator and SoC configuration, passive
simulation observer, DMA `memcpy` wrapper, HTIF timing/statistics, and exit
workaround. It applies to Chipyard
`4ab72313087580a44d647b52923389a06ec0712f`, initialized in the published CHIA
build image pinned in `../config.yaml`.

[memory-tests/](memory-tests/) supplies six short baremetal C workloads, their
expected checksums, and a Makefile that links the existing DMA/HTIF runtime.
The loop recompiles the candidate workloads alongside the hardware and runs one
simulation task per ELF for each candidate. Per-test `checksum.c` files own the
output storage and verification; these remain read-only. The suite README describes each workload and native validation.

`prepare.sh` runs inside the pinned image. It copies tracked source and
initialized submodule markers, applies the patch, and copies the memory-test
Makefile, manifest, check script, sources, and support files into `tests/memory-tests/`.
Generated host binaries, Python caches, and build outputs are not copied.
`prepare` freezes the complete source under `experiments/seeds/HASH/`.

The seed identity and frozen experiment contract include the image, base
revision, patch/script, evaluator code, and every memory-test build input.
Build workers copy that seed into a private checkout; generation mounts it
read-only except for the two candidate Scala files and six C workload files. No host Chipyard
checkout or external Embench checkout is read.

To change the baseline, edit the bundled patch or C suite, then start a new run.
Existing runs retain their saved seed and require matching evaluation code and
settings. Embench checkpoints cannot resume under this new workload contract.
