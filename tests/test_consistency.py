"""三条跨证据一致性规则单元测试。"""

import os
import tempfile
import unittest

from memory_bench.evidence.consistency import (
    memory_behavior_check,
    say_do_check,
    time_update_check,
)
from memory_bench.evidence.model import EvidenceType, Source
from memory_bench.evidence.store import EvidenceStore

TS1 = "2026-09-18T09:00:00.000000+00:00"
TS2 = "2026-09-18T09:00:01.000000+00:00"
TS3 = "2026-09-18T09:00:02.000000+00:00"


class StoreTestCase(unittest.TestCase):
    """基类：每个用例独立临时证据存储。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_chk_")
        self.addCleanup(self.tmp.cleanup)
        self.store = EvidenceStore(os.path.join(self.tmp.name, "evidence.ndjson"))

    def append(self, etype, ts, source, content, task="t"):
        self.store.append(
            {
                "type": etype,
                "ts": ts,
                "session": "s",
                "task": task,
                "source": source,
                "content": content,
            }
        )


class TestSayDoCheck(StoreTestCase):
    def test_pass_when_action_evidence_exists(self):
        self.append("DIALOGUE", TS1, "AGENT", {"text": "已创建 /tmp/backup.tar，任务完成"})
        self.append("ARTIFACT", TS2, "AGENT", {"path": "/tmp/backup.tar", "content": "hello"})
        results = say_do_check(self.store)
        self.assertTrue(results)
        self.assertEqual(results[0].status.value, "PASS")

    def test_fail_when_no_action_evidence(self):
        self.append("DIALOGUE", TS1, "AGENT", {"text": "已删除 /tmp/ghost.txt 了"})
        results = say_do_check(self.store)
        self.assertTrue(results)
        self.assertEqual(results[0].status.value, "FAIL")

    def test_na_when_no_claim(self):
        self.append("DIALOGUE", TS1, "AGENT", {"text": "好的，我先看一眼当前情况"})
        results = say_do_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "N/A")


class TestMemoryBehaviorCheck(StoreTestCase):
    def seed_chain(self, action_cmd=None):
        self.append(
            "MEMORY", TS1, Source.AGENT.value,
            {"op": "WRITE", "key": "server_ip", "value": "192.168.1.100"}, task="inject",
        )
        self.append(
            "MEMORY", TS2, Source.AGENT.value,
            {"op": "UPDATE", "key": "server_ip", "value": "192.168.2.50"}, task="update",
        )
        if action_cmd is not None:
            self.append(
                "ACTION", TS3, Source.AGENT.value,
                {"command": action_cmd, "path": None, "detail": "连接"}, task="probe",
            )

    def test_fail_when_old_value_used(self):
        self.seed_chain(action_cmd="ssh kylin@192.168.1.100 -p 22")
        results = memory_behavior_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "FAIL")
        self.assertIn("192.168.1.100", results[0].message)

    def test_pass_when_new_value_used(self):
        self.seed_chain(action_cmd="ssh kylin@192.168.2.50 -p 2222")
        results = memory_behavior_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "PASS")

    def test_na_when_no_action_reference(self):
        self.seed_chain(action_cmd=None)
        results = memory_behavior_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "N/A")
        self.assertIn("N/A", results[0].message)


class TestTimeUpdateCheck(StoreTestCase):
    def seed(self, action_cmd):
        self.append(
            "MEMORY", TS1, "AGENT",
            {"op": "WRITE", "key": "port", "value": "22"}, task="inject",
        )
        self.append(
            "MEMORY", TS2, "AGENT",
            {"op": "UPDATE", "key": "port", "value": "2222"}, task="update",
        )
        if action_cmd:
            self.append(
                "ACTION", TS3, "AGENT",
                {"command": action_cmd, "detail": "连接"}, task="probe",
            )

    def test_fail_when_stale_used(self):
        self.seed("ssh -p 22")
        results = time_update_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "FAIL")
        self.assertIn("stale", results[0].message)

    def test_pass_when_fresh_used(self):
        self.seed("ssh -p 2222")
        results = time_update_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "PASS")

    def test_na_when_nothing_referenced(self):
        self.seed(None)
        results = time_update_check(self.store)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status.value, "N/A")


if __name__ == "__main__":
    unittest.main()