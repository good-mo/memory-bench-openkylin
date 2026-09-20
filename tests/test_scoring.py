"""多维评分与失败模式归因测试。"""

import unittest

from memory_bench.evidence.consistency import CheckResult, Status
from memory_bench.report.scoring import (
    DIMENSIONS,
    FailureMode,
    annotate_checks,
    build_scoring,
    classify_failure,
    compute_overall,
    format_scoring,
    score_dimension,
    score_dimensions,
)


def _check(rule, status, message="", evidence_ids=None):
    return CheckResult(
        rule=rule,
        status=Status(status),
        message=message,
        evidence_ids=evidence_ids or [],
    )


class TestClassifyFailure(unittest.TestCase):
    def test_pass_maps_to_correct(self):
        self.assertEqual(classify_failure(_check("say_do_check", "PASS")), "correct")

    def test_na_maps_to_na(self):
        self.assertEqual(classify_failure(_check("say_do_check", "N/A")), "na")

    def test_say_do_fail_is_omission(self):
        self.assertEqual(classify_failure(_check("say_do_check", "FAIL")), "omission")

    def test_boundary_fail_is_erroneous_reuse(self):
        self.assertEqual(
            classify_failure(_check("boundary_check", "FAIL")), "erroneous_reuse"
        )

    def test_probe_fail_is_confusion(self):
        self.assertEqual(
            classify_failure(_check("probe_expectation_check", "FAIL")), "confusion"
        )

    def test_message_refines_to_erroneous_persistence(self):
        check = _check("say_do_check", "FAIL", "值最终停留在旧值 10.0.0.1")
        self.assertEqual(
            classify_failure(check), FailureMode.ERRONEOUS_PERSISTENCE.value
        )

    def test_message_refines_to_erroneous_reuse(self):
        check = _check("boundary_check", "FAIL", "复用了临时口令，不应继续使用")
        self.assertEqual(classify_failure(check), FailureMode.ERRONEOUS_REUSE.value)

    def test_all_modes_reachable(self):
        modes = {
            classify_failure(_check(r, s))
            for r in (
                "say_do_check",
                "memory_behavior_check",
                "probe_expectation_check",
                "boundary_check",
            )
            for s in ("PASS", "FAIL", "WARN", "N/A")
        }
        self.assertIn(FailureMode.CORRECT.value, modes)
        self.assertIn(FailureMode.OMISSION.value, modes)
        self.assertIn(FailureMode.ERRONEOUS_REUSE.value, modes)
        self.assertIn(FailureMode.NA.value, modes)


class TestScoreDimension(unittest.TestCase):
    def test_all_pass_scores_one(self):
        checks = [_check("say_do_check", "PASS"), _check("memory_behavior_check", "PASS")]
        result = score_dimension("retention", checks)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["status"], "优秀")

    def test_mixed_warn_halves(self):
        checks = [_check("say_do_check", "PASS"), _check("say_do_check", "WARN")]
        result = score_dimension("retention", checks)
        self.assertEqual(result["score"], 0.75)

    def test_all_fail_scores_zero(self):
        checks = [_check("say_do_check", "FAIL"), _check("say_do_check", "FAIL")]
        result = score_dimension("retention", checks)
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "差")

    def test_na_only_is_skipped(self):
        result = score_dimension("retention", [_check("say_do_check", "N/A")])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "N/A")


class TestScoreDimensionsAndOverall(unittest.TestCase):
    def test_dimensions_separate_by_rule(self):
        checks = [
            _check("say_do_check", "PASS"),
            _check("memory_behavior_check", "FAIL"),
            _check("probe_expectation_check", "PASS"),
            _check("boundary_check", "PASS"),
        ]
        dims = score_dimensions(checks, "retention")
        by_name = {d["dimension"]: d for d in dims}
        self.assertEqual(by_name["retention"]["score"], 1.0)
        self.assertEqual(by_name["update"]["score"], 0.0)
        self.assertEqual(by_name["recall"]["score"], 1.0)
        self.assertEqual(dims[0]["dimension"], "retention", "主维度应排第一")

    def test_annotate_checks_attaches_dimension_and_mode(self):
        checks = [_check("boundary_check", "FAIL")]
        annotated = annotate_checks(checks)
        self.assertEqual(annotated[0]["dimension"], "boundary")
        self.assertEqual(annotated[0]["failure_mode"], "erroneous_reuse")

    def test_overall_mixed(self):
        checks = [
            _check("say_do_check", "PASS"),
            _check("memory_behavior_check", "WARN"),
            _check("boundary_check", "FAIL"),
            _check("causality_check", "N/A"),
        ]
        overall = compute_overall(checks)
        self.assertEqual(overall["pass"], 1)
        self.assertEqual(overall["warn"], 1)
        self.assertEqual(overall["fail"], 1)
        self.assertEqual(overall["na"], 1)
        self.assertEqual(overall["score"], (1 + 0.5) / 3)
        modes = {m["mode"] for m in overall["failure_modes"]}
        self.assertEqual(modes, {"correct", "na", "erroneous_reuse"})

    def test_all_na_gives_none_score(self):
        overall = compute_overall([_check("say_do_check", "N/A")])
        self.assertIsNone(overall["score"])
        self.assertEqual(overall["status"], "N/A")

    def test_build_scoring_shape(self):
        checks = [_check("say_do_check", "PASS"), _check("boundary_check", "FAIL")]
        scoring = build_scoring(checks, "boundary")
        self.assertIn("overall", scoring)
        self.assertIn("dimensions", scoring)
        self.assertIn("annotated_checks", scoring)
        self.assertEqual(len(scoring["annotated_checks"]), 2)

    def test_format_scoring_contains_keywords(self):
        checks = [_check("say_do_check", "PASS"), _check("boundary_check", "FAIL")]
        text = format_scoring(build_scoring(checks, "boundary"))
        self.assertIn("综合评分", text)
        self.assertIn("失败模式", text)
        self.assertIn("boundary", text)

    def test_dimension_labels_cover_ten_dimensions(self):
        self.assertGreaterEqual(len(DIMENSIONS), 10)
        self.assertIn("retention", DIMENSIONS)
        self.assertIn("causality", DIMENSIONS)


if __name__ == "__main__":
    unittest.main()