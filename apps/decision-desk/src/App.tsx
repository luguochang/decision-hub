import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, ArrowUpRight, Ban, BarChart3, Bell, BookOpen, BrainCircuit, CheckCircle2, ChevronRight, CircleHelp, Clock3, Database, FileText, Gauge, History, Inbox, Layers3, Menu, Network, PanelLeftClose, RotateCcw, Search, Settings2, ShieldCheck, Sparkles, Target, Undo2, X } from 'lucide-react'
import { useState } from 'react'
import { api, fallbackSummary } from './api/client'
import type { EvolutionOverview, PromotionDecisionCommand, PromotionReview, ResearchMemo, RunInspector } from './api/types'
import type { GateStatus, ProductHealth, RunView } from './api/types'

const nav = [
  { label: 'Inbox', icon: Inbox },
  { label: 'Decision', icon: BrainCircuit },
  { label: 'Forecasts', icon: Target },
  { label: 'Timeline', icon: Clock3 },
  { label: 'Evidence', icon: Network },
  { label: 'Health', icon: Gauge },
  { label: 'Assets', icon: Layers3 },
  { label: 'Evolution', icon: Sparkles },
]

const DOMAIN_PACK_REF = 'crypto_macro.v1'
const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
type PromotionAction = PromotionDecisionCommand['decision']

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

export function sourceHealthDisplay(health: ProductHealth | undefined) {
  const sources = health?.sources ?? []
  const degraded = sources.filter((source) => source.status === 'degraded')
  return {
    healthyCount: sources.filter((source) => source.status === 'healthy').length,
    degraded,
    detail: degraded.length
      ? degraded.map((source) => `${source.source_id}: ${source.error_code ?? 'degraded'}`).join(' · ')
      : sources.map((source) => source.source_id).join(' · ') || 'manual text only',
  }
}

