# 全面交接给 Codex（2026-10-28）

从本页起，Decision Hub 的后续开发任务（含代码修改、测试、启动、真实研究、文档）全部由 Codex 负责。之前 `HANDOFF_CODEX.md` 中"Codex 只执行、代码由 Claude 负责"的分工作废；本页与 `DEVELOPMENT_WORKFLOW.md` 冲突时，以 `DEVELOPMENT_WORKFLOW.md` 的开发/发布边界为准，分工以本页为准。

## 1. 先读这些（按顺序）

| 文件 | 内容 |
|---|---|
| `DEVELOPMENT_WORKFLOW.md` | 日常开发入口 `run-dev.ps1`/`stop-dev.ps1`、端口、隔离约束、开发与正式发布的区别 |
| `PROBABILITY_BASE_RATE_PLAN.md` | 基准率修复 P1–P6 的方案和进度（单一进度源，进度只追加） |
| `VERIFICATION.md` | 改动文件清单、已验证项、第 4 节真实验收标准 |
| `CODEX_TASKS_2026-10-28.md` | 已经派发的 5 项只读排查/整理任务（本页第 4 节 A 组） |
| `PROBABILITY_SYMMETRY_DIAGNOSIS.md` | 40/20/40 对称概率的根因 |
| `DIAGNOSIS_AND_SOLUTION.md`、`OPTIMIZATION_AND_EXTENSIONS.md` | 项目整体诊断和长期方向，不是本轮任务 |
| `CODEX_LOG.md` | 历次执行记录，新内容追加，不改写旧节 |

## 2. 当前真实状态（2026-10-28 18:40 核对）

- 开发实例 `data/development/default` **已停止**：supervisor PID 16740、hub-api 29300、Vite 30024 都不存在，18047/5173 无监听；`runtime/processes.json` 仍写 running（最后修改 18:15），属于状态文件残留，原因未查。
- 合约元数据：之前通过设置 API 给开发库写入了 OKX BTC/ETH 元数据。临时客户端 `E:/project/study/codex/_shared/temp/claude/live/dev_client.py`（仓库外，通过私有 launch 文件配对，不打印 token），研究回执在同目录 `research-receipts*.json`。
- 真实 4h 研究（开发库）**两个都失败了**：
  - BTC `run_82740daa898548309bef03c4eada3c7c`：06:58 UTC 开始，07:58:05 UTC 失败，`dsh_host_read_transport_timeout`（worker 日志里还有 `research_baseline_transport_timeout`）。
  - ETH `run_e8c4952c3424404fa03a8823cfa80ba7`：06:58 UTC 开始，07:58:56 UTC 失败，`provider_timeout`。
  - `run_role_outputs` 分别有 131/128 条记录，其中模型输出 57/60 条，**没有一条包含 up/neutral/down 概率**，也没有报告。`run_steps`、`run_calls` 表对这两个 run 为空。
  - 两个都在约 60 分钟时终止，可能是统一的总时限，没有核实。
- `VERIFICATION.md` 第 4 节：第 1 项通过（admission 含 `direction_base_rate`，90 天 2157 样本，阈值与 close window 一致，模型输入里有）；第 2–4 项无法判定。
- 正式发布：没有新的完整构建，也没有正式回执。上一次完整构建失败在 `assessment_basis_immutable_binding_limit`（256 MiB），当前预算余量约 195 KB，详见 `DEVELOPMENT_WORKFLOW.md`"当前已知事实"一节。
- git：工作区有大量未提交改动（10-23 时约 1861 项），一直没有收口。

## 3. 工作约束（沿用，未放宽的不要自行放宽）

- 日常开发走 `DEVELOPMENT_WORKFLOW.md` 的独立开发实例。不写正式目录（`core-live-20260913`、`dsh-live`、`product-local`），不复用或冒充正式回执。
- 不删除历史归档、数据库、备份和以前的失败产物；不靠删历史或调大阈值解决 256 MiB 预算问题。
- 不 git commit、不 push，除非用户另行确认分支和提交方式。
- 研究运行期间不改源码（会改变该 Run 的来源身份）；改 Python 后在队列空闲时 stop 再重启。
- 不在文档、日志、聊天里打印 pairing token、凭据、DSH 认证 URL。
- 结果如实记录：SKIP、失败、未执行都不算通过；推测要标注"推测"。旧失败断言不改成通过。
- 改动前先写排查记录（原件、原因、方案、未核对项），再改代码。
- 进度写回对应文档：基准率进度追加到 `PROBABILITY_BASE_RATE_PLAN.md`，执行记录追加到 `CODEX_LOG.md`。

