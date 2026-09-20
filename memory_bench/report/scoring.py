"""多维评分模型与失败模式归因。

把一次评测的原始一致性检查结果（PASS/FAIL/WARN/N/A）结构化为：

1. 多维能力画像（dimensions）：长期记忆不是单一总分，而是十个可解释
   的能力维度各自评分（per-dimension PASS 比率 + 状态），发现
   「记性好但边界差」「更新好但跨会话差」等差异；
2. 失败模式归因（failure modes）：把每个 FAIL/WARN 进一步分类为
   正确 / 遗漏 / 混淆 / 错误持久化 / 错误复用，让结论可解释、
   可复查、可自动聚合（不依赖人工看 message 文本）；
3. 综合评分（overall）：多维得分的加权汇总 + 等级。

评分只依赖结构化对象（CheckResult），确定性、无随机、无模型调用，
同一输入必然得到同一输出，满足可复现性要求。
"""

import enum
from typing import Any, Dict, List, Optional

from memory_bench.evidence.consistency import CheckResult, Status

# ---------------------------------------------------------------------------
# 能力维度定义
# ---------------------------------------------------------------------------

# 规则 -> 主要能力维度。每个维度描述一种长期记忆能力，
# 由若干一致性规则给出量化证据。
RULE_TO_DIMENSION: Dict[str, str] = {
    "say_do_check": "retention",
    "memory_behavior_check": "update",
    "time_update_check": "update",
    "probe_expectation_check": "recall",
    "boundary_check": "boundary",
    "cross_session_check": "persistence",
    "conflict_rollback_check": "rollback",
    "cross_file_consistency_check": "consistency",
    "causality_check": "causality",
}

DIMENSIONS: Dict[str, str] = {
    "retention": "记忆保留（干扰后仍能保持与兑现承诺）",
    "update": "动态更新（更新后不残留旧值）",
    "recall": "记忆调用（期望值被正确调用）",
    "distinguish": "相近区分（不与近似干扰值混淆）",
    "boundary": "边界保密（临时/敏感信息不被长期复用）",
    "reuse": "任务复用（同一记忆被多个任务正确复用）",
    "persistence": "跨会话持久化（新会话仍能调用旧会话记忆）",
    "rollback": "冲突回滚（错误值能回滚到权威值）",
    "consistency": "交叉文件一致性（多文件取值一致）",
    "causality": "时序因果（行为引用不早于记忆写入）",
}

_SUB_RULES: Dict[str, List[str]] = {
    "retention": ["say_do_check"],
    "update": ["memory_behavior_check", "time_update_check"],
    "recall": ["probe_expectation_check"],
    "distinguish": ["probe_expectation_check"],
    "boundary": ["boundary_check"],
    "reuse": ["probe_expectation_check", "time_update_check"],
    "persistence": ["cross_session_check"],
    "rollback": ["conflict_rollback_check"],
    "consistency": ["cross_file_consistency_check"],
    "causality": ["causality_check"],
}

# ---------------------------------------------------------------------------
# 失败模式
# ---------------------------------------------------------------------------


class FailureMode(enum.Enum):
    """失败模式归因：把结论细分为可解释的长期记忆异常类别。"""

    CORRECT = "correct"                    # 正确记忆与使用
    OMISSION = "omission"                  # 遗漏：应调用记忆却未调用
    CONFUSION = "confusion"                # 混淆：调用了错误/相近的值
    ERRONEOUS_PERSISTENCE = "erroneous_persistence"  # 错误持久化：旧值残留/错误值未纠正
    ERRONEOUS_REUSE = "erroneous_reuse"    # 错误复用：临时/敏感信息被长期复用
    NA = "na"                              # 不适用


MODE_LABELS: Dict[str, str] = {
    "correct": "正确记忆",
    "omission": "遗漏（应调用未调用）",
    "confusion": "混淆（用了错误/相近值）",
    "erroneous_persistence": "错误持久化（旧值残留/未纠正）",
    "erroneous_reuse": "错误复用（敏感/临时信息复用）",
    "na": "不适用",
}