function App() {
  const [collapsed, setCollapsed] = useState(false)
  const [selected, setSelected] = useState<RunView | null>(null)
  const [submitOpen, setSubmitOpen] = useState(false)
  const [textInput, setTextInput] = useState('')
  const [workspaceView, setWorkspaceView] = useState('Inbox')
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
  const healthQuery = useQuery({ queryKey: ['product-health'], queryFn: api.health, refetchInterval: 10_000, retry: false })
  const summary = summaryQuery.data ?? fallbackSummary
  const health = healthQuery.data
  const sourceHealth = sourceHealthDisplay(health)
  const inspectorQuery = useQuery({
    queryKey: ['run-inspector', selected?.run_id],
    queryFn: () => api.runInspector(selected!.run_id),
    enabled: Boolean(selected),
    retry: false,
  })
  const inspector: RunInspector | null = inspectorQuery.data ?? null
  const memosQuery = useQuery({ queryKey: ['research-memos'], queryFn: api.researchMemos, retry: false })
  const evolutionQuery = useQuery({ queryKey: ['evolution-overview'], queryFn: api.evolutionOverview, retry: false })
  const promotionMutation = useMutation({
    mutationFn: api.promotionDecision,
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['evolution-overview'] }) },
  })

  return <div className="app-shell">
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="brand"><div className="brand-mark"><ShieldCheck size={18} /></div>{!collapsed && <div><div className="brand-name">Decision Hub</div><div className="brand-sub">OWNER CONSOLE</div></div>}</div>
      <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'}>{collapsed ? <Menu size={18} /> : <PanelLeftClose size={18} />}</button>
      <div className="nav-label">WORKSPACE</div>
      <nav>{nav.map(({ label, icon: Icon }) => <button className={`nav-item ${workspaceView === label ? 'active' : ''}`} key={label} title={collapsed ? label : undefined} onClick={() => setWorkspaceView(label)}><Icon size={17} />{!collapsed && <span>{label}</span>}{!collapsed && label === 'Inbox' && <span className="nav-count">{summary.inbox.pending_count}</span>}</button>)}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="side-health"><span className="live-dot" /> <span>Core healthy</span><span className="side-health-time">12s</span></div><button className="nav-item"><Settings2 size={17} /><span>Settings</span></button><button className="nav-item"><CircleHelp size={17} /><span>Runbook</span></button></div>}
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="breadcrumbs"><span>Decision Desk</span><ChevronRight size={14} /><strong>Inbox</strong></div><div className="top-actions"><div className="search-shell"><Search size={15} /><input placeholder="Search events, runs, assets" aria-label="Search" /></div><button className="icon-button" aria-label="Notifications"><Bell size={17} /><span className="notification-dot" /></button><div className="owner-avatar">C</div></div></header>
      <div className="content-wrap">
        <section className="page-heading"><div><div className="eyebrow"><span className="live-dot" />LIVE DECISION SYSTEM</div><h1>Inbox</h1><p>事件、证据和待确认动作集中在这里。系统不会把未经 Gate 的候选当成结论。</p></div><div className="heading-actions"><button className="button secondary" onClick={() => setSubmitOpen(true)}><FileText size={15} />提交文本</button><button className="button primary" onClick={() => setSubmitOpen(true)}><Sparkles size={15} />新建分析</button></div></section>
        <section className="metrics-grid"><Metric icon={Inbox} label="待处理事件" value={summary.inbox.pending_count} hint="需要人工确认" tone="amber" /><Metric icon={Activity} label="运行中" value={summary.inbox.running_count} hint="自动刷新 · 5s" tone="blue" /><Metric icon={Target} label="近 30 天预测" value={summary.forecast_count} hint={`${summary.evaluated_count} 已有结果`} tone="green" /><Metric icon={Database} label="当前策略" value="v1" hint={summary.active_pack} tone="purple" /></section>
        <section className="workspace-grid">
          <div className="panel inbox-panel"><div className="panel-header"><div><h2>Recent events</h2><p>按接收时间排序 · Point-in-time evidence</p></div><button className="text-button">查看全部 <ArrowUpRight size={14} /></button></div><div className="table-wrap"><table><thead><tr><th>事件</th><th>状态</th><th>策略</th><th>延迟</th><th>接收时间</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{summary.inbox.latest.map((run) => <tr key={run.run_id} onClick={() => setSelected(run)}><td><div className="event-cell"><span className="event-type"><BarChart3 size={15} /></span><div><strong>{run.headline ?? '分析运行中'}</strong><span>{run.event_id}</span></div></div></td><td><StatusPill status={run.gate_status} runStatus={run.status} errorCode={run.error_code} /></td><td><span className="mono">{run.strategy_version}</span></td><td><span className="mono">{run.latency_ms ? `${(run.latency_ms / 1000).toFixed(1)}s` : '—'}</span></td><td><span className="time">{formatRelative(run.updated_at)}</span></td><td><ChevronRight size={16} className="row-chevron" /></td></tr>)}</tbody></table></div></div>
          <div className="side-column"><div className="panel health-panel"><div className="panel-header"><div><h2>System health</h2><p>来源、运行和资产状态</p></div><span className="health-badge"><span className="live-dot" />{health?.status ?? summary.health_status}</span></div><div className="health-list"><HealthRow icon={Activity} label="Decision Core" value={health?.status === 'degraded' ? 'Degraded' : 'Healthy'} detail={`${health?.running_runs ?? summary.inbox.running_count} running · ${health?.failed_runs ?? 0} failed`} tone={health?.status === 'degraded' ? 'warning' : 'success'} /><HealthRow icon={Network} label="Evidence sources" value={health ? `${sourceHealth.healthyCount}/${health.sources.length} healthy` : 'Loading'} detail={sourceHealth.detail} tone={sourceHealth.degraded.length || !health?.sources.length ? 'warning' : 'success'} /><HealthRow icon={BrainCircuit} label="Agent runtime" value="Ready" detail="langgraph-native.v1" tone="success" /><HealthRow icon={Database} label="Ledger" value="SQLite WAL" detail="cursor, runs and outbox durable" tone="success" /></div><button className="panel-link">Open health inspector <ArrowUpRight size={14} /></button></div><div className="panel principle-panel"><div className="principle-icon"><ShieldCheck size={18} /></div><div><h3>发布由 Gate 决定</h3><p>Agent 只能提交候选。证据、反方、时间一致性和操作字段通过代码检查后，结果才会进入发布账本。</p></div></div></div>
        </section>
        <section className="bottom-grid"><div className="panel signal-panel"><div className="panel-header"><div><h2>Forecast coverage</h2><p>输出窗口和结果闭环</p></div><button className="icon-button small" aria-label="More forecast options"><Menu size={16} /></button></div><div className="coverage-row"><div className="coverage-score"><span>{summary.evaluated_count}</span><small>/ {summary.forecast_count}</small><strong>已评估</strong></div><div className="coverage-bars"><CoverageBar label="已产生预测" value={summary.forecast_count ? 100 : 0} tone="blue" /><CoverageBar label="已获得结果" value={summary.forecast_count ? Math.round((summary.evaluated_count / summary.forecast_count) * 100) : 0} tone="green" /><CoverageBar label="待补标签" value={summary.forecast_count ? Math.max(0, Math.round(((summary.forecast_count - summary.evaluated_count) / summary.forecast_count) * 100)) : 0} tone="amber" /></div></div></div><div className="panel assets-panel"><div className="panel-header"><div><h2>Personal assets</h2><p>从结果中沉淀的方法，而不是 Prompt 集合</p></div><button className="text-button">打开资产库 <ArrowUpRight size={14} /></button></div><div className="asset-items"><div className="asset-empty"><Layers3 size={17} /><div><strong>{summary.evaluated_count ? '资产提炼待审核' : '暂无已验证资产'}</strong><span>{summary.evaluated_count ? `${summary.evaluated_count} 个结果可进入 Experience 提炼流程` : '完成 Forecast Outcome 后，经验才会进入资产候选。'}</span></div></div></div></div></section>
      </div>
    </main>
    {selected && <RunDrawer selected={selected} inspector={inspector} loading={inspectorQuery.isLoading} failed={inspectorQuery.isError} onClose={() => setSelected(null)} />}
    {workspaceView !== 'Inbox' && <WorkbenchDrawer view={workspaceView} memos={memosQuery.data ?? []} evolution={evolutionQuery.data ?? null} loading={memosQuery.isLoading || evolutionQuery.isLoading} failed={memosQuery.isError || evolutionQuery.isError} deciding={promotionMutation.isPending} onReview={(candidateId, evaluationRefs) => api.promotionReview(DOMAIN_PACK_REF, { candidate_id: candidateId, evaluation_refs: evaluationRefs })} onDecision={(payload) => promotionMutation.mutateAsync({ ...payload, domain_pack_ref: DOMAIN_PACK_REF })} onClose={() => setWorkspaceView('Inbox')} />}
    {submitOpen && <div className="modal-backdrop" onClick={() => !submitMutation.isPending && setSubmitOpen(false)}><section className="submit-modal" role="dialog" aria-modal="true" aria-labelledby="submit-title" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">TEXT SOURCE</span><h2 id="submit-title">提交一段事件文本</h2></div><button className="icon-button" onClick={() => setSubmitOpen(false)} aria-label="Close"><X size={17} /></button></div><p className="modal-copy">文本会先保存为 Observation，再冻结 point-in-time snapshot，之后才进入研究图和 Gate。</p><textarea value={textInput} onChange={(event) => setTextInput(event.target.value)} placeholder="粘贴讲话、公告或新闻正文…" aria-label="事件文本" autoFocus /><div className="modal-footer"><span className="char-count">{textInput.length.toLocaleString()} / 200,000</span>{submitMutation.error && <span className="form-error" role="alert">{submitMutation.error.message}</span>}<button className="button secondary" onClick={() => setSubmitOpen(false)}>取消</button><button className="button primary" disabled={!textInput.trim() || submitMutation.isPending} onClick={() => submitMutation.mutate(textInput.trim())}>{submitMutation.isPending ? '提交中…' : '开始分析'} <ArrowUpRight size={15} /></button></div></section></div>}
  </div>
}