## 4. 待办（按优先级）

### A. 已派发的排查和整理（见 `CODEX_TASKS_2026-10-28.md`）

1. 两个 4h 研究超时根因：超时发生在哪一步、配置上限在哪个文件哪一行、最后一次成功输出的时间。
2. 开发实例为什么停了：崩溃、被外部结束还是休眠/重启。
3. 状态文档对照表 `STATUS_RECONCILIATION.md`（`docs/v3/STATUS.md`、`00_MASTER_CONTROL.md`、`docs/IMPLEMENTATION_STATUS.md`）。
4. 全量 pytest 基线失败清单，对照 `_shared/temp/claude/selftest/baseline_pytest.txt`。
5. `PAPER_HORIZON_ALIGNMENT_FIX.md` 的落地情况核对。

任务单原来写的"不要重启实例，等 Claude 决定""只读"等限制作废，现在由 Codex 自己决定，但排查结论仍需先落盘再动手修。

### B. 让 4h 研究能跑完（阻塞所有真实验收）

- 根据 A1 的结论修复超时（缩短链路、调整分段时限或修复 host 读取），把修改、原因和测试写进记录。
- 重启开发实例，BTC、ETH 各跑一次 4h 研究，按 `VERIFICATION.md` 第 4 节填写全部记录项。
- `direction_base_rate` 审核原件没有经过真实审核链路（`VERIFICATION.md` 第 3 节）。研究在审核原件校验处报错时先查这里。

### C. 基准率修复收尾（`PROBABILITY_BASE_RATE_PLAN.md`）

- P3 确定性审核：模型概率与基准率差距都在 2pp 内且没有调整理由时，标 `base_rate_only`；偏离超过 15pp 且没有证据引用时，审核退回。
- P4：确认路径幅度取自原件分位数；`action_comparison.py` 不改公式。
- P6：`PerpetualAdvicePanel.tsx` 显示基准率、模型概率、偏离理由和"仅基准率"标记；同步 contracts schema、TS/Py 语义校验和 fixtures。
- 研究到期后核对对照分（P5 `base_rate_comparison`）在结果页可见。
- vitest `perpetualManualPaper.test.ts` 的 6 个 `localStorage is not defined` 失败：先做改动前对照，再决定是否修。

### D. 正式发布（B、C 完成后）

- 确定正式候选，解决归档预算持续增长问题（不能删历史或调大阈值）。
- 先跑独立 manifest 预检，通过后再跑完整构建（约 85 分钟，需要 ≥10 GiB 空闲内存，用 PowerShell 7），然后用 `run-product.ps1` 启动并做真实发布验收。

### E. 总控遗留项（`PROBABILITY_BASE_RATE_PLAN.md` 第 2 节）

T01/T08 六组合 14 天稳定性、T03 事件真实链、T05 条件触发、PAPER ≥10 笔成交与资金费、30 次失败回放、实时评分角色（目前靠手动 `process_due`）、retention 磁盘治理。git 收口需要先找用户确认分支和提交方式。

## 5. 用户偏好（此前对话中确认）

- 2026-10-23 用户原话："不用找我确认，我都同意，需要你把主要性价比高的任务排好优先级完成和自测"。所以正常构建、`run-product.ps1` 启动、真实 BTC/ETH 研究可以直接执行。删除数据、git 提交/强推这类不可逆操作仍要先问。
- 汇报时给出优先级、完成情况和测试输出。
- 回复简洁，结论先行，事实和推测分开写。

## 6. 交接时 Claude 留下的仓库外文件

都在 `E:/project/study/codex/_shared/temp/claude/`，可以读或复用，不会入库：

- `live/dev_client.py`、`live/research-receipts*.json`：开发实例 API 客户端和两个研究的回执。
- `selftest/`：10-23 部分基线 `baseline_pytest.txt`、OKX 冒烟脚本 `live_hourly_smoke.py`。
- `br/`：基准率试算脚本。
