# memory-bench-openkylin

面向 openKylin 操作系统智能体长期记忆能力的自动化评测 Benchmark（M1–M6）。

## 简介

「证据驱动」的智能体长期记忆评测框架：把对话、记忆操作、行动轨迹、文件产物统一成
带时间戳的证据事件流（NDJSON），再用**跨证据一致性校验**自动判断智能体是否真的记住了、
真的用了、真的更新了。M1 提供证据模型 + 最小 harness + 脚本化 dummy 智能体 +
演示场景 + 报告输出；M2–M6 在此基础上补齐真实 LLM 智能体、场景库扩充、更多
一致性规则、规模化跑批与鲁棒性分析、多维评分与失败模式归因、评测追踪与可复现性
校验、遗忘指令与面向 openKylin 操作系统的配置记忆场景（详见「里程碑状态」）。

## 背景

- 长期记忆是智能体在操作系统环境中提供持续性服务的关键能力：用户偏好、动态配置、
  历史决策都必须长期保留、按需使用、及时更新。
- 传统评测只看对话答复或单一结果，无法抓出「口头承诺但行为没兑现」的作弊式遗忘。
- 本框架把「说」（DIALOGUE）、「记」（MEMORY）、「做」（ACTION）、「产物」（ARTIFACT）
  全部固化为证据，用九条一致性规则交叉印证，自动生成 PASS / FAIL / WARN 结论。

## M1–M5 范围与模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 证据模型 | `memory_bench/evidence/model.py` | 五类证据事件 + 序列化/校验 |
| 证据存储 | `memory_bench/evidence/store.py` | NDJSON 追加权威存储 + sqlite3 查询索引 |
| 一致性校验 | `memory_bench/evidence/consistency.py` | 九条规则（见下） |
| 场景建模 | `memory_bench/scenarios/types.py` | Scenario / Step / Fact 数据模型与 JSON 加载 |
| 内置场景 | `memory_bench/scenarios/library.py` | 十二个演示场景（六维 + 跨会话/回滚/交叉文件 + 遗忘指令/openKylin 配置） |
| 沙箱 | `memory_bench/harness/sandbox.py` | 工作区快照 / 回滚 |
| OS 审计采集 | `memory_bench/audit/` | systemd journal / auditd / 进程快照 / 工作区文件差异 / 离线重放（新） |
| 智能体协议 | `memory_bench/harness/agent.py` | Agent 抽象 + AgentContext 证据发射 + 跨会话状态钩子 |
| 编排器 | `memory_bench/harness/orchestrator.py` | 注入 → 演化 → 探针 → 固化 + 跨会话调度 + 审计联动 |
| 报告 | `memory_bench/report/generator.py` | JSON + 自包含 HTML（内联 CSS，含遗忘曲线 + 能力维度评分卡片） |
| 遗忘曲线 | `memory_bench/report/forgetting.py` | 按时间窗聚合记忆引用强度（M4） |
| 多维评分 | `memory_bench/report/scoring.py` | 10 个能力维度画像 + 5 种失败模式归因 + 综合评分（M6，新） |
| 评测追踪 | `memory_bench/report/tracking.py` | run manifest / 可复现指纹（sha256）/ NDJSON 批次清单 / 可复现性校验（M6，新） |
| 鲁棒性 | `memory_bench/robustness.py` | seed 扰动矩阵与稳定率聚合（M5） |
| CLI | `memory_bench/runner.py` | `run` / `bench` / `serve` 三个子命令 |
| 演示智能体 | `agents/` | Dummy 系列 + DeepSeek + 外部 Adapter + openKylin 适配器 + 遗忘/openKylin 配置变体（见下） |
| 测试 | `tests/` | 标准库 unittest，11 个测试文件 120 用例 |

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

### M3–M5 进阶示例

```bash
# M3 跨会话持久化：会话 3 的"部署"应调用会话 1 注入、后续会话未重现的配置
python3 -m memory_bench.runner run --scenario demo_persist --agent sessiondummy --seed 42

# M3 冲突回滚：server_ip 被误改为 99 后应回滚回权威值 10
python3 -m memory_bench.runner run --scenario demo_rollback --agent rollback --seed 31

# M4 交叉文件一致性：两份部署产物中的同一键取值必须一致
python3 -m memory_bench.runner run --scenario demo_crossfile --agent crossfile --seed 17

# M5 seed 扰动鲁棒性矩阵：同一场景多 seed 运行，聚合稳定率 → out/<id>/__matrix__.json
python3 -m memory_bench.runner bench --scenario demo_recall --agent dummy --seeds 1 2 3 4 5

# M6 遗忘指令：用户要求忘记临时令牌后不得再复用
python3 -m memory_bench.runner run --scenario demo_forget --agent forget --seed 41

# M6 openKylin 配置记忆：跨三天会话保留软件源/SSH 端口/主题偏好
python3 -m memory_bench.runner run --scenario demo_ok_config --agent okconfig --seed 43

# 完整 21 组好坏对照矩阵（每个组合输出综合评分 + 失败模式）
bash run_all_scenarios.sh
```

