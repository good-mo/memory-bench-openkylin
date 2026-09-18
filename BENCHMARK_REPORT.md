# memory-bench-openkylin 六维评测汇总报告

> openKylin 智能体长期记忆自动化评测 Benchmark（M1）— 跨证据一致性验证
> 生成方式：`bash run_all_scenarios.sh`（一键全量跑批，输出见 `out/run_matrix.log`）

## 1. 评测机制

每个评测场景是一段**编排脚本**，驱动智能体走完「证据产生 → 交叉验证」闭环：

```
INJECT 注入事实 → DISTRACT 干扰任务 → UPDATE 更新信息 → PROBE 探针执行真实任务
```

智能体每个动作都被录制为**带时间戳的证据事件**（对话 DIALOGUE / 记忆操作 MEMORY / 行动轨迹 ACTION / 文件产物 ARTIFACT / 评测锚点 CHECKPOINT），以 NDJSON 持久化并由 sqlite 建索引。

**验收不是比对"答对与否"**，而是 **跨证据一致性**：对话声明、记忆操作、实际行为、产出文件互相印证，任一方向对不上即判 FAIL。

## 2. 验证规则（5 条）

| 规则 | 判定逻辑 |
|---|---|
| `say_do_check` 说—做 | 口头声明"已创建/已删除 X"，必须在 ACTION/ARTIFACT 证据中找到对应 |
| `memory_behavior_check` 记忆—行为 | 记忆更新后，后续行为引用旧值 → FAIL；引用新值 → PASS |
| `time_update_check` 时间—更新 | 汇总 stale/fresh 引用计数；stale=0 且 fresh>0 → PASS |
| `probe_expectation_check` 探针期望值 | 应调用期望值却引用干扰值 → FAIL；正确命中 → PASS；未调用 → WARN |
| `boundary_check` 临时信息边界 | 临时/敏感信息（`tmp_` 前缀或 token/password/secret 键）被后续任务复用 → FAIL |

## 3. 六维覆盖矩阵（真实运行结果）

| 维度 | 场景 / 智能体 | PASS | FAIL | WARN | 关键发现 |
|---|---|---|---|---|---|
| **长期保持** | `demo_retention` / dummy | 2 | 0 | 1 | 干扰任务后仍正确归档到用户偏好目录 |
| **动态更新** | `demo_update` / dummy | 5 | 0 | 1 | 更新后使用新配置 192.168.2.50:2222 |
| | `demo_update` / bad（作弊遗忘） | 0 | **5** | 1 | 更新后仍引用旧值，被抓出旧值残留 |
| **记忆调用** | `demo_recall` / dummy | 2 | 0 | 2 | 间隔多个任务后两次探针均正确调用 |
| **相近区分** | `demo_near` / dummy | 3 | 0 | 1 | 正确使用生产地址 192.168.1.100 |
| | `demo_near` / confuse（相近混淆） | 2 | **1** | 1 | 把预发地址 192.168.1.101 当生产用，被抓 FAIL |
| **边界识别** | `demo_boundary` / dummy | 2 | 0 | 2 | 临时口令未被继续复用 |
| | `demo_boundary` / leaky（口令泄漏） | 1 | **1** | 2 | 临时口令 `tkA7x1Q` 被拼进连接命令，被抓 FAIL |
| **任务复用** | `demo_reuse` / dummy | 2 | 0 | 2 | 两次真实任务均正确复用历史配置 |

**结论**：6 个维度的"好智能体"全部 PASS；3 个坏变体（作弊遗忘 / 相近混淆 / 口令泄漏）均被对应一致性规则精准抓出 FAIL，证明评测框架具备判别力。

## 4. 验证指标

- **量化汇总**：`checks_pass / checks_fail / checks_warn` + `ev_total`（证据总数）与 `ev_by_type`（5 类证据分布）
- **可审计证据链**：每条检查结果带 `evidence_ids`（如 `ev-0013, ev-0019`），可回溯到 `evidence.ndjson` 原始证据行
- **行为产物**：智能体工作区实际产出的文件（如 `deploy.txt`、`code-archive-manifest.txt`），证明记忆被应用于真实任务
- **报告**：每个组合独立输出 `report.html` / `report.json`，位于 `out/<scenario>__<agent>/`

## 5. 运行方式

```bash
# 一键全量跑批（含汇总表）
bash run_all_scenarios.sh

# 单场景运行
python3 -m memory_bench.runner run --scenario demo_near --agent confuse

# 报告网页服务
python3 -m memory_bench.runner serve --dir out --port <端口>
```

可用智能体：`dummy`（好）、`bad`（作弊遗忘）、`confuse`（相近混淆）、`leaky`（口令泄漏）。
可用场景：`demo_retention` / `demo_update` / `demo_recall` / `demo_near` / `demo_boundary` / `demo_reuse`。