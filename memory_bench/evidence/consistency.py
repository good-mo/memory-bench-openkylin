"""跨证据一致性校验：说—做、记忆—行为、时间—更新三条规则。

核心思想是「跨证据一致性」：不单独信任某一条事件，而是让对话、记忆操作、
行动轨迹、文件产物互相印证，任一方向对不上即产生 FAIL 证据链。
"""

import enum
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from memory_bench.evidence.model import EvidenceEvent, EvidenceType, MemoryOp, Source
from memory_bench.evidence.store import EvidenceStore
from memory_bench.scenarios.types import Scenario, StepType


class Status(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NA = "N/A"


@dataclass
class CheckResult:
    """一条一致性检查结果。"""

    status: Status
    rule: str
    message: str
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, str]:
        return {
            "status": self.status.value,
            "rule": self.rule,
            "message": self.message,
            "evidence_ids": self.evidence_ids,
        }


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

def _event_text(event: EvidenceEvent) -> str:
    """把一条事件的正文转成便于全文匹配的文本（覆盖 content 全部字段）。"""
    return json.dumps(event.content, ensure_ascii=False)


def _normalize_value(value: str) -> str:
    """值语义归一（保守）：去首尾空白、纯数字去前导零、路径剥尾部斜杠。

    用于缓解「同一事实、不同写法」导致的语义偏移误报，例如：
      - 端口 "0022" 与 "22"；
      - 路径 "/home/u/app/" 与 "/home/u/app"。
    不做大小写归一（Linux 路径大小写敏感），不做用户主目录展开
    （`~` 与 `/home/<user>` 的等价需上下文知识，留作已知边界）。
    """
    v = value.strip()
    if not v:
        return v
    if re.fullmatch(r"0*\d+", v) and int(v) > 0:
        return str(int(v))
    return v.rstrip("/")


def _mentions(text: str, value: str) -> bool:
    """判断文本里是否「引用了」value（词边界 + 轻量语义归一）。

    对值 v 构造 re.escape(v)，要求其前后都不是数字/字母/下划线，
    因此：
      - "22" 不会误命中 "2222"（后随数字）；
      - "192.168.1.10" 不会误命中 "192.168.1.100"（前后边界阻断）；
    text 与 value 均先做轻量归一（零填充端口、尾部斜杠），缓解同义改写误报。
    """
    if not value:
        return False
    text_candidates = [text, _normalize_value(text)]
    value_candidates = [value, _normalize_value(value)]
    for tv in text_candidates:
        if not tv:
            continue
        for vv in value_candidates:
            if not vv:
                continue
            pattern = re.compile(
                r"(?<![0-9A-Za-z_])" + re.escape(vv) + r"(?![0-9A-Za-z_])"
            )
            if pattern.search(tv) is not None:
                return True
    return False


def _normalize_path(p: str) -> str:
    """规范化路径：剥离开头的 ~/~ 前缀，统一后续比较。"""
    return p.lstrip("~")


def _dialogue_text(event: EvidenceEvent) -> str:
    """从 DIALOGUE 事件的 content 中提取对话文本。"""
    for key in ("text", "message", "reply", "content"):
        val = event.content.get(key)
        if isinstance(val, str) and val:
            return val
    return _event_text(event)


def _memory_pair_events(store: EvidenceStore) -> List[EvidenceEvent]:
    """取全部 MEMORY.WRITE / MEMORY.UPDATE 事件，按 ts 排序。"""
    events = store.query(type=EvidenceType.MEMORY, source=Source.AGENT)
    pairs = []
    for ev in events:
        op = ev.content.get("op")
        if op not in (MemoryOp.WRITE.value, MemoryOp.UPDATE.value):
            continue
        pairs.append(ev)
    return pairs


def _memory_changes(
    store: EvidenceStore,
) -> List[Tuple[str, str, str, str, str]]:
    """提取记忆值变化链。

    返回 [(key, old_value, new_value, update_ts, update_ev_id)]，
    对同一 key 按时间排序后，相邻两次写入值不同即为一次变化。
    """
    by_key: Dict[str, List[EvidenceEvent]] = {}
    for ev in _memory_pair_events(store):
        key = ev.content.get("key")
        if not key:
            continue
        by_key.setdefault(str(key), []).append(ev)

    changes: List[Tuple[str, str, str, str, str]] = []
    for key, evs in by_key.items():
        evs.sort(key=lambda e: e.ts)
        prev_value: Optional[str] = None
        for ev in evs:
            value = ev.content.get("value")
            if value is None:
                continue
            value = str(value)
            if prev_value is not None and value != prev_value:
                changes.append((key, prev_value, value, ev.ts, ev.ev_id))
            prev_value = value
        # 首次写入也可有效，但变化链要求有新老值对比，故仅记录值不同的相邻写入
    return changes


