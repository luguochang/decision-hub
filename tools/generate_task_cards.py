"""
Task card generator for Decision Hub v3.

根据 DIAGNOSIS_AND_SOLUTION.md 的 8 阶段计划，批量生成所有任务卡。
"""

PHASE_0_TASKS = [
    {
        "id": "T-000",
        "name": "Git 基线与分支策略",
        "hours": 2,
        "status": "WRITTEN",  # 已手写
    },
    {
        "id": "T-001",
        "name": "v3 代码骨架与模块划分",
        "hours": 3,
        "status": "WRITTEN",  # 已手写
    },
    {
        "id": "T-002",
        "name": "Feature Store 核心实现",
        "hours": 6,
        "deps": ["T-001"],
        "deliverables": [
            "storage.py: features 表 (known_at, asset, metric, value, source, cassette_id)",
            "query.py: fetch_latest(), fetch_range(), fetch_as_of()",
            "okx_adapter.py: fetch_klines(), fetch_funding_rate()",
            "3 个单元测试 (插入、查询、known_at 过滤)",
        ],
    },
    {
        "id": "T-003",
        "name": "回放框架与 cassette 存储",
        "hours": 5,
        "deps": ["T-001"],
        "deliverables": [
            "cassettes 表 (id, recorded_at, scope, api_calls JSON)",
            "录制模式: 真实 API → 存 cassette",
            "回放模式: 从 cassette 读取",
            "1 个端到端测试 (录制 OKX → 回放通过)",
        ],
    },
    {
        "id": "T-004",
        "name": "v3 检查工具",
        "hours": 2,
        "deps": ["T-001"],
        "deliverables": [
            "tools/v3_check.py: 运行 ruff + pyright",
            "CI 集成 (GitHub Actions / 本地 pre-commit)",
        ],
    },
    {
        "id": "T-005",
        "name": "合格研究定义与状态机",
        "hours": 4,
        "deps": ["T-001"],
        "deliverables": [
            "state_machine.py: ResearchState 枚举、transition 规则",
            "research_runs 表 (id, asset, horizon, state, stage_outputs JSONB, created_at)",
            "advance() 方法与状态校验",
            "5 个单元测试 (正常流转、非法跳转、失败重试)",
        ],
    },
    {
        "id": "T-006",
        "name": "错误码体系与日志结构",
        "hours": 3,
        "deps": ["T-001"],
        "deliverables": [
            "errors.py: 30 个错误码 (FEATURE_STORE_*, RESEARCH_*, PAPER_*)",
            "DecisionHubError 基类",
            "结构化日志 JSON 格式",
        ],
    },
    {
        "id": "T-007",
        "name": "测试基础设施与 fixture",
        "hours": 4,
        "deps": ["T-002", "T-005"],
        "deliverables": [
            "conftest.py: db fixture (内存 SQLite)",
            "mock_feature_store fixture",
            "mock_cassette fixture",
            "sample_research_run fixture",
        ],
    },
]

PHASE_1_TASKS = [
    {
        "id": "T-101",
        "name": "研究主循环与 DSH 会话管理",
        "hours": 8,
        "deps": ["T-005"],
        "deliverables": [
            "dsh_session.py: 调用 DSH API、保留上下文",
            "主循环: 从 IDLE → PREPARE → EVIDENCE → ... → PUBLISHED",
            "断点续跑: 从任意阶段恢复",
            "sessions 表 (id, research_run_id, dsh_session_id, messages JSONB)",
        ],
    },
    {
        "id": "T-102",
        "name": "PREPARE 阶段实现",
        "hours": 5,
        "deps": ["T-101"],
        "deliverables": [
            "stage_prepare.py: 确定 asset、horizon、定义信息集边界",
            "输出: information_set_cutoff (known_at 上界)",
        ],
    },
    {
        "id": "T-103",
        "name": "EVIDENCE 阶段实现",
        "hours": 6,
        "deps": ["T-101", "T-002"],
        "deliverables": [
            "stage_evidence.py: 调用 Feature Store 按 known_at 查询",
            "调用 DSH web_search (如需)",
            "输出: evidence_summary (结构化 JSON)",
        ],
    },
    {
        "id": "T-104",
        "name": "不可变存储与原件归档",
        "hours": 4,
        "deps": ["T-101"],
        "deliverables": [
            "artifacts 表 (content_hash, content BLOB, archived_at)",
            "内容按 SHA256 存储，相同内容不重复",
            "研究引用原件时只存 hash",
        ],
    },
    {
        "id": "T-105",
        "name": "会话日志追加校验",
        "hours": 4,
        "deps": ["T-101"],
        "deliverables": [
            "session_log_entries 表 (session_id, seq, event_type, payload, prefix_hash)",
            "追加时校验 prefix_hash 链",
            "禁止删除或修改已有条目",
        ],
    },
    {
        "id": "T-106",
        "name": "研究快照生成",
        "hours": 3,
        "deps": ["T-104"],
        "deliverables": [
            "snapshot.py: 把研究所有输入输出、原件 hash 打包成自包含 JSON",
            "快照可离线读取，不依赖数据库",
        ],
    },
]

