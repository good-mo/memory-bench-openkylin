"""报告生成：把证据统计 + 一致性检查结果固化为 JSON 与自包含 HTML。"""

import html
import json
import os
from typing import Any, Dict, List

from memory_bench.evidence.consistency import CheckResult
from memory_bench.evidence.model import EvidenceEvent, EvidenceType
from memory_bench.evidence.store import EvidenceStore
from memory_bench.scenarios.types import Scenario

_HTML_TITLE = "openKylin 智能体长期记忆评测报告"

_CSS = """
body { font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
       margin: 0; background: #f6f8fa; color: #24292f; line-height: 1.6; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 24px 16px 64px; }
header { background: #ffffff; border-bottom: 1px solid #d0d7de; padding: 20px 0; }
header h1 { margin: 0; font-size: 22px; }
header .sub { color: #57606a; font-size: 13px; margin-top: 6px; }
.card { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px;
        padding: 16px 20px; margin-top: 20px; }
.card h2 { font-size: 16px; margin: 0 0 12px; border-bottom: 1px solid #eaeef2; padding-bottom: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f0f3f6; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 12px; color: #fff;
         font-size: 12px; font-weight: 600; }
.badge-PASS { background: #1a7f37; }
.badge-FAIL { background: #cf222e; }
.badge-WARN { background: #9a6700; }
.timeline { border-left: 3px solid #d0d7de; margin-left: 8px; padding-left: 16px; }
.tl-item { margin: 10px 0; }
.tl-item .tag { font-family: Menlo, Consolas, monospace; font-size: 12px;
                color: #0969da; margin-right: 8px; }
.tl-item .src {} 
.meta { color: #57606a; font-size: 12px; }
footer { margin-top: 32px; text-align: center; color: #8c959f; font-size: 12px; }
"""


def _event_summary(ev: EvidenceEvent) -> str:
    """为证据事件生成一行文本摘要。"""
    content = ev.content or {}
    if ev.type == EvidenceType.DIALOGUE:
        return str(content.get("text", ""))
    if ev.type == EvidenceType.MEMORY:
        op = content.get("op", "?")
        key = content.get("key", "?")
        value = content.get("value", "")
        return "{} {}={}".format(op, key, value)
    if ev.type == EvidenceType.ACTION:
        cmd = content.get("command", "")
        path = content.get("path")
        detail = content.get("detail", "")
        return "cmd=[{}] path=[{}] detail=[{}]".format(cmd, path or "-", detail)
    if ev.type == EvidenceType.ARTIFACT:
        path = content.get("path", "?")
        body = content.get("content", "")
        return "path=[{}] content=[{}]".format(path, body)
    if ev.type == EvidenceType.CHECKPOINT:
        phase = content.get("phase", "")
        step = content.get("step", "")
        return "phase={} step={}".format(phase, step or "-")
    return json.dumps(content, ensure_ascii=False)


