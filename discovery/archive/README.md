# Optional evaluator features

`evaluation_extras.py` preserves configurable workload weighting and parsing of
individual DMA hardware/copy events. The tutorial does not import this module:
it scores all six workloads equally and retains raw simulator logs for debugging.

The required CHIA worker tasks live in `../chia_dma/nodes.py`; the evaluation
controller lives in `../chia_dma/evaluation.py`. Correctness checks and temporary-file
cleanup remain active; RTL simulations use CHIA's native 30-minute timeout. Build output is saved once
per task as `artifacts/CANDIDATE/NAME-build.log`.
