# memory-bench-openkylin

面向 openKylin 操作系统智能体长期记忆能力的自动化评测 Benchmark（M1 骨架）。

## 简介

「证据驱动」的智能体长期记忆评测框架：把对话、记忆操作、行动轨迹、文件产物统一成
带时间戳的证据事件流（NDJSON），再用**跨证据一致性校验**自动判断智能体是否真的记住了、
真的用了、真的更新了。M1 提供证据模型 + 最小 harness + 脚本化 dummy 智能体 +
演示场景 + 报告输出，跑通端到端 demo。

## 背景

- 长期记忆是智能体在操作系统环境中提供持续性服务的关键能力：用户偏好、动态配置、
  历史决策都必须长期保留、按需使用、及时更新。
- 传统评测只看对话答复或单一结果，无法抓出「口头承诺但行为没兑现」的作弊式遗忘。
- 本框架把「说」（DIALOGUE）、「记」（MEMORY）、「做」（ACTION）、「产物」（ARTIFACT）
  全部固化为证据，用三条一致性规则交叉印证，自动生成 PASS / FAIL / WARN 结论。

## M1 范围与模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 证据模型 | `memory_bench/evidence/model.py` | 五类证据事件 + 序列化/校验 |
| 证据存储 | `memory_bench/evidence/store.py` | NDJSON 追加权威存储 + sqlite3 查询索引 |
| 一致性校验 | `memory_bench/evidence/consistency.py` | 说—做 / 记忆—行为 / 时间—更新 三条规则 |
| 场景建模 | `memory_bench/scenarios/types.py` | Scenario / Step / Fact 数据模型与 JSON 加载 |
| 内置场景 | `memory_bench/scenarios/library.py` | retention / dynamic-update 两个演示场景 |
| 沙箱 | `memory_bench/harness/sandbox.py` | 工作区快照 / 回滚 |
| 智能体协议 | `memory_bench/harness/agent.py` | Agent 抽象 + AgentContext 证据发射 |
| 编排器 | `memory_bench/harness/orchestrator.py` | 注入 → 演化 → 探针 → 固化 流程 |
| 报告 | `memory_bench/report/generator.py` | JSON + 自包含 HTML（内联 CSS） |
| CLI | `memory_bench/runner.py` | `run` / `serve` 两个子命令 |
| 演示智能体 | `agents/dummy_agent.py` | DummyAgent（好）/ BadDummyAgent（坏） |
| 测试 | `tests/` | 标准库 unittest，4 个测试文件 28 用例 |

## 快速开始

环境要求：Python 3.8+，**仅标准库**，无需任何第三方依赖、无需虚拟环境。

```bash
# 1. 跑 retention 场景（好智能体，记忆保留维度）
python3 -m memory_bench.runner run --scenario demo_retention --agent dummy --seed 42

# 2. 跑 dynamic-update 场景（好智能体，动态更新维度）
python3 -m memory_bench.runner run --scenario demo_update --agent dummy --seed 7

# 3. 用坏智能体作对比（故意旧值残留，会产出 FAIL 演示）
python3 -m memory_bench.runner run --scenario demo_update --agent bad --seed 7

# 4. 运行全部单元测试
python3 -m unittest discover -s tests -v

# 5.（可选）把报告目录作为 HTTP 静态服务浏览
python3 -m memory_bench.runner serve --dir out --port 8000
```

运行产物位于 `out/<scenario_id>/`：

- `evidence.ndjson`：全部证据事件（权威来源）
- `evidence.db`：sqlite3 查询索引（自动建表）
- `report.json`：结构化报告
- `report.html`：自包含可视化报告（内联 CSS，无外部资源）
- `workspace/`：智能体工作区（产出文件）

## 目录结构

```
memory-bench-openkylin/
├── README.md
├── pyproject.toml              # 最小元数据（>=3.8，仅标准库）
├── memory_bench/
│   ├── evidence/               # 证据模型 / 存储 / 一致性校验
│   ├── scenarios/              # 场景建模 / 内置场景
│   ├── harness/                # 沙箱 / 智能体协议 / 编排器
│   ├── report/                 # 报告生成
│   └── runner.py               # CLI（run / serve）
├── agents/dummy_agent.py       # 演示智能体（dummy / bad）
├── scenarios/*.json            # 演示场景定义（运行时优先磁盘文件）
├── tests/                      # unittest 测试
└── examples/evidence_sample.ndjson  # 人工示例证据
```

## 证据事件类型

| 类型 | 说明 | 示例 content |
|------|------|--------------|
| `DIALOGUE` | 对话 | `{"text": "请记住：server_ip=192.168.1.100"}` |
| `MEMORY` | 记忆操作 | `{"op": "WRITE", "key": "server_ip", "value": "192.168.1.100"}` |
| `ACTION` | 行动轨迹 | `{"command": "ssh kylin@192.168.1.100 -p 22", "path": ...}` |
| `ARTIFACT` | 文件产物 | `{"path": "deploy.txt", "content": "ip=192.168.1.100"}` |
| `CHECKPOINT` | 评测锚点 | `{"phase": "scenario_start"}` |

所有事件统一字段：`ev_id / type / ts(ISO8601 UTC) / session / task / source(USER|AGENT|HARNESS) / content / metadata`。

## 三条一致性规则

1. **say_do_check（说—做一致性）**：扫描 AGENT 对话中的「已备份/已创建/已删除/已安装/已复制/已移动/已写入/已修改」类声明，
   提取目标路径，到 ACTION/ARTIFACT 证据中交叉验证。找到即 PASS，找不到即 FAIL。
2. **memory_behavior_check（记忆—行为一致性）**：对每个发生值变化的记忆键 `(key, old, new)`，
   检查更新之后的行为是否仍引用旧值：引用旧值 → FAIL（残留）；引用新值 → PASS；无引用 → WARN。
3. **time_update_check（时间—更新一致性）**：整体统计更新后旧值（stale）与新值（fresh）被引用的次数，
   有残留即 FAIL，无残留且有新值引用即 PASS，完全无引用即 WARN。

匹配采用词边界判定，避免 `22` 误命中 `2222`。

## Agent 接入约定

MEMORY 事件 `content` 固定结构（一致性规则解析的基础）：

```json
{"op": "WRITE|READ|UPDATE|DELETE|QUERY", "key": "<字段名>", "value": "<值>", "note": "<备注>"}
```

- `UPDATE` 的 `value` 一律是**新值**。
- `key` 形如 `server_ip` / `port` / `download_dir` / `editor`。
- 智能体通过 `ctx.emit(type, content, task=...)` 把每个动作都固化为证据。

## 下一步规划（M2–M5）

| 里程碑 | 内容 |
|--------|------|
| M2 | 支持真实 LLM Agent（openKylin CLI/API 封装），证据协议稳定化 |
| M3 | 场景库扩充：多轮会话、跨会话持久化、冲突回滚、权限类记忆 |
| M4 | 更多一致性规则：交叉文件一致性、时序因果性、遗忘曲线统计 |
| M5 | 规模化跑批与指标聚合、seed 扰动鲁棒性分析、与 CI 集成 |

## 许可与说明

仅供 openKylin 智能体评测研究使用。仅用 Python 标准库实现，可在任意 Python 3.8+ 环境直接运行。