def build_report(
    store: EvidenceStore,
    checks: List[CheckResult],
    scenario: Scenario,
    outdir: str,
) -> Dict[str, str]:
    """生成 report.json 与 report.html，返回 {"json": 路径, "html": 路径}。"""
    os.makedirs(outdir, exist_ok=True)

    all_events = store.query()
    ev_by_type: Dict[str, int] = {}
    for ev in all_events:
        ev_by_type[ev.type.value] = ev_by_type.get(ev.type.value, 0) + 1

    report_data: Dict[str, Any] = {
        "meta": {
            "project": "memory-bench-openkylin",
            "milestone": "M1",
            "scenario": scenario.name,
            "dimension": scenario.dimension,
            "seed": scenario.seed,
            "generated_at": _now_iso(),
        },
        "evidence_stats": {
            "total": len(all_events),
            "by_type": ev_by_type,
        },
        "consistency": [c.to_dict() for c in checks],
        "steps": [
            {
                "type": s.type.value,
                "name": s.name,
                "description": s.description,
                "facts": [f.to_dict() for f in s.facts],
                "expected": s.expected,
            }
            for s in scenario.steps
        ],
    }

    json_path = os.path.join(outdir, "report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, ensure_ascii=False, indent=2)

    html_path = os.path.join(outdir, "report.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(_render_html(report_data, all_events))

    return {"json": json_path, "html": html_path}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------

def _render_html(data: Dict[str, Any], events: List[EvidenceEvent]) -> str:
    meta = data["meta"]
    stats = data["evidence_stats"]

    stat_rows = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(k, v)
        for k, v in sorted(stats["by_type"].items())
    )
    check_rows = "".join(_render_check_row(c) for c in data["consistency"])
    timeline = "".join(_render_timeline_item(ev) for ev in events)
    step_section = _render_steps(data["steps"])

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>{title}</h1>
    <div class="sub">场景：{scenario}（维度：{dimension}）｜seed={seed}｜生成于 {generated_at}</div>
  </div>
</header>
<div class="wrap">

  <div class="card">
    <h2>证据统计</h2>
    <p>证据事件总数：<strong>{total}</strong></p>
    <table>
      <tr><th>证据类型</th><th>数量</th></tr>
      {stat_rows}
    </table>
  </div>

  <div class="card">
    <h2>跨证据一致性检查</h2>
    <table>
      <tr><th>规则</th><th>状态</th><th>说明</th><th>相关证据 id</th></tr>
      {check_rows}
    </table>
  </div>

  <div class="card">
    <h2>场景步骤</h2>
    {step_section}
  </div>

  <div class="card">
    <h2>证据流时间线</h2>
    <div class="timeline">{timeline}</div>
  </div>

  <footer>memory-bench-openkylin M1 ｜ 证据驱动：对话 · 记忆 · 行动 · 产物 统一为带时间戳的证据事件</footer>
</div>
</body>
</html>""".format(
        title=html.escape(_HTML_TITLE),
        css=_CSS,
        scenario=html.escape(str(meta["scenario"])),
        dimension=html.escape(str(meta["dimension"])),
        seed=html.escape(str(meta["seed"])),
        generated_at=html.escape(str(meta["generated_at"])),
        total=str(stats["total"]),
        stat_rows=stat_rows,
        check_rows=check_rows,
        step_section=step_section,
        timeline=timeline,
    )


def _render_check_row(check: Dict[str, Any]) -> str:
    status = str(check["status"])
    ids = check.get("evidence_ids") or []
    return (
        "<tr><td>{rule}</td><td><span class=\"badge badge-{status}\">{status}</span></td>"
        "<td>{msg}</td><td>{ids}</td></tr>"
    ).format(
        rule=html.escape(str(check["rule"])),
        status=html.escape(status),
        msg=html.escape(str(check["message"])),
        ids=html.escape(", ".join(ids)) if ids else "—",
    )


def _render_timeline_item(ev: EvidenceEvent) -> str:
    return (
        '<div class="tl-item">'
        '<span class="tag">{ev_id}</span>'
        '<span class="badge badge-{badge_type}">{type}</span>&nbsp;'
        '<span class="src">[{source}]</span>&nbsp;'
        '<span class="meta">{ts}</span>&nbsp;'
        '<span class="meta">task={task}</span><br>'
        '<span>{summary}</span>'
        '</div>'
    ).format(
        ev_id=html.escape(ev.ev_id),
        badge_type=html.escape(ev.type.value),
        type=html.escape(ev.type.value),
        source=html.escape(ev.source.value),
        ts=html.escape(ev.ts),
        task=html.escape(ev.task or "-"),
        summary=html.escape(_event_summary(ev)),
    )


def _render_steps(steps: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, step in enumerate(steps, start=1):
        facts = "; ".join(
            "{k}={v}".format(k=k, v=v)
            for f in step.get("facts", [])
            for k, v in f.get("fields", {}).items()
        )
        rows.append(
            "<li><strong>{idx}. [{type}] {name}</strong>：{desc}"
            "{facts_note}{expected_note}</li>".format(
                idx=idx,
                type=html.escape(str(step["type"])),
                name=html.escape(str(step["name"])),
                desc=html.escape(str(step["description"])),
                facts_note=(
                    "（事实：{}）".format(html.escape(facts)) if facts else ""
                ),
                expected_note=(
                    "（期望：{}）".format(html.escape(str(step.get("expected", {}))))
                    if step.get("expected")
                    else ""
                ),
            )
        )
    return "<ol>" + "".join(rows) + "</ol>"