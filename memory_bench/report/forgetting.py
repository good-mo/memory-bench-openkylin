"""遗忘曲线统计（M4）：按时间窗口统计记忆键被行为引用的频率分布。

遗忘曲线描述「记忆的引用强度随时间衰减」：若智能体在某一键写入后越来越
少引用它（或从不引用），说明该记忆没有持续进入行为回路。本模块把一个
场景运行中某个键的全部 AGENT 行为引用按时间窗（按证据事件序号分桶）聚合，
输出每个时间窗内的引用次数，供报告生成器渲染为可视化表格/条形图。
"""

import json
import re
from typing import Any, Dict, List, NamedTuple, Optional

from memory_bench.evidence.model import EvidenceEvent, EvidenceType, Source
from memory_bench.evidence.store import EvidenceStore
from memory_bench.scenarios.types import Scenario


class ForgettingWindow(NamedTuple):
    """一个时间窗的引用统计。"""

    bucket: int          # 分桶序号（0 起）
    references: int      # 该桶内命中次数
    session: str         # 分桶边界所属会话（用于跨会话观察）


def _scenarios_keys(scenario: Optional[Scenario]) -> List[str]:
    """返回场景定义里出现过的记忆键列表（facts + expected，按出现顺序去重）。"""
    keys: List[str] = []
    if scenario is None:
        return keys
    for step in scenario.steps:
        for fact in step.facts:
            for k in fact.fields:
                if k not in keys:
                    keys.append(k)
        for k in step.expected:
            if k not in keys:
                keys.append(k)
    return keys


def _mention_count(text: str, values: List[str]) -> int:
    """统计 text 中出现任意 values（词边界）的次数。"""
    count = 0
    for value in values:
        if not value:
            continue
        pattern = re.compile(r"(?<![0-9A-Za-z_])" + re.escape(value) + r"(?![0-9A-Za-z_])")
        count += len(pattern.findall(text))
    return count


def _key_value_pairs(scenario: Optional[Scenario]) -> Dict[str, List[str]]:
    """收集场景中每个记忆键出现的全部值（facts + expected）。"""
    by_key: Dict[str, List[str]] = {}
    if scenario is None:
        return by_key
    for step in scenario.steps:
        for fact in step.facts:
            for key, value in fact.fields.items():
                by_key.setdefault(key, []).append(str(value))
        for key, value in step.expected.items():
            by_key.setdefault(key, []).append(str(value))
    return by_key


def compute_forgetting_curve(
    store: EvidenceStore, scenario: Optional[Scenario] = None, buckets: int = 8
) -> List[Dict[str, Any]]:
    """计算遗忘曲线数据。

    返回每个记忆键的一条遗忘曲线：
        {
          "key": "server_ip",
          "values": ["192.168.1.100", ...],
          "buckets": [{"bucket": 0, "references": n, "session": "..."}, ...],
          "total_references": int,
          "first_half_refs": int,   # 前半时间窗引用数
          "second_half_refs": int,  # 后半时间窗引用数
          "trend": "decay|stable|rise|none",
        }
    """
    keys = _scenarios_keys(scenario)
    if not keys:
        return []

    # 每个键对应的场景里出现过的所有值（用于匹配行为文本）
    key_values = _key_value_pairs(scenario)
    for key, vals in key_values.items():
        seen: List[str] = []
        for v in vals:
            if v not in seen:
                seen.append(v)
        key_values[key] = seen

    # 全部 AGENT 行为证据（对话/行动/产物），按 ts 排序
    events: List[EvidenceEvent] = []
    for etype in (
        EvidenceType.DIALOGUE,
        EvidenceType.ACTION,
        EvidenceType.ARTIFACT,
    ):
        events.extend(store.query(type=etype, source=Source.AGENT))
    events.sort(key=lambda e: e.ts)
    total = len(events)
    if total == 0:
        bucket_count = 0
    else:
        bucket_count = min(buckets, total)

    curves = []
    for key in keys:
        values = key_values.get(key, [])
        if not values:
            continue
        bucket_refs = [0] * bucket_count
        bucket_sessions: List[str] = [""] * bucket_count
        for idx, ev in enumerate(events):
            text = _event_text(ev)
            hit = _mention_count(text, values)
            if hit == 0:
                continue
            if bucket_count == 0:
                break
            bucket = min(idx * bucket_count // total, bucket_count - 1)
            bucket_refs[bucket] += hit
            bucket_sessions[bucket] = ev.session
        total_refs = sum(bucket_refs)
        if bucket_count >= 2:
            half = bucket_count // 2
            first = sum(bucket_refs[:half])
            second = sum(bucket_refs[half:])
            if second > first:
                trend = "rise"
            elif first > second:
                trend = "decay"
            else:
                trend = "stable"
        else:
            trend = "none"
        curves.append(
            {
                "key": key,
                "values": values,
                "buckets": [
                    {
                        "bucket": i,
                        "references": n,
                        "session": bucket_sessions[i],
                    }
                    for i, n in enumerate(bucket_refs)
                ],
                "total_references": total_refs,
                "first_half_refs": sum(bucket_refs[: bucket_count // 2]),
                "second_half_refs": sum(bucket_refs[bucket_count // 2 :]),
                "trend": trend,
            }
        )
    return curves


def _event_text(ev: EvidenceEvent) -> str:
    """事件正文 JSON 文本（与 consistency 模块保持相同语义）。"""
    return json.dumps(ev.content, ensure_ascii=False)