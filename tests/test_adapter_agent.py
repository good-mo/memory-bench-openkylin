"""通用外部智能体适配器（AdapterAgent）单元测试。"""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agents.adapter_agent import AdapterAgent, _extract_json
from memory_bench.evidence.model import EvidenceType
from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.agent import AgentContext


class AdapterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mb_adapter_")
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


class TestAdapterProcessBackend(AdapterTestBase):
    def test_process_backend_json_via_stdin(self):
        script = self._python_script(
            "import json, sys\n"
            "msg = sys.stdin.read().strip()\n"
            "print(json.dumps({'memory_update': [{'op': 'WRITE', "
            "'key': 'server_ip', 'value': '10.0.0.1'}], "
            "'reply': '\u5df2\u8bb0\u4f4f ' + msg}))\n"
        )
        agent = AdapterAgent(seed=1, backend="process", command=script, mode="json")
        events = self.run_once(agent, user_message="\u8bb0\u4f4f server_ip=10.0.0.1")
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        dialogues = [e for e in events if e.type == EvidenceType.DIALOGUE]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["op"], "WRITE")
        self.assertEqual(memories[0].content["key"], "server_ip")
        self.assertEqual(memories[0].content["value"], "10.0.0.1")
        self.assertTrue(
            any("server_ip=10.0.0.1" in d.content.get("text", "") for d in dialogues)
        )

    def test_process_backend_arg_placeholder(self):
        script = self._python_script(
            "import json, sys\n"
            "print(json.dumps({'reply': 'got: ' + sys.argv[1]}))\n"
        )
        agent = AdapterAgent(
            seed=1,
            backend="process",
            command=script + " {user_message}",
            mode="json",
        )
        events = self.run_once(agent, user_message="hello-arg")
        self.assertTrue(
            any("got: hello-arg" in e.content.get("text", "") for e in events)
        )

    def test_process_backend_mb_protocol(self):
        lines = [
            "MB|MEMORY|" + json.dumps({"op": "WRITE", "key": "port", "value": "2222"}),
            "MB|DIALOGUE|" + json.dumps({"text": "hello from external"}),
        ]
        script = self._python_script(
            "for l in {!r}:\n    print(l)\n".format(lines)
        )
        agent = AdapterAgent(seed=1, backend="process", command=script, mode="mb")
        events = self.run_once(agent)
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        dialogues = [e for e in events if e.type == EvidenceType.DIALOGUE]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["key"], "port")
        self.assertEqual(memories[0].content["value"], "2222")
        self.assertTrue(
            any("hello from external" in d.content.get("text", "") for d in dialogues)
        )

    def test_process_backend_text_heuristic(self):
        script = self._python_script(
            "print('\u597d\u7684\uff0c\u8bb0\u4f4f server_ip=192.168.1.9 \u7aef\u53e3\u662f 22')\n"
        )
        agent = AdapterAgent(seed=1, backend="process", command=script, mode="text")
        events = self.run_once(agent)
        memories = [e for e in events if e.type == EvidenceType.MEMORY]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content["op"], "WRITE")
        self.assertEqual(memories[0].content["key"], "server_ip")
        self.assertEqual(memories[0].content["value"], "192.168.1.9")

    def test_process_backend_auto_falls_back_to_dialogue(self):
        script = self._python_script("print('plain text reply')\n")
        agent = AdapterAgent(seed=1, backend="process", command=script)
        events = self.run_once(agent)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EvidenceType.DIALOGUE)
        self.assertEqual(events[0].content["text"], "plain text reply")

    def test_process_backend_json_artifact_written(self):
        script = self._python_script(
            "import json\n"
            "print(json.dumps({'action': {'command': 'deploy', 'path': 'deploy.txt', "
            "'detail': 'x'}, 'artifact': {'path': 'deploy.txt', "
            "'content': 'ip=1.2.3.4'}}))\n"
        )
        agent = AdapterAgent(seed=1, backend="process", command=script, mode="json")
        events = self.run_once(agent)
        self.assertTrue(any(e.type == EvidenceType.ACTION for e in events))
        self.assertTrue(any(e.type == EvidenceType.ARTIFACT for e in events))
        artifact_path = self.root / "ws" / "deploy.txt"
        self.assertTrue(artifact_path.exists())
        self.assertEqual(artifact_path.read_text(encoding="utf-8"), "ip=1.2.3.4")

    def test_process_backend_timeout_reports_dialogue(self):
        script = self._python_script("import time\ntime.sleep(30)\n")
        agent = AdapterAgent(
            seed=1, backend="process", command=script, timeout=1, mode="json"
        )
        events = self.run_once(agent)
        self.assertTrue(
            any(
                "\u5916\u90e8\u667a\u80fd\u4f53\u8c03\u7528\u5931\u8d25" in e.content.get("text", "")
                for e in events
            )
        )

    def test_process_backend_missing_command_errors_gracefully(self):
        agent = AdapterAgent(seed=1, backend="process", command=None)
        events = self.run_once(agent)
        self.assertTrue(
            any(
                "\u5916\u90e8\u667a\u80fd\u4f53\u8c03\u7528\u5931\u8d25" in e.content.get("text", "")
                for e in events
            )
        )

    def test_process_backend_output_stdout_only(self):
        script = self._python_script(
            "import sys\n"
            "print('final answer')\n"
            "print('reasoning noise', file=sys.stderr)\n"
        )
        agent = AdapterAgent(
            seed=1, backend="process", command=script, output="stdout"
        )
        events = self.run_once(agent)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].content["text"], "final answer")
        self.assertNotIn("reasoning noise", events[0].content["text"])

    def test_prompt_prefix_prepended_to_message(self):
        received = {}

        script = self._python_script(
            "import json, sys\n"
            "received = sys.stdin.read().strip()\n"
            "print(json.dumps({'reply': received}))\n"
        )
        agent = AdapterAgent(
            seed=1,
            backend="process",
            command=script,
            mode="json",
            prompt_prefix="PROTO: reply with MB lines",
        )
        events = self.run_once(agent, user_message="记住 port=22")
        self.assertTrue(
            any(
                "PROTO: reply with MB lines" in e.content.get("text", "")
                and "记住 port=22" in e.content.get("text", "")
                for e in events
            )
        )


