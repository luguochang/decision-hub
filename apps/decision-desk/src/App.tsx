import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, ArrowUpRight, BarChart3, Bell, BookOpen, BrainCircuit, ChevronRight, CircleHelp, Clock3, Database, FileText, Gauge, Inbox, Layers3, Menu, Network, PanelLeftClose, Search, Settings2, ShieldCheck, Sparkles, Target, X } from 'lucide-react'
import { useState } from 'react'
import { api, fallbackSummary } from './api/client'
import type { GateStatus, RunView } from './api/types'

const nav = [
  { label: 'Inbox', icon: Inbox, active: true },
  { label: 'Decision', icon: BrainCircuit },
  { label: 'Forecasts', icon: Target },
  { label: 'Timeline', icon: Clock3 },
  { label: 'Evidence', icon: Network },
  { label: 'Health', icon: Gauge },
  { label: 'Assets', icon: Layers3 },
  { label: 'Evolution', icon: Sparkles },
]

const statusMeta: Record<GateStatus, { label: string; tone: string }> = {
  publish: { label: '已发布', tone: 'success' },
  degraded: { label: '降级发布', tone: 'warning' },
  research_only: { label: '仅研究', tone: 'neutral' },
  reject: { label: '拒绝', tone: 'danger' },
}

function StatusPill({ status, runStatus, errorCode }: { status: GateStatus | null; runStatus?: RunView['status']; errorCode?: string | null }) {
  if (!status && errorCode === 'duplicate_observation') return <span className="status-pill neutral"><span className="status-dot" />重复输入</span>
  if (!status && runStatus === 'degraded') return <span className="status-pill warning"><span className="status-dot" />已降级</span>
  if (!status) return <span className="status-pill neutral">处理中</span>
  const meta = statusMeta[status]
  return <span className={`status-pill ${meta.tone}`}><span className="status-dot" />{meta.label}</span>
}

function Metric({ icon: Icon, label, value, hint, tone = 'default' }: { icon: typeof Activity; label: string; value: string | number; hint: string; tone?: string }) {
  return <div className="metric">
    <div className="metric-top"><span className={`metric-icon ${tone}`}><Icon size={16} /></span><span className="metric-label">{label}</span><ArrowUpRight size={14} className="metric-arrow" /></div>
    <div className="metric-value">{value}</div>
    <div className="metric-hint">{hint}</div>
  </div>
}