function WorkbenchDrawer({ view, memos, evolution, loading, failed, deciding, onReview, onDecision, onClose }: { view: string; memos: ResearchMemo[]; evolution: EvolutionOverview | null; loading: boolean; failed: boolean; deciding: boolean; onReview: (candidateId: string, evaluationRefs: string[]) => Promise<PromotionReview>; onDecision: (payload: PromotionDecisionCommand) => Promise<unknown>; onClose: () => void }) {
  const title = view === 'Evolution' ? 'Evolution & Promotion' : view === 'Assets' ? 'Personal assets' : 'Research workbench'
  return <div className="drawer-backdrop" onClick={onClose}><aside className="run-drawer workbench-drawer" aria-label={title} onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><div><span className="eyebrow">DECISION WORKBENCH</span><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17} /></button></div>
    {loading && <div className="inspector-message"><Activity size={16} />正在读取研究与评测资产…</div>}
    {failed && <div className="inspector-message danger"><AlertTriangle size={16} />工作台数据不可用，没有用缓存 JSON 补写状态。</div>}
    {!loading && !failed && view !== 'Evolution' && <>
      <section className="drawer-section"><h3>Research memos</h3>{memos.length === 0 && <EmptyLine text="暂无研究备忘录；从已存在的 Run/Snapshot 提交后才会出现。" />}{memos.map((memo) => <article className="memo-item" key={memo.memo_id}><div className="memo-title"><strong>{memo.claims[0]}</strong><span className={`status-pill ${memo.status === 'accepted' ? 'success' : 'neutral'}`}>{memo.status}</span></div><small>{memo.domain_pack_ref} · {formatDate(memo.created_at)}</small><EvidenceGroup title="Evidence" items={memo.evidence_refs} mono /><EvidenceGroup title="Counterpoints" items={memo.counterpoints} /><EvidenceGroup title="Uncertainties" items={memo.uncertainties} /></article>)}</section>
      <section className="drawer-section"><h3>Reusable assets</h3><AssetRows evolution={evolution} /></section>
    </>}
    {!loading && !failed && view === 'Evolution' && <EvolutionPanel evolution={evolution} deciding={deciding} onReview={onReview} onDecision={onDecision} />}
  </aside></div>
}