def _behavior_events_after(store: EvidenceStore, ts: str) -> List[EvidenceEvent]:
    """取时间戳 >= ts 的「行为引用」证据：ACTION 与 ARTIFACT 事件。

    MEMORY 事件本身不作为行为引用计（它只描述记忆状态，不代表使用行为）。
    """
    result = []
    for etype in (EvidenceType.ACTION, EvidenceType.ARTIFACT):
        for ev in store.query(type=etype):
            if ev.ts >= ts:
                result.append(ev)
    result.sort(key=lambda e: e.ts)
    return result


# ---------------------------------------------------------------------------
# 规则一：说—做一致性（say-do）
# ---------------------------------------------------------------------------

# 正则：一次匹配任一动词声明，其后紧跟目标。
# 目标提取到下一分隔符为止（空格、中文/英文标点、括号、引号等），不吞入括号注释。
_SAY_DO_PATTERN = re.compile(
    r"(?:已|成功)?(备份|创建|删除|安装|复制|移动|写入|修改)[了]?\s*"
    r"([^\s，。；、（）()「」…—\"']+)"
)


def say_do_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """说—做一致性：AGENT 在对话中声明已做的操作，须能在行动轨迹/文件产物中找到证据。

    对每条提取到的声明路径 p（规范化后 np），检查是否存在 ACTION.path 或
    ARTIFACT.path（规范化后 npath）满足 np == npath、npath.startswith(np) 或
    np.startswith(npath) 三者之一；满足即 PASS，否则 FAIL。
    """
    results: List[CheckResult] = []
    dialogues = [
        ev
        for ev in store.query(type=EvidenceType.DIALOGUE)
        if ev.source == Source.AGENT
    ]
    actions = store.query(type=EvidenceType.ACTION) + store.query(
        type=EvidenceType.ARTIFACT
    )
    candidate_paths = [
        _normalize_path(str(ev.content.get("path")))
        for ev in actions
        if ev.content.get("path")
    ]
    found_any = False
    # 已提取到的待匹配声明路径清单（去重保序），供 FAIL 定位
    extracted_targets: List[str] = []

    for ev in dialogues:
        text = _dialogue_text(ev)
        for match in _SAY_DO_PATTERN.finditer(text):
            found_any = True
            verb = match.group(1)
            target = match.group(2)
            norm_target = _normalize_path(target)
            if norm_target not in extracted_targets:
                extracted_targets.append(norm_target)

            hit = False
            for npath in candidate_paths:
                if (
                    norm_target == npath
                    or npath.startswith(norm_target)
                    or norm_target.startswith(npath)
                ):
                    hit = True
                    break
            if hit:
                results.append(
                    CheckResult(
                        status=Status.PASS,
                        rule="say_do_check",
                        message="声明「已{} {}」找到对应行动/产物证据".format(verb, target),
                        evidence_ids=[ev.ev_id],
                    )
                )
            else:
                results.append(
                    CheckResult(
                        status=Status.FAIL,
                        rule="say_do_check",
                        message=(
                            "声明「已{} {}」在 ACTION/ARTIFACT 中找不到对应路径；"
                            "本轮提取到的待匹配路径：{}"
                        ).format(verb, target, "、" .join(extracted_targets)),
                        evidence_ids=[ev.ev_id],
                    )
                )
    if not found_any:
        results.append(
            CheckResult(
                status=Status.NA,
                rule="say_do_check",
                message="未观察到「已Xxx」形式的声明，说—做一致性无法验证（N/A）",
            )
        )
    return results


# ---------------------------------------------------------------------------
# 规则二：记忆—行为一致性（memory-behavior）
# ---------------------------------------------------------------------------

