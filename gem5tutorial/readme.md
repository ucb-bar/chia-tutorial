# Adding decoding and execution support for RISCV Extensions (for OpenSSL SHA) into Gem5

This tutorial contains a CHIA loop for implementing basic support for a new RISCV extension in Gem5.

The CHIA loop ([loop.py](loop.py)) patches and builds gem5 on a gem5 worker, then copies over RISC-V OpenSSL benchmarks (SHA-256, SHA-512 and ChaCha20) that use vector-crypto instructions gem5 doesn't support yet. It then asks an Antigravity (Gemini) agent to implement `vsha2ms`, `vsha2ch`, `vsha2cl` and `vrev8` in gem5, giving it tools to edit, build and run gem5 on that worker, and saves the agent's transcript, summary and source diff. Finally, it rebuilds gem5 with the agent's changes, simulates every benchmark on the O3 CPU, and reports any whose output is wrong.

## Bring up the cluster

```bash
cd ~/chia-tutorial/gem5tutorial
conda activate chia_env
chia up cluster.yaml -y
```

## Submit the CHIA loop to the cluster

```bash
cd ~/chia-tutorial
conda activate chia_env
chia job submit --working-dir . -- python gem5tutorial/loop.py
```

## Bring the cluster down

```bash
cd ~/chia-tutorial/gem5tutorial
conda activate chia_env
chia down cluster.yaml -y
```

## Outputs

Each run of the loop writes its results on chia-dev to `~/chia-tutorial/gem5tutorial/runs/<YYYYMMDD-HHMMSS>/`:

- `agent_transcript.txt`: the agent's full streamed session, including its reasoning and every tool call it made and the result it got back. Read this to see how the agent arrived at its changes.
- `agent_summary.txt`: the agent's final reply, which lists the instructions it implemented, where it put them, its pass/fail result for each workload, and anything it left unfinished.
- `gem5_source_state.diff`: every change to gem5's `src/` relative to `origin/HEAD`, which is the agent's work plus the VLEFF patch the loop applied before the agent started. Applying it to a clean gem5 checkout reproduces the final source.

The pass/fail results of the final O3 simulations aren't saved to a file. The loop prints them to the job's log, where any benchmark whose output is wrong is reported as `Test <name> Failed`.

