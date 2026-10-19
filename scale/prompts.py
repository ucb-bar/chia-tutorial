"""Keep Angela's software -> hardware -> evaluate -> debug sequence."""
from pathlib import Path
from suite import body_from


RULES = """You co-design a RoCC memcpy accelerator for Chipyard FastRTLSimRocketConfig.
The software signature is int memcpy_rocc(const void *src, void *dst, int len).
Copy exactly len non-overlapping bytes for all alignments, leave src unchanged,
return 1 only after all writes are visible to ordinary core loads. Use rocc.h
custom0..custom3 macros consistently. You may change ONLY the function body.

Your chipyard_source tools read/list/search source and write complete files.
Hardware writes are restricted to generators/chipyard/src/main/scala/*.scala.
All third-party submodules are read-only. NEVER change submodules, git revisions,
build scripts, the frozen harness, benchmark vectors, scoring, or output logs.
Read generators/rocket-chip/src/main/scala/tile/LazyRoCC.scala and
generators/chipyard/src/main/scala/config/fragments/RoCCFragments.scala.
Attach your accelerator to FastRTLSimRocketConfig in
generators/chipyard/src/main/scala/config/RocketConfigs.scala.
Do not build or simulate yourself. The loop does that and provides feedback.
Do not special-case tests, bypass the accelerator, or return fabricated output.
The accelerator MUST perform the copy. Never call memcpy/memmove or add a CPU
copy loop after a dummy RoCC command. Return a real RoCC interface, not the
software baseline as a placeholder. No preprocessor directives in the body.
Use chipyard_source for Chipyard files: the CLI's local filesystem is the
model container, NOT the builder. Do not search that local filesystem for Chipyard.
The compiler's rocc.h provides these exact public macro signatures:
ROCC_INSTRUCTION_SS(X, rs1, rs2, funct)
ROCC_INSTRUCTION_S(X, rs1, funct)
ROCC_INSTRUCTION_DSS(X, rd, rs1, rs2, funct)
ROCC_INSTRUCTION_D(X, rd, funct)
Use X=0 for custom0. Do not invent macro names or use low-level R_R_R with
five arguments: that low-level macro requires additional register numbers.

If supplying software, return its complete body between these EXACT lines:
// ===== memcpy_rocc body: BEGIN =====
... C statements ...
// ===== memcpy_rocc body: END =====
No new includes, globals, or helper functions outside the body.
"""


def software(parent, strategy, counts):
    harness = Path(__file__).with_name("memcpy_rocc.c").read_text()
    return (RULES + f"\nYour independent strategy: {strategy}\n"
            f"Suite sizes: {counts}. Full validation, not quick results, decides promotion.\n"
            f"Starting design {parent.name}, full-suite speedup {parent.score:.4f}x.\n"
            f"Current software body:\n{parent.body}\nCurrent hardware patch:\n{parent.patch[-60000:]}\n"
            f"Frozen harness (case table is filled by the loop):\n{harness}\n"
            "First design the software interface. Explain your choices and return the software body.")


def hardware():
    return (RULES + "\nNow implement the hardware half using chipyard_source tools. "
            "The selected parent patch is already applied to your dedicated builder. "
            "Implement exactly the interface from your preceding software answer. "
            "If you revise that interface, return an updated software body; otherwise omit body markers.")


def debug(stage, feedback, attempt):
    return RULES + f"\nDebug attempt {attempt}: failure at {stage}. Form a concrete hypothesis, fix it, and return an updated body if needed.\n{feedback[-40000:]}"


def answer_body(answer, previous=None):
    if not answer.success or not (answer.result or "").strip():
        raise RuntimeError("Model call failed or returned an empty answer: " + (answer.stderr or ""))
    try:
        return body_from(answer.result)
    except ValueError:
        if previous is not None:
            return previous
        raise
