import { useModalScrollLock } from './product/useModalScrollLock'
import { CandidateExecutionPanel } from './product/CandidateExecutionPanel'
import { executionScopeName, executionScopeLimit } from './product/executionScope'
import { EvolutionExperimentCards } from './product/EvolutionExperimentCards'
import { PromotionReviewPanel } from './product/PromotionReviewPanel'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, Ban, BookOpen, BrainCircuit, CalendarDays, CheckCircle2, ChevronRight, Database, History, Inbox, Layers3, Menu, Network, PanelLeftClose, RotateCcw, ScanSearch, Settings2, ShieldCheck, Target, Undo2, X } from 'lucide-react'
import { lazy, useEffect, useMemo, useRef, useState } from 'react'
import { confirmEvolutionReceipt, dismissEvolutionRefusal, evolutionPendingDecisions, evolutionRefusal, freezeEvolutionDecision, originalEvolutionCommand, rememberEvolutionRefusal, type PendingEvolutionDecision } from './product/evolutionDecisionReceipts'
import { api } from './api/client'
import type { EvolutionOverview, PromotionDecisionCommand, PromotionReview, ResearchMemo, RunInspector } from './api/types'
import type { ProductHealth, RunView } from './api/types'
import type { DesignScene } from './DesignStatePage'
import { RouteContent } from './product/RouteContent'
const EvolutionReadinessPanel = lazy(() => import('./product/EvolutionReadinessPanel').then(module => ({ default: module.EvolutionReadinessPanel })))
const OperationsPage = lazy(() => import('./OperationsPage').then(module => ({ default: module.OperationsPage })))
const EvolutionExecutionStatus = lazy(() => import('./OperationsPage').then(module => ({ default: module.EvolutionExecutionStatus })))
const EvolutionJobQueue = lazy(() => import('./OperationsPage').then(module => ({ default: module.EvolutionJobQueue })))
const PageTitle = lazy(() => import('./OperationsPage').then(module => ({ default: module.PageTitle })))
const DesignStatePage = lazy(() => import('./DesignStatePage').then(module => ({ default: module.DesignStatePage })))
const ResearchPage = lazy(() => import('./research/ResearchPage').then(module => ({ default: module.ResearchPage })))
import { ProductPages, type ProductScene } from './product/ProductPages'
import { bootstrapSettingsSession } from './api/decisionSettings'
const DataJobsPage = lazy(() => import('./product/DataJobsPage').then(module => ({ default: module.DataJobsPage })))
const HistoryStatusPage = lazy(() => import('./product/HistoryStatusPage').then(module => ({ default: module.HistoryStatusPage })))
const ResultsPage = lazy(() => import('./product/ResultsPage').then(module => ({ default: module.ResultsPage })))
const FactsPage = lazy(() => import('./product/FactsPage').then(module => ({ default: module.FactsPage })))
const StudiesPage = lazy(() => import('./product/StudiesPage').then(module => ({ default: module.StudiesPage })))
const DeliveryPage = lazy(() => import('./product/DeliveryPage').then(module => ({ default: module.DeliveryPage })))
const SourceAuditPage = lazy(() => import('./SourceAuditPage').then(module => ({ default: module.SourceAuditPage })))
import { researchServiceHealth, sourceLabel } from './product/operationLabels'
import { LedgerRunStatus as StatusPill, ledgerRunStatus } from './product/LedgerRunStatus'
import { ObservationSubmitDialog } from './product/ObservationSubmitDialog'
import { directionLabel } from './research/technicalPresentation'
import { legacyContent, legacyCost, legacyDuration, legacyNumber, legacyReportSummary, legacyRunName } from './product/legacyRunPresentation'
const LegacyLedgerPage = lazy(() => import('./product/LegacyLedgerPage').then(module => ({ default: module.LegacyLedgerPage })))

export const nav = [
  { label: '今日研究', icon: ScanSearch, scene: 'home' },
  { label: '市场与事件', icon: CalendarDays, scene: 'market' },
  { label: '研究', icon: Layers3, scene: 'research' },
  { label: '资料', icon: BookOpen, scene: 'library' },
  { label: '管理与审计', icon: Settings2, scene: 'management' },
]

type AppRoute = { scene: string; params: URLSearchParams }

function readAppRoute(): AppRoute {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [scene = 'home', query = ''] = raw.split('?')
  return { scene: scene || 'home', params: new URLSearchParams(query) }
}

function appRouteHref(scene: string, params: Record<string, string | undefined> = {}): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value) })
  const suffix = query.toString()
  return `#/${scene}${suffix ? `?${suffix}` : ''}`
}

function workspaceForScene(scene: string): string {
  if (scene === 'Inbox' || scene === 'Operations' || scene === 'Evolution') return scene
  if (scene === 'home') return '今日研究'
  if (scene === 'market' || scene === 'facts' || scene === 'calendar-all' || scene === 'calendar-upcoming' || scene === 'calendar-history' || scene === 'event') return '市场与事件'
  if (scene === 'delivery' || scene === 'studies' || scene === 'research' || scene === 'results' || scene === 'runs' || scene === 'report' || scene === 'report-incomplete' || scene === 'technical' || scene === 'evidence' || scene === 'graph' || scene === 'graph-node') return '研究'
  if (scene === 'library' || scene === 'upload' || scene.startsWith('document-')) return '资料'
  if (scene === 'settings' || scene === 'management' || scene === 'history-status' || scene === 'data-jobs' || scene === 'sources' || scene === 'states' || scene === 'catalog' || scene.startsWith('dsh')) return '管理与审计'
  return '研究'
}

const researchPageScenes = new Set(['report', 'report-incomplete', 'technical', 'evidence', 'graph', 'graph-node'])
const productPageScenes = new Set<ProductScene>(['home', 'market', 'research', 'management', 'settings'])


