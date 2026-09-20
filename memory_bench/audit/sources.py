"""OS 审计数据源：从不同系统通道采集原始行为记录。

设计原则：
- 每个源实现 collect() -> List[AuditRecord]，失败时优雅降级返回空列表，
  不中断评测（metadata 说明原因）。
- 命令不存在 / 无权限 / 非 Linux 环境下仍然可用：直接返回空，评测继续。
- 所有采集只读，不修改系统状态。

可用后端：
- JournalSource      systemd journal（journalctl -o json）
- AuditdSource       auditd（ausearch / audit.log）
- ProcessSource      进程快照（ps）
- FileSystemDiffSource  工作区文件变更（两次快照 diff）
- NdjsonReplaySource 离线审计流回放（NDJSON，测试/演示/无权限环境）
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from memory_bench.audit.records import AuditRecord
from memory_bench.harness.sandbox import list_files


def _run(args: List[str], timeout: float = 10.0, **kw: Any) -> Optional[str]:
    """执行命令并返回 stdout；命令缺失/失败返回 None（不抛异常）。"""
    binary = shutil.which(args[0])
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary] + list(args[1:]),
            capture_output=True,
            text=True,
            timeout=timeout,
            **kw,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


class AuditSource:
    """审计数据源抽象基类。"""

    name = "base"

    def __init__(self, backend: Optional[str] = None):
        self.backend = backend or self.name

    def collect(self) -> List[AuditRecord]:
        raise NotImplementedError

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def _record(
        kind: str,
        command: str = "",
        path: str = "",
        content: str = "",
        detail: str = "",
        backend: str = "",
        **meta: Any,
    ) -> AuditRecord:
        return AuditRecord(
            kind=kind,
            command=command,
            path=path,
            content=content,
            detail=detail,
            backend=backend,
            metadata=dict(meta),
        )


class JournalSource(AuditSource):
    """systemd journal 审计源：采集最近时间窗内执行的命令与日志。

    通过 ``journalctl -o json --since <since>`` 读取，解析每条记录的
    _COMM（可执行文件名）与 MESSAGE，翻译为 ACTION / DIALOGUE 证据。
    时间窗为空时采集最近 N 条记录（尽快返回，避免 hang）。
    """

    name = "journal"

    def __init__(
        self,
        since: Optional[str] = None,
        lines: int = 500,
        journalctl: str = "journalctl",
        command_keywords: Optional[List[str]] = None,
    ):
        super().__init__("journal")
        self.since = since
        self.lines = int(lines)
        self.journalctl = journalctl
        self.command_keywords = command_keywords or [
            "ssh", "scp", "rsync", "cp", "mv", "rm", "mkdir", "touch",
            "systemctl", "apt", "dnf", "yum", "dpkg", "curl", "wget",
            "tar", "python", "bash", "sh",
        ]

    def collect(self) -> List[AuditRecord]:
        args = [self.journalctl, "-o", "json", "--no-pager"]
        if self.since:
            args += ["--since", self.since]
        else:
            args += ["-n", str(self.lines)]
        out = _run(args)
        if not out:
            return [
                self._record(
                    "dialogue",
                    content="journalctl 不可用或未返回数据，跳过 systemd journal 采集",
                    backend=self.backend,
                    error="journal_unavailable",
                )
            ]
        return [self._translate(line) for line in out.splitlines() if line.strip()]

    def _translate(self, line: str) -> AuditRecord:
        try:
            entry = json.loads(line)
        except ValueError:
            return self._record("dialogue", content=line[:256], backend=self.backend)
        message = str(entry.get("MESSAGE") or entry.get("__MESSAGE") or "").strip()
        comm = str(entry.get("_COMM") or entry.get("_EXE") or "").strip()
        unit = str(entry.get("_SYSTEMD_UNIT") or "").strip()
        if comm:
            command = comm
            if unit:
                command = "{} [{}]".format(comm, unit)
            return self._record(
                "action",
                command=command,
                content=message[:512],
                detail="journal: {}".format(comm),
                backend=self.backend,
                unit=unit,
            )
        if message:
            return self._record(
                "dialogue", content=message[:512], backend=self.backend, unit=unit
            )
        return self._record(
            "dialogue", content=line[:256], backend=self.backend
        )


class AuditdSource(AuditSource):
    """auditd 审计源：解析内核审计日志中的 EXECVE / PATH 记录。

    优先使用 ``ausearch -m EXECVE -i``；ausearch 缺失时回退读取
    /var/log/audit/audit.log 原文，尽力提取 execve 命令。
    EXECVE 记录翻译为 ACTION，PATH 记录中的文件路径可为 ARTIFACT。
    """

    name = "auditd"

    def __init__(
        self,
        log_path: str = "/var/log/audit/audit.log",
        ausearch_cmd: str = "ausearch",
        hostname_filter: Optional[str] = None,
    ):
        super().__init__("auditd")
        self.log_path = log_path
        self.ausearch_cmd = ausearch_cmd
        self.hostname_filter = hostname_filter

    def collect(self) -> List[AuditRecord]:
        records: List[AuditRecord] = []
        out = _run([self.ausearch_cmd, "-m", "EXECVE", "-i", "--format", "text"])
        if out:
            records.extend(self._parse_ausearch(out))
        if not records and os.path.isfile(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="ignore") as fh:
                    records.extend(self._parse_raw(fh.read()))
            except OSError:
                pass
        if not records:
            return [
                self._record(
                    "dialogue",
                    content="auditd 不可用且未找到审计日志，跳过 auditd 采集",
                    backend=self.backend,
                    error="auditd_unavailable",
                )
            ]
        return records

    @staticmethod
    def _parse_ausearch(text: str) -> List[AuditRecord]:
        records: List[AuditRecord] = []
        current: List[str] = []
        for raw in text.splitlines():
            if raw.startswith("----"):
                if current:
                    parsed = AuditdSource._extract(current)
                    if parsed is not None:
                        records.append(parsed)
                current = []
            elif raw.strip():
                current.append(raw)
        if current:
            parsed = AuditdSource._extract(current)
            if parsed is not None:
                records.append(parsed)
        return records

    @staticmethod
    def _parse_raw(text: str) -> List[AuditRecord]:
        records: List[AuditRecord] = []
        for raw in text.splitlines():
            if "EXECVE" in raw or "execve" in raw:
                cmd = AuditdSource._extract_command(raw)
                if cmd:
                    records.append(
                        AuditRecord(
                            kind="action",
                            command=cmd,
                            detail="execve from audit.log",
                            backend="auditd",
                        )
                    )
        return records

    @staticmethod
    def _extract(lines: List[str]) -> Optional[AuditRecord]:
        """从一组 ausearch 输出行中提取一条 ACTION 记录。"""
        command = ""
        for line in lines:
            if "EXECVE" in line:
                command = AuditdSource._extract_command(line)
                if command:
                    break
        if command:
            return AuditRecord(
                kind="action",
                command=command,
                detail="execve from ausearch",
                backend="auditd",
            )
        return None

    @staticmethod
    def _extract_command(line: str) -> str:
        """从 EXECVE 行提取被执行的命令（含参数，近似还原）。"""
        parts: List[str] = []
        for token in line.split():
            match = re.match(r"^a(\d+)=(.*)$", token)
            if not match:
                continue
            arg_index = int(match.group(1))
            value = match.group(2).strip('"')
            parts.append((arg_index, value))
        parts.sort(key=lambda item: item[0])
        return " ".join(value for _, value in parts)


class ProcessSource(AuditSource):
    """进程快照审计源：采集当前系统进程的命令/参数。

    仅采集与工作区/已知命令相关的进程（避免把整机无关进程引入证据）。
    翻译为 ACTION 证据。
    """

    name = "process"

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        max_records: int = 100,
        exclude_self: bool = True,
    ):
        super().__init__("process")
        self.keywords = keywords or [
            "ssh", "scp", "rsync", "deploy", "tar", "curl", "wget",
            "kylin", "openkylin", "agent",
        ]
        self.max_records = int(max_records)
        self.exclude_self = exclude_self

    def collect(self) -> List[AuditRecord]:
        out = _run(["ps", "-eo", "pid,comm,args", "--no-headers"])
        if not out:
            return [
                self._record(
                    "dialogue",
                    content="ps 不可用，跳过进程快照采集",
                    backend=self.backend,
                    error="ps_unavailable",
                )
            ]
        records: List[AuditRecord] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid, comm, args = line.split(None, 2)
            except ValueError:
                continue
            low = "{} {}".format(comm, args).lower()
            if not any(kw in low for kw in self.keywords):
                continue
            if self.exclude_self and "memory_bench" in low:
                continue
            records.append(
                self._record(
                    "action",
                    command="{} {}".format(comm, args).strip()[:512] or str(comm),
                    detail="process pid={}".format(pid),
                    backend=self.backend,
                    pid=pid,
                )
            )
            if len(records) >= self.max_records:
                break
        return records


class FileSystemDiffSource(AuditSource):
    """工作区文件变更审计源：两次快照间的差异即智能体产生的产物。

    需显式调用 begin()（场景开始前）与 collect()（场景结束后）：
    新增/修改的文件翻译为 ARTIFACT，删除翻译为 ACTION（remove）。
    未调用 begin 时 collect 退化为「当前文件清单」，不产生变更判定。
    """

    name = "filesystem"

    def __init__(self, workspace: str):
        super().__init__("filesystem")
        self.workspace = os.path.abspath(workspace)
        self._snapshot: Optional[List[str]] = None

    def begin(self) -> None:
        """记录开始快照。"""
        os.makedirs(self.workspace, exist_ok=True)
        self._snapshot = list_files(self.workspace)

    def collect(self) -> List[AuditRecord]:
        if not os.path.isdir(self.workspace):
            return []
        current = list_files(self.workspace)
        if self._snapshot is None:
            self._snapshot = current
            return []
        added = [f for f in current if f not in self._snapshot]
        deleted = [f for f in self._snapshot if f not in current]
        records: List[AuditRecord] = []
        for rel in added:
            full = os.path.join(self.workspace, rel)
            content = ""
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read(4096)
            except OSError:
                pass
            records.append(
                self._record(
                    "artifact",
                    path=rel,
                    content=content,
                    detail="workspace file added",
                    backend=self.backend,
                )
            )
        for rel in deleted:
            records.append(
                self._record(
                    "action",
                    command="rm {}".format(shlex.quote(rel)),
                    path=rel,
                    detail="workspace file removed",
                    backend=self.backend,
                )
            )
        self._snapshot = current
        return records


class NdjsonReplaySource(AuditSource):
    """离线审计流回放源：从 NDJSON 文件读取预采集的审计记录。

    用于测试、演示无权限环境，以及「评测重放」：把真实系统上预录的
    审计流翻译成证据，无需目标机器具备 journalctl/auditd。
    每行 JSON 为 AuditRecord.to_dict() 格式。
    """

    name = "replay"

    def __init__(self, path: str):
        super().__init__("replay")
        self.path = path

    def collect(self) -> List[AuditRecord]:
        if not os.path.isfile(self.path):
            return [
                self._record(
                    "dialogue",
                    content="审计流文件不存在：{}".format(self.path),
                    backend=self.backend,
                    error="replay_file_missing",
                    path=self.path,
                )
            ]
        records: List[AuditRecord] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    record = AuditRecord.from_dict(data)
                    record.backend = record.backend or "replay"
                    records.append(record)
        except OSError:
            return []
        return records