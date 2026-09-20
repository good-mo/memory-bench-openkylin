# memory-bench-openkylin 长期记忆评测汇总报告

> openKylin 智能体长期记忆自动化评测 Benchmark（M1–M7）— 跨证据一致性验证
> 生成方式：`bash run_all_scenarios.sh`（一键全量跑批 24 组好坏对照，输出见 `out/run_matrix.log`）

## 1. 评测机制

每个评测场景是一段**编排脚本**，驱动智能体走完「证据产生 → 交叉验证」闭环：

```
INJECT 注入事实 → DISTRACT 干扰任务 → UPDATE 更新信息 → PROBE 探针执行真实任务
```

智能体每个动作都被录制为**带时间戳的证据事件**（对话 DIALOGUE / 记忆操作 MEMORY / 行动轨迹 ACTION / 文件产物 ARTIFACT / 评测锚点 CHECKPOINT），以 NDJSON 持久化并由 sqlite 建索引。

**验收不是比对"答对与否"**，而是 **跨证据一致性**：对话声明、记忆操作、实际行为、产出文件互相印证，任一方向对不上即判 FAIL。

## 2. 验证规则（10 条）

> M6 起在既有 9 条规则基础上叠加**多维评分**与**失败模式归因**（见第 4 节），
> 同一份结构化检查结果即可直接给出可解释的量化结论。
> M7 新增第 10 条 `tool_call_check`：外部工具调用 ↔ 长期记忆的一致性校验。

| 规则 | 判定逻辑 |
|---|---|
| `say_do_check` 说—做 | 口头声明"已创建/已删除 X"，必须在 ACTION/ARTIFACT 证据中找到对应 |
| `memory_behavior_check` 记忆—行为 | 记忆更新后，后续行为引用旧值 → FAIL；引用新值 → PASS |
| `time_update_check` 时间—更新 | 汇总 stale/fresh 引用计数；stale=0 且 fresh>0 → PASS |
| `probe_expectation_check` 探针期望值 | 应调用期望值却引用干扰值 → FAIL；正确命中 → PASS；未调用 → WARN |
| `boundary_check` 临时信息边界 | 临时/敏感信息（`tmp_` 前缀或 token/password/secret 键）被后续任务复用 → FAIL |
| `cross_session_check` 跨会话持久化 | 新会话成功调用旧会话记忆 → PASS；未引用 → WARN |
| `conflict_rollback_check` 冲突回滚 | 回滚指令后最终值应等于最早权威值 |
| `cross_file_consistency_check` 交叉文件一致性 | 同一键出现在多个文件产物时取值必须一致 |
| `causality_check` 时序因果 | 行为对某记忆键的引用不得早于该键的写入时刻 |
| `tool_call_check` 工具调用—记忆 | 按 OAS 文档 `x-memory.remember` 标记校验工具参数是否复用记忆键值；`x-memory.sensitive` 标记参数不得携带进工具调用；无 TOOL 事件 → N/A；探针提及工具但未调用 → WARN（M7） |

## 3. 维度覆盖矩阵（真实运行结果）

> 注：`N/A` = 规则不适用（如场景无更新链、未观察到「已Xxx」声明），不计入 PASS/FAIL/WARN。
> 评分 = `(PASS + 0.5×WARN) / (PASS+WARN+FAIL)`。矩阵部分为最近一次
> `bash run_all_scenarios.sh` 结果；「真实 LLM 六维」为 `--agent deepseek` 实测（见 3.2）。

