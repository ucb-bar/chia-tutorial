"""Keep Angela's software -> hardware -> evaluate -> debug sequence."""
import re
from suite import body_from


RULES = """You co-design a RoCC memcpy accelerator for Chipyard FastRTLSimRocketConfig.
The software signature is int memcpy_rocc(const void *src, void *dst, int len).
Copy exactly len non-overlapping bytes for all alignments, leave src unchanged,
return 1 only after all writes are visible to ordinary core loads. Use rocc.h
custom0..custom3 macros consistently. You may change ONLY the function body.

Use chipyard_bash to read and edit files in your dedicated builder checkout.
Edit hardware under generators/chipyard/src/main/scala/; only that directory is
saved in design patches and reset between iterations. Do not change submodules, git revisions,
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
Use chipyard_bash for Chipyard files: the CLI's local filesystem is the
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
    return (f"Your independent strategy: {strategy}\n"
            f"Suite sizes: {counts}. Full validation, not quick results, decides promotion.\n"
            f"Starting design {parent.name}, full-suite speedup {parent.score:.4f}x.\n"
            f"Current software body:\n{parent.body}\nCurrent hardware patch:\n{parent.patch[-60000:]}\n"
            "First design the software interface. Explain your choices and return the software body.")


def hardware():
    return ("Now implement the hardware half using chipyard_bash. "
            "The selected parent patch is already applied to your dedicated builder. "
            "Implement exactly the interface from your preceding software answer. "
            "If you revise that interface, return an updated software body; otherwise omit body markers.")


def concise_feedback(feedback, max_chars=4000):
    """Keep diagnostic lines and nearby source context, not build boilerplate.

    This changes model input only. Original failure text and raw build/simulation
    logs remain in the attempt directory; validation and scoring are unchanged.
    """
    lines = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", feedback or "").splitlines()
    if not lines:
        return "No diagnostic text was returned. Full logs are in the attempt directory."[:max_chars]
    diagnostic = re.compile(
        r"^\s*\[error\]|\b(?:fatal\s+)?error\s*:|\bassertion failed\b|"
        r"\bexception\b|^\s*(?:TEST|EVAL)\s+\S+\s+FAIL\b|"
        r"\btimed?\s*out\b|\btimeout\b|\bout of memory\b|\bpermission denied\b|"
        r"\bno space left\b|\bundefined reference\b|\bno such file\b", re.I)
    focus = [i for i, line in enumerate(lines) if diagnostic.search(line)]
    indices = {0}
    for i in focus[:12]:
        indices.update(range(max(0, i - 1), min(len(lines), i + 3)))
    if not focus:
        indices.update(range(max(1, len(lines) - 12), len(lines)))
    selected, seen = [], set()
    for i in sorted(indices):
        line = lines[i].rstrip()
        # Context can land on a huge echoed make/Java command. Do not let that
        # crowd out the compiler diagnostic we selected the context for.
        if i and len(line) > 500 and not diagnostic.search(line):
            continue
        if line and line not in seen:
            seen.add(line)
            selected.append(line[:500] + (" ... [line shortened]" if len(line) > 500 else ""))
    note = "\n[Condensed feedback; full failure text and logs are in the attempt directory.]"
    return "\n".join(selected)[:max(0, max_chars - len(note))] + note[:max_chars]


def debug(stage, feedback, attempt, max_chars=4000):
    return f"Debug attempt {attempt}: failure at {stage}. Form a concrete hypothesis, fix it, and return an updated body if needed.\n{concise_feedback(feedback, max_chars)}"


def answer_body(answer, previous=None):
    if not answer.success or not (answer.result or "").strip():
        raise RuntimeError("Model call failed or returned an empty answer: " + (answer.stderr or ""))
    try:
        return body_from(answer.result)
    except ValueError:
        if previous is not None:
            return previous
        raise