function AssetRows({ evolution }: { evolution: EvolutionOverview | null }) {
  if (!evolution || (!evolution.datasets.length && !evolution.candidates.length)) return <EmptyLine text="暂无经过登记的数据集或候选版本" />
  return <div className="asset-ledger">{evolution.datasets.map((item) => <div className="asset-ledger-row" key={item.dataset_id}><Database size={15} /><div><strong>{item.dataset_id}</strong><small>{item.split} · {item.fixture_refs.length} fixtures · PIT locked</small></div></div>)}{evolution.candidates.map((item) => <div className="asset-ledger-row" key={item.candidate_id}><Layers3 size={15} /><div><strong>{item.version}</strong><small>{item.candidate_type} · {item.status}</small></div></div>)}</div>
}

export function EvolutionPanel({ evolution, deciding, onReview, onDecision }: { evolution: EvolutionOverview | null; deciding: boolean; onReview: (candidateId: string, evaluationRefs: string[]) => Promise<PromotionReview>; onDecision: (payload: PromotionDecisionCommand) => Promise<unknown> }) {
  const [draft, setDraft] = useState<{ action: PromotionAction; candidate: EvolutionOverview['candidates'][number]; evaluationRefs: string[]; generation: number } | null>(null)
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [review, setReview] = useState<PromotionReview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null)
  if (!evolution) return <EmptyLine text="Evolution 数据不可用" />
  const pointer = evolution.pointers.find((item) => item.domain_pack_ref === DOMAIN_PACK_REF)
  const historicalTargets = new Set(evolution.decisions.flatMap((item) => [item.candidate_id, item.previous_candidate_id].filter((value): value is string => Boolean(value))))

  const evidenceRefs = (candidateId: string) => {
    const experimentIds = new Set(evolution.experiments.filter((item) => item.candidate_refs.includes(candidateId)).map((item) => item.experiment_id))
    return evolution.results.filter((item) => item.candidate_id === candidateId && experimentIds.has(item.experiment_id)).map((item) => item.result_id)
  }
  const openDecision = async (action: PromotionAction, candidate: EvolutionOverview['candidates'][number]) => {
    const refs = action === 'promote' ? evidenceRefs(candidate.candidate_id) : []
    setDraft({ action, candidate, evaluationRefs: refs, generation: pointer?.generation ?? 0 })
    setReason('')
    setConfirmed(false)
    setDecisionError(null)
    setDecisionNotice(null)
    setReview(null)
    if (action === 'promote') {
      setReviewing(true)
      try { setReview(await onReview(candidate.candidate_id, refs)) }
      catch (error) { setDecisionError(error instanceof Error ? error.message : 'promotion_review_failed') }
      finally { setReviewing(false) }
    }
  }
  const submitDecision = async () => {
    if (!draft || !reason.trim() || !confirmed) return
    setDecisionError(null)
    try {
      await onDecision({ request_id: crypto.randomUUID(), candidate_id: draft.candidate.candidate_id, owner: OWNER_ID, decision: draft.action, reason: reason.trim(), evaluation_refs: draft.evaluationRefs, expected_generation: draft.generation })
      setDecisionNotice(`${actionLabel(draft.action)} 已写入审计记录；active pointer 以 Core 返回结果为准。`)
      setDraft(null)
    } catch (error) {
      setDecisionError(error instanceof Error ? error.message : 'promotion_decision_failed')
    }
  }

  return <>
    <section className="drawer-section"><h3>Active pointer</h3>{!pointer && <EmptyLine text="尚无候选通过人工 Promotion" />}{pointer && <div className="promotion-row"><ShieldCheck size={16} /><div><strong>{pointer.domain_pack_ref}</strong><small>{pointer.candidate_id} · generation {pointer.generation}</small></div></div>}</section>
    <section className="drawer-section"><h3>Experiments & baseline deltas</h3>{evolution.experiments.length === 0 && <EmptyLine text="尚无 replay / holdout / shadow 实验" />}{evolution.experiments.map((experiment) => { const results = evolution.results.filter((result) => result.experiment_id === experiment.experiment_id); const baseline = results.find((result) => result.candidate_id === experiment.baseline_ref); return <article className="experiment-row" key={experiment.experiment_id}><div><strong>{experiment.experiment_id}</strong><span>{experiment.status}</span></div><small>{experiment.dataset_id} · baseline {experiment.baseline_ref}</small>{results.map((result) => <div className="result-metrics" key={result.result_id}><span>{result.candidate_id}</span><b>{result.sample_count} samples</b><b>Brier {result.brier_score ?? 'unknown'} {result !== baseline && <em>{formatMetricDelta(result.brier_score, baseline?.brier_score)}</em>}</b><b>{result.p95_latency_ms ?? 'unknown'}ms p95</b><b className={result.safety_violations ? 'danger-text' : ''}>{result.safety_violations} safety</b></div>)}</article>})}</section>
    <section className="drawer-section"><h3>Candidate registry</h3>{evolution.candidates.length === 0 && <EmptyLine text="暂无候选版本" />}{evolution.candidates.map((candidate) => { const active = pointer?.candidate_id === candidate.candidate_id; const refs = evidenceRefs(candidate.candidate_id); const canRollback = !active && historicalTargets.has(candidate.candidate_id); return <div className="asset-ledger-row candidate-row" key={candidate.candidate_id}><Layers3 size={15} /><div><strong>{candidate.version}</strong><small>{candidate.candidate_type} · {candidate.status} · {candidate.content_hash.slice(0, 12)}…</small></div><div className="candidate-actions">{active ? <span className="candidate-state"><ShieldCheck size={14} />active</span> : <>{candidate.status !== 'rejected' && <button className="icon-action promote" disabled={deciding || refs.length === 0} onClick={() => void openDecision('promote', candidate)} title={refs.length ? 'Review and promote' : '需要 challenger 实验结果'} aria-label={`Promote ${candidate.version}`}><CheckCircle2 size={15} /></button>}{candidate.status !== 'rejected' && <button className="icon-action reject" disabled={deciding} onClick={() => void openDecision('reject', candidate)} title="Reject candidate" aria-label={`Reject ${candidate.version}`}><Ban size={15} /></button>}{canRollback && <button className="icon-action rollback" disabled={deciding} onClick={() => void openDecision('rollback', candidate)} title="Rollback to this version" aria-label={`Rollback to ${candidate.version}`}><Undo2 size={15} /></button>}</>}</div></div>})}</section>
    <section className="drawer-section"><h3>Promotion audit history</h3>{evolution.decisions.length === 0 && <EmptyLine text="暂无人工 Promotion / Reject / Rollback 记录" />}{evolution.decisions.map((item) => <div className="audit-row" key={item.decision_id}><History size={15} /><div><strong>{actionLabel(item.decision)} · {item.candidate_id}</strong><small>{item.owner} · generation {item.resulting_generation} · {formatDate(item.created_at)}</small><p>{item.reason}</p></div></div>)}</section>
    {decisionNotice && <div className="decision-notice" role="status"><CheckCircle2 size={15} />{decisionNotice}</div>}
    {decisionError && !draft && <div className="form-error decision-error" role="alert">操作未执行：{decisionError}</div>}
    {draft && <div className="modal-backdrop nested-modal" onClick={() => !deciding && setDraft(null)}><section className="promotion-modal" role="dialog" aria-modal="true" aria-labelledby="promotion-title" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">OWNER DECISION · GENERATION {draft.generation}</span><h2 id="promotion-title">{actionLabel(draft.action)} {draft.candidate.version}</h2></div><button className="icon-button" onClick={() => setDraft(null)} aria-label="Close"><X size={17} /></button></div><p className="modal-copy">{actionWarning(draft.action)}</p>{draft.action === 'promote' && <PromotionReviewPanel review={review} reviewing={reviewing} />}
      <label className="reason-field"><span>决策理由</span><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="记录本次决定依据、风险和预期回滚条件…" autoFocus /></label>
      <label className="confirm-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我确认这是 owner 人工决定，并理解 Core Gate 仍会独立裁决。</span></label>
      {decisionError && <div className="form-error decision-error" role="alert">操作未执行：{decisionError}</div>}
      <div className="modal-footer promotion-footer"><button className="button secondary" disabled={deciding} onClick={() => setDraft(null)}>取消</button><button className={`button decision-submit ${draft.action}`} disabled={deciding || reviewing || !reason.trim() || !confirmed || (draft.action === 'promote' && !review?.eligible)} onClick={() => void submitDecision()}>{deciding ? <RotateCcw size={14} className="spin" /> : draft.action === 'rollback' ? <Undo2 size={14} /> : draft.action === 'reject' ? <Ban size={14} /> : <CheckCircle2 size={14} />}{deciding ? '提交中' : `确认${actionLabel(draft.action)}`}</button></div>
    </section></div>}
  </>
}

