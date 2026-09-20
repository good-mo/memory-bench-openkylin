"""证据事件模型：把对话、记忆操作、行动轨迹、文件产物统一为带时间戳的证据事件。"""

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


class EvidenceType(enum.Enum):
    """证据事件的六类类型。"""

    DIALOGUE = "DIALOGUE"      # 对话
    MEMORY = "MEMORY"          # 记忆操作
    ACTION = "ACTION"          # 行动轨迹
    ARTIFACT = "ARTIFACT"      # 文件产物
    CHECKPOINT = "CHECKPOINT"  # 评测锚点
    TOOL = "TOOL"              # 外部工具调用（OAS 契约驱动，M7）


class MemoryOp(enum.Enum):
    """记忆操作的语义类型。"""

    WRITE = "WRITE"    # 写入
    READ = "READ"      # 读取
    UPDATE = "UPDATE"  # 更新
    DELETE = "DELETE"  # 删除
    QUERY = "QUERY"    # 查询


class Source(enum.Enum):
    """证据事件来源。"""

    USER = "USER"       # 用户
    AGENT = "AGENT"     # 智能体
    HARNESS = "HARNESS" # 评测框架


def _validate_ts(value: str) -> str:
    """校验时间戳为可解析的 ISO8601 字符串，返回原值。"""
    if not isinstance(value, str) or not value:
        raise ValueError("证据事件 ts 必须是非空 ISO8601 字符串")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("证据事件 ts 无法解析为 ISO8601 时间：{!r}".format(value)) from exc
    return value


@dataclass
class EvidenceEvent:
    """一条带时间戳的证据事件。

    属性：
        ev_id: 事件唯一标识，格式 "ev-0001"，由 EvidenceStore 顺序分配递增。
        type: 证据类型（EvidenceType）。
        ts: 事件时间戳，ISO8601 + UTC 字符串。
        session: 会话标识。
        task: 所属任务名，可为空字符串。
        source: 来源（USER / AGENT / HARNESS）。
        content: 证据正文，dict。
        metadata: 附加元数据，dict，默认空。
    """

    ev_id: str
    type: EvidenceType
    ts: str
    session: str
    source: Source
    content: Dict[str, Any] = field(default_factory=dict)
    task: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 归一化枚举类型，容忍字符串入参
        if isinstance(self.type, str):
            self.type = EvidenceType(self.type)
        if isinstance(self.source, str):
            self.source = Source(self.source)
        if self.ev_id is None:
            raise ValueError("证据事件 ev_id 必填且必须为非空字符串")
        self.ts = _validate_ts(self.ts)
        if self.content is None:
            self.content = {}
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为普通 dict（枚举转为字符串值）。"""
        return {
            "ev_id": self.ev_id,
            "type": self.type.value,
            "ts": self.ts,
            "session": self.session,
            "task": self.task,
            "source": self.source.value,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceEvent":
        """从 dict 构造事件，缺失字段用默认值填充。"""
        return cls(
            ev_id=data.get("ev_id"),
            type=EvidenceType(data["type"]) if isinstance(data["type"], str) else data["type"],
            ts=data["ts"],
            session=data.get("session", ""),
            task=data.get("task", ""),
            source=Source(data["source"]) if isinstance(data["source"], str) else data["source"],
            content=data.get("content", {}),
            metadata=data.get("metadata", {}),
        )

    def to_line(self) -> str:
        """序列化为 NDJSON 单行（JSON 单行，末尾不含换行）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_line(cls, line: str) -> "EvidenceEvent":
        """从 NDJSON 单行反序列化。"""
        return cls.from_dict(json.loads(line))

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<EvidenceEvent {} {} {}>".format(self.ev_id, self.type.value, self.ts)


def now_utc() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()