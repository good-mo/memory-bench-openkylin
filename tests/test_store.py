"""EvidenceStore 追加/查询单元测试。"""

import os
import tempfile
import unittest

from memory_bench.evidence.model import (
    EvidenceEvent,
    EvidenceType,
    MemoryOp,
    Source,
)
from memory_bench.evidence.store import EvidenceStore

TS1 = "2026-09-18T09:00:00.000000+00:00"
TS2 = "2026-09-18T09:00:01.000000+00:00"


class TestEvidenceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_store_")
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "evidence.ndjson")
        self.store = EvidenceStore(self.path)

    def test_append_assigns_incremental_ids(self):
        e1 = self.store.append(
            {"type": "CHECKPOINT", "ts": TS1, "session": "s", "source": "HARNESS",
             "content": {"phase": "start"}}
        )
        e2 = self.store.append(
            EvidenceEvent(ev_id="", type=EvidenceType.MEMORY, ts=TS2, session="s",
                          source=Source.AGENT,
                          content={"op": MemoryOp.WRITE.value, "key": "k", "value": "v"})
        )
        self.assertEqual(e1.ev_id, "ev-0001")
        self.assertEqual(e2.ev_id, "ev-0002")
        self.assertEqual(self.store.count(), 2)

    def test_cross_run_id_continues(self):
        self.store.append(
            {"type": "CHECKPOINT", "ts": TS1, "session": "s", "source": "HARNESS",
             "content": {"phase": "start"}}
        )
        reopened = EvidenceStore(self.path)
        e = reopened.append(
            {"type": "CHECKPOINT", "ts": TS2, "session": "s", "source": "HARNESS",
             "content": {"phase": "end"}}
        )
        self.assertEqual(e.ev_id, "ev-0002")

    def test_query_filters(self):
        base = self.store.path
        _ = base
        for idx, (etype, src, task) in enumerate(
            [("ACTION", "AGENT", "probe"), ("MEMORY", "AGENT", "inject"),
             ("DIALOGUE", "USER", "inject")]
        ):
            self.store.append(
                {"ev_id": "", "type": etype,
                 "ts": TS1 if idx == 0 else TS2,
                 "session": "s", "task": task, "source": src,
                 "content": {"k": idx}}
            )

        actions = self.store.query(type="ACTION")
        self.assertEqual(len(actions), 1)

        by_session = self.store.query(session="s")
        self.assertEqual(len(by_session), 3)

        inject_tasks = self.store.query(task="inject")
        self.assertEqual(len(inject_tasks), 2)

        by_source = self.store.query(source="USER")
        self.assertEqual(len(by_source), 1)

        ordered = self.store.query()
        ts_vals = [ev.ts for ev in ordered]
        self.assertEqual(ts_vals, sorted(ts_vals))

    def test_by_type_and_count(self):
        self.store.append(
            {"type": "MEMORY", "ts": TS1, "session": "s", "source": "AGENT",
             "content": {"op": "READ", "key": "a", "value": "1"}}
        )
        self.store.append(
            {"type": "MEMORY", "ts": TS2, "session": "s", "source": "AGENT",
             "content": {"op": "READ", "key": "b", "value": "2"}}
        )
        self.assertEqual(len(self.store.by_type(EvidenceType.MEMORY)), 2)
        self.assertEqual(len(self.store.by_type("MEMORY")), 2)
        self.assertEqual(self.store.count(), 2)

    def test_export_ndjson(self):
        self.store.append(
            {"type": "CHECKPOINT", "ts": TS1, "session": "s", "source": "HARNESS",
             "content": {"phase": "start"}}
        )
        dest = os.path.join(self.tmp.name, "copy.ndjson")
        self.store.export_ndjson(dest)
        with open(dest, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)

    def test_db_created_automatically(self):
        self.store.append(
            {"type": "CHECKPOINT", "ts": TS1, "session": "s", "source": "HARNESS",
             "content": {"phase": "start"}}
        )
        self.assertTrue(os.path.exists(self.store.db_path))

    def test_start_end_ts_filter(self):
        self.store.append(
            {"type": "MEMORY", "ts": TS1, "session": "s", "source": "AGENT",
             "content": {"op": "WRITE", "key": "a", "value": "1"}}
        )
        self.store.append(
            {"type": "MEMORY", "ts": TS2, "session": "s", "source": "AGENT",
             "content": {"op": "WRITE", "key": "b", "value": "2"}}
        )
        res = self.store.query(start_ts=TS2)
        self.assertEqual(len(res), 1)
        res = self.store.query(start_ts=TS1, end_ts=TS1)
        self.assertEqual(len(res), 1)


if __name__ == "__main__":
    unittest.main()