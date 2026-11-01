/* Design fixtures only. No live prices, official event schedule, or backend access. */
window.DH_PAGES = [
    ['home', '研究指挥台', '获取建议、事件、任务与选中报告的统一工作台'],
    ['compose', '获取最新操作建议', '空输入使用固定 BTC / 宏观研究模板'],
    ['submitting', '提交中', '输入保留、按钮锁定、同一意图幂等提交'],
    ['queued', '研究已排队', 'Run 已创建；离开页面不取消任务'],
    ['researching', '正在研究', '轮次、工具数与最近活动，不伪造完成百分比'],
    ['failed', '研究失败', '错误来源、错误码、可重试性及原输入'],
    ['runs', 'Research Runs', '搜索、状态筛选、证据覆盖、父子任务'],
    ['calendar-all', '宏观事件 · 全部', '事件账本与来源采集状态分离'],
    ['calendar-upcoming', '宏观事件 · 即将发生', '示例风险窗口与发布时间'],
    ['calendar-history', '宏观事件 · 历史发布', '尚未采集历史发布；不是未来事件的复制'],
    ['event', '事件详情抽屉', '来源、前值、预期、实际、意外、版本记录'],
    ['report', '研究报告', '方向、行动候选、概率和权限分别呈现'],
    ['report-incomplete', '结论不完整', '研究已结束；synthesis_incomplete 明确提示'],
    ['technical', '技术详情', '八类结构化审计模块，不展示私有逐字思维链'],
    ['evidence', '证据详情抽屉', '来源身份、质量、时间及证据关联'],
    ['graph', '全屏分析链路', '分组、缩放、平移、适配与小地图'],
    ['graph-node', '图谱节点检查器', '节点、关系与证据在同一 Run 下联动'],
    ['library', '研究资料库', '资料版本、审核状态与关联 Run'],
    ['upload', '上传资料', '支持格式、元数据、隐私与文件校验'],
    ['document-uploading', '资料上传中', '本地模拟进度；没有实际上传'],
    ['document-parsing', '资料解析中', '结构提取进度与原文保留提示'],
    ['document-quality', '资料质量不足', '硬缺口阻止资料进入研究'],
    ['document-review', '语义修正审核', '逐条接受 / 拒绝，敏感语义变化明确阻断'],
    ['document-confirm', '确认审核范围', '全部差异已处理；显式确认后生成新版本'],
    ['document-confirmed', '质量复检中', '人工确认不能绕过质量门'],
    ['document-ready', '可用于研究', '版本已确认且质量门通过'],
    ['document-linked', '资料已连接', '资料到新 Run 以及新 Run 到资料的双向入口'],
    ['document-failed', '资料解析失败', '文件保留；恢复与重试入口'],
    ['home-loading', '工作台加载中', '按 worker、日历、任务分别反馈'],
    ['home-unknown', '服务状态未知', '未知不渲染成健康'],
    ['home-degraded', '来源降级', '来源、影响范围、最后成功与下次检查'],
    ['home-empty', '暂无研究任务', '下一步明确，不填入假报告'],
    ['sources', '服务与数据源', '运行健康与来源质量独立显示'],
    ['states', '组件与状态规范', '颜色、图标、控件、反馈及状态边界'],
    ['dsh', 'DSH · 技术轨迹', '内部只读排查；带当前 Run 返回'],
    ['dsh-conversation', 'DSH · 对话摘要', '公开运行摘要，不暴露逐字思维链'],
    ['dsh-research', 'DSH · 主动研究', '子任务与失败记录'],
    ['dsh-report', 'DSH · 原生报告', '技术原生记录不代替 Hub 用户结论']
];
window.DH_FIXTURES = {
    snapshot: '2026-09-23T13:42:10+08:00',
    runs: [
        { id: 'demo-r01', title: 'BTC 最新操作建议', focus: '获取最新操作建议', status: 'completed', trigger: '自动触发', at: '2026-09-23T13:40:00+08:00', evidence: 6, required: 12, sources: 2, parent: null, kind: 'research_only', doc: null },
        { id: 'demo-r02', title: '宏观事件与资金流核查', focus: '重点关注美元指数和 ETF 资金流', status: 'researching', trigger: '人工提交', at: '2026-09-23T13:32:00+08:00', evidence: 5, required: 12, sources: 3, parent: 'demo-r01', kind: 'research_only', doc: null },
        { id: 'demo-r03', title: 'BTC 方向基线 · 定时复查', focus: '复查关键证据与失效条件', status: 'queued', trigger: '定时复查', at: '2026-09-23T13:20:00+08:00', evidence: 0, required: 12, sources: 0, parent: 'demo-r01', kind: 'research_only', doc: null },
        { id: 'demo-r04', title: '流动性来源补充研究', focus: '补充宏观流动性来源', status: 'failed', trigger: '人工提交', at: '2026-09-23T12:54:00+08:00', evidence: 3, required: 12, sources: 2, parent: null, kind: 'research_only', doc: null },
        { id: 'demo-r05', title: 'BTC 因果链完整性复核', focus: '复核因果链与反方论据', status: 'completed', trigger: '自动触发', at: '2026-09-23T11:26:00+08:00', evidence: 8, required: 12, sources: 4, parent: 'demo-r01', kind: 'synthesis_incomplete', doc: null }
    ],
    events: [
        { id: 'event-01', name: '美国 CPI（同比）', region: '美国', code: 'US', date: '2026-09-24T20:30:00+08:00', impact: '高', previous: null, forecast: null, actual: null, surprise: null, source: 'BLS · 来源占位', state: '来源降级', observed: null, versions: [] },
        { id: 'event-02', name: '美国零售销售（月率）', region: '美国', code: 'US', date: '2026-09-25T20:30:00+08:00', impact: '高', previous: null, forecast: null, actual: null, surprise: null, source: '官方统计源 · 占位', state: '待采集', observed: null, versions: [] },
        { id: 'event-03', name: '欧元区 CPI（同比）', region: '欧元区', code: 'EU', date: '2026-09-25T17:00:00+08:00', impact: '中', previous: null, forecast: null, actual: null, surprise: null, source: '官方统计源 · 占位', state: '待采集', observed: null, versions: [] },
        { id: 'event-04', name: '中国 LPR 利率', region: '中国', code: 'CN', date: '2026-09-28T09:15:00+08:00', impact: '高', previous: null, forecast: null, actual: null, surprise: null, source: '官方发布源 · 占位', state: '待采集', observed: null, versions: [] }
    ],
    evidence: [
        { id: 'EV-001', title: '7 日价格规则基线', type: '价格规则', source: '交接稿 · 第 7 页', authority: '用户提供', quality: '可追溯', observed: '2026-09-23T13:40:00+08:00', claim: '7 日价格规则基线为偏多。该描述不等同于买入授权。', relation: '支持方向', origin: 'source', url: null },
        { id: 'EV-002', title: '短周期行动候选', type: '执行限制', source: '交接稿 · 第 7 页', authority: '用户提供', quality: '可追溯', observed: '2026-09-23T13:40:00+08:00', claim: '30m、24h 为 no_trade；这不是未分析，而是当前不满足行动条件。', relation: '限制行动', origin: 'source', url: null },
        { id: 'EV-003', title: '72h 候选与执行边界', type: '执行限制', source: '交接稿 · 第 7 页', authority: '用户提供', quality: '可追溯', observed: '2026-09-23T13:40:00+08:00', claim: '72h 为保守 long 候选。概率可能未校准，交易权限未授权。', relation: '限制行动', origin: 'source', url: null },
        { id: 'EV-004', title: '宏观来源完整性核查', type: '来源检查', source: '设计演示条目', authority: '待核验', quality: '有缺口', observed: null, claim: '用于演示来源缺失与反方限制的呈现；不是外部研究事实。', relation: '反方限制', origin: 'fixture', url: null },
        { id: 'EV-005', title: '资金流证据时间核查', type: '时效检查', source: '设计演示条目', authority: '待核验', quality: '待采集', observed: null, claim: '用于演示证据时效检查，不包含实际资金流判断。', relation: '反方限制', origin: 'fixture', url: null },
        { id: 'EV-006', title: '独立复核记录占位', type: '独立复核', source: '设计演示条目', authority: '演示', quality: '待复核', observed: null, claim: '独立复核的存在不能由一条绿色成功标签推定。', relation: '审计摘要', origin: 'fixture', url: null }
    ],
    diffs: [
        { id: 'blk-01', label: '术语规范化', location: '第 1 页 · 段落 1', original: '7 日价格規則基线：偏多。', proposed: '7 日价格规则基线：偏多。', before: '規則', after: '规则', risk: false, note: '演示繁简字形统一；不改变方向、数字或限定词。' },
        { id: 'blk-02', label: '周期格式规范化', location: '第 1 页 · 段落 2', original: '30 m、24 h：no_trade。', proposed: '30m、24h：no_trade。', before: '30 m、24 h', after: '30m、24h', risk: false, note: '仅统一周期写法；保留 no_trade。' },
        { id: 'blk-03', label: '高风险语义变化', location: '第 1 页 · 段落 4', original: '概率未校准，交易权限未授权。', proposed: '概率已校准，交易权限已授权。', before: '未', after: '已', risk: true, note: '该建议改变事实和权限；不能接受。应拒绝建议并保留原文。' }
    ],
    logs: [
        { id: 'log-01', time: '13:40:00', level: 'INFO', type: 'run_started', component: 'orchestrator', summary: '研究任务已登记', status: '成功', latency: '—', tokens: '—' },
        { id: 'log-02', time: '13:40:03', level: 'INFO', type: 'tool_call', component: 'source_reader', summary: '请求公开来源元数据', status: '成功', latency: '2.1s', tokens: '—' },
        { id: 'log-03', time: '13:40:06', level: 'WARN', type: 'tool_result', component: 'source_reader', summary: '部分来源未获取；保留缺口', status: '部分失败', latency: '3.0s', tokens: '—' },
        { id: 'log-04', time: '13:40:12', level: 'INFO', type: 'evidence_indexed', component: 'researcher', summary: '证据 ID 与来源版本建立关联', status: '成功', latency: '0.4s', tokens: '—' },
        { id: 'log-05', time: '13:40:20', level: 'INFO', type: 'review_started', component: 'reviewer', summary: '进入结构化复核阶段', status: '待核验', latency: '—', tokens: '—' },
        { id: 'log-06', time: '13:41:03', level: 'ERROR', type: 'source_timeout', component: 'source_reader', summary: '示例来源超时；允许重试', status: '失败', latency: '10s', tokens: '—' },
        { id: 'log-07', time: '13:41:10', level: 'INFO', type: 'gate_checked', component: 'quality_gate', summary: '研究可读；未授权交易', status: '成功', latency: '0.1s', tokens: '—' },
        { id: 'log-08', time: '13:42:10', level: 'INFO', type: 'run_completed', component: 'orchestrator', summary: '返回 Hub 研究结果视图', status: '成功', latency: '—', tokens: '—' }
    ]
};