PHASE_2_TASKS = [
    {
        "id": "T-201",
        "name": "FORECAST 阶段 — 历史基率",
        "hours": 6,
        "deps": ["T-103"],
        "deliverables": [
            "base_rate.py: 查询历史同类情况（asset、horizon、事件族、波动环境）",
            "返回方向频率与波动分位数",
            "3 个单元测试（有样本、无样本、边界）",
        ],
    },
    {
        "id": "T-202",
        "name": "FORECAST 阶段 — LLM 主观估计",
        "hours": 5,
        "deps": ["T-201"],
        "deliverables": [
            "llm_adjuster.py: 调用 DSH 让 LLM 给出概率",
            "prompt 包含基率作为参考",
            "输出: scenarios [(name, prob, rationale)]",
        ],
    },
    {
        "id": "T-203",
        "name": "FORECAST 阶段 — 多视角聚合",
        "hours": 5,
        "deps": ["T-202"],
        "deliverables": [
            "aggregator.py: 独立运行 3 个视角（宏观、拥挤度、反方）",
            "中位数聚合，记录分歧度",
            "2 个单元测试（聚合、分歧度计算）",
        ],
    },
    {
        "id": "T-204",
        "name": "PLAN 阶段实现",
        "hours": 6,
        "deps": ["T-203"],
        "deliverables": [
            "stage_plan.py: 根据概率生成多空方案",
            "输出: plans [(scenario, direction, entry, stop_loss, take_profit, invalidation)]",
        ],
    },
    {
        "id": "T-205",
        "name": "REVIEW 阶段 — 独立审核",
        "hours": 5,
        "deps": ["T-204"],
        "deliverables": [
            "stage_review.py: 独立 DSH 会话审核概率与方案",
            "检查: 概率加和为 1、止损合理、失效条件明确",
            "输出: review_verdict (APPROVED / NEED_REVISION / REJECTED), issues",
        ],
    },
    {
        "id": "T-206",
        "name": "PUBLISH 阶段实现",
        "hours": 4,
        "deps": ["T-205", "T-106"],
        "deliverables": [
            "stage_publish.py: 生成快照、写 reports 表",
            "reports 表 (id, research_run_id, snapshot_hash, published_at)",
            "状态改为 PUBLISHED",
        ],
    },
    {
        "id": "T-207",
        "name": "报告 HTML 渲染",
        "hours": 5,
        "deps": ["T-206"],
        "deliverables": [
            "renderer.py: 读取快照 JSON → 渲染成 HTML",
            "首屏: 方向、概率、方案、核心理由",
            "详情: 证据、审核、哈希（可折叠）",
        ],
    },
    {
        "id": "T-208",
        "name": "PC 研究发起页",
        "hours": 6,
        "deps": ["T-101"],
        "deliverables": [
            "ResearchPage.tsx: 选择 asset、horizon，点击发起",
            "调用 POST /api/v3/research",
            "轮询状态显示进度",
        ],
    },
    {
        "id": "T-209",
        "name": "PC 报告展示页",
        "hours": 6,
        "deps": ["T-207"],
        "deliverables": [
            "ReportPage.tsx: 调用 GET /api/v3/reports/{id}",
            "展示方向、概率、方案、理由",
            ""详情"按钮展开审计信息",
        ],
    },
    {
        "id": "T-210",
        "name": "4h 普通市场端到端测试",
        "hours": 8,
        "deps": ["T-209"],
        "deliverables": [
            "test_btc_4h_e2e.py: 录制真实数据 → 运行完整研究 → 验证输出",
            "检查: 状态流转正确、报告可读、快照完整",
            "连续 3 次成功",
        ],
    },
]

