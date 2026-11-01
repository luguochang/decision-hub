# Decision Hub v3 完整任务清单

**总计:** 55 个任务，311 小时，约 11 周

---

## Phase 0: 基线与基础设施 (7 任务, 22h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-000 | Git 基线与分支策略 | 2h | - | 存档分支、v3/main、Git 工作流文档 |
| T-001 | v3 代码骨架与模块划分 | 3h | T-000 | packages/core_v3/ 全部目录、API/Worker/PC 骨架 |
| T-002 | Feature Store 核心实现 | 6h | T-001 | features 表、known_at 查询、OKX 适配器 |
| T-003 | 回放框架与 cassette 存储 | 5h | T-001 | cassettes 表、录制/回放模式 |
| T-004 | v3 检查工具 | 2h | T-001 | tools/v3_check.py (ruff + pyright) |
| T-005 | 合格研究定义与状态机 | 4h | T-001 | ResearchState 枚举、research_runs 表、状态流转 |
| T-006 | 错误码体系与日志结构 | 3h | T-001 | 30 个错误码、DecisionHubError 基类 |
| T-007 | 测试基础设施与 fixture | 4h | T-002, T-005 | conftest.py、db/feature_store/cassette fixtures |

**Phase 0 验收标准:**
- Python 代码 ruff + pyright strict 零错误
- Feature Store 能存取按 known_at 查询的数据
- 回放框架能录制并回放 OKX API 调用
- 研究状态机能正确流转
- 所有测试通过

---

## Phase 1: 架构主干 (6 任务, 34h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-101 | 研究主循环与 DSH 会话管理 | 8h | T-005 | dsh_session.py、主循环、断点续跑 |
| T-102 | PREPARE 阶段实现 | 5h | T-101 | 确定信息集边界 (known_at 上界) |
| T-103 | EVIDENCE 阶段实现 | 6h | T-101, T-002 | 调用 Feature Store、web_search |
| T-104 | 不可变存储与原件归档 | 4h | T-101 | artifacts 表 (SHA256 内容哈希) |
| T-105 | 会话日志追加校验 | 4h | T-101 | session_log_entries 表、prefix_hash 链 |
| T-106 | 研究快照生成 | 3h | T-104 | 自包含 JSON 快照 |

**Phase 1 验收标准:**
- 研究能从 IDLE → PREPARE → EVIDENCE 流转
- DSH 会话能保留上下文并调用
- 原件按内容哈希存储,相同内容不重复
- 会话日志只能追加,不能删除或修改
- 快照可离线读取,不依赖数据库

---

## Phase 2: 普通市场 4h (10 任务, 56h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-201 | FORECAST 阶段 — 历史基率 | 6h | T-103 | 查询历史同类情况、返回方向频率与波动分位数 |
| T-202 | FORECAST 阶段 — LLM 主观估计 | 5h | T-201 | 调用 DSH 给出概率、prompt 包含基率 |
| T-203 | FORECAST 阶段 — 多视角聚合 | 5h | T-202 | 3 个视角独立运行、中位数聚合、分歧度 |
| T-204 | PLAN 阶段实现 | 6h | T-203 | 根据概率生成多空方案 (entry/stop/take_profit/invalidation) |
| T-205 | REVIEW 阶段 — 独立审核 | 5h | T-204 | 独立 DSH 会话审核、检查概率加和/止损/失效条件 |
| T-206 | PUBLISH 阶段实现 | 4h | T-205, T-106 | 生成快照、写 reports 表、状态改为 PUBLISHED |
| T-207 | 报告 HTML 渲染 | 5h | T-206 | 读取快照 JSON → HTML (首屏: 方向/概率/方案) |
| T-208 | PC 研究发起页 | 6h | T-101 | ResearchPage.tsx、调用 POST /api/v3/research |
| T-209 | PC 报告展示页 | 6h | T-207 | ReportPage.tsx、GET /api/v3/reports/{id} |
| T-210 | 4h 普通市场端到端测试 | 8h | T-209 | test_btc_4h_e2e.py、录制真实数据、验证输出、连续 3 次成功 |

**Phase 2 验收标准:**
- BTC/ETH × 4h 能完成完整研究流程 (PREPARE → PUBLISH)
- 报告包含: 方向、概率、多空方案、核心理由
- 概率来自: 历史基率 + LLM 主观估计 + 多视角聚合
- 独立审核能拦截不合理方案
- PC 能发起研究并查看报告
- 端到端测试连续 3 次成功

---

## Phase 3: 24h / 72h 期限 (3 任务, 14h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-301 | 24h 期限支持 | 4h | T-210 | Feature Store 扩展到 24h 窗口、基率查询支持 horizon=24h |
| T-302 | 72h 期限支持 | 4h | T-301 | 72h 特殊处理 (周末流动性、多事件叠加) |
| T-303 | 六组合稳定性测试 | 6h | T-302 | BTC/ETH × 4h/24h/72h 各 10 次、合格率 ≥ 80% |

