"""D-Bus wire 协议实现（纯标准库，对齐规则与 gdbus/GLib 兼容）。

仅覆盖 openKylin AI 子系统（kylin-ai-proto gdbus 定义）用到的子集：
- 基础类型：u(uint32) i(int32) b(bool) s(string) o(object path) g(signature)
- 消息帧：16 字节固定头 + header fields 数组（STRUCT(BYTE, VARIANT)，8 对齐）+ body
- header 字段：PATH / INTERFACE / MEMBER / REPLY_SERIAL / DESTINATION / SIGNATURE
- 连接协商：EXTERNAL 认证（AUTH OK -> BEGIN），little-endian

对齐规则（D-Bus 规范，相对消息起点；body 起点为 8 的倍数，故相对 body 也等价）：
- BYTE=1，BOOL/INT32/UINT32/STRING/OBJECT_PATH/SIGNATURE=4，STRUCT/ARRAY/VARIANT=8；
- 字符串：uint32 长度（4 对齐）+ bytes + NUL；其后仅按下一值的类型对齐（无固定 8 补位）；
- header fields 数组元素为 STRUCT(BYTE, VARIANT)，每个元素 8 对齐，数组总长为 8 的倍数，
  使 body 起始位于 8 字节边界。
"""

import struct
from typing import Any, Dict, List, Optional, Tuple

# 消息类型
METHOD_CALL = 1
METHOD_RETURN = 2
ERROR = 3
SIGNAL = 4

# header 字段代码
FIELD_PATH = 1
FIELD_INTERFACE = 2
FIELD_MEMBER = 3
FIELD_ERROR_NAME = 4
FIELD_REPLY_SERIAL = 5
FIELD_DESTINATION = 6
FIELD_SENDER = 7
FIELD_SIGNATURE = 8

_LITTLE = "<"

# 各基础类型的对齐字节数
_TYPE_ALIGN = {"b": 4, "u": 4, "i": 4, "s": 4, "o": 4, "g": 4}
_STRING_TYPES = {"s", "o", "g"}


def _pad_count(pos: int, alignment: int) -> int:
    return (alignment - (pos % alignment)) % alignment


def _encode_value(sig: str, value: Any, pos: int) -> Tuple[bytes, int]:
    """编码单个基础类型值（不含前置对齐），返回 (bytes, 结束位置)。"""
    if sig == "b":
        return struct.pack(_LITTLE + "I", 1 if value else 0), pos + 4
    if sig == "u":
        return struct.pack(_LITTLE + "I", value), pos + 4
    if sig == "i":
        return struct.pack(_LITTLE + "i", value), pos + 4
    if sig in _STRING_TYPES:
        raw = str(value).encode("utf-8") + b"\x00"
        chunk = struct.pack(_LITTLE + "I", len(raw)) + raw
        return chunk, pos + len(chunk)
    raise ValueError("不支持的签名类型 {!r}".format(sig))


def _decode_value(sig: str, data: bytes, pos: int) -> Tuple[Any, int]:
    """解码单个基础类型值（pos 已对齐），返回 (value, 结束位置)。"""
    if sig == "b":
        return bool(struct.unpack(_LITTLE + "I", data[pos : pos + 4])[0]), pos + 4
    if sig == "u":
        return struct.unpack(_LITTLE + "I", data[pos : pos + 4])[0], pos + 4
    if sig == "i":
        return struct.unpack(_LITTLE + "i", data[pos : pos + 4])[0], pos + 4
    if sig in _STRING_TYPES:
        length = struct.unpack_from(_LITTLE + "I", data, pos)[0]
        pos += 4
        raw = data[pos : pos + length]
        pos += length
        return raw[:-1].decode("utf-8"), pos
    raise ValueError("不支持的签名类型 {!r}".format(sig))