function PromotionReviewPanel({ review, reviewing }: { review: PromotionReview | null; reviewing: boolean }) {
  if (reviewing) return <div className="promotion-review loading"><RotateCcw size={15} className="spin" />Core 正在计算 replay / holdout / shadow 硬门…</div>
  if (!review) return <div className="promotion-review failed"><AlertTriangle size={15} />无法取得 Core Gate review，禁止晋级。</div>
  return <div className={`promotion-review ${review.eligible ? 'eligible' : 'failed'}`}><div className="review-heading"><div><strong>{review.eligible ? 'Core Gate ready' : 'Core Gate blocked'}</strong><span>{review.evaluation_refs.length} evaluation refs · 仅 owner 确认后才能改变 pointer</span></div><span className={`status-pill ${review.eligible ? 'success' : 'danger'}`}>{review.eligible ? 'ELIGIBLE' : 'BLOCKED'}</span></div><div className="stage-deltas">{review.deltas.map((item) => <div key={item.result_id}><span>{item.stage}</span><strong>Brier Δ {formatSigned(item.brier_delta)}</strong><small>{item.sample_count} samples · {item.p95_latency_ms ?? 'unknown'}ms · safety {item.safety_violations}</small></div>)}</div><div className="review-checks">{review.checks.map((item) => <div className={item.status} key={item.rule_id}><span>{item.status === 'pass' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}</span><div><strong>{item.rule_id}</strong><small>{item.detail}</small></div></div>)}</div></div>
}