所有新规则、新智能体清单见「一致性规则（九条）」与「演示智能体」。

运行产物位于 `out/<scenario_id>/`：

- `evidence.ndjson`：全部证据事件（权威来源）
- `evidence.db`：sqlite3 查询索引（自动建表）
- `report.json`：结构化报告（含 `scoring` 多维评分块）
- `report.html`：自包含可视化报告（内联 CSS，无外部资源）
- `manifest.json`：评测追踪（run id / 可复现指纹 / 环境指纹 / 评分摘要）
- `workspace/`：智能体工作区（产出文件）

### M6 多维评分与评测追踪

每次运行在 CLI 与 `report.json` 中输出：

- **综合评分**（0–1）+ 等级（优秀 / 良好 / 一般 / 差）
- **失败模式归因**：把 FAIL/WARN 细分识别为 `omission`（遗漏）、`confusion`（混淆）、
  `erroneous_persistence`（错误持久化）、`erroneous_reuse`（错误复用），区别于
  `correct`（正确）与 `na`（不适用）——不依赖人工读文案，可自动聚合
- **10 个能力维度画像**：记忆保留 / 动态更新 / 记忆调用 / 相近区分 / 边界保密 /
  任务复用 / 跨会话持久化 / 冲突回滚 / 交叉文件一致性 / 时序因果，各自单独评分
- **可复现指纹**：`sha256(场景定义 + 智能体 + seed + 规则版本)`，同一提交、同一参数
  必然产出同一指纹；`bench` 逐 seed 打印指纹与评分，`compare_manifests` 可校验两次
  运行是否可比（可复现性）

```bash
# 批量运行后把全部 manifest 追加入批次清单，供跨运行追踪
python3 - <<'PY'
from memory_bench.report.tracking import (
    record_run, load_manifest, load_run_index, compare_manifests,
)
import glob

index = "out/runs.ndjson"
for path in glob.glob("out/*/manifest.json"):
    record_run(index, load_manifest(path))
print(len(load_run_index(index)), "条运行记录已收录")
PY
```

## 目录结构