const DOMAIN_PACK_REF = 'crypto_macro.v1'
const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
type PromotionAction = PromotionDecisionCommand['decision']

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
  useEffect(() => { void bootstrapSettingsSession(); setAppRoute(readAppRoute()) }, [])
  const [collapsed, setCollapsed] = useState(false)
  const [selected, setSelected] = useState<RunView | null>(null)
  const [submitOpen, setSubmitOpen] = useState(false)
  const submitOrigin = useRef<string | null>(null)
  const [workspaceView, setWorkspaceView] = useState(() => workspaceForScene(readAppRoute().scene))
  const [appRoute, setAppRoute] = useState<AppRoute>(() => readAppRoute())
  const queryClient = useQueryClient()
  const navigate = (scene: string, params: Record<string, string | undefined> = {}) => {
    if (['event', 'Operations'].includes(scene) && !params.back) params = { ...params, back: window.location.hash || '#/home' }
    if (scene !== appRoute.scene) {
      const scroller = document.scrollingElement
      if (scroller) scroller.scrollTop = 0
    }
    window.history.pushState(null, '', appRouteHref(scene, params))
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
  useEffect(() => {
    const syncRoute = () => {
      setAppRoute(readAppRoute())
      if (submitOrigin.current !== null && window.location.hash !== submitOrigin.current) setSubmitOpen(false)
    }
    window.addEventListener('popstate', syncRoute)
    window.addEventListener('hashchange', syncRoute)
    return () => {
      window.removeEventListener('popstate', syncRoute)
      window.removeEventListener('hashchange', syncRoute)
    }
  }, [])
  useEffect(() => { setWorkspaceView(workspaceForScene(appRoute.scene)); setSelected(null) }, [appRoute.scene])
  const peripheralEnabled = workspaceView === 'Evolution'
  const ledgerRecordId = appRoute.scene === 'Inbox' ? appRoute.params.get('record') : null
  const closeLedgerRecord = () => { setSelected(null); const params = Object.fromEntries(appRoute.params); delete params.record; navigate('Inbox', params) }
  const inspectorQuery = useQuery({
    queryKey: ['run-inspector', ledgerRecordId],
    queryFn: () => api.runInspector(ledgerRecordId!),
    enabled: Boolean(ledgerRecordId),
    retry: false,
    refetchInterval: query => ['admitted', 'running'].includes(query.state.data?.run.status ?? '') ? 5_000 : false,
  })
  const inspector: RunInspector | null = inspectorQuery.data ?? null
  const selectedRecord = ledgerRecordId ? inspector?.run.run_id === ledgerRecordId ? inspector.run : selected?.run_id === ledgerRecordId ? selected : null : null
  useModalScrollLock(Boolean(ledgerRecordId && !selectedRecord))
  const memosQuery = useQuery({ queryKey: ['research-memos'], queryFn: api.researchMemos, retry: false, enabled: peripheralEnabled })
  const evolutionQuery = useQuery({ queryKey: ['evolution-overview'], queryFn: api.evolutionOverview, retry: false, enabled: peripheralEnabled })
  const evolutionJobsQuery = useQuery({ queryKey: ['evolution-jobs'], queryFn: api.evolutionJobs, refetchInterval: 5_000, retry: false, enabled: peripheralEnabled })
  const operationsQuery = useQuery({ queryKey: ['operations'], queryFn: api.operations, refetchInterval: 5_000, retry: false })
  const promotionMutation = useMutation({
    retry: false,
    mutationFn: api.promotionDecision,
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['evolution-overview'] }); void queryClient.invalidateQueries({ queryKey: ['evolution-jobs'] }); void queryClient.invalidateQueries({ queryKey: ['operations'] }) },
  })
  const refreshEvolutionReceipt = (receipt: Awaited<ReturnType<typeof api.promotionDecisionReceipt>>) => {
    if (receipt.decision.decision === 'reject') {
      queryClient.setQueryData<EvolutionOverview>(['evolution-overview'], current => current ? {
        ...current,
        candidates: current.candidates.map(candidate => candidate.candidate_id === receipt.decision.candidate_id ? { ...candidate, status: 'rejected' } : candidate),
        decisions: current.decisions.some(item => item.request_id === receipt.decision.request_id) ? current.decisions : [receipt.decision, ...current.decisions],
      } : current)
    }
    void evolutionQuery.refetch()
    void operationsQuery.refetch()
  }
  const researchHealth = researchServiceHealth(operationsQuery.data?.services)
  const onlineServices = researchHealth.online
  const coreServiceState = operationsQuery.isLoading
    ? 'loading'
    : operationsQuery.isError || !operationsQuery.data
      ? 'unknown'
      : researchHealth.complete
        ? 'online'
        : 'degraded'

  return <div className="app-shell">
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="brand"><div className="brand-mark"><img src="/ui/logo.svg" alt="" /></div>{!collapsed && <div><div className="brand-name">Decision Hub</div><div className="brand-sub">AI FOR BETTER DECISIONS</div></div>}</div>
      <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'}>{collapsed ? <Menu size={18} /> : <PanelLeftClose size={18} />}</button>
      <div className="nav-label">日常工作</div>
      <nav>{nav.map(({ label, icon: Icon, scene }) => <button className={`nav-item ${workspaceView === label ? 'active' : ''}`} key={label} aria-label={label} title={collapsed ? label : undefined} onClick={() => navigate(scene)}><Icon size={17} />{!collapsed && <span>{label}</span>}<span className="mobile-nav-label" aria-hidden="true">{label}</span></button>)}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="sidebar-scope"><div><Target size={14} /><strong>加密资产 / 宏观</strong></div><p>开通范围见“市场与事件”</p></div><button className="side-utility" onClick={() => navigate('sources')}><Network size={14} />系统状态</button><div className="side-health"><span className={`live-dot ${coreServiceState !== 'online' ? 'warning' : ''}`} /> <span>API 与研究进程</span><span className="side-health-time">{coreServiceState === 'unknown' || coreServiceState === 'loading' ? '?' : onlineServices}/{researchHealth.expected}</span></div></div>}
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="breadcrumbs"><span>Decision Hub</span><ChevronRight size={14} /><strong>{workspaceView === 'Evolution' ? '演进与审核' : workspaceView === 'Inbox' ? '业务账本' : workspaceView === 'Operations' ? '运行审计' : workspaceView}</strong></div><div className="top-actions"><button className={`status-pill top-status-button ${coreServiceState === 'online' ? 'success' : coreServiceState === 'degraded' ? 'warning' : 'neutral'}`} onClick={() => navigate('management')}><span className="status-dot" />{coreServiceState === 'loading' ? '状态读取中' : coreServiceState === 'online' ? 'API 与研究进程在线' : coreServiceState === 'degraded' ? '运行链路需检查' : '服务状态未知'}</button><div className="owner-avatar">DH</div></div></header>
      {operationsQuery.isError && <div className="content-wrap"><div className="product-notice danger" role="alert"><div><strong>暂时无法获取服务状态</strong><p>可能是请求超时或连接中断，当前页面可能保留上次读取的内容。系统会自动重试，也可以点击重新连接；若持续失败，请在系统状态中检查服务。</p><button className="button secondary" onClick={() => void queryClient.invalidateQueries()}>重新连接</button></div></div></div>}
      <RouteContent scene={appRoute.scene}>
      {appRoute.scene === 'delivery' ? <DeliveryPage params={appRoute.params} onNavigate={navigate} /> : appRoute.scene === 'studies' ? <StudiesPage params={appRoute.params} onNavigate={navigate} /> : appRoute.scene === 'facts' ? <FactsPage params={appRoute.params} onNavigate={navigate} /> : appRoute.scene === 'history-status' ? <HistoryStatusPage onNavigate={navigate} /> : appRoute.scene === 'data-jobs' ? <DataJobsPage params={appRoute.params} onNavigate={navigate} /> : appRoute.scene === 'sources' ? <SourceAuditPage params={appRoute.params} onNavigate={navigate} /> : appRoute.scene === 'results' ? <ResultsPage params={appRoute.params} onNavigate={navigate} /> : productPageScenes.has(appRoute.scene as ProductScene) ? <ProductPages scene={appRoute.scene as ProductScene} params={appRoute.params} onNavigate={navigate} /> : researchPageScenes.has(appRoute.scene) ? <ResearchPage operations={operationsQuery.data} operationsLoading={operationsQuery.isLoading} operationsFailed={operationsQuery.isError} /> : (appRoute.scene !== 'Inbox' && appRoute.scene !== 'Operations' && appRoute.scene !== 'Evolution') ? <DesignStatePage scene={appRoute.scene as DesignScene} params={appRoute.params} onNavigate={navigate} /> : <>
      {workspaceView === 'Operations' ? <OperationsPage data={operationsQuery.data} loading={operationsQuery.isLoading} failed={operationsQuery.isError} onNavigate={navigate} onRefresh={() => void operationsQuery.refetch()} back={appRoute.params.get('back') ?? undefined} /> : workspaceView === 'Evolution' ? <div className="content-wrap evolution-page"><div className="heading-actions"><button className="product-link" onClick={() => navigate('management')}>返回管理与审计</button><button className="button secondary" onClick={() => { void evolutionQuery.refetch(); void evolutionJobsQuery.refetch(); void operationsQuery.refetch() }}>刷新演进记录</button></div><PageTitle title="演进与审核" eyebrow="研究方法改进" copy="从反馈与评估中发现问题，先验证候选，再由负责人决定是否启用。" trailing={operationsQuery.data ? `${operationsQuery.data.jobs.pending_owner_review} 项待审查` : undefined} /><EvolutionExecutionStatus data={operationsQuery.data} loading={operationsQuery.isLoading} failed={operationsQuery.isError} /><EvolutionReadinessPanel onObservations={() => navigate('results', { back: window.location.hash })} onRun={(runId) => navigate('report', { run: runId, back: window.location.hash })} /><div className="evolution-layout"><EvolutionJobQueue jobs={evolutionJobsQuery.data} loading={evolutionJobsQuery.isLoading} failed={evolutionJobsQuery.isError} /><section className="panel evolution-assets-panel"><div className="panel-header"><div><h2>候选、评测与晋级</h2><p>启用版本必须经过负责人审核和后台校验</p></div></div>{evolutionQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取演进资产…</div>}{evolutionQuery.isError && <div className="workspace-state danger"><AlertTriangle size={17} />演进资产不可用，未使用演示数据。</div>}{!evolutionQuery.isLoading && !evolutionQuery.isError && <div className="evolution-panel-body"><EvolutionPanel evolution={evolutionQuery.data ?? null} deciding={promotionMutation.isPending} onReceiptConfirmed={refreshEvolutionReceipt} onReview={(candidateId, evaluationRefs) => api.promotionReview(DOMAIN_PACK_REF, { candidate_id: candidateId, evaluation_refs: evaluationRefs })} onDecision={(payload) => promotionMutation.mutateAsync({ ...payload, domain_pack_ref: DOMAIN_PACK_REF })} /></div>}</section></div></div> : <LegacyLedgerPage params={appRoute.params} onInspect={setSelected} onSubmit={() => { submitOrigin.current = window.location.hash; setSubmitOpen(true) }} onNavigate={navigate} />}
      </>}
      </RouteContent>
    </main>
    {ledgerRecordId && !selectedRecord && <div className="drawer-backdrop"><aside className="run-drawer" role="dialog" aria-modal="true" aria-label="执行记录详情"><button className="button secondary" onClick={closeLedgerRecord}>返回账本列表</button>{inspectorQuery.isError ? <div role="alert"><p>记录详情读取失败，任务编号可能不存在。</p><button className="button secondary" onClick={() => void inspectorQuery.refetch()}>重试读取详情</button></div> : <p role="status">正在读取所选记录…</p>}</aside></div>}
    {selectedRecord && <RunDrawer selected={selectedRecord} inspector={inspector} loading={inspectorQuery.isLoading} failed={inspectorQuery.isError} onClose={closeLedgerRecord} onRefresh={() => void inspectorQuery.refetch()} onReport={(scene = 'report') => { navigate(scene, { run: selectedRecord.run_id, back: window.location.hash }); setSelected(null) }} />}
    {workspaceView === 'Assets' && <WorkbenchDrawer view={workspaceView} memos={memosQuery.data ?? []} evolution={evolutionQuery.data ?? null} loading={memosQuery.isLoading || evolutionQuery.isLoading} failed={memosQuery.isError || evolutionQuery.isError} deciding={promotionMutation.isPending} onReview={(candidateId, evaluationRefs) => api.promotionReview(DOMAIN_PACK_REF, { candidate_id: candidateId, evaluation_refs: evaluationRefs })} onDecision={(payload) => promotionMutation.mutateAsync({ ...payload, domain_pack_ref: DOMAIN_PACK_REF })} onClose={() => setWorkspaceView('Inbox')} />}
    {submitOpen && <ObservationSubmitDialog originHash={submitOrigin.current ?? undefined} onClose={() => setSubmitOpen(false)} onReceipt={(receipt, originHash) => {
      if (window.location.hash !== submitOrigin.current) return
      setSubmitOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['summary'] })
      void queryClient.invalidateQueries({ queryKey: ['ledger-runs'] })
      const originalParams = new URLSearchParams(originHash.split('?')[1])
      navigate('Inbox', { ...Object.fromEntries(originalParams), record: receipt.run_id })
    }} />}
  </div>
}

