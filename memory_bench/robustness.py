"""seed 扰动鲁棒性分析（M5）：同一场景×智能体在多种子下重复运行并聚合。

鲁棒性指标回答一个问题：「结果对随机种子敏感吗？」如果同一配置在不同 seed
下结论剧烈摇摆（PASS → FAIL），说明评测或智能体行为不稳定。本模块提供：

- run_seed_matrix：按给定 seed 列表重复运行同一场景×智能体；
- aggregate_matrix：跨 seed 聚合一致性结论，输出每个规则的状态分布、
  稳定率（出现次数最多的状态占比）、以及六维结论汇总。
"""

import json
import os
from typing import Any, Dict, List, Optional

from memory_bench.evidence.consistency import CheckResult, Status, run_all, summarize
from memory_bench.evidence.store import EvidenceStore
from memory_bench.scenarios.types import Scenario


def run_single(
    scenario: Scenario,
    agent,
    store: EvidenceStore,
    workspace: str,
    seed: int,
    audit_collector: Optional[Any] = None,
) -> Dict[str, Any]:
    """用指定 seed 运行一次场景×智能体，返回 {checks, summary}。

    agent 传已实例化的智能体；workspace 传目录路径。seed 仅用于确定性。
    audit_collector 可选：OS 审计采集器（见 Orchestrator）。
    """
    from memory_bench.harness.orchestrator import Orchestrator

    result = Orchestrator(
        scenario=scenario,
        agent=agent,
        store=store,
        workspace=workspace,
        seed=seed,
        audit_collector=audit_collector,
    ).run()
    return {
        "seed": seed,
        "checks": [c.to_dict() for c in result.checks],
        "summary": result.summary,
    }


def run_seed_matrix(
    scenario: Scenario,
    agent_factory,
    seeds: List[int],
    outdir: str = "out/bench",
    seeds_callback: Optional[Any] = None,
    agent_name: str = "agent",
    audit_sources: Optional[List[str]] = None,
    audit_replay: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按 seed 列表重复运行同一场景×智能体，输出到 outdir/<id>__<agent>/seed-<n>/。

    每个 seed 使用独立 store（NDJSON + sqlite）与独立 workspace，互不干扰。
    返回全部 seed 的运行结果（含 summary / checks / report 路径）。
    audit_sources / audit_replay 非空时，每个 seed 挂载 OS 审计采集器。
    """
    from memory_bench.runner import build_audit_collector

    results: List[Dict[str, Any]] = []
    for seed in seeds:
        run_dir = os.path.join(outdir, "{}__{}".format(scenario.id, agent_name), "seed-{}".format(seed))
        os.makedirs(run_dir, exist_ok=True)
        store = EvidenceStore(os.path.join(run_dir, "evidence.ndjson"))
        agent = agent_factory(seed=seed)
        workspace = os.path.join(run_dir, "workspace")
        audit_collector = build_audit_collector(
            store=store,
            audit_flag=",".join(audit_sources) if audit_sources else "",
            replay=audit_replay,
            workspace=workspace,
        )
        run = run_single(
            scenario=scenario,
            agent=agent,
            store=store,
            workspace=workspace,
            seed=seed,
            audit_collector=audit_collector,
        )
        run["outdir"] = run_dir
        results.append(run)
        if seeds_callback is not None:
            seeds_callback(seed, run)
    return results


def _status_counts(checks: List[Dict[str, str]]) -> Dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "N/A": 0}
    for c in checks:
        key = str(c.get("status", "N/A")).upper()
        if key not in counts:
            counts[key] = 0
        counts[key] += 1
    return counts


def aggregate_matrix(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """跨 seed 聚合一致性结论，计算稳定率与风险摘要。"""
    if not results:
        return {"seeds": [], "rules": {}, "summary": {}}

    seed_count = len(results)
    # 规则维度聚合：rule -> {status -> count}
    per_rule_status: Dict[str, Dict[str, int]] = {}

    def _register(rule: str, status: str) -> None:
        entry = per_rule_status.setdefault(rule, {})
        entry[status] = entry.get(status, 0) + 1

    for run in results:
        for c in run["checks"]:
            _register(str(c.get("rule", "?")), str(c.get("status", "N/A")).upper())

    rules_agg: Dict[str, Any] = {}
    for rule, statuses in per_rule_status.items():
        total = sum(statuses.values())
        dominant_status = max(statuses, key=lambda k: statuses[k])
        stability = statuses[dominant_status] / total if total else 0.0
        rules_agg[rule] = {
            "distribution": statuses,
            "dominant": dominant_status,
            "stability": round(stability, 3),
            "stable_overall": stability >= 0.8,
        }

    # 全局稳定性：每个 seed 的 PASS/FAIL/WARN 汇总
    seeds_summary = []
    for run in results:
        seed_summary = dict(run["summary"])
        seed_summary["seed"] = run["seed"]
        seeds_summary.append(seed_summary)

    # 整体稳定率 = 各 seed 主结论一致性（按 PASS 比例计算）
    pass_ratios = []
    for run in results:
        counts = _status_counts(run["checks"])
        total = sum(counts.values()) or 1
        pass_ratios.append(counts["PASS"] / total)
    mean_pass = sum(pass_ratios) / len(pass_ratios) if pass_ratios else 0.0
    variance = (
        sum((p - mean_pass) ** 2 for p in pass_ratios) / len(pass_ratios)
        if pass_ratios
        else 0.0
    )

    return {
        "seeds": seeds_summary,
        "rules": rules_agg,
        "summary": {
            "seed_count": seed_count,
            "mean_pass_ratio": round(mean_pass, 3),
            "pass_ratio_std": round(variance ** 0.5, 3),
            "verdict": (
                "robust"
                if mean_pass >= 0.8 and variance ** 0.5 <= 0.05
                else "sensitive"
                if variance ** 0.5 > 0.15
                else "moderate"
            ),
        },
    }


def write_matrix_summary(outdir: str, aggregate: Dict[str, Any]) -> str:
    """把聚合结果写入 <outdir>/__matrix__.json，返回路径。"""
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "__matrix__.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(aggregate, fh, ensure_ascii=False, indent=2)
    return path