PHASE_3_TASKS = [
    {
        "id": "T-301",
        "name": "24h 期限支持",
        "hours": 4,
        "deps": ["T-210"],
        "deliverables": [
            "Feature Store 查询扩展到 24h 窗口",
            "基率查询支持 horizon=24h",
            "2 个端到端测试 (BTC 24h, ETH 24h)",
        ],
    },
    {
        "id": "T-302",
        "name": "72h 期限支持",
        "hours": 4,
        "deps": ["T-301"],
        "deliverables": [
            "72h 特殊处理（周末流动性、多事件叠加）",
            "2 个端到端测试 (BTC 72h, ETH 72h)",
        ],
    },
    {
        "id": "T-303",
        "name": "六组合稳定性测试",
        "hours": 6,
        "deps": ["T-302"],
        "deliverables": [
            "test_stability.py: BTC/ETH × 4h/24h/72h 各运行 10 次",
            "统计成功率、P50/P95 时间",
            "合格率目标 ≥ 80%（Phase 2 交付标准是 90%，这里作为中间里程碑）",
        ],
    },
]

PHASE_4_TASKS = [
    {
        "id": "T-401",
        "name": "经济数据共识接入",
        "hours": 6,
        "deps": ["T-302"],
        "deliverables": [
            "consensus_adapter.py: 接入 Trading Economics 或 Bloomberg API",
            "采集 CPI、PCE、就业、GDP 的共识预期",
            "features 表新增 metric_type = 'consensus'",
        ],
    },
    {
        "id": "T-402",
        "name": "事件日历接入",
        "hours": 5,
        "deps": ["T-401"],
        "deliverables": [
            "calendar_adapter.py: 接入 Forex Factory 或官方日历",
            "events 表 (event_id, event_type, scheduled_at, actual_value, consensus, previous)",
            "提前 N 小时触发准备窗口",
        ],
    },
    {
        "id": "T-403",
        "name": "事件情景判定",
        "hours": 6,
        "deps": ["T-402"],
        "deliverables": [
            "event_scenario.py: 数据公布后计算惊喜 (actual - consensus)",
            "映射到预定义情景 (大幅超预期、小幅、符合、低于)",
            "触发对应方案",
        ],
    },
    {
        "id": "T-404",
        "name": "因果图模板 — CPI",
        "hours": 8,
        "deps": ["T-403"],
        "deliverables": [
            "causal_graph.py: 定义 CPI 传导链",
            "节点: 惊喜 → 利率预期 → 美债收益率 → BTC",
            "每个节点对应 Feature Store 指标",
            "事后逐节点核对",
        ],
    },
    {
        "id": "T-405",
        "name": "预测市场锚点 — Polymarket",
        "hours": 6,
        "deps": ["T-201"],
        "deliverables": [
            "market_anchor.py: 调用 Polymarket API",
            "查询事件结果概率、BTC 价格区间市场",
            "作为 FORECAST 阶段的参考锚点",
        ],
    },
    {
        "id": "T-406",
        "name": "期权隐含波动接入 — Deribit",
        "hours": 6,
        "deps": ["T-405"],
        "deliverables": [
            "options_adapter.py: 调用 Deribit API",
            "采集 ATM IV、25-delta skew、事件隐含跳动",
            "features 表新增 metric_type = 'implied_vol'",
        ],
    },
    {
        "id": "T-407",
        "name": "市场锚点与 LLM 差异展示",
        "hours": 4,
        "deps": ["T-406"],
        "deliverables": [
            "报告中新增 vs 市场共识一栏",
            "显示: 预测市场隐含 XX%, 系统估计 YY%, 差异 ZZ pp",
            "差异小于 5pp 时标注与市场一致无交易信号",
        ],
    },
]

