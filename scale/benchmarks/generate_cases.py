"""Enumerate the PINNED glibc loops, not a hand-written replacement suite.

Prints cases.csv; --verify compares it with the checked-in copy. Requires cc.
Only the deterministic call loops are executed, in a tiny host-side shim.
Upstream implementations and Linux's test driver are never compiled/run.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
COMMIT = "d2097651cc57834dbfcaa102ddfacae0d86cfb66"  # glibc-2.42


def generate():
    lock = json.loads((ROOT / "sources.json").read_text())
    rows, seen = [], set()
    for kind, source_path in [("eval", "benchtests/bench-memcpy.c"), ("test", "string/test-memcpy.c")]:
        source = (ROOT / "upstream" / source_path).read_text()
        assert hashlib.sha256(source.encode()).hexdigest() == lock["sha256"][source_path]
        main = source[source.index("test_main (void)"):]
        loops = main[main.index("  for (i = 0; i < 18; ++i)"):main.index("  return ret;")]
        if kind == "eval":
            loops = loops[:loops.index("  json_array_end (&json_ctx);")]
        else:
            loops = loops.replace("  do_random_tests ();", "")
        shim = r'''
#include <stdio.h>
#include <stddef.h>
static size_t page_size = 131072;
static int getpagesize(void) { return 4096; }
static void emit(size_t a, size_t b, size_t n, int both, int bounded) {
  a &= 4095; b &= 4095;
  if (bounded && (a + n >= page_size || b + n >= page_size)) return;
  for (int reverse = 0; reverse < (both ? 2 : 1); ++reverse)
    printf("%zu,%zu,%zu,%d\n", n, a, b, reverse);
}
'''
        if kind == "eval":
            shim += "typedef int json_ctx_t; static void do_test(json_ctx_t *c, size_t a, size_t b, size_t n, int both) { emit(a,b,n,both,1); }\n"
        else:
            shim += "static void do_test(size_t a,size_t b,size_t n) { emit(a,b,n,1,1); }\n"
            # do_test1's arguments are destination, source, size (opposite of do_test).
            shim += "static void do_test1(size_t dst,size_t src,size_t n) { emit(src,dst,n,1,0); }\n"
        shim += "int main(void) { size_t i,j,half_page=2048; int json_ctx=0;\n" + loops + "return 0; }\n"
        with tempfile.TemporaryDirectory(prefix="scale-glibc-") as directory:
            src, binary = Path(directory) / "enumerate.c", Path(directory) / "enumerate"
            src.write_text(shim)
            subprocess.run(["cc", "-std=c11", "-O2", str(src), "-o", str(binary)], check=True)
            output = subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout
        for line in output.splitlines():
            length, src, dst, reverse = map(int, line.split(","))
            key = (kind, length, src, dst, reverse)
            if key in seen:
                continue
            seen.add(key)
            rows.append((f"glibc_{kind}_{len(rows):04d}", *key, source_path))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["name", "kind", "length", "src_offset", "dst_offset", "reverse", "upstream"])
    writer.writerows(rows)
    return stream.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = generate()
    if args.verify:
        assert result == (ROOT / "cases.csv").read_text(), "cases.csv differs from pinned upstream loops"
        print("Pinned glibc case enumeration verified")
    else:
        print(result, end="")