```
memory-bench-openkylin/
├── README.md
├── pyproject.toml              # 最小元数据（>=3.8，仅标准库）
├── run_all_scenarios.sh        # 六维 + 扩展场景的 17 组好坏对照一键矩阵
├── .github/workflows/ci.yml    # M5 CI：多版本 Python 跑测试 + 矩阵 + bench
├── memory_bench/
│   ├── evidence/               # 证据模型 / 存储 / 一致性校验（九条规则）
│   ├── audit/                  # OS 侧审计采集层：journal/auditd/进程/文件差异/重放
│   ├── scenarios/              # 场景建模 / 内置场景库
│   ├── harness/                # 沙箱 / 智能体协议 / 编排器（跨会话调度 + 审计联动）
│   ├── report/                 # 报告生成（含遗忘曲线 forgetting.py）
│   ├── robustness.py           # M5 seed 扰动矩阵与稳定率聚合
│   └── runner.py               # CLI（run / bench / serve）
├── agents/dummy_agent.py       # 演示智能体（好/坏系列共 12 个）
├── agents/deepseek_agent.py    # 真实 LLM 智能体（OpenAI 兼容 HTTP）
├── agents/adapter_agent.py     # 通用外部智能体适配器（JSONL 协议）
├── agents/openkylin_adapter.py # openKylin 智能体框架适配器（HTTP/CLI + 审计联动）
├── scenarios/*.json            # 演示场景定义（运行时优先磁盘文件）
├── tests/                      # unittest 测试（86 用例）
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

## 一致性规则（九条）

1. **say_do_check（说—做一致性）**：扫描 AGENT 对话中的「已备份/已创建/已删除/已安装/已复制/已移动/已写入/已修改」类声明，
   提取目标路径，到 ACTION/ARTIFACT 证据中交叉验证。找到即 PASS，找不到即 FAIL。
2. **memory_behavior_check（记忆—行为一致性）**：对每个发生值变化的记忆键 `(key, old, new)`，
   检查更新之后的行为是否仍引用旧值：引用旧值 → FAIL（残留）；引用新值 → PASS；无引用 → WARN。
3. **time_update_check（时间—更新一致性）**：整体统计更新后旧值（stale）与新值（fresh）被引用的次数，
   有残留即 FAIL，无残留且有新值引用即 PASS，完全无引用即 WARN。
4. **probe_expectation_check（探针期望校验）**：对每个带 expected 的 PROBE 步骤校验期望值被引用：
   期望值命中 → PASS；引用其它场景值 → FAIL（相近区分失败）；无引用 → WARN（未调用记忆）。
5. **boundary_check（临时/敏感信息边界）**：被标记为 tmp_/token/password/secret 的记忆
   在后续任务中不得被再次引用：引用 → FAIL；未引用 → PASS。
6. **cross_session_check（跨会话持久化）**：存在多会话的场景中，后续会话应能调用先前
   会话注入、未经本会话重现的记忆（M3）。
7. **conflict_rollback_check（冲突回滚）**：记忆键被错误更新后，最终值应回滚到最早权威值
   （场景含回滚语义时启用，M3）。
8. **cross_file_consistency_check（交叉文件一致性）**：同一记忆键在多个 ARTIFACT 文件中
   取值必须一致（M4）。
9. **causality_check（时序因果性）**：行为对某值的引用不得早于该值被写入记忆的时间（M4）。

匹配采用词边界判定，避免 `22` 误命中 `2222`。

## Agent 接入约定

MEMORY 事件 `content` 固定结构（一致性规则解析的基础）：

```json
{"op": "WRITE|READ|UPDATE|DELETE|QUERY", "key": "<字段名>", "value": "<值>", "note": "<备注>"}
```

- `UPDATE` 的 `value` 一律是**新值**。
- `key` 形如 `server_ip` / `port` / `download_dir` / `editor`。
- 智能体通过 `ctx.emit(type, content, task=...)` 把每个动作都固化为证据。

## OS 侧审计证据采集层（`memory_bench/audit/`）

**动机**：智能体自证（AGENT 主动 emit）可能「口说无凭」。OS 审计采集层
把系统侧的客观行为固化为证据（`source=HARNESS`），与智能体自证交叉印证，
作为九条一致性规则的独立证据来源。审计证据带 `metadata.audit_backend`
标记来源，并可写入同一 `EvidenceStore` 参与 say_do / memory-behavior /
time-update / cross-file / causality 等规则校验。

**可用审计后端**（`memory_bench/audit/sources.py`）：

| 后端 | 命令/数据源 | 产出证据 |
|------|------------|---------|
| `journal` | `journalctl -o json` | ACTION（进程/命令）/ DIALOGUE（日志） |
| `auditd` | `ausearch -m EXECVE -i`，回退 `/var/log/audit/audit.log` | ACTION（被执行的命令） |
| `process` | `ps -eo pid,comm,args`，按关键词过滤 | ACTION（相关进程） |
| `filesystem` | 工作区两次快照 diff | ARTIFACT（新增/修改文件）、ACTION（删除） |
| `replay` | NDJSON 审计流文件（`AuditRecord.to_dict()` 行） | 按行类型翻译 |

所有后端均**优雅降级**：命令缺失/无权限/非 Linux 环境返回空证据并记 DIALOGUE
说明，不中断评测。

**启用方式**（run / bench 通用）：

```bash
# 真实 OS：journal + 进程 + 工作区文件差异
python3 -m memory_bench.runner run --scenario demo_update --agent dummy \
    --audit journal,process,filesystem --seed 7

# 无系统权限环境：回放预采集的审计流
python3 -m memory_bench.runner run --scenario demo_update --agent dummy \
    --audit replay --audit-replay /path/to/audit.ndjson --seed 7
