## Debug attempt ${ATTEMPT} of 4

The design you wrote in this conversation failed at the **${STAGE}** stage.
Find the root cause and fix it: in the Chisel (edit the chipyard tree with
`chipyard_bash_run_command`), in the software body, or both.

Rules:
- Do not detach, bypass or disable the accelerator, and do not fall back to a
  software-only copy: the point is a working accelerator.
- Form a concrete hypothesis from the evidence below (RoCC cmd/resp
  handshakes, `rd` writes, memory request/response handling, address and
  length bookkeeping, alignment, the `busy` signal, ordering with the core's
  loads) and fix that.
- Do NOT build or simulate yourself; the flow reruns every stage after your
  answer.

If you change the software, end your answer with the full new body between
the markers:

```
// ===== memcpy_rocc body: BEGIN =====
    ...
// ===== memcpy_rocc body: END =====
```

Without the markers, the current body is kept.

${FEEDBACK}
