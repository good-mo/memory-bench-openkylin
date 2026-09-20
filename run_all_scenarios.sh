#!/usr/bin/env bash
# 一键全量跑批：清理输出 → 运行六维度场景矩阵 → 输出汇总
# 用法：bash run_all_scenarios.sh [--keep-out]
set -u

cd "$(dirname "$0")" || exit 1

RUNNERS=(
  "demo_retention dummy"
  "demo_update dummy"
  "demo_update bad"
  "demo_recall dummy"
  "demo_near dummy"
  "demo_near confuse"
  "demo_boundary dummy"
  "demo_boundary leaky"
  "demo_reuse dummy"
  "demo_privacy_constraint dummy"
  "demo_privacy_constraint leaky"
  "demo_persist sessiondummy"
  "demo_persist amnesia"
  "demo_rollback rollback"
  "demo_rollback norollback"
  "demo_crossfile crossfile"
  "demo_crossfile dirtyfile"
  "demo_forget forget"
  "demo_forget ignoreforget"
  "demo_ok_config okconfig"
  "demo_ok_config okamnesia"
  "demo_tool tooldummy"
  "demo_tool tooltokenreuse"
  "demo_tool toolnomemory"
)

if [ "${1:-}" != "--keep-out" ]; then
  rm -rf out
fi
mkdir -p out
LOG="out/run_matrix.log"
: > "$LOG"

for pair in "${RUNNERS[@]}"; do
  set -- $pair
  sid="$1"
  agent="$2"
  outdir="out/${sid}__${agent}"
  echo ">>> ${sid} / ${agent}" | tee -a "$LOG"
  if python3 -m memory_bench.runner run --scenario "$sid" --agent "$agent" \
      --outdir "$outdir" >> "$LOG" 2>&1; then
    echo "    OK" | tee -a "$LOG"
  else
    echo "    失败（见日志）" | tee -a "$LOG"
  fi
done

echo
echo "==== 维度矩阵汇总（PASS/FAIL/WARN/N/A + 综合评分）====" | tee -a "$LOG"
python3 - <<'PY' >> "$LOG" 2>&1
import glob
import json
import os


def load(sid, agent):
    path = os.path.join("out", "{}__{}".format(sid, agent), "report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


ORDER = [
    ("demo_retention", "dummy", "长期保持"),
    ("demo_update", "dummy", "动态更新"),
    ("demo_update", "bad", "动态更新(坏变体)"),
    ("demo_recall", "dummy", "记忆调用"),
    ("demo_near", "dummy", "相近区分"),
    ("demo_near", "confuse", "相近区分(坏变体)"),
    ("demo_boundary", "dummy", "边界识别"),
    ("demo_boundary", "leaky", "边界识别(坏变体)"),
    ("demo_reuse", "dummy", "任务复用"),
    ("demo_privacy_constraint", "dummy", "隐私边界"),
    ("demo_privacy_constraint", "leaky", "隐私边界(坏变体)"),
    ("demo_persist", "sessiondummy", "跨会话持久化"),
    ("demo_persist", "amnesia", "跨会话持久化(坏变体)"),
    ("demo_rollback", "rollback", "冲突回滚"),
    ("demo_rollback", "norollback", "冲突回滚(坏变体)"),
    ("demo_crossfile", "crossfile", "交叉文件一致性"),
    ("demo_crossfile", "dirtyfile", "交叉文件一致性(坏变体)"),
    ("demo_forget", "forget", "遗忘指令执行"),
    ("demo_forget", "ignoreforget", "遗忘指令执行(坏变体)"),
    ("demo_ok_config", "okconfig", "openKylin 配置记忆"),
    ("demo_ok_config", "okamnesia", "openKylin 配置记忆(坏变体)"),
    ("demo_tool", "tooldummy", "外部工具调用"),
    ("demo_tool", "tooltokenreuse", "外部工具(复用敏感令牌)"),
    ("demo_tool", "toolnomemory", "外部工具(不读记忆)"),
]

header = "{:<22} {:<16} {:>6} {:>6} {:>6} {:>6} {:>7}  {}".format(
    "场景/智能体", "维度", "PASS", "FAIL", "WARN", "N/A", "评分", "说明")
print(header)
print("-" * len(header))
for sid, agent, label in ORDER:
    data = load(sid, agent)
    if data is None:
        print("{:<22} {:<16} 无报告".format(sid + "/" + agent, label))
        continue
    checks = data.get("consistency", [])
    stat = {"PASS": 0, "FAIL": 0, "WARN": 0, "N/A": 0}
    for c in checks:
        stat[c["status"]] = stat.get(c["status"], 0) + 1
    scoring = data.get("scoring") or {}
    overall = scoring.get("overall") or {}
    score = overall.get("score")
    score_txt = "{:.3f}".format(score) if score is not None else "-"
    note = ""
    for c in checks:
        if c["status"] == "FAIL":
            note = c["message"]
            break
    if c["status"] in ("FAIL",) and note == "":
        note = ""
    note = note if len(note) <= 40 else note[:40] + "…"
    print("{:<22} {:<16} {:>6} {:>6} {:>6} {:>6} {:>7}  {}".format(
        sid + "/" + agent, label, stat["PASS"], stat["FAIL"], stat["WARN"],
        stat["N/A"], score_txt, note))
PY

echo
echo "完整运行日志：out/run_matrix.log"