function actionLabel(action: PromotionAction) { return action === 'promote' ? 'Promote' : action === 'rollback' ? 'Rollback' : 'Reject' }
function actionWarning(action: PromotionAction) { return action === 'promote' ? '晋级会在 Core 重新校验硬门后原子更新 active pointer；Agent、DSH 和前端均不能绕过。' : action === 'rollback' ? '回滚会产生新的审计事件和 generation，不会覆盖历史版本。' : '拒绝只改变非 active 候选状态，不会修改当前 active pointer。' }
function formatMetricDelta(value: number | null, baseline: number | null | undefined) { return value === null || baseline == null ? '' : `(${formatSigned(value - baseline)})` }
function formatSigned(value: number | null) { return value === null ? 'unknown' : `${value >= 0 ? '+' : ''}${value.toFixed(4)}` }

function RunDrawer({ selected, inspector, loading, failed, onClose }: { selected: RunView; inspector: RunInspector | null; loading: boolean; failed: boolean; onClose: () => void }) {
  const artifact = inspector?.artifact
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside className="run-drawer" aria-label="Run inspector" onClick={(event) => event.stopPropagation()}>
      <div className="drawer-header"><div><span className="eyebrow">RUN INSPECTOR</span><h2>{artifact?.headline ?? selected.headline ?? 'Run detail'}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17} /></button></div>
      <div className="drawer-meta"><StatusPill status={artifact?.gate_status ?? selected.gate_status} runStatus={selected.status} errorCode={selected.error_code} /><span className="mono ellipsis" title={selected.run_id}>{selected.run_id}</span></div>
      {loading && <div className="inspector-message" role="status" aria-live="polite"><Activity size={16} />正在读取规范化运行数据…</div>}
      {failed && <div className="inspector-message danger" role="alert"><AlertTriangle size={16} />Inspector 数据不可用，运行结果未被推断或补写。</div>}
      {inspector && <>
        <section className="drawer-section"><h3>Run context & versions</h3><div className="detail-grid"><Detail label="Status" value={inspector.run.status} /><Detail label="Strategy" value={inspector.versions.strategy_version} /><Detail label="Runtime" value={inspector.versions.runtime_version} /><Detail label="Domain pack" value={inspector.versions.pack_version ?? 'unversioned'} /><Detail label="Provider" value={formatList(inspector.versions.provider_ids)} /><Detail label="Model" value={formatList(inspector.versions.models)} /><Detail label="Schema" value={formatList(inspector.versions.schema_versions)} /><Detail label="Pricing" value={formatList(inspector.versions.pricing_versions)} /><Detail label="PIT cutoff" value={formatDate(inspector.snapshot_cutoff_at)} /><Detail label="Snapshot hash" value={shortHash(inspector.snapshot_hash)} /><Detail label="Latency" value={inspector.run.latency_ms === null ? '—' : `${inspector.run.latency_ms}ms`} /><Detail label="Cost" value={inspector.run.cost_usd === null ? 'unknown' : `$${inspector.run.cost_usd.toFixed(4)}`} /></div>{inspector.run.error_code && <div className="error-callout"><AlertTriangle size={15} /><span><strong>{inspector.run.error_code}</strong>本次运行已 fail-closed，没有用错误结果绕过 Gate。</span></div>}</section>
        <section className="drawer-section"><h3>Orchestration</h3><div className="detail-grid"><Detail label="Mode" value={inspector.orchestration.mode} /><Detail label="Supervisor" value={inspector.orchestration.supervisor_role ?? 'fixed graph'} /><Detail label="Replans" value={`${inspector.orchestration.replan_count} / 1`} /><Detail label="Experiments" value={formatList(inspector.orchestration.experiment_refs)} /></div><CapabilityCoverage label="Required" items={inspector.orchestration.required_capabilities} /><CapabilityCoverage label="Planned" items={inspector.orchestration.planned_capabilities} /><CapabilityCoverage label="Covered" items={inspector.orchestration.specialist_coverage} tone="success" /><CapabilityCoverage label="Missing" items={inspector.orchestration.missing_capabilities} tone={inspector.orchestration.missing_capabilities.length ? 'danger' : 'neutral'} /></section>
        <section className="drawer-section"><h3>Execution steps</h3>{inspector.steps.length === 0 && <EmptyLine text="尚无步骤记录" />}{inspector.steps.map((step) => <div className={`step-row ${step.status}`} key={step.step_id}><span className="step-attempt">{step.attempt}</span><div><strong>{step.step_name}</strong><small>{step.status} · {step.latency_ms === null ? 'pending' : `${step.latency_ms}ms`}{step.error_code ? ` · ${step.error_code}` : ''}</small></div></div>)}</section>
        <section className="drawer-section"><h3>Activity timeline</h3><div className="timeline-list">{inspector.timeline.length === 0 && <EmptyLine text="尚无活动记录" />}{inspector.timeline.map((item) => <div className="timeline-item" key={`${item.sequence_no}-${item.event_type}`}><span className="timeline-index">{item.sequence_no}</span><div><strong>{item.event_type}</strong><small>{formatDate(item.occurred_at)}{item.reference_id ? ` · ${item.reference_type}: ${item.reference_id}` : ''}</small></div></div>)}</div></section>
        <section className="drawer-section"><h3>Agent calls</h3>{inspector.calls.length === 0 && <EmptyLine text="没有模型调用记录" />}{inspector.calls.map((call) => <div className={`call-row ${call.status === 'failed' ? 'failed' : ''}`} key={call.call_id}><span className="check-icon">{call.status === 'failed' ? <AlertTriangle size={14} /> : <BrainCircuit size={14} />}</span><div><strong>{call.role} <b>attempt {call.attempt}</b></strong><small>{call.provider_id ?? call.runtime_id ?? 'runtime unknown'} · {call.model ?? call.runtime_version ?? 'model unknown'} · {call.api_mode ?? 'offline'}</small><small>{call.latency_ms === null ? 'pending' : `${call.latency_ms}ms`} · {call.total_tokens === null ? 'tokens unknown' : `${call.total_tokens} tokens`} · cost {call.cost_status}{call.pricing_version ? ` (${call.pricing_version})` : ''}{call.error_code ? ` · ${call.error_code}` : ''}</small></div></div>)}</section>
        <section className="drawer-section"><h3>Evidence lineage & PIT</h3>{inspector.evidence_lineage.length === 0 && <EmptyLine text="Snapshot 尚无规范化证据血缘" />}{inspector.evidence_lineage.map((evidence) => <article className="lineage-item" key={evidence.evidence_id}><div className="lineage-title"><div><strong>{evidence.source_id}</strong><small>{evidence.source_type} · {evidence.evidence_id}</small></div><span className="hash-chip" title={evidence.content_hash}>{shortHash(evidence.content_hash)}</span></div><div className="lineage-times"><TimeStamp label="Observed" value={evidence.observed_at} /><TimeStamp label="Published" value={evidence.published_at} /><TimeStamp label="Received" value={evidence.received_at} /><TimeStamp label="Cutoff" value={evidence.cutoff_at} /></div></article>)}</section>
        <section className="drawer-section"><h3>Decision evidence</h3>{!artifact && <EmptyLine text="运行未生成 Artifact" />}{artifact && <><p className="artifact-summary">{artifact.summary}</p><EvidenceGroup title="Facts" items={artifact.facts} /><EvidenceGroup title="Inferences" items={artifact.inferences} /><EvidenceGroup title="Counter-thesis" items={artifact.counter_thesis ? [artifact.counter_thesis] : []} /><EvidenceGroup title="Transmission chain" items={artifact.transmission_chain} ordered /><EvidenceGroup title="Citations" items={artifact.citations} mono /></>}</section>
        <section className="drawer-section"><h3>Gate decisions</h3>{!artifact?.gate_decisions.length && <EmptyLine text="没有 Gate 记录" />}{artifact?.gate_decisions.map((gate) => <div className="gate-row" key={gate.rule_id}><span className={`gate-mark ${gate.status}`}>{gate.status}</span><div><strong>{gate.rule_id}</strong><small>{gate.reason_code}</small></div></div>)}</section>
        <section className="drawer-section"><h3>Forecasts</h3>{!artifact?.forecasts.length && <EmptyLine text="没有发布预测" />}<div className="forecast-list">{artifact?.forecasts.map((forecast) => <div className="forecast-row" key={forecast.forecast_id}><div><span>{forecast.horizon}</span><strong>{forecast.direction}</strong></div><b>{Math.round(forecast.probability * 100)}%</b><p><em>Trigger</em>{forecast.trigger}</p><p><em>Invalidation</em>{forecast.invalidation}</p></div>)}</div></section>
        <section className="drawer-section"><h3>Outcome evaluation</h3>{inspector.evaluations.length === 0 && <EmptyLine text={`${artifact?.forecasts.length ?? 0} 个 Forecast 尚未录入 Outcome`} />}{inspector.evaluations.map((evaluation) => <div className="evaluation-row" key={evaluation.evaluation_id}><BookOpen size={15} /><div><strong>Brier {evaluation.brier_score.toFixed(4)} · Net {evaluation.net_return_pct.toFixed(2)}%</strong><small>{evaluation.label_status} · {formatDate(evaluation.evaluated_at)}</small></div></div>)}</section>
      </>}
    </aside>
  </div>
}

