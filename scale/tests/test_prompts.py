"""Offline checks for compact diagnostics; no model or cluster calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prompts import concise_feedback, debug


class FeedbackTests(unittest.TestCase):
    def test_compiler_error_survives_long_build_boilerplate(self):
        error = "[error] /src/MemCpyRoCC.scala:150:18: not found: value LazyModule"
        raw = ("Hardware build failed:\n" + "[info] resolving dependencies\n" * 600
               + "java " + "long-build-argument " * 900 + "\n" + error
               + "\n[error]     val copy = LazyModule(new MemCpyRoCC)\n[error]                  ^\n"
               + "[info] cleanup complete\n" * 600)
        result = concise_feedback(raw)
        self.assertIn(error, result)
        self.assertIn("val copy = LazyModule", result)
        self.assertIn("^", result)
        self.assertNotIn("long-build-argument", result)
        self.assertLessEqual(len(result), 4000)

    def test_ansi_and_duplicate_errors_are_removed(self):
        line = "[error] Missing symbol"
        result = concise_feedback("Build failed\n\x1b[31m" + line + "\x1b[0m\n" + line)
        self.assertNotIn("\x1b", result)
        self.assertEqual(result.count(line), 1)

    def test_simulator_failures_and_assertions_are_preserved(self):
        raw = ("Simulator incomplete or failed (rc=255)\n"
               "TEST aligned PASS bytes=8 cycles=20 ret=1 mismatches=0\n"
               "TEST tail FAIL bytes=7 cycles=58 ret=1 mismatches=6\n"
               "%Error: SimpleHellaCacheIF.sv:133: Assertion failed: cache exception\n"
               "    at SimpleHellaCacheIF.scala:137 assert(!s2_req_fire)\n")
        result = concise_feedback(raw)
        self.assertIn("rc=255", result)
        self.assertIn("TEST tail FAIL", result)
        self.assertIn("SimpleHellaCacheIF.scala:137", result)

    def test_no_diagnostic_falls_back_to_bounded_tail(self):
        raw = "Build failed\n" + "startup noise\n" * 500 + "final diagnostic detail"
        result = concise_feedback(raw, 600)
        self.assertIn("Build failed", result)
        self.assertIn("final diagnostic detail", result)
        self.assertLessEqual(len(result), 600)

    def test_large_errors_and_small_budgets_are_bounded(self):
        raw = "Build failed\n" + "[error] " + "x" * 30000
        for size in [1, 50, 300, 4000]:
            with self.subTest(size=size):
                self.assertLessEqual(len(concise_feedback(raw, size)), size)
        self.assertIn("[error]", concise_feedback(raw))

    def test_empty_feedback_and_prompt_budget(self):
        self.assertIn("No diagnostic", concise_feedback(""))
        raw = "Build failed\n" + "[error] missing import\n" * 2000
        prompt = debug("hardware build", raw, 1, 500)
        self.assertIn("Debug attempt 1: failure at hardware build", prompt)
        self.assertIn("[error] missing import", prompt)
        self.assertLess(len(prompt), 4000)


if __name__ == "__main__":
    unittest.main()
