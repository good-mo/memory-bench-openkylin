"""评测追踪（manifest / 指纹 / 可复现性校验）测试。"""

import json
import os
import tempfile
import unittest

from memory_bench.report.tracking import (
    build_manifest,
    compare_manifests,
    compute_repro_fingerprint,
    environment_fingerprint,
    load_manifest,
    load_run_index,
    record_run,
    write_manifest,
)
from memory_bench.scenarios.library import builtin_scenarios


class TestTracking(unittest.TestCase):
    def setUp(self):
        self.scenario = builtin_scenarios()["demo_update"]
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_track_")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_compute_repro_fingerprint_deterministic(self):
        a = compute_repro_fingerprint(self.scenario, "dummy", 42)
        b = compute_repro_fingerprint(self.scenario, "dummy", 42)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_fingerprint_changes_with_agent_or_seed(self):
        base = compute_repro_fingerprint(self.scenario, "dummy", 42)
        self.assertNotEqual(
            base, compute_repro_fingerprint(self.scenario, "dummy", 43)
        )
        self.assertNotEqual(
            base, compute_repro_fingerprint(self.scenario, "bad", 42)
        )

    def test_environment_fingerprint_has_keys(self):
        env = environment_fingerprint()
        for key in ("python", "platform", "machine", "git_commit"):
            self.assertIn(key, env)
        self.assertTrue(env["python"].startswith("3"))

    def test_build_manifest_shape(self):
        manifest = build_manifest(
            run_id="run-test-1",
            scenario=self.scenario,
            agent_name="dummy",
            seed=42,
            evidence_stats={"total": 10, "by_type": {}},
            scoring_summary={"overall": {"score": 0.8}},
        )
        self.assertEqual(manifest["run_id"], "run-test-1")
        self.assertEqual(manifest["scenario_id"], "demo_update")
        self.assertEqual(manifest["agent"], "dummy")
        self.assertEqual(manifest["seed"], 42)
        self.assertEqual(manifest["evidence"]["total"], 10)
        self.assertEqual(manifest["scoring"]["overall"]["score"], 0.8)
        self.assertIn("repro_fingerprint", manifest)
        self.assertIn("env", manifest)

    def test_write_and_load_manifest_roundtrip(self):
        manifest = build_manifest(
            run_id="run-test-2",
            scenario=self.scenario,
            agent_name="dummy",
            seed=1,
            evidence_stats={"total": 3, "by_type": {}},
        )
        path = write_manifest(self.root, manifest)
        self.assertTrue(os.path.exists(path))
        loaded = load_manifest(path)
        self.assertEqual(loaded["run_id"], "run-test-2")
        self.assertEqual(loaded["repro_fingerprint"], manifest["repro_fingerprint"])

    def test_record_and_load_run_index(self):
        index_path = os.path.join(self.root, "runs.ndjson")
        for i in range(3):
            manifest = build_manifest(
                run_id="run-{}".format(i),
                scenario=self.scenario,
                agent_name="dummy",
                seed=i,
                evidence_stats={"total": i, "by_type": {}},
            )
            record_run(index_path, manifest)
        runs = load_run_index(index_path)
        self.assertEqual(len(runs), 3)
        self.assertEqual([r["seed"] for r in runs], [0, 1, 2])

    def test_load_run_index_missing_file(self):
        self.assertEqual(load_run_index(os.path.join(self.root, "nope.ndjson")), [])

    def test_load_run_index_skips_bad_lines(self):
        index_path = os.path.join(self.root, "runs.ndjson")
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write("not-json\n")
            fh.write(json.dumps({"run_id": "ok"}) + "\n")
        runs = load_run_index(index_path)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "ok")

    def test_compare_manifests_identical(self):
        left = build_manifest(
            run_id="run-a",
            scenario=self.scenario,
            agent_name="dummy",
            seed=42,
            evidence_stats={"total": 5, "by_type": {}},
            scoring_summary={"overall": {"score": 0.8}},
        )
        right = dict(left)
        right["run_id"] = "run-b"
        result = compare_manifests(left, right)
        self.assertTrue(result["reproducible"])
        self.assertTrue(result["fingerprint_same"])
        self.assertTrue(result["evidence_same"])
        self.assertTrue(result["scoring_same"])

    def test_compare_manifests_different_seed(self):
        left = build_manifest(
            run_id="run-a",
            scenario=self.scenario,
            agent_name="dummy",
            seed=42,
            evidence_stats={"total": 5, "by_type": {}},
        )
        right = build_manifest(
            run_id="run-b",
            scenario=self.scenario,
            agent_name="dummy",
            seed=43,
            evidence_stats={"total": 5, "by_type": {}},
        )
        result = compare_manifests(left, right)
        self.assertFalse(result["reproducible"])
        self.assertFalse(result["fields"]["seed"])


if __name__ == "__main__":
    unittest.main()