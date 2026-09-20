"""CLI 入口：`python3 -m memory_bench.runner run ...` / `serve ...`。"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from memory_bench.evidence.store import EvidenceStore
from memory_bench.harness.orchestrator import Orchestrator
from memory_bench.scenarios.library import builtin_scenarios
from memory_bench.scenarios.types import Scenario, load_scenario

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_DIR = os.path.join(PROJECT_ROOT, "scenarios")

_BUILTIN = [
    "demo_retention",
    "demo_update",
    "demo_recall",
    "demo_near",
    "demo_boundary",
    "demo_reuse",
    "demo_privacy_constraint",
    "demo_persist",
    "demo_rollback",
    "demo_crossfile",
    "demo_forget",
    "demo_ok_config",
]


def load_scenario_with_fallback(name_or_path: str) -> Scenario:
    """加载场景：优先磁盘 scenarios/<id>.json，找不到再回退到内置 library。"""
    candidate = os.path.join(SCENARIOS_DIR, name_or_path + ".json")
    if os.path.isfile(name_or_path):
        return load_scenario(name_or_path)
    if os.path.isfile(candidate):
        return load_scenario(candidate)
    builtin = builtin_scenarios()
    if name_or_path in builtin:
        return builtin[name_or_path]
    raise SystemExit(
        "未知场景 {!r}：可用内置场景 {}，或传入 JSON 文件路径".format(
            name_or_path, ", ".join(_BUILTIN)
        )
    )


def make_agent(name: str, seed: int):
    if name == "dummy":
        from agents.dummy_agent import DummyAgent

        return DummyAgent(seed=seed)
    if name == "bad":
        from agents.dummy_agent import BadDummyAgent

        return BadDummyAgent(seed=seed)
    if name == "confuse":
        from agents.dummy_agent import ConfuseDummyAgent

        return ConfuseDummyAgent(seed=seed)
    if name == "leaky":
        from agents.dummy_agent import LeakyDummyAgent

        return LeakyDummyAgent(seed=seed)
    if name == "deepseek":
        from agents.deepseek_agent import DeepSeekAgent

        return DeepSeekAgent(seed=seed)
    if name == "adapter":
        from agents.adapter_agent import AdapterAgent

        return AdapterAgent(seed=seed)
    if name == "sessiondummy":
        from agents.dummy_agent import SessionDummyAgent

        return SessionDummyAgent(seed=seed)
    if name == "amnesia":
        from agents.dummy_agent import AmnesiaDummyAgent

        return AmnesiaDummyAgent(seed=seed)
    if name == "rollback":
        from agents.dummy_agent import RollbackDummyAgent

        return RollbackDummyAgent(seed=seed)
    if name == "norollback":
        from agents.dummy_agent import NoRollbackDummyAgent

        return NoRollbackDummyAgent(seed=seed)
    if name == "crossfile":
        from agents.dummy_agent import CrossFileDummyAgent

        return CrossFileDummyAgent(seed=seed)
    if name == "dirtyfile":
        from agents.dummy_agent import DirtyFileDummyAgent

        return DirtyFileDummyAgent(seed=seed)
    if name == "forget":
        from agents.dummy_agent import ForgetDummyAgent

        return ForgetDummyAgent(seed=seed)
    if name == "ignoreforget":
        from agents.dummy_agent import IgnoreForgetDummyAgent

        return IgnoreForgetDummyAgent(seed=seed)
    if name == "okconfig":
        from agents.dummy_agent import OkConfigDummyAgent

        return OkConfigDummyAgent(seed=seed)
    if name == "okamnesia":
        from agents.dummy_agent import OkConfigAmnesiaDummyAgent

        return OkConfigAmnesiaDummyAgent(seed=seed)
    if name == "openkylin":
        from agents.openkylin_adapter import OpenKylinAgent

        return OpenKylinAgent(seed=seed)
    raise SystemExit(
        "未知智能体 {!r}：可选 dummy / bad / confuse / leaky / deepseek / adapter"
        " / sessiondummy / amnesia / rollback / norollback / crossfile / dirtyfile"
        " / forget / ignoreforget / okconfig / okamnesia / openkylin".format(
            name
        )
    )


# ---------------------------------------------------------------------------
# run 子命令
# ---------------------------------------------------------------------------


def build_audit_collector(store, audit_flag, replay, workspace):
    """根据 --audit 参数构造 OS 审计采集器；未启用返回 None。

    audit_flag: 逗号分隔的审计源（journal / process / filesystem / replay）。
    replay: 可选离线审计流 NDJSON 文件路径（--audit-replay）。
    """
    if not audit_flag and not replay:
        return None
    from memory_bench.audit import AuditCollector
    from memory_bench.audit.sources import (
        AuditdSource,
        FileSystemDiffSource,
        JournalSource,
        NdjsonReplaySource,
        ProcessSource,
    )

    enabled = {s.strip() for s in (audit_flag or "").split(",") if s.strip()}
    if not enabled and replay:
        enabled = {"replay"}

    collector = AuditCollector(store)
    has_source = False

    def _activate(source):
        nonlocal has_source
        if isinstance(source, FileSystemDiffSource):
            source.begin()
        collector.add_source(source)
        has_source = True

    if "journal" in enabled:
        _activate(JournalSource(since=None, lines=300))
    if "auditd" in enabled:
        _activate(AuditdSource())
    if "process" in enabled:
        _activate(ProcessSource())
    if "filesystem" in enabled:
        _activate(FileSystemDiffSource(workspace))
    if "replay" in enabled:
        _activate(NdjsonReplaySource(replay))
    if not has_source:
        return None
    return collector


def cmd_run(args: argparse.Namespace) -> int:
    scenario = load_scenario_with_fallback(args.scenario)

    outdir = args.outdir if args.outdir else os.path.join("out", scenario.id)
    outdir = os.path.abspath(outdir)
    workspace = (
        args.workspace
        if args.workspace
        else os.path.join(outdir, "workspace")
    )
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)

    store = EvidenceStore(os.path.join(outdir, "evidence.ndjson"))
    agent = make_agent(args.agent, args.seed)
    audit_collector = build_audit_collector(
        store=store,
        audit_flag=args.audit,
        replay=args.audit_replay,
        workspace=workspace,
    )

    result = Orchestrator(
        scenario=scenario,
        agent=agent,
        store=store,
        workspace=Path(workspace),
        seed=args.seed,
        audit_collector=audit_collector,
    ).run()

    print("== memory-bench-openkylin M2 ==")
    print("场景：{}（维度：{}） 智能体：{}  seed={}".format(
        scenario.name, scenario.dimension, agent.name, args.seed
    ))
    print("证据总数：{}  按类型：{}".format(
        result.summary["ev_total"], result.summary["ev_by_type"]
    ))
    print("一致性检查：PASS={} FAIL={} WARN={} N/A={}".format(
        result.summary["checks_pass"],
        result.summary["checks_fail"],
        result.summary["checks_warn"],
        result.summary["checks_na"],
    ))
    for check in result.checks:
        print("  [{}] {} {}".format(
            check.status.value, check.rule, check.message
        ))

    from memory_bench.report.scoring import build_scoring, format_scoring

    print("\n---- 多维能力评分 ----")
    print(format_scoring(build_scoring(result.checks, scenario.dimension)))

    manifest_path = os.path.join(outdir, "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        print("\n---- 评测追踪 ----")
        print("run_id：{}".format(manifest.get("run_id")))
        print("可复现指纹：{}".format(manifest.get("repro_fingerprint")))
        env = manifest.get("env", {})
        print("环境：{}｜git={}（{}）".format(
            env.get("python"),
            env.get("git_commit"),
            "dirty" if env.get("git_dirty") else "clean",
        ))

    print("报告 HTML：{}".format(result.report_paths.get("html")))
    print("证据 NDJSON：{}".format(result.store_path))
    return 0


# ---------------------------------------------------------------------------
# bench 子命令（seed 扰动鲁棒性）
# ---------------------------------------------------------------------------


def cmd_bench(args: argparse.Namespace) -> int:
    from memory_bench.robustness import (
        aggregate_matrix,
        run_seed_matrix,
        write_matrix_summary,
    )

    scenario = load_scenario_with_fallback(args.scenario)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    def factory(seed: int):
        return make_agent(args.agent, seed)

    outdir = args.outdir if args.outdir else "out/bench"
    audit_sources = [s.strip() for s in args.audit.split(",") if s.strip()] or None
    results = run_seed_matrix(
        scenario=scenario,
        agent_factory=factory,
        seeds=seeds,
        outdir=outdir,
        agent_name=args.agent,
        audit_sources=audit_sources,
        audit_replay=args.audit_replay,
    )
    aggregate = aggregate_matrix(results)
    matrix_path = write_matrix_summary(outdir, aggregate)

    print("== memory-bench-openkylin seed 扰动鲁棒性 ==")
    print("场景：{}（维度：{}） 智能体：{}  seeds={}".format(
        scenario.name, scenario.dimension, args.agent, seeds
    ))
    summary = aggregate["summary"]
    print("seed 数量：{}  PASS 均值：{:.3f}  PASS 标准差：{:.3f}  判定：{}".format(
        summary["seed_count"],
        summary["mean_pass_ratio"],
        summary["pass_ratio_std"],
        summary["verdict"],
    ))
    print("\n规则级稳定率（出现次数最多的状态占比）：")
    for rule, info in sorted(aggregate["rules"].items()):
        print("  {:<32} 主状态={:<6} 稳定率={:.3f} 分布={}".format(
            rule, info["dominant"], info["stability"], info["distribution"]
        ))

    import glob as _glob

    print("\n---- 评测追踪（逐 seed manifest）----")
    for manifest_path in sorted(
        _glob.glob(os.path.join(outdir, "*__*", "seed-*", "manifest.json"))
    ):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        overall = (manifest.get("scoring") or {}).get("overall") or {}
        print("  {} : seed={} 评分={} 指纹={}".format(
            os.path.basename(os.path.dirname(manifest_path)),
            manifest.get("seed"),
            overall.get("score"),
            manifest.get("repro_fingerprint"),
        ))

    print("\n聚合报告：{}".format(matrix_path))
    return 0


# ---------------------------------------------------------------------------
# serve 子命令
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    docroot = os.path.abspath(args.dir)
    if not os.path.isdir(docroot):
        print("报告目录不存在：{}".format(docroot), file=sys.stderr)
        return 1
    handler = lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=docroot, **kw)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print("memory-bench-openkylin M1 报告服务")
    print("服务目录：{}".format(docroot))
    print("访问地址：http://127.0.0.1:{}/".format(args.port))
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-bench-openkylin",
        description="openKylin 智能体长期记忆自动化评测 Benchmark（M1）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行一个评测场景")
    p_run.add_argument(
        "--scenario",
        default="demo_retention",
        help="内置场景 demo_retention 等，或 JSON 文件路径",
    )
    p_run.add_argument(
        "--agent",
        default="dummy",
        choices=[
            "dummy",
            "bad",
            "confuse",
            "leaky",
            "deepseek",
            "adapter",
            "sessiondummy",
            "amnesia",
            "rollback",
            "norollback",
            "crossfile",
            "dirtyfile",
            "forget",
            "ignoreforget",
            "okconfig",
            "okamnesia",
            "openkylin",
        ],
        help="评测智能体：dummy（好）/ bad / confuse / leaky（坏变体）/ deepseek（真实 LLM）"
        " / adapter（外部智能体）/ sessiondummy / amnesia（跨会话变化体）/"
        " rollback / norollback（冲突回滚变化体）/ crossfile / dirtyfile（交叉文件变化体）/"
        " forget / ignoreforget（遗忘指令变化体）/ okconfig / okamnesia（openKylin 配置变化体）"
        " / openkylin（openKylin 智能体框架适配器）",
    )
    p_run.add_argument(
        "--audit",
        default="",
        help="启用 OS 侧审计证据采集（逗号分隔）：journal / auditd / process / filesystem / replay",
    )
    p_run.add_argument(
        "--audit-replay",
        default=None,
        help="离线审计流 NDJSON 文件路径（配合 --audit replay），用于无系统权限环境评测重放",
    )
    p_run.add_argument("--seed", type=int, default=42, help="随机种子")
    p_run.add_argument("--outdir", default=None, help="输出目录（默认 out/<scenario_id>）")
    p_run.add_argument("--workspace", default=None, help="工作区目录")
    p_run.set_defaults(func=cmd_run)

    p_bench = sub.add_parser("bench", help="seed 扰动鲁棒性：多种子重复运行并聚合")
    p_bench.add_argument(
        "--scenario",
        default="demo_retention",
        help="内置场景或 JSON 文件路径",
    )
    p_bench.add_argument(
        "--agent",
        default="dummy",
        choices=[
            "dummy",
            "bad",
            "confuse",
            "leaky",
            "deepseek",
            "adapter",
            "sessiondummy",
            "amnesia",
            "rollback",
            "norollback",
            "crossfile",
            "dirtyfile",
            "forget",
            "ignoreforget",
            "okconfig",
            "okamnesia",
            "openkylin",
        ],
        help="评测智能体",
    )
    p_bench.add_argument(
        "--seeds",
        default="1,2,3,4,5",
        help="逗号分隔的 seed 列表（默认 1,2,3,4,5）",
    )
    p_bench.add_argument(
        "--audit",
        default="",
        help="启用 OS 侧审计证据采集（逗号分隔）：journal / auditd / process / filesystem / replay",
    )
    p_bench.add_argument(
        "--audit-replay",
        default=None,
        help="离线审计流 NDJSON 文件路径（配合 --audit replay）",
    )
    p_bench.add_argument(
        "--outdir",
        default="out/bench",
        help="聚合输出目录（默认 out/bench）",
    )
    p_bench.set_defaults(func=cmd_bench)

    p_serve = sub.add_parser("serve", help="HTTP 静态服务报告输出目录")
    p_serve.add_argument("--dir", default="out", help="报告目录（默认 out）")
    p_serve.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())