# 每条规则在 FAIL/WARN 时的主要归因倾向（message 关键词可细化）
_FAIL_MODE_HINTS: Dict[str, str] = {
    "say_do_check": FailureMode.OMISSION.value,
    "memory_behavior_check": FailureMode.ERRONEOUS_PERSISTENCE.value,
    "time_update_check": FailureMode.ERRONEOUS_PERSISTENCE.value,
    "probe_expectation_check": FailureMode.CONFUSION.value,
    "boundary_check": FailureMode.ERRONEOUS_REUSE.value,
    "cross_session_check": FailureMode.CONFUSION.value,
    "conflict_rollback_check": FailureMode.ERRONEOUS_PERSISTENCE.value,
    "cross_file_consistency_check": FailureMode.ERRONEOUS_PERSISTENCE.value,
    "causality_check": FailureMode.ERRONEOUS_REUSE.value,
}

_WARN_MODE_HINTS: Dict[str, str] = {
    "probe_expectation_check": FailureMode.OMISSION.value,
    "cross_session_check": FailureMode.OMISSION.value,
    "say_do_check": FailureMode.OMISSION.value,
}


def classify_failure(check: CheckResult) -> str:
    """把一条检查结果归因到失败模式。

    优先使用规则内置倾向；FAIL/WARN 的 message 中若显式包含
    关键语义（如「残留」「复用临时」「应使用期望值却引用」），
    则按语义细化归因，提升可解释性。
    """
    status = check.status
    rule = check.rule
    message = check.message or ""
    if status == Status.PASS:
        return FailureMode.CORRECT.value
    if status == Status.NA:
        return FailureMode.NA.value
    if status == Status.WARN:
        base = _WARN_MODE_HINTS.get(rule, FailureMode.NA.value)
    else:  # FAIL
        base = _FAIL_MODE_HINTS.get(rule, FailureMode.ERRONEOUS_PERSISTENCE.value)

    # message 语义细化
    low = message.lower()
    if any(tok in low for tok in ("残留", "旧值", "回滚", "最终停留", "未回滚", "取值不一致")):
        return FailureMode.ERRONEOUS_PERSISTENCE.value
    if any(tok in low for tok in ("复用", "不应", "临时", "敏感", "过早引用")):
        return FailureMode.ERRONEOUS_REUSE.value
    if any(tok in low for tok in ("未调用", "未观察到", "找不到", "无法验证")):
        return FailureMode.OMISSION.value
    if any(tok in low for tok in ("混淆", "干扰", "却引用", "引用其它", "相近")):
        return FailureMode.CONFUSION.value
    return base


def annotate_checks(checks: List[CheckResult]) -> List[Dict[str, Any]]:
    """为每条检查结果附加 failure_mode 与所属维度，返回可 JSON 化的 dict 列表。"""
    out = []
    for check in checks:
        item = check.to_dict()
        item["dimension"] = RULE_TO_DIMENSION.get(check.rule, "other")
        item["failure_mode"] = classify_failure(check)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 多维评分
# ---------------------------------------------------------------------------


def _status_counts(checks: List[CheckResult]) -> Dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "N/A": 0}
    for c in checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    return counts


def score_dimension(
    name: str, checks: List[CheckResult]
) -> Dict[str, Any]:
    """计算单个能力维度的评分。

    score = (PASS + 0.5 * WARN) / 参与计分的检查数（N/A 不计）：
    PASS 计 1.0，WARN 计 0.5（模糊但可解释），FAIL 计 0。
    status 按比率给出优良中差等级。
    """
    counts = _status_counts(checks)
    scored = counts["PASS"] + counts["WARN"] + counts["FAIL"]
    if scored == 0:
        return {
            "dimension": name,
            "label": DIMENSIONS.get(name, name),
            "score": None,
            "status": "N/A",
            "counts": counts,
            "checks": 0,
        }
    score = (counts["PASS"] + 0.5 * counts["WARN"]) / scored
    score = round(score, 3)
    if score >= 0.9:
        status = "优秀"
    elif score >= 0.7:
        status = "良好"
    elif score >= 0.4:
        status = "一般"
    else:
        status = "差"
    return {
        "dimension": name,
        "label": DIMENSIONS.get(name, name),
        "score": score,
        "status": status,
        "counts": counts,
        "checks": scored,
    }


