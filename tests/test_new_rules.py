"""M3–M5 新功能单元测试：新规则 + 跨会话编排 + 遗忘曲线 + 鲁棒性聚合。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from memory_bench.evidence.consistency import (
    causality_check,
    conflict_rollback_check,
    cross_file_consistency_check,
    cross_session_check,
    run_all,
)
from memory_bench.evidence.model import EvidenceType, Source
from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.orchestrator import Orchestrator
from memory_bench.report.forgetting import compute_forgetting_curve
from memory_bench.scenarios.library import builtin_scenarios

TS1 = "2026-09-18T09:00:00.000000+00:00"
TS2 = "2026-09-18T09:00:01.000000+00:00"
TS3 = "2026-09-18T09:00:02.000000+00:00"


class BaseStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_new_")
        self.addCleanup(self.tmp.cleanup)
        self.store = EvidenceStore(os.path.join(self.tmp.name, "evidence.ndjson"))

    def append(self, etype, ts, source, content, task="t", session="s"):
        self.store.append(
            {
                "type": etype,
                "ts": ts,
                "session": session,
                "task": task,
                "source": source,
                "content": content,
            }
        )


# ---------------------------------------------------------------------------
# 跨会话持久化规则
# ---------------------------------------------------------------------------


class TestCrossSessionCheck(BaseStore):
    def test_multi_session_pass_when_recalled(self):
        scenario = builtin_scenarios()["demo_persist"]
        # 会话切换 checkpoint + day2 probe 引用期望值
        self.append(
            "CHECKPOINT", TS1, "HARNESS",
            {"phase": "step", "step": "probe-day2-connect", "type": "PROBE"},
            session="day2",
        )
        self.append(
            "DIALOGUE", TS2, "AGENT",
            {"text": "已连接 10.10.1.50，端口 2222"}, session="day2",
            task="probe-day2-connect",
        )
        results = cross_session_check(self.store, scenario)
        self.assertTrue(any(r.status.value == "PASS" for r in results))

    def test_multi_session_warn_when_forgotten(self):
        scenario = builtin_scenarios()["demo_persist"]
        self.append(
            "CHECKPOINT", TS1, "HARNESS",
            {"phase": "step", "step": "probe-day2-connect", "type": "PROBE"},
            session="day2",
        )
        self.append(
            "DIALOGUE", TS2, "AGENT",
            {"text": "我找不到服务器信息"}, session="day2",
            task="probe-day2-connect",
        )
        results = cross_session_check(self.store, scenario)
        self.assertTrue(any(r.status.value == "WARN" for r in results))

    def test_single_session_no_result(self):
        scenario = builtin_scenarios()["demo_retention"]
        results = cross_session_check(self.store, scenario)
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# 冲突回滚规则
# ---------------------------------------------------------------------------


class TestConflictRollbackCheck(BaseStore):
    def test_pass_when_rolled_back(self):
        scenario = builtin_scenarios()["demo_rollback"]
        for ts, val in ((TS1, "192.168.5.10"), (TS2, "192.168.5.99"), (TS3, "192.168.5.10")):
            self.append(
                "MEMORY", ts, "AGENT",
                {"op": "UPDATE", "key": "server_ip", "value": val},
            )
        results = conflict_rollback_check(self.store, scenario)
        self.assertTrue(any(r.status.value == "PASS" for r in results))

    def test_fail_when_not_rolled_back(self):
        scenario = builtin_scenarios()["demo_rollback"]
        for ts, val in ((TS1, "192.168.5.10"), (TS2, "192.168.5.99")):
            self.append(
                "MEMORY", ts, "AGENT",
                {"op": "UPDATE", "key": "server_ip", "value": val},
            )
        results = conflict_rollback_check(self.store, scenario)
        self.assertTrue(any(r.status.value == "FAIL" for r in results))

    def test_no_rollback_semantics_returns_empty(self):
        scenario = builtin_scenarios()["demo_update"]
        self.append(
            "MEMORY", TS1, "AGENT",
            {"op": "UPDATE", "key": "server_ip", "value": "192.168.2.50"},
        )
        results = conflict_rollback_check(self.store, scenario)
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# 交叉文件一致性规则
# ---------------------------------------------------------------------------


class TestCrossFileConsistencyCheck(BaseStore):
    def test_pass_when_files_agree(self):
        self.append(
            "ARTIFACT", TS1, "AGENT",
            {"path": "deploy.txt", "content": "ip=172.16.8.20 port=8822"},
        )
        self.append(
            "ARTIFACT", TS2, "AGENT",
            {"path": "backup.txt", "content": "ip=172.16.8.20 port=8822"},
        )
        results = cross_file_consistency_check(self.store, builtin_scenarios()["demo_crossfile"])
        self.assertTrue(any(r.status.value == "PASS" for r in results))

    def test_fail_when_files_disagree(self):
        self.append(
            "ARTIFACT", TS1, "AGENT",
            {"path": "deploy.txt", "content": "ip=172.16.8.20"},
        )
        self.append(
            "ARTIFACT", TS2, "AGENT",
            {"path": "backup.txt", "content": "ip=172.16.8.20.0"},
        )
        results = cross_file_consistency_check(self.store, builtin_scenarios()["demo_crossfile"])
        self.assertTrue(any(r.status.value == "FAIL" for r in results))


# ---------------------------------------------------------------------------
# 时序因果性规则
# ---------------------------------------------------------------------------


class TestCausalityCheck(BaseStore):
    def test_pass_when_ref_after_write(self):
        self.append(
            "MEMORY", TS1, "AGENT",
            {"op": "WRITE", "key": "server_ip", "value": "192.168.1.100"},
        )
        self.append(
            "ACTION", TS2, "AGENT",
            {"command": "ssh 192.168.1.100", "detail": "连接"},
        )
        results = causality_check(self.store, builtin_scenarios()["demo_recall"])
        self.assertTrue(any(r.status.value == "PASS" for r in results))


# ---------------------------------------------------------------------------
# 遗忘曲线统计
# ---------------------------------------------------------------------------


class TestForgettingCurve(BaseStore):
    def test_curve_computed(self):
        self.append(
            "MEMORY", TS1, "AGENT",
            {"op": "WRITE", "key": "server_ip", "value": "192.168.1.100"},
        )
        self.append("DIALOGUE", TS2, "AGENT", {"text": "ip=192.168.1.100"}),
        scenario = builtin_scenarios()["demo_recall"]
        curves = compute_forgetting_curve(self.store, scenario)
        self.assertTrue(len(curves) >= 1)
        curve = curves[0]
        self.assertIn("key", curve)
        self.assertIn("buckets", curve)
        self.assertIn("trend", curve)
        self.assertGreaterEqual(curve["total_references"], 1)

    def test_no_keys_returns_empty(self):
        self.append("DIALOGUE", TS1, "AGENT", {"text": "hi"})
        curves = compute_forgetting_curve(self.store, None)
        self.assertEqual(curves, [])


# ---------------------------------------------------------------------------
# 跨会话编排端到端
# ---------------------------------------------------------------------------


class TestCrossSessionOrchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_cross_")
        self.addCleanup(self.tmp.cleanup)

    def run_scenario(self, agent_name, expect_session_check):
        from agents.dummy_agent import AmnesiaDummyAgent, SessionDummyAgent

        scenario = builtin_scenarios()["demo_persist"]
        outdir = os.path.join(self.tmp.name, agent_name)
        os.makedirs(outdir, exist_ok=True)
        store = EvidenceStore(os.path.join(outdir, "evidence.ndjson"))
        agent = SessionDummyAgent(seed=1) if agent_name == "sessiondummy" else AmnesiaDummyAgent(seed=1)
        result = Orchestrator(
            scenario=scenario,
            agent=agent,
            store=store,
            workspace=Path(outdir) / "ws",
            seed=1,
        ).run()
        checks = {c.rule: c.status.value for c in result.checks}
        if expect_session_check == "pass":
            self.assertEqual(checks["cross_session_check"], "PASS")
        else:
            self.assertEqual(checks["cross_session_check"], "WARN")

    def test_sessiondummy_pass(self):
        self.run_scenario("sessiondummy", "pass")

    def test_amnesia_warn(self):
        self.run_scenario("amnesia", "warn")


# ---------------------------------------------------------------------------
# 冲突回滚智能体端到端
# ---------------------------------------------------------------------------


class TestRollbackAgents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_roll_")
        self.addCleanup(self.tmp.cleanup)

    def run_agent(self, name):
        from agents.dummy_agent import NoRollbackDummyAgent, RollbackDummyAgent

        scenario = builtin_scenarios()["demo_rollback"]
        outdir = os.path.join(self.tmp.name, name)
        store = EvidenceStore(os.path.join(outdir, "evidence.ndjson"))
        agent = RollbackDummyAgent(seed=1) if name == "rollback" else NoRollbackDummyAgent(seed=1)
        result = Orchestrator(
            scenario=scenario,
            agent=agent,
            store=store,
            workspace=Path(outdir) / "ws",
            seed=1,
        ).run()
        checks = {c.rule: c.status.value for c in result.checks}
        return checks

    def test_rollback_agent_pass(self):
        checks = self.run_agent("rollback")
        self.assertEqual(checks["conflict_rollback_check"], "PASS")

    def test_norollback_agent_fail(self):
        checks = self.run_agent("norollback")
        self.assertEqual(checks["conflict_rollback_check"], "FAIL")


# ---------------------------------------------------------------------------
# seed 扰动鲁棒性
# ---------------------------------------------------------------------------


class TestRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_robust_")
        self.addCleanup(self.tmp.cleanup)

    def test_aggregate_matrix_stability(self):
        from memory_bench.robustness import aggregate_matrix

        results = [
            {
                "seed": s,
                "checks": [
                    {"rule": "r1", "status": "PASS"},
                    {"rule": "r2", "status": "PASS"},
                ],
                "summary": {"checks_pass": 2, "checks_fail": 0},
            }
            for s in (1, 2, 3)
        ]
        agg = aggregate_matrix(results)
        self.assertEqual(agg["summary"]["seed_count"], 3)
        self.assertEqual(agg["summary"]["verdict"], "robust")
        self.assertAlmostEqual(agg["summary"]["mean_pass_ratio"], 1.0)

    def test_run_seed_matrix_creates_dir(self):
        from agents.dummy_agent import DummyAgent

        from memory_bench.robustness import run_seed_matrix

        scenario = builtin_scenarios()["demo_recall"]
        outdir = os.path.join(self.tmp.name, "bench")
        results = run_seed_matrix(
            scenario=scenario,
            agent_factory=lambda seed: DummyAgent(seed=seed),
            seeds=[1, 2],
            outdir=outdir,
            agent_name="dummy",
        )
        self.assertEqual(len(results), 2)
        for run in results:
            self.assertIn(run["seed"], (1, 2))
            self.assertTrue(os.path.isdir(run["outdir"]))


if __name__ == "__main__":
    unittest.main()