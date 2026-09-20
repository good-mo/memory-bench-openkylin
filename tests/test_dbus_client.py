"""DBusConnection 端到端测试（针对假 openKylin Assistant D-Bus 服务端）。"""

import json
import unittest

from memory_bench.dbus.client import DBusConnection, DBusError

from tests.fake_dbus_server import FakeAssistantDBusServer


class SignalCollector:
    def __init__(self):
        self.signals = []

    def __call__(self, signal):
        self.signals.append(signal)


class DBusConnectionEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeAssistantDBusServer(
            replies=['MB|DIALOGUE|{"text":"openkylin dbus ok"}']
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.collector = SignalCollector()
        self.conn = DBusConnection(
            address=self.server.address(),
            timeout=5.0,
            signal_handler=self.collector,
        )
        self.conn.connect()
        self.addCleanup(self.conn.close)

    def test_auth_init_chat_signal_roundtrip(self):
        body = self.conn.call(
            "com.kylin.AiRuntime.Assistant",
            "init",
            "/com/kylin/AiRuntime/Assistant",
        )
        self.assertEqual(body[0], 42)  # 首个 session id
        self.assertEqual(body[1], 0)
        self.assertEqual(body[2], "")
        session_id = int(body[0])

        payload = json.dumps(
            {"content": [{"type": "text", "text": "记住 port=7777"}]},
            ensure_ascii=False,
        )
        self.collector.signals.clear()
        reply = self.conn.call(
            "com.kylin.AiRuntime.Assistant",
            "chat",
            "/com/kylin/AiRuntime/Assistant",
            signature="si",
            body=(payload, session_id),
        )
        self.assertEqual(reply, ())

        self.wait_signal(5.0)
        self.assertEqual(len(self.collector.signals), 1)
        sig = self.collector.signals[0]
        self.assertEqual(sig.member, "ChatResult")
        self.assertEqual(sig.interface, "com.kylin.AiRuntime.Assistant42")
        self.assertEqual(sig.path, "/com/kylin/AiRuntime/Assistant")
        self.assertEqual(sig.body[1], 0)
        result = json.loads(sig.body[0])
        self.assertEqual(
            result["content"][0]["text"]["result"],
            'MB|DIALOGUE|{"text":"openkylin dbus ok"}',
        )

    def test_chat_message_payload_matches_engine_contract(self):
        session_id = int(
            self.conn.call(
                "com.kylin.AiRuntime.Assistant",
                "init",
                "/com/kylin/AiRuntime/Assistant",
            )[0]
        )
        self.conn.call(
            "com.kylin.AiRuntime.Assistant",
            "chat",
            "/com/kylin/AiRuntime/Assistant",
            signature="si",
            body=('{"content":[{"type":"text","text":"hello"}]}', session_id),
        )
        member, body = self.server.received[-1]
        self.assertEqual(member, "chat")
        message_json = json.loads(body[0])
        self.assertEqual(message_json["content"][0]["type"], "text")
        self.assertEqual(message_json["content"][0]["text"], "hello")
        self.assertEqual(body[1], session_id)

    def test_error_reply_raises_dbus_error(self):
        with self.assertRaises(DBusError) as cm:
            self.conn.call(
                "com.kylin.AiRuntime.Assistant",
                "no_such_method",
                "/com/kylin/AiRuntime/Assistant",
            )
        self.assertEqual(
            cm.exception.error_name, "org.freedesktop.DBus.Error.UnknownMethod"
        )

    def wait_signal(self, timeout):
        import time

        deadline = time.time() + timeout
        while not self.collector.signals and time.time() < deadline:
            time.sleep(0.02)
        return self.collector.signals


class DBusConnectionTimeoutTest(unittest.TestCase):
    def test_timeout_raises_timeout_error(self):
        server = FakeAssistantDBusServer(replies=["late"])
        server.start()
        self.addCleanup(server.stop)
        conn = DBusConnection(address=server.address(), timeout=5.0)
        conn.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(TimeoutError):
            conn.call(
                "com.kylin.AiRuntime.Assistant",
                "no_reply",
                "/com/kylin/AiRuntime/Assistant",
                timeout=0.5,
            )


if __name__ == "__main__":
    unittest.main()