def score_dimensions(
    checks: List[CheckResult],
    scenario_dimension: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """为报告提供十个能力维度的评分画像。

    每个维度由对应规则集合驱动；未触发的规则计 0 参与项。
    主维度（场景声明的 dimension）优先展示在第一位。
    """
    by_dim: Dict[str, List[CheckResult]] = {}
    for check in checks:
        dim = RULE_TO_DIMENSION.get(check.rule)
        if dim:
            by_dim.setdefault(dim, []).append(check)

    results = []
    for name in DIMENSIONS:
        dim_checks = by_dim.get(name, [])
        if not dim_checks:
            continue
        results.append(score_dimension(name, dim_checks))

    if scenario_dimension:
        results.sort(
            key=lambda d: (0 if d["dimension"] == scenario_dimension else 1, d["dimension"])
        )
    return results


def compute_overall(checks: List[CheckResult]) -> Dict[str, Any]:
    """综合评分：所有已触发规则的平均得分 + 等级 + 失败模式分布。

    NA（不适用）检查不计入得分，但在 na 字段中如实计数。
    """
    scored = [c for c in checks if c.status != Status.NA]
    counts = _status_counts(scored)
    if not scored:
        return {
            "score": None,
            "status": "N/A",
            "pass": 0,
            "warn": 0,
            "fail": 0,
            "na": _status_counts(checks)["N/A"],
            "checked": 0,
            "failure_modes": [],
        }
    score = (counts["PASS"] + 0.5 * counts["WARN"]) / len(scored)
    status = "优秀" if score >= 0.9 else "良好" if score >= 0.7 else "一般" if score >= 0.4 else "差"

    modes: Dict[str, int] = {}
    for check in scored:
        mode = classify_failure(check)
        modes[mode] = modes.get(mode, 0) + 1
    mode_rows = [
        {"mode": mode, "label": MODE_LABELS.get(mode, mode), "count": count}
        for mode, count in sorted(modes.items())
        if count
    ]
    return {
        "score": round(score, 3),
        "status": status,
        "pass": counts["PASS"],
        "warn": counts["WARN"],
        "fail": counts["FAIL"],
        "na": _status_counts(checks)["N/A"],
        "checked": len(scored),
        "failure_modes": mode_rows,
    }


def build_scoring(checks: List[CheckResult], scenario_dimension: Optional[str]) -> Dict[str, Any]:
    """组装完整评分块（供报告与 CLI 使用）。"""
    return {
        "overall": compute_overall(checks),
        "dimensions": score_dimensions(checks, scenario_dimension),
        "annotated_checks": annotate_checks(checks),
    }


def format_scoring(scoring: Dict[str, Any]) -> str:
    """把评分块渲染为人类可读文本（CLI 输出用）。"""
    overall = scoring["overall"]
    lines = [
        "综合评分：{}（状态：{}）".format(
            overall.get("score") if overall.get("score") is not None else "-",
            overall.get("status"),
        ),
        "PASS={} WARN={} FAIL={} N/A={}".format(
            overall["pass"], overall["warn"], overall["fail"], overall["na"]
        ),
    ]
    if overall["failure_modes"]:
        lines.append("失败模式归因：")
        for row in overall["failure_modes"]:
            lines.append(
                "  {:<12} x{}（{}）".format(row["label"], row["count"], row["mode"])
            )
    lines.append("能力维度画像：")
    for dim in scoring["dimensions"]:
        score = dim["score"]
        lines.append(
            "  {:<10} score={:<6} {}（{} 条检查）".format(
                dim["dimension"],
                str(score) if score is not None else "-",
                dim["status"],
                dim["checks"],
            )
        )
    return "\n".join(lines)