class TestAdapterHttpBackend(AdapterTestBase):
    def test_http_backend_receives_message_and_parses_reply(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                payload = json.dumps({"reply": "http ok"}).encode("utf-8")
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
            agent = AdapterAgent(seed=1, backend="http", url=url, mode="json")
            events = self.run_once(agent, user_message="\u8bb0\u4f4f port=3333")
            self.assertEqual(received["body"].get("user_message"), "\u8bb0\u4f4f port=3333")
            self.assertTrue(
                any(
                    e.type == EvidenceType.DIALOGUE
                    and e.content.get("text") == "http ok"
                    for e in events
                )
            )
        finally:
            server.server_close()

    def test_http_backend_auth_header(self):
        sent_headers = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                sent_headers["auth"] = self.headers.get("Authorization")
                payload = b"{}"
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
            agent = AdapterAgent(seed=1, backend="http", url=url, token="tok-123")
            self.run_once(agent)
            self.assertEqual(sent_headers["auth"], "Bearer tok-123")
        finally:
            server.server_close()


class TestAdapterHelpersAndRegistry(AdapterTestBase):
    def test_extract_json_helper(self):
        self.assertEqual(_extract_json('prefix {"a": 1} suffix')["a"], 1)
        self.assertIsNone(_extract_json("no json here"))
        self.assertIsNone(_extract_json(""))

    def test_make_agent_registers_adapter(self):
        from memory_bench.runner import make_agent

        agent = make_agent("adapter", 7)
        self.assertIsInstance(agent, AdapterAgent)
        self.assertEqual(agent.name, "adapter")

    def test_repr_shows_backend_and_mode(self):
        agent = AdapterAgent(seed=1, backend="http", url="http://x", mode="auto")
        self.assertIn("http", repr(agent))
        self.assertIn("auto", repr(agent))


if __name__ == "__main__":
    unittest.main()