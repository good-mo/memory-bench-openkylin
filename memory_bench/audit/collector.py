"""审计证据门面：把多个 OS 审计源聚合翻译成证据并写入 EvidenceStore。"""

from typing import List, Optional

from memory_bench.audit.records import AuditRecord
from memory_bench.audit.sources import (
    AuditSource,
    FileSystemDiffSource,
    NdjsonReplaySource,
)
from memory_bench.evidence.store import EvidenceStore


class AuditCollector:
    """OS 审计证据采集器。

    持有证据存储与一个或多个审计源；collect(session, task) 触发各源采集，
    并把每条 AuditRecord 翻译写入 EvidenceStore（source=HARNESS），
    供九条一致性规则与其他证据交叉印证。

    典型用法（评测编排阶段）：
        collector = AuditCollector(store)
        collector.add_source(JournalSource(since="5 minutes ago"))
        collector.add_source(ProcessSource())
        fs = FileSystemDiffSource(workspace); fs.begin()
        ... 运行智能体 ...
        collector.collect(session="s1", task="scenario_end")
    """

    def __init__(
        self,
        store: EvidenceStore,
        sources: Optional[List[AuditSource]] = None,
    ):
        self.store = store
        self.sources: List[AuditSource] = list(sources or [])
        self.last_collected: int = 0

    # ------------------------------------------------------------------ 源管理

    def add_source(self, source: AuditSource) -> "AuditCollector":
        self.sources.append(source)
        return self

    def register(
        self,
        store: EvidenceStore,
        sources: Optional[List[AuditSource]] = None,
    ) -> "AuditCollector":
        self.store = store
        if sources is not None:
            self.sources = list(sources)
        return self

    @classmethod
    def from_workspace(
        cls,
        store: EvidenceStore,
        workspace: str,
        replay: Optional[str] = None,
        journal_since: Optional[str] = None,
    ) -> "AuditCollector":
        """便捷构造：journal + process + 工作区文件差异（+ 可选离线回放）。"""
        collector = cls(store)
        from memory_bench.audit.sources import JournalSource, ProcessSource

        if journal_since:
            collector.add_source(JournalSource(since=journal_since))
        collector.add_source(ProcessSource())
        collector.add_source(FileSystemDiffSource(workspace))
        if replay:
            collector.add_source(NdjsonReplaySource(replay))
        return collector

    # ------------------------------------------------------------------ 采集

    def collect(
        self,
        session: str = "",
        task: str = "",
        skip_fs_diff: bool = False,
    ) -> int:
        """触发全部源采集并写入证据，返回写入条数。

        session/task 会写入每条证据；FileSystemDiffSource 若有 begin()
        已先行调用（首次 collect 返回空快照）。
        """
        written = 0
        for source in self.sources:
            if isinstance(source, FileSystemDiffSource) and skip_fs_diff:
                continue
            try:
                records = source.collect()
            except Exception:  # noqa: BLE001 - 任何源异常不得中断评测
                records = [] if not hasattr(source, "error") else []
            if not records:
                continue
            for record in records:
                if isinstance(record, AuditRecord):
                    self.store.append(record.to_event(session=session, task=task))
                    written += 1
        self.last_collected = written
        return written