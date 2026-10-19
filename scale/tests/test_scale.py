"""Offline tests. No cloud, model calls, or third-party checkout mutations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from accel_loop import batches, case_batches, load_settings, Engine, EvaluationError, reuse_baseline
from coordinator import Coordinator, Design
from nodes import EDIT_ROOT, SourceTool, editable, source_path, validate_patch, workspace, inspect_builder
from suite import Case, body_from, category, fingerprint, load_cases, parse_results, render, score, validate_offload
from build_worker import target_settings


class SuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings(ROOT / "settings.yaml")
        cls.full = load_cases(cls.settings, "full")
        cls.quick = load_cases(cls.settings, "quick")
        cls.body = body_from((ROOT / "memcpy_rocc.c").read_text())

    def test_pinned_upstream_enumeration(self):
        subprocess.run([sys.executable, str(ROOT / "benchmarks/generate_cases.py"), "--verify"], check=True)

    def test_quick_is_deterministic_subset_and_has_both_directions(self):
        self.assertEqual(self.quick, load_cases(self.settings, "quick"))
        self.assertTrue(set(self.quick) <= set(self.full))
        self.assertEqual(len(self.quick), 64)
        self.assertGreater(len(self.full), 1000)
        self.assertEqual({c.reverse for c in self.quick}, {0, 1})
        self.assertEqual({c.kind for c in self.quick}, {"test", "eval"})
        self.assertEqual(min(c.length for c in self.quick), 0)
        self.assertEqual(max(c.length for c in self.quick), self.settings["max_case_bytes"])

    def test_fixed_1024_subset_preserves_upstream_categories(self):
        available = load_cases(self.settings, "all")
        self.assertEqual(len(available), 4488)
        self.assertEqual(len(self.full), 1024)
        self.assertEqual(self.full, load_cases(self.settings, "full"))
        self.assertEqual(len(set(self.full)), 1024)
        self.assertTrue(set(self.full) <= set(available))
        self.assertEqual(load_cases(dict(self.settings, case_limit=0)), available)
        for kind in ("test", "eval"):
            chosen = [c for c in self.full if c.kind == kind]
            self.assertEqual(len(chosen), 512)
            self.assertEqual({category(c) for c in chosen}, {category(c) for c in available if c.kind == kind})

    def test_adaptive_batches_cover_exact_cases_with_both_caps(self):
        groups = case_batches(self.full, self.settings)
        self.assertEqual([c for group in groups for c in group], self.full)
        self.assertTrue(all(1 <= len(group) <= 8 for group in groups))
        for group in groups:
            cost = sum((c.length + 128) * (self.settings["benchmark_repeats"] + self.settings["benchmark_warmups"] if c.kind == "eval" else 1) for c in group)
            self.assertLessEqual(cost, self.settings["batch_work_bytes"])
            if any(c.kind == "eval" and c.length == 16384 for c in group):
                self.assertLessEqual(sum(c.kind == "eval" and c.length == 16384 for c in group), 2)
        with self.assertRaises(ValueError): batches([Case("large", "eval", 16384, 0, 0, 0, "fixture")], 8, 100)

    def test_geomean_and_no_missing_results(self):
        rows = [dict(name="a", kind="eval", cycles=10), dict(name="b", kind="eval", cycles=40)]
        self.assertAlmostEqual(score(rows, dict(a=20, b=20)), 1.0)
        with self.assertRaises(ValueError): score(rows[:1], dict(a=20, b=20))
        with self.assertRaises(ValueError): score([dict(name="a", kind="eval", cycles=0)], dict(a=20))

    def test_parser_rejects_failures_missing_duplicate_wrong_kind_and_length(self):
        case = self.quick[0]
        good = f'{case.kind.upper()} {case.name} PASS bytes={case.length} cycles=10 ret=1 mismatches=0\nDONE\n'
        self.assertEqual(len(parse_results([case], good, 0)), 1)
        bad = [good.replace("PASS", "FAIL"), good.replace("ret=1", "ret=0"), good.replace("mismatches=0", "mismatches=1"), good.replace("DONE", ""), good + good, good.replace(f"bytes={case.length}", f"bytes={case.length + 1}"), good.replace(case.kind.upper(), "EVAL" if case.kind == "test" else "TEST")]
        for log in bad:
            with self.subTest(log=log), self.assertRaises(ValueError): parse_results([case], log, 0)
        with self.assertRaises(ValueError): parse_results([case], good, 1)

    def test_fingerprint_changes_with_suite_harness_settings_or_environment(self):
        original = fingerprint(self.settings, self.full, dict(compiler="v1"))
        self.assertNotEqual(original, fingerprint(self.settings, self.full[:-1], dict(compiler="v1")))
        self.assertNotEqual(original, fingerprint(self.settings, self.full, dict(compiler="v2")))
        self.assertNotEqual(original, fingerprint(dict(self.settings, simulation_threads=2), self.full, dict(compiler="v1")))
        self.assertNotEqual(original, fingerprint(dict(self.settings, batch_work_bytes=65536), self.full, dict(compiler="v1")))
        self.assertNotEqual(original, fingerprint(dict(self.settings, cases_per_binary=4), self.full, dict(compiler="v1")))

    def test_native_quick_and_full_corpus(self):
        # Host-mode cycle counters are synthetic: these are CORRECTNESS tests only.
        for name, cases in [("quick", self.quick), ("configured_1024", self.full), ("available_4488", load_cases(self.settings, "all"))]:
            with self.subTest(profile=name):
                parsed = []
                with tempfile.TemporaryDirectory(prefix="scale-host-test-") as directory:
                    for i, group in enumerate(batches(cases, 128)):
                        src, binary = Path(directory) / "test.c", Path(directory) / "test"
                        src.write_text(render(self.body, group, self.settings))
                        subprocess.run(["cc", "-O2", "-fno-builtin-memcpy", "-DSCALE_HOST_TEST", str(src), "-o", str(binary)], check=True, capture_output=True)
                        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                        parsed.extend(parse_results(group, result.stdout, result.returncode))
                self.assertEqual(len(parsed), len(cases))

    def test_native_guards_wrong_return_and_source_modification(self):
        cases = [Case("small", "eval", 8, 3, 5, 0, "test fixture")]
        bodies = ["memcpy(dst, src, len); ((unsigned char *)dst)[len] = 0; return 1;",
                  "memcpy(dst, src, len); ((unsigned char *)src)[0] ^= 1; return 1;",
                  "memcpy(dst, src, len); return 0;", "return 1;"]
        for body in bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory(prefix="scale-negative-test-") as directory:
                src, binary = Path(directory) / "test.c", Path(directory) / "test"
                src.write_text(render(body, cases, self.settings))
                subprocess.run(["cc", "-O2", "-DSCALE_HOST_TEST", str(src), "-o", str(binary)], check=True, capture_output=True)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                with self.assertRaises(ValueError): parse_results(cases, result.stdout, result.returncode)

    def test_body_cannot_escape_or_redefine_harness(self):
        for body in ["} int fake(void) { return 1;", "#define printf(...) 0\nreturn 1;", "{ return 1;"]:
            with self.assertRaises(ValueError): render(body, self.quick, self.settings)
        render('/* } #define */ const char *x = "}"; return ({ 1; });', self.quick, self.settings)

    def test_dummy_rocc_plus_software_copy_is_rejected(self):
        for body in ["memcpy(dst, src, len); return 1;", "ROCC_INSTRUCTION_SS(0, src, dst, 0); memcpy(dst, src, len); return 1;", "ROCC_INSTRUCTION_SS(0, src, dst, 0); __builtin_memmove(dst, src, len); return 1;", "/* ROCC_INSTRUCTION_SS(0, src, dst, 0); */ return 1;"]:
            with self.assertRaises(ValueError): validate_offload(body)
        validate_offload("ROCC_INSTRUCTION_SS(0, src, dst, 0); /* no memcpy(dst, src, len) */ return 1;")


class PermissionTests(unittest.TestCase):
    def test_sbt_projects_get_distinct_external_targets(self):
        output = "[info] In file:/home/ray/chipyard/\n[info]   * chipyardRoot\n[info]     rocketchip\n[info]     testchipip\n"
        command = target_settings(output)
        for name in ["chipyardRoot", "rocketchip", "testchipip"]:
            self.assertIn(f'LocalProject("{name}") / target := file("/tmp/chia-scale-sbt-projects/{name}")', command)
        self.assertNotIn("every", command)
        with self.assertRaises(RuntimeError): target_settings("[error] project discovery failed")

    def test_edit_allowlist_and_symlink_escape(self):
        self.assertTrue(editable(EDIT_ROOT + "example/Foo.scala"))
        for name in ["generators/rocket-chip/Foo.scala", "build.sbt", EDIT_ROOT + "../Foo.scala", EDIT_ROOT + "bad name.scala"]:
            self.assertFalse(editable(name))
        with tempfile.TemporaryDirectory(prefix="scale-path-test-") as directory:
            root = Path(directory)
            (root / EDIT_ROOT).mkdir(parents=True)
            forbidden = root / "submodule.scala"
            forbidden.write_text("untouched")
            (root / EDIT_ROOT / "Link.scala").symlink_to(forbidden)
            with self.assertRaises(ValueError): source_path(root, EDIT_ROOT + "Link.scala", write=True)
            self.assertEqual(forbidden.read_text(), "untouched")

    def test_tool_rejects_submodule_writes_before_opening(self):
        tool = object.__new__(SourceTool)
        with tempfile.TemporaryDirectory(prefix="scale-tool-test-") as directory:
            tool.root = directory
            with self.assertRaises(ValueError): tool.write_file("generators/rocket-chip/Forbidden.scala", "bad")
            self.assertFalse((Path(directory) / "generators/rocket-chip").exists())
            self.assertIn("Wrote", tool.write_file(EDIT_ROOT + "Allowed.scala", "allowed"))

    def test_patch_rejects_submodule_and_traversal_paths(self):
        path = EDIT_ROOT + "Allowed.scala"
        validate_patch(f"diff --git a/{path} b/{path}\n")
        for path in ["generators/rocket-chip/Foo.scala", "build.sbt", EDIT_ROOT + "../../Foo.scala"]:
            with self.assertRaises(ValueError): validate_patch(f"diff --git a/{path} b/{path}\n")

    def test_workspace_reset_is_scoped_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory(prefix="scale-git-test-") as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            scala = root / EDIT_ROOT / "Existing.scala"
            scala.parent.mkdir(parents=True)
            scala.write_text("original\n")
            (root / ".gitignore").write_text("cache/\n")
            (root / "image-setup.txt").write_text("original image\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            (root / "image-setup.txt").write_text("pre-existing image customization\n")
            expected = inspect_builder._chia_original(str(root))
            scala.write_text("changed\n")
            created = scala.with_name("Created.scala")
            created.write_text("agent file\n")
            cache = root / "cache" / "keep"
            cache.parent.mkdir(); cache.write_text("keep cached builds")
            patch = workspace._chia_original(str(root), "capture", expected)
            validate_patch(patch)
            workspace._chia_original(str(root), "reset", expected)
            self.assertEqual(scala.read_text(), "original\n")
            self.assertFalse(created.exists())
            self.assertEqual(cache.read_text(), "keep cached builds")
            self.assertEqual((root / "image-setup.txt").read_text(), "pre-existing image customization\n")
            workspace._chia_original(str(root), "apply", expected, patch)
            self.assertEqual(scala.read_text(), "changed\n")
            self.assertTrue(created.exists())
            (root / "image-setup.txt").write_text("unexpected external change\n")
            with self.assertRaises(RuntimeError): workspace._chia_original(str(root), "reset", expected)

    def test_kernel_write_policy_blocks_forbidden_fake_checkout(self):
        with tempfile.TemporaryDirectory(prefix="scale-landlock-test-") as directory:
            allowed, forbidden = Path(directory) / "allowed", Path(directory) / "third-party"
            allowed.mkdir(); forbidden.mkdir()
            code = "from build_worker import protect_writes; from pathlib import Path; import sys; protect_writes([sys.argv[1], '/dev/null']); Path(sys.argv[1], 'ok').write_text('ok'); Path(sys.argv[2], 'forbidden').write_text('bad')"
            result = subprocess.run([sys.executable, "-c", code, str(allowed), str(forbidden)], cwd=ROOT, capture_output=True, text=True)
            if "required" in result.stderr or "Cannot create Landlock" in result.stderr:
                self.skipTest("Host kernel lacks Landlock; real build will fail closed")
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((allowed / "ok").is_file(), result.stderr)
            self.assertFalse((forbidden / "forbidden").exists())


class CoordinatorTests(unittest.TestCase):
    def test_three_agents_publish_only_valid_full_results_atomically(self):
        with tempfile.TemporaryDirectory(prefix="scale-coordinator-test-") as directory:
            root = Path(directory)
            baseline = Design("baseline", str(root), "body", "", 1.0, "suite1")
            coordinator = Coordinator(root, baseline)
            barrier = threading.Barrier(3)
            parents, lock = [], threading.Lock()
            def agent(index):
                parent = coordinator.snapshot()
                with lock: parents.append(parent.name)
                barrier.wait(timeout=5)
                path = root / str(index); path.mkdir()
                value = 1.1 + index / 10
                (path / "summary.json").write_text(json.dumps(dict(status="passed", validated_profile="full", fingerprint="suite1", score=value)))
                coordinator.publish(Design(str(index), str(path), "body", "patch", value, "suite1"))
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(agent, range(3)))
            self.assertEqual(parents, ["baseline"] * 3)
            self.assertAlmostEqual(coordinator.snapshot().score, 1.3)
            self.assertAlmostEqual(json.loads((root / "best.json").read_text())["score"], 1.3)
            path = root / "invalid"; path.mkdir()
            for status, profile, identity in [("failed", "full", "suite1"), ("passed", "quick", "suite1"), ("passed", "full", "different_suite")]:
                (path / "summary.json").write_text(json.dumps(dict(status=status, validated_profile=profile, fingerprint=identity, score=2.0)))
                with self.assertRaises(ValueError): coordinator.publish(Design("bad", str(path), "", "", 2.0, identity))
            with self.assertRaises(ValueError): coordinator.publish(Design("nan", str(path), "", "", math.nan, "suite1"))
            self.assertAlmostEqual(coordinator.snapshot().score, 1.3)


class BaselineTests(unittest.TestCase):
    def test_reuse_requires_full_logs_and_matching_measurement_environment(self):
        settings = load_settings(ROOT / "settings.yaml")
        settings["cases_per_binary"] = 1
        cases = load_cases(settings, "quick")[:2]
        builder = dict(commit="same", submodules="same", protected_state="same")
        old_environment = dict(compiler=dict(version="same"), builders=[builder] * 3, model=dict(auth=False))
        current_environment = dict(compiler=dict(version="same"), builders=[builder], model=dict(auth=True))
        with tempfile.TemporaryDirectory(prefix="scale-baseline-test-") as directory:
            root = Path(directory)
            (root / "memcpy_rocc.c").write_text((ROOT / "memcpy_rocc.c").read_text())
            previous = root / "out/previous"
            baseline = previous / "baseline"
            (baseline / "full").mkdir(parents=True)
            body = body_from((ROOT / "memcpy_rocc.c").read_text())
            (baseline / "memcpy_body.c").write_text(body)
            (baseline / "hardware.patch").write_text("")
            (previous / "settings.json").write_text(json.dumps(settings))
            (previous / "environment.json").write_text(json.dumps(old_environment))
            summary = dict(status="passed", validated_profile="full", fingerprint=fingerprint(settings, cases, old_environment))
            (baseline / "summary.json").write_text(json.dumps(summary))
            for index, case in enumerate(cases):
                (baseline / f"full/batch_{index:04d}.log").write_text(f'{case.kind.upper()} {case.name} PASS bytes={case.length} cycles=10 ret=1 mismatches=0\nDONE\n')
            output = root / "out/new"
            output.mkdir()
            engine = Engine(settings, output, {})
            with patch("accel_loop.ROOT", root):
                design, build, rows = reuse_baseline(engine, previous, cases, "new_identity", current_environment)
                self.assertEqual(len(rows), len(cases))
                self.assertIsNone(build)
                self.assertEqual(design.fingerprint, "new_identity")
                changed = dict(current_environment, compiler=dict(version="different"))
                with self.assertRaises(ValueError): reuse_baseline(engine, previous, cases, "new_identity", changed)
                (baseline / "full/batch_0000.log").write_text("DONE\n")
                with self.assertRaises(ValueError): reuse_baseline(engine, previous, cases, "new_identity", current_environment)
                (baseline / "summary.json").write_text(json.dumps(dict(summary, validated_profile="quick")))
                with self.assertRaises(ValueError): reuse_baseline(engine, previous, cases, "new_identity", current_environment)


class EngineTests(unittest.TestCase):
    def test_three_evaluations_share_budgets_cache_exact_sources_and_reject_failed_hardware(self):
        settings = load_settings(ROOT / "settings.yaml")
        settings.update(compile_slots=2, simulation_slots=2, cases_per_binary=1)
        cases = load_cases(settings, "quick")[:2]
        body = body_from((ROOT / "memcpy_rocc.c").read_text())
        active, maximum = dict(compile=0, sim=0), dict(compile=0, sim=0)
        lock = threading.Lock()
        compile_calls = []
        def enter(kind):
            with lock:
                active[kind] += 1
                maximum[kind] = max(maximum[kind], active[kind])
        def leave(kind):
            with lock: active[kind] -= 1
        def compile_fake(source, name, settings):
            enter("compile")
            try:
                time.sleep(.02)
                with lock: compile_calls.append(source)
                return SimpleNamespace(success=True, source=source)
            finally: leave("compile")
        def simulate_fake(artifact, elf, settings):
            enter("sim")
            try:
                time.sleep(.02)
                case = next(c for c in cases if f'{{"{c.name}"' in elf.source)
                return SimpleNamespace(returncode=0, out="", log=f'{case.kind.upper()} {case.name} PASS bytes={case.length} cycles=10 ret=1 mismatches=0\nDONE\n')
            finally: leave("sim")
        with tempfile.TemporaryDirectory(prefix="scale-engine-test-") as directory:
            root = Path(directory)
            engine = Engine(settings, root, {})
            artifact = SimpleNamespace(success=True)
            caches = [{} for _ in range(3)]
            def evaluate(index, changed_body=body):
                target = root / f'agent{index}_{len(list(root.glob("agent*")))}'
                target.mkdir()
                return engine.evaluate(changed_body, cases, artifact, caches[index], target, f"agent{index}")
            with patch("accel_loop.get", side_effect=lambda ref: ref), patch("accel_loop.ray.put", side_effect=lambda value: value), patch("accel_loop.nodes.compile_software", side_effect=compile_fake), patch("accel_loop.nodes.simulate", side_effect=simulate_fake):
                with ThreadPoolExecutor(max_workers=3) as pool:
                    rows = list(pool.map(evaluate, range(3)))
                self.assertEqual([len(r) for r in rows], [2, 2, 2])
                self.assertEqual(len(compile_calls), 6)
                self.assertLessEqual(maximum["compile"], 2)
                self.assertLessEqual(maximum["sim"], 2)
                evaluate(0)
                self.assertEqual(len(compile_calls), 6, "Identical complete sources should reuse ELFs")
                evaluate(0, body + "\n/* software changed */\n")
                self.assertEqual(len(compile_calls), 8)
                failed = root / "failed"; failed.mkdir()
                with patch("accel_loop.get", side_effect=RuntimeError("hardware failed")):
                    with self.assertRaises(EvaluationError) as error:
                        engine.evaluate(body, cases, artifact, caches[0], failed, "failure")
                    self.assertEqual(error.exception.stage, "hardware build")


if __name__ == "__main__":
    unittest.main()
