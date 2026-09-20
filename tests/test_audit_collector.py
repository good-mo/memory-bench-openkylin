"""OS 侧审计证据采集层单元测试。"""

import json
import tempfile
import unittest
from pathlib import Path

import memory_bench.audit.sources as sources_mod
from memory_bench.audit.collector import AuditCollector
from memory_bench.audit.records import AuditRecord
from memory_bench.audit.sources import (
    AuditdSource,
    FileSystemDiffSource,
    JournalSource,
    NdjsonReplaySource,
    ProcessSource,
)
from memory_bench.evidence.model import EvidenceType, Source
from memory_bench.evidence.store import EvidenceStore


class AuditCollectorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_audit_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = EvidenceStore(str(self.root / "evidence.ndjson"))


class TestAuditRecord(AuditCollectorTestBase):
    def test_to_event_action(self):
        rec = AuditRecord(
            kind="action",
            command="ssh kylin@10.0.0.1 -p 22",
            detail="ssh connect",
            backend="replay",
            metadata={"pid": "1234"},
        )
        ev = rec.to_event(session="s1", task="t1")
        self.assertEqual(ev.type, EvidenceType.ACTION)
        self.assertEqual(ev.source, Source.HARNESS)
        self.assertEqual(ev.session, "s1")
        self.assertEqual(ev.task, "t1")
        self.assertEqual(ev.content["command"], "ssh kylin@10.0.0.1 -p 22")
        self.assertEqual(ev.metadata["audit_backend"], "replay")
        self.assertEqual(ev.metadata["pid"], "1234")

    def test_to_event_artifact(self):
        rec = AuditRecord(
            kind="artifact",
            path="deploy.txt",
            content="ip=10.0.0.1",
            backend="filesystem",
        )
        ev = rec.to_event(session="s")
        self.assertEqual(ev.type, EvidenceType.ARTIFACT)
        self.assertEqual(ev.content["path"], "deploy.txt")
        self.assertEqual(ev.content["content"], "ip=10.0.0.1")

    def test_to_event_dialogue(self):
        rec = AuditRecord(kind="dialogue", content="some log", backend="journal")
        ev = rec.to_event(session="s")
        self.assertEqual(ev.type, EvidenceType.DIALOGUE)

    def test_roundtrip_dict(self):
        rec = AuditRecord(
            kind="action", command="ls -la", backend="replay",
            metadata={"note": "x"},
        )
        restored = AuditRecord.from_dict(rec.to_dict())
        self.assertEqual(restored.kind, "action")
        self.assertEqual(restored.command, "ls -la")
        self.assertEqual(restored.backend, "replay")
        self.assertEqual(restored.metadata["note"], "x")


class TestJournalSource(AuditCollectorTestBase):
    def test_collect_translates_journal_lines(self):
        src = JournalSource(lines=10)
        sources_mod._run = lambda *a, **kw: json.dumps(
            {"MESSAGE": "hello", "_COMM": "bash"}, ensure_ascii=False
        )
        records = src.collect()
        self.assertTrue(records)
        self.assertEqual(records[0].kind, "action")
        self.assertEqual(records[0].command, "bash")


class TestAuditdSource(unittest.TestCase):
    def test_extract_command_from_execve_line(self):
        line = (
            'type=EXECVE msg=audit(1): argc=2 a0="ssh" a1="kylin@10.0.0.1" '
        )
        cmd = AuditdSource._extract_command(line)
        self.assertEqual(cmd, 'ssh kylin@10.0.0.1')

    def test_extract_single_line(self):
        cmd = AuditdSource._extract_command('a0="ls" a1="-la"')
        self.assertEqual(cmd, "ls -la")


class TestProcessSource(AuditCollectorTestBase):
    def test_filters_unrelated_processes(self):
        src = ProcessSource(keywords=["ssh"], max_records=10)
        sources_mod._run = lambda *a, **kw: (
            "1122 python3 /usr/bin/serve.py\n"
            "2233 ssh kylin@10.0.0.1 -p 22\n"
        )
        records = src.collect()
        self.assertEqual(len(records), 1)
        self.assertIn("ssh", records[0].command)


class TestFileSystemDiffSource(AuditCollectorTestBase):
    def test_detects_added_files(self):
        src = FileSystemDiffSource(str(self.root / "ws" / "a"))
        src.begin()
        (self.root / "ws" / "a").mkdir(parents=True, exist_ok=True)
        (self.root / "ws" / "a" / "deploy.txt").write_text("ip=1.2.3.4\n")
        records = src.collect()
        added = [r for r in records if r.kind == "artifact"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].path, "deploy.txt")
        self.assertIn("ip=1.2.3.4", added[0].content)


class TestNdjsonReplaySource(AuditCollectorTestBase):
    def test_loads_replay_file(self):
        path = str(self.root / "audit.ndjson")
        records_in = [
            AuditRecord(kind="action", command="ssh a b", backend="replay"),
            AuditRecord(kind="artifact", path="x.txt", content="x=1", backend="replay"),
        ]
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records_in:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        src = NdjsonReplaySource(path)
        records = src.collect()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].command, "ssh a b")

    def test_missing_file_reports_dialogue(self):
        src = NdjsonReplaySource(str(self.root / "missing.ndjson"))
        records = src.collect()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "dialogue")
        self.assertIn("不存在", records[0].content)


class TestAuditCollector(AuditCollectorTestBase):
    def test_collect_writes_evidence(self):
        collector = AuditCollector(self.store)
        file_path = str(self.root / "audit.ndjson")
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    AuditRecord(
                        kind="action", command="ssh kylin@1.2.3.4", backend="replay"
                    ).to_dict(),
                    ensure_ascii=False,
                )
                + "\n"
            )
        collector.add_source(NdjsonReplaySource(file_path))
        written = collector.collect(session="s9", task="t9")
        self.assertEqual(written, 1)
        events = self.store.query(session="s9")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EvidenceType.ACTION)
        self.assertEqual(events[0].source, Source.HARNESS)
        self.assertEqual(events[0].metadata["audit_backend"], "replay")


if __name__ == "__main__":
    unittest.main()