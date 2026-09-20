"""最小 D-Bus 客户端（纯标准库，面向 openKylin AI 子系统私有总线）。

openKylin AI 子系统（kylin-ai-runtime）通过 GDBusServer 监听 Unix socket，
服务端独立于系统/会话总线（peer-to-peer）。客户端流程：
1. 连接 unix socket，发送 NUL 并完成 EXTERNAL 认证（AUTH -> OK -> BEGIN）；
2. 发送 METHOD_CALL 调用 com.kylin.AiRuntime.Assistant 的方法；
3. 同步等待 METHOD_RETURN / ERROR；
4. init/chat 等都使用独立 session。

真实协议来源：https://gitee.com/openkylin/kylin-ai-proto
"""

import os
import socket as _socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .message import (
    DBusMessage,
    ERROR,
    METHOD_CALL,
    METHOD_RETURN,
    SIGNAL,
    marshal,
)


class DBusError(Exception):
    """D-Bus 调用返回 ERROR 消息。"""

    def __init__(self, name: str, body: Tuple[Any, ...]):
        super().__init__("{} {}".format(name, body))
        self.error_name = name
        self.body = body


class DBusSignal:
    """收到的 SIGNAL 消息。"""

    __slots__ = ("interface", "member", "path", "body", "sender")

    def __init__(
        self,
        interface: str,
        member: str,
        path: str,
        body: Tuple[Any, ...],
        sender: str = "",
    ):
        self.interface = interface
        self.member = member
        self.path = path
        self.body = body
        self.sender = sender

    def __repr__(self):  # pragma: no cover
        return "<DBusSignal {}.{} body={}>".format(
            self.interface, self.member, self.body
        )


