"""智能体协议：Agent 抽象基类 + AgentContext 上下文。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc
from memory_bench.evidence.store import EvidenceStore


@dataclass
class AgentContext:
    """提供给智能体的运行上下文。

    属性：
        store: 证据存储（智能体通过上下文把对话/记忆/动作/产物写为证据）。
        workspace: 工作区目录（智能体可在其中产出文件）。
        session: 会话标识。
        seed: 随机种子（保证可复现）。
    """

    store: EvidenceStore
    workspace: Path
    session: str
    seed: int = 0

    def emit(
        self,
        type: EvidenceType,
        content: Dict[str, Any],
        task: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EvidenceEvent:
        """以 AGENT 身份发出一条证据事件。"""
        event = EvidenceEvent(
            ev_id="",
            type=type,
            ts=now_utc(),
            session=self.session,
            task=task,
            source=Source.AGENT,
            content=content,
            metadata=metadata or {},
        )
        return self.store.append(event)


class Agent:
    """智能体抽象基类。

    子类实现 act(ctx, user_message, step_name)：收到一条来自场景步骤的用户消息，
    自主决定：写/读/更新记忆、执行动作、产出文件，并全部通过 ctx.emit 记录为证据。
    """

    name = "base"

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return "<Agent {}>".format(self.name)