```

旁路：`AuditCollector` 可在编排器外部独立使用（见 `collector.py`），
经 `Orchestrator(audit_collector=...)` 挂载后，每个步骤结束会自动采集一次。

## openKylin 智能体框架适配器（`agents/openkylin_adapter.py`）

**动机**：把 openKylin 智能体操作系统上运行的智能体无缝接入评测。

- `backend="http"`：调用 openKylin 智能体服务 API（OpenAI 兼容或自定义 REST），
  请求体模板替换 `{user_message}` / `{memory_json}` / `{system_prompt}`；
- `backend="process"`：调用 openKylin 智能体 CLI 入口，命令含 `{user_message}`
  以参数填充，否则消息经 stdin 送入；
- 证据翻译复用三模式（json / `MB|...|` 行协议 / text 启发式），缺省引导
  openKylin 智能体打印机器可读行，零解析接入；
- **审计联动**：传入 `audit_collector` 后每次 act 结束自动采集 OS 侧证据，
  让「智能体自证 ↔ OS 客观证据」在同一次交互里交叉印证。

```bash
# HTTP 方式接入 openKylin 智能体服务
export MK_OK_URL=http://127.0.0.1:8080/v1/chat/completions
export MK_OK_TOKEN=xxx
python3 -m memory_bench.runner run --scenario demo_update --agent openkylin \
    --audit journal,process,filesystem

# CLI 方式接入（openKylin 智能体可执行入口）
export MK_OK_BACKEND=process
export MK_OK_CMD="/usr/bin/openkylin-agent {user_message}"
python3 -m memory_bench.runner run --scenario demo_update --agent openkylin
```

配置前缀 `MK_OK_*`：`MK_OK_BACKEND / MK_OK_CMD / MK_OK_URL / MK_OK_METHOD /
MK_OK_TOKEN / MK_OK_TEMPLATE / MK_OK_MODE / MK_OK_TIMEOUT /
MK_OK_SYSTEM_PROMPT / MK_OK_PROMPT_PREFIX / MK_OK_OUTPUT`。
跨会话持久化复用 Agent 基类 `save_state` / `load_state`，可直接参与 M3 跨会话评测。

## 演示智能体

| 智能体 | 能力 | 好/坏对照 |
|--------|------|-----------|
| `dummy` | 完整保留记忆并在后续任务正确引用 | 好 |
| `deepseek` / `adapter` | 真实 LLM / 外部智能体 | 真实 |
| `openkylin` | openKylin 智能体框架适配器（HTTP/CLI + 审计联动） | 真实 |
| `sessiondummy` | 跨会话保留记忆 | 好（M3） |
| `amnesia` | 每会话开始时遗忘先前记忆 | 坏（M3） |
| `rollback` | 冲突后回滚到权威值 | 好（M3） |
| `norollback` | 冲突后不回滚、继续误用错误值 | 坏（M3） |
| `crossfile` | 多文件产物引用同一记忆时保持一致 | 好（M4） |
| `dirtyfile` | 不同文件产物写入相互冲突的值 | 坏（M4） |
| `forget` | 用户要求忘记临时令牌后从记忆删除、不再复用 | 好（M6） |
| `ignoreforget` | 口头答应遗忘却继续复用临时令牌 | 坏（M6） |
| `okconfig` | 跨三天会话保留 openKylin 软件源/SSH 端口/UKUI 主题偏好 | 好（M6） |
| `okamnesia` | 会话切换即清空 openKylin 系统配置记忆 | 坏（M6） |
| `bad` / `confuse` / `leaky` | M1 坏变体：旧值残留 / 相近混淆 / 敏感泄漏 | 坏 |

新场景与智能体可直接放入 `scenarios/*.json` / `agents/`（见 `memory_bench/runner.py`
中 `_BUILTIN` 与 `make_agent` 的注册方式）。

## 里程碑状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | 证据模型 + 最小 harness + 脚本化智能体 + 报告 + 三条规则 | ✅ 已完成 |
| M2 | 真实 LLM Agent（DeepSeek）+ 通用外部智能体适配器 | ✅ 已完成 |
| M3 | 场景库扩充 + 跨会话持久化 + 冲突回滚 | ✅ 已完成 |
| M4 | 交叉文件一致性 + 时序因果性 + 遗忘曲线统计 | ✅ 已完成 |
| M5 | 规模化跑批（bench）+ seed 扰动鲁棒性 + CI | ✅ 已完成 |
| M6 | 多维评分 + 失败模式归因 + 评测追踪 + 遗忘指令 + openKylin 配置场景 | ✅ 已完成 |
| M7 | 开放接入：OAS/OpenAPI 文档标记 + 外部工具（工具调用的长期记忆） | 🚧 规划中 |

## 许可与说明

仅供 openKylin 智能体评测研究使用。仅用 Python 标准库实现，可在任意 Python 3.8+ 环境直接运行。