PHASE_5_TASKS = [
    {
        "id": "T-501",
        "name": "条件监控 — 数据层",
        "hours": 5,
        "deps": ["T-206"],
        "deliverables": [
            "monitors 表 (id, report_id, condition_type, threshold, status)",
            "condition_logs 表 (monitor_id, checked_at, triggered, value)",
        ],
    },
    {
        "id": "T-502",
        "name": "条件监控 — Worker 循环",
        "hours": 6,
        "deps": ["T-501"],
        "deliverables": [
            "condition_monitor.py: 每分钟检查所有 ACTIVE monitors",
            "触发时: 更新状态、记录日志、发送提醒",
        ],
    },
    {
        "id": "T-503",
        "name": "提醒通知 — 邮件/Webhook",
        "hours": 4,
        "deps": ["T-502"],
        "deliverables": [
            "notifier.py: 支持邮件、Webhook、Telegram",
            "配置表 notification_configs",
        ],
    },
    {
        "id": "T-504",
        "name": "PC 监控管理页",
        "hours": 5,
        "deps": ["T-503"],
        "deliverables": [
            "MonitorPage.tsx: 列出所有监控、状态、最后触发时间",
            "新建/编辑/停用监控",
        ],
    },
]

PHASE_6_TASKS = [
    {
        "id": "T-601",
        "name": "PAPER 账户与持仓",
        "hours": 6,
        "deps": ["T-206"],
        "deliverables": [
            "accounts 表 (id, name, initial_balance, current_balance)",
            "positions 表 (id, account_id, asset, direction, size, entry_price, opened_at)",
            "account.py: 创建账户、查询余额",
            "position.py: 开仓、查询持仓",
        ],
    },
    {
        "id": "T-602",
        "name": "PAPER 订单执行",
        "hours": 6,
        "deps": ["T-601"],
        "deliverables": [
            "orders 表 (id, position_id, order_type, trigger_price, executed_at, fill_price)",
            "order.py: 市价单、限价单、止损止盈单",
            "execution_fills 表 (order_id, filled_at, price, size, fee)",
        ],
    },
    {
        "id": "T-603",
        "name": "PAPER 方案自动执行",
        "hours": 6,
        "deps": ["T-602", "T-403"],
        "deliverables": [
            "auto_executor.py: 事件触发 → 判定情景 → 查询对应方案 → 下单",
            "支持"条件单": 情景 A 出现时才执行",
        ],
    },
    {
        "id": "T-604",
        "name": "PAPER 保护机制",
        "hours": 5,
        "deps": ["T-603"],
        "deliverables": [
            "protection.py: 止损、止盈、失效条件触发平仓",
            "Worker 循环检查所有持仓",
        ],
    },
    {
        "id": "T-605",
        "name": "PAPER 结算与 PnL",
        "hours": 4,
        "deps": ["T-604"],
        "deliverables": [
            "settlement.py: 持仓平仓后计算 PnL、更新账户余额",
            "pnl_records 表 (position_id, realized_pnl, closed_at)",
        ],
    },
    {
        "id": "T-606",
        "name": "PC PAPER 操作页",
        "hours": 6,
        "deps": ["T-605"],
        "deliverables": [
            "PaperPage.tsx: 账户余额、持仓列表、订单历史",
            "手动开仓/平仓、修改止损止盈",
        ],
    },
]

