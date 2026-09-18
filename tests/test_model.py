"""证据模型序列化单元测试。"""

import unittest

from memory_bench.evidence.model import (
    EvidenceEvent,
    EvidenceType,
    MemoryOp,
    Source,
)

TS = "2026-09-18T09:00:00.000000+00:00"


class TestEvidenceEventDict(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        ev = EvidenceEvent(
            ev_id="ev-0001",
            type=EvidenceType.MEMORY,
            ts=TS,
            session="s1",
            task="t1",
            source=Source.AGENT,
            content={"op": MemoryOp.WRITE.value, "key": "server_ip", "value": "1.2.3.4"},
        )
        data = ev.to_dict()
        self.assertEqual(data["ev_id"], "ev-0001")
        self.assertEqual(data["type"], "MEMORY")
        self.assertEqual(data["source"], "AGENT")
        self.assertEqual(data["content"]["op"], "WRITE")

        back = EvidenceEvent.from_dict(data)
        self.assertEqual(back, ev)

    def test_metadata_default_empty_dict(self):
        ev = EvidenceEvent(
            ev_id="ev-0002", type=EvidenceType.ACTION, ts=TS, session="s", source=Source.USER
        )
        self.assertEqual(ev.metadata, {})
        self.assertEqual(ev.task, "")


class TestEvidenceEventLines(unittest.TestCase):
    def test_to_line_from_line_roundtrip(self):
        ev = EvidenceEvent(
            ev_id="ev-0003",
            type=EvidenceType.ARTIFACT,
            ts=TS,
            session="s",
            task="probe",
            source=Source.AGENT,
            content={"path": "deploy.txt", "content": "ip=1.2.3.4"},
            metadata={"missing_memory": False},
        )
        line = ev.to_line()
        self.assertNotIn("\n", line)
        back = EvidenceEvent.from_line(line)
        self.assertEqual(back, ev)
        self.assertEqual(back.content["path"], "deploy.txt")

    def test_line_with_unicode(self):
        ev = EvidenceEvent(
            ev_id="ev-0004",
            type=EvidenceType.DIALOGUE,
            ts=TS,
            session="s",
            task="t",
            source=Source.AGENT,
            content={"text": "好的，已记住 server_ip=192.168.1.100"},
        )
        self.assertIn("已记住", ev.to_line())


class TestEvidenceEventValidation(unittest.TestCase):
    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceEvent.from_dict(
                {"ev_id": "ev-0005", "type": "NOPE", "ts": TS, "source": "HARNESS"}
            )

    def test_missing_ev_id_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceEvent.from_dict({"type": "DIALOGUE", "ts": TS, "source": "USER"})

    def test_invalid_ts_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceEvent(
                ev_id="ev-0006",
                type=EvidenceType.CHECKPOINT,
                ts="not-a-time",
                session="s",
                source=Source.HARNESS,
            )

    def test_invalid_source_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceEvent.from_dict(
                {"ev_id": "ev-0007", "type": "MEMORY", "ts": TS, "source": "WHO"}
            )


if __name__ == "__main__":
    unittest.main()