function App() {
  const [collapsed, setCollapsed] = useState(false)
  const [selected, setSelected] = useState<RunView | null>(null)
  const [submitOpen, setSubmitOpen] = useState(false)
  const [textInput, setTextInput] = useState('')
  const queryClient = useQueryClient()
  const submitMutation = useMutation({
    mutationFn: async (text: string) => {
      const response = await fetch('/v1/observations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ text, source_id: 'decision-desk', language: 'zh' }),
      })
      if (!response.ok) throw new Error(`提交失败 (${response.status})`)
      return response.json() as Promise<{ run_id: string }>
    },
    onSuccess: () => { setTextInput(''); setSubmitOpen(false); void queryClient.invalidateQueries({ queryKey: ['summary'] }) },
  })
  const summaryQuery = useQuery({ queryKey: ['summary'], queryFn: api.summary, refetchInterval: 5_000, retry: false })
  const summary = summaryQuery.data ?? fallbackSummary

  return <div className="app-shell">
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="brand"><div className="brand-mark"><ShieldCheck size={18} /></div>{!collapsed && <div><div className="brand-name">Decision Hub</div><div className="brand-sub">OWNER CONSOLE</div></div>}</div>
      <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'}>{collapsed ? <Menu size={18} /> : <PanelLeftClose size={18} />}</button>
      <div className="nav-label">WORKSPACE</div>
      <nav>{nav.map(({ label, icon: Icon, active }) => <button className={`nav-item ${active ? 'active' : ''}`} key={label} title={collapsed ? label : undefined}><Icon size={17} />{!collapsed && <span>{label}</span>}{!collapsed && label === 'Inbox' && <span className="nav-count">3</span>}</button>)}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="side-health"><span className="live-dot" /> <span>Core healthy</span><span className="side-health-time">12s</span></div><button className="nav-item"><Settings2 size={17} /><span>Settings</span></button><button className="nav-item"><CircleHelp size={17} /><span>Runbook</span></button></div>}
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="breadcrumbs"><span>Decision Desk</span><ChevronRight size={14} /><strong>Inbox</strong></div><div className="top-actions"><div className="search-shell"><Search size={15} /><input placeholder="Search events, runs, assets" aria-label="Search" /></div><button className="icon-button" aria-label="Notifications"><Bell size={17} /><span className="notification-dot" /></button><div className="owner-avatar">C</div></div></header>
      <div className="content-wrap">
        <section className="page-heading"><div><div className="eyebrow"><span className="live-dot" />LIVE DECISION SYSTEM</div><h1>Inbox</h1><p>事件、证据和待确认动作集中在这里。系统不会把未经 Gate 的候选当成结论。</p></div><div className="heading-actions"><button className="button secondary" onClick={() => setSubmitOpen(true)}><FileText size={15} />提交文本</button><button className="button primary" onClick={() => setSubmitOpen(true)}><Sparkles size={15} />新建分析</button></div></section>
        <section className="metrics-grid"><Metric icon={Inbox} label="待处理事件" value={summary.inbox.pending_count} hint="需要人工确认" tone="amber" /><Metric icon={Activity} label="运行中" value={summary.inbox.running_count} hint="自动刷新 · 5s" tone="blue" /><Metric icon={Target} label="近 30 天预测" value={summary.forecast_count} hint={`${summary.evaluated_count} 已有结果`} tone="green" /><Metric icon={Database} label="当前策略" value="v1" hint={summary.active_pack} tone="purple" /></section>
        <section className="workspace-grid">
          <div className="panel inbox-panel"><div className="panel-header"><div><h2>Recent events</h2><p>按接收时间排序 · Point-in-time evidence</p></div><button className="text-button">查看全部 <ArrowUpRight size={14} /></button></div><div className="table-wrap"><table><thead><tr><th>事件</th><th>状态</th><th>策略</th><th>延迟</th><th>接收时间</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{summary.inbox.latest.map((run) => <tr key={run.run_id} onClick={() => setSelected(run)}><td><div className="event-cell"><span className="event-type"><BarChart3 size={15} /></span><div><strong>{run.headline ?? '分析运行中'}</strong><span>{run.event_id}</span></div></div></td><td><StatusPill status={run.gate_status} runStatus={run.status} errorCode={run.error_code} /></td><td><span className="mono">{run.strategy_version}</span></td><td><span className="mono">{run.latency_ms ? `${(run.latency_ms / 1000).toFixed(1)}s` : '—'}</span></td><td><span className="time">{formatRelative(run.updated_at)}</span></td><td><ChevronRight size={16} className="row-chevron" /></td></tr>)}</tbody></table></div></div>
          <div className="side-column"><div className="panel health-panel"><div className="panel-header"><div><h2>System health</h2><p>来源、运行和资产状态</p></div><span className="health-badge"><span className="live-dot" />{summary.health_status}</span></div><div className="health-list"><HealthRow icon={Activity} label="Decision Core" value="Healthy" detail="last check 12s ago" tone="success" /><HealthRow icon={Network} label="Evidence sources" value="1 source" detail="manual text · no live feeds" tone="warning" /><HealthRow icon={BrainCircuit} label="Agent runtime" value="Ready" detail="langgraph-native.v1" tone="success" /><HealthRow icon={Database} label="Ledger" value="SQLite WAL" detail="backup 6h ago" tone="success" /></div><button className="panel-link">Open health inspector <ArrowUpRight size={14} /></button></div><div className="panel principle-panel"><div className="principle-icon"><ShieldCheck size={18} /></div><div><h3>发布由 Gate 决定</h3><p>Agent 只能提交候选。证据、反方、时间一致性和操作字段通过代码检查后，结果才会进入发布账本。</p></div></div></div>
        </section>
        <section className="bottom-grid"><div className="panel signal-panel"><div className="panel-header"><div><h2>Forecast coverage</h2><p>输出窗口和结果闭环</p></div><button className="icon-button small" aria-label="More forecast options"><Menu size={16} /></button></div><div className="coverage-row"><div className="coverage-score"><span>{summary.evaluated_count}</span><small>/ {summary.forecast_count}</small><strong>已评估</strong></div><div className="coverage-bars"><CoverageBar label="已产生预测" value={summary.forecast_count ? 100 : 0} tone="blue" /><CoverageBar label="已获得结果" value={summary.forecast_count ? Math.round((summary.evaluated_count / summary.forecast_count) * 100) : 0} tone="green" /><CoverageBar label="待补标签" value={summary.forecast_count ? Math.max(0, Math.round(((summary.forecast_count - summary.evaluated_count) / summary.forecast_count) * 100)) : 0} tone="amber" /></div></div></div><div className="panel assets-panel"><div className="panel-header"><div><h2>Personal assets</h2><p>从结果中沉淀的方法，而不是 Prompt 集合</p></div><button className="text-button">打开资产库 <ArrowUpRight size={14} /></button></div><div className="asset-items"><div className="asset-empty"><Layers3 size={17} /><div><strong>{summary.evaluated_count ? '资产提炼待审核' : '暂无已验证资产'}</strong><span>{summary.evaluated_count ? `${summary.evaluated_count} 个结果可进入 Experience 提炼流程` : '完成 Forecast Outcome 后，经验才会进入资产候选。'}</span></div></div></div></div></section>
      </div>
    </main>
    {selected && <div className="drawer-backdrop" onClick={() => setSelected(null)}><aside className="run-drawer" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">RUN INSPECTOR</span><h2>{selected.headline ?? 'Run detail'}</h2></div><button className="icon-button" onClick={() => setSelected(null)} aria-label="Close"><X size={17} /></button></div><div className="drawer-meta"><StatusPill status={selected.gate_status} /><span className="mono">{selected.run_id}</span></div><div className="drawer-section"><h3>Decision context</h3><div className="detail-row"><span>Strategy</span><strong>{selected.strategy_version}</strong></div><div className="detail-row"><span>Snapshot</span><strong>{selected.snapshot_id ?? 'pending'}</strong></div><div className="detail-row"><span>Latency</span><strong>{selected.latency_ms ? `${(selected.latency_ms / 1000).toFixed(1)}s` : 'running'}</strong></div></div><div className="drawer-section"><h3>What to inspect</h3><div className="inspect-item"><span className="check-icon"><ShieldCheck size={14} /></span><span>Evidence snapshot and citations</span><ChevronRight size={15} /></div><div className="inspect-item"><span className="check-icon"><Network size={14} /></span><span>Transmission chain and counter-thesis</span><ChevronRight size={15} /></div><div className="inspect-item"><span className="check-icon"><Gauge size={14} /></span><span>Gate rules and reason codes</span><ChevronRight size={15} /></div></div><button className="button primary drawer-action">Open full run inspector <ArrowUpRight size={15} /></button></aside></div>}
    {submitOpen && <div className="modal-backdrop" onClick={() => !submitMutation.isPending && setSubmitOpen(false)}><section className="submit-modal" role="dialog" aria-modal="true" aria-labelledby="submit-title" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">TEXT SOURCE</span><h2 id="submit-title">提交一段事件文本</h2></div><button className="icon-button" onClick={() => setSubmitOpen(false)} aria-label="Close"><X size={17} /></button></div><p className="modal-copy">文本会先保存为 Observation，再冻结 point-in-time snapshot，之后才进入研究图和 Gate。</p><textarea value={textInput} onChange={(event) => setTextInput(event.target.value)} placeholder="粘贴讲话、公告或新闻正文…" aria-label="事件文本" autoFocus /><div className="modal-footer"><span className="char-count">{textInput.length.toLocaleString()} / 200,000</span>{submitMutation.error && <span className="form-error" role="alert">{submitMutation.error.message}</span>}<button className="button secondary" onClick={() => setSubmitOpen(false)}>取消</button><button className="button primary" disabled={!textInput.trim() || submitMutation.isPending} onClick={() => submitMutation.mutate(textInput.trim())}>{submitMutation.isPending ? '提交中…' : '开始分析'} <ArrowUpRight size={15} /></button></div></section></div>}
  </div>
}

function HealthRow({ icon: Icon, label, value, detail, tone }: { icon: typeof Activity; label: string; value: string; detail: string; tone: string }) { return <div className="health-row"><span className={`health-icon ${tone}`}><Icon size={15} /></span><div><strong>{label}</strong><span>{detail}</span></div><b className={tone}>{value}</b></div> }
function CoverageBar({ label, value, tone }: { label: string; value: number; tone: string }) { return <div className="coverage-bar"><div><span>{label}</span><strong>{value}%</strong></div><div className="bar-track"><span className={tone} style={{ width: `${value}%` }} /></div></div> }
function formatRelative(value: string) { const diff = Math.max(0, Date.now() - new Date(value).getTime()); const minutes = Math.round(diff / 60000); return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago` }

export default App