PHASE_7_TASKS = [
    {
        "id": "T-701",
        "name": "评分指标计算",
        "hours": 5,
        "deps": ["T-605"],
        "deliverables": [
            "metrics.py: Brier、log loss、方向准确率、PnL",
            "evaluations 表 (report_id, metric_name, value, computed_at)",
        ],
    },
    {
        "id": "T-702",
        "name": "校准曲线生成",
        "hours": 5,
        "deps": ["T-701"],
        "deliverables": [
            "calibration_curve.py: 按预测概率分桶、计算实际频率",
            "生成可靠性曲线图",
        ],
    },
    {
        "id": "T-703",
        "name": "分层统计",
        "hours": 4,
        "deps": ["T-702"],
        "deliverables": [
            "stratified.py: 按 asset、horizon、event_family、market_regime 分层",
            "每层独立计算指标",
        ],
    },
    {
        "id": "T-704",
        "name": "校准器训练与版本化",
        "hours": 6,
        "deps": ["T-703"],
        "deliverables": [
            "calibrator.py: Platt 或保序回归",
            "calibrator_versions 表 (id, trained_on_samples, coefficients, deployed_at)",
            "应用时记录使用了哪个版本",
        ],
    },
    {
        "id": "T-705",
        "name": "历史回放评估框架",
        "hours": 8,
        "deps": ["T-003"],
        "deliverables": [
            "replay_eval.py: 读取历史 cassette → 重建信息集 → 运行研究 → 评分",
            "支持批量运行（10、50、100 个场景）",
            "输出: 聚合指标、失败案例清单",
        ],
    },
    {
        "id": "T-706",
        "name": "PC 复盘中心",
        "hours": 6,
        "deps": ["T-704"],
        "deliverables": [
            "ReviewPage.tsx: 展示所有评分报表",
            "过滤: 按时间、资产、事件族",
            "显示: Brier、准确率、PnL、校准曲线",
        ],
    },
    {
        "id": "T-707",
        "name": "周报生成",
        "hours": 4,
        "deps": ["T-706"],
        "deliverables": [
            "weekly_report.py: 汇总过去 7 天的研究、评分、PAPER 成交",
            "生成 Markdown + HTML",
            "可配置自动发送",
        ],
    },
]

PHASE_8_TASKS = [
    {
        "id": "T-801",
        "name": "用户手册编写",
        "hours": 6,
        "deps": ["T-707"],
        "deliverables": [
            "docs/v3/USER_MANUAL.md: 安装、配置、发起研究、查看报告、PAPER 操作",
            "截图与示例",
        ],
    },
    {
        "id": "T-802",
        "name": "运维手册编写",
        "hours": 5,
        "deps": ["T-801"],
        "deliverables": [
            "docs/v3/OPS_MANUAL.md: 部署、监控、备份、故障排查",
            "数据库迁移步骤",
        ],
    },
    {
        "id": "T-803",
        "name": "性能测试与优化",
        "hours": 8,
        "deps": ["T-303"],
        "deliverables": [
            "test_performance.py: 100 次研究压测",
            "目标: P50 ≤ 15 分钟、P95 ≤ 30 分钟",
            "识别瓶颈、优化（缓存、并发）",
        ],
    },
    {
        "id": "T-804",
        "name": "14 天稳定性验证",
        "hours": 24,  # 实际是 14 天，但需要人每天检查
        "deps": ["T-803"],
        "deliverables": [
            "连续 14 天运行 BTC/ETH × 4h/24h/72h",
            "记录: 每天成功率、失败原因、P50/P95",
            "合格率 ≥ 90%",
        ],
    },
    {
        "id": "T-805",
        "name": "交接清单与代码审查",
        "hours": 4,
        "deps": ["T-804"],
        "deliverables": [
            "docs/v3/HANDOFF_CHECKLIST_FINAL.md: 所有交付物、验收结果",
            "代码审查报告: ruff + pyright 零错误",
            "测试覆盖率报告",
        ],
    },
]

ALL_PHASES = [
    ("Phase 0", PHASE_0_TASKS),
    ("Phase 1", PHASE_1_TASKS),
    ("Phase 2", PHASE_2_TASKS),
    ("Phase 3", PHASE_3_TASKS),
    ("Phase 4", PHASE_4_TASKS),
    ("Phase 5", PHASE_5_TASKS),
    ("Phase 6", PHASE_6_TASKS),
    ("Phase 7", PHASE_7_TASKS),
    ("Phase 8", PHASE_8_TASKS),
]

if __name__ == "__main__":
    total = 0
    for phase_name, tasks in ALL_PHASES:
        print(f"\n## {phase_name}")
        for task in tasks:
            if task.get("status") == "WRITTEN":
                print(f"  {task['id']}: {task['name']} — 已手写")
            else:
                hours = task.get("hours", "?")
                deps = ", ".join(task.get("deps", [])) or "无"
                print(f"  {task['id']}: {task['name']} ({hours}h, 依赖: {deps})")
                total += hours if isinstance(hours, int) else 0

    print(f"\n总计: {total} 小时（不含已手写的 T-000, T-001）")
    print(f"按每天 6 小时工作量: {total // 6} 天")
    print(f"按自然日（含等待、验收）: 约 {total // 6 * 1.5:.0f} 天 = {total // 6 * 1.5 / 7:.1f} 周")
