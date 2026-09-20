"""智能体协议：Agent 抽象基类 + AgentContext 上下文。"""

from dataclasses import dataclass, field
import json
import os
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
        state_dir: 跨会话持久化目录（智能体可把记忆快照落盘/恢复）。
    """

    store: EvidenceStore
    workspace: Path
    session: str
    seed: int = 0
    state_dir: Optional[Path] = None

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

    跨会话持久化：子类可覆写 save_state / load_state，把记忆快照落盘并在新会话恢复，
    用于评测「跨会话持久化」维度（M3）。基类实现缺省为 JSON 序列化 self.memory。
    """

    name = "base"

    def act(self, ctx: AgentContext, user_message: str, step_name: str) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------ 跨会话持久化

    def memory_snapshot(self) -> Dict[str, Any]:
        """返回可序列化的记忆快照（默认是内存里的全部记忆）。"""
        return {"memory": dict(getattr(self, "memory", {}) or {})}

    def restore_memory(self, snapshot: Dict[str, Any]) -> None:
        """从快照恢复记忆（默认覆盖 self.memory）。"""
        memory = (snapshot or {}).get("memory")
        if isinstance(memory, dict):
            self.memory = dict(memory)

    def save_state(self, ctx: AgentContext, session: str) -> str:
        """把当前记忆快照写入 state_dir/session-<session>.json，返回路径。"""
        if not ctx.state_dir:
            return ""
        path = os.path.join(str(ctx.state_dir), "session-{}.json".format(session))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.memory_snapshot(), fh, ensure_ascii=False, indent=2)
        return path

    def load_state(self, ctx: AgentContext, session: str) -> bool:
        """从 state_dir/session-<session>.json 恢复记忆；文件不存在返回 False。"""
        if not ctx.state_dir:
            return False
        path = os.path.join(str(ctx.state_dir), "session-{}.json".format(session))
        if not os.path.isfile(path):
            return False
        with open(path, "r", encoding="utf-8") as fh:
            self.restore_memory(json.load(fh))
        return True

    def __repr__(self) -> str:  # pragma: no cover
        return "<Agent {}>".format(self.name)