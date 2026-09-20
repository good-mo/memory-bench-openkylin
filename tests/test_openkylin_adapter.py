"""openKylin 智能体框架适配器单元测试。"""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agents.openkylin_adapter import OpenKylinAgent
from memory_bench.evidence.model import EvidenceType, Source
from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.agent import AgentContext


class OpenKylinAdapterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_openkylin_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = EvidenceStore(str(self.root / "evidence.ndjson"))

    def run_once(self, agent, user_message="你好", step_name="step1"):
        ctx = AgentContext(
            store=self.store, workspace=self.root / "ws", session="sess", seed=1
        )
        agent.act(ctx, user_message, step_name)
        return self.store.query(session="sess")

    def _python_script(self, body):
        path = self.root / "agent_emit.py"
        path.write_text(body, encoding="utf-8")
        return "python3 {}".format(path)


class TestOpenKylinAgentProcessBackend(OpenKylinAdapterTestBase):
    def test_process_backend_json_memory(self):
        script = self._python_script(
            "import json, sys\n"
            "msg = sys.stdin.read().strip()\n"
            "print(json.dumps({'memory_update': [{'op': 'WRITE', "
            "'key': 'server_ip', 'value': '10.9.9.9'}], "
            "'reply': '\u5df2\u8bb0\u4f4f ' + msg}))\n"
        )
        agent = OpenKylinAgent(seed=1, backend="process", command=script, mode="json")
        events = self.run_once(agent, user_message="记住 server_ip=10.9.9.9")
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["op"], "WRITE")
        self.assertEqual(memories[0].content["key"], "server_ip")
        self.assertEqual(memories[0].content["value"], "10.9.9.9")

    def test_process_backend_mb_lines(self):
        script = self._python_script(
            "import json\n"
            "lines = [\n"
            "  'MB|MEMORY|' + json.dumps({'op': 'WRITE', 'key': 'port', 'value': '3333'}),\n"
            "  'MB|DIALOGUE|' + json.dumps({'text': 'openkylin ok'})\n"
            "]\n"
            "for l in lines: print(l)\n"
        )
        agent = OpenKylinAgent(seed=1, backend="process", command=script, mode="mb")
        events = self.run_once(agent, user_message="记住 port=3333")
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        dialogues = [e for e in events if e.type == EvidenceType.DIALOGUE]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["value"], "3333")
        self.assertTrue(
            any("openkylin ok" in d.content.get("text", "") for d in dialogues)
        )

    def test_text_heuristic_memory(self):
        script = self._python_script("print('\u597d\uff0c\u8bb0\u4f4f server_ip=1.2.3.4')\n")
        agent = OpenKylinAgent(seed=1, backend="process", command=script, mode="text")
        events = self.run_once(agent, user_message="记住 server_ip=1.2.3.4")
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["value"], "1.2.3.4")

    def test_openkylin_registered_in_make_agent(self):
        from memory_bench.runner import make_agent

        agent = make_agent("openkylin", 7)
        self.assertIsInstance(agent, OpenKylinAgent)
        self.assertEqual(agent.name, "openkylin")


class TestOpenKylinAgentHttpBackend(OpenKylinAdapterTestBase):
    def test_http_backend_template_substitution(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received["body"] = self.rfile.read(length).decode("utf-8")
                payload = json.dumps(
                    {
                        "memory_update": [
                            {"op": "WRITE", "key": "server_ip", "value": "5.5.5.5"}
                        ],
                        "reply": "ok",
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        try:
            url = "http://127.0.0.1:{}/chat".format(server.server_address[1])
            agent = OpenKylinAgent(seed=1, backend="http", url=url, mode="json")
            events = self.run_once(agent, user_message="记住 server_ip=5.5.5.5")
            body = json.loads(received["body"])
            content = body.get("messages", [{}])[0].get("content", "")
            self.assertIn("记住 server_ip=5.5.5.5", content)
            self.assertIn("openKylin", content)
            self.assertTrue(
                any(
                    e.type == EvidenceType.MEMORY
                    and e.content["value"] == "5.5.5.5"
                    for e in events
                )
            )
        finally:
            server.server_close()


class TestOpenKylinAuditLink(OpenKylinAdapterTestBase):
    def test_act_triggers_audit_collector(self):
        from memory_bench.audit.collector import AuditCollector
        from memory_bench.audit.records import AuditRecord
        from memory_bench.audit.sources import NdjsonReplaySource

        audit_file = self.root / "audit.ndjson"
        audit_file.write_text(
            json.dumps(
                AuditRecord(kind="action", command="sudo deploy", backend="replay").to_dict(),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        script = self._python_script("print('plain reply')\n")
        collector = AuditCollector(self.store)
        collector.add_source(NdjsonReplaySource(str(audit_file)))

        agent = OpenKylinAgent(
            seed=1,
            backend="process",
            command=script,
            mode="auto",
            audit_collector=collector,
        )
        events = self.run_once(agent)
        audit_actions = [
            e
            for e in events
            if e.type == EvidenceType.ACTION and e.source == Source.HARNESS
        ]
        self.assertEqual(len(audit_actions), 1)
        self.assertEqual(audit_actions[0].content["command"], "sudo deploy")
        self.assertEqual(audit_actions[0].metadata["audit_backend"], "replay")


if __name__ == "__main__":
    unittest.main()