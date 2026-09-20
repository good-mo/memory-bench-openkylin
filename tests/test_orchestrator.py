"""端到端 orchestrator 测试：跑一个最小场景并校验产出。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from agents.dummy_agent import (
    BadDummyAgent,
    ConfuseDummyAgent,
    DummyAgent,
    ForgetDummyAgent,
    IgnoreForgetDummyAgent,
    LeakyDummyAgent,
    OkConfigAmnesiaDummyAgent,
    OkConfigDummyAgent,
    ToolCallDummyAgent,
    ToolNoMemoryAgent,
    ToolTokenReuseAgent,
)
from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.orchestrator import Orchestrator
from memory_bench.scenarios.library import builtin_scenarios


class TestOrchestratorEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_e2e_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.outdir = self.root / "out"
        self.workspace = self.outdir / "workspace"

    def run_scenario(self, scenario_id, agent, seed):
        scenario = builtin_scenarios()[scenario_id]
        strategy = {
            "dummy": DummyAgent(seed=seed),
            "bad": BadDummyAgent(seed=seed),
            "confuse": ConfuseDummyAgent(seed=seed),
            "leaky": LeakyDummyAgent(seed=seed),
            "forget": ForgetDummyAgent(seed=seed),
            "ignoreforget": IgnoreForgetDummyAgent(seed=seed),
            "okconfig": OkConfigDummyAgent(seed=seed),
            "okamnesia": OkConfigAmnesiaDummyAgent(seed=seed),
            "tooldummy": ToolCallDummyAgent(seed=seed),
            "tooltokenreuse": ToolTokenReuseAgent(seed=seed),
            "toolnomemory": ToolNoMemoryAgent(seed=seed),
        }[agent]
        store = EvidenceStore(str(self.outdir / "evidence.ndjson"))
        return Orchestrator(
            scenario=scenario,
            agent=strategy,
            store=store,
            workspace=self.workspace,
            seed=seed,
        ).run()

    def test_dummy_update_all_green(self):
        result = self.run_scenario("demo_update", "dummy", 7)
        self.assertEqual(result.scenario_id, "demo_update")
        self.assertGreater(result.summary["ev_total"], 0)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertIn("PASS", [c.status.value for c in result.checks])

    def test_bad_update_catches_stale_value(self):
        result = self.run_scenario("demo_update", "bad", 7)
        memory_results = [c for c in result.checks if c.rule == "memory_behavior_check"]
        time_results = [c for c in result.checks if c.rule == "time_update_check"]
        self.assertTrue(
            any(c.status.value == "FAIL" for c in memory_results + time_results),
            "坏智能体必须被抓出旧值残留 FAIL",
        )

    def test_artifacts_and_report_files_written(self):
        result = self.run_scenario("demo_retention", "dummy", 42)
        report_data = json.loads(
            (self.outdir / "report.json").read_text(encoding="utf-8")
        )
        self.assertTrue((self.outdir / "report.html").exists())
        self.assertEqual(report_data["meta"]["scenario"], "长期保留偏好（retention）")
        self.assertEqual(
            report_data["evidence_stats"]["total"], result.summary["ev_total"]
        )
        workspace_files = list(self.workspace.iterdir())
        self.assertTrue(
            workspace_files, "dummy 智能体应在工作区产出文件（deploy/manifest）"
        )

    def test_run_result_summary_shape(self):
        result = self.run_scenario("demo_update", "dummy", 1)
        summary = result.summary
        for key in ("ev_total", "ev_by_type", "checks_pass", "checks_fail", "checks_warn"):
            self.assertIn(key, summary)
        self.assertIn("html", result.report_paths)

    # ------------------------------------------------------------ 新维度覆盖

    def test_recall_dummy_passes_twice(self):
        result = self.run_scenario("demo_recall", "dummy", 11)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(
            result.summary["checks_pass"], 2,
            "记忆调用：间隔任务后仍应正确调用记忆",
        )

    def test_near_confuse_caught_by_expected_value(self):
        result = self.run_scenario("demo_near", "confuse", 13)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "probe_expectation_check" for c in fails),
            "相近区分：使用相近干扰值必须被抓出 FAIL",
        )

    def test_near_dummy_uses_prod_value(self):
        result = self.run_scenario("demo_near", "dummy", 13)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(result.summary["checks_pass"], 1)

    def test_boundary_leaky_caught_by_tmp_token(self):
        result = self.run_scenario("demo_boundary", "leaky", 17)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "boundary_check" for c in fails),
            "边界识别：临时口令被复用必须被抓出 FAIL",
        )

    def test_boundary_dummy_no_reuse(self):
        result = self.run_scenario("demo_boundary", "dummy", 17)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(result.summary["checks_pass"], 1)

    def test_reuse_dummy_multiple_tasks_pass(self):
        result = self.run_scenario("demo_reuse", "dummy", 19)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(
            result.summary["checks_pass"], 2,
            "任务复用：多个任务都应正确复用历史信息",
        )

    def test_privacy_dummy_no_secret_reuse(self):
        result = self.run_scenario("demo_privacy_constraint", "dummy", 23)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(result.summary["checks_pass"], 1)

    def test_privacy_leaky_caught_by_payment_ref(self):
        result = self.run_scenario("demo_privacy_constraint", "leaky", 23)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "boundary_check" for c in fails),
            "隐私边界：敏感支付凭据被复用必须被抓出 FAIL",
        )

    # ------------------------------------------------------------ M2 维度覆盖

    def test_forget_dummy_no_token_reuse(self):
        result = self.run_scenario("demo_forget", "forget", 41)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(
            result.summary["checks_pass"], 1,
            "遗忘指令：忘记后不应再复用临时令牌",
        )

    def test_ignoreforget_caught_by_token_reuse(self):
        result = self.run_scenario("demo_forget", "ignoreforget", 41)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "boundary_check" for c in fails),
            "遗忘指令：抗命复用临时令牌必须被抓出 FAIL",
        )

    def test_okconfig_cross_session_passes(self):
        result = self.run_scenario("demo_ok_config", "okconfig", 43)
        self.assertEqual(result.summary["checks_fail"], 0)
        self.assertGreaterEqual(
            result.summary["checks_pass"], 1,
            "openKylin 配置：跨会话应能调用第一天的配置",
        )

    def test_okamnesia_caught_by_lost_config(self):
        result = self.run_scenario("demo_ok_config", "okamnesia", 43)
        fails_or_warn = [c for c in result.checks if c.status.value in ("FAIL", "WARN")]
        self.assertTrue(
            any(c.rule == "cross_session_check" for c in fails_or_warn),
            "openKylin 配置：跨会话丢失配置必须被抓出",
        )

    def test_report_contains_scoring_and_manifest(self):
        result = self.run_scenario("demo_retention", "dummy", 42)
        report_data = json.loads(
            (self.outdir / "report.json").read_text(encoding="utf-8")
        )
        self.assertIn("scoring", report_data)
        self.assertIn("overall", report_data["scoring"])
        self.assertIn("dimensions", report_data["scoring"])
        self.assertTrue((self.outdir / "manifest.json").exists())
        manifest = json.loads(
            (self.outdir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["agent"], "dummy")
        self.assertIn("repro_fingerprint", manifest)

    # ------------------------------------------------------------ M7 工具调用

    def test_tool_dummy_uses_memory_params(self):
        result = self.run_scenario("demo_tool", "tooldummy", 47)
        self.assertEqual(result.summary["checks_fail"], 0)
        tool_checks = [c for c in result.checks if c.rule == "tool_call_check"]
        self.assertTrue(
            any(c.status.value == "PASS" for c in tool_checks),
            "外部工具调用：应从记忆复用 apt_mirror/cache_ttl",
        )

    def test_tool_token_reuse_caught(self):
        result = self.run_scenario("demo_tool", "tooltokenreuse", 47)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "tool_call_check" and "敏感参数" in c.message for c in fails),
            "外部工具调用：一次性认证令牌被复用必须被抓出 FAIL",
        )

    def test_tool_no_memory_caught(self):
        result = self.run_scenario("demo_tool", "toolnomemory", 47)
        fails = [c for c in result.checks if c.status.value == "FAIL"]
        self.assertTrue(
            any(c.rule == "tool_call_check" and "未复用记忆键" in c.message for c in fails),
            "外部工具调用：硬编码默认值、不使用记忆必须被抓出 FAIL",
        )

    def test_tool_events_written_to_evidence(self):
        result = self.run_scenario("demo_tool", "tooldummy", 47)
        lines = (self.outdir / "evidence.ndjson").read_text(encoding="utf-8")
        self.assertIn('"TOOL"', lines)
        self.assertIn("refreshPackageCache", lines)


if __name__ == "__main__":
    unittest.main()