function WorkbenchDrawer({ view, memos, evolution, loading, failed, deciding, onReview, onDecision, onClose }: { view: string; memos: ResearchMemo[]; evolution: EvolutionOverview | null; loading: boolean; failed: boolean; deciding: boolean; onReview: (candidateId: string, evaluationRefs: string[]) => Promise<PromotionReview>; onDecision: (payload: PromotionDecisionCommand) => Promise<unknown>; onClose: () => void }) {
  if (view === 'Research') return null
  const title = view === 'Evolution' ? 'Evolution & Promotion' : view === 'Assets' ? 'Personal assets' : 'Research workbench'
  return <div className="drawer-backdrop" onClick={onClose}><aside className="run-drawer workbench-drawer" aria-label={title} onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><div><span className="eyebrow">DECISION WORKBENCH</span><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={17} /></button></div>
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

export function EvolutionPanel({ evolution, deciding, onReview, onDecision, onReceiptConfirmed }: { evolution: EvolutionOverview | null; deciding: boolean; onReview: (candidateId: string, evaluationRefs: string[]) => Promise<PromotionReview>; onDecision: (payload: PromotionDecisionCommand) => Promise<unknown>; onReceiptConfirmed?: (receipt: Awaited<ReturnType<typeof api.promotionDecisionReceipt>>) => void }) {
  const [draft, setDraft] = useState<{ action: PromotionAction; candidateId: string; candidateVersion: string; evaluationRefs: string[]; generation: number } | null>(null)
  useModalScrollLock(Boolean(draft))
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [review, setReview] = useState<PromotionReview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null)
  const [saved, setSaved] = useState<{ items: PendingEvolutionDecision[]; error: string | null }>(() => {
    try { return { items: evolutionPendingDecisions(DOMAIN_PACK_REF, OWNER_ID), error: null } }
    catch (error) { return { items: [], error: error instanceof Error ? error.message : '原决定凭据暂不可读，已暂停新决定。' } }
  })
  const [working, setWorking] = useState(false)
  const [refusalChecked, setRefusalChecked] = useState(false)
  const [confirmedRejections, setConfirmedRejections] = useState<Set<string>>(() => new Set())
  const inFlight = useRef(false)
  const pending = saved.items[0]
  const busy = deciding || working
  const locked = busy || Boolean(pending) || Boolean(saved.error)
  const pointer = evolution?.pointers.find((item) => item.domain_pack_ref === DOMAIN_PACK_REF)
  const historicalTargets = new Set(evolution?.decisions.filter(item => item.decision === 'promote' || item.decision === 'rollback').flatMap((item) => [item.candidate_id, item.previous_candidate_id].filter((value): value is string => Boolean(value))) ?? [])

  const evidenceRefs = (candidateId: string) => {
    const experimentIds = new Set(evolution?.experiments.filter((item) => item.candidate_refs.includes(candidateId)).map((item) => item.experiment_id) ?? [])
    return evolution?.results.filter((item) => item.candidate_id === candidateId && experimentIds.has(item.experiment_id)).map((item) => item.result_id) ?? []
  }
  const openDecision = async (action: PromotionAction, candidate: EvolutionOverview['candidates'][number]) => {
    if (locked || inFlight.current) return
    const refs = action === 'promote' ? evidenceRefs(candidate.candidate_id) : []
    setDraft({ action, candidateId: candidate.candidate_id, candidateVersion: candidate.version, evaluationRefs: refs, generation: pointer?.generation ?? 0 })
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
  const openOriginal = () => {
    if (!pending || busy) return
    const command = pending.payload.command
    setDraft({ action: command.decision, candidateId: command.candidate_id, candidateVersion: pending.payload.candidate_version, evaluationRefs: command.evaluation_refs, generation: command.expected_generation })
    setReason(command.reason)
    setConfirmed(true)
    setReview(null)
  }
  const confirmedReceipt = (item: PendingEvolutionDecision, value: unknown) => {
    const receipt = confirmEvolutionReceipt(item, value)
    if (item.payload.command.decision === 'reject') setConfirmedRejections(previous => new Set([...previous, item.payload.command.candidate_id]))
    const remaining = evolutionPendingDecisions(DOMAIN_PACK_REF, OWNER_ID)
    setSaved({ items: remaining, error: null })
    setDecisionError(null)
    setRefusalChecked(false)
    setDecisionNotice(`${actionLabel(item.payload.command.decision)}决定已确认并写入审核记录。${remaining.some(row => row.request.id === item.request.id) ? '浏览器凭据尚未清理，请再次核对回执。' : ''}`)
    setDraft(null)
    onReceiptConfirmed?.(receipt)
  }
  const failureText = (error: unknown) => {
    const detail = error instanceof Error ? error.message : ''
    const reasons: Record<string, string> = { candidate_not_rejectable: '候选状态已变化', active_pointer_generation_conflict: '账本版本已变化', promotion_request_reused: '请求编号对应的原决定不一致', promotion_gate_failed: '候选尚未通过启用校验', owner_identity_mismatch: '负责人身份不匹配' }
    return reasons[detail] ?? (detail.includes('_') ? '后台未返回可确认的决定回执' : detail || '回执暂不可用')
  }
  const checkReceipt = async () => {
    if (!pending || busy || inFlight.current) return
    inFlight.current = true
    setWorking(true)
    setDecisionError(null)
    try { confirmedReceipt(pending, await api.promotionDecisionReceipt(pending.payload.domain_pack_ref, pending.request.id)) }
    catch (error) {
      if (error instanceof Error && error.message === 'promotion_decision_not_found') {
        try {
          const refusal = evolutionRefusal(pending)
          setRefusalChecked(Boolean(refusal))
          setDecisionError(refusal ? `本次原请求收到后台拒绝（${failureText(new Error(refusal))}），精准查询未找到成功回执。可明确关闭这次原请求后重新填写决定。` : '尚未找到原决定回执。可以使用原请求重试，候选、理由和版本序号保持锁定。')
        } catch (readError) { setDecisionError(failureText(readError)) }
      } else { setRefusalChecked(false); setDecisionError(`决定结果尚未确认：${failureText(error)}。原请求已保留。`) }
    } finally { inFlight.current = false; setWorking(false) }
  }
  const closeRefusedDecision = async () => {
    if (!pending || !refusalChecked || busy || inFlight.current) return
    inFlight.current = true
    setWorking(true)
    try {
      try { confirmedReceipt(pending, await api.promotionDecisionReceipt(pending.payload.domain_pack_ref, pending.request.id)) }
      catch (error) {
        if (!(error instanceof Error) || error.message !== 'promotion_decision_not_found') throw error
        const originalRefusal = evolutionRefusal(pending)
        dismissEvolutionRefusal(pending)
        const remaining = evolutionPendingDecisions(DOMAIN_PACK_REF, OWNER_ID)
        const retained = remaining.some(item => item.request.id === pending.request.id)
        setSaved({ items: remaining, error: null })
        setRefusalChecked(false)
        setDraft(null)
        setDecisionError(null)
        setDecisionNotice(retained ? '后台拒绝已核对，但浏览器凭据尚未清理。原请求仍保留，请再次核对。' : `已核对本次后台拒绝（${failureText(new Error(originalRefusal ?? ''))}）并关闭原请求。可以重新填写决定。`)
      }
    } catch (error) { setDecisionError(`原请求仍保留：${failureText(error)}。`) }
    finally { inFlight.current = false; setWorking(false) }
  }
  const submitDecision = async () => {
    if (!draft || !reason.trim() || !confirmed || busy || inFlight.current || saved.error) return
    inFlight.current = true
    setWorking(true)
    setRefusalChecked(false)
    setDecisionError(null)
    let attempted: PendingEvolutionDecision | undefined
    try {
      const item = pending ?? freezeEvolutionDecision({ domain_pack_ref: DOMAIN_PACK_REF, candidate_version: draft.candidateVersion, command: { candidate_id: draft.candidateId, owner: OWNER_ID, decision: draft.action, reason: reason.trim(), evaluation_refs: draft.evaluationRefs, expected_generation: draft.generation } })
      attempted = item
      setSaved(previous => ({ ...previous, items: previous.items.length ? previous.items : [item] }))
      if (pending) {
        // A user-requested retry first reads the receipt; mounting never POSTs.
        try { confirmedReceipt(item, await api.promotionDecisionReceipt(item.payload.domain_pack_ref, item.request.id)); return }
        catch (error) { if (!(error instanceof Error) || error.message !== 'promotion_decision_not_found') throw error }
      }
      confirmedReceipt(item, await onDecision(originalEvolutionCommand(item)))
    } catch (error) {
      if (attempted) {
        try { rememberEvolutionRefusal(attempted, error) }
        catch { setDecisionError('后台返回了拒绝信息，但本地拒绝凭据未能保存。请保留原请求并核对回执。'); return }
      }
      setDecisionError(`决定结果尚未确认：${failureText(error)}。请先核对回执；已发送的原请求会保留。`)
    } finally { inFlight.current = false; setWorking(false) }
  }

  return <>
    {saved.error && <div className="form-error decision-error" role="alert">{saved.error}</div>}
    {pending && <section className="drawer-section decision-error" aria-label="待确认的演进决定"><h3>上次决定的回执待核对</h3><p>{actionLabel(pending.payload.command.decision)} · {pending.payload.candidate_version}</p><p>理由：{pending.payload.command.reason}</p><p>原候选、决定内容和版本序号已保留。返回本页不会自动提交。</p><details><summary>原提交凭据</summary><p>{pending.request.id} · {pending.payload.command.candidate_id} · 原版本序号 {pending.payload.command.expected_generation}</p></details><div className="heading-actions"><button className="button secondary" disabled={busy} onClick={() => void checkReceipt()}>{working ? '核对中…' : '核对回执'}</button><button className="button secondary" disabled={busy} onClick={openOriginal}>查看原决定</button>{refusalChecked && <button className="button secondary" disabled={busy} onClick={() => void closeRefusedDecision()}>后台拒绝已核对，关闭原请求</button>}</div></section>}
    {!evolution && <EmptyLine text="演进记录暂不可用；已保存的决定仍可核对回执" />}
    {evolution && <>
    <section className="drawer-section"><h3>方向综合账本当前版本</h3>{!pointer && <EmptyLine text="尚无通过负责人审核的演进候选；不代表现有研究服务停止" />}{pointer && <div className="promotion-row"><ShieldCheck size={16} /><div><strong>账本已选方法</strong><small>版本序号 {pointer.generation}</small><details><summary>方法技术标识</summary><small>{pointer.domain_pack_ref} · {pointer.candidate_id}</small></details></div></div>}</section>
    <section className="drawer-section"><h3>评估与基线对照</h3>{evolution.experiments.length === 0 && <EmptyLine text="尚无已登记的历史回放、独立样本或前瞻观察结果" />}<EvolutionExperimentCards evolution={evolution} /></section>
    <section className="drawer-section"><h3>待评估候选</h3>{evolution.candidates.length === 0 && <EmptyLine text="暂无候选版本" />}{evolution.candidates.map((candidate) => { const active = pointer?.candidate_id === candidate.candidate_id; const refs = evidenceRefs(candidate.candidate_id); const scopeReady = candidate.execution_scope === 'directional_synthesis' && !['runtime', 'provider_policy'].includes(candidate.candidate_type); const canRollback = Boolean(pointer) && !active && historicalTargets.has(candidate.candidate_id); return <div className="asset-ledger-row candidate-row" key={candidate.candidate_id}><Layers3 size={15} /><div><strong>{active ? "账本已选方法" : "候选方法"}</strong><small>{executionScopeName(candidate.execution_scope)} · 登记于 {formatDate(candidate.created_at)}</small>{executionScopeLimit(candidate.execution_scope) && <p>{executionScopeLimit(candidate.execution_scope)}</p>}<CandidateExecutionPanel candidateId={candidate.candidate_id} active={active} /><details><summary>方法版本与技术标识</summary><small>{candidate.version} · {candidate.candidate_type} · {candidate.status} · {candidate.content_hash}</small></details></div><div className="candidate-actions">{active ? <span className="candidate-state"><ShieldCheck size={14} />账本已选用</span> : <>{candidate.status !== 'rejected' && !confirmedRejections.has(candidate.candidate_id) && <button className="icon-action promote" disabled={locked || !scopeReady || refs.length === 0} onClick={() => void openDecision('promote', candidate)} title={refs.length ? '审核并启用' : '需先完成候选评估'} aria-label={`审核并启用 ${candidate.version}`}><CheckCircle2 size={15} /></button>}{candidate.status !== 'rejected' && !confirmedRejections.has(candidate.candidate_id) && <button className="icon-action reject" disabled={locked} onClick={() => void openDecision('reject', candidate)} title="拒绝候选" aria-label={`拒绝 ${candidate.version}`}><Ban size={15} /></button>}{canRollback && <button className="icon-action rollback" disabled={locked || !scopeReady} onClick={() => void openDecision('rollback', candidate)} title="回滚至此版本" aria-label={`回滚至 ${candidate.version}`}><Undo2 size={15} /></button>}</>}</div></div>})}</section>
    <section className="drawer-section"><h3>版本审核历史</h3>{evolution.decisions.length === 0 && <EmptyLine text="暂无启用、拒绝或回滚记录" />}{evolution.decisions.map((item) => <div className="audit-row" key={item.decision_id}><History size={15} /><div><strong>{actionLabel(item.decision)}候选方法</strong><small>版本序号 {item.resulting_generation} · {formatDate(item.created_at)}</small><p>{item.reason}</p><details><summary>原审核凭据</summary><small>候选 {item.candidate_id} · 负责人 {item.owner} · 请求 {item.request_id}</small></details></div></div>)}</section>
    </>}
    {decisionNotice && <div className="decision-notice" role="status"><CheckCircle2 size={15} />{decisionNotice}</div>}
    {decisionError && !draft && <div className="form-error decision-error" role="alert">{decisionError}</div>}
    {draft && <div className="modal-backdrop nested-modal" onClick={() => !busy && setDraft(null)}><section className="promotion-modal" role="dialog" aria-modal="true" aria-labelledby="promotion-title" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">负责人审核 · 原版本序号 {draft.generation}</span><h2 id="promotion-title">{actionLabel(draft.action)}候选方法</h2></div><button className="icon-button" disabled={busy} onClick={() => setDraft(null)} aria-label="关闭"><X size={17} /></button></div><p className="modal-copy">{actionWarning(draft.action)}</p>{draft.action === 'promote' && !pending && <PromotionReviewPanel review={review} reviewing={reviewing} />}
      <label className="reason-field"><span>决策理由{pending ? '（原提交内容已锁定）' : ''}</span><textarea value={reason} readOnly={Boolean(pending)} disabled={busy} onChange={(event) => { if (!pending) setReason(event.target.value) }} placeholder="记录本次决定依据、风险和预期回滚条件…" autoFocus /></label>
      <label className="confirm-row"><input type="checkbox" checked={confirmed} disabled={Boolean(pending) || busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>我确认本次版本决定，后台仍会独立检查启用条件。</span></label>
      {decisionError && <div className="form-error decision-error" role="alert">{decisionError}</div>}
      <div className="modal-footer promotion-footer"><button className="button secondary" disabled={busy} onClick={() => setDraft(null)}>{pending ? '关闭并保留原决定' : '取消'}</button>{pending && <button className="button secondary" disabled={busy} onClick={() => void checkReceipt()}>核对回执</button>}<button className={`button decision-submit ${draft.action}`} disabled={busy || reviewing || !reason.trim() || !confirmed || Boolean(saved.error) || (!pending && draft.action === 'promote' && !review?.eligible)} onClick={() => void submitDecision()}>{busy ? <RotateCcw size={14} className="spin" /> : draft.action === 'rollback' ? <Undo2 size={14} /> : draft.action === 'reject' ? <Ban size={14} /> : <CheckCircle2 size={14} />}{busy ? '核对与提交中' : pending ? '重试原决定' : `确认${actionLabel(draft.action)}`}</button></div>
    </section></div>}
  </>
}

function actionLabel(action: PromotionAction) { return action === 'promote' ? '启用' : action === 'rollback' ? '回滚' : '拒绝' }
function actionWarning(action: PromotionAction) { return action === 'promote' ? '后台会重新核对全部条件，通过后更新演进账本版本。正式研究执行接入尚未完成。' : action === 'rollback' ? '回滚只更新方向综合账本并保存审核记录，不改变正式 DSH 研究的执行版本。' : '拒绝会关闭此候选的待审核状态，当前启用版本保持不变。' }

export function RunDrawer({ selected, inspector, loading, failed, onClose, onRefresh, onReport }: { selected: RunView; inspector: RunInspector | null; loading: boolean; failed: boolean; onClose: () => void; onRefresh?: () => void; onReport: (scene?: 'report' | 'technical') => void }) {
  useModalScrollLock()
  const artifact = inspector?.artifact
  const nativeResearch = inspector?.orchestration.mode === 'dsh_native'
  const testOutput = inspector?.versions.provider_ids.includes('fake') || inspector?.versions.models.includes('deterministic-fixture')
  const replayOutput = inspector?.versions.provider_ids.includes('replay') || selected.runtime_version.startsWith('replay')
  const errorCode = inspector?.run.error_code ?? selected.error_code
  const execution = nativeResearch ? 'DSH 研究会话' : testOutput ? '确定性测试执行' : replayOutput ? '历史回放执行' : '执行方式待核对'
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside className="run-drawer" role="dialog" aria-modal="true" aria-label="执行记录详情" onClick={(event) => event.stopPropagation()}>
      <div className="drawer-header"><div><span className="eyebrow">执行记录详情</span><h2>执行记录详情</h2><LegacyRawRecord title="核对原始任务与报告标题" value={{ task_headline: selected.headline, report_headline: artifact?.headline ?? null }} /></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={17} /></button></div>
      <div className="drawer-meta"><StatusPill status={artifact?.gate_status ?? selected.gate_status} runStatus={selected.status} errorCode={selected.error_code} /><span>创建于 {formatDate(selected.created_at)}</span></div>
      {onRefresh && <button className="button secondary" onClick={onRefresh}>刷新执行详情</button>}
      {loading && <div className="inspector-message" role="status" aria-live="polite"><Activity size={16} />正在读取规范化运行数据…</div>}
      {failed && <div className="inspector-message danger" role="alert"><AlertTriangle size={16} />执行详情读取失败，当前详情尚未确认。{onRefresh ? '请点击刷新执行详情重试。' : '请关闭后重试。'}</div>}
      {testOutput && <div className="inspector-message danger" role="note">本记录来自确定性测试数据，预测与评分仅用于验证流程，不能作为真实市场研究结果。</div>}
      {!testOutput && replayOutput && <div className="inspector-message" role="note">本记录来自历史回放，不能作为实时市场研究或前瞻验证结果。</div>}
      {errorCode && <div className="error-callout" role="alert"><AlertTriangle size={15} /><span><strong>{legacyRunName(errorCode, '异常原因')}</strong>具体代码保留在任务原始记录中。</span></div>}
      <LegacyRawRecord title="查看任务与版本原始记录" value={{ run: selected, versions: inspector?.versions ?? null, snapshot_cutoff_at: inspector?.snapshot_cutoff_at ?? null, snapshot_hash: inspector?.snapshot_hash ?? null }} />
      {selected.strategy_version === 'research.v1' && inspector && <div className="drawer-section"><button className="button primary" onClick={() => onReport(artifact ? 'report' : 'technical')}>{artifact ? '打开研究报告' : '查看已留存材料与恢复入口'}</button>{!artifact && <p>本次尚未形成报告。查看过程与材料不会重新执行研究。</p>}</div>}
      {inspector && <>
        <section className="drawer-section"><h3>执行状态与版本</h3>
          <div className="detail-grid">
            <Detail label="执行状态" value={ledgerRunStatus(inspector.run.status).label} />
            <Detail label="分析类别" value={inspector.versions.strategy_version === 'research.v1' ? 'AI 研究' : inspector.versions.strategy_version === 'baseline.v1' ? '规则分析' : '策略类别待解释'} />
            <Detail label="执行方式" value={execution} />
            <Detail label="服务商记录" value={inspector.versions.provider_ids.length ? `已记录 ${inspector.versions.provider_ids.length} 个服务商，见原始记录` : '此账本未记录'} />
            <Detail label="模型记录" value={inspector.versions.models.length ? `已记录 ${inspector.versions.models.length} 个模型，见原始记录` : '此账本未记录'} />
            <Detail label="版本凭据" value="已保留原始字段，不能单凭版本名称确认执行方式" />
            <Detail label="信息截止时间" value={inspector.snapshot_cutoff_at ? formatDate(inspector.snapshot_cutoff_at) : '未记录'} />
            <Detail label="输入校验记录" value={inspector.snapshot_hash ? '已保存，见原始记录' : '未记录'} />
            <Detail label="耗时" value={legacyDuration(inspector.run.latency_ms)} />
            <Detail label="费用" value={legacyCost(inspector.run.cost_usd)} />
          </div>
        </section>
        <section className="drawer-section"><h3>任务编排</h3>
          {nativeResearch ? <p>本次由 DSH 研究会话执行。完整协作、工具调用和用量请打开研究技术详情核对；下方仅保留旧账本已有记录。</p> : <>
            <div className="detail-grid">
              <Detail label="编排模式" value={({ fixed_graph: '固定流程', supervisor_candidate: '协调规划', dsh_native: 'DSH 研究会话', unknown: '未确认' })[inspector.orchestration.mode]} />
              <Detail label="协调角色" value={legacyRunName(inspector.orchestration.supervisor_role, '协调角色')} />
              <Detail label="重新规划记录" value={`${inspector.orchestration.replan_count} / 1 次`} />
              <Detail label="关联实验" value={inspector.orchestration.experiment_refs.length ? `已登记 ${inspector.orchestration.experiment_refs.length} 项引用` : '此账本未记录'} />
            </div>
            <CapabilityCoverage label="所需能力" items={inspector.orchestration.required_capabilities} />
            <CapabilityCoverage label="计划能力" items={inspector.orchestration.planned_capabilities} />
            <CapabilityCoverage label="已覆盖能力" items={inspector.orchestration.specialist_coverage} tone="success" />
            <CapabilityCoverage label="缺失能力" items={inspector.orchestration.missing_capabilities} tone={inspector.orchestration.missing_capabilities.length ? 'danger' : 'neutral'} />
          </>}
          <LegacyRawRecord title="查看编排与能力原始记录" value={inspector.orchestration} />
        </section>
        <section className="drawer-section"><h3>执行步骤</h3>
          {inspector.steps.length === 0 && <EmptyLine text={nativeResearch ? '旧步骤账本未记录此会话，请在研究技术详情中查看实际过程。' : '尚无步骤记录'} />}
          {inspector.steps.map(step => <div className={`step-row ${step.status}`} key={step.step_id}>
            <span className="step-attempt">{step.attempt}</span><div>
              <strong>{legacyRunName(step.step_name, '步骤名称')}</strong>
              <small>{legacyRunName(step.status, '步骤状态')} · {legacyDuration(step.latency_ms)}{step.error_code ? ` · ${legacyRunName(step.error_code, '异常原因')}` : ''}</small>
              <LegacyRawRecord title="查看这一步原始记录" value={step} />
            </div>
          </div>)}
        </section>
        <section className="drawer-section"><h3>活动时间线</h3><div className="timeline-list">
          {inspector.timeline.length === 0 && <EmptyLine text="尚无活动记录" />}
          {inspector.timeline.map(item => <div className="timeline-item" key={`${item.sequence_no}-${item.event_type}`}>
            <span className="timeline-index">{item.sequence_no}</span><div>
              <strong>{legacyRunName(item.event_type, '活动类别')}</strong>
              <small>{formatDate(item.occurred_at)}{item.reference_id ? ` · 关联${legacyRunName(item.reference_type, '引用类别')}` : ''}</small>
              <LegacyRawRecord title="查看这条活动原始记录" value={item} />
            </div>
          </div>)}
        </div></section>
        <section className="drawer-section"><h3>模型调用</h3>
          {inspector.calls.length === 0 && <EmptyLine text={nativeResearch ? '旧调用账本为空，不代表本次未调用模型。研究技术详情可核对主会话和协作会话。' : '此账本尚无模型调用记录'} />}
          {inspector.calls.map(call => <div className={`call-row ${call.status === 'failed' ? 'failed' : ''}`} key={call.call_id}>
            <span className="check-icon">{call.status === 'failed' ? <AlertTriangle size={14} /> : <BrainCircuit size={14} />}</span><div>
              <strong>{legacyRunName(call.role, '调用角色')} <b>第 {call.attempt} 次尝试</b></strong>
              <small>{legacyRunName(call.status, '调用状态')} · {legacyRunName(call.api_mode, '接口类型')} · {call.model ? '模型已记录' : '模型未记录'}</small>
              <small>{legacyDuration(call.latency_ms)} · {call.total_tokens === null ? '词元未记录' : `${legacyNumber(call.total_tokens)} 个词元`} · {legacyCost(call.cost_usd, call.cost_status)}{call.error_code ? ` · ${legacyRunName(call.error_code, '异常原因')}` : ''}</small>
              <LegacyRawRecord title="查看这次调用原始记录" value={call} />
            </div>
          </div>)}
        </section>
        <section className="drawer-section"><h3>证据来源与时间</h3>
          {inspector.evidence_lineage.length === 0 && <EmptyLine text="输入快照尚无规范化证据记录" />}
          {inspector.evidence_lineage.map(evidence => <article className="lineage-item" key={evidence.evidence_id}>
            <div className="lineage-title"><div><strong>{sourceLabel(evidence.source_id, '来源名称待解释')}</strong><small>{legacyRunName(evidence.source_type, '来源类别')}</small></div><span>校验指纹{evidence.content_hash ? '已保存' : '未记录'}</span></div>
            <div className="lineage-times"><TimeStamp label="观察时间" value={evidence.observed_at} /><TimeStamp label="发布时间" value={evidence.published_at} /><TimeStamp label="接收时间" value={evidence.received_at} /><TimeStamp label="信息截止" value={evidence.cutoff_at} /></div>
            <LegacyRawRecord title="查看这条来源与时间原始记录" value={evidence} />
          </article>)}
        </section>
        <section className="drawer-section"><h3>报告内容与依据</h3>{!artifact && <EmptyLine text="尚未生成报告" />}{artifact && <>
          <p className="artifact-summary">{legacyReportSummary(artifact.summary)}</p>
          <EvidenceGroup title="事实" items={artifact.facts.map(legacyContent)} />
          <EvidenceGroup title="推断" items={artifact.inferences.map(legacyContent)} />
          <EvidenceGroup title="反方观点" items={artifact.counter_thesis ? [legacyContent(artifact.counter_thesis)] : []} />
          <EvidenceGroup title="影响路径" items={artifact.transmission_chain.map(legacyContent)} ordered />
          <EvidenceGroup title="局限" items={artifact.uncertainty.map(legacyContent)} />
          <p>已登记 {artifact.citations.length} 项引用依据，原始标识保留如下。</p>
          <LegacyRawRecord title="查看报告与引用原始记录" value={artifact} />
        </>}</section>
        <section className="drawer-section"><h3>发布校验</h3>
          {!artifact?.gate_decisions.length && <EmptyLine text="暂无发布校验记录" />}
          {artifact?.gate_decisions.map(gate => <div className="gate-row" key={gate.rule_id}>
            <span className={`gate-mark ${gate.status}`}>{legacyRunName(gate.status, '检查状态')}</span><div><strong>{legacyRunName(gate.rule_id, '检查项目')}</strong><small>{legacyRunName(gate.reason_code, '检查原因')}</small><LegacyRawRecord title="查看这项检查原始记录" value={gate} /></div>
          </div>)}
        </section>
        <section className="drawer-section"><h3>预测记录</h3>{!artifact?.forecasts.length && <EmptyLine text="没有发布预测" />}<div className="forecast-list">
          {artifact?.forecasts.map(forecast => <div className="forecast-row" key={forecast.forecast_id}>
            <div><span>{({ '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时' } as Record<string, string>)[forecast.horizon] ?? legacyRunName(forecast.horizon, '研究窗口')}</span><strong>{directionLabel(forecast.direction)}</strong></div>
            <b>原记录概率 {Math.round(forecast.probability * 100)}%</b><small>校准状态未记录，不能作为已验证胜率。</small>
            <p><em>触发条件</em>{legacyContent(forecast.trigger)}</p><p><em>失效条件</em>{legacyContent(forecast.invalidation)}</p>
            <LegacyRawRecord title="查看这条预测原始记录" value={forecast} />
          </div>)}
        </div></section>
        <section className="drawer-section"><h3>结果评估</h3>
          {inspector.evaluations.length === 0 && <EmptyLine text={`${artifact?.forecasts.length ?? 0} 条预测暂无关联评估；是否需要评估请核对方向和到期条件`} />}
          {inspector.evaluations.map(evaluation => <div className="evaluation-row" key={evaluation.evaluation_id}><BookOpen size={15} /><div>
            <strong>概率评分 {legacyNumber(evaluation.brier_score)} · 记录收益 {legacyNumber(evaluation.net_return_pct)}%</strong>
            <small>{({ estimated: '行情估算', observed: '已观察', unverified: '未核验' } as Record<string, string>)[evaluation.label_status] ?? '标签含义待解释'} · {formatDate(evaluation.evaluated_at)}</small>
            <LegacyRawRecord title="查看评分原始字段" value={evaluation} />
          </div></div>)}
        </section>
      </>}
    </aside>
  </div>
}

function Detail({ label, value }: { label: string; value: string }) { return <div className="detail-cell"><span>{label}</span><strong title={value}>{value}</strong></div> }
function CapabilityCoverage({ label, items, tone = 'neutral' }: { label: string; items: string[]; tone?: 'neutral' | 'success' | 'danger' }) { return <div className="capability-row"><strong>{label}</strong><div>{items.length ? items.map((item) => <span className={`capability-chip ${tone}`} key={`${label}-${item}`}>{legacyRunName(item, '能力名称')}</span>) : <span className="capability-empty">此账本未记录</span>}</div></div> }
function LegacyRawRecord({ title, value }: { title: string; value: unknown }) { return <details className="legacy-audit"><summary>{title}</summary><pre className="asset-json">{JSON.stringify(value, null, 2)}</pre></details> }
function TimeStamp({ label, value }: { label: string; value: string | null }) { return <div><span>{label}</span><strong>{value ? formatDate(value) : '未记录'}</strong></div> }
function EmptyLine({ text }: { text: string }) { return <div className="empty-line">{text}</div> }
function EvidenceGroup({ title, items, ordered = false, mono = false }: { title: string; items: string[]; ordered?: boolean; mono?: boolean }) { if (!items.length) return null; const Tag = ordered ? 'ol' : 'ul'; return <div className="evidence-group"><strong>{title}</strong><Tag className={mono ? 'mono-list' : undefined}>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</Tag></div> }
function formatDate(value: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatList(values: string[]) { return values.length ? values.join(' · ') : '未知' }


export default App
