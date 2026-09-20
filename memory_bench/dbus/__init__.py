"""纯标准库 D-Bus 客户端（面向 openKylin AI 子系统私有总线）。

openKylin AI 子系统（kylin-ai-runtime）通过私有 D-Bus 总线（Unix socket，
peer-to-peer）暴露助手服务 com.kylin.AiRuntime.Assistant。本模块用 Python
标准库实现最小可用的 D-Bus 客户端：EXTERNAL 认证握手、METHOD_CALL、
METHOD_RETURN/ERROR 与 SIGNAL 解析，无任何第三方依赖。

真实协议来源：https://gitee.com/openkylin/kylin-ai-proto
（protocols/usr/share/kylin-ai/protocols/）
"""

from .message import DBusMessage, Signature, marshal, unmarshal
from .client import DBusConnection, DBusError, DBusSignal

__all__ = [
    "DBusConnection",
    "DBusError",
    "DBusSignal",
    "DBusMessage",
    "Signature",
    "marshal",
    "unmarshal",
]