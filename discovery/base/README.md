# Base implementation

`chipyard.patch` contains the tutorial's DMA accelerator and SoC configuration,
passive simulation observer, DMA software, metrics parser, and the 19 Embench
workloads with their build support and licenses. A DMA-only Makefile target calls
Embench’s SCons build; CHIA owns execution, caching, and saved outputs. The
metrics parser accepts Embench results and passive DMA observations. The DMA
hardware is captured from the local implementation; the supporting software is
narrowed to this Embench-only tutorial. Standalone microbenchmarks are omitted.

The patch applies to Chipyard `4ab72313087580a44d647b52923389a06ec0712f`, already
initialized in the published CHIA chisel-build image pinned in `../config.yaml`.
Embench inputs come from `09c2ed8c3b7008c95d08b038de4a3f6dc103ed70`.
The image supplies its own pinned generator submodules and toolchain; this patch
does not import unrelated changes from the surrounding development checkout.

`prepare.sh` runs inside that image. It copies tracked source and initialized
submodule markers, then applies the patch. `prepare` freezes this complete tree
under `experiments/seeds/HASH/`; build workers copy it into their private checkout.
The manifest identifies the image, base revision, and patch/script hashes.
Neither a new run nor a restored run reads the enclosing Chipyard checkout.

To change the baseline, edit/regenerate this patch against the pinned image's
source, keeping benchmark licenses and required build inputs. Start a new run:
patch/script hashes are part of the frozen experiment contract. Existing runs
retain their saved seed; matching loop code/settings are required to resume.
There is no automatic capture of later edits outside `discovery/`.
