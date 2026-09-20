"""OAS/OpenAPI 文档解析器与工具调用长短期记忆规则测试。"""

import json
import os
import tempfile
import unittest

from memory_bench.evidence.store import EvidenceStore
from memory_bench.evidence.consistency import tool_call_check
from memory_bench.scenarios.library import builtin_scenarios
from memory_bench.tools.oas import load_oas, parse_oas

OAS_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "测试服务", "version": "1.0.0"},
    "paths": {
        "/caches/refresh": {
            "post": {
                "operationId": "refreshPackageCache",
                "summary": "刷新缓存",
                "parameters": [
                    {
                        "name": "mirror",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "x-memory": {"remember": "apt_mirror"},
                    },
                    {
                        "name": "ttl",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "x-memory": {"remember": "cache_ttl"},
                    },
                    {
                        "name": "auth_token",
                        "in": "header",
                        "schema": {"type": "string"},
                        "x-memory": {"sensitive": True},
                    },
                ],
            }
        }
    },
}


class TestOasParser(unittest.TestCase):
    def test_parse_oas_extracts_tool(self):
        spec = parse_oas(OAS_DOC)
        self.assertEqual(spec.title, "测试服务")
        tool = spec.tool("refreshPackageCache")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.method, "post")
        self.assertEqual(tool.path, "/caches/refresh")

    def test_parse_oas_params_memory_marks(self):
        spec = parse_oas(OAS_DOC)
        tool = spec.tool("refreshPackageCache")
        mirror = tool.param("mirror")
        self.assertEqual(mirror.memory_key, "apt_mirror")
        self.assertFalse(mirror.sensitive)
        token = tool.param("auth_token")
        self.assertTrue(token.sensitive)
        self.assertIsNone(token.memory_key)

    def test_parse_oas_rejects_non_3_x(self):
        doc = dict(OAS_DOC)
        doc["openapi"] = "2.0"
        with self.assertRaises(ValueError):
            parse_oas(doc)

    def test_load_oas_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "doc.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(OAS_DOC, fh)
            spec = load_oas(path)
            self.assertIsNotNone(spec.tool("refreshPackageCache"))

    def test_load_oas_missing_version(self):
        with self.assertRaises(ValueError):
            parse_oas({})
        with self.assertRaises(ValueError):
            parse_oas({"openapi": "3.0.3", "info": {}, "paths": []})


class TestToolCallCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_tool_")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _store(self):
        return EvidenceStore(os.path.join(self.root, "evidence.ndjson"))

    def _emit_memory(self, store, key, value):
        from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc

        store.append(
            EvidenceEvent(
                ev_id="",
                type=EvidenceType.MEMORY,
                ts=now_utc(),
                session="s",
                task="inject",
                source=Source.AGENT,
                content={"op": "WRITE", "key": key, "value": value},
            )
        )

    def _emit_tool(self, store, args):
        from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc

        store.append(
            EvidenceEvent(
                ev_id="",
                type=EvidenceType.TOOL,
                ts=now_utc(),
                session="s",
                task="probe",
                source=Source.AGENT,
                content={"tool": "refreshPackageCache", "args": args},
            )
        )

    def test_no_oas_returns_empty(self):
        scenario = builtin_scenarios()["demo_update"]
        self.assertEqual(tool_call_check(self._store(), scenario), [])

    def test_no_tool_event_is_na(self):
        scenario = builtin_scenarios()["demo_tool"]
        checks = tool_call_check(self._store(), scenario)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status.value, "N/A")
        self.assertEqual(checks[0].rule, "tool_call_check")

    def test_unknown_operation_fails(self):
        scenario = builtin_scenarios()["demo_tool"]
        store = self._store()
        from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc

        store.append(
            EvidenceEvent(
                ev_id="",
                type=EvidenceType.TOOL,
                ts=now_utc(),
                session="s",
                task="probe",
                source=Source.AGENT,
                content={"tool": "noSuchOperation", "args": {"x": "1"}},
            )
        )
        checks = tool_call_check(store, scenario)
        fails = [c for c in checks if c.status.value == "FAIL"]
        self.assertTrue(any("不存在的 operationId" in c.message for c in fails))

    def test_remember_params_match_memory_pass(self):
        scenario = builtin_scenarios()["demo_tool"]
        store = self._store()
        self._emit_memory(store, "apt_mirror", "https://mirrors.openkylin.top")
        self._emit_memory(store, "cache_ttl", "3600")
        self._emit_tool(
            store,
            {"mirror": "https://mirrors.openkylin.top", "ttl": "3600"},
        )
        checks = tool_call_check(store, scenario)
        pass_rows = [c for c in checks if c.status.value == "PASS"]
        fail_rows = [c for c in checks if c.status.value == "FAIL"]
        self.assertGreaterEqual(len(pass_rows), 2)
        self.assertEqual(fail_rows, [])

    def test_remember_params_ignore_memory_fail(self):
        scenario = builtin_scenarios()["demo_tool"]
        store = self._store()
        self._emit_memory(store, "apt_mirror", "https://mirrors.openkylin.top")
        self._emit_memory(store, "cache_ttl", "3600")
        self._emit_tool(
            store,
            {"mirror": "http://hardcoded.invalid", "ttl": "60"},
        )
        checks = tool_call_check(store, scenario)
        fails = [c for c in checks if c.status.value == "FAIL"]
        self.assertGreaterEqual(len(fails), 2)
        self.assertTrue(
            any("未复用记忆键" in c.message for c in fails)
        )

    def test_sensitive_param_leak_fail(self):
        scenario = builtin_scenarios()["demo_tool"]
        store = self._store()
        self._emit_memory(store, "apt_mirror", "https://mirrors.openkylin.top")
        self._emit_memory(store, "cache_ttl", "3600")
        self._emit_tool(
            store,
            {"mirror": "https://mirrors.openkylin.top",
             "ttl": "3600",
             "auth_token": "tkCache1"},
        )
        checks = tool_call_check(store, scenario)
        fails = [c for c in checks if c.status.value == "FAIL"]
        self.assertTrue(
            any("敏感参数" in c.message and "不应" in c.message for c in fails)
        )

    def test_sensitive_param_absent_pass(self):
        scenario = builtin_scenarios()["demo_tool"]
        store = self._store()
        self._emit_memory(store, "apt_mirror", "https://mirrors.openkylin.top")
        self._emit_memory(store, "cache_ttl", "3600")
        self._emit_tool(
            store,
            {"mirror": "https://mirrors.openkylin.top", "ttl": "3600"},
        )
        checks = tool_call_check(store, scenario)
        pass_rows = [c for c in checks if c.status.value == "PASS"]
        self.assertTrue(
            any("敏感参数" in c.message and "未被复用" in c.message for c in pass_rows)
        )


if __name__ == "__main__":
    unittest.main()