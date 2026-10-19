"""Put a _chia_tag on accel's dispatches without touching accel's code.

The cache only writes a call that carries a tag, and accel/nodes.py passes
none. Rather than re-writing their dispatches, this installs the tag at the
source: a @ChiaFunction exposes ``chia_remote`` and ``options`` as attributes,
and ``options(...)`` returns a fresh handle each call, so both can be wrapped
once per function. Their code then tags itself:

    node.build.options(resources=...).chia_remote(node)   # -> _chia_tag="hw_0001"

Tags are call-order sequence numbers plus a readable label, so a replay of the
same loop looks up the same keys. An explicit _chia_tag always wins.
"""

from __future__ import annotations

import itertools

from chia.chipyard.chisel_build_node import ChiselBuildNode
from chia.chipyard.riscv_build_node import RiscvBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.models.antigravity import AntigravityLLM

# kind -> the call's label, read from the arguments the loop already passes.
TAGGED = {
    "hw": (ChiselBuildNode, "build", lambda a, k: ""),
    "sw": (RiscvBuildNode, "build", lambda a, k: a[2] if len(a) > 2 else ""),
    "sim": (VerilatorRunNode, "run", lambda a, k: a[3] if len(a) > 3 else ""),
    "llm": (AntigravityLLM, "prompt", lambda a, k: ""),
}

_counters: dict[str, itertools.count] = {}
_installed = False


def _tag(kind: str, label: str) -> str:
    n = next(_counters[kind])
    return f"{kind}_{n:04d}" + (f"_{label}" if label else "")


def install() -> list[str]:
    """Tag every call to the four accel node functions. Idempotent."""
    global _installed
    if _installed:
        return []
    done = []
    for kind, (cls, method, label_of) in TAGGED.items():
        fn = getattr(cls, method)
        _counters[kind] = itertools.count(1)
        _wrap(fn, kind, label_of)
        done.append(f"{cls.__name__}.{method} -> {kind}_*")
    _installed = True
    return done


def _wrap(fn, kind: str, label_of) -> None:
    orig_remote, orig_options = fn.chia_remote, fn.options

    def tagged_remote(*args, **kwargs):
        kwargs.setdefault("_chia_tag", _tag(kind, label_of(args, kwargs)))
        return orig_remote(*args, **kwargs)

    def options(**override):
        handle = orig_options(**override)
        inner = handle.chia_remote

        def chia_remote(*args, **kwargs):
            kwargs.setdefault("_chia_tag", _tag(kind, label_of(args, kwargs)))
            return inner(*args, **kwargs)

        handle.chia_remote = chia_remote
        return handle

    fn.chia_remote = tagged_remote
    fn.options = options