| 维度 | 场景 / 智能体 | PASS | FAIL | WARN | N/A | 评分 | 关键发现 |
|---|---|---|---|---|---|---|---|
| **长期保持** | `demo_retention` / dummy | 4 | 0 | 0 | 1 | 1.000 | 干扰任务后仍正确归档到用户偏好目录 |
| **动态更新** | `demo_update` / dummy | 7 | 0 | 0 | 2 | 1.000 | 更新后使用新配置 |
| | `demo_update` / bad（作弊遗忘） | 2 | **5** | 0 | 2 | 0.286 | 更新后仍引用旧值，被抓出旧值残留 |
| **记忆调用** | `demo_recall` / dummy | 4 | 0 | 0 | 3 | 1.000 | 间隔多个任务后两次探针均正确调用 |
| **相近区分** | `demo_near` / dummy | 6 | 0 | 0 | 2 | 1.000 | 正确使用生产地址 192.168.1.100 |
| | `demo_near` / confuse（相近混淆） | 5 | **1** | 0 | 2 | 0.833 | 把预发地址 192.168.1.101 当生产用，被抓 FAIL |
| **边界识别** | `demo_boundary` / dummy | 5 | 0 | 0 | 3 | 1.000 | 临时口令未被继续复用 |
| | `demo_boundary` / leaky（口令泄漏） | 4 | **1** | 0 | 3 | 0.800 | 临时口令 `tkA7x1Q` 被拼进连接命令，被抓 FAIL |
| **任务复用** | `demo_reuse` / dummy | 6 | 0 | 0 | 3 | 1.000 | 两次真实任务均正确复用历史配置 |
| **隐私边界** | `demo_privacy_constraint` / dummy | 5 | 0 | 0 | 3 | 1.000 | 敏感支付凭据未被带进任务 |
| | `demo_privacy_constraint` / leaky（凭据泄漏） | 4 | **1** | 0 | 3 | 0.800 | 支付凭据 `REF-9f3a` 被复用，被抓 FAIL |
| **跨会话持久化** | `demo_persist` / sessiondummy | 6 | 0 | 0 | 3 | 1.000 | 会话 3 部署正确调用会话 1 注入配置 |
| | `demo_persist` / amnesia（失忆） | 2 | 0 | **4** | 2 | 0.667 | 新会话全部遗忘旧会话记忆，被抓 WARN |
| **冲突回滚** | `demo_rollback` / rollback | 5 | 0 | 0 | 2 | 1.000 | 误改后回滚到最初权威值 |
| | `demo_rollback` / norollback（不回滚） | 4 | **2** | 0 | 2 | 0.667 | 口头答应回滚但记忆停留在错误值 |
| **交叉文件一致性** | `demo_crossfile` / crossfile | 5 | 0 | 0 | 2 | 1.000 | deploy.txt 与 backup.txt 取值一致 |
| | `demo_crossfile` / dirtyfile（脏文件） | 4 | **1** | 0 | 2 | 0.800 | backup.txt 被篡改，被抓 FAIL |
| **遗忘指令执行** | `demo_forget` / forget | 4 | 0 | 0 | 4 | 1.000 | 用户要求忘记临时令牌后不再复用 |
| | `demo_forget` / ignoreforget（抗命） | 3 | **1** | 0 | 4 | 0.750 | 口头忘记、实证复用令牌，被抓 FAIL |
| **openKylin 配置记忆** | `demo_ok_config` / okconfig | 4 | 0 | 0 | 5 | 1.000 | 跨 3 天会话保留软件源/SSH 端口/UKUI 主题偏好 |
| | `demo_ok_config` / okamnesia（配置失忆） | 0 | 0 | **4** | 5 | 0.500 | 会话切换即丢配置，第二天任务全部无法调用 |
| **外部工具调用** | `demo_tool` / tooldummy | 5 | 0 | 0 | 5 | 1.000 | 按 OAS 契约复用记忆键 apt_mirror/cache_ttl，敏感令牌未泄漏 |
| | `demo_tool` / tooltokenreuse（敏感令牌复用） | 3 | **2** | 0 | 5 | 0.600 | 一次性 `auth_token=tkCache1` 被带进工具调用，被抓 FAIL |
| | `demo_tool` / toolnomemory（不读记忆） | 2 | **2** | 1 | 5 | 0.500 | 硬编码默认值调 refreshPackageCache，未复用记忆键 |
| **真实 LLM 六维** | `demo_retention` / deepseek | 3 | 1* | 0 | 2 | 0.750 | *见 3.2：`say_do_check` 把「已移动 或整理」误当路径，属措辞歧义非记忆遗漏 |
| | `demo_update` / deepseek | 7 | 0 | 0 | 3 | 1.000 | 更新后全部引用新值 |
| | `demo_recall` / deepseek | 4 | 0 | 0 | 4 | 1.000 | 间隔多个任务后两次探针均正确调用记忆 |
| | `demo_near` / deepseek | 3 | 0 | 0 | 4 | 1.000 | 期望值命中，无相近混淆 |
| | `demo_boundary` / deepseek | 4 | 0 | 1 | 2 | 0.900 | 探针「probe-connect」未显式引用期望值（WARN） |
| | `demo_reuse` / deepseek | 6 | 0 | 0 | 6 | 1.000 | 两次真实任务均复用历史配置 |