**Phase 3 验收标准:**
- BTC/ETH × 4h/24h/72h 六个组合都能完成研究
- 稳定性测试合格率 ≥ 80%
- P50 时间 ≤ 20 分钟、P95 ≤ 40 分钟 (Phase 8 优化到 15/30)

---

## Phase 4: 事件研究 (7 任务, 47h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-401 | 经济数据共识接入 | 6h | T-302 | consensus_adapter.py、Trading Economics API |
| T-402 | 事件日历接入 | 5h | T-401 | calendar_adapter.py、events 表、提前 N 小时触发 |
| T-403 | 事件情景判定 | 6h | T-402 | 数据公布后计算惊喜、映射到预定义情景 |
| T-404 | 因果图模板 — CPI | 8h | T-403 | CPI 传导链 (惊喜 → 利率预期 → 美债 → BTC)、事后逐节点核对 |
| T-405 | 预测市场锚点 — Polymarket | 6h | T-201 | market_anchor.py、查询事件结果概率 |
| T-406 | 期权隐含波动接入 — Deribit | 6h | T-405 | options_adapter.py、ATM IV / 25-delta skew |
| T-407 | 市场锚点与 LLM 差异展示 | 4h | T-406 | 报告新增 vs 市场共识、差异 < 5pp 时标注无信号 |

**Phase 4 验收标准:**
- CPI、PCE、FOMC、就业数据有共识预期和日历
- 数据公布后能自动判定情景并触发方案
- 因果图能逐节点展示传导路径
- 概率展示: 基率 + 市场锚点 + LLM 调整
- 只有与市场锚点差异显著时才给出方向性方案

---

## Phase 5: 监控与提醒 (4 任务, 20h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-501 | 条件监控 — 数据层 | 5h | T-206 | monitors 表、condition_logs 表 |
| T-502 | 条件监控 — Worker 循环 | 6h | T-501 | 每分钟检查 ACTIVE monitors、触发时记录日志 |
| T-503 | 提醒通知 — 邮件/Webhook | 4h | T-502 | notifier.py、邮件/Webhook/Telegram |
| T-504 | PC 监控管理页 | 5h | T-503 | MonitorPage.tsx、列出/新建/编辑/停用监控 |

**Phase 5 验收标准:**
- 能创建"BTC 跌破 XX 时提醒"条件监控
- 条件触发时能通过邮件/Webhook 通知
- PC 能管理所有监控的状态

---

## Phase 6: PAPER 成交 (6 任务, 33h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-601 | PAPER 账户与持仓 | 6h | T-206 | accounts 表、positions 表、account.py / position.py |
| T-602 | PAPER 订单执行 | 6h | T-601 | orders 表、execution_fills 表、市价/限价/止损止盈单 |
| T-603 | PAPER 方案自动执行 | 6h | T-602, T-403 | 事件触发 → 判定情景 → 查询方案 → 下单 |
| T-604 | PAPER 保护机制 | 5h | T-603 | protection.py、止损/止盈/失效条件触发平仓 |
| T-605 | PAPER 结算与 PnL | 4h | T-604 | settlement.py、pnl_records 表 |
| T-606 | PC PAPER 操作页 | 6h | T-605 | PaperPage.tsx、账户余额/持仓/订单历史 |

**Phase 6 验收标准:**
- 能创建 PAPER 账户 (初始余额 10000 USDT)
- 事件触发后能自动执行对应情景的方案
- 止损/止盈/失效条件能正确触发平仓
- PnL 计算正确
- PC 能查看持仓、订单、PnL
- 至少完成 ≥ 10 笔成交

---

## Phase 7: 结果评分与学习 (7 任务, 38h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-701 | 评分指标计算 | 5h | T-605 | metrics.py、Brier / log loss / 方向准确率 / PnL |
| T-702 | 校准曲线生成 | 5h | T-701 | calibration_curve.py、按预测概率分桶、实际频率 |
| T-703 | 分层统计 | 4h | T-702 | stratified.py、按 asset/horizon/event_family/market_regime 分层 |
| T-704 | 校准器训练与版本化 | 6h | T-703 | calibrator.py、Platt / 保序回归、calibrator_versions 表 |
| T-705 | 历史回放评估框架 | 8h | T-003 | replay_eval.py、批量回放历史 cassette、聚合指标 |
| T-706 | PC 复盘中心 | 6h | T-704 | ReviewPage.tsx、展示评分报表/校准曲线 |
| T-707 | 周报生成 | 4h | T-706 | weekly_report.py、汇总 7 天研究/评分/PAPER |

