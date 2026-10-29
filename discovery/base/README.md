# Base implementation

`chipyard.patch` contains the DMA accelerator and SoC configuration, passive
simulation observer, DMA `memcpy` wrapper, HTIF timing/statistics, and exit
workaround. It applies to the Chipyard checkout initialized in the published
CHIA build image named in `../cluster.yaml` (`:latest`).

[memory-tests/](memory-tests/) supplies six short baremetal C workloads, their
expected checksums, and a Makefile that links the existing DMA/HTIF runtime.
The loop recompiles the candidate workloads alongside the hardware and runs one
simulation task per ELF for each candidate. Per-test `checksum.c` files own the
output storage and verification; evaluation uses the preserved seed copies. The suite README describes each workload and native validation.

`prepare.sh` runs inside that build image. It copies tracked source and
initialized submodule Git metadata, applies the patch, and copies the memory-test
Makefile, manifest, check script, sources, and support files into `tests/memory-tests/`.
Generated host binaries, Python caches, and build outputs are not copied.
It records the prepared tutorial baseline in the existing Git repositories,
retaining their original history. `prepare` freezes the complete source and Git
metadata under `experiments/seeds/HASH/`. Each candidate is saved as one Git patch
relative to those baseline commits, including changes inside initialized submodules.

New seed identities include the image, base revision, patch/script, and every
memory-test build input. Resume uses the saved seed without comparing current
code or settings against its fingerprint.
Startup prepares private build and generation checkouts once, then uses Git
reset/clean and patch application for each candidate. The seed stays read-only. The
evaluation node stages the original checksum readers and measurement harness
separately from the candidate checkout. No host Chipyard
checkout or external Embench checkout is read.

To change the baseline, edit the bundled patch or C suite, then start a new run.
Existing runs retain their saved seed and accept current code and settings. Embench checkpoints cannot resume under this new workload contract.
