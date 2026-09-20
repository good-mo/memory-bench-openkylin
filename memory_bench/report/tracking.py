"""评测追踪：run manifest / 批次清单 / 可复现性校验。

目标：
- 每次评测产出结构化 manifest（run id、时间、场景、智能体、seed、
  Git 版本、环境指纹、证据统计、评分摘要），做到「哪个提交跑出哪份结果」；
- 批量运行把每条 manifest 追加进批次清单（NDJSON runs.ndjson），
  支持跨运行追踪与结果对比；
- 可复现性校验（verify）：同一 git 提交 + 同一 seed + 同一参数再次运行，
  manifest 里的 repro_fingerprint 一致，且证据统计/评分摘要可对比，
  从而在 openKylin 标准环境下稳定复现。
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(args: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def environment_fingerprint() -> Dict[str, str]:
    """采集运行环境指纹（python / git / 平台），供可复现性定位。"""
    import platform
    import sys

    return {
        "python": "{}.{}.{}".format(
            sys.version_info[0], sys.version_info[1], sys.version_info[2]
        ),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "git_commit": _git(["rev-parse", "--short", "HEAD"]) or "n/a",
        "git_branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "n/a",
        "git_dirty": "yes" if _git(["status", "--porcelain"]) else "no",
    }


def compute_repro_fingerprint(
    scenario: Any,
    agent_name: str,
    seed: int,
    audit_flag: str = "",
    audit_replay: Optional[str] = None,
    rule_version: str = "v1",
) -> str:
    """计算可复现指纹：由场景定义 + 智能体 + seed + 审计配置共同决定。

    相同输入（同一提交、同一场景 JSON、同一参数）必然得到相同指纹，
    用于判断两次运行是否可比。
    """
    scenario_dict = scenario.to_dict() if hasattr(scenario, "to_dict") else scenario
    import hashlib

    payload = json.dumps(
        {
            "scenario": scenario_dict,
            "agent": agent_name,
            "seed": int(seed),
            "audit": audit_flag,
            "audit_replay": audit_replay,
            "rule_version": rule_version,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    run_id: str,
    scenario: Any,
    agent_name: str,
    seed: int,
    evidence_stats: Dict[str, Any],
    scoring_summary: Optional[Dict[str, Any]] = None,
    audit_flag: str = "",
    audit_replay: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装一条 run manifest。"""
    return {
        "run_id": run_id,
        "created_at": _now_iso(),
        "scenario_id": scenario.id if hasattr(scenario, "id") else str(scenario),
        "scenario_name": scenario.name if hasattr(scenario, "name") else "",
        "dimension": scenario.dimension if hasattr(scenario, "dimension") else "",
        "agent": agent_name,
        "seed": int(seed),
        "audit": audit_flag,
        "audit_replay": audit_replay,
        "env": environment_fingerprint(),
        "repro_fingerprint": compute_repro_fingerprint(
            scenario, agent_name, seed, audit_flag, audit_replay
        ),
        "evidence": evidence_stats,
        "scoring": scoring_summary,
        "extra": extra or {},
    }


def write_manifest(outdir: str, manifest: Dict[str, Any]) -> str:
    """把 manifest 写入 <outdir>/manifest.json，返回路径。"""
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return path


def load_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def record_run(index_path: str, manifest: Dict[str, Any]) -> str:
    """把 manifest 追加进批次清单（NDJSON），返回 index 路径。"""
    os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)
    with open(index_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False) + "\n")
    return index_path


def load_run_index(index_path: str) -> List[Dict[str, Any]]:
    """读取批次清单，返回 manifest 列表（按写入顺序）。"""
    if not os.path.isfile(index_path):
        return []
    runs = []
    with open(index_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except ValueError:
                continue
    return runs


def manifest_repro_key(manifest: Dict[str, Any]) -> str:
    """从 manifest 取可复现指纹（带兜底）。"""
    return str(manifest.get("repro_fingerprint", ""))


def compare_manifests(
    left: Dict[str, Any], right: Dict[str, Any]
) -> Dict[str, Any]:
    """比较两份 manifest，输出可复现性校验结果。

    对比字段：指纹、场景、智能体、seed、证据统计、综合评分。
    """
    fields = ["scenario_id", "agent", "seed"]
    equal_fields = {
        f: (left.get(f) == right.get(f)) for f in fields
    }
    fingerprint_same = manifest_repro_key(left) == manifest_repro_key(right)

    evidence_same = left.get("evidence") == right.get("evidence")
    scoring_left = left.get("scoring") or {}
    scoring_right = right.get("scoring") or {}
    scoring_same = (
        (scoring_left.get("overall") or {}).get("score")
        == (scoring_right.get("overall") or {}).get("score")
    )

    reproducible = (
        fingerprint_same
        and all(equal_fields.values())
        and evidence_same
        and scoring_same
    )
    return {
        "reproducible": reproducible,
        "fingerprint_same": fingerprint_same,
        "left_fingerprint": manifest_repro_key(left),
        "right_fingerprint": manifest_repro_key(right),
        "evidence_same": evidence_same,
        "scoring_same": scoring_same,
        "fields": equal_fields,
    }