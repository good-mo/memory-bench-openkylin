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
    """记忆—行为一致性：记忆被更新后，后续行为（ACTION/ARTIFACT）不得再引用旧值。

    回滚感知：若某变化属于回滚语义下的「中间冲突值过渡」（已被回滚覆盖），
    跳过该变化，避免把正确的回滚误判为旧值残留。
    """
    results: List[CheckResult] = []
    changes = _memory_changes(store)
    suppressed = _rollback_suppressed_changes(store, scenario)
    for key, old_value, new_value, update_ts, update_ev_id in changes:
        if (key, new_value) in suppressed:
            continue
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
    """时间—更新一致性：汇总统计更新后旧值/新值在行为（ACTION/ARTIFACT）中被引用的次数。

    回滚感知：跳过回滚语义下被覆盖的中间冲突值变化（见
    memory_behavior_check 说明），避免把正确回滚误判为旧值残留。
    """
    changes = _memory_changes(store)
    suppressed = _rollback_suppressed_changes(store, scenario)
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
        if (key, old_value) in suppressed or (key, new_value) in suppressed:
            continue
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
# 规则六：跨会话持久化校验（cross-session）
# ---------------------------------------------------------------------------


def _session_texts(store: EvidenceStore, session: str, after_ts: Optional[str] = None) -> List[str]:
    """取指定会话内（可选时间窗）AGENT 事件正文文本。"""
    texts: List[str] = []
    for ev in store.query(source=Source.AGENT, session=session):
        if after_ts is not None and ev.ts < after_ts:
            continue
        texts.append(_event_text(ev))
    return texts