class DBusConnection:
    """一条 D-Bus 连接：同步调用 + 信号监听回调。"""

    def __init__(
        self,
        address: str,
        timeout: float = 30.0,
        signal_handler: Optional[Callable[[DBusSignal], None]] = None,
        guid: Optional[str] = None,
    ):
        self.address = address
        self.timeout = timeout
        self.signal_handler = signal_handler
        self.guid = guid or "memory-bench-openkylin-guid"
        self._serial = 1
        self._lock = threading.Lock()
        self._pending: Dict[int, Tuple[Optional[DBusMessage], Optional[BaseException]]] = {}
        self._cond = threading.Condition(threading.Lock())
        self._reader: Optional[threading.Thread] = None
        self._closed = False
        self._sock: Optional[_socket.socket] = None

    # ------------------------------------------------------------------ 连接

    def connect(self) -> None:
        """解析 address 并建立连接（D-Bus EXTERNAL 认证）。"""
        sock = self._open_socket(self.address)
        self._sock = sock
        self._authenticate(sock)
        self._reader = threading.Thread(
            target=self._read_loop, name="mb-dbus-reader", daemon=True
        )
        self._reader.start()

    @staticmethod
    def _open_socket(address: str) -> _socket.socket:
        if address.startswith("unix:path="):
            path = address[len("unix:path=") :]
        elif "abstract=" in address:
            path = "\x00" + address.split("abstract=", 1)[1]
        else:
            raise ValueError("不支持的 D-Bus 地址 {!r}".format(address))
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(10.0)
        sock.connect(path)
        sock.settimeout(None)
        return sock

    def _authenticate(self, sock: _socket.socket) -> None:
        uid = os.getuid()
        hex_uid = "{:x}".format(uid).encode("ascii")
        hello = b"\x00" + b"AUTH EXTERNAL " + hex_uid + b"\r\n"
        sock.sendall(hello)
        buf = b""
        while True:
            data = sock.recv(4096)
            if not data:
                raise DBusError("DbusDisconnected", ("认证期间连接关闭",))
            buf += data
            if b"\r\n" in buf:
                line, _, buf = buf.partition(b"\r\n")
                break
        line = line.strip()
        if line.startswith(b"OK"):
            # OK <guid>：进入消息阶段
            sock.sendall(b"BEGIN\r\n")
            return
        if line.startswith(b"REJECTED"):
            raise DBusError("AuthRejected", (line.decode("utf-8", "replace"),))
        raise DBusError("AuthFailed", (line.decode("utf-8", "replace"),))

    # ------------------------------------------------------------------ 调用

    def call(
        self,
        interface: str,
        member: str,
        path: str,
        destination: Optional[str] = None,
        signature: str = "",
        body: Tuple[Any, ...] = (),
        timeout: Optional[float] = None,
    ) -> Tuple[Any, ...]:
        """同步方法调用，返回 body 元组；ERROR 时抛 DBusError。"""
        if self._sock is None or self._closed:
            raise DBusError("Disconnected", ("连接未建立或已关闭",))
        with self._cond:
            self._serial += 1
            serial = self._serial
            self._pending[serial] = (None, None)
        msg = DBusMessage(
            msg_type=METHOD_CALL,
            path=path,
            interface=interface,
            member=member,
            destination=destination,
            signature=signature,
            body=body,
            serial=serial,
        )
        payload = msg.to_bytes()
        with self._lock:
            self._sock.sendall(payload)

        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        with self._cond:
            while self._pending.get(serial) == (None, None):
                if self._closed:
                    raise DBusError("Disconnected", ("连接已关闭",))
                if time.time() > deadline:
                    self._pending.pop(serial, None)
                    raise TimeoutError("D-Bus 调用 {}.{} 超时".format(interface, member))
                self._cond.wait(timeout=min(0.5, deadline - time.time()))
            result, exc = self._pending.pop(serial, (None, None))
        if exc is not None:
            raise exc
        return result

    def close(self) -> None:
        self._closed = True
        with self._cond:
            self._cond.notify_all()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    # ------------------------------------------------------------------ 内部

    def _read_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        buf = b""
        try:
            while not self._closed:
                chunk = sock.recv(65536)
                if not chunk:
                    self._on_disconnect()
                    return
                buf += chunk
                # 按消息解析
                while True:
                    msg, used = try_parse_message(buf)
                    if msg is None:
                        break
                    buf = buf[used:]
                    self._dispatch(msg)
        except OSError:
            self._on_disconnect()
        except Exception:
            self._on_disconnect()

    def _on_disconnect(self) -> None:
        self._closed = True
        with self._cond:
            # 唤醒所有等待的调用并标记失败
            for serial in list(self._pending):
                if self._pending[serial] == (None, None):
                    self._pending[serial] = (
                        None,
                        DBusError(
                            "Disconnected",
                            ("服务端关闭了连接",),
                        ),
                    )
            self._cond.notify_all()

    def _dispatch(self, msg: DBusMessage) -> None:
        if msg.msg_type == METHOD_RETURN or msg.msg_type == ERROR:
            with self._cond:
                if msg.reply_serial in self._pending:
                    if msg.msg_type == METHOD_RETURN:
                        self._pending[msg.reply_serial] = (msg.body, None)
                    else:
                        self._pending[msg.reply_serial] = (
                            None,
                            DBusError(msg.error_name or "Error", msg.body),
                        )
                    self._cond.notify_all()
        elif msg.msg_type == SIGNAL:
            if self.signal_handler is not None:
                signal = DBusSignal(
                    interface=msg.interface or "",
                    member=msg.member or "",
                    path=msg.path or "",
                    body=msg.body,
                    sender=msg.sender or "",
                )
                try:
                    self.signal_handler(signal)
                except Exception:
                    self._on_disconnect()


# ---------------------------------------------------------------------------
# 消息流解析
# ---------------------------------------------------------------------------

_HEADER_FIXED = 16  # 1(order)+3(type/flags/version)+4(body_len)+4(serial)+4(fields_len)


def try_parse_message(buf: bytes) -> Tuple[Optional[DBusMessage], int]:
    """尝试从 buf 起始解析整条消息；数据不足返回 (None, 0)。"""
    if len(buf) < _HEADER_FIXED:
        return None, 0
    if buf[0:1] != b"l":
        raise ValueError("非 little-endian")
    body_len = struct.unpack_from("<I", buf, 4)[0]
    fields_len = struct.unpack_from("<I", buf, 12)[0]
    total = _HEADER_FIXED + fields_len + body_len
    # 注意：16 字节固定头后紧跟 header fields（已含其对齐），body 在 fields 之后；
    # fields 长度已含对齐(通常已 8 对齐)。总计校验。
    if len(buf) < total:
        return None, 0
    msg, _ = DBusMessage.from_bytes(buf)
    return msg, total