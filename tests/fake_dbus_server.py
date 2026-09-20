"""假 openKylin AI Assistant D-Bus 服务端（端到端单测使用）。

按真实实现（kylin-ai-runtime，Gitee 官方仓）模拟：
- unix socket 监听 assistant.sock，EXTERNAL 认证（AUTH -> OK -> BEGIN）；
- init() 返回 (session_Id:i, 0, "")；
- chat(message:s, session_Id:i) 立即 METHOD_RETURN，随后经 SIGNAL
  「ChatResult」（接口 com.kylin.AiRuntime.Assistant<sessionId>，
  对象路径 /com/kylin/AiRuntime/Assistant，载荷 (result_json:s, 0:i)）
  异步推送回复（见 requesthandler.cpp sendChatResult）。
"""

import json
import os
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from memory_bench.dbus.client import try_parse_message
from memory_bench.dbus.message import DBusMessage, ERROR, METHOD_CALL, METHOD_RETURN, SIGNAL

OBJECT_PATH = "/com/kylin/AiRuntime/Assistant"
INTERFACE = "com.kylin.AiRuntime.Assistant"


class FakeAssistantDBusServer:
    """可在测试中启动/停止的假 Assistant D-Bus 服务。"""

    def __init__(
        self,
        replies: Optional[List[str]] = None,
        reply_delay: float = 0.0,
        init_error_code: int = 0,
        init_error_message: str = "",
        fail_init: bool = False,
    ):
        self._tmp = tempfile.mkdtemp(prefix="mb_fake_assistant_")
        self.sock_path = os.path.join(self._tmp, "assistant.sock")
        self.replies = replies
        self.reply_delay = reply_delay
        self.init_error_code = init_error_code
        self.init_error_message = init_error_message
        self.fail_init = fail_init
        self.closed = False
        self.received: List[Tuple[str, Tuple[Any, ...]]] = []
        self.members_seen: List[str] = []
        self.init_count = 0
        self._out_serial = 0
        self._next_session_id = 42
        self._next_idx = 0
        self._lock = threading.Lock()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        self._srv.listen(8)
        self._thread = threading.Thread(
            target=self._accept_loop, name="fake-assistant-accept", daemon=True
        )

    # ------------------------------------------------------------- 生命周期

    def address(self) -> str:
        return "unix:path={}".format(self.sock_path)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.closed = True
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.sock_path)
            os.rmdir(self._tmp)
        except OSError:
            pass

    def next_reply(self) -> str:
        if not self.replies:
            return "已收到你的消息"
        with self._lock:
            reply = self.replies[self._next_idx % len(self.replies)]
            self._next_idx += 1
            return reply

    def _msg_serial(self) -> int:
        with self._lock:
            self._out_serial += 1
            return self._out_serial

    # ------------------------------------------------------------- 网络循环

    def _accept_loop(self) -> None:
        while not self.closed:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handle_conn, args=(conn,), daemon=True
            ).start()

    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10.0)
            buf = self._handshake(conn)
            while not self.closed:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while True:
                    msg, used = try_parse_message(buf)
                    if msg is None:
                        break
                    buf = buf[used:]
                    if msg.msg_type == METHOD_CALL:
                        self._on_method_call(conn, msg)
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handshake(self, conn: socket.socket) -> bytes:
        """完成 EXTERNAL 认证并进入消息阶段，返回 BEGIN 后的残留缓冲。"""
        data = b""
        while b"\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise OSError("认证期间客户端断开")
            data += chunk
        if b"\x00AUTH EXTERNAL" not in data:
            raise OSError("非 EXTERNAL 认证请求")
        conn.sendall(b"OK 4a3c2b1d5e6f70891a2b3c\r\n")
        rest = b""
        while b"BEGIN\r\n" not in rest:
            chunk = conn.recv(4096)
            if not chunk:
                raise OSError("未收到 BEGIN")
            rest += chunk
        prefix, _, remain = rest.partition(b"BEGIN\r\n")
        return remain

    # ------------------------------------------------------------- 方法分发

    def _on_method_call(self, conn: socket.socket, msg: DBusMessage) -> None:
        member = msg.member or ""
        with self._lock:
            self.members_seen.append(member)
            self.received.append((member, tuple(msg.body)))
        if member == "no_reply":
            return  # 故意不回复，用于超时测试
        handler = getattr(self, "_handle_{}".format(member), None)
        if handler is None:
            self._send(
                conn,
                DBusMessage(
                    ERROR,
                    error_name="org.freedesktop.DBus.Error.UnknownMethod",
                    reply_serial=msg.serial,
                    signature="s",
                    body=("unknown method {}".format(member),),
                    serial=self._msg_serial(),
                ),
            )
            return
        handler(conn, msg)

    def _handle_init(self, conn: socket.socket, msg: DBusMessage) -> None:
        self.init_count += 1
        body: Tuple[Any, ...]
        if self.fail_init or self.init_error_code:
            body = (0, self.init_error_code, self.init_error_message)
        else:
            session_id = self._next_session_id
            self._next_session_id += 1
            body = (session_id, 0, "")
        self._send(
            conn,
            DBusMessage(
                METHOD_RETURN,
                reply_serial=msg.serial,
                signature="iis",
                body=body,
                serial=self._msg_serial(),
            ),
        )

    def _handle_chat(self, conn: socket.socket, msg: DBusMessage) -> None:
        message, session_id = msg.body[0], msg.body[1]
        self._send(
            conn,
            DBusMessage(
                METHOD_RETURN,
                reply_serial=msg.serial,
                signature="",
                body=(),
                serial=self._msg_serial(),
            ),
        )
        text = self.next_reply()
        time.sleep(self.reply_delay) if self.reply_delay else None
        self._push_result(conn, int(session_id), text)

    def _push_result(self, conn: socket.socket, session_id: int, text: str) -> None:
        result = json.dumps(
            {
                "message_type": "chat",
                "model": "fake-kylin",
                "content": [
                    {
                        "type": "text",
                        "text": {
                            "sentence_id": 0,
                            "result": text,
                            "is_end": True,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        signal = DBusMessage(
            SIGNAL,
            path=OBJECT_PATH,
            interface="{}{}".format(INTERFACE, session_id),
            member="ChatResult",
            signature="si",
            body=(result, 0),
            serial=self._msg_serial(),
        )
        try:
            self._send(conn, signal)
        except OSError:
            pass

    def _handle_stop_chat(self, conn: socket.socket, msg: DBusMessage) -> None:
        self._send_empty_reply(conn, msg)

    def _handle_clear_context(self, conn: socket.socket, msg: DBusMessage) -> None:
        self._send_empty_reply(conn, msg)

    def _handle_supportedFeatures(self, conn: socket.socket, msg: DBusMessage) -> None:
        self._send(
            conn,
            DBusMessage(
                METHOD_RETURN,
                reply_serial=msg.serial,
                signature="i",
                body=(2097151,),
                serial=self._msg_serial(),
            ),
        )

    def _send_empty_reply(self, conn: socket.socket, msg: DBusMessage) -> None:
        self._send(
            conn,
            DBusMessage(
                METHOD_RETURN,
                reply_serial=msg.serial,
                signature="",
                body=(),
                serial=self._msg_serial(),
            ),
        )

    def _send(self, conn: socket.socket, msg: DBusMessage) -> None:
        conn.sendall(msg.to_bytes())