def memory_behavior_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """记忆—行为一致性：记忆被更新后，后续行为（ACTION/ARTIFACT）不得再引用旧值。"""
    results: List[CheckResult] = []
    changes = _memory_changes(store)
    for key, old_value, new_value, update_ts, update_ev_id in changes:
        later_actions = _behavior_events_after(store, update_ts)
        refs = []
        for act in later_actions:
            text = _event_text(act)
            if _mentions(text, old_value) or _mentions(text, new_value):
                refs.append((act, text))

        used_old = any(_mentions(text, old_value) for _, text in refs)
        used_new = any(_mentions(text, new_value) for _, text in refs)

        if used_old:
            # 精确定位引用了旧值的行为证据 id（ACTION/ARTIFACT）
            stale_ids = [act.ev_id for act, text in refs if _mentions(text, old_value)]
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="memory_behavior_check",
                    message=(
                        "记忆键 {} 已由 {} 更新为 {}，但更新后的行为仍引用旧值 {}"
                    ).format(key, old_value, new_value, old_value),
                    evidence_ids=[update_ev_id] + stale_ids,
                )
            )
        elif used_new:
            ids = [act.ev_id for act, _ in refs]
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="memory_behavior_check",
                    message="记忆键 {} 更新为 {} 后，后续行为正确引用了新值".format(
                        key, new_value
                    ),
                    evidence_ids=[update_ev_id] + ids,
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.NA,
                    rule="memory_behavior_check",
                    message="记忆键 {} 更新后未观察到任何行为引用，无法验证（N/A）".format(
                        new_value
                    ),
                    evidence_ids=[update_ev_id],
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则三：时间—更新一致性（time-update）
# ---------------------------------------------------------------------------

