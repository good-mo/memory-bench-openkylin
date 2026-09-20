"""OS 侧审计证据采集层。

把真实操作系统侧的行为证据（systemd journal、auditd、进程快照、
工作区文件变更、离线审计流）统一翻译成评测框架的 EvidenceEvent，
使「智能体自证」（AGENT 主动 emit）与「OS 客观证据」（HARNESS 采集）
可以交叉印证，作为九条一致性规则的独立证据来源。
"""

from memory_bench.audit.collector import AuditCollector
from memory_bench.audit.records import AuditRecord
from memory_bench.audit.sources import (
    AuditdSource,
    AuditSource,
    FileSystemDiffSource,
    JournalSource,
    NdjsonReplaySource,
    ProcessSource,
)

__all__ = [
    "AuditCollector",
    "AuditRecord",
    "AuditSource",
    "AuditdSource",
    "FileSystemDiffSource",
    "JournalSource",
    "NdjsonReplaySource",
    "ProcessSource",
]