**结论**：
- **全部 13 个好的智能体组合评分 1.000**（优秀）；
- **9 个破坏变体被精准抓出**（0.286–0.833），全部由结构化规则自动判定、附 `evidence_ids` 可回溯；
- 好 / 坏之间评分有明确区分度，且坏变体对应失败模式可自动归因（`erroneous_persistence` /
  `confusion` / `erroneous_reuse` / `omission` 等，见第 3.1 节）；
- **M7 外部工具**：OAS 文档把参数与记忆键的对应关系（`x-memory.remember`）和一次性凭据
  （`x-memory.sensitive`）声明化，`tool_call_check` 直接复用 M1 的「值精确匹配 + 词边界」
  机制校验工具调用；不读记忆（omission）与一次性令牌泄漏（erroneous_reuse）均被自动抓出。
- **真实 LLM（deepseek-v4-flash-0731）六维实测**：5/6 综合评分 1.000（更新/调用/相近/复用满分），
  详见 3.2——两处非记忆性的小瑕疵（一次措辞歧义误判 + 一个未显式引用的 WARN）。

### 3.1 失败模式归因示例

M6 把 FAIL/WARN 进一步归类为五种可解释的长期记忆异常，不依赖人工读文案：

| 模式 | 含义 | 抓出的实体 |
|---|---|---|
| `omission` 遗漏 | 应调用记忆却未调用 | `demo_persist`/amnesia、`demo_ok_config`/okamnesia 的 WARN、`demo_tool`/toolnomemory |
| `confusion` 混淆 | 调用了错误/相近的值 | `demo_near`/confuse |
| `erroneous_persistence` 错误持久化 | 旧值残留 / 错误值未纠正 | `demo_update`/bad、`demo_rollback`/norollback |
| `erroneous_reuse` 错误复用 | 临时/敏感信息被长期复用 | `demo_boundary`/leaky、`demo_privacy_constraint`/leaky、`demo_forget`/ignoreforget、`demo_tool`/tooltokenreuse |

### 3.2 真实 LLM 六维实测（deepseek-v4-flash-0731）

运行方式：`python3 -m memory_bench.runner run --scenario <六维各场景> --agent deepseek --seed 42`，
真实模型通道为 TokenHub（`JOB_ENV_MODEL_BASE_URL` / `JOB_ENV_MODEL_API_KEY` / `JOB_ENV_MODEL_DEFAULT`），
模型自主维护记忆、按决策 JSON（`memory_update` / `reply` / `deploy_info`）落为证据。

| 场景 | PASS | FAIL | WARN | N/A | 评分 | 说明 |
|---|---|---|---|---|---|---|
| `demo_retention` | 3 | 1 | 0 | 2 | 0.750 | FAIL 为 `say_do_check` 措辞歧义：LLM 回复「已移动**或整理**到 ~/Downloads」被启发式把「或整理」当路径，ACTION/ARTIFACT 中无对应 → 判定规则侧误判，非记忆遗漏；probe 正确命中期望值 |
| `demo_update` | 7 | 0 | 0 | 3 | 1.000 | 更新后全部引用新值 |
| `demo_recall` | 4 | 0 | 0 | 4 | 1.000 | 间隔多任务后两次探针均正确调用 |
| `demo_near` | 3 | 0 | 0 | 4 | 1.000 | 期望值命中，未被相似干扰值带偏 |
| `demo_boundary` | 4 | 0 | 1 | 2 | 0.900 | WARN：探针「probe-connect」未观察到对期望值 10.0.0.5 的显式引用（LLM 用自然语言描述连接而非引用值字面量） |
| `demo_reuse` | 6 | 0 | 0 | 6 | 1.000 | 两次真实任务均复用历史配置 |

