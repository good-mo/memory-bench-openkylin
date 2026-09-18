"""CLI 入口：`python3 -m memory_bench.runner run ...` / `serve ...`。"""

import argparse
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
    raise SystemExit(
        "未知智能体 {!r}：可选 dummy / bad / confuse / leaky / deepseek / adapter".format(
            name
        )
    )


# ---------------------------------------------------------------------------
# run 子命令
# ---------------------------------------------------------------------------

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

    result = Orchestrator(
        scenario=scenario,
        agent=agent,
        store=store,
        workspace=Path(workspace),
        seed=args.seed,
    ).run()

    print("== memory-bench-openkylin M1 ==")
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
    print("报告 HTML：{}".format(result.report_paths.get("html")))
    print("证据 NDJSON：{}".format(result.store_path))
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
        help="内置场景 demo_retention / demo_update，或 JSON 文件路径",
    )
    p_run.add_argument("--agent", default="dummy", choices=["dummy", "bad", "confuse", "leaky", "deepseek", "adapter"],
                       help="评测智能体：dummy（好）/ bad / confuse / leaky（坏变体）/ deepseek（真实 LLM）/ adapter（外部智能体，通过 MB_ADAPTER_* 环境变量配置后端）")
    p_run.add_argument("--seed", type=int, default=42, help="随机种子")
    p_run.add_argument("--outdir", default=None, help="输出目录（默认 out/<scenario_id>）")
    p_run.add_argument("--workspace", default=None, help="工作区目录")
    p_run.set_defaults(func=cmd_run)

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