**Phase 7 验收标准:**
- 所有研究都有 Brier、log loss、方向准确率评分
- 校准曲线展示预测概率 vs 实际频率
- 分层统计能识别哪类情况下表现更好/更差
- 校准器能改善概率校准 (Brier 下降 ≥ 5%)
- 历史回放评估能在几小时内积累 ≥ 30 个样本
- PC 复盘中心能展示所有指标
- 周报自动生成并可发送

---

## Phase 8: 最终交付 (5 任务, 47h)

| ID | 任务名 | 工时 | 依赖 | 交付物简述 |
|---|---|---|---|---|
| T-801 | 用户手册编写 | 6h | T-707 | docs/v3/USER_MANUAL.md、安装/配置/操作说明 |
| T-802 | 运维手册编写 | 5h | T-801 | docs/v3/OPS_MANUAL.md、部署/监控/备份/故障排查 |
| T-803 | 性能测试与优化 | 8h | T-303 | test_performance.py、100 次压测、P50 ≤ 15min |
| T-804 | 14 天稳定性验证 | 24h | T-803 | 连续 14 天、合格率 ≥ 90% |
| T-805 | 交接清单与代码审查 | 4h | T-804 | HANDOFF_CHECKLIST_FINAL.md、测试覆盖率报告 |

**Phase 8 验收标准 (最终交付):**
- BTC/ETH × 4h/24h/72h 连续 14 天合格率 ≥ 90%
- 单次研究 P50 ≤ 15 分钟、P95 ≤ 30 分钟
- ≥ 2 类事件族完成事前预案 → 数据落地 → 情景判定全流程
- PAPER ≥ 10 笔成交
- ruff + pyright strict 零错误
- ≥ 30 个历史回放用例全部通过
- 用户手册与运维手册完整
- 交接清单包含所有交付物

---

## 任务卡详细信息

每个任务的详细任务卡在 `docs/v3/tasks/T-XXX-<name>.md`，包含：
- 目标
- 输入/输出
- 详细需求 (功能、接口、数据结构)
- 禁止事项
- 必须的测试
- 允许修改的路径
- 验收命令 (可复现、可自动化)
- 验收标准

**如果某个任务的详细任务卡尚未编写，执行智能体应该：**
1. 阅读本清单中的"交付物简述"
2. 阅读 `docs/v3/02_ARCHITECTURE.md` 中对应模块的设计
3. 阅读 `docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` 中对应阶段的方案
4. 如仍有疑问，写 Question 到 `docs/v3/questions/` 并标记任务为 BLOCKED

---

## 阶段验收流程

每个 Phase 完成后，架构师编写阶段验收报告 `docs/v3/acceptance/phase-N.md`，内容包含：
- 该阶段所有任务完成情况
- 阶段验收标准是否达成
- 实际工时 vs 预估工时
- 遇到的问题与解决方案
- 下一阶段是否可以开工

只有阶段验收报告批准后，才能开始下一 Phase 的任务。

---

## 依赖关系图

```
Phase 0 (T-000—T-007)
  ↓
Phase 1 (T-101—T-106) — 架构主干
  ↓
Phase 2 (T-201—T-210) — 4h 普通市场
  ↓
Phase 3 (T-301—T-303) — 24h/72h 期限
  ↓                        ↓
Phase 4 (T-401—T-407)   Phase 5 (T-501—T-504)   Phase 6 (T-601—T-606)
事件研究                  监控提醒                PAPER 成交
  ↓                        ↓                        ↓
  └────────────────────────┴────────────────────────┘
                           ↓
              Phase 7 (T-701—T-707) — 评分与学习
                           ↓
              Phase 8 (T-801—T-805) — 最终交付
```

Phase 4/5/6 可以并行开工（都依赖 Phase 3），但 Phase 7 需要等 Phase 4/5/6 全部完成。

---

## 工时统计

- **Phase 0:** 22h (约 4 天)
- **Phase 1:** 34h (约 6 天)
- **Phase 2:** 56h (约 10 天)
- **Phase 3:** 14h (约 2 天)
- **Phase 4/5/6 并行:** 最长 47h (约 8 天)
- **Phase 7:** 38h (约 7 天)
- **Phase 8:** 47h (约 8 天, 其中 T-804 需真实等待 14 天)

**总计:** 约 11 周（不含 14 天稳定性验证的真实等待时间）

---

## 注意事项

1. **阶段串行，任务可并行：** Phase N 未完成前不开工 Phase N+1，但同一 Phase 内独立任务可并行。
2. **任务卡未写全时：** 按本清单 + ARCHITECTURE + DIAGNOSIS 理解任务，有疑问写 Question。
3. **验收严格：** 任何一条验收命令失败，任务 REJECTED。
4. **报告诚实：** 隐瞒问题比失败更严重。
5. **禁止修改旧代码：** `packages/kernel/`、`apps/hub_*` 全部禁止 import 或修改。

---

最后更新: 2026-10-23