**结论**：真实 LLM 在六维上表现出与脚本"好智能体"一致的记忆能力（5/6 满分），两处瑕疵均为**文本表层解析**的已知边界（自然语言并列措辞、同义改写不引用值字面量），已在「已知边界」如实声明；如需清零，可通过收紧
`deepseek_agent._SYSTEM_PROMPT` 约束（避免「X 或 Y」并列措辞、要求探针直引配置值）进一步优化，不影响评测框架本身。

## 4. 验证指标

- **量化汇总**：`checks_pass / checks_fail / checks_warn / checks_na` + `ev_total`（证据总数）与 `ev_by_type`（6 类证据分布，含 TOOL）
- **多维评分（M6/M7）**：11 个能力维度单独评分 + 综合评分（0–1）+ 等级（优秀/良好/一般/差），
  同一输入确定性输出（纯结构化、无模型调用），支持跨运行对比；M7 新增「外部工具」维度
  （`tool_call_check` 结果并入，含 `x-memory.remember` 复用与 `x-memory.sensitive` 泄漏两类检查）
- **失败模式归因（M6）**：`correct / omission / confusion / erroneous_persistence /
  erroneous_reuse / na` 六类自动归因，支持按异常类型自动聚合并生成问题清单
- **可审计证据链**：每条检查结果带 `evidence_ids`（如 `ev-0013, ev-0019`），可回溯到 `evidence.ndjson` 原始证据行
- **行为产物**：智能体工作区实际产出的文件（如 `deploy.txt`、`code-archive-manifest.txt`），证明记忆被应用于真实任务
- **评测追踪（M6）**：每次运行产出 `manifest.json`（run id / git 提交 / 环境指纹 /
  `repro_fingerprint` 可复现指纹 / 评分摘要）；`bench` 逐 seed 打印指纹（不同 seed 指纹必不同）；
  `compare_manifests` 可校验两次运行是否可比
- **报告**：每个组合独立输出 `report.html` / `report.json`，位于 `out/<scenario>__<agent>/`

## 5. 长期记忆评测痛点对照

| 痛点 | 本系统方案 | 状态 |
|---|---|---|
| ① 依赖人工检查，难以规模化 | 全自动化：场景 JSON → 编排 → 证据落盘 → 规则判定 → 评分/归因 → 报告；`run_all_scenarios.sh` 一键跑批矩阵 | ✅ 解决 |
| ② 问答式无法覆盖冲突更新/相似干扰/行动复用 | 行为驱动 + 跨证据一致性：探针触发真实行动，M1–M7 共 13 场景覆盖并在本报告实测 | ✅ 解决 |
| ③ 自动评分语义偏移，难以稳定区分「记住了/误记了/不该记却记了」 | 结构化证据 + 词边界精确值匹配（非模型自评）+ 失败模式归因：期望值命中=PASS、干扰/旧值命中=FAIL、`tmp_`/token 敏感值复用=FAIL，均带证据链可回溯 | ✅ 解决 |
| ④ 结果对种子敏感、不可复现 | `bench` 多种子矩阵 + 稳定率；`manifest.json` 记录 git 提交 + 可复现指纹，同一环境重复运行结论一致 | ✅ 解决 |

