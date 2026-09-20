"""场景编排：按 Scenario 步骤驱动智能体产生证据，并汇总一致性检查与报告。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from memory_bench.evidence.consistency import CheckResult, run_all
from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source, now_utc
from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.agent import Agent, AgentContext
from memory_bench.report.generator import build_report
from memory_bench.scenarios.types import Scenario, StepType


@dataclass
class RunResult:
    """一次场景编排的运行结果。"""

    scenario_id: str
    store_path: str
    checks: List[CheckResult] = field(default_factory=list)
    report_paths: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "store_path": self.store_path,
            "report_paths": self.report_paths,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


class Orchestrator:
    """场景编排器：注入 → 演化（干扰/更新）→ 探针 → 固化。"""

    def __init__(
        self,
        scenario: Scenario,
        agent: Agent,
        store: EvidenceStore,
        workspace: Path,
        seed: int = 0,
    ):
        self.scenario = scenario
        self.agent = agent
        self.store = store
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.seed = seed

    def run(self) -> RunResult:
        """按步骤执行场景并固化证据与报告。

        支持跨会话场景：若有任意 step 显式指定 session，则按 session 分组，
        会话切换时把上一会话的 agent 记忆快照落盘（state_dir）、载入新会话状态，
        从而评测「跨会话持久化」维度。所有步骤的 session 未指定时退化为单会话。
        """
        base_session = "session-{sid}-{seed}".format(
            sid=self.scenario.id, seed=self.seed
        )
        state_dir = Path(self.store.path).parent / "states"
        self.agent.memory = dict(
            getattr(self.agent, "memory", {}) or {}
        )

        has_sessions = any(
            step.session for step in self.scenario.steps if step.session
        )
        session = base_session
        ctx = AgentContext(
            store=self.store,
            workspace=self.workspace,
            session=session,
            seed=self.seed,
            state_dir=state_dir,
        )

        self._emit(
            ctx,
            EvidenceType.CHECKPOINT,
            {"phase": "scenario_start"},
            source=Source.HARNESS,
        )

        for step in self.scenario.steps:
            next_session = step.session or session
            if has_sessions and next_session != session:
                self.agent.save_state(ctx, session)
                session = next_session
                ctx = AgentContext(
                    store=self.store,
                    workspace=self.workspace,
                    session=session,
                    seed=self.seed,
                    state_dir=state_dir,
                )
                self.agent.load_state(ctx, session)
                self._emit(
                    ctx,
                    EvidenceType.CHECKPOINT,
                    {"phase": "session_start", "session": session},
                    source=Source.HARNESS,
                )

            self._emit(
                ctx,
                EvidenceType.CHECKPOINT,
                {"phase": "step", "step": step.name, "type": step.type.value},
                source=Source.HARNESS,
            )
            if step.type == StepType.INJECT:
                user_message = self._build_inject_message(step)
            elif step.type == StepType.UPDATE:
                user_message = self._build_update_message(step)
            elif step.type == StepType.PROBE:
                user_message = step.description
            elif step.type == StepType.CHECKPOINT:
                continue
            else:  # DISTRACT
                user_message = step.description

            if step.type in (StepType.INJECT, StepType.UPDATE, StepType.PROBE, StepType.DISTRACT):
                self._emit(
                    ctx,
                    EvidenceType.DIALOGUE,
                    {"text": user_message},
                    task=step.name,
                    source=Source.USER,
                )
                self.agent.act(ctx, user_message, step.name)

        if has_sessions:
            self.agent.save_state(ctx, session)

        self._emit(
            ctx,
            EvidenceType.CHECKPOINT,
            {"phase": "scenario_end"},
            source=Source.HARNESS,
        )

        checks = run_all(self.store, self.scenario)

        # 报告输出到 NDJSON 同级目录
        outdir = str(Path(self.store.path).parent)
        report_paths = build_report(
            store=self.store, checks=checks, scenario=self.scenario, outdir=outdir
        )

        all_events = self.store.query()
        ev_by_type: Dict[str, int] = {}
        for ev in all_events:
            ev_by_type[ev.type.value] = ev_by_type.get(ev.type.value, 0) + 1
        summary = {
            "ev_total": self.store.count(),
            "ev_by_type": ev_by_type,
            "checks_pass": sum(1 for c in checks if c.status.value == "PASS"),
            "checks_fail": sum(1 for c in checks if c.status.value == "FAIL"),
            "checks_warn": sum(1 for c in checks if c.status.value == "WARN"),
            "checks_na": sum(1 for c in checks if c.status.value == "N/A"),
        }

        return RunResult(
            scenario_id=self.scenario.id,
            store_path=self.store.path,
            checks=checks,
            report_paths=report_paths,
            summary=summary,
        )

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def _emit(
        ctx: AgentContext,
        type: EvidenceType,
        content: Dict[str, Any],
        source: Source = Source.HARNESS,
        task: str = "",
    ) -> EvidenceEvent:
        return ctx.store.append(
            EvidenceEvent(
                ev_id="",
                type=type,
                ts=now_utc(),
                session=ctx.session,
                task=task,
                source=source,
                content=content,
            )
        )

    @staticmethod
    def _build_inject_message(step) -> str:
        """把注入事实组装成 "请记住：k1=v1；k2=v2" 消息。"""
        parts = []
        for fact in step.facts:
            for key, value in fact.fields.items():
                parts.append("{k}={v}".format(k=key, v=value))
        return "请记住：" + "；".join(parts) + "（请长期保存这些信息）"

    @staticmethod
    def _build_update_message(step) -> str:
        """把更新事实组装成 "请把 k 改为 k=v" 消息（key 后面紧邻 = 便于脚本化 agent 解析）。

        若步骤带 rollback 标志，则改述为「回滚到最初值」，触发智能体的回滚逻辑。
        """
        parts = []
        for fact in step.facts:
            for key, value in fact.fields.items():
                parts.append("{k} 改为 {k}={v}".format(k=key, v=value))
        prefix = "请回滚配置：" if getattr(step, "rollback", False) else "请更新配置："
        if getattr(step, "rollback", False):
            return prefix + "；".join(parts) + "（把 key 恢复为最初权威值，务必记录最新值）"
        return prefix + "；".join(parts) + "（重要，务必记录最新值）"