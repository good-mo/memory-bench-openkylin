"""D-Bus wire 协议编解码与流式解析测试。"""

import unittest

from memory_bench.dbus.client import try_parse_message
from memory_bench.dbus.message import (
    DBusMessage,
    ERROR,
    METHOD_CALL,
    METHOD_RETURN,
    SIGNAL,
    marshal,
    unmarshal,
)


class TestMarshalUnmarshal(unittest.TestCase):
    def test_roundtrip_basic_types(self):
        cases = [
            ("i", (42,)),
            ("u", (7,)),
            ("b", (True,)),
            ("s", ("你好，openKylin",)),
            ("o", ("/com/kylin/AiRuntime/Assistant",)),
            ("g", ("si",)),
        ]
        for sig, values in cases:
            data = marshal(sig, values)
            got, _ = unmarshal(sig, data)
            self.assertEqual(got, values, sig)

    def test_roundtrip_multi(self):
        for sig, values in [
            ("si", ("记住 port=7777", 42)),
            ("iis", (42, 0, "")),
            ("is", (0, "no model")),
            ("ss", ("a", "bb")),
        ]:
            data = marshal(sig, values)
            got, _ = unmarshal(sig, data)
            self.assertEqual(got, values, sig)

    def test_string_alignment_matches_gdbus(self):
        # 实心验证：短字符串后的 int32 只按 4 字节对齐（真实 gdbus 行为），
        # 不能误用 8 字节补位。
        data = marshal("si", ("hello", 77))
        # s: len=6(含 NUL)+4 前缀 = 10 字节；i 对齐到 12（4 的倍数），结束于 16
        values, pos = unmarshal("si", data)
        self.assertEqual(values, ("hello", 77))
        self.assertEqual(pos, 16)

    def test_truncated_raises(self):
        data = marshal("s", ("hello",))
        with self.assertRaises(ValueError):
            unmarshal("s", data[:5])


class TestDBusMessageCodec(unittest.TestCase):
    def test_method_call_roundtrip(self):
        msg = DBusMessage(
            msg_type=METHOD_CALL,
            path="/com/kylin/AiRuntime/Assistant",
            interface="com.kylin.AiRuntime.Assistant",
            member="chat",
            signature="si",
            body=("记住 port=7777", 42),
            serial=5,
        )
        raw = msg.to_bytes()
        parsed, offset = DBusMessage.from_bytes(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(parsed.msg_type, METHOD_CALL)
        self.assertEqual(parsed.path, "/com/kylin/AiRuntime/Assistant")
        self.assertEqual(parsed.interface, "com.kylin.AiRuntime.Assistant")
        self.assertEqual(parsed.member, "chat")
        self.assertEqual(parsed.signature, "si")
        self.assertEqual(parsed.body, ("记住 port=7777", 42))
        self.assertEqual(parsed.serial, 5)

    def test_reply_serial_and_destination(self):
        msg = DBusMessage(
            msg_type=METHOD_RETURN,
            reply_serial=9,
            destination="com.kylin.AiRuntime.Assistant",
            signature="iis",
            body=(1, 0, ""),
            serial=6,
        )
        raw = msg.to_bytes()
        parsed, _ = DBusMessage.from_bytes(raw)
        self.assertEqual(parsed.msg_type, METHOD_RETURN)
        self.assertEqual(parsed.reply_serial, 9)
        self.assertEqual(parsed.destination, "com.kylin.AiRuntime.Assistant")
        self.assertEqual(parsed.body, (1, 0, ""))

    def test_error_message(self):
        msg = DBusMessage(
            msg_type=ERROR,
            error_name="org.freedesktop.DBus.Error.UnknownMethod",
            reply_serial=3,
            signature="s",
            body=("no such method",),
            serial=7,
        )
        parsed, _ = DBusMessage.from_bytes(msg.to_bytes())
        self.assertEqual(parsed.msg_type, ERROR)
        self.assertEqual(
            parsed.error_name, "org.freedesktop.DBus.Error.UnknownMethod"
        )
        self.assertEqual(parsed.reply_serial, 3)
        self.assertEqual(parsed.body, ("no such method",))

    def test_signal_roundtrip(self):
        msg = DBusMessage(
            msg_type=SIGNAL,
            path="/com/kylin/AiRuntime/Assistant",
            interface="com.kylin.AiRuntime.Assistant42",
            member="ChatResult",
            signature="si",
            body=('{"content":[]}', 0),
            serial=8,
        )
        parsed, _ = DBusMessage.from_bytes(msg.to_bytes())
        self.assertEqual(parsed.msg_type, SIGNAL)
        self.assertEqual(parsed.interface, "com.kylin.AiRuntime.Assistant42")
        self.assertEqual(parsed.member, "ChatResult")
        self.assertEqual(parsed.body, ('{"content":[]}', 0))

    def test_empty_body_message(self):
        msg = DBusMessage(
            msg_type=METHOD_RETURN, reply_serial=2, signature="", body=(), serial=9
        )
        raw = msg.to_bytes()
        parsed, offset = DBusMessage.from_bytes(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(parsed.body, ())


class TestStreamParsing(unittest.TestCase):
    def test_try_parse_message_stream(self):
        msg1 = DBusMessage(
            msg_type=METHOD_CALL,
            path="/com/kylin/AiRuntime/Assistant",
            interface="com.kylin.AiRuntime.Assistant",
            member="init",
            serial=1,
        )
        msg2 = DBusMessage(
            msg_type=METHOD_CALL,
            path="/com/kylin/AiRuntime/Assistant",
            interface="com.kylin.AiRuntime.Assistant",
            member="chat",
            signature="si",
            body=("记住 port=42", 42),
            serial=2,
        )
        stream = msg1.to_bytes() + msg2.to_bytes()
        # 逐字节喂给解析器，模拟 unix socket 粘包/分包
        buf = b""
        parsed = []
        for byte in stream:
            buf += bytes([byte])
            while True:
                msg, used = try_parse_message(buf)
                if msg is None:
                    break
                parsed.append(msg)
                buf = buf[used:]
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].member, "init")
        self.assertEqual(parsed[0].serial, 1)
        self.assertEqual(parsed[1].member, "chat")
        self.assertEqual(parsed[1].body, ("记住 port=42", 42))


if __name__ == "__main__":
    unittest.main()