def marshal(signature: str, values: Any) -> bytes:
    """把一段 body 编码为字节：按类型自动对齐，尾部不补对齐（与 gdbus 一致）。"""
    buf = bytearray()
    pos = 0
    for sig, value in zip(signature, values):
        pad = _pad_count(pos, _TYPE_ALIGN[sig])
        buf += b"\x00" * pad
        pos += pad
        chunk, pos = _encode_value(sig, value, pos)
        buf += chunk
    return bytes(buf)


def unmarshal(
    signature: str, data: bytes, offset: int = 0, end: Optional[int] = None
) -> Tuple[Tuple[Any, ...], int]:
    """从 data[offset:end] 解码一段 body，返回 (值元组, 结束位置)。"""
    values: List[Any] = []
    pos = offset
    limit = len(data) if end is None else end
    for sig in signature:
        pos += _pad_count(pos, _TYPE_ALIGN[sig])
        if sig in _STRING_TYPES:
            if pos + 4 > limit:
                raise ValueError("body 截断，无法解析字符串 {!r}".format(sig))
            length = struct.unpack_from(_LITTLE + "I", data, pos)[0]
            if pos + 4 + length > limit:
                raise ValueError(
                    "body 截断，字符串 {!r} 需 {} 字节，实际剩余 {}".format(
                        sig, 4 + length, limit - pos
                    )
                )
        elif pos + 4 > limit:
            raise ValueError("body 截断，无法解析 {!r}".format(sig))
        value, pos = _decode_value(sig, data, pos)
        values.append(value)
    return tuple(values), pos


def _encode_variant(sig: str, value: Any) -> bytes:
    """variant = signature 字符串 + 8 对齐补位 + 值（内部按自身类型对齐）。"""
    out = bytearray()
    sig_raw = sig.encode("ascii") + b"\x00"
    out += struct.pack(_LITTLE + "I", len(sig_raw)) + sig_raw
    out += b"\x00" * _pad_count(len(out), _TYPE_ALIGN[sig])
    chunk, _ = _encode_value(sig, value, len(out))
    out += chunk
    return bytes(out)


def _decode_variant(data: bytes, pos: int) -> Tuple[str, Any, int]:
    """解析 variant：返回 (sig, value, 新 pos)。"""
    sig_len = struct.unpack_from(_LITTLE + "I", data, pos)[0]
    pos += 4
    sig = data[pos : pos + sig_len - 1].decode("ascii")
    pos += sig_len
    pos += _pad_count(pos, _TYPE_ALIGN[sig])
    value, pos = _decode_value(sig, data, pos)
    return sig, value, pos