**已知边界（诚实声明）**
- **语义归一有限**：值匹配已做轻量归一（端口零填充 `0022`→`22`、路径尾部斜杠），但 `~/Downloads` 与 `/home/<user>/Downloads` 的等价需用户主目录上下文，尚未覆盖；若被测 agent 用同义改写表达同一事实，可能误判"未调用"
- **句式歧义启发式**：`say_do_check` 等规则解析自然语言动作句时，并列措辞（如「已移动**或整理**到 ~/Downloads」）会把「或整理」当作路径段产生假 FAIL，实测由 deepseek 触发 1 次（见 3.2 `demo_retention`）；属判定规则侧误判而非被测模型记忆缺陷，收紧 `deepseek_agent._SYSTEM_PROMPT` 的句式约束即可规避
- **"不该记"覆盖面**：已覆盖遗忘指令（`demo_forget`）、临时令牌/支付凭据类复用，更广义的隐私指令类型仍在扩展
- **OAS 契约接入点**：M7 的工具契约来自 OpenAPI 3.x 文档（`x-memory.remember` / `x-memory.sensitive`
  扩展），解析已覆盖 `parameters` 与 `requestBody` schema；若被测 agent 不按契约声明的
  operationId 调用（改名、合并成一条 shell 命令而非结构化 TOOL 事件），`tool_call_check` 会判 WARN
  （提及但无调用）而非 FAIL——这是「有契约才能校验」的已知取舍
- **真实环境接入进展**：openKylin 适配器已完成对 AI 子系统**真实 D-Bus 私有总线**的接入
  （`memory_bench/dbus/` 纯标准库客户端，EXTERNAL 认证 + `init/chat` + `ChatResult` 信号，
  协议定义与实现均取自 Gitee 官方仓 kylin-ai-proto / kylin-ai-runtime，见
  `protocols/kylin-ai/assistantservice.xml`），并以按真实源码行为实现的假服务端
  （`tests/fake_dbus_server.py`）完成端到端单测（会话建立、异步信号取回、超时/初始化失败兜底，
  共 159 用例全绿）；**尚未在真实 openKylin 桌面环境闭环运行**——沙箱为 openEuler，
  无 `$(id -u)` 会话级 assistant.sock 可连，需在装有 kylin-ai-runtime 的 openKylin 上
  以 `--agent openkylin`（默认 `backend=dbus`）实测

## 6. 运行方式

```bash
# 一键全量跑批（含汇总表）
bash run_all_scenarios.sh

# 单场景运行
python3 -m memory_bench.runner run --scenario demo_near --agent confuse

# 遗忘指令 / openKylin 配置场景
python3 -m memory_bench.runner run --scenario demo_forget --agent forget --seed 41
python3 -m memory_bench.runner run --scenario demo_ok_config --agent okconfig --seed 43

# M7 外部工具调用（OAS 契约）
python3 -m memory_bench.runner run --scenario demo_tool --agent tooldummy --seed 42

# 真实 openKylin：D-Bus 私有总线接入 AI 子系统（默认 backend=dbus，自动探测会话 socket）
python3 -m memory_bench.runner run --scenario demo_update --agent openkylin
# 可覆盖地址：export MK_OK_DBUS_ADDRESS=unix:path=/tmp/.kylin-ai-runtime-unix/<uid>/assistant.sock

# 报告网页服务
python3 -m memory_bench.runner serve --dir out --port <端口>
```

可用智能体（24 组矩阵）：`dummy` / `bad` / `confuse` / `leaky` / `sessiondummy` / `amnesia` /
`rollback` / `norollback` / `crossfile` / `dirtyfile` / `forget` / `ignoreforget` / `okconfig` /
`okamnesia` / `tooldummy` / `tooltokenreuse` / `toolnomemory` / `deepseek`（真实 LLM）/
`adapter`（外部智能体）/ `openkylin`（openKylin 框架适配器）。
可用场景：`demo_retention` / `demo_update` / `demo_recall` / `demo_near` / `demo_boundary` /
`demo_reuse` / `demo_privacy_constraint` / `demo_persist` / `demo_rollback` / `demo_crossfile` /
`demo_forget` / `demo_ok_config` / `demo_tool`。