def time_update_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """时间—更新一致性：汇总统计更新后旧值/新值在行为（ACTION/ARTIFACT）中被引用的次数。"""
    changes = _memory_changes(store)
    if not changes:
        return [
            CheckResult(
                status=Status.NA,
                rule="time_update_check",
                message="场景不含记忆更新链，时间—更新一致性不适用（N/A）",
            )
        ]

    results: List[CheckResult] = []
    for key, old_value, new_value, update_ts, update_ev_id in changes:
        later_actions = _behavior_events_after(store, update_ts)
        stale_count = 0
        fresh_count = 0
        stale_ids = []
        fresh_ids = []
        for act in later_actions:
            text = _event_text(act)
            if _mentions(text, old_value):
                stale_count += 1
                stale_ids.append(act.ev_id)
            if _mentions(text, new_value):
                fresh_count += 1
                fresh_ids.append(act.ev_id)

        if stale_count == 0 and fresh_count > 0:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="time_update_check",
                    message="记忆键 {} 更新为 {} 后，未发现旧值残留（stale=0, fresh={}）".format(
                        key, new_value, fresh_count
                    ),
                    evidence_ids=[update_ev_id] + fresh_ids,
                )
            )
        elif stale_count == 0 and fresh_count == 0:
            results.append(
                CheckResult(
                    status=Status.NA,
                    rule="time_update_check",
                    message="记忆键 {} 更新后没有任何行为引用该值（stale=0, fresh=0，N/A）".format(
                        key
                    ),
                    evidence_ids=[update_ev_id],
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="time_update_check",
                    message=(
                        "记忆键 {} 更新后旧值 {} 仍出现 {} 次，新值 {} 出现 {} 次"
                        "（stale={}, fresh={}）"
                    ).format(key, old_value, stale_count, new_value, fresh_count,
                             stale_count, fresh_count),
                    evidence_ids=[update_ev_id] + stale_ids,
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则四：探针期望值校验（probe-expectation）
# ---------------------------------------------------------------------------

def _scenario_values_by_key(scenario: Scenario) -> Dict[str, set]:
    """收集场景中出现过的全部 key -> 值集合（INJECT/UPDATE facts + PROBE expected）。"""
    values: Dict[str, set] = {}
    for step in scenario.steps:
        for fact in step.facts:
            for key, value in fact.fields.items():
                values.setdefault(key, set()).add(value)
        for key, value in step.expected.items():
            values.setdefault(key, set()).add(value)
    return values


def _scenario_all_values(scenario: Scenario) -> set:
    """收集场景注入/期望过的全部值（全局干扰候选集）。"""
    all_values: set = set()
    for step in scenario.steps:
        for fact in step.facts:
            all_values.update(fact.fields.values())
        all_values.update(step.expected.values())
    return all_values


def _checkpoint_ts(store: EvidenceStore, step_name: str) -> Optional[str]:
    """定位某步骤开始时 HARNESS 写入的 CHECKPOINT 时间戳。"""
    for ev in store.query(type=EvidenceType.CHECKPOINT):
        content = ev.content or {}
        if content.get("phase") == "step" and content.get("step") == step_name:
            return ev.ts
    return None


def _agent_texts_after(
    store: EvidenceStore, ts: str
) -> List[str]:
    """取时间戳 >= ts 的 AGENT 事件正文文本（含对话/行动/产物）。"""
    return [
        _event_text(ev)
        for ev in store.query(source=Source.AGENT)
        if ev.ts >= ts
    ]


def probe_expectation_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """探针期望值校验：记忆调用 / 相近区分 / 任务复用。

    对每个带 expected 的 PROBE 步骤：
    - 期望值集合被引用 → PASS（正确调用记忆；即使顺带引用了其它附加信息也容忍）；
    - 期望值未被引用、却引用了场景中的其它值（旧值/相近干扰值）→ FAIL；
    - 完全未引用任何相关值 → WARN（没调用记忆）。
    """
    if scenario is None:
        return []
    all_values = _scenario_all_values(scenario)
    results: List[CheckResult] = []
    for step in scenario.steps:
        if step.type != StepType.PROBE or not step.expected:
            continue
        ts0 = _checkpoint_ts(store, step.name)
        if ts0 is None:
            continue
        texts = _agent_texts_after(store, ts0)
        expected_values = {str(v) for v in step.expected.values()}
        used_expected = any(
            _mentions(t, v) for t in texts for v in expected_values
        )
        distractors = sorted(all_values - expected_values)
        used_other = [v for v in distractors if any(_mentions(t, v) for t in texts)]
        if used_expected:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="probe_expectation_check",
                    message="探针任务「{}」正确调用了记忆（期望值 {} 已命中）".format(
                        step.name, "、".join(sorted(expected_values))
                    ),
                )
            )
        elif used_other:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="probe_expectation_check",
                    message="探针任务「{}」应使用期望值 {}，却引用了其它值：{}".format(
                        step.name,
                        "、".join(sorted(expected_values)),
                        "、".join(used_other),
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.WARN,
                    rule="probe_expectation_check",
                    message="探针任务「{}」未观察到对期望值 {} 的引用".format(
                        step.name, "、".join(sorted(expected_values))
                    ),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则五：临时信息边界校验（boundary）
# ---------------------------------------------------------------------------

_TMP_MARKERS = ("token", "password", "secret", "tmp_")


def _forbidden_values(scenario: Scenario) -> Dict[str, str]:
    """收集被标记为「临时/敏感、不应长期复用」的事实值。

    判定规则：fact.id 以 tmp_ 开头，或 key 含 token/password/secret 关键字。
    """
    forbidden: Dict[str, str] = {}
    for step in scenario.steps:
        for fact in step.facts:
            if fact.id.startswith("tmp_"):
                forbidden.update(fact.fields)
                continue
            for key, value in fact.fields.items():
                low = key.lower()
                if any(marker in low for marker in ("token", "password", "secret")):
                    forbidden[key] = value
    return forbidden


def boundary_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """临时/敏感信息边界识别：不应长期保留复用的信息，在后续任务中不得被再次引用。"""
    if scenario is None:
        return []
    forbidden = _forbidden_values(scenario)
    if not forbidden:
        return []
    probe_tss = [
        ts
        for step in scenario.steps
        if step.type == StepType.PROBE
        for ts in [_checkpoint_ts(store, step.name)]
        if ts
    ]
    if not probe_tss:
        return []
    texts = []
    for ev in store.query(source=Source.AGENT):
        if any(ev.ts >= ts0 for ts0 in probe_tss):
            texts.append(_event_text(ev))

    results: List[CheckResult] = []
    for key, value in forbidden.items():
        reused = any(_mentions(t, value) for t in texts)
        if reused:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="boundary_check",
                    message="临时/敏感信息 {}={} 不应被后续任务复用（边界识别失败）".format(
                        key, value
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="boundary_check",
                    message="临时/敏感信息 {} 未被后续任务复用，边界识别正确".format(key),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

_RULES = [
    ("say_do_check", say_do_check),
    ("memory_behavior_check", memory_behavior_check),
    ("time_update_check", time_update_check),
    ("probe_expectation_check", probe_expectation_check),
    ("boundary_check", boundary_check),
]


def run_all(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """依次执行全部一致性规则并汇总结果。

    新规则（探针期望/边界识别）依赖场景定义，需传入 scenario；缺失时自动跳过。
    """
    results: List[CheckResult] = []
    for _name, fn in _RULES:
        results.extend(fn(store, scenario))
    return results


def summarize(checks: List[CheckResult]) -> Dict[str, int]:
    """统计 PASS / FAIL / WARN / N/A 数量。"""
    summary = {
        "checks_pass": 0,
        "checks_fail": 0,
        "checks_warn": 0,
        "checks_na": 0,
    }
    for c in checks:
        if c.status == Status.NA:
            summary["checks_na"] += 1
            continue
        key = "checks_{}".format(c.status.value.lower())
        if key in summary:
            summary[key] += 1
    return summary