function Detail({ label, value }: { label: string; value: string }) { return <div className="detail-cell"><span>{label}</span><strong title={value}>{value}</strong></div> }
function CapabilityCoverage({ label, items, tone = 'neutral' }: { label: string; items: string[]; tone?: 'neutral' | 'success' | 'danger' }) { return <div className="capability-row"><strong>{label}</strong><div>{items.length ? items.map((item) => <span className={`capability-chip ${tone}`} key={`${label}-${item}`}>{item}</span>) : <span className="capability-empty">none</span>}</div></div> }
function TimeStamp({ label, value }: { label: string; value: string | null }) { return <div><span>{label}</span><strong>{formatDate(value)}</strong></div> }
function EmptyLine({ text }: { text: string }) { return <div className="empty-line">{text}</div> }
function EvidenceGroup({ title, items, ordered = false, mono = false }: { title: string; items: string[]; ordered?: boolean; mono?: boolean }) { if (!items.length) return null; const Tag = ordered ? 'ol' : 'ul'; return <div className="evidence-group"><strong>{title}</strong><Tag className={mono ? 'mono-list' : undefined}>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</Tag></div> }
function shortHash(value: string | null) { return value ? `${value.slice(0, 12)}…` : 'pending' }
function formatDate(value: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatList(values: string[]) { return values.length ? values.join(' · ') : 'unknown' }

function HealthRow({ icon: Icon, label, value, detail, tone }: { icon: typeof Activity; label: string; value: string; detail: string; tone: string }) { return <div className="health-row"><span className={`health-icon ${tone}`}><Icon size={15} /></span><div><strong>{label}</strong><span>{detail}</span></div><b className={tone}>{value}</b></div> }
function CoverageBar({ label, value, tone }: { label: string; value: number; tone: string }) { return <div className="coverage-bar"><div><span>{label}</span><strong>{value}%</strong></div><div className="bar-track"><span className={tone} style={{ width: `${value}%` }} /></div></div> }
function formatRelative(value: string) { const diff = Math.max(0, Date.now() - new Date(value).getTime()); const minutes = Math.round(diff / 60000); return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago` }

export default App