class DBusMessage:
    """一条 D-Bus 消息：header 字段 + body。"""

    __slots__ = (
        "msg_type",
        "path",
        "interface",
        "member",
        "error_name",
        "reply_serial",
        "destination",
        "sender",
        "signature",
        "body",
        "serial",
    )

    def __init__(
        self,
        msg_type: int,
        path: Optional[str] = None,
        interface: Optional[str] = None,
        member: Optional[str] = None,
        error_name: Optional[str] = None,
        reply_serial: Optional[int] = None,
        destination: Optional[str] = None,
        sender: Optional[str] = None,
        signature: str = "",
        body: Tuple[Any, ...] = (),
        serial: int = 0,
    ):
        self.msg_type = msg_type
        self.path = path
        self.interface = interface
        self.member = member
        self.error_name = error_name
        self.reply_serial = reply_serial
        self.destination = destination
        self.sender = sender
        self.signature = signature
        self.body = body
        self.serial = serial

    # ------------------------------------------------------------------ 编码

    def to_bytes(self) -> bytes:
        """序列化为 wire 字节流（固定头 + header fields + body）。"""
        fields = self._encode_fields()
        body_raw = marshal(self.signature, self.body)
        header = bytearray()
        header += b"l"  # little-endian
        header += struct.pack(
            _LITTLE + "BBB", self.msg_type, 0, 1
        )  # type, flags, version
        header += struct.pack(_LITTLE + "I", len(body_raw))  # body length
        header += struct.pack(_LITTLE + "I", self.serial)  # serial
        header += struct.pack(_LITTLE + "I", len(fields))  # header fields length
        return bytes(header) + fields + body_raw

    def _encode_fields(self) -> bytes:
        """Header fields 数组：STRUCT(BYTE, VARIANT)，元素 8 对齐，数组总长 8 的倍数。"""
        entries: List[Tuple[int, str, Any]] = []
        if self.path is not None:
            entries.append((FIELD_PATH, "o", self.path))
        if self.interface is not None:
            entries.append((FIELD_INTERFACE, "s", self.interface))
        if self.member is not None:
            entries.append((FIELD_MEMBER, "s", self.member))
        if self.error_name is not None:
            entries.append((FIELD_ERROR_NAME, "s", self.error_name))
        if self.reply_serial is not None:
            entries.append((FIELD_REPLY_SERIAL, "u", self.reply_serial))
        if self.destination is not None:
            entries.append((FIELD_DESTINATION, "s", self.destination))
        if self.sender is not None:
            entries.append((FIELD_SENDER, "s", self.sender))
        if self.signature:
            entries.append((FIELD_SIGNATURE, "g", self.signature))

        buf = bytearray()
        for code, sig, value in entries:
            buf += b"\x00" * _pad_count(len(buf), 8)  # 元素 8 对齐
            buf += struct.pack(_LITTLE + "B", code)  # byte
            buf += b"\x00" * _pad_count(len(buf), 8)  # variant 8 对齐
            buf += _encode_variant(sig, value)
        buf += b"\x00" * _pad_count(len(buf), 8)  # 数组总长 8 的倍数（body 8 对齐）
        return bytes(buf)

    # ------------------------------------------------------------------ 解码

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> Tuple["DBusMessage", int]:
        """从 data[offset:] 解析一条消息，返回 (msg, 下一条消息偏移)。"""
        if data[offset : offset + 1] != b"l":
            raise ValueError("byte order 非 little-endian")
        offset += 1
        msg_type, _flags, _version = struct.unpack_from(_LITTLE + "BBB", data, offset)
        offset += 3
        body_len = struct.unpack_from(_LITTLE + "I", data, offset)[0]
        offset += 4
        serial = struct.unpack_from(_LITTLE + "I", data, offset)[0]
        offset += 4
        header_len = struct.unpack_from(_LITTLE + "I", data, offset)[0]
        offset += 4  # offset = 16

        msg = DBusMessage(msg_type=msg_type, serial=serial)
        fields_end = offset + header_len
        while offset < fields_end:
            offset += _pad_count(offset, 8)
            if offset >= fields_end:
                break
            code = struct.unpack_from(_LITTLE + "B", data, offset)[0]
            offset += 1
            offset += _pad_count(offset, 8)
            sig, value, offset = _decode_variant(data, offset)
            msg._set_field(code, value)

        offset += _pad_count(offset, 8)  # body 起始 8 对齐
        if body_len:
            body, offset = unmarshal(msg.signature, data, offset, offset + body_len)
            msg.body = body
        return msg, offset

    def _set_field(self, code: int, value: Any) -> None:
        if code == FIELD_PATH:
            self.path = value
        elif code == FIELD_INTERFACE:
            self.interface = value
        elif code == FIELD_MEMBER:
            self.member = value
        elif code == FIELD_ERROR_NAME:
            self.error_name = value
        elif code == FIELD_REPLY_SERIAL:
            self.reply_serial = value
        elif code == FIELD_DESTINATION:
            self.destination = value
        elif code == FIELD_SENDER:
            self.sender = value
        elif code == FIELD_SIGNATURE:
            self.signature = value
        else:
            raise ValueError("未知 header 字段代码 {}".format(code))


class Signature:
    """常用签名常量。"""

    S = "s"
    I = "i"
    IS = "is"
    SI = "si"
    SS = "ss"
    SIS = "sis"
    IIS = "iis"