def cross_session_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """跨会话持久化：会话 B 应能调用会话 A 注入、未经当前会话重现的记忆。

    对每个 PROBE 步骤（带 expected），若其所属 session 在场景中出现过更早的
    会话（初始 INJECT 会话），则校验探针会话中是否引用了期望值：
    - 引用了期望值 → PASS（跨会话记忆被正确调用）；
    - 引用了其它场景值 → FAIL（跨会话调用混乱/取了别的会话的值）；
    - 完全未引用 → WARN（跨会话记忆未被调用）。
    """
    if scenario is None:
        return []
    # 场景里显式声明的会话顺序（未声明 session 的步骤归属于场景默认会话）
    sessions = list(dict.fromkeys([s.session or "" for s in scenario.steps if s.session]))
    if len(sessions) < 2:
        return []
    all_values = _scenario_all_values(scenario)
    results: List[CheckResult] = []
    for step in scenario.steps:
        if step.type != StepType.PROBE or not step.expected:
            continue
        sess = step.session or ""
        ts0 = _checkpoint_ts(store, step.name)
        texts = _session_texts(store, sess, after_ts=ts0)
        expected_values = {str(v) for v in step.expected.values()}
        used_expected = any(_mentions(t, v) for t in texts for v in expected_values)
        distractors = sorted(all_values - expected_values)
        used_other = [v for v in distractors if any(_mentions(t, v) for t in texts)]
        if ts0 is None:
            continue
        if used_expected:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="cross_session_check",
                    message="跨会话场景：会话「{}」正确调用了期望值 {}（持久化记忆未丢失）".format(
                        sess, "、".join(sorted(expected_values))
                    ),
                )
            )
        elif used_other:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="cross_session_check",
                    message="跨会话场景：会话「{}」本应使用期望值 {}，却引用了其它值：{}".format(
                        sess, "、".join(sorted(expected_values)), "、".join(used_other)
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.WARN,
                    rule="cross_session_check",
                    message="跨会话场景：会话「{}」未观察到对期望值 {} 的引用".format(
                        sess, "、".join(sorted(expected_values))
                    ),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则七：冲突回滚校验（conflict-rollback）
# ---------------------------------------------------------------------------

_KNOWN_KEYS = (
    "server_ip",
    "port",
    "download_dir",
    "editor",
    "staging_ip",
    "ssh_user",
    "deploy_root",
    "ssh_token",
    "tmp_payment_ref",
)


def _key_values_of_scenario(scenario: Scenario) -> Dict[str, List[str]]:
    """收集场景中出现过的 key -> 全部值列表（INJECT / UPDATE facts + PROBE expected）。"""
    by_key: Dict[str, List[str]] = {}
    for step in scenario.steps:
        for fact in step.facts:
            for key, value in fact.fields.items():
                by_key.setdefault(key, []).append(value)
        for key, value in step.expected.items():
            by_key.setdefault(key, []).append(value)
    return by_key


def _scenario_rollback_keys(scenario: Optional[Scenario]) -> Dict[str, str]:
    """推断场景中的「回滚语义」键：key -> 最早权威值。

    判定规则：某键最早注入值（或最早的 expected 值）与某个 PROBE 的期望值
    相同，且场景中曾给该键写入过不同的中间值（冲突值）。满足则说明场景
    期望智能体在冲突后把值回滚回这个权威值。
    """
    if scenario is None:
        return {}

    def _earliest_value(k: str) -> Optional[str]:
        for step in scenario.steps:
            for fact in step.facts:
                if k in fact.fields:
                    return str(fact.fields[k])
            if step.expected and k in step.expected:
                return str(step.expected[k])
        return None

    rollback_keys: Dict[str, str] = {}
    for step in scenario.steps:
        if step.type != StepType.PROBE or not step.expected:
            continue
        for key, value in step.expected.items():
            earliest = _earliest_value(str(key))
            if earliest is not None and earliest == str(value):
                conflict_seen = False
                for step2 in scenario.steps:
                    for fact in step2.facts:
                        if str(key) in fact.fields and str(fact.fields[str(key)]) != earliest:
                            conflict_seen = True
                    if step2.expected and str(key) in step2.expected:
                        if str(step2.expected[str(key)]) != earliest:
                            conflict_seen = True
                if conflict_seen:
                    rollback_keys[str(key)] = earliest
    return rollback_keys


def _memory_final_value(
    store: EvidenceStore, key: str
) -> Optional[str]:
    """取某记忆键最后一次写入的值（用于判断回滚是否真的发生）。"""
    evs = [
        ev
        for ev in store.query(type=EvidenceType.MEMORY, source=Source.AGENT)
        if str(ev.content.get("key", "")) == key
    ]
    evs.sort(key=lambda e: e.ts)
    if not evs:
        return None
    last = evs[-1].content.get("value")
    return None if last is None else str(last)


def _rollback_suppressed_changes(
    store: EvidenceStore, scenario: Optional[Scenario]
) -> Dict[Tuple[str, str], None]:
    """识别在回滚场景中应被跳过的「中间过渡变化」。

    当某键存在回滚语义（权威值为 A，中间冲突值为 C），且最终记忆值已回滚
    回 A 时，变化链里 A->C 这一步只是被错误覆盖的临时状态，后续行为正确地
    引用 A，不应被「旧值残留」类规则视为违规。返回 {(key, conflict_value)}。
    """
    rollback_keys = _scenario_rollback_keys(scenario)
    suppressed: Dict[Tuple[str, str], None] = {}
    for key, authoritative in rollback_keys.items():
        if _memory_final_value(store, key) != authoritative:
            continue  # 回滚未发生，不豁免中间过渡变化
        for ev in store.query(type=EvidenceType.MEMORY, source=Source.AGENT):
            if str(ev.content.get("key", "")) != key:
                continue
            val = ev.content.get("value")
            if val is not None and str(val) != authoritative:
                suppressed[(key, str(val))] = None
    return suppressed


def conflict_rollback_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """冲突回滚：同一记忆键被错误更新后，智能体应把值回滚到最早的权威值。

    仅当场景存在「回滚语义」时启用：某键最早注入值（权威值）与某个 PROBE 的
    期望值相同，且场景中曾给该键写入过不同的中间值（冲突值）。此时：
    - 最终记忆值等于最早权威值 → PASS（回滚成功）；
    - 最终记忆值停留在中间冲突值 → FAIL（回滚失败）；
    - 无法构造上述对比 → 返回空（该场景无回滚语义，不参与评测）。
    """
    if scenario is None:
        return []

    rollback_keys = _scenario_rollback_keys(scenario)

    results: List[CheckResult] = []
    for key, authoritative in rollback_keys.items():
        memory_events = [
            ev
            for ev in store.query(type=EvidenceType.MEMORY, source=Source.AGENT)
            if str(ev.content.get("key", "")) == key
        ]
        memory_events.sort(key=lambda e: e.ts)
        seen: List[str] = []
        for ev in memory_events:
            val = ev.content.get("value")
            if val is None:
                continue
            val = str(val)
            if not seen or seen[-1] != val:
                seen.append(val)
        if len(seen) < 2:
            continue  # 无实际变更，无需回滚
        last = seen[-1]
        if last == authoritative:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="conflict_rollback_check",
                    message="键「{}」发生过冲突变更（{}），最终回滚到最早值「{}」，冲突已消解".format(
                        key, "->".join(seen), authoritative
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="conflict_rollback_check",
                    message="键「{}」发生了冲突变更（{}），最终停留在「{}」，未回滚到最早值「{}」".format(
                        key, "->".join(seen), last, authoritative
                    ),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则八：交叉文件一致性校验（cross-file）
# ---------------------------------------------------------------------------

_KV_IN_FILE = re.compile(r"([A-Za-z_][\w.-]*)\s*=\s*([^\s,\n，；]+)")


def _artifact_kv_pairs(store: EvidenceStore) -> Dict[str, Dict[str, str]]:
    """统计每个 ARTIFACT 中 k=v 键值对；key -> {path: value}。

    用于校验「多个文件引用同一键时取值是否一致」。仅在 ARTIFACT 的
    content.content 正文里做 k=v 匹配，避免 JSON 外壳字段干扰。
    """
    raw: Dict[str, set] = {}
    for ev in store.query(type=EvidenceType.ARTIFACT):
        body = ev.content.get("content")
        if not isinstance(body, str) or not body:
            continue
        path = str(ev.content.get("path") or ev.task or ev.ev_id)
        for key, value in _KV_IN_FILE.findall(body):
            raw.setdefault(key, set()).add((path, value))
    by_key: Dict[str, Dict[str, str]] = {}
    for key, pairs in raw.items():
        merged: Dict[str, str] = {}
        for path, value in pairs:
            merged[path] = value
        by_key[key] = merged
    return by_key


def cross_file_consistency_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """交叉文件一致性：同一记忆键在多个文件产物中取值必须一致。

    - 同一键至少出现在 2 个文件且全部取值一致 → PASS；
    - 同一键出现在 2 个以上文件但取值不一致 → FAIL（交叉文件冲突）；
    - 键只出现在单个文件，或场景未定义该键 → N/A（跳过）。
    """
    if scenario is None:
        return []
    scenario_values = _key_values_of_scenario(scenario)
    by_key = _artifact_kv_pairs(store)
    results: List[CheckResult] = []
    for key, files_values in by_key.items():
        paths = list(files_values.keys())
        values = list(files_values.values())
        if len(paths) < 2:
            continue
        if len(set(values)) == 1:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="cross_file_consistency_check",
                    message="键「{}」在 {} 个文件中取值一致（{}）".format(
                        key, len(paths), "、".join(values)
                    ),
                )
            )
        else:
            details = "；".join(
                "{}={}".format(p, v) for p, v in files_values.items()
            )
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="cross_file_consistency_check",
                    message="键「{}」在多个文件中取值不一致：{}".format(key, details),
                )
            )
    # 场景里期望跨文件一致的键未出现在多个文件中 → WARN
    for key in scenario_values:
        if key in _KNOWN_KEYS and key not in by_key:
            continue
        if len(by_key.get(key, {})) < 2 and key in scenario_values:
            results.append(
                CheckResult(
                    status=Status.NA,
                    rule="cross_file_consistency_check",
                    message="键「{}」未出现在多个文件产物中，无法进行交叉文件一致性校验".format(key),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则九：时序因果性校验（causality）
# ---------------------------------------------------------------------------


def causality_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """时序因果性：行为引用某个值的时间不得早于该值被写入记忆的时间。

    对每个发生过变动的键，要求其最新值的首次行为引用必须发生在该值首次
    MEMORY 写入之后；若行为引用了「后来才写入」的值（时序倒挂），则 FAIL。
    """
    if scenario is None:
        return []
    scenario_values = _key_values_of_scenario(scenario)
    results: List[CheckResult] = []
    for key in _KNOWN_KEYS:
        if key not in scenario_values:
            continue
        # 该键的 MEMORY 写入时间线（key,value,ts,ev_id）
        memory_events = [
            ev
            for ev in store.query(type=EvidenceType.MEMORY, source=Source.AGENT)
            if str(ev.content.get("key", "")) == key
        ]
        memory_events.sort(key=lambda e: e.ts)
        if not memory_events:
            continue
        value_first_ts: Dict[str, str] = {}
        for ev in memory_events:
            val = ev.content.get("value")
            if val is None:
                continue
            val = str(val)
            value_first_ts.setdefault(val, ev.ts)

        # 行为引用：DIALOGUE / ACTION / ARTIFACT（AGENT）
        use_events = []
        for etype in (EvidenceType.DIALOGUE, EvidenceType.ACTION, EvidenceType.ARTIFACT):
            for ev in store.query(type=etype, source=Source.AGENT):
                use_events.append(ev)
        use_events.sort(key=lambda e: e.ts)

        anomalies: List[Tuple[str, str]] = []
        for ev in use_events:
            text = _event_text(ev)
            for val, write_ts in value_first_ts.items():
                if _mentions(text, val) and ev.ts < write_ts:
                    anomalies.append((val, ev.ev_id))
        if not anomalies:
            results.append(
                CheckResult(
                    status=Status.PASS,
                    rule="causality_check",
                    message="键「{}」所有行为引用都发生在记忆写入之后，时序因果成立".format(key),
                )
            )
        else:
            detail = "；".join(
                "值 {} 被 {} 过早引用".format(val, ev_id) for val, ev_id in anomalies[:3]
            )
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="causality_check",
                    message="键「{}」存在时序倒挂：{}".format(key, detail),
                )
            )
    return results


# ---------------------------------------------------------------------------
# 规则十：工具调用长期记忆校验（tool-call，M7）
# ---------------------------------------------------------------------------

def _scenario_oas_tools(scenario: Optional[Scenario]):
    """从场景里解析 OAS 工具契约；无 OAS 文档返回 None。

    场景类型新增 oas 字段（OpenAPI 文档 dict 或文件路径字符串），
    供 tool_call_check 定位工具与 x-memory 参数标记。
    """
    if scenario is None:
        return None
    oas = getattr(scenario, "oas", None)
    if not oas:
        return None
    from memory_bench.tools.oas import load_oas, parse_oas

    if isinstance(oas, str):
        try:
            return load_oas(oas)
        except (OSError, ValueError):
            return None
    if isinstance(oas, dict):
        try:
            return parse_oas(oas)
        except ValueError:
            return None
    return None


def tool_call_check(
    store: EvidenceStore, scenario: Optional[Scenario] = None
) -> List[CheckResult]:
    """工具调用长期记忆：外部工具参数须复用记忆 / 敏感参数不得复用。

    对每条 TOOL 证据（content 形如 {"tool": "operationId", "args": {...}}）：
    1. 参数记忆（x-memory.remember 标记的参数）在 TOOL 调用事件中若出现了，
       其值与记忆键的写入值一致 → PASS；与已写入记忆值不同（没用记忆）→ FAIL；
    2. 敏感参数（x-memory.sensitive 标记）不应在工具调用中携带/复用
       （一次性 token/密钥）→ 出现即 FAIL，未出现 → PASS；
    3. 工具调用缺参数记忆链（无 TOOL 事件）→ N/A。
    """
    if scenario is None:
        return []
    spec = _scenario_oas_tools(scenario)
    if spec is None or not spec.tools:
        return []

    # 记忆键 → 最新写入值
    memory_latest: Dict[str, str] = {}
    for ev in store.query(type=EvidenceType.MEMORY, source=Source.AGENT):
        key = str(ev.content.get("key", ""))
        value = ev.content.get("value")
        if key and value is not None:
            memory_latest[key] = str(value)

    tool_events = store.query(type=EvidenceType.TOOL, source=Source.AGENT)
    if not tool_events:
        return [
            CheckResult(
                status=Status.NA,
                rule="tool_call_check",
                message="场景定义了 OAS 工具契约，但未观察到任何 TOOL 调用事件（N/A）",
            )
        ]

    results: List[CheckResult] = []
    seen_ops: Dict[str, bool] = {}
    for ev in tool_events:
        content = ev.content or {}
        operation_id = str(content.get("tool", ""))
        args = content.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        tool = spec.tool(operation_id)
        if tool is None:
            results.append(
                CheckResult(
                    status=Status.FAIL,
                    rule="tool_call_check",
                    message="工具调用事件引用了 OAS 文档中不存在的 operationId「{}」".format(
                        operation_id
                    ),
                    evidence_ids=[ev.ev_id],
                )
            )
            continue
        seen_ops[operation_id] = True

        # 1) x-memory.remember：参数值应与记忆键写入值一致
        for param in tool.params:
            if param.memory_key is None:
                continue
            if param.name not in args:
                continue
            actual = str(args[param.name])
            expected = memory_latest.get(param.memory_key)
            if expected is not None and _mentions(actual, expected):
                results.append(
                    CheckResult(
                        status=Status.PASS,
                        rule="tool_call_check",
                        message="工具「{}」参数 {}={} 复用了记忆键 {}(={})".format(
                            tool.operation_id, param.name, actual,
                            param.memory_key, expected,
                        ),
                        evidence_ids=[ev.ev_id],
                    )
                )
            else:
                results.append(
                    CheckResult(
                        status=Status.FAIL,
                        rule="tool_call_check",
                        message="工具「{}」参数 {}={} 未复用记忆键 {}（期望 {}，即长期记忆未应用到工具调用）".format(
                            tool.operation_id, param.name, actual,
                            param.memory_key, expected or "未写入",
                        ),
                        evidence_ids=[ev.ev_id],
                    )
                )

        # 2) x-memory.sensitive：敏感参数不应出现在工具调用 args 中
        for param in tool.params:
            if not param.sensitive:
                continue
            if param.name in args and str(args[param.name]) != "":
                results.append(
                    CheckResult(
                        status=Status.FAIL,
                        rule="tool_call_check",
                        message="工具「{}」敏感参数 {} 不应在调用中携带/复用（一次性凭据泄漏）".format(
                            tool.operation_id, param.name
                        ),
                        evidence_ids=[ev.ev_id],
                    )
                )
            else:
                results.append(
                    CheckResult(
                        status=Status.PASS,
                        rule="tool_call_check",
                        message="工具「{}」敏感参数 {} 未被复用，边界正确".format(
                            tool.operation_id, param.name
                        ),
                        evidence_ids=[ev.ev_id],
                    )
                )

    # 场景期望调用的工具（PROBE 描述中提及 operationId）从未被调用 → WARN
    expected_ops = set()
    for step in scenario.steps:
        if getattr(step, "type", None) == StepType.PROBE:
            for op_id in spec.tools:
                if op_id and op_id in step.description:
                    expected_ops.add(op_id)
    for op_id in sorted(expected_ops - set(seen_ops)):
        results.append(
            CheckResult(
                status=Status.WARN,
                rule="tool_call_check",
                message="探针任务提及工具「{}」但未观察到对应 TOOL 调用（工具记忆未被使用）".format(
                    op_id
                ),
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
    ("cross_session_check", cross_session_check),
    ("conflict_rollback_check", conflict_rollback_check),
    ("cross_file_consistency_check", cross_file_consistency_check),
    ("causality_check", causality_check),
    ("tool_call_check", tool_call_check),
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
