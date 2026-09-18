"""EvidenceStore：NDJSON 追加式权威存储 + sqlite3 查询索引。

设计要点：
- NDJSON 文件是证据的权威来源，每行一条 EvidenceEvent。
- sqlite 仅作为查询加速索引；数据库不存在时自动建表，不报错。
- ev_id 顺序递增，跨运行也连续（从文件中已有行数继续）。
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Union

from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source


class EvidenceStore:
    """证据存储：NDJSON 追加写入 + sqlite 索引 + 条件查询。"""

    def __init__(self, path: str = "out/evidence.ndjson"):
        self.path = path
        self.dirname = os.path.dirname(os.path.abspath(path))
        self.db_path = os.path.join(self.dirname, "evidence.db")
        os.makedirs(self.dirname, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ 内部辅助

    def _init_db(self) -> None:
        """确保 sqlite 索引表存在（不存在则自动建表）。"""
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " ev_id TEXT PRIMARY KEY,"
                " type TEXT, ts TEXT, session TEXT, task TEXT,"
                " source TEXT, content TEXT, metadata TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        return conn

    def _count_ndjson_lines(self) -> int:
        """统计 NDJSON 文件已有的数据行数（用于跨运行连续分配 ev_id）。"""
        if not os.path.exists(self.path):
            return 0
        count = 0
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    # ------------------------------------------------------------------ 写入

    def append(self, event: Union[EvidenceEvent, Dict[str, Any]]) -> EvidenceEvent:
        """追加一条证据：分配（或保留）ev_id、写 NDJSON、同步 sqlite 索引。

        - 若传入事件的 ev_id 为空/None，则自动按文件行数顺序分配 "ev-%04d"。
        - 返回写入后的 EvidenceEvent（带最终 ev_id）。
        """
        if isinstance(event, dict):
            event.setdefault("ev_id", "")
            event = EvidenceEvent.from_dict(event)
        if not getattr(event, "ev_id", None):
            next_id = self._count_ndjson_lines() + 1
            event.ev_id = "ev-{:04d}".format(next_id)
        # 校验落盘内容合法性（type / ts / ev_id）
        event.to_dict()

        line = event.to_line()
        os.makedirs(self.dirname, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        self._index_db(event)
        return event

    def _index_db(self, event: EvidenceEvent) -> None:
        """把一条事件写入 sqlite 索引。"""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO events"
                " (ev_id, type, ts, session, task, source, content, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ev_id,
                    event.type.value,
                    event.ts,
                    event.session,
                    event.task,
                    event.source.value,
                    json.dumps(event.content, ensure_ascii=False),
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ 查询

    def query(
        self,
        type: Optional[str] = None,
        session: Optional[str] = None,
        task: Optional[str] = None,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[EvidenceEvent]:
        """按条件过滤查询，结果按 ts 升序排序。

        type / source 可传入枚举或字符串值；task 精确匹配。
        """
        sql = "SELECT * FROM events WHERE 1=1"
        params: List[Any] = []
        if type is not None:
            type_val = type.value if isinstance(type, EvidenceType) else str(type)
            sql += " AND type = ?"
            params.append(type_val)
        if session is not None:
            sql += " AND session = ?"
            params.append(session)
        if task is not None:
            sql += " AND task = ?"
            params.append(task)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        if source is not None:
            source_val = source.value if isinstance(source, Source) else str(source)
            sql += " AND source = ?"
            params.append(source_val)
        sql += " ORDER BY ts ASC"

        conn = self._connect()
        rows = []
        try:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
        finally:
            conn.close()
        return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EvidenceEvent:
        cols = ["ev_id", "type", "ts", "session", "task", "source", "content", "metadata"]
        item = {}
        for idx, col in enumerate(cols):
            item[col] = row[idx]
        event = EvidenceEvent.from_dict(item)
        event.content = json.loads(item["content"]) if item["content"] else {}
        event.metadata = json.loads(item["metadata"]) if item["metadata"] else {}
        return event

    def by_type(self, t: Union[str, EvidenceType]) -> List[EvidenceEvent]:
        """按证据类型便捷查询。"""
        return self.query(type=t)

    def count(self) -> int:
        """返回当前证据总数（以 NDJSON 文件为准）。"""
        return self._count_ndjson_lines()

    def export_ndjson(self, path: str) -> str:
        """把全部证据复制为新的 NDJSON 文件，返回目标路径。"""
        dest = os.path.abspath(path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        content = ""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                content = fh.read()
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
        return dest