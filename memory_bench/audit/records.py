"""审计记录模型：OS 侧收集到的原始行为记录，以及到证据事件的翻译。"""

from dataclasses import dataclass, field
from typing import Any, Dict

from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc


@dataclass
class AuditRecord:
    """一条 OS 侧审计记录（尚未写入证据存储）。

    kind 指定翻译后应落成的证据类型：
      - "action"    → ACTION（命令/系统调用执行轨迹）
      - "artifact"  → ARTIFACT（文件产物/变更）
      - "dialogue"  → DIALOGUE（日志等其他文本）
    metadata 携带审计后端与原始摘要，便于追踪证据来源。
    """

    kind: str
    command: str = ""
    path: str = ""
    content: str = ""
    detail: str = ""
    ts: str = ""
    backend: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_event(self, session: str, task: str = "") -> EvidenceEvent:
        """翻译为 EvidenceEvent。

        - 统一 source=HARNESS：审计由评测框架（而非智能体）采集；
        - metadata 记录审计后端（audit_backend）与原始命令摘要。
        """
        etype = EvidenceType.ACTION
        if self.kind == "artifact":
            etype = EvidenceType.ARTIFACT
        elif self.kind == "dialogue":
            etype = EvidenceType.DIALOGUE

        meta = dict(self.metadata)
        meta["audit_backend"] = self.backend or "os"
        if self.detail:
            meta["detail"] = self.detail

        return EvidenceEvent(
            ev_id="",
            type=etype,
            ts=self.ts or now_utc(),
            session=session,
            task=task,
            source=Source.HARNESS,
            content={
                "command": self.command,
                "path": self.path,
                "content": self.content,
            },
            metadata=meta,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "command": self.command,
            "path": self.path,
            "content": self.content,
            "detail": self.detail,
            "ts": self.ts,
            "backend": self.backend,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditRecord":
        kind = str(data.get("kind", "action"))
        if kind not in ("action", "artifact", "dialogue"):
            kind = "dialogue"
        return cls(
            kind=kind,
            command=str(data.get("command", "")),
            path=str(data.get("path", "")),
            content=str(data.get("content", "")),
            detail=str(data.get("detail", "")),
            ts=str(data.get("ts", "")),
            backend=str(data.get("backend", "")),
            metadata=dict(data.get("metadata") or {}),
        )