import { PerpetualSavedReportBoundary } from '../product/PerpetualSavedReport'
import { ReportNativeFailureCard } from './ReportNativeFailureCard'
import { FollowupFailureDetail52 } from './FollowupFailureDetail52'
import { useModalScrollLock } from '../product/useModalScrollLock'
import { useModalFocusContainment } from '../product/useModalFocusContainment'
import { abandonGraphFocusReturn, requestGraphFocusReturn, useGraphFocusReturn } from '../product/graphFocusReturn'
import { coverageUnavailableText } from './coverageLabels'
import { directionLabel, evidenceSummary, savedDailyRequirementLabel, missingEvidencePublicationText, requirementNames, technicalName, technicalSummaryName, technicalLabels, warningName, currentPreparationWaiting, preparationWaitingText, researchTraceTitle, researchRecordTime, researchTraceTimeNotice, failureExplanation, observationUnavailableExplanation, isAcquisitionClosingReserve, acquisitionClosingSummary, acquisitionClosingRecordSummary } from './technicalPresentation'
import { sourceLabel } from '../product/operationLabels'
import { ResearchFocus, researchTitle } from '../product/ResearchContext'
import { EvidenceCapturePanel } from '../product/EvidenceCapturePanel'
import { restoredSubmissionText } from './submissionReceipts'
import { useSubmission } from './useSubmission'
import { SubmissionError, SubmissionRecovery } from './SubmissionRecovery'
import { graphNodeKinds, graphNodeStatuses, graphNodeSummary, graphNodeTitle, graphNodeMatches, graphReviewPresentations, graphReviewPresentation } from './graphPresentation'
import type { GraphReviewPresentations } from './graphPresentation'
import { ResearchCostPanel } from '../product/ResearchCostPanel'
import { ResearchQuality, useResearchWindowClock } from '../product/ResearchQuality'
import { researchWindowExpiry } from '../product/researchReadingMarkdown'
import { moneyAmount } from '../product/numericPresentation'
import { ToolAttemptsPanel } from '../product/ToolAttemptsPanel'
import { calendarStatus, calendarTime } from '../product/calendarTime'
import { AssetBackLink, assetBackLabel, directionName, horizonName } from '../product/assetFormat'
import { researchDisplayText } from '../product/researchLanguage'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  Ban,
  CalendarDays,
  BookOpenCheck,
  CheckCircle2,
  CircleGauge,
  Clock3,
  Code2,
  ExternalLink,
  FileSearch,
  Filter,
  GitBranch,
  MessageSquareText,
  Maximize2,
  Network,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  SearchCheck,
  Send,
  ShieldAlert,
  TimerReset,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType, ReactNode, RefObject } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { api, apiUrl } from '../api/client'
import { delivery, productOwner } from '../api/delivery'
import { RevisionFeedback } from '../product/RevisionFeedback'
import type {
  OperationsOverview,
  MacroEventView,
  ForecastAuditView,
  StrategyCalibrationView,
  ReviewScheduleView,
  ResearchRunCommand,
  ResearchRunDetailView,
  ResearchRunScope,
  ResearchRunComparisonView,
  ResearchRunView,
  ResearchInboxItem,
  ResearchObservabilityView,
  ResearchValueEvaluation,
} from '../api/types'
import { PageTitle } from '../OperationsPage'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'
import { useRunCommand } from './useRunCommand'
import { RunCommandRecovery } from './RunCommandRecovery'
import { reviewStatusText, workflowCheckpointCallIds } from './workflowCheckpoints'

type EvidenceReferenceScope = { runId: string; ordinals: ReadonlyMap<string, number> }
const EvidenceReferenceContext = createContext<EvidenceReferenceScope | null>(null)
function savedEvidenceReferenceScope(runId: string, evidence: ResearchRunDetailView['evidence']): EvidenceReferenceScope {
  const counts = new Map<string, number>()
  evidence.forEach(item => counts.set(item.evidence_id, (counts.get(item.evidence_id) ?? 0) + 1))
  const ordinals = new Map<string, number>()
  // Use the full saved record order, including unaccepted inputs. Numbering is not admission.
  evidence.forEach((item, index) => { if (item.evidence_id && counts.get(item.evidence_id) === 1) ordinals.set(item.evidence_id, index + 1) })
  return { runId, ordinals }
}
function evidenceReferenceLabel(scope: EvidenceReferenceScope | null, runId: string, ref: string): string {
  const ordinal = scope?.runId === runId ? scope.ordinals.get(ref) : undefined
  return ordinal === undefined ? '相关证据' : `证据 ${ordinal}`
}

function reviewDisplayStatus(steps: ResearchRunDetailView['process_steps'], reviewCount: number, runStatus: ResearchRunDetailView['run']['status']): string {
  return reviewStatusText(steps, reviewCount, runStatus)
}

function executionReviewRecordName(steps: ResearchRunDetailView['process_steps']): string {
  const reviewSteps = steps.filter(step => step.kind === 'review' || step.step_id === 'review')
  return reviewSteps.length === 1 && reviewSteps[0].kind === 'review'
    && reviewSteps[0].label === '自动证据检查' && reviewSteps[0].status === 'limited'
    ? '自动证据检查记录' : '执行检查/审查记录'
}

const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const TERMINAL = new Set<ResearchRunView['status']>([
  'completed', 'degraded', 'research_only', 'rejected', 'failed', 'cancelled',
])
const TRACE_EVENTS = [
  'session_started', 'plan_created', 'task_started', 'model_step_started',
  'model_step_completed', 'tool_started', 'tool_completed', 'tool_failed',
  'subagent_started', 'subagent_completed', 'evidence_accepted', 'evidence_rejected',
  'coverage_assessed', 'replan', 'round_completed', 'synthesis_started', 'session_stopped',
]

const statusLabel: Record<ResearchRunView['status'], string> = {
  admitted: '已接收', queued: '排队中', researching: '研究中', retry_wait: '等待重试',
  completed: '已完成', degraded: '有界降级', research_only: '仅研究', rejected: '已拒绝',
  failed: '失败', cancelled: '已取消',
}

const originLabel: Record<ResearchRunView['admission_origin'], string> = {
  manual: '用户提交',
  automatic: '自动发现',
  scheduled_recheck: '定时复查',
  legacy: '历史兼容',
}

type ResearchRouteName = 'home' | 'report' | 'report-incomplete' | 'technical' | 'evidence' | 'graph' | 'graph-node' | 'event' | 'document-review'
type GraphView = { graphQuery?: string; graphFilter?: string; graphView?: 'map' | 'list' }
type ResearchRoute = GraphView & {
  name: ResearchRouteName
  runId?: string
  evidenceId?: string
  eventId?: string
  versionId?: string
  nodeId?: string
  back?: string
  focus?: 'sources'
}

function readResearchRoute(hash = window.location.hash): ResearchRoute {
  const raw = hash.replace(/^#\/?/, '')
  const [path, query = ''] = raw.split('?')
  const params = new URLSearchParams(query)
  const name = (path || 'home') as ResearchRouteName
  return {
    name: ['report', 'report-incomplete', 'technical', 'evidence', 'graph', 'graph-node', 'event', 'document-review'].includes(name) ? name : 'home',
    runId: params.get('run') ?? params.get('run_id') ?? undefined,
    evidenceId: params.get('evidence') ?? params.get('evidence_id') ?? undefined,
    eventId: params.get('event') ?? params.get('event_id') ?? undefined,
    versionId: params.get('version') ?? params.get('version_id') ?? undefined,
    nodeId: params.get('node') ?? params.get('node_id') ?? undefined,
    back: params.get('back') ?? undefined,
    focus: params.get('focus') === 'sources' ? 'sources' : undefined,
    graphQuery: params.get('q') ?? undefined,
    graphFilter: params.get('kind') ?? undefined,
    graphView: params.get('view') === 'list' ? 'list' : params.get('view') === 'map' ? 'map' : undefined,
  }
}

export function researchRouteHref(route: ResearchRoute): string {
  const params = new URLSearchParams()
  if (route.runId) params.set('run', route.runId)
  if (route.evidenceId) params.set('evidence', route.evidenceId)
  if (route.nodeId) params.set('node', route.nodeId)
  if (route.eventId) params.set('event', route.eventId)
  if (route.versionId) params.set('version', route.versionId)
  if (route.back) params.set('back', route.back)
  if (route.focus) params.set('focus', route.focus)
  if (route.graphQuery) params.set('q', route.graphQuery)
  if (route.graphFilter && route.graphFilter !== 'all') params.set('kind', route.graphFilter)
  if (route.graphView) params.set('view', route.graphView)
  const query = params.toString()
  return `#/${route.name}${query ? `?${query}` : ''}`
}

export function researchNavigationRoute(next: ResearchRoute, currentHash: string): ResearchRoute {
  if (next.name === 'graph-node') {
    const graph = graphBaseRoute(currentHash, next.runId)
    const view = { graphQuery: next.graphQuery ?? graph.graphQuery, graphFilter: next.graphFilter ?? graph.graphFilter, graphView: next.graphView ?? graph.graphView }
    return { ...next, ...view, back: researchRouteHref({ ...graph, ...view }) }
  }
  const needsReturnContext = ['evidence', 'graph', 'event', 'document-review'].includes(next.name)
  return needsReturnContext && !next.back ? { ...next, back: currentHash } : next
}

// Keep one graph parent while moving between nodes; never build a node-to-node chain.
export function graphBaseRoute(hash: string, runId?: string): ResearchRoute {
  const current = readResearchRoute(hash)
  if (current.runId === runId && current.name === 'graph') return current
  if (current.runId === runId && current.name === 'graph-node' && current.back) {
    const parent = readResearchRoute(current.back)
    if (parent.name === 'graph' && parent.runId === runId) return parent
  }
  const report = current.runId === runId && ['report', 'report-incomplete', 'technical'].includes(current.name)
    ? hash : researchRouteHref({ name: 'report', runId })
  return { name: 'graph', runId, back: report }
}

// The fullscreen graph belongs to one Run; restore its own reading context.
function graphReturnRoute(hash: string, runId: string | undefined, view: GraphView): ResearchRoute {
  const graph = graphBaseRoute(hash, runId)
  const parent = graph.back ? readResearchRoute(graph.back) : undefined
  return parent && parent.runId === runId && ['report', 'report-incomplete', 'technical'].includes(parent.name)
    ? { ...parent, ...view }
    : { name: 'report', runId, ...view }
}

// An evidence drawer keeps the same Run's report and graph mounted beneath it.
function reportReadingRoute(route: ResearchRoute, runId: string): ResearchRoute {
  if (route.name !== 'evidence' || route.runId !== runId || !route.back) return route
  const origin = readResearchRoute(route.back)
  return origin.runId === runId && ['report', 'report-incomplete', 'technical', 'graph', 'graph-node'].includes(origin.name)
    ? origin : route
}

function graphViewFromRoute(route: ResearchRoute, runId: string): GraphView {
  route = reportReadingRoute(route, runId)
  return route.runId === runId && ['report', 'report-incomplete', 'technical', 'graph', 'graph-node'].includes(route.name)
    ? { graphQuery: route.graphQuery, graphFilter: route.graphFilter, graphView: route.graphView } : {}
}

function isSafeResearchBack(value: string | undefined): value is string {
  if (!value || !value.startsWith('#/')) return false
  const path = value.slice(2).split('?')[0]
  return Boolean(assetBackLabel(value)) || ['report-incomplete', 'technical', 'evidence', 'graph', 'graph-node', 'event', 'document-review'].includes(path)
}

const sourceAuthorityName = (value: string) => ({ official: '官方来源', exchange: '交易所', audited_aggregator: '聚合数据来源', verified_web: '已核验网页', search_derived: '搜索线索', unverified: '未核验来源' }[value] ?? '来源类别未知')
export function evidenceTimeLabel(item: Pick<ResearchRunDetailView['evidence'][number], 'kind' | 'published_at' | 'freshness_status'>) {
  if (item.kind !== 'market' && !item.published_at) return '发布时间未核验'
  return item.freshness_status === 'fresh' ? '符合当时的时效要求' : item.freshness_status === 'stale' ? '超出当时的时效要求' : '时效未知'
}

export function evidenceRouteHref(runId: string, evidenceId: string): string {
  const current = readResearchRoute()
  // A second citation in the open drawer retains the same parent, without
  // building an evidence-to-evidence chain or discarding the report's return.
  const parent = current.name === 'evidence' ? current.back : window.location.hash
  const back = current.runId === runId && isSafeResearchBack(parent) ? parent : undefined
  return researchRouteHref({ name: 'evidence', runId, evidenceId, back })
}

function goToResearchBack(value: string | undefined, fallback: ResearchRoute): void {
  const target = isSafeResearchBack(value) ? value : researchRouteHref(fallback)
  window.history.pushState(null, '', target)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function isCancellationCutoffTrace(event: ResearchRunDetailView['trace'][number]): boolean {
  return event.event_type === 'evidence_accepted' && event.status === 'succeeded' && event.reference_type === 'cancellation_cutoff'
}

function useDialogFocus(ref: RefObject<HTMLElement | null>, scopeKey: string): void {
  useModalScrollLock()
  useModalFocusContainment(ref, { scopeKey })
}

function pushResearchRoute(route: ResearchRoute): void {
  window.history.pushState(null, '', researchRouteHref(route))
  window.dispatchEvent(new PopStateEvent('popstate'))
}

type ResearchPageProps = {
  operations?: OperationsOverview
  operationsLoading: boolean
  operationsFailed: boolean
}

export function ResearchPage({ operations, operationsLoading, operationsFailed }: ResearchPageProps) {
  const queryClient = useQueryClient()
  const pageRef = useRef<HTMLDivElement>(null)
  const [route, setRoute] = useState<ResearchRoute>(() => readResearchRoute())
  const [selectedId, setSelectedId] = useState<string | null>(
    () => { const initial = readResearchRoute(); return initial.name === 'evidence' ? initial.runId ?? null : initial.runId ?? new URLSearchParams(window.location.search).get('run_id') },
  )
  const [composerOpen, setComposerOpen] = useState(false)
  const [input, setInput] = useState(restoredSubmissionText('research-submit') ?? '')
  const [calendarScope, setCalendarScope] = useState<'all' | 'upcoming' | 'history'>('all')
  const [viewMode, setViewMode] = useState<'runs' | 'library'>(() => route.name === 'document-review' ? 'library' : 'runs')
  const [reportContextOpen, setReportContextOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [reportReadRevision, setReportReadRevision] = useState(0)
  const reportFocused = ['report', 'report-incomplete', 'evidence', 'graph', 'graph-node', 'technical'].includes(route.name)
  const listVisible = viewMode === 'runs' && (!reportFocused || reportContextOpen)
  const historyVisible = viewMode === 'runs' && historyOpen
  const navigate = (next: ResearchRoute) => {
    pushResearchRoute(researchNavigationRoute(next, window.location.hash || '#/home'))
  }
  const runsQuery = useQuery({
    queryKey: ['research-runs'],
    queryFn: api.researchRuns,
    enabled: listVisible || historyVisible || (viewMode === 'runs' && !selectedId && route.name !== 'evidence'),
    refetchInterval: 3_000,
    retry: false,
  })
  const inboxQuery = useQuery({
    queryKey: ['research-inbox'],
    queryFn: api.researchInbox,
    enabled: historyVisible,
    refetchInterval: 5_000,
    retry: false,
  })
  const macroEventsQuery = useQuery({
    queryKey: ['macro-events', calendarScope],
    queryFn: () => api.macroEvents(calendarScope),
    enabled: listVisible,
    refetchInterval: 15 * 60_000,
    retry: false,
  })
  const runs = runsQuery.data ?? []
  const displayRuns = useMemo(
    () => [...runs].sort((left, right) => Number(Boolean(left.parent_run_id)) - Number(Boolean(right.parent_run_id))),
    [runs],
  )
  useEffect(() => {
    if (!selectedId && route.name !== 'evidence') {
      const preferred = displayRuns.find((item) => TERMINAL.has(item.status) && Boolean(item.artifact_id) && !item.parent_run_id)
        ?? displayRuns.find((item) => TERMINAL.has(item.status) && Boolean(item.artifact_id))
        ?? displayRuns.find((item) => item.status === 'researching')
        ?? displayRuns[0]
      if (preferred) setSelectedId(preferred.run_id)
    }
    // A newly queued Run or a shared older Run may not be in this page of
    // the list yet. Its detail endpoint owns existence; keep the selection
    // while the list refreshes instead of silently opening an older report.
  }, [displayRuns, route.name, selectedId])
  useEffect(() => {
    if (!selectedId) return
    const url = new URL(window.location.href)
    if (url.searchParams.get('run_id') === selectedId) return
    url.searchParams.set('run_id', selectedId)
    window.history.replaceState(null, '', url)
  }, [selectedId])
  useEffect(() => {
    const selectFromUrl = () => {
      const next = readResearchRoute()
      setRoute(next)
      setSelectedId(next.name === 'evidence' ? next.runId ?? null : next.runId ?? new URLSearchParams(window.location.search).get('run_id'))
      if (next.name === 'document-review') setViewMode('library')
    }
    window.addEventListener('popstate', selectFromUrl)
    window.addEventListener('hashchange', selectFromUrl)
    return () => {
      window.removeEventListener('popstate', selectFromUrl)
      window.removeEventListener('hashchange', selectFromUrl)
    }
  }, [])
  useEffect(() => {
    if (route.runId && route.runId !== selectedId) setSelectedId(route.runId)
  }, [route.runId, selectedId])
  useEffect(() => {
    if (route.name !== 'evidence' && route.name !== 'event') return
    const closeOnEscape = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === 'Escape') goToResearchBack(route.back, { name: 'report', runId: selectedId ?? undefined })
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [route.name, selectedId])
  const detailQuery = useQuery({
    queryKey: ['research-run', selectedId],
    queryFn: () => api.researchRun(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: query => query.state.data && TERMINAL.has(query.state.data.run.status) ? false : 3_000,
    retry: false,
  })
  const detail = detailQuery.data
  useLayoutEffect(() => {
    if (selectedId && (detailQuery.isError || (detailQuery.isSuccess && !detail))) abandonGraphFocusReturn(window, selectedId)
  }, [selectedId, detailQuery.isError, detailQuery.isSuccess, detail])
  const closeGraphRoute = (view: GraphView) => {
    const current = readResearchRoute()
    const next = graphReturnRoute(window.location.hash, selectedId ?? undefined, view)
    if (selectedId && detail?.run.run_id === selectedId && current.runId === selectedId
      && ['graph', 'graph-node'].includes(current.name) && next.runId === selectedId) {
      requestGraphFocusReturn(pageRef.current, selectedId, researchRouteHref(next))
    }
    pushResearchRoute(next)
  }
  const libraryScopeQuery = useQuery({
    queryKey: ['research-run-library-scope', selectedId],
    queryFn: () => api.researchRunScope(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const parentRunId = detail?.run.parent_run_id
  const parentLibraryScopeQuery = useQuery({
    queryKey: ['research-run-library-scope', parentRunId],
    queryFn: () => api.researchRunScope(parentRunId!),
    enabled: Boolean(parentRunId) && libraryScopeQuery.isSuccess,
    retry: false,
  })
  // The detail request owns existence. A missing record, timeout or interrupted
  // connection must keep this Run selected; the first list page cannot replace
  // the identity in a shared link with another readable report.
  const comparisonQuery = useQuery({
    queryKey: ['research-run-comparison', selectedId],
    queryFn: () => api.researchRunComparison(selectedId!),
    enabled: Boolean(selectedId) && detail?.run.run_id === selectedId && Boolean(detail.run.artifact_id) && TERMINAL.has(detail.run.status) && !detailQuery.isError,
    retry: false,
  })
  const observabilityQuery = useQuery({
    queryKey: ['research-observability', selectedId],
    queryFn: () => api.researchObservability(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const valueEvaluationQuery = useQuery({
    queryKey: ['research-value-evaluation', selectedId],
    queryFn: () => api.researchValueEvaluation(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const forecastAuditQuery = useQuery({
    queryKey: ['research-forecast-audit', selectedId],
    queryFn: () => api.researchForecastAudit(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const calibrationsQuery = useQuery({
    queryKey: ['strategy-calibrations'],
    queryFn: api.strategyCalibrations,
    retry: false,
  })
  const reviewPlansQuery = useQuery({
    queryKey: ['research-review-plans', selectedId],
    queryFn: () => api.researchReviewPlans(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: 5_000,
    retry: false,
  })
  useResearchTrace(selectedId, detail?.run.latest_sequence_no ?? 0, queryClient)

  const submit = useSubmission('research-submit', { text: input.trim() }, JSON.stringify([route, selectedId, composerOpen]),
    (payload, key) => api.submitResearch((payload as { text: string }).text, key, productOwner),
    (result) => {
      setInput('')
      setComposerOpen(false)
      navigate({ name: 'report', runId: result.run_id })
      void queryClient.invalidateQueries({ queryKey: ['research-runs'] })
    })
  const command = useRunCommand(selectedId, OWNER_ID, receipt => {
    const result = receipt.result
    if (result.target_run_id) navigate({ name: 'report', runId: result.target_run_id, back: researchRouteHref({ name: 'report', runId: result.source_run_id }) })
  })
  const sendCommand = (action: ResearchRunCommand['command'], reasonOverride?: string) => {
    const reason = (reasonOverride ?? command.reason).trim()
    if (!selectedId || !reason || command.locked || command.busy) return
    command.execute(action, reason)
  }

  const worker = operations?.services.find((item) => item.service_id === 'hub-research-worker')
  const stats = useMemo(() => ({
    active: runs.filter((item) => item.status === 'researching' || item.status === 'queued').length,
    gaps: runs.filter((item) => item.coverage?.status === 'insufficient').length,
    completed: runs.filter((item) => TERMINAL.has(item.status)).length,
    // The list endpoint intentionally does not carry cost. Do not present an
    // incomplete aggregate as $0; only the selected detail can provide a
    // known estimate and null remains visibly unknown.
    cost: detail?.estimated_cost_usd ?? null,
  }), [runs, detail])

  if (viewMode === 'library') {
    return <>
      <div className="research-view-switch"><button className="button secondary" onClick={() => { setViewMode('runs'); navigate({ name: 'home', runId: selectedId ?? undefined }) }}>返回研究 Runs</button><span>资料入口与研究域隔离</span></div>
      <ResearchLibraryPanel routeVersionId={route.versionId} onOpenVersion={(versionId) => navigate({ name: 'document-review', versionId })} />
    </>
  }

  const researchControls = <>
    <div className="research-toolbar">
      <div><span className={`live-dot ${worker?.status !== 'online' ? 'warning' : ''}`} /><strong>{worker?.status === 'online' ? '后台研究循环在线' : '后台研究循环未确认在线'}</strong><small>{worker?.heartbeat_at ? `最近心跳 ${formatDate(worker.heartbeat_at)}` : '暂无心跳记录'}</small></div>
      <div className="heading-actions"><button className="button secondary" onClick={() => { setViewMode('library'); navigate({ name: 'document-review', versionId: route.versionId }) }}><BookOpenCheck size={14} />资料研究入口</button><button className="button primary" onClick={() => setComposerOpen((value) => !value)}><FileSearch size={15} />获取最新操作建议（BTC 模板）</button></div>
    </div>
    {composerOpen && <section className="research-composer" aria-label="获取最新操作建议（BTC 模板）">
      <p>将新建一项 BTC 模板研究。系统会固定核验当前 BTC 现货、衍生品、已收盘日线趋势与最新公开信息，形成未来 7 天研究观点。补充关注点可留空。</p>
      <textarea value={input} disabled={submit.busy} onChange={(event) => setInput(event.target.value)} placeholder="可选：例如“重点关注美债收益率和 ETF 资金流”" autoFocus />
      <div><span>{input.trim().length} 字符</span><button className="button primary" disabled={submit.busy || submit.blocked} onClick={submit.execute}>{submit.busy ? <RotateCcw className="spin" size={14} /> : <Send size={14} />}{submit.busy ? '正在获取' : '开始 BTC 分析'}</button></div>
      {!submit.busy && submit.originalPending && <p role="status">有一笔尚未确认的提交；继续提交将核对原请求，不会自动再次发起研究。</p>}
      <SubmissionError error={submit.error} />
    </section>}
    <SubmissionRecovery state={submit} />
  </>
  const calendar = <MacroEventCalendar events={macroEventsQuery.data ?? []} scope={calendarScope} onScopeChange={setCalendarScope} loading={macroEventsQuery.isLoading} failed={macroEventsQuery.isError} onSelectEvent={(eventId) => navigate({ name: 'event', eventId, runId: selectedId ?? undefined })} />
  const runList = <ResearchRunList runs={displayRuns} selectedId={selectedId} loading={runsQuery.isLoading} failed={runsQuery.isError} onSelect={(runId) => { setSelectedId(runId); navigate({ name: 'report', runId }) }} />
  const readingRoute = detail ? reportReadingRoute(route, detail.run.run_id) : route
  const detailPanel = <section className="panel research-detail-panel">
    <RunCommandRecovery state={command} />
    {selectedId && <div className="research-toolbar" aria-label="读取任务状态">
      <small>{detailQuery.isFetching ? '正在核对任务状态…' : detailQuery.dataUpdatedAt ? `页面最近读取：${formatDate(new Date(detailQuery.dataUpdatedAt).toISOString())}` : '尚未读取到任务记录'}</small>
      <button className="button secondary" disabled={detailQuery.isFetching} onClick={() => { setReportReadRevision(value => value + 1); void detailQuery.refetch({ cancelRefetch: false }) }}><RefreshCw size={14} />刷新任务状态</button>
    </div>}
    {detailQuery.isLoading && <ResearchState icon={Activity} text="正在读取研究记录…" />}
    {detailQuery.isError && <div className="research-notice danger" role="alert"><AlertTriangle size={15} /><div><strong>{detailQuery.error.message === 'research_run_not_found' ? '未找到链接中的研究任务' : '这次研究暂时读取失败'}</strong><p>{detailQuery.error.message === 'research_run_not_found' ? '请核对任务链接，或返回研究列表查找。' : detail ? '下方保留这次研究上次读取的内容，当前状态尚未确认。' : '任务编号已保留，可以重新读取或稍后从研究列表继续查看。'}</p><button className="button secondary" disabled={detailQuery.isFetching} onClick={() => void detailQuery.refetch()}>{detailQuery.isFetching ? '正在读取…' : '重新读取这次研究'}</button></div></div>}
    {!selectedId && !detailQuery.isLoading && <ResearchState icon={SearchCheck} text="选择一个研究任务查看计划、证据和结论。" />}
    {detail && (route.name !== 'evidence' || detail.run.run_id === route.runId) && <ResearchRunDetail detail={detail} reportReadRevision={reportReadRevision} currentStateUnconfirmed={detailQuery.isError} libraryScope={libraryScopeQuery.data} parentLibraryScope={libraryScopeQuery.isSuccess ? parentLibraryScopeQuery.data : undefined} libraryScopeLoading={libraryScopeQuery.isLoading} libraryScopeFailed={libraryScopeQuery.isError} comparison={comparisonQuery.data} commandReason={command.reason} setCommandReason={command.setReason} commandPending={command.busy || command.locked} commandBusy={command.busy} commandError={null} onCommand={sendCommand} focusSources={route.focus === 'sources'} followup={<ReportFollowup runId={detail.run.run_id} parentRunId={detailQuery.isError ? null : detail.run.parent_run_id} admissionOrigin={detail.run.admission_origin} plans={reviewPlansQuery.data} suggestedReviewAt={detail.action_summary.next_review_at} loading={reviewPlansQuery.isLoading} failed={reviewPlansQuery.isError} refreshing={reviewPlansQuery.isFetching} back={window.location.hash} onRefresh={() => void reviewPlansQuery.refetch()} />} initialDetailView={readingRoute.name === 'technical' ? 'technical' : 'result'} onDetailViewChange={(view) => navigate({ name: view === 'technical' ? 'technical' : 'report', runId: selectedId ?? undefined, back: route.back, focus: route.focus, graphQuery: route.graphQuery, graphFilter: route.graphFilter, graphView: route.graphView })} graphRouteOpen={readingRoute.name === 'graph' || readingRoute.name === 'graph-node'} graphNodeId={readingRoute.nodeId} onOpenGraphRoute={(view) => navigate({ name: 'graph', runId: selectedId ?? undefined, ...view })} onOpenGraphNode={(nodeId, view) => navigate({ name: 'graph-node', runId: selectedId ?? undefined, nodeId, ...view })} onCloseGraphNode={() => goToResearchBack(researchRouteHref(graphBaseRoute(window.location.hash, selectedId ?? undefined)), { name: 'graph', runId: selectedId ?? undefined })} onCloseGraphRoute={closeGraphRoute} onOpenEvidence={(evidenceId) => navigate({ name: 'evidence', runId: selectedId ?? undefined, evidenceId })} revisionFeedback={<ReportRevisionFeedback key={detail.run.run_id} runId={detail.run.run_id} hasReport={Boolean(detail.run.artifact_id) && TERMINAL.has(detail.run.status)} unconfirmed={detailQuery.isError || detail.run.run_id !== selectedId} />} diagnostics={<ResearchDiagnostics observability={observabilityQuery.data} evaluation={valueEvaluationQuery.data} evaluationFailed={valueEvaluationQuery.isError} forecastAudit={forecastAuditQuery.data} calibrations={calibrationsQuery.data} reviewPlans={reviewPlansQuery.data} loading={observabilityQuery.isLoading || valueEvaluationQuery.isLoading || forecastAuditQuery.isLoading || calibrationsQuery.isLoading || reviewPlansQuery.isLoading} />} />}
  </section>

  return <div ref={pageRef} className="content-wrap research-page">
    {['report', 'report-incomplete', 'technical'].includes(route.name) && <AssetBackLink back={route.back ?? null} />}
    <PageTitle
      title={route.name === 'technical' ? '研究过程与材料' : reportFocused ? '研究报告' : '研究任务记录'}
      eyebrow="市场 / 宏观研究"
      copy={route.name === 'technical' ? '核对这次研究保存的材料、执行过程和限制；未完成的研究也可在这里继续查阅。' : reportFocused ? '先看结论与限制；证据、过程和其他任务可按需展开。' : '查看研究进度、证据与结论，也可发起临时研究。'}
      trailing={operationsLoading ? '正在读取研究进程…' : worker?.status === 'online' ? '研究进程在线' : '研究进程未确认在线'}
    />
    {operationsFailed && <div className="research-notice danger"><AlertTriangle size={15} />运行健康数据不可用，研究结论不会被伪装为在线。</div>}
    {route.name === 'evidence' && !route.runId && <div className="research-notice danger" role="alert"><AlertTriangle size={15} />证据链接缺少 Run ID，无法确认这条证据属于哪个研究任务。</div>}
    {route.name === 'report-incomplete' && <div className="research-notice warning"><AlertTriangle size={15} /><span>{detail?.run.coverage?.status === 'sufficient' ? '本 Run 已提交受限研究报告：关键 Evidence 覆盖通过，但复核、校准或交易权限仍有限制。请以下方审计结果为准。' : '本 Run 的研究结论受限。请查看下方证据缺口、复核意见和执行权限；页面不会把它渲染成完整交易结论。'}</span></div>}
    {reportFocused ? <>
      {detailPanel}
      <details className="research-history-panel research-report-context" open={reportContextOpen} onToggle={event => setReportContextOpen(event.currentTarget.open)}>
        <summary>查看其他任务、事件日历与临时研究</summary>
        <div className="research-report-context-body">{researchControls}{calendar}{runList}</div>
      </details>
    </> : <>
      {researchControls}
      {calendar}
      <div className="research-workspace">{runList}{detailPanel}</div>
    </>}
    <details className="research-history-panel" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary>查看任务概览与历史 Inbox</summary>
      <section className="research-metrics" aria-label="研究任务概览">
        <ResearchMetric icon={Activity} label="正在处理" value={stats.active} detail="排队或研究中" tone="blue" />
        <ResearchMetric icon={ShieldAlert} label="证据不足" value={stats.gaps} detail="需要补充证据" tone="amber" />
        <ResearchMetric icon={CheckCircle2} label="已有终态" value={stats.completed} detail="已完成或有界停止" tone="green" />
        <ResearchMetric icon={CircleGauge} label="选中任务成本" value={stats.cost === null ? '未知' : `$${stats.cost.toFixed(4)}`} detail="仅显示已知模型/工具估算" tone="neutral" />
      </section>
      <ResearchInboxPanel inbox={inboxQuery.data?.items ?? []} loading={inboxQuery.isLoading} failed={inboxQuery.isError} onSelectRun={setSelectedId} />
    </details>
    {route.name === 'event' && route.eventId && <EventDrawer event={macroEventsQuery.data?.find((item) => item.macro_event_id === route.eventId) ?? null} onClose={() => goToResearchBack(route.back, { name: selectedId ? 'report' : 'home', runId: selectedId ?? undefined })} />}
    {route.name === 'evidence' && route.runId && route.evidenceId && detail?.run.run_id === route.runId && <EvidenceDrawer runId={route.runId} originalInputId={route.evidenceId} owner={OWNER_ID} libraryInput={libraryScopeQuery.data?.context_inputs.find((item) => item.evidence_id === route.evidenceId)} libraryLoading={libraryScopeQuery.isPending && route.evidenceId.startsWith('library:')} evidence={detail.evidence.find((item) => item.evidence_id === route.evidenceId) ?? null} onClose={() => goToResearchBack(route.back, { name: 'report', runId: route.runId })} />}
  </div>
}

function MacroEventCalendar({ events, scope, onScopeChange, loading, failed, onSelectEvent }: { events: MacroEventView[]; scope: 'all' | 'upcoming' | 'history'; onScopeChange: (scope: 'all' | 'upcoming' | 'history') => void; loading: boolean; failed: boolean; onSelectEvent: (eventId: string) => void }) {
  return <section className="macro-event-calendar" aria-label="宏观事件日历">
    <div className="macro-event-calendar-heading"><div><strong>宏观事件日历</strong><small>官方时间与发布状态；历史版本保留，修订不会覆盖复核依据</small></div><span>{loading ? '同步中' : `${events.length} 项`}</span></div>
    <div className="macro-event-calendar-tabs" role="tablist" aria-label="日历范围">{(['all', 'upcoming', 'history'] as const).map((item) => <button key={item} type="button" role="tab" aria-selected={scope === item} className={scope === item ? 'active' : ''} onClick={() => onScopeChange(item)}>{item === 'all' ? '全部' : item === 'upcoming' ? '即将发生' : '历史发布'}</button>)}</div>
    {failed && <p className="macro-event-calendar-empty">事件日历不可用，研究不会臆测事件预期。</p>}
    {!loading && !failed && events.length === 0 && <p className="macro-event-calendar-empty">尚未发现官方日历事件；下次来源轮询后更新。</p>}
    {events.length > 0 && <div className="macro-event-calendar-list">{events.slice(0, 8).map((event) => <button type="button" className="macro-event-card" key={event.macro_event_id} onClick={() => onSelectEvent(event.macro_event_id)}>
      <div><strong>{macroEventLabel(event.family)}</strong><time>{calendarTime(event)}</time></div>
      <small>{event.source_id} · {calendarStatus(event)}{event.revision_flag ? ' · 已修订' : ''}</small>
      <dl><div><dt>前值</dt><dd>{macroValue(event.prior_value, event.unit)}</dd></div><div><dt>预期</dt><dd>{macroValue(event.consensus_value, event.unit)}</dd></div><div><dt>实际</dt><dd>{macroValue(event.actual_value, event.unit)}</dd></div><div><dt>意外</dt><dd>{event.surprise === null ? '待发布' : macroValue(event.surprise, event.unit)}</dd></div></dl>
    </button>)}</div>}
  </section>
}

function EventDrawer({ event, onClose }: { event: MacroEventView | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null)
  useDialogFocus(dialogRef, `event:${event?.macro_event_id ?? 'missing'}`)
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside ref={dialogRef} tabIndex={-1} className="run-drawer context-drawer" role="dialog" aria-modal="true" aria-label="宏观事件详情" onClick={(eventClick) => eventClick.stopPropagation()}>
      <div className="drawer-header"><div><span className="eyebrow"><CalendarDays size={13} />EVENT DETAIL</span><h2>{event ? macroEventLabel(event.family) : '事件不可用'}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭事件详情"><X size={17} /></button></div>
      {!event ? <div className="inspector-message danger"><AlertTriangle size={16} />该事件已不在当前日历账本中。</div> : <>
        <div className="drawer-meta"><span className="status-pill warning"><span className="status-dot" />{calendarStatus(event)}</span><span className="mono">{event.macro_event_id}</span></div>
        <section className="drawer-section"><h3>发布时间</h3><div className="detail-grid"><div className="detail-cell"><span>Scheduled</span><strong>{calendarTime(event)}</strong></div><div className="detail-cell"><span>Updated</span><strong>{formatDate(event.updated_at)}</strong></div><div className="detail-cell"><span>Source</span><strong>{event.source_id}</strong></div><div className="detail-cell"><span>Authority</span><strong>{event.source_authority}</strong></div></div></section>
        <section className="drawer-section"><h3>数值快照</h3><div className="detail-grid"><div className="detail-cell"><span>前值</span><strong>{macroValue(event.prior_value, event.unit)}</strong></div><div className="detail-cell"><span>预期</span><strong>{macroValue(event.consensus_value, event.unit)}</strong></div><div className="detail-cell"><span>实际</span><strong>{macroValue(event.actual_value, event.unit)}</strong></div><div className="detail-cell"><span>意外</span><strong>{event.surprise === null ? '待发布' : macroValue(event.surprise, event.unit)}</strong></div></div>{event.revision_flag && <div className="inspector-message"><RefreshCw size={15} />该事件存在修订版本，历史值不会覆盖原始观测。</div>}</section>
        <section className="drawer-section"><h3>来源</h3>{event.source_url ? <a className="source-link" href={event.source_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开来源页面</a> : <div className="empty-line">未提供来源链接</div>}</section>
      </>}
    </aside>
  </div>
}

function libraryExcerptText(value: string): string {
  return value.replace(/^Reviewed library source: .*? Part (\d+)\/(\d+)\.\s*/, '资料正文 · 第 $1/$2 个片段\n')
    .replace(/\[document:\/\/[^\s;]+\/page\/(\d+)\/block\/(\d+); chars \d+:\d+\]\s*/g, '\n\n第 $1 页 · 第 $2 段\n')
}

function SavedEvidenceOriginal({ runId, evidenceId }: { runId: string; evidenceId: string }) {
  const [open, setOpen] = useState(false)
  const original = useQuery({
    queryKey: ['evidence-original', runId, evidenceId],
    queryFn: ({ signal }) => api.researchEvidenceOriginal(runId, evidenceId, signal),
    enabled: open, retry: false, staleTime: Infinity,
  })
  return <><p>阅读取证时保存并通过完整性校验的文本；来源网站的当前内容可能已经变化。</p><button className="button secondary" aria-expanded={open} onClick={() => setOpen(value => !value)}><BookOpenCheck size={14} />{open ? '收起已保存的原文' : '阅读已保存的原文'}</button>{open && <div aria-label="已保存的网页原文">{original.isPending ? <p role="status">正在读取已保存的原文…</p> : original.isError ? <><p role="alert">{original.error.message}</p><button className="button secondary" onClick={() => void original.refetch()}>重试读取原文</button></> : <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 400, overflowY: 'auto', lineHeight: 1.7 }}>{original.data}</p>}</div>}</>
}

export function OriginalInputReadback({ runId, evidenceId, owner }: { runId: string; evidenceId: string; owner: string }) {
  const query = useQuery({
    queryKey: ['research-original-input', runId, evidenceId, owner],
    queryFn: ({ signal }) => api.researchOriginalInput(runId, evidenceId, owner, signal),
    enabled: Boolean(runId && evidenceId && owner), retry: false, staleTime: Infinity,
  })
  const receipt = query.data?.run_id === runId && query.data.owner_id === owner && query.data.input.evidence_id === evidenceId ? query.data : undefined
  if (query.isPending) return <p role="status">正在读取这份原输入的档案…</p>
  if (!receipt) return <section className="drawer-section"><p role="alert">这份原输入的身份或档案暂时无法核对，不能将它记作已采纳证据。较早任务可能未保存可核对的所有者记录。</p><button className="button secondary" onClick={() => void query.refetch()}>重新读取输入档案</button></section>
  const input = receipt.input
  const inputKind = { document: '研究输入文本', web: '网页输入', market: '市场数据输入', official: '官方材料输入', transcript: '转录文本输入' }[input.kind] ?? '研究输入'
  return <section className="drawer-section" aria-label="原始研究输入">
    <h3>原始研究输入</h3><p>这是本任务获准引用的原始研究上下文，不属于已采纳的公开市场证据。</p>
    <p><strong>授权原输入 · {sourceAuthorityName(input.authority)}</strong>{input.authority === 'unverified' && ' · 未核验上下文'}</p>
    <div className="detail-grid"><div className="detail-cell"><span>输入类别</span><strong>{inputKind}</strong></div><div className="detail-cell"><span>读取时间</span><strong>{formatDate(input.observed_at)}</strong></div><div className="detail-cell"><span>发布时间</span><strong>{input.published_at ? formatDate(input.published_at) : '未记录发布时间'}</strong></div></div>
    <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{input.excerpt}</p>
    {input.source_url && /^https?:\/\//i.test(input.source_url) ? <a className="source-link" href={input.source_url} target="_blank" rel="noreferrer">打开原输入来源</a> : <p>未提供可打开的外部来源链接；以上为本任务保存的实际输入。</p>}
    <details><summary>核对原输入档案</summary><p style={{ overflowWrap: 'anywhere' }}>输入标识：{input.evidence_id}<br />来源标识：{input.source_id}<br />原内容身份 SHA-256：{input.content_hash}<br />本段文本 SHA-256：{receipt.excerpt_sha256}<br />原任务请求 SHA-256：{receipt.request_hash}<br />输入回执：{receipt.request_receipt.output_id} · {formatDate(receipt.request_receipt.recorded_at)}</p></details>
  </section>
}

export function EvidenceDrawer({ evidence, runId, originalInputId, owner = OWNER_ID, libraryInput, libraryLoading = false, onClose }: { evidence: ResearchRunDetailView['evidence'][number] | null; runId?: string; originalInputId?: string; owner?: string; libraryInput?: ResearchRunScope['context_inputs'][number]; libraryLoading?: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null)
  useDialogFocus(dialogRef, `evidence:${runId ?? 'unknown-run'}:${evidence?.evidence_id ?? libraryInput?.evidence_id ?? 'missing'}`)
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside ref={dialogRef} tabIndex={-1} className="run-drawer context-drawer" role="dialog" aria-modal="true" aria-label="证据详情" onClick={(eventClick) => eventClick.stopPropagation()}>
      <div className="drawer-header"><div><span className="eyebrow"><Network size={13} />证据记录</span><h2>{evidence ? sourceLabel(evidence.source_id) : (libraryInput ? '已审核资料的输入片段' : libraryLoading ? '正在读取输入档案' : runId && originalInputId && !originalInputId.startsWith('library:') ? '研究输入档案' : '证据不可用')}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭证据详情"><X size={17} /></button></div>
      {!evidence && libraryLoading ? <p role="status">正在读取本任务的资料输入回执…</p> : !evidence && libraryInput ? <section className="drawer-section"><h3>实际选入的资料内容</h3><p>这是本任务保存的辅助资料输入，尚需独立事实核验；不代表已满足市场证据或交易条件。</p><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{libraryExcerptText(libraryInput.excerpt)}</p><details><summary>核对实际输入原文</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{libraryInput.excerpt}</p></details><a href={`#/document-review?version=${encodeURIComponent(libraryInput.version_id)}&back=${encodeURIComponent(window.location.hash)}`}>查看这份资料原件与审核记录</a><details><summary>内容指纹与原文定位</summary><p style={{ overflowWrap: 'anywhere' }}>片段 SHA-256：{libraryInput.excerpt_hash}<br />原件 SHA-256：{libraryInput.original_file_hash}</p>{libraryInput.locator_refs.map((locator) => <p key={locator} style={{ overflowWrap: 'anywhere' }}>{locator}</p>)}</details></section> : !evidence && runId && originalInputId && !originalInputId.startsWith('library:') ? <OriginalInputReadback runId={runId} evidenceId={originalInputId} owner={owner} /> : !evidence ? <div className="inspector-message danger"><AlertTriangle size={16} />该证据已不在当前任务的可审计记录中。</div> : <>
        <div className="drawer-meta"><span className={`evidence-kind ${evidence.kind}`}>{evidence.kind === 'web' ? '网页' : evidence.kind === 'market' ? '市场数据' : evidence.kind === 'official' ? technicalName(evidence.kind) : '其他来源'}</span><span className={`status-pill ${evidence.quality === 'accepted' ? 'success' : 'warning'}`}><span className="status-dot" />{evidence.quality === 'accepted' ? '已采纳' : evidence.quality === 'rejected' ? '未采纳' : evidence.quality === 'stale' ? '材料已过时' : '待复核'}</span></div>
        <details className="research-original-record" aria-label="来源标识核对"><summary>核对来源标识</summary><p style={{ overflowWrap: 'anywhere' }}>来源标识：{evidence.source_id}</p></details>
        <section className="drawer-section"><h3>证据身份</h3><div className="detail-grid"><div className="detail-cell"><span>依据类别</span><strong>{savedDailyRequirementLabel(evidence) ?? (evidence.requirement_id === 'market_narrative' ? '公开信息与市场叙事' : requirementNames[evidence.requirement_id] ?? evidence.requirement_id)}</strong></div><div className="detail-cell"><span>来源级别</span><strong>{sourceAuthorityName(evidence.authority)}</strong></div><div className="detail-cell"><span>{evidence.kind === 'market' ? '观测时间' : '读取时间'}</span><strong>{formatDate(evidence.observed_at)}</strong></div><div className="detail-cell"><span>内容发布时间</span><strong>{evidence.published_at ? formatDate(evidence.published_at) : missingEvidencePublicationText(evidence)}</strong></div><div className="detail-cell"><span>时效</span><strong>{evidenceTimeLabel(evidence)}</strong></div><div className="detail-cell"><span>研究轮次</span><strong>{evidence.round}</strong></div><div className="detail-cell"><span>内容指纹</span><strong>{shortId(evidence.content_hash)}</strong></div></div></section>
        <section className="drawer-section"><h3>原始摘要</h3><p className="evidence-drawer-excerpt">{evidenceSummary(evidence.excerpt, evidence.source_id)}</p><details><summary>查看完整摘录</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{evidence.excerpt}</p></details></section>
        <section className="drawer-section"><h3>本次保存的原文</h3>{runId && evidence.content_ref ? <SavedEvidenceOriginal key={evidence.evidence_id} runId={runId} evidenceId={evidence.evidence_id} /> : runId ? <EvidenceCapturePanel key={JSON.stringify([runId, evidence.evidence_id])} runId={runId} evidenceId={evidence.evidence_id} /> : <p>缺少研究任务标识，无法核对原文归属。</p>}</section>
        <section className="drawer-section"><h3>来源</h3>{evidence.source_url ? <a className="source-link" href={evidence.source_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开来源页面</a> : <div className="empty-line">未提供外部来源链接</div>}</section>
      </>}
    </aside>
  </div>
}

function macroValue(value: number | null, unit: string | null): string {
  return value === null ? '—' : `${value}${unit ?? ''}`
}

function macroEventLabel(family: string): string {
  return ({ fomc: 'FOMC', ecb_policy: 'ECB 利率决议', boj_mpm: 'BOJ 会议', cpi: 'CPI', nfp: '非农就业', ppi: 'PPI', pce: 'PCE', gdp: 'GDP' } as Record<string, string>)[family] ?? family
}

const inboxStatusLabel: Record<ResearchInboxItem['status'], string> = {
  watching: '观察中', queued: '排队中', researching: '研究中', report_ready: '报告就绪',
  research_only: '仅研究', rejected: '已拒绝', failed: '失败', cancelled: '已取消',
}
const inboxBaselineLabel: Record<ResearchInboxItem['baseline_status'], string> = {
  not_applicable: '无需基线', pending: '基线采集中', ready: '基线就绪', unavailable: '基线不可用',
}
const inboxNotificationLabel: Record<ResearchInboxItem['notification_status'], string> = {
  not_applicable: '无需通知', pending: '通知待发送', retry_wait: '通知待重试',
  delivered: '通知已送达', failed: '通知失败',
}

function ResearchInboxPanel({ inbox, loading, failed, onSelectRun }: { inbox: ResearchInboxItem[]; loading: boolean; failed: boolean; onSelectRun: (id: string) => void }) {
  return <section className="panel research-inbox-panel" aria-label="Active research inbox">
    <div className="panel-header">
      <div><h2>主动研究 Inbox</h2><p>后台观察、事件触发、报告与通知状态</p></div>
      <span>{loading ? '同步中' : String(inbox.length) + ' 项'}</span>
    </div>
    {failed && <ResearchState icon={AlertTriangle} text="主动研究 Inbox 不可用" danger />}
    {!loading && !failed && inbox.length === 0 && <ResearchState icon={BookOpenCheck} text="暂无主动研究任务或观察事件" />}
    {!failed && inbox.length > 0 && <div className="research-inbox-items">{inbox.slice(0, 8).map((item) => {
      const canOpen = typeof item.run_id === 'string' && item.run_id.length > 0
      return <button key={item.event_id + ':' + (item.run_id ?? 'watch')} className={'research-inbox-item ' + (canOpen ? 'interactive' : '')} disabled={!canOpen} onClick={() => canOpen && onSelectRun(item.run_id!)}>
        <div className="research-inbox-heading"><span className={'research-status ' + inboxTone(item.status)}>{inboxStatusLabel[item.status]}</span><time>{formatRelative(item.updated_at)}</time></div>
        <strong>{item.event_title}</strong>
        <small>{item.event_family ?? '未分类事件'} · {inboxBaselineLabel[item.baseline_status]} · {inboxNotificationLabel[item.notification_status]}</small>
        <div className="research-inbox-meta"><span>{item.gate_status ?? '待裁决'}</span>{item.headline && <span>{item.headline}</span>}{item.next_recheck_at && <span>复查 {formatDate(item.next_recheck_at)}</span>}</div>
        {!canOpen && <em>观察中，尚未触发研究</em>}
      </button>
    })}</div>}
  </section>
}

export function ReportRevisionFeedback({ runId, hasReport, unconfirmed = false }: { runId: string; hasReport: boolean; unconfirmed?: boolean }) {
  const revision = useQuery({
    queryKey: ['product-report-revision', productOwner, runId],
    queryFn: async () => {
      const page = await delivery.reports({ run: runId, offset: 0, unread: false })
      if (page.total !== 1 || page.items.length !== 1 || page.items[0].source_run_id !== runId) {
        throw new Error('这版报告的唯一版本关联无法核对。')
      }
      return page.items[0]
    },
    enabled: hasReport && !unconfirmed,
    retry: false,
  })
  if (!hasReport) return null
  return <section className="research-section" aria-label="反馈这版报告">
    <h3>反馈这版报告</h3>
    {unconfirmed ? <p role="status">当前报告记录尚未确认，反馈入口暂时关闭；请先重新读取这次研究。</p>
      : revision.isPending ? <p role="status">正在核对这版报告…</p>
        : revision.isError ? <p role="alert">这版报告的反馈入口暂时无法核对。<button className="product-link" type="button" onClick={() => void revision.refetch()}>重新核对报告版本</button></p>
          : <><RevisionFeedback revision={revision.data.revision_id} sourceRunId={runId} saved={revision.data.latest_feedback} />{revision.data.strategy_version === 'research.v1' && <p><a className="product-link" href={`#/studies?study=${encodeURIComponent(revision.data.study_id)}&effect_revision=${encodeURIComponent(revision.data.revision_id)}&back=${encodeURIComponent(window.location.hash || `#/report?run=${runId}`)}`}>登记或查看这版研究效果<ArrowRight size={14} /></a></p>}</>}
  </section>
}

function inboxTone(status: ResearchInboxItem['status']) {
  return status === 'report_ready' ? 'success' : status === 'failed' || status === 'rejected' || status === 'cancelled' ? 'danger' : status === 'research_only' ? 'warning' : 'running'
}

export function ResearchDiagnostics({ observability, evaluation, evaluationFailed = false, forecastAudit, calibrations, reviewPlans, loading }: { observability?: ResearchObservabilityView; evaluation?: ResearchValueEvaluation | null; evaluationFailed?: boolean; forecastAudit?: ForecastAuditView[]; calibrations?: StrategyCalibrationView[]; reviewPlans?: ReviewScheduleView[]; loading: boolean }) {
  if (loading && !observability && !evaluation && !forecastAudit && !calibrations && !reviewPlans) return <div className="research-diagnostics loading">正在读取版本、来源和成本投影…</div>
  if (!observability && !evaluation && !forecastAudit && !calibrations && !reviewPlans) return <div className="research-diagnostics unavailable">当前任务暂无可用的可观测或价值评估记录</div>
  const relevantVersions = new Set((forecastAudit ?? []).flatMap((item) => item.strategy_version ? [item.strategy_version] : []))
  const relevantCalibrations = (calibrations ?? []).filter((item) => relevantVersions.has(item.strategy_version))
  return <section className="research-diagnostics" aria-label="Research observability and evaluation">
    {evaluationFailed && <p role="status">研究最新评估本次读取未确认；已显示的旧记录不是这笔版本反馈的当时评估。</p>}
    <div className="diagnostic-grid">
      {observability && <><div><span>运行版本</span><strong>{observability.versions.runtime_id} · {observability.versions.runtime_version}</strong><small>{observability.versions.domain_pack_ref ?? '研究包未记录'} · {observability.versions.domain_pack_version ?? '版本未记录'}</small><small>{observability.versions.pack_basis === 'saved_pack' ? '研究包来自本次冻结凭据' : observability.versions.pack_basis === 'invalid' ? '原研究包凭据无法核验' : '原研究包未保存完整版本凭据'}</small></div><div><span>来源尝试</span><strong>{observability.source_attempts.length} 次</strong><small>{observability.readiness.length ? `完整任务保留 ${observability.readiness.length} 项要求` : '完整任务要求明细无法核验'}</small></div><div><span>成本</span><strong>{observability.cost.status === 'known' && observability.cost.total_usd !== null ? '$' + observability.cost.total_usd.toFixed(4) : '未知/部分'}</strong><small>{observability.cost.unknown_components.length ? observability.cost.unknown_components.map(component => ({ model: '模型费用', search: '检索费用', subscription: '订阅费用', tools: '工具费用' }[component] ?? '其他费用')).join(' · ') : '全部组件已归集'}</small></div></>}
      {evaluation && <div><span>本次研究最新评估（综合反馈）</span><strong>{{ unlabeled: '尚未标注', useful: '有帮助', partial: '部分有帮助', not_useful: '没有帮助' }[evaluation.usefulness]}</strong><small>{{ directional: '方向研究', research_only: '仅供研究', failed: '任务失败' }[evaluation.mode]} · {Math.round(evaluation.citation_traceability * 100)}% 引用可追溯</small></div>}
    </div>
    {observability && <details className="prediction-ledger" aria-label="历史配置核验"><summary>查看本次配置的保存范围</summary><p>研究包中的质量门引用只说明当时声明的配置，不能单独证明该规则已执行。</p><p>角色配置：{observability.versions.role_profile_ref ?? '未记录'}；质量门引用：{observability.versions.gate_policy_ref ?? '未记录'}。</p><p>工具版本：{observability.versions.capability_versions.length ? observability.versions.capability_versions.join('、') : '未保存可核验版本'}；来源注册表：{observability.versions.source_registry_ref ?? '未保存可核验版本'}。</p></details>}
    {evaluation && <section className="prediction-ledger" aria-label="证据覆盖评估依据">
      <div className="prediction-ledger-heading"><strong>证据覆盖评估依据</strong><span>{coverageBasisLabel(evaluation)}</span></div>
      {(evaluation.coverage_basis === 'saved_result' || evaluation.coverage_basis === 'saved_task_request' || evaluation.coverage_basis === 'saved_checkpoint') && evaluation.hard_coverage_ratio !== null && <p>必需证据覆盖 {Math.round(evaluation.hard_coverage_ratio * 100)}%{evaluation.soft_coverage_ratio !== null ? ` · 补充证据覆盖 ${Math.round(evaluation.soft_coverage_ratio * 100)}%` : ''}。覆盖率不代表结论正确率。</p>}
      {evaluation.coverage_cutoff_at && <small>按当时的信息截止时间核验：{formatDate(evaluation.coverage_cutoff_at)}</small>}
    </section>}
    {evaluation && <section className="prediction-ledger" aria-label="预测复盘">
      <div className="prediction-ledger-heading"><div><strong>预测复盘</strong><small>记录已写入；结果只在到期后更新，缺标签不会被算作未命中</small></div><span>评估于 {formatDate(evaluation.evaluated_at)}</span></div>
      {evaluation.outcomes.length === 0
        ? <p className="prediction-ledger-empty">本次没有可复核的方向预测。</p>
        : <div className="prediction-ledger-table" role="table">
          {evaluation.outcomes.map((outcome) => <div className="prediction-ledger-row" role="row" key={outcome.forecast_id}>
            <strong>{outcome.horizon}</strong>
            <span className={`direction ${outcome.direction}`}>{forecastDirectionLabel[outcome.direction]}</span>
            <span>{outcomeStatusLabel(outcome)}</span>
            <span>{outcome.direction === 'neutral' || outcome.direction === 'no_trade' ? '方向：不计成绩' : outcome.direction_correct === null ? '方向：待复核' : outcome.direction_correct ? '方向：正确' : '方向：错误'}</span>
            <span>{outcome.net_return_pct === null ? '净收益：—' : `净收益：${outcome.net_return_pct.toFixed(3)}%`}</span>
            <span>{outcome.brier_score === null ? 'Brier：—' : `Brier：${outcome.brier_score.toFixed(3)}`}</span>
            <small title={outcome.forecast_id}>{outcome.unavailable_reason ? ({ direction_not_applicable: '中性或不交易，不计入方向准确率', outcome_pending: '等待到期观察', market_path_unavailable: '行情路径未齐，最大盈亏暂不可评估' }[outcome.unavailable_reason] ?? '观察依据不足') : (outcome.observed_at ? `观察于 ${formatDate(outcome.observed_at)}` : '等待到期标签')}</small>
          </div>)}
        </div>}
    </section>}
    {forecastAudit && forecastAudit.length > 0 && <section className="forecast-audit" aria-label="前视与追涨偏差审计">
      <div className="prediction-ledger-heading"><div><strong>前视与追涨偏差审计</strong><small>确认预测在结果之前写入，并标出与当时趋势同向的候选</small></div><span>{forecastAudit.length} 条</span></div>
      <div className="forecast-audit-grid">{forecastAudit.map((item) => <article key={item.forecast_id}>
        <header><strong>{item.horizon}</strong><span>{forecastDirectionLabel[item.forecast_direction]}</span></header>
        <p>{item.immutable_snapshot ? '快照已固化' : '缺少完整快照'} · {item.forecasted_before_outcome === null ? '结果待到期' : item.forecasted_before_outcome ? '预测早于结果' : '时间顺序异常'}</p>
        <p>当时趋势：{trailingDirectionLabel[item.trailing_direction]} · {item.same_as_trailing_trend === null ? '无法比较' : item.same_as_trailing_trend ? '与趋势同向，需防滞后' : '与趋势反向'}</p>
        <small>{item.strategy_version ?? '策略版本缺失'} · {item.as_of ?? 'as-of 缺失'}</small>
      </article>)}</div>
    </section>}
    <section className="calibration-panel" aria-label="样本外校准准入">
      <div className="prediction-ledger-heading"><div><strong>样本外校准准入</strong><small>只有固定版本、时间切分和成本后指标同时通过，概率才获得 calibrated 状态</small></div><span>{relevantCalibrations.filter((item) => item.passed).length} 个通过</span></div>
      {relevantCalibrations.length === 0
        ? <p className="prediction-ledger-empty">本次策略没有通过样本外评估的校准凭证；方向仅供研究，概率未获交易准入。</p>
        : <div className="calibration-grid">{relevantCalibrations.map((item) => <article key={item.receipt_id} className={item.passed ? 'passed' : 'failed'}>
          <header><strong>{item.horizon} · {item.strategy_version}</strong><span>{item.passed ? '通过' : '未通过'}</span></header>
          <p>{item.sample_count} 样本 · {item.fold_count} 个样本外时间折 · 准确率 {(item.accuracy * 100).toFixed(1)}%（95% 下界 {(item.accuracy_lower_bound * 100).toFixed(1)}%）</p>
          <p>Brier {item.brier_score.toFixed(3)} / 基准 {item.baseline_brier_score.toFixed(3)} · 净收益 {item.net_return_pct.toFixed(2)}% · 最大回撤 {item.max_drawdown_pct.toFixed(2)}%</p>
          <small>{formatDate(item.evaluated_at)}{item.failure_reasons.length ? ` · ${item.failure_reasons.join(' · ')}` : ''}</small>
        </article>)}</div>}
    </section>
    <section className="review-plan-panel" aria-label="自动复查计划">
      <div className="prediction-ledger-heading"><div><strong>自动复查计划</strong><small>持久化计划才会触发复查；页面时间提示本身不具备调度权威</small></div><span>{reviewPlans?.length ?? 0} 项</span></div>
      {!reviewPlans?.length
        ? <p className="prediction-ledger-empty">尚未生成持久化复查计划。</p>
        : <div className="review-plan-grid">{reviewPlans.map((item) => <article key={item.review_plan_id}>
          <strong>{item.horizon} · {reviewTriggerLabel[item.trigger_type]}</strong><span>{reviewStatusLabel[item.status]}</span>
          <time>{formatDate(item.trigger_at)}</time><small>{item.child_run_id ?? item.failure_code ?? '等待触发'}</small>
        </article>)}</div>}
    </section>
  </section>
}

function coverageBasisLabel(evaluation: ResearchValueEvaluation): string {
  switch (evaluation.coverage_basis) {
    case 'saved_result': return '沿用本次报告保存的覆盖结论'
    case 'saved_checkpoint': return '按原始检查点保存的完整任务要求核验保留材料'
    case 'saved_task_request': return '按执行前冻结的完整任务要求核验保留材料'
    case 'saved_request':
    case 'round_request_only': return '仅保留单轮执行要求，完整任务覆盖率未知'
    case 'invalid': return '原始请求校验未通过，无法核验覆盖率'
    case 'unavailable': return '未保存可核验的原始请求，覆盖率未知'
    default: return '旧评估未记录计算依据，覆盖率暂不展示'
  }
}

const reviewTriggerLabel: Record<ReviewScheduleView['trigger_type'], string> = {
  next_review: '定时复查', macro_event: '宏观事件', invalidation_evidence: '失效证据', outcome_due: '到期复盘',
}
const reviewStatusLabel: Record<ReviewScheduleView['status'], string> = {
  planned: '已计划', admitted: '已入队', completed: '已完成', skipped: '已跳过', failed: '失败',
}

const reviewDeferredReason: Record<string, string> = {
  research_admission_budget_exhausted: '本期研究预算不足，计划已延后，尚未创建新的复查任务。',
  study_research_active: '同一主题已有研究进行中，计划已延后，尚未创建新的复查任务。',
  study_host_terminal_unknown: '同一主题存在结束状态尚未确认的历史会话，计划已延后，尚未创建新的复查任务。',
}
const reviewSkippedReason: Record<string, string> = {
  superseded_by_newer_tracking_basis: '后续跟踪已由新报告接续，原计划已停止；这份报告与原复查记录继续保留。',
  superseded_by_newer_root: '系统记录后续跟踪已交给新报告，原计划已停止；这份报告与原复查记录继续保留。',
  research_review_window_expired: '本次复查的适用时间已结束，计划已停止。',
  review_trigger_already_consumed: '相同触发已登记过复查，系统已停止这项重复计划。',
  study_tracking_paused: '主题跟踪已暂停，原自动复查计划已停止。',
  study_not_active: '主题未处于跟踪状态，原自动复查计划已停止。',
  study_scope_not_enabled: '本主题的研究范围尚未启用，原自动复查计划已停止。',
}

function isReviewHandoff(plan: ReviewScheduleView): boolean {
  return plan.status === 'skipped' && ['superseded_by_newer_tracking_basis', 'superseded_by_newer_root'].includes(plan.failure_code ?? '')
}

function reviewPlanPresentation(plan: ReviewScheduleView): { label: string; explanation: string } {
  if (isReviewHandoff(plan)) return { label: '已由新报告接续', explanation: reviewSkippedReason[plan.failure_code!] }
  if (plan.status === 'planned') {
    const deferredReason = reviewDeferredReason[plan.failure_code ?? '']
    return {
      label: deferredReason ? '已延后' : plan.failure_code ? '等待调度确认' : '已计划',
      explanation: plan.failure_code
        ? deferredReason ?? '调度尚未放行，计划仍保留；尚未创建新的复查任务。'
        : '已登记自动计划，尚未确认执行；计划时间不代表任务已经开始。',
    }
  }
  if (plan.status === 'admitted') return { label: '复查任务已创建', explanation: '已收到复查任务回执；执行进度以原任务记录为准。' }
  if (plan.status === 'completed') return { label: '复查已结束', explanation: '这次复查已结束；请打开记录核对是否形成研究结论、保存的材料和失败原因。' }
  if (plan.status === 'skipped') return { label: '计划已停止', explanation: reviewSkippedReason[plan.failure_code ?? ''] ?? '本项计划已停止，原记录仍保留，可展开核对停止原因。' }
  return {
    label: '复查失败',
    explanation: plan.failure_code === 'research_observation_unavailable'
      ? observationUnavailableExplanation
      : plan.failure_code === 'research_method_binding_failed'
        ? '复查未完成：运行身份核验未完成。失败发生在哪个阶段、旧执行是否已经结束，需核对这次复查的原任务记录。'
        : plan.failure_code ? `复查未完成：${technicalSummaryName(plan.failure_code)}` : '本次复查未完成，原记录未提供具体原因。',
  }
}

export function ReportFollowup({ runId, parentRunId, admissionOrigin, plans, suggestedReviewAt, loading, failed, refreshing, back, onRefresh }: { runId: string; parentRunId?: string | null; admissionOrigin?: ResearchRunView['admission_origin']; plans?: ReviewScheduleView[]; suggestedReviewAt?: string | null; loading: boolean; failed: boolean; refreshing: boolean; back: string; onRefresh: () => void }) {
  const matchingPlans = plans?.filter(plan => plan.parent_run_id === runId)
  const wrongRun = Boolean(plans?.some(plan => plan.parent_run_id !== runId))
  const returnToReport = back || researchRouteHref({ name: 'report', runId })
  const parentId = parentRunId?.trim()
  const actualParent = parentId && parentId !== runId ? parentId : null
  const parentBack = back && readResearchRoute(back).runId === runId ? back : researchRouteHref({ name: 'report', runId, back: back || undefined })
  return <section className="research-section review-plan-panel report-followup" aria-label="本报告的后续复查">
    <div className="prediction-ledger-heading"><div><strong>本报告的后续复查</strong><small>复查会保留原报告，后续观察另行记录判断到期后的市场变化。</small></div><button className="button secondary" disabled={refreshing} onClick={onRefresh}>刷新复查状态</button></div>
    {failed && <p role="alert">后续复查状态暂时读取失败。下方保留最近一次记录，请刷新后核对当前状态。</p>}
    {loading && !plans && <p role="status">正在读取本报告的复查计划…</p>}
    {wrongRun && <p role="alert">返回的复查计划与本报告不一致，未显示其他报告的计划。请重新读取。</p>}
    {!loading && !failed && !wrongRun && matchingPlans?.length === 0 && <>
      <p>{suggestedReviewAt ? `报告建议复查时间：${formatDate(suggestedReviewAt)}；` : ''}尚未登记自动检查计划。</p>
      {actualParent && <><p>{admissionOrigin === 'scheduled_recheck' ? '这是一次定时复查产生的子报告，本报告没有单独登记后续计划。' : '这份报告与上次报告有关联，本版本尚无单独登记的后续计划。'}后续安排请回到原研究记录核对，父报告是否已有计划需打开后确认。</p><a className="product-link" href={researchRouteHref({ name: 'report', runId: actualParent, back: parentBack })}>查看上次报告与复查记录<ArrowRight size={14} /></a></>}
    </>}
    {matchingPlans && matchingPlans.length > 0 && <div className="review-plan-grid">{matchingPlans.map(plan => {
      const presentation = reviewPlanPresentation(plan)
      // Both references come from the server's verified handoff receipt. A
      // reason code or an unrelated latest Run cannot identify a successor.
      const replacementRunId = isReviewHandoff(plan) && plan.replacement_run_id?.trim() && plan.replacement_run_id !== runId
        && plan.replacement_review_plan_id?.trim() && plan.replacement_review_plan_id !== plan.review_plan_id
        ? plan.replacement_run_id : null
      const retryAttempt = plan.retry_of_plan_id?.trim() && plan.retry_of_plan_id !== plan.review_plan_id
        && Number.isSafeInteger(plan.retry_attempt) && (plan.retry_attempt ?? 0) > 0 ? plan.retry_attempt : null
      return <article key={plan.review_plan_id}>
        <header><strong>{horizonName(plan.horizon)} · {reviewTriggerLabel[plan.trigger_type]}</strong><span className={`review-plan-status ${plan.status}`}>{presentation.label}</span></header>
        <p>计划触发时间：<time dateTime={plan.trigger_at}>{formatDate(plan.trigger_at)}</time></p>
        <p>{presentation.explanation}</p>
        {retryAttempt && <p>第 {retryAttempt} 次自动重试；原失败任务与记录继续保留。</p>}
        {replacementRunId
          ? <a className="product-link" href={researchRouteHref({ name: 'report', runId: replacementRunId, back: returnToReport })}>查看接续报告与后续计划<ArrowRight size={14} /></a>
          : isReviewHandoff(plan) && <p>尚无可跳转的接续计划回执，可从主题记录核对后续报告。</p>}
        {plan.child_run_id && <a className="product-link" href={researchRouteHref({ name: 'report', runId: plan.child_run_id, back: returnToReport })}>{plan.status === 'failed' ? plan.failure_code === 'research_method_binding_failed' ? '查看复查任务与原记录' : '查看失败原因与恢复' : plan.status === 'admitted' ? '查看复查进度' : '查看这次复查'}<ArrowRight size={14} /></a>}
        {plan.status === 'failed' && plan.child_run_id && <FollowupFailureDetail52 plan={plan} />}
        {(plan.failure_code || retryAttempt || replacementRunId) && <details><summary>{plan.status === 'failed' ? '查看复查失败原记录' : '查看复查计划原记录'}</summary><pre className="asset-json">{JSON.stringify(plan, null, 2)}</pre></details>}
      </article>
    })}</div>}
  </section>
}

const forecastDirectionLabel: Record<ResearchValueEvaluation['outcomes'][number]['direction'], string> = {
  long: '做多', short: '做空', neutral: '中性', no_trade: '不交易',
}

const trailingDirectionLabel: Record<ForecastAuditView['trailing_direction'], string> = {
  bullish: '上涨', bearish: '下跌', flat: '持平', unavailable: '不可用',
}

function outcomeStatusLabel(outcome: ResearchValueEvaluation['outcomes'][number]): string {
  if (outcome.direction === 'neutral' || outcome.direction === 'no_trade') return '不计方向成绩'
  if (outcome.unavailable_reason === 'outcome_pending') return '待到期观察'
  if (outcome.quality_status === 'unavailable') return '观察数据不可用'
  if (outcome.quality_status === 'estimated') return '估计标签'
  if (outcome.observed_at) return '已复核'
  return '待到期'
}

function useResearchTrace(selectedId: string | null, after: number, queryClient: ReturnType<typeof useQueryClient>) {
  const scopeRef = useRef<{ live: boolean; terminalTails: Set<string> } | null>(null)
  useEffect(() => {
    const scope = { live: true, terminalTails: new Set<string>() }
    scopeRef.current = scope
    return () => { scope.live = false }
  }, [selectedId, queryClient])
  useEffect(() => {
    const scope = scopeRef.current
    if (!selectedId || !scope || typeof EventSource === 'undefined') return
    const source = new EventSource(apiUrl(`/v1/research/runs/${encodeURIComponent(selectedId)}/events?after=${after}`))
    let listening = true
    const refresh = (event: Event) => {
      if (!listening || !scope.live) return
      for (const queryKey of [['research-run', selectedId], ['research-runs']] as const) {
        const fetching = queryClient.getQueryState(queryKey)?.fetchStatus === 'fetching'
        const settled = queryClient.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false })
        // A terminal event may arrive while an older GET still owns this query.
        // Keep at most one fresh trailing read per key, including same-Run cursor reconnects.
        if (event.type === 'session_stopped' && fetching && !scope.terminalTails.has(queryKey[0])) {
          scope.terminalTails.add(queryKey[0])
          void settled.then(() => {
            if (scope.live) void queryClient.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false })
          }, () => {})
        }
      }
    }
    TRACE_EVENTS.forEach((event) => source.addEventListener(event, refresh))
    return () => { listening = false; source.close() }
  }, [selectedId, after, queryClient])
}

function runGapSummary(run: ResearchRunView) {
  if (!run.coverage) return '缺口未核验'
  const gaps = run.coverage?.gaps ?? []
  if (gaps.length === 0) return '无数据缺口'
  const hard = gaps.filter((gap) => gap.importance === 'hard').length
  return hard > 0 ? `${gaps.length} 项数据限制（${hard} 项阻断）` : `${gaps.length} 项数据限制`
}

export function ResearchRunList({ runs, selectedId, loading, failed, onSelect }: { runs: ResearchRunView[]; selectedId: string | null; loading: boolean; failed: boolean; onSelect: (id: string) => void }) {
  return <section className="panel research-run-list">
    <div className="panel-header"><div><h2>研究任务</h2><p>自动发现、用户提交与定时复查</p></div><span>{runs.length}</span></div>
    {loading && <ResearchState icon={Activity} text="正在读取研究队列…" />}
    {failed && <ResearchState icon={AlertTriangle} text="研究队列不可用" danger />}
    {!loading && !failed && runs.length === 0 && <ResearchState icon={BookOpenCheck} text="暂无研究任务" />}
    <div className="research-run-items">{runs.map((run) => <button key={run.run_id} className={`research-run-item ${selectedId === run.run_id ? 'selected' : ''}`} onClick={() => onSelect(run.run_id)}>
      <div className="research-run-heading"><span className={`research-status ${statusTone(run.status)}`}>{statusLabel[run.status]}</span><time>{formatRelative(run.updated_at)}</time></div>
      <strong>{run.event_title}</strong>
      <div className="research-run-meta"><span>{runGapSummary(run)}</span><span>{run.coverage ? `${Math.round(run.coverage.hard_coverage_ratio * 100)}% 必需证据` : coverageUnavailableText(run)}</span></div>
      {run.parent_run_id && <small><TimerReset size={12} />复查 · 上次任务 {shortId(run.parent_run_id)}</small>}
    </button>)}</div>
  </section>
}

export function ResearchRunDetail({ detail, reportReadRevision = 0, currentStateUnconfirmed = false, libraryScope, parentLibraryScope, libraryScopeLoading = false, libraryScopeFailed = false, comparison, commandReason, setCommandReason, commandPending, commandBusy = commandPending, commandError, onCommand, initialDetailView = 'result', focusSources = false, followup = null, onDetailViewChange, graphRouteOpen = false, graphNodeId, onOpenGraphRoute, onOpenGraphNode, onCloseGraphNode, onCloseGraphRoute, onOpenEvidence, revisionFeedback = null, diagnostics = null }: { detail: ResearchRunDetailView; reportReadRevision?: number; currentStateUnconfirmed?: boolean; libraryScope?: ResearchRunScope | null; parentLibraryScope?: ResearchRunScope | null; libraryScopeLoading?: boolean; libraryScopeFailed?: boolean; comparison?: ResearchRunComparisonView; commandReason: string; setCommandReason: (value: string) => void; commandPending: boolean; commandBusy?: boolean; commandError: string | null; onCommand: (action: ResearchRunCommand['command'], reason?: string) => void; initialDetailView?: 'result' | 'technical'; focusSources?: boolean; followup?: ReactNode; onDetailViewChange?: (view: 'result' | 'technical') => void; graphRouteOpen?: boolean; graphNodeId?: string; onOpenGraphRoute?: (view: GraphView) => void; onOpenGraphNode?: (nodeId: string, view: GraphView) => void; onCloseGraphNode?: () => void; onCloseGraphRoute?: (view: GraphView) => void; onOpenEvidence?: (evidenceId: string) => void; revisionFeedback?: ReactNode; diagnostics?: ReactNode }) {
  const { run } = detail
  const evidenceReferenceScope = useMemo(() => savedEvidenceReferenceScope(run.run_id, detail.evidence), [run.run_id, detail.evidence])
  const runCreatedTime = researchRecordTime(run.created_at), triggerCutoffTime = researchRecordTime(detail.trigger_snapshot?.cutoff_at), decisionCutoffTime = researchRecordTime(detail.decision_snapshot?.cutoff_at)
  const preparationWaiting = !currentStateUnconfirmed && currentPreparationWaiting(detail) !== null
  const processSummaryRef = useRef<HTMLElement>(null)
  const nativeFailureRef = useRef<HTMLElement>(null)
  const executionProblemRef = useRef<HTMLElement>(null)
  const executionStopRef = useRef<HTMLElement>(null)
  const problemFocusRun = useRef<string | null>(null)
  useGraphFocusReturn(processSummaryRef, run.run_id)
  const omittedParentVersions = [...new Set([
    ...(parentLibraryScope?.selected_version_ids ?? []),
    ...(detail.context_manifest?.excluded_inputs.filter(item => item.reason === 'parent_library_not_selected_for_this_run' && item.evidence_ref.startsWith('library:')).map(item => item.evidence_ref.slice('library:'.length)) ?? []),
  ])].filter(id => !libraryScope?.selected_version_ids.includes(id))
  const [detailView, setDetailView] = useState<'result' | 'technical'>(initialDetailView)
  const [processOpen, setProcessOpen] = useState(initialDetailView === 'technical' || graphRouteOpen)
  useEffect(() => { setProcessOpen(detailView === 'technical' || graphRouteOpen) }, [detailView, graphRouteOpen, run.run_id])
  const canCancel = ['admitted', 'queued', 'researching', 'retry_wait'].includes(run.status)
  const waitingCopy = waitingResearchCopy[run.status]
  const hasSavedReport = Boolean(run.artifact_id) && !canCancel
  const terminalFailureExplanation = !currentStateUnconfirmed && !preparationWaiting && !hasSavedReport
    && ['failed', 'rejected'].includes(run.status) && run.failure?.origin === 'orchestration'
      ? failureExplanation(run.failure.error_code, run.failure.cause_code, run.failure) : null
  const perpetual = run.research_profile === 'perpetual_decision_v2'
  const canRetry = !perpetual && ['failed', 'cancelled', 'degraded'].includes(run.status)
  const canRecheck = !perpetual && ['completed', 'degraded', 'research_only'].includes(run.status)
  const reviewStatus = reviewDisplayStatus(detail.process_steps, detail.reviews.length, detail.run.status)
  useEffect(() => setDetailView(initialDetailView), [initialDetailView, run.run_id])
  const openProblemDetails = () => {
    problemFocusRun.current = run.run_id
    setDetailView('technical')
    setProcessOpen(true)
  }
  useLayoutEffect(() => {
    if (detailView !== 'technical' || !processOpen || problemFocusRun.current !== run.run_id) return
    problemFocusRun.current = null
    const target = nativeFailureRef.current ?? executionProblemRef.current ?? executionStopRef.current
    target?.scrollIntoView?.({ block: 'start' })
    target?.focus({ preventScroll: true })
  }, [detailView, processOpen, run.run_id])
  const taskSummary = <>
      <ResearchActionSummary currentStateUnconfirmed={currentStateUnconfirmed} preparationWaiting={preparationWaiting} runId={run.run_id} summary={detail.action_summary} failure={run.failure ?? null} nativeFailure={detail.native_execution_failure} runStatus={run.status} stopReason={run.stop_reason} hasSavedReport={hasSavedReport} onOpenProblemDetails={openProblemDetails} horizons={detail.horizons} gaps={run.coverage?.gaps ?? []} causalCase={detail.causal_case} reviews={detail.reviews} reviewStatus={reviewStatus} reviewRecordName={executionReviewRecordName(detail.process_steps)} />
      <ReportNativeFailureCard detail={detail} currentStateUnconfirmed={currentStateUnconfirmed} problemFocusRef={nativeFailureRef} />
  </>
  const processRecords = <>
      <ResearchTrustPanel detail={detail} focusSources={focusSources} />
      {libraryScopeLoading && <section className="research-section research-library-link" aria-label="已连接研究资料"><p>正在读取资料版本关联…</p></section>}
      {libraryScopeFailed && <section className="research-section research-library-link" aria-label="已连接研究资料"><p>资料版本关联不可用，暂不显示版本链接。</p></section>}
      {libraryScope && libraryScope.selected_version_ids.length > 0 && <section className="research-section research-library-link" aria-label="已连接研究资料"><SectionHeading icon={BookOpenCheck} title="已连接研究资料" meta={`${libraryScope.selected_version_ids.length} 个版本`} /><p>本次任务选择了以下资料版本。下方区分实际输入与预算排除；输入资料不等同于报告采纳证据。</p><div>{libraryScope.selected_version_ids.map((versionId, index) => {
        const prefix = `library:${versionId}:part:`
        const included = detail.context_manifest?.effective_inputs.filter((id) => id.startsWith(prefix)).length ?? libraryScope.context_inputs.filter((item) => item.version_id === versionId && item.evidence_id.startsWith(prefix)).length
        const excluded = detail.context_manifest?.excluded_inputs.filter((item) => item.evidence_ref.startsWith(prefix)).length ?? 0
        return <div className="research-library-version" key={`${run.run_id}-${versionId}`}><a href={`#/document-review?version=${encodeURIComponent(versionId)}&run=${encodeURIComponent(run.run_id)}&back=${encodeURIComponent(window.location.hash || `#/report?run=${run.run_id}`)}`} title={versionId}>查看资料版本 {index + 1} <ArrowRight size={13} /></a><p>{included ? `已纳入初始研究上下文：${included} 个正文片段。` : '尚无正文进入初始研究上下文的记录。'}{excluded > 0 && `另有 ${excluded} 个片段因上下文预算未纳入。`}后续是否支持结论，以报告引用为准。</p>{libraryScope.context_inputs.some((item) => item.version_id === versionId) && <details className="library-input-audit"><summary>核对实际输入与原文定位</summary>{libraryScope.context_inputs.filter((item) => item.version_id === versionId).map((item, part) => <details key={item.evidence_id}><summary>核对输入片段 {part + 1}</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{libraryExcerptText(item.excerpt)}</p><small>保留 {item.locator_refs.length} 处原文定位 · 输入片段 SHA-256：{item.excerpt_hash}</small><a href={evidenceRouteHref(run.run_id, item.evidence_id)}>打开片段档案</a></details>)}</details>}</div>
      })}</div></section>}
      <div className="research-detail-tabs" role="tablist" aria-label="研究结果视图">
        <button type="button" role="tab" aria-selected={detailView === 'result'} className={detailView === 'result' ? 'active' : ''} onClick={() => { setDetailView('result'); onDetailViewChange?.('result') }}>用户结果</button>
        <button type="button" role="tab" aria-selected={detailView === 'technical'} className={detailView === 'technical' ? 'active' : ''} onClick={() => { setDetailView('technical'); onDetailViewChange?.('technical') }}>技术详情</button>
      </div>
      <details key={`${run.run_id}-${detailView}`} className="research-report-evidence" open={processOpen} onToggle={(event) => setProcessOpen(event.currentTarget.open)}>
        <summary ref={processSummaryRef}>查看研究过程、证据、审查与预测复盘 <span>{detail.evidence.length} 条证据 · {detail.reviews.length} 份{executionReviewRecordName(detail.process_steps)}</span></summary>
      {!detail.action_summary.research_outlook && <HorizonDecisions runId={run.run_id} horizons={detail.horizons} />}
      {processOpen && <DecisionGraphPanel graph={detail.decision_graph} detail={detail} routeOpen={graphRouteOpen} routeNodeId={graphNodeId} onOpenRoute={onOpenGraphRoute} onOpenNodeRoute={onOpenGraphNode} onCloseNodeRoute={onCloseGraphNode} onCloseRoute={onCloseGraphRoute} onOpenEvidence={onOpenEvidence} />}
      {detailView === 'technical' && (comparison || run.parent_run_id) && <RunComparisonPanel comparison={comparison} run={run} currentStateUnconfirmed={currentStateUnconfirmed} />}
      <ResearchCostRecordSection key={run.run_id} detail={detail} expanded={detailView === 'technical'} visible={processOpen} />
      {detailView === 'technical' && <section className="research-section role-packet-section" aria-label="专家角色证据包">
        <SectionHeading icon={Users} title="专家角色证据包" meta={`${detail.role_packets.length} 个角色 · ${detail.claim_transitions.length} 次观点记录`} />
        <p className="research-section-note">这里展示每个职责收到的证据、开放缺口和结构化结论；它是可复核投影，不是模型私有思维链。</p>
        {detail.role_packets.length === 0 ? <ResearchEmpty text="当前 Run 没有角色证据包" /> : <div className="role-packet-grid">{detail.role_packets.map((packet) => <article key={packet.packet_id} className={`role-packet-card ${packet.status}`}>
          <header><strong>{rolePacketLabel[packet.role] ?? packet.role}</strong><span>{run.coverage ? rolePacketStatus[packet.status] : '覆盖未核验'}</span></header>
          <RolePacketMaterials packet={packet} evidence={detail.evidence} runId={run.run_id} />
          <footer><span>{packet.evidence_refs.length} 条引用</span><span>{!run.coverage ? '职责缺口未核验' : packet.gap_ids.length ? `缺口 ${packet.gap_ids.length}` : '无职责缺口'}</span><span>第 {packet.round} 轮</span></footer>
          <OriginalRecord><p>{packet.conclusion}</p>{packet.claims.map((claim, index) => <p key={index}>{claim}</p>)}<small>角色：{packet.role} · 状态：{packet.status} · 记录：{packet.packet_id}</small></OriginalRecord>
        </article>)}</div>}
        {detail.claim_transitions.length > 0 && <div className="claim-transition-list"><strong>轮次观点变化记录</strong>{detail.claim_transitions.map((transition) => <div key={transition.transition_id}><span>第 {transition.round} 轮</span><p>{directionLabel(transition.from_direction)} → {directionLabel(transition.to_direction)}</p><OriginalRecord>{transition.reason}</OriginalRecord><small>{transition.changed ? '观点有变化' : '未记录方向改变'} · {transition.evidence_refs.length} 条新增证据 · {run.coverage ? `${transition.gap_ids.length} 个缺口` : '缺口未核验'}</small></div>)}</div>}
      </section>}
      {diagnostics && <ResearchRecordSection title="评估、校准与自动复查记录" expanded={detailView === 'technical'}>{diagnostics}</ResearchRecordSection>}
      {detailView === 'technical' && <div className="research-dsh-link"><Code2 size={14} /><span>需要内部排查时，DSH 会保持同一 Run / Session 上下文。</span><a href={`#/dsh?run=${encodeURIComponent(run.run_id)}&back=${encodeURIComponent(window.location.hash || `#/technical?run=${run.run_id}`)}`}>打开 DSH 技术轨迹 <ArrowRight size={13} /></a></div>}
      {detailView === 'technical' && run.failure && <section ref={executionProblemRef} tabIndex={-1} className="research-section research-failure" aria-label={preparationWaiting ? "执行准备尚未确认" : canCancel ? "执行问题与来源" : run.status === 'cancelled' ? "停止原因与记录" : "失败原因与来源"}><SectionHeading icon={AlertTriangle} title={preparationWaiting ? "执行准备尚未确认" : canCancel ? "执行问题与来源" : run.status === 'cancelled' ? "停止原因与记录" : "失败原因与来源"} meta={currentStateUnconfirmed ? "当前状态尚未确认" : preparationWaiting ? "等待原任务恢复核对" : run.failure.retryable ? '允许有界重试' : '需先解决原因'} />{preparationWaiting && <p>{preparationWaitingText}</p>}{failureExplanation(run.failure.error_code, run.failure.cause_code, run.failure) && <p>{failureExplanation(run.failure.error_code, run.failure.cause_code, run.failure)}</p>}<p>{detail.evidence.length} 条证据已保留</p><div className="failure-grid"><strong>{preparationWaiting ? '执行准备尚未确认' : technicalSummaryName(run.failure.error_code)}</strong><span>发生位置 · {technicalSummaryName(run.failure.origin)}</span><span>原因 · {preparationWaiting ? '准备核对未在本次等待窗口内确认' : technicalSummaryName(run.failure.cause_code)}</span><span>调用能力 · {technicalSummaryName(run.failure.capability_id)}</span><span>调用记录 · {run.failure.tool_call_id ? shortId(run.failure.tool_call_id) : '未定位'}</span><span>调用时限 · {run.failure.deadline_ms === null ? '未指定' : `${run.failure.deadline_ms} 毫秒`}</span></div><OriginalRecord>错误码：{run.failure.error_code} · 来源：{run.failure.origin} · 原因码：{run.failure.cause_code ?? '未提供'} · 能力：{run.failure.capability_id ?? '未提供'} · 调用：{run.failure.tool_call_id ?? '未提供'}{preparationWaiting && <pre>{JSON.stringify(run.failure, null, 2)}</pre>}</OriginalRecord></section>}
      {detailView === 'technical' && <section ref={executionStopRef} tabIndex={-1} className="research-section sufficiency-section"><SectionHeading icon={ShieldAlert} title="材料充分性" meta={technicalName(run.coverage?.status, '尚未检查')} />
        <div className="sufficiency-grid"><Coverage label="必需证据覆盖" value={run.coverage?.hard_coverage_ratio ?? null} /><Coverage label="补充证据覆盖" value={run.coverage?.soft_coverage_ratio ?? null} /><div className="stop-reason"><span>{currentStateUnconfirmed || preparationWaiting || !TERMINAL.has(run.status) ? '当前任务状态' : '结束原因'}</span><strong>{currentStateUnconfirmed ? '当前状态尚未确认' : preparationWaiting ? '等待恢复核对' : !TERMINAL.has(run.status) ? (run.status === 'researching' ? '研究进行中' : statusLabel[run.status]) : terminalFailureExplanation ? technicalSummaryName(run.failure?.cause_code ?? run.failure?.error_code) : technicalSummaryName(run.stop_reason?.code, '结束原因未记录')}</strong><OriginalRecord>原因码：{run.stop_reason?.code ?? '未记录'}<p>{run.stop_reason?.detail ?? run.current_action ?? '等待下一条记录'}</p></OriginalRecord></div></div>
        {run.coverage?.gaps.length ? <div className="gap-list">{run.coverage.gaps.map((gap) => <div key={gap.requirement_id}><AlertTriangle size={14} /><div><strong>{requirementNames[gap.requirement_id] ?? '其他材料要求'}</strong><span>{technicalSummaryName(gap.reason_code)} · {technicalName(gap.importance)}</span><OriginalRecord>要求：{gap.requirement_id} · 原因：{gap.reason_code}<p>{gap.query_hint}</p></OriginalRecord></div></div>)}</div> : <div className="research-empty-line">{run.coverage ? <><CheckCircle2 size={14} />没有未关闭的证据缺口</> : <><AlertTriangle size={14} />{coverageUnavailableText(run)}，缺口数量未知。原始材料和调用记录仍保留。</>}</div>}
      </section>}
      {detailView === 'technical' && <section className="research-section"><SectionHeading icon={GitBranch} title="研究计划与调用" meta={`${detail.rounds.length} 轮`} />
        {detail.rounds.length === 0 ? <ResearchEmpty text="尚未形成计划" /> : detail.rounds.map((round) => <div className="research-round" key={round.round}><div className="round-heading"><strong>第 {round.round} 轮</strong><span>{round.plan.tasks.length} 项取证任务</span><time>{formatDate(round.finished_at)}</time></div><OriginalRecord>{round.plan.objective}</OriginalRecord><div className="task-list">{round.plan.tasks.map((task) => <div key={task.task_id}><span>{task.priority}</span><div><strong>{requirementNames[task.requirement_id] ?? '其他材料要求'}</strong><p>{technicalSummaryName(task.capability_id)}</p><OriginalRecord><p>目标：{task.objective}</p><p>问题：{task.question}</p><p>成功条件：{task.success_condition}</p><small>任务：{task.task_id} · 要求：{task.requirement_id} · 能力：{task.capability_id}</small></OriginalRecord></div></div>)}</div><ToolActivity invocations={round.tool_invocations} checkpoints={workflowCheckpointCallIds(detail)} /></div>)}
      </section>}
      <ResearchRecordSection title={`完整证据记录（${detail.evidence.length} 条）`} expanded={detailView === 'technical'}><section className="research-section"><SectionHeading icon={Network} title="证据原始记录" meta={`${detail.evidence.filter((item) => item.quality === 'accepted').length} 已采纳 · ${detail.evidence.filter((item) => item.quality !== 'accepted').length} 待核对或未采纳`} /><p className="research-section-note">证据编号对应本任务完整保存记录，未采纳材料也保留编号；是否可用以各条采纳状态为准。</p>
        {detail.evidence.length === 0 ? <ResearchEmpty text="尚无新增研究证据" /> : <div className="research-evidence-list">{detail.evidence.map((evidence) => <article id={`evidence-${evidence.evidence_id}`} key={evidence.evidence_id}><div><span className={`evidence-kind ${evidence.kind}`}>{technicalName(evidence.kind)}</span><span>{technicalName(evidence.authority)}</span><span className={evidence.quality === 'accepted' ? 'success-text' : 'warning-text'}>{technicalName(evidence.quality)}</span></div><h3>{evidenceReferenceLabel(evidenceReferenceScope, run.run_id, evidence.evidence_id)} · {sourceLabel(evidence.source_id)}</h3><p>{evidenceSummary(evidence.excerpt, evidence.source_id)}</p><OriginalRecord><p>{evidence.excerpt}</p><small>证据：{evidence.evidence_id} · 要求：{evidence.requirement_id} · 来源：{evidence.source_id}</small></OriginalRecord><footer><span>{requirementNames[evidence.requirement_id] ?? '其他材料要求'} · 第 {evidence.round} 轮 · {shortId(evidence.evidence_id)}</span>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer" aria-label={`打开来源 ${sourceLabel(evidence.source_id)}`}><ExternalLink size={13} /></a>}</footer></article>)}</div>}
      </section>
      </ResearchRecordSection>
      <ResearchRecordSection title="主反论据完整记录" expanded={detailView === 'technical'}><section className="research-section"><SectionHeading icon={GitBranch} title="主反论据" meta={detail.causal_case ? '主论据与反向解释' : '等待形成'} />
        {!detail.causal_case ? <ResearchEmpty text="证据尚不足以形成因果案例" /> : <><h3 className="causal-thesis">{researchDisplayText(detail.causal_case.thesis)}</h3><OriginalRecord><p>{detail.causal_case.thesis}</p></OriginalRecord><div className="causal-columns"><CausalChain title="主论据" runId={run.run_id} links={detail.causal_case.main_chain} /><CausalChain title="反向解释" runId={run.run_id} links={detail.causal_case.opposite_chain} counter /></div></>}
      </section>
      </ResearchRecordSection>
      <ResearchRecordSection title={`${executionReviewRecordName(detail.process_steps)}（${detail.reviews.length} 份）`} expanded={detailView === 'technical'}><section className="research-section"><SectionHeading icon={BookOpenCheck} title={reviewStatus} meta={`${detail.reviews.length} 份记录`} />
        {detail.reviews.length === 0 ? <ResearchEmpty text="尚无复核记录" /> : <div className="research-review-list">{detail.reviews.map((review) => <article key={review.review_id}><header><strong>{executionReviewRecordName(detail.process_steps)}</strong><span className={`review-resolution ${review.resolution}`}>{technicalName(review.resolution)}</span></header><p>{/[\u4e00-\u9fff]/.test(review.challenge) ? researchDisplayText(review.challenge) : '复核意见以英文保存，请展开查看原始意见。'}</p><p>{review.limitations.length} 项限制 · 引用 <EvidenceRefLinks refs={review.evidence_refs} runId={run.run_id} /></p><OriginalRecord><h4>{review.target_claim}</h4><p>{review.challenge}</p>{review.limitations.length > 0 && <small>限制：{review.limitations.join('；')}</small>}<small>角色：{review.role} · 状态：{review.resolution}</small></OriginalRecord><footer>{review.evidence_refs.length} 条证据引用 · {formatDate(review.created_at)}</footer></article>)}</div>}
      </section>
      </ResearchRecordSection>
      {detailView === 'technical' && <section className="research-section" aria-label="研究轨迹"><SectionHeading icon={Activity} title="研究轨迹" meta={`${detail.trace.length} 条记录`} />
        <p className="research-section-note">时间按本地日期与时区显示，轨迹保留原记录顺序。记录时间不等于实际执行开始；冻结快照的业务截止时间另列核对。</p>
        <div className="research-section-note research-trace-time-context" style={{ display: 'grid', gap: 4, overflowWrap: 'anywhere' }}>
          <span>任务创建：<time dateTime={runCreatedTime.dateTime} title={run.created_at}>{runCreatedTime.label}</time></span>
          <span>触发快照业务截止：<time dateTime={triggerCutoffTime.dateTime} title={detail.trigger_snapshot?.cutoff_at}>{triggerCutoffTime.label}</time></span>
          <span>决策快照业务截止：<time dateTime={decisionCutoffTime.dateTime} title={detail.decision_snapshot?.cutoff_at}>{decisionCutoffTime.label}</time></span>
        </div>
        {detail.trace.length === 0 ? <ResearchEmpty text="等待第一条轨迹" /> : <div className="research-trace">{detail.trace.map((event) => {
          const recordTime = researchRecordTime(event.occurred_at), timeNotice = researchTraceTimeNotice(event.occurred_at, run.created_at)
          return <div key={event.sequence_no} style={{ gridTemplateColumns: '10px minmax(0, 1fr)' }}><span className={`trace-mark ${event.status}`} /><div style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
            <time dateTime={recordTime.dateTime} title={event.occurred_at} style={{ gridColumn: 'auto', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{recordTime.label}</time>
            <strong>{isCancellationCutoffTrace(event) ? '取消后收到的资料已留档，不发布新报告' : researchTraceTitle(event, run.run_id)}</strong>
            <small>{technicalSummaryName(event.stage)} · {technicalSummaryName(event.status)}{event.reference_id ? ` · 关联${isCancellationCutoffTrace(event) ? '取消操作' : technicalSummaryName(event.reference_type)}` : ''}{event.error_code ? ` · ${technicalSummaryName(event.error_code)}` : ''}</small>
            {timeNotice && <p className="trace-time-notice">{timeNotice}</p>}
            <OriginalRecord><p>{event.summary}</p><small>类型：{event.event_type} · 阶段：{event.stage} · 状态：{event.status} · 引用类型：{event.reference_type ?? '未提供'} · 引用：{event.reference_id ?? '未提供'} · 错误码：{event.error_code ?? '无'}</small><pre>{JSON.stringify(event, null, 2)}</pre></OriginalRecord>
          </div></div>
        })}</div>}
      </section>}
      {detailView === 'technical' && <section className="research-section owner-command"><SectionHeading icon={MessageSquareText} title="任务操作与反馈" meta="操作记录可追溯" />
        <textarea value={commandReason} disabled={commandPending} onChange={(event) => setCommandReason(event.target.value)} placeholder="记录取消、重试、复查或反馈的理由…" />
        <div className="owner-command-actions"><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canCancel} onClick={() => onCommand('cancel')}><Ban size={14} />取消</button><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canRetry} onClick={() => onCommand('retry')}><RefreshCw size={14} />重试</button><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canRecheck} onClick={() => onCommand('recheck')}><TimerReset size={14} />立即复查</button><button className="button primary" disabled={!commandReason.trim() || commandPending} onClick={() => onCommand('feedback')}><MessageSquareText size={14} />记录反馈</button></div>
        {commandError && <p className="command-error" role="alert">命令失败：{commandError}</p>}
      </section>}
      </details>
  </>
  const legacyReport = <>
    {taskSummary}
    {hasSavedReport && <ResearchQuality key={`quality:${run.run_id}`} runId={run.run_id} visible={!currentStateUnconfirmed} readRevision={reportReadRevision} />}
    {processRecords}
  </>
  return <EvidenceReferenceContext.Provider value={evidenceReferenceScope}>
    <header className="research-detail-header">
      <div><div className="research-detail-status"><span className={`research-status ${statusTone(run.status)}`}>{statusLabel[run.status]}</span><span>{originLabel[run.admission_origin]}</span></div><h2>{researchTitle(run, detail.research_focus)}</h2><div className="research-report-context"><strong>{currentStateUnconfirmed ? '最近一次读取的任务记录 · 当前状态尚未确认' : hasSavedReport ? '本次研究结论与过程记录' : TERMINAL.has(run.status) ? '已结束的任务记录 · 未生成报告' : '本次研究任务'}</strong><span>任务最近更新：<time dateTime={run.updated_at}>{formatDate(run.updated_at)}</time></span>{hasSavedReport && <p>这是该次任务保存的研究记录，不会随当前市场自动刷新。报告原件核对结果见下方；任务收尾状态与报告独立审核分别保留。</p>}</div><ResearchFocus run={run} focus={detail.research_focus} /><details className="research-heading-audit"><summary>查看原始任务标题与时间</summary><pre>{JSON.stringify({ run_id: run.run_id, event_id: run.event_id, event_title: run.event_title, created_at: run.created_at, updated_at: run.updated_at }, null, 2)}</pre></details>{detailView === 'technical' && <p className="mono">{run.event_title} · {run.run_id}</p>}</div>
      {detailView === 'technical' && <div className="research-budget" aria-label="本次研究预算"><strong>第 {run.current_round} 轮{run.budget ? ` / 上限 ${run.budget.max_evidence_rounds} 轮` : ' · 原预算未核验'}</strong><span>取证 {detail.total_tool_calls} 次{run.budget ? ` / 上限 ${run.budget.max_tool_calls} 次` : ''} · 协作 {detail.total_subagents} 次{run.budget ? ` / 上限 ${run.budget.max_subagents} 次` : ''}</span>{run.budget && <small>上限来自本次首次保存的请求</small>}</div>}
    </header>
    {detailView === 'technical' && run.budget && <div className="research-progress" aria-label="轮次预算使用比例"><span style={{ width: `${Math.min(100, (run.current_round / run.budget.max_evidence_rounds) * 100)}%` }} /></div>}
    <div className="research-detail-body">
      {!run.coverage && (run.coverage_basis === 'invalid' || run.coverage_basis === 'unavailable' || run.coverage_basis === 'round_request_only') && <section className="research-notice warning" aria-label="覆盖评估依据缺失"><p>{coverageUnavailableText(run)}，不能认定缺口为零。已保留的证据、调用和失败记录仍可查看。</p></section>}
      {omittedParentVersions.length > 0 && <section className="research-notice warning" aria-label="复查资料范围"><div><strong>本次复查未重新使用历史研究选择的 {omittedParentVersions.length} 份资料</strong><p>历史资料选择和授权仅用于当次任务。本次按可用输入和当前市场证据重新核查；原研究问题中提到的资料不代表本次已读取。</p><a href={`#/report?run=${encodeURIComponent(run.parent_run_id!)}`}>查看上次研究</a>{omittedParentVersions.map((id, index) => <p key={id}><a href={`#/document-ready?version=${encodeURIComponent(id)}&back=${encodeURIComponent(window.location.hash)}`}>重新选择资料 {index + 1} 发起研究</a></p>)}</div></section>}
      {hasSavedReport ? <PerpetualSavedReportBoundary runId={run.run_id} visible={!currentStateUnconfirmed} readRevision={reportReadRevision}
        technicalRecords={<><ReportNativeFailureCard detail={detail} currentStateUnconfirmed={currentStateUnconfirmed} problemFocusRef={nativeFailureRef} />{processRecords}</>}>
        {legacyReport}
      </PerpetualSavedReportBoundary> : legacyReport}
      {detailView === 'result' && perpetual && !canCancel && <section className="research-section research-refresh-actions" aria-label="永续研究更新"><h3>需要最新判断？</h3><p>原报告和研究回执保持不变。返回建议与模拟，核对当前已保存账户设置后，显式发起一次新研究。</p><a className="button primary" href="#/settings">按当前已保存设置发起新研究</a></section>}
      {detailView === 'result' && (canRecheck || canRetry || canCancel) && <section className="research-section research-refresh-actions" aria-label="更新研究">
        <div><h3>{currentStateUnconfirmed ? '上次读取的任务状态' : preparationWaiting ? '等待恢复核对' : canCancel ? '本次研究操作' : !hasSavedReport && ['failed', 'cancelled', 'rejected'].includes(run.status) ? '想继续这项研究？' : '需要最新判断？'}</h3><p>{currentStateUnconfirmed ? '当前状态尚未确认，请先重新读取任务记录；原材料仍可核对。' : preparationWaiting ? preparationWaitingText : canCancel ? (waitingCopy?.detail ?? '任务会继续查证并保存报告，完成后本页自动更新。') : !hasSavedReport && ['failed', 'cancelled', 'rejected'].includes(run.status) ? '可重新发起研究，原任务记录保留；历史资料需重新选择并授权。' : '系统会重新核查公开市场证据，保留这版报告供对照。无需再填写研究问题；使用历史资料需重新选择并授权。'}</p></div>
        <div className="owner-command-actions">{canRecheck ? <button className="button primary" disabled={commandPending} onClick={() => onCommand('recheck', '用户请求更新公开市场证据并复查当前研究；不继承历史资料授权。')}><RefreshCw size={14} />{commandBusy ? '正在核对操作…' : commandPending ? '请先核对原操作' : '更新公开证据'}</button> : canRetry ? <button className="button primary" disabled={commandPending} onClick={() => onCommand('retry', '用户请求重试未完成的研究；不继承历史资料授权。')}><RefreshCw size={14} />{commandBusy ? '正在核对操作…' : commandPending ? '请先核对原操作' : '重新尝试研究'}</button> : <button className="button secondary" disabled={commandPending} onClick={() => onCommand('cancel', '用户在研究报告页停止本次任务。')}><Ban size={14} />{commandBusy ? '正在核对操作…' : commandPending ? '请先核对原操作' : '停止本次研究'}</button>}</div>
        {commandError && <p className="command-error" role="alert">操作未完成：{commandError}</p>}
      </section>}
      {detailView === 'result' && hasSavedReport && revisionFeedback}
      {hasSavedReport && followup}
    </div>
  </EvidenceReferenceContext.Provider>
}

function ResearchCostRecordSection({ detail, expanded, visible }: { detail: ResearchRunDetailView; expanded: boolean; visible: boolean }) {
  const [open, setOpen] = useState(expanded)
  const [requested, setRequested] = useState(expanded && visible)
  useEffect(() => { setOpen(expanded) }, [expanded])
  useEffect(() => { if (open && visible) setRequested(true) }, [open, visible])
  return <details className="research-record-section" aria-label="运行步骤与用量费用" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>运行步骤与用量费用</summary>
    <ResearchProcessSteps steps={detail.process_steps} />
    {/* Keep an opened instance: closing must retain unknown POST keys and selected history. */}
    {requested && <ResearchCostPanel detail={detail} visible={open && visible} />}
  </details>
}

function ResearchRecordSection({ title, expanded, children }: { title: string; expanded: boolean; children: ReactNode }) {
  return <details className="research-record-section" aria-label={title} open={expanded}>
    <summary>{title}</summary>{children}
  </details>
}

function OriginalRecord({ children }: { children: ReactNode }) {
  return <details className="research-original-record"><summary>查看原始记录</summary><div>{children}</div></details>
}

function RolePacketMaterials({ packet, evidence, runId }: {
  packet: ResearchRunDetailView['role_packets'][number]; evidence: ResearchRunDetailView['evidence']; runId: string
}) {
  const materials = evidence.filter(item => packet.evidence_refs.includes(item.evidence_id))
  const accepted = materials.filter(item => item.quality === 'accepted')
  const unlocated = packet.evidence_refs.filter(ref => !materials.some(item => item.evidence_id === ref)).length
  return <>
    <p>{accepted.length} 条已采纳材料{materials.length > accepted.length ? `，${materials.length - accepted.length} 条待核对或未采纳` : ''}{unlocated > 0 ? `；另有 ${unlocated} 条引用未定位` : ''}。本角色状态见上方，完整表述保留在原始记录。</p>
    {materials.length > 0 && <ul>{materials.slice(0, 3).map(item => <li key={item.evidence_id}><b>{sourceLabel(item.source_id)} · {technicalName(item.quality)}</b><p>{evidenceSummary(item.excerpt, item.source_id)}</p><EvidenceRefLinks refs={[item.evidence_id]} runId={runId} /><OriginalRecord>来源标识：{item.source_id}</OriginalRecord></li>)}</ul>}
    {materials.length > 3 && <p>另外 {materials.length - 3} 条材料见完整证据记录。</p>}
    {packet.gap_ids.length > 0 && <p>待核对：{packet.gap_ids.map(id => requirementNames[id] ?? id).join('、')}</p>}
  </>
}

const graphNodeTone: Record<string, string> = {
  confirmed: 'confirmed', supporting: 'supporting', opposing: 'opposing', derived: 'derived',
  unresolved: 'unresolved', blocked: 'blocked', scheduled: 'scheduled',
}

type GraphCanvasProps = { graph: NonNullable<ResearchRunDetailView['decision_graph']>; nodesEdges: Edge[]; expanded?: boolean; onNodeSelect?: (node: Node) => void; reviewPresentations?: GraphReviewPresentations }

function DecisionGraphCanvas(props: GraphCanvasProps) {
  const [Canvas, setCanvas] = useState<ComponentType<GraphCanvasProps> | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setFailed(false)
    import('./DecisionGraphCanvas').then((module) => {
      if (active) setCanvas(() => module.default)
    }).catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [attempt])
  if (Canvas) return <Canvas {...props} />
  return <div className="research-empty" role="status">{failed ? <><span>图谱加载失败，研究结论和证据仍可查看。</span><button type="button" className="button secondary" onClick={() => setAttempt((value) => value + 1)}>重新加载图谱</button></> : '正在加载分析链路…'}</div>
}

type DecisionGraphPanelProps = {
  graph: ResearchRunDetailView['decision_graph']
  detail: ResearchRunDetailView
  routeOpen?: boolean
  routeNodeId?: string
  onOpenRoute?: (view: GraphView) => void
  onOpenNodeRoute?: (nodeId: string, view: GraphView) => void
  onCloseNodeRoute?: () => void
  onCloseRoute?: (view: GraphView) => void
  onOpenEvidence?: (evidenceId: string) => void
}

function DecisionGraphPanel({ graph, detail, routeOpen = false, routeNodeId, onOpenRoute, onOpenNodeRoute, onCloseNodeRoute, onCloseRoute, onOpenEvidence }: DecisionGraphPanelProps) {
  const reviewPresentations = useMemo(() => graphReviewPresentations(graph?.nodes ?? [], detail.reviews), [graph, detail.reviews])
  const [expanded, setExpanded] = useState(false)
  const fullscreenRef = useRef<HTMLDivElement>(null)
  const exitRef = useRef<HTMLButtonElement>(null)
  const expandRef = useRef<HTMLButtonElement>(null)
  const currentRoute = readResearchRoute()
  const coveredByDrawer = currentRoute.name === 'evidence' || currentRoute.name === 'event'
  useModalScrollLock(expanded && Boolean(graph))
  useModalFocusContainment(fullscreenRef, { active: expanded && Boolean(graph) && !coveredByDrawer, scopeKey: detail.run.run_id, initialFocusRef: exitRef, returnFocusRef: expandRef })
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const routeView = graphViewFromRoute(currentRoute, detail.run.run_id)
  const [graphQuery, setGraphQuery] = useState(routeView.graphQuery ?? '')
  const [graphFilter, setGraphFilter] = useState(routeView.graphFilter ?? 'all')
  const [graphListView, setGraphListView] = useState(routeView.graphView ? routeView.graphView === 'list' : window.matchMedia?.('(max-width: 600px)').matches ?? false)
  const graphView: GraphView = { graphQuery, graphFilter, graphView: graphListView ? 'list' : 'map' }
  useEffect(() => {
    setGraphQuery(routeView.graphQuery ?? '')
    setGraphFilter(routeView.graphFilter ?? 'all')
    setGraphListView(routeView.graphView ? routeView.graphView === 'list' : window.matchMedia?.('(max-width: 600px)').matches ?? false)
  }, [detail.run.run_id, routeView.graphQuery, routeView.graphFilter, routeView.graphView])
  const changeView = (patch: GraphView) => {
    const next = { ...graphView, ...patch }
    setGraphQuery(next.graphQuery ?? '')
    setGraphFilter(next.graphFilter ?? 'all')
    setGraphListView(next.graphView === 'list')
    const current = readResearchRoute()
    if (current.runId !== detail.run.run_id || !['report', 'report-incomplete', 'technical', 'graph', 'graph-node'].includes(current.name)) return
    const back = current.name === 'graph-node' ? researchRouteHref({ ...graphBaseRoute(window.location.hash, detail.run.run_id), ...next }) : current.back
    window.history.replaceState(null, '', researchRouteHref({ ...current, ...next, back }))
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
  const closeExpanded = () => { setExpanded(false); onCloseRoute?.(graphView) }
  useEffect(() => {
    if (routeOpen) setExpanded(true)
  }, [routeOpen])
  useEffect(() => {
    setSelectedNodeId(routeNodeId && graph?.nodes.some((node) => node.node_id === routeNodeId) ? routeNodeId : null)
  }, [graph, routeNodeId])
  useEffect(() => {
    if (!expanded || coveredByDrawer) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeExpanded()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [expanded, coveredByDrawer, onCloseRoute, graphQuery, graphFilter, graphListView])
  if (!graph) return <section className="research-section decision-graph-panel"><SectionHeading icon={Network} title="分析链路" meta="不可用" /><ResearchEmpty text="当前 Run 没有可视化链路投影" /></section>
  const closeNodeInspector = () => { setSelectedNodeId(null); onCloseNodeRoute?.() }
  const edges: Edge[] = graph.edges.filter((edge) => graph.nodes.some((node) => node.node_id === edge.from_node) && graph.nodes.some((node) => node.node_id === edge.to_node)).map((edge) => ({
    id: edge.edge_id, source: edge.from_node, target: edge.to_node, label: edge.relation === 'reviews' && edge.label === '复核综合判断' ? '审查综合判断' : (({ fact: '事实支持', inference: '推断关系', scenario: '情景关系' } as Record<string, string>)[edge.label] ?? edge.label), animated: edge.relation === 'gates',
    type: 'smoothstep',
  }))
  const acceptedEvidence = detail.evidence.filter((item) => item.quality === 'accepted')
  const sourceCount = new Set(acceptedEvidence.map((item) => item.source_id)).size
  const meta = `${graph.nodes.length} 个节点`
  const note = `${reviewDisplayStatus(detail.process_steps, detail.reviews.length, detail.run.status)}。图中显示可核对的结构化证据、主链/反链、覆盖评估、审查记录和执行质量门；点击节点查看详情，再打开关联证据核对原文。全图用于看关系，可筛选分支或切换列表逐项阅读。`
  const nodeTypes = [...new Set(graph.nodes.map((node) => node.node_type))].sort()
  const visibleNodes = graph.nodes.filter((node) => graphNodeMatches(node, graphQuery, graphFilter))
  const visibleIds = new Set(visibleNodes.map((node) => node.node_id))
  const visibleEdges = edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
  const visibleGraphEdges = graph.edges.filter((edge) => visibleIds.has(edge.from_node) && visibleIds.has(edge.to_node))
  const selectNode = (nodeId: string) => { setSelectedNodeId(nodeId); onOpenNodeRoute?.(nodeId, graphView) }
  const graphToolbar = <div className="decision-graph-toolbar"><label className="decision-graph-search"><Search size={13} /><input value={graphQuery} onChange={(event) => changeView({ graphQuery: event.target.value })} placeholder="搜索节点、结论或状态…" aria-label="搜索分析链路节点" /></label><label className="decision-graph-filter"><Filter size={13} /><select value={graphFilter} onChange={(event) => changeView({ graphFilter: event.target.value })} aria-label="筛选图谱节点类型"><option value="all">全部节点</option>{nodeTypes.map((type) => <option key={type} value={type}>{graphNodeKinds[type] ?? type}</option>)}</select></label><button type="button" className="decision-graph-view-toggle" onClick={() => changeView({ graphView: graphListView ? 'map' : 'list' })}>{graphListView ? '返回图谱' : '列表查看'}</button>{(graphQuery || graphFilter !== 'all') && <button type="button" className="decision-graph-view-toggle" onClick={() => changeView({ graphQuery: '', graphFilter: 'all' })}>清除筛选</button>}<span>{visibleNodes.length} / {graph.nodes.length} 节点</span></div>
  const graphBody = graphListView || visibleNodes.length === 0 ? <GraphNodeList nodes={visibleNodes} onSelect={selectNode} reviewPresentations={reviewPresentations} /> : <div className="decision-graph-canvas-column decision-graph-canvas"><DecisionGraphCanvas graph={{ ...graph, nodes: visibleNodes, edges: visibleGraphEdges }} nodesEdges={visibleEdges} onNodeSelect={(node) => selectNode(node.id)} reviewPresentations={reviewPresentations} /></div>
  return <>
    <section className="research-section decision-graph-panel" aria-label="分析链路图" hidden={expanded}>
      <header className="research-section-heading">
        <div><Network size={15} /><h3>分析链路</h3></div>
        <div className="decision-graph-heading-actions"><span>{meta}</span><button ref={expandRef} type="button" className="decision-graph-expand-button" onClick={() => { setExpanded(true); onOpenRoute?.(graphView) }} aria-label="全屏查看分析链路" title="全屏查看分析链路"><Maximize2 size={14} />全屏查看</button></div>
      </header>
      <p className="decision-graph-note">{note}</p>
      <div className="decision-graph-audit-strip" aria-label="研究审计摘要"><span><b>{detail.rounds.length}</b> 轮研究</span><span><b>{detail.total_tool_calls}</b> 次取证执行</span><span><b>{acceptedEvidence.length}</b> 条采纳证据</span><span><b>{sourceCount}</b> 个来源</span><span><b>{detail.reviews.length}</b> 份{executionReviewRecordName(detail.process_steps)}</span></div>
      {graphToolbar}
      <div className={`decision-graph-layout ${selectedNodeId ? 'has-inspector' : ''}`}>
        {!expanded && graphBody}
        {!expanded && selectedNodeId && <GraphNodeInspector node={graph.nodes.find((item) => item.node_id === selectedNodeId) ?? null} runId={detail.run.run_id} onClose={closeNodeInspector} onOpenEvidence={onOpenEvidence} reviewPresentations={reviewPresentations} />}
      </div>
    </section>
    {expanded && <div ref={fullscreenRef} tabIndex={-1} className="decision-graph-fullscreen" style={coveredByDrawer ? { visibility: 'hidden', pointerEvents: 'none' } : undefined} inert={coveredByDrawer || undefined} aria-hidden={coveredByDrawer || undefined} role="dialog" aria-modal={coveredByDrawer ? undefined : true} aria-label="全屏分析链路">
      <div className="decision-graph-fullscreen-inner">
        <header className="decision-graph-fullscreen-header"><div><Network size={18} /><div><strong>分析链路</strong><span>{meta}</span></div></div><button ref={exitRef} type="button" className="decision-graph-close-button" onClick={closeExpanded} aria-label="退出全屏" title="退出全屏"><X size={18} />退出全屏</button></header>
        <p className="decision-graph-fullscreen-note">{note} 可使用滚轮缩放、拖拽画布移动，按 Esc 退出全屏。</p>
        <div className="decision-graph-audit-strip" aria-label="研究审计摘要"><span><b>{detail.rounds.length}</b> 轮研究</span><span><b>{detail.total_tool_calls}</b> 次取证执行</span><span><b>{acceptedEvidence.length}</b> 条采纳证据</span><span><b>{sourceCount}</b> 个来源</span><span><b>{detail.reviews.length}</b> 份{executionReviewRecordName(detail.process_steps)}</span></div>
        {graphToolbar}
        <div className="decision-graph-expanded-canvas">{graphListView || visibleNodes.length === 0 ? <GraphNodeList nodes={visibleNodes} onSelect={selectNode} reviewPresentations={reviewPresentations} /> : <DecisionGraphCanvas graph={{ ...graph, nodes: visibleNodes, edges: visibleGraphEdges }} nodesEdges={visibleEdges} expanded onNodeSelect={(node) => selectNode(node.id)} reviewPresentations={reviewPresentations} />}{selectedNodeId && <GraphNodeInspector node={graph.nodes.find((item) => item.node_id === selectedNodeId) ?? null} runId={detail.run.run_id} fullscreen onClose={closeNodeInspector} onOpenEvidence={onOpenEvidence} reviewPresentations={reviewPresentations} />}</div>
      </div>
    </div>}
  </>
}

function GraphNodeList({ nodes, onSelect, reviewPresentations }: { nodes: NonNullable<ResearchRunDetailView['decision_graph']>['nodes']; onSelect: (nodeId: string) => void; reviewPresentations: GraphReviewPresentations }) {
  return <div className="graph-node-list" aria-label="分析链路节点列表">{nodes.length === 0 ? <div className="research-empty"><SearchCheck size={15} />没有符合条件的节点</div> : nodes.map((node) => <button type="button" key={node.node_id} onClick={() => onSelect(node.node_id)}><span className={`graph-node-list-mark ${graphNodeTone[node.status] ?? 'derived'}`} /><span><strong>{graphNodeTitle(node)}</strong><small>{graphNodeKinds[node.node_type] ?? node.node_type} · {graphNodeStatuses[node.status] ?? node.status}</small><p>{graphNodeSummary(node, reviewPresentations)}</p></span><ArrowRight size={14} /></button>)}</div>
}

function ResearchTrustPanel({ detail, focusSources = false }: { detail: ResearchRunDetailView; focusSources?: boolean }) {
  const sourcesRef = useRef<HTMLDetailsElement>(null)
  const [toolAudit, setToolAudit] = useState({ runId: detail.run.run_id, open: false })
  const toolAuditOpen = toolAudit.runId === detail.run.run_id && toolAudit.open
  useLayoutEffect(() => {
    if (!focusSources || !sourcesRef.current) return
    sourcesRef.current.open = true
    sourcesRef.current.scrollIntoView?.({ block: 'start' })
    sourcesRef.current.querySelector<HTMLElement>('summary')?.focus({ preventScroll: true })
  }, [focusSources, detail.run.run_id])
  const calls = new Map(detail.rounds.flatMap(round => round.tool_invocations).map(call => [call.tool_call_id, call]))
  const checkpoints = workflowCheckpointCallIds(detail)
  const failedCalls = [...calls.values()].filter(call => call.status === 'failed')
  const closingReserveCalls = failedCalls.filter(call => isAcquisitionClosingReserve(call.error)
    && (call.error_code == null || call.error_code === call.error?.error_code))
  const accepted = detail.evidence.filter((item) => item.quality === 'accepted')
  const fresh = accepted.filter((item) => item.freshness_status === 'fresh' && (item.kind === 'market' || item.published_at))
  const sources = new Map<string, ResearchRunDetailView['evidence']>()
  const requirements = new Map<string, ResearchRunDetailView['evidence']>()
  accepted.forEach((item) => {
    const sourceItems = sources.get(item.source_id) ?? []
    sourceItems.push(item)
    sources.set(item.source_id, sourceItems)
    const requirementItems = requirements.get(item.requirement_id) ?? []
    requirementItems.push(item)
    requirements.set(item.requirement_id, requirementItems)
  })
  const sessionId = detail.trace.find((item) => item.research_session_id)?.research_session_id ?? null
  const latestObserved = accepted.reduce<string | null>((latest, item) => {
    if (!latest || item.observed_at > latest) return item.observed_at
    return latest
  }, null)
  const sourceRows = [...sources.entries()].sort(([left], [right]) => left.localeCompare(right))
  const requirementRows = [...requirements.entries()].sort(([left], [right]) => left.localeCompare(right))
  return <section className="research-section research-trust-panel" aria-label="可信度与来源">
    <SectionHeading icon={ShieldAlert} title="可信度与来源" meta={accepted.length === 0 ? '尚未采纳证据' : '可查看采纳证据的来源记录'} />
    <div className="research-trust-grid">
      <div><span>采纳证据</span><strong>{accepted.length} 条 · {fresh.length} 条符合当时时效</strong><small>只统计通过校验的证据</small></div>
      <div><span>记录来源</span><strong>{sources.size} 个</strong><small>按来源标识去重，尚未完全识别同源转载</small></div>
      <div><span>研究与审查</span><strong>{detail.rounds.length} 轮 · {detail.reviews.length} 份{executionReviewRecordName(detail.process_steps)}</strong><small>{reviewDisplayStatus(detail.process_steps, detail.reviews.length, detail.run.status)}</small><small>{detail.total_tool_calls} 次取证执行（网关记录）</small></div>
      <div><span>最近取证记录</span><strong>{latestObserved ? formatDate(latestObserved) : '暂无'}</strong><small>不等于内容发布时间</small></div>
    </div>
    <p className="research-trust-note">这里只统计已采纳的证据。不同来源标识仍可能引用同一原始材料，数量不等同于独立证据支持。证据覆盖通过不代表预测已经校准。</p>
    {failedCalls.length > 0 && <p className="research-notice warning" role="status">研究过程保留 {failedCalls.length} 次调用失败。{checkpoints.size > 0 ? `其中包括 ${checkpoints.size} 次审查流程检查点（后续审查状态见分析过程）；其余 ${failedCalls.length - checkpoints.size} 次为真实或未分类失败。` : ''}{closingReserveCalls.length > 0 ? `其中 ${closingReserveCalls.length} 次是取证收尾预留拒绝，仍保留在失败账中；整理与独立复核结果待核对。` : ''}{failedCalls.some(call => call.error_code === 'research_pit_violation') ? '其中有请求未通过时间校验。' : ''}已采纳证据仍保留，可在技术详情查看调用时间与原因。</p>}
    <details key={`tool-audit-${detail.run.run_id}`} className="research-trust-sources" aria-label="工具调用审计" open={toolAuditOpen} onToggle={event => setToolAudit({ runId: detail.run.run_id, open: event.currentTarget.open })}><summary>核对工具尝试与失败记录</summary>{toolAuditOpen && <ToolAttemptsPanel runId={detail.run.run_id} active={['queued', 'researching'].includes(detail.run.status)} />}</details>
    <details ref={sourcesRef} className="research-trust-sources" aria-label="采纳来源与证据"><summary>查看来源与证据清单 <span>{accepted.length} 条 · {sources.size} 个来源</span></summary>
    {requirementRows.length === 0 ? <ResearchEmpty text="当前任务没有可展示的采纳证据" /> : <div className="research-source-matrix" aria-label="来源矩阵">
      {requirementRows.map(([requirement, items]) => <article key={requirement}>
        <header><strong>{gapLabel[requirement] ?? requirement}</strong><span>{items.length} 条 · {new Set(items.map((item) => item.source_id)).size} 个来源</span></header>
        <div>{items.map((item) => <a key={item.evidence_id} href={evidenceRouteHref(detail.run.run_id, item.evidence_id)}><strong>{sourceLabel(item.source_id)}</strong><span>{sourceAuthorityName(item.authority)} · {evidenceTimeLabel(item)} · {item.published_at ? '发布于' : item.kind === 'market' ? '观测于' : '读取于'} {formatDate(item.published_at ?? item.observed_at)}</span></a>)}</div>
      </article>)}
    </div>}
    {detail.evidence.some(item => item.quality !== 'accepted') && <details><summary>未计入当前有效证据的材料（{detail.evidence.filter(item => item.quality !== 'accepted').length} 条）</summary><p>保留供背景核对，不把旧消息或时间未知材料当作最新证据。</p><ul>{detail.evidence.filter(item => item.quality !== 'accepted').map(item => <li key={item.evidence_id}><a href={evidenceRouteHref(detail.run.run_id, item.evidence_id)}>{sourceLabel(item.source_id)}</a> · {evidenceTimeLabel(item)}{item.published_at && ` · 发布于 ${formatDate(item.published_at)}`}</li>)}</ul></details>}
    {sourceRows.length > 0 && <div className="research-source-identity"><strong>来源身份</strong>{sourceRows.map(([source, items]) => <span key={source}>{sourceLabel(source)} · {sourceAuthorityName(items[0]?.authority ?? '')}</span>)}</div>}
    {detail.evidence.length > 0 && <details className="research-original-record" aria-label="来源标识核对"><summary>核对来源标识</summary>{detail.evidence.map(item => <p key={item.evidence_id} style={{ overflowWrap: 'anywhere' }}>证据：{item.evidence_id} · 来源标识：{item.source_id}</p>)}</details>}
    <details className="research-original-record" aria-label="执行身份审计"><summary>查看执行身份与技术记录</summary><div className="research-source-identity"><strong>执行记录</strong><span>运行时：{detail.run.runtime_id} · {detail.run.profile_ref ?? '角色版本未记录'}</span><span>会话：{sessionId ?? '未提供'}</span></div></details>
    </details>
  </section>
}


function GraphNodeInspector({ node, runId, fullscreen = false, onClose, onOpenEvidence, reviewPresentations }: { node: NonNullable<ResearchRunDetailView['decision_graph']>['nodes'][number] | null; runId: string; fullscreen?: boolean; onClose: () => void; onOpenEvidence?: (evidenceId: string) => void; reviewPresentations: GraphReviewPresentations }) {
  const evidenceReferences = useContext(EvidenceReferenceContext)
  if (!node) return null
  return <aside className={`graph-node-inspector ${fullscreen ? 'fullscreen' : ''}`} aria-label="图谱节点检查器">
    <header><div><span>节点详情</span><strong>{graphNodeTitle(node)}</strong></div><button type="button" className="icon-button small" onClick={onClose} aria-label="关闭节点检查器"><X size={15} /></button></header>
    <div className="graph-node-inspector-meta"><span>{graphNodeKinds[node.node_type] ?? node.node_type}</span><span>{graphNodeStatuses[node.status] ?? node.status}</span></div>
    <p>{graphNodeSummary(node, reviewPresentations)}</p>
    <details><summary>原始节点记录</summary><p>{node.label}</p><p>{node.summary}</p><small>{node.node_type} · {node.status} · {node.node_id}</small>{node.node_type === 'review' && <pre>{JSON.stringify(node, null, 2)}</pre>}</details>
    {graphReviewPresentation(node, reviewPresentations) && <details><summary>完整复核原始记录</summary><pre>{JSON.stringify(graphReviewPresentation(node, reviewPresentations)!.originalReview, null, 2)}</pre></details>}
    <section><h4>关联证据</h4>{node.evidence_refs.length === 0 ? <span className="empty-line">没有直接关联证据</span> : <div className="graph-node-evidence-list">{node.evidence_refs.map((ref) => <a key={ref} title={ref} href={evidenceRouteHref(runId, ref)} onClick={(event) => { if (onOpenEvidence) { event.preventDefault(); onOpenEvidence(ref) } }}>{evidenceReferenceLabel(evidenceReferences, runId, ref)}<ArrowRight size={13} /></a>)}</div>}</section>
  </aside>
}


function RunComparisonPanel({ comparison, run, currentStateUnconfirmed }: { comparison?: ResearchRunComparisonView; run: ResearchRunView; currentStateUnconfirmed: boolean }) {
  const previousRunId = comparison?.previous_run_id ?? run.parent_run_id
  const unavailable = currentStateUnconfirmed
    ? '本次任务状态尚未确认，暂不能比较结论。'
    : !TERMINAL.has(run.status)
      ? '本次研究尚未完成，暂不能比较结论。'
      : !run.artifact_id
        ? '本次任务已结束，但未保存报告，暂不能比较结论。'
        : !comparison || comparison.run_id !== run.run_id || comparison.current_status !== run.status
          ? '正在读取本次已保存报告的版本比较，暂不能比较结论。'
          : null
  return <section className="research-section run-comparison-panel" aria-label="研究版本比较"><SectionHeading icon={RefreshCw} title="与上一次研究比较" meta={previousRunId ? shortId(previousRunId) : '无上一版本'} />
    {unavailable
      ? <ResearchEmpty text={unavailable} />
      : !comparison?.previous_run_id
        ? <ResearchEmpty text="这是该事件的第一版研究，后续复查会在这里显示变化。" />
        : <div className="run-comparison-grid"><div><span>方向</span><strong>{directionLabel(comparison.previous_direction)} → {directionLabel(comparison.current_direction)}</strong></div><div><span>证据数量</span><strong>{comparison.previous_evidence_count ?? 0} → {comparison.current_evidence_count}</strong></div><div><span>图状态</span><strong>{technicalName(comparison.previous_graph_status)} → {technicalName(comparison.current_graph_status)}</strong></div><div><span>变化字段</span><strong>{comparison.changed_fields.length ? comparison.changed_fields.map(field => ({ direction: '方向', evidence_count: '证据数量', graph_status: '图状态', causal_case: '论据', horizons: '分周期判断', coverage: '覆盖' } as Record<string, string>)[field] ?? field).join(' · ') : '无变化'}</strong></div></div>}
    {previousRunId && <a className="product-link" href={researchRouteHref({ name: 'report', runId: previousRunId, back: window.location.hash })}>查看上次研究记录<ArrowRight size={14} /></a>}
  </section>
}


const recommendationLabel = {
  do_not_trade: '不应据此交易',
  wait_for_confirmation: '暂不交易，等待确认',
  manual_review_required: '方向情景待人工复核',
  unavailable: '无法形成可靠判断',
} as const
const reliabilityLabel = { unavailable: '不可用', low: '低', limited: '有限', reviewable: '可供人工复核' } as const
const dataStatusLabel = { insufficient: '不足', partial: '部分充分', sufficient: '检查通过' } as const
const artifactStatusLabel = { pending: '研究结果待生成', saved: '研究结果已生成', limited: '研究结果受限', rejected: '研究结果未通过' } as const
const horizonActionLabel = { long: '做多情景', short: '做空情景', neutral: '中性等待', no_trade: '不交易' } as const
const gapLabel: Record<string, string> = {
  event_identity: '事件身份与原始公告', policy_or_data_delta: '政策或数据相对上次的变化', expectation_pricing: '市场预期与定价',
  macro_transmission: '实时宏观传导数据',
  market_transmission: '实时宏观传导数据',
  cross_asset_confirmation: '实时跨资产确认',
  crypto_spot_confirmation: '双交易所现货确认',
  derivatives_crowding: '衍生品与拥挤度确认',
  market_regime_context: '日线趋势背景',
  market_narrative: '最新公开信息未核验',
  usd_reserve_balances: requirementNames.usd_reserve_balances,
  real_yield_10y: requirementNames.real_yield_10y,
  inflation_compensation_10y: requirementNames.inflation_compensation_10y,
}
const processStatusLabel = { waiting: '等待', running: '进行中', completed: '完成', limited: '有限制', failed: '失败' } as const
const executionStatusLabel = { blocked: '执行阻断', manual_review: '待人工复核', authorized: '已授权' } as const
const rolePacketLabel: Record<string, string> = { manager: '研究经理', market_structure: '市场结构', derivatives_crowding: '衍生品与拥挤度', macro_calendar: '宏观与事件日历', cross_asset_risk: '跨资产与风险', narrative_source_critic: '叙事与来源批评', counter_thesis: '反方链', data_quality: '数据质量', reviewer: '独立复核', synthesizer: '综合器' }
const rolePacketStatus = { sufficient: '职责充分', partial: '部分充分', blocked: '职责阻断' } as const

const waitingResearchCopy: Partial<Record<ResearchRunView['status'], { title: string; detail: string }>> = {
  admitted: { title: '研究请求已接收，等待开始', detail: '请求已接收，等待研究进程开始处理。实际取证与复核记录会在开始后显示。' },
  queued: { title: '研究已排队，等待开始', detail: '任务已进入队列，等待研究进程领取。实际取证与复核记录会在开始后显示。' },
  retry_wait: { title: '等待重试，原记录仍保留', detail: '当前任务正在等待重试，尚不能据此认定已恢复取证。已保留的材料与调用记录仍可查看。' },
}

function unfinishedResearchCopy(status: ResearchRunView['status'], failure: ResearchRunView['failure'] | null, stopReason: ResearchRunView['stop_reason'] | undefined) {
  if (status === 'cancelled') {
    const ownerStopped = failure?.error_code === 'cancelled_by_owner'
    return { title: '本次研究已取消', detail: `${ownerStopped ? '你已停止本次研究' : '本次研究已取消'}。未生成本次报告，本任务不再发布新报告；此前材料与调用记录保留。` }
  }
  if (status !== 'failed' && status !== 'rejected') return null
  const code = failure?.error_code ?? stopReason?.code
  const reason = failure ? failureExplanation(failure.error_code, failure.cause_code, failure) : null
  const knownReason = code && Object.prototype.hasOwnProperty.call(technicalLabels, code) ? technicalLabels[code] : null
  return {
    title: status === 'rejected' ? '本次研究未通过检查' : '本次研究未完成',
    detail: `未生成本次报告。${reason ?? (knownReason ? `原因：${knownReason}。` : '具体原因尚无可读说明，可查看任务记录核对。')}`,
  }
}

function ResearchActionSummary({ currentStateUnconfirmed = false, preparationWaiting = false, runId, summary, failure, nativeFailure, runStatus, stopReason, hasSavedReport = false, onOpenProblemDetails, horizons = [], gaps = [], causalCase, reviews, reviewStatus, reviewRecordName }: { currentStateUnconfirmed?: boolean; preparationWaiting?: boolean; runId: string; summary: ResearchRunDetailView['action_summary']; failure: ResearchRunView['failure'] | null; nativeFailure: ResearchRunDetailView['native_execution_failure']; runStatus: ResearchRunView['status']; stopReason?: ResearchRunView['stop_reason']; hasSavedReport?: boolean; onOpenProblemDetails?: () => void; horizons?: ResearchRunDetailView['horizons']; gaps?: NonNullable<ResearchRunView['coverage']>['gaps']; causalCase: ResearchRunDetailView['causal_case']; reviews: ResearchRunDetailView['reviews']; reviewStatus: string; reviewRecordName: string }) {
  const browserNow = useResearchWindowClock(horizons.map(item => item.expires_at))
  const terminalFailure = ['failed', 'rejected', 'cancelled'].includes(runStatus)
  const taskCopy = !currentStateUnconfirmed && !preparationWaiting && !hasSavedReport ? unfinishedResearchCopy(runStatus, failure, stopReason) : null
  const running = ['admitted', 'queued', 'researching', 'retry_wait'].includes(runStatus)
  const confirmedNativeFailure = !currentStateUnconfirmed && !preparationWaiting && !running && nativeFailure?.run_id === runId ? nativeFailure : null
  const incompleteSynthesis = !currentStateUnconfirmed && !preparationWaiting && !running && stopReason?.code === 'synthesis_incomplete' && !causalCase
  const waitingCopy = waitingResearchCopy[runStatus]
  const outlook = summary.artifact_status !== 'rejected' ? summary.research_outlook : null
  const showSynthesis = !currentStateUnconfirmed && !preparationWaiting && !confirmedNativeFailure && !running && !terminalFailure && summary.artifact_status !== 'rejected' && causalCase
  const collectedEvidenceSummary = summary.accepted_evidence_count > 0
    ? `已保留 ${summary.accepted_evidence_count} 条可用证据；综合判断摘要尚未提供引用。`
    : '尚未保留可用证据，也未形成综合判断摘要引用。'
  const limitations = [...new Set([...summary.blocking_gap_ids, ...gaps.filter(gap => gap.importance === 'soft').map(gap => gap.requirement_id)])]
  const pastReview = summary.next_review_at && Date.parse(summary.next_review_at) < Date.now()
  return <section className={`research-action-summary ${running ? 'researching' : summary.recommendation}`} aria-label="研究判断摘要">
    <div className="research-action-heading"><div><span>{taskCopy || confirmedNativeFailure || incompleteSynthesis ? '任务状态与下一步' : '研究结论与下一步'}</span><h2>{currentStateUnconfirmed ? '最新任务状态暂时无法确认' : preparationWaiting ? '等待恢复核对' : running ? (waitingCopy?.title ?? '正在取证与复核，结论生成中') : confirmedNativeFailure ? '本次 AI 研究失败' : taskCopy ? taskCopy.title : incompleteSynthesis ? '本次 AI 综合判断未完成' : showSynthesis ? '本次 AI 综合判断' : outlook ? '已有日线参考，综合判断暂不充分' : '当前尚不能形成可靠判断'}</h2></div><strong>未授权交易</strong></div>
    {hasSavedReport && runStatus === 'research_only' && !currentStateUnconfirmed && <section className="research-notice warning" aria-label="报告适用边界">
      <strong>{showSynthesis ? '本次综合分析已生成' : '已保留取证记录，综合判断未完成'}</strong>
      <p>本次研究记录已生成。完整报告是否已保存、是否已独立确认，以及当前能否读取，请以下方“这份研究能支持什么”为准。</p>
      <p>{showSynthesis ? '下方偏多或偏空是待验证的研究假说，不能据此认为途中急跌急涨风险或杠杆风险已经量化。请同时阅读反方意见，以及“这份研究能支持什么”中的关键缺口。' : '当前报告保留已采纳材料和未完成记录，尚无可核验的综合方向判断。请先核对执行失败原因与关键缺口。'}</p>
    </section>}
    {confirmedNativeFailure && <section className="research-notice warning research-native-failure" aria-label="AI 研究执行失败" role="status">
      <p>{failureExplanation(confirmedNativeFailure.error_code, null) ?? (confirmedNativeFailure.error_code === 'dsh_model_binding_unavailable' ? 'AI 研究所需的模型调用未能建立，研究执行已结束。' : confirmedNativeFailure.error_code === 'dsh_provider_emission_transport_timeout' ? '模型输出回传确认超时，本次研究已停止。已保留证据和失败记录。' : confirmedNativeFailure.error_code === 'dsh_provider_finish_transport_timeout' ? '最终结果回传确认超时，已保留证据和失败记录。' : 'AI 研究执行中断，后台已保留失败记录。')}</p>
      <p>发生时间：<time dateTime={confirmedNativeFailure.terminal_at}>{formatDate(confirmedNativeFailure.terminal_at)}</time></p>
      <p>{!hasSavedReport ? '未生成本次报告。' : incompleteSynthesis ? '本次综合 AI 结果未完成。' : '请结合已保存的结论与执行记录核对结果。'}已采纳证据{outlook ? '和独立日线参考' : ''}仍保留。可使用下方研究操作重新发起研究，原任务{hasSavedReport ? '与报告' : '的执行记录'}保留供对照。</p>
      <details><summary>查看 AI 执行记录</summary><dl><div><dt>原任务</dt><dd>{confirmedNativeFailure.run_id}</dd></div><div><dt>执行会话</dt><dd>{confirmedNativeFailure.dsh_session_id}</dd></div><div><dt>执行代次</dt><dd>{confirmedNativeFailure.generation}</dd></div><div><dt>整体终态错误码</dt><dd>{confirmedNativeFailure.error_code}</dd></div></dl></details>
    </section>}
    {!confirmedNativeFailure && incompleteSynthesis && <section className="research-notice warning" aria-label="AI 综合判断未完成"><p>这版报告没有完成综合分析，不能作为完整的 AI 研究结论。已采纳证据{outlook ? '和独立日线参考' : ''}仍保留。</p><button type="button" className="button secondary" onClick={onOpenProblemDetails}>查看结束记录</button></section>}
    {taskCopy && <div className="research-task-outcome">{!confirmedNativeFailure && <p>{taskCopy.detail}</p>}<button type="button" className="button secondary" onClick={onOpenProblemDetails}>查看问题详情</button></div>}
    {running && <p>{currentStateUnconfirmed ? '下方保留最近一次读取的任务与材料，不代表当前执行状态；请重新读取后核对。' : preparationWaiting ? preparationWaitingText : (waitingCopy?.detail ?? '系统正在获取公开资料和行情，并保留实际取证记录。完成后这里会展示综合判断与不同窗口的条件。')}</p>}
    {showSynthesis && <section className="research-synthesis-summary" aria-label="本次综合研究">
      <p className="research-thesis-lead">{horizons.length ? `本次报告记录：${horizons.map(h => `${horizonName(h.horizon)}：${directionName(h.action)}（${researchWindowExpiry(h.expires_at, browserNow).label}）`).join('；')}。各窗口的确认条件与失效条件见下方。` : '这版报告保留了分析解释，但尚未形成可用的分周期判断。'}</p>
      {causalCase.main_chain.filter(link => link.claim_type === 'inference').slice(0, 2).map(link => <p key={link.link_id}>{researchDisplayText(link.statement)} <EvidenceRefLinks refs={link.evidence_refs} runId={runId} /></p>)}
      <details className="research-summary-original"><summary>展开完整研究解释与原始措辞</summary><p>{causalCase.thesis}</p>{causalCase.main_chain.map(link => <p key={link.link_id}>{link.statement} <EvidenceRefLinks refs={link.evidence_refs} runId={runId} /></p>)}</details>
      <div className="research-window-summary">{horizons.map(horizon => <article key={horizon.horizon}><span>{horizonName(horizon.horizon)}</span><strong>{directionName(horizon.action)}</strong><p>{researchWindowExpiry(horizon.expires_at, browserNow).detail} 原到期 UTC：{researchWindowExpiry(horizon.expires_at, browserNow).expiresUtc ?? '无法核对'}。</p><details><summary>确认与失效条件</summary><p><b>确认：</b>{researchDisplayText(horizon.trigger)}</p><p><b>失效：</b>{researchDisplayText(horizon.invalidation)}</p><EvidenceRefLinks refs={horizon.evidence_refs} runId={runId} /></details></article>)}</div>
      <p className="research-summary-scope">这是本次报告保存的研究判断；已到期窗口仅供历史复盘，未到期也不代表资料当前新鲜或预测有效。按浏览器参考 UTC：{new Date(browserNow).toISOString()}。方向效果尚待验证。<a href={`#/results?run=${encodeURIComponent(runId)}&back=${encodeURIComponent(window.location.hash || `#/report?run=${runId}`)}`}>查看这次判断的后续观察</a></p>
      {!!causalCase.unresolved_questions.length && <details><summary>本次综合保留的未解问题（{causalCase.unresolved_questions.length} 项）</summary><ul>{causalCase.unresolved_questions.map((item, index) => <li key={index}>{researchDisplayText(item)}</li>)}</ul></details>}
      <details><summary>反向解释与{reviewRecordName}（{causalCase.opposite_chain.length} 条反向解释，{reviews.length} 份记录）</summary><p className="research-section-note">{reviewStatus}。以下属于当前研究执行结果的检查记录，与下载原报告内的审查列表分开记载；自动证据检查不等于独立模型复核，原报告审查以已确认快照为准。</p>{causalCase.opposite_chain.map(link => <p key={link.link_id}><strong>反向解释：</strong>{researchDisplayText(link.statement)} <EvidenceRefLinks refs={link.evidence_refs} runId={runId} /></p>)}{reviews.map(review => <div key={review.review_id}>
        <p><strong>审查结论：{review.resolution === 'accepted' ? '接受' : review.resolution === 'rejected' ? '拒绝' : '有保留'}</strong> · {review.limitations.length} 项限制</p>
        <details><summary>展开完整审查意见</summary><p>{/[\u3400-\u9fff]/.test(review.challenge) ? researchDisplayText(review.challenge) : '复核意见以原文保存，尚无中文摘要。'}</p></details>
        {review.limitations.length > 0 && <ol aria-label="审查限制" style={{ listStyleType: 'none' }}>{review.limitations.map((value, index) => <li key={`${review.review_id}-limitation-${index}`}>{technicalSummaryName(value, '未提供', '未分类限制，见原始复核记录')}</li>)}</ol>}
        <OriginalRecord><p>{review.challenge}</p><small>限制：{review.limitations.join('；')}</small></OriginalRecord>
      </div>)}</details>
    </section>}
    {!taskCopy && !running && horizons.length === 0 && <section className="research-action-fallback"><h3>重新评估条件</h3><p>触发：{summary.blocking_gap_ids.length ? `补齐${summary.blocking_gap_ids.map(gap => gapLabel[gap] ?? '待核验材料').join('、')}` : '取得新的可引用证据并完成复核'}</p><p>失效：任一关键来源过期、冲突或再次不可用时，当前边界继续有效</p></section>}
    {!running && <div className="research-action-gaps"><strong>关键依据</strong><span>{summary.supporting_evidence_refs.length ? <EvidenceRefLinks refs={summary.supporting_evidence_refs} runId={runId} /> : collectedEvidenceSummary}</span></div>}
    {!taskCopy && limitations.length > 0 && <div className="research-action-gaps"><strong>使用这份研究时仍需核对</strong>{limitations.map(gap => <span key={gap}>{gapLabel[gap] ?? '其他待核验材料'}</span>)}</div>}
    {outlook && <details className="research-outlook" aria-label="日线规则参考"><summary>独立日线参考 · 7 天 · {outlookLabel[outlook.outlook]}（非综合判断）</summary><p>{outlook.rationale}</p><p>日线数据截至：{outlook.strategy_receipt?.as_of ?? '未提供'}；{outlook.strategy_receipt && `${outlook.strategy_receipt.bar_count} 根日线；`}规则概率未校准。</p><p><a href={`#/results?method=daily_rule&horizon=${encodeURIComponent(outlook.horizon)}&run=${encodeURIComponent(runId)}&back=${encodeURIComponent(window.location.hash || `#/report?run=${runId}`)}`}>查看这条日线判断的后续观察</a></p><dl><div><dt>规则提出的条件</dt><dd>{outlookActionLabel[outlook.action_candidate] ?? '详见规则原始回执'}</dd></div><div><dt>后续确认</dt><dd>{outlook.trigger}</dd></div><div><dt>最强反方</dt><dd>{outlook.counter_case}</dd></div><div><dt>判断失效</dt><dd>{outlook.invalidation}</dd></div><div><dt>依据</dt><dd><EvidenceRefLinks refs={outlook.evidence_refs} runId={runId} /></dd></div></dl>{outlook.missing_impact.map(impact => <p key={impact}>数据影响：{impact}</p>)}</details>}
    {!running && <details className="research-summary-audit"><summary>发布状态、数据质量与执行权限</summary><p>{summary.headline}。{summary.rationale}</p><div className="research-action-facts"><span>研究结果 <b>{artifactStatusLabel[summary.artifact_status]}</b></span><span>可靠性 <b>{reliabilityLabel[summary.reliability]}</b></span><span>数据 <b>{dataStatusLabel[summary.data_status]}</b></span><span>证据 <b>{summary.accepted_evidence_count} 可用 / {summary.stale_evidence_count} 过期 / {summary.rejected_evidence_count} 未采纳</b></span></div><p>{recommendationLabel[summary.recommendation]}</p>{summary.execution_view && <section className={`research-execution-view ${summary.execution_view.status}`} aria-label="执行权限"><strong>{executionStatusLabel[summary.execution_view.status]}</strong>{summary.execution_view.reasons.map(reason => <p key={reason}>{reason}</p>)}</section>}</details>}
    {!taskCopy && summary.next_review_at && <p className="research-action-review">报告建议复查时间：{formatDate(summary.next_review_at)}{pastReview ? '（建议时间已过）' : ''}；自动计划与执行情况请看本报告的后续复查记录。</p>}
    {!taskCopy && failure && <div className="research-action-failure"><AlertTriangle size={15} /><span>{currentStateUnconfirmed ? '原执行问题记录仍保留；当前状态尚未确认，请重新读取后核对。' : preparationWaiting ? '执行准备尚未确认，原任务仍保留；可展开研究过程核对原记录。' : terminalFailure ? `本次研究未完成，已有材料仍保留。${failure.retryable ? '可在任务记录重试。' : '请先查看失败原因。'}` : isAcquisitionClosingReserve(failure) ? (running && !hasSavedReport ? acquisitionClosingSummary : acquisitionClosingRecordSummary) : failure.error_code === 'research_pit_violation' ? '部分取证请求未通过时间校验，未纳入报告；已通过校验的证据仍保留，可展开研究过程核对。' : '部分调用未完成，已取得的证据继续保留；可展开研究过程核对影响与原因。'}</span></div>}
  </section>
}

const outlookLabel: Record<NonNullable<ResearchRunDetailView['action_summary']['research_outlook']>['outlook'], string> = {
  bullish: '偏多', bearish: '偏空', range: '区间 / 趋势分歧', unresolved: '证据不足',
}

const outlookActionLabel: Record<string, string> = {
  wait_for_range_break_or_trend_alignment: '旧规则：等待突破或趋势对齐；尚未形成当前单边配置判断',
  wait_pullback_or_breakout_confirmation: '旧规则偏多，入场待评估；未提供当前持仓操作判断',
  reduce_long_exposure_and_wait_for_reclaim: '降低多头暴露倾向，复查价格能否收复趋势',
}

function HorizonDecisions({ runId, horizons }: { runId: string; horizons: ResearchRunDetailView['horizons'] }) {
  const browserNow = useResearchWindowClock(horizons.map(item => item.expires_at))
  return <section className="research-section"><SectionHeading icon={Clock3} title="分周期行动条件" meta="30 分钟 / 24 小时 / 72 小时" />
    {horizons.length === 0 ? <ResearchEmpty text="当前证据不足以生成分周期判断" /> : <div className="horizon-grid">{horizons.map((horizon) => <article key={horizon.horizon}><header><span>{horizonName(horizon.horizon)}</span><strong className={`direction ${horizon.action}`}>{horizonActionLabel[horizon.action]}</strong><b>{horizon.probability_status === 'calibrated' ? `${Math.round(horizon.subjective_probability * 100)}%` : '未校准'}</b></header><p>{researchWindowExpiry(horizon.expires_at, browserNow).detail} 原到期 UTC：{researchWindowExpiry(horizon.expires_at, browserNow).expiresUtc ?? '无法核对'}。</p><dl><div><dt>满足后再评估</dt><dd>{researchDisplayText(horizon.trigger)}</dd></div><div><dt>判断失效</dt><dd>{researchDisplayText(horizon.invalidation)}</dd></div><div><dt>支持证据</dt><dd><EvidenceRefLinks refs={horizon.evidence_refs} runId={runId} /></dd></div><div><dt>报告建议复查时间</dt><dd>{formatDate(horizon.next_review_at)}</dd></div></dl>{horizon.confidence_cap_reason && <p><ShieldAlert size={13} />{researchDisplayText(horizon.confidence_cap_reason)}</p>}<OriginalRecord><pre>{JSON.stringify(horizon, null, 2)}</pre></OriginalRecord></article>)}</div>}
  </section>
}

function ResearchProcessSteps({ steps }: { steps: ResearchRunDetailView['process_steps'] }) {
  return <section className="research-section research-process" aria-label="分析过程">
    <SectionHeading icon={Network} title="分析过程" meta={`${steps.filter((step) => ['completed', 'limited', 'failed'].includes(step.status)).length}/${steps.length} 步已结束 · ${steps.filter(step => step.status === 'limited').length} 步有限制${steps.some(step => step.status === 'failed') ? ` · ${steps.filter(step => step.status === 'failed').length} 步失败` : ''}`} />
    <div className="research-process-list">{steps.map((step) => <article key={step.step_id} className={step.status}>
      <span>{step.order}</span>
      <div><header><strong>{step.label}</strong><b>{processStatusLabel[step.status]}</b></header><p>{step.kind === 'review' ? `复核${processStatusLabel[step.status]}；引用 ${step.evidence_refs.length} 条证据，完整意见见原始记录。` : researchDisplayText(step.outcome).replace(/Evidence/g, '证据').replace(/hard /g, '必需覆盖 ' ).replace(/soft /g, '补充覆盖 ').replace(/run_[a-zA-Z0-9]+/g, '本次任务')}</p><small>{step.evidence_refs.length} 条证据引用 · {step.tool_call_refs.length} 次调用{step.warning_codes.length ? ` · ${step.warning_codes.map(warningName).join('、')}` : ''}</small><OriginalRecord><p>{step.objective}</p><p>{step.outcome}</p><small>{step.warning_codes.join(' · ')}</small></OriginalRecord></div>
    </article>)}</div>
  </section>
}

function ToolActivity({ invocations, checkpoints }: { invocations: ResearchRunDetailView['rounds'][number]['tool_invocations']; checkpoints: ReadonlySet<string> }) {
  if (!invocations.length) return <div className="research-empty-line"><Wrench size={13} />本轮未记录工具调用</div>
  return <div className="tool-activity">{invocations.map((tool) => {
    const errorCode = tool.error?.error_code ?? tool.error_code
    const checkpoint = checkpoints.has(tool.tool_call_id)
    const closingReserve = tool.status === 'failed' && isAcquisitionClosingReserve(tool.error)
      && (tool.error_code == null || tool.error_code === tool.error?.error_code)
    return <div key={tool.tool_call_id}><span className={`tool-mark ${tool.status}`}>{tool.status === 'succeeded' ? <CheckCircle2 size={13} /> : tool.status === 'running' ? <Play size={13} /> : <AlertTriangle size={13} />}</span><div><strong>{technicalSummaryName(tool.capability_id)}</strong><p>{checkpoint ? '审查流程检查点（原账：调用失败）' : technicalName(tool.status)} · 第 {tool.attempt} 次尝试 · {tool.latency_ms === null ? '耗时尚未记录' : `${tool.latency_ms} 毫秒`} · {moneyAmount(tool.cost_usd, '费用未知')}</p>{checkpoint ? <small>候选冻结检查点；后续审查状态见分析过程，原错误和失败状态见原始记录。</small> : errorCode && <small className="tool-error">{closingReserve ? '此次新取证请求被拒绝，为整理与独立复核保留原时间；仍记失败，不允许重试。' : <>{technicalSummaryName(errorCode)} · {tool.error?.retryable ? '允许有界重试' : tool.error ? '需先解决原因' : '重试许可未记录'}</>}</small>}<OriginalRecord><p>{tool.query_summary}</p><pre className="asset-json">{JSON.stringify(tool, null, 2)}</pre></OriginalRecord></div></div>
  })}</div>
}

function CausalChain({ title, runId, links, counter = false }: { title: string; runId: string; links: NonNullable<ResearchRunDetailView['causal_case']>['main_chain']; counter?: boolean }) {
  return <div className={`causal-chain ${counter ? 'counter' : ''}`}><h4>{title}</h4>{links.map((link, index) => <div key={link.link_id}><span>{index + 1}</span><div><strong>{researchDisplayText(link.statement)}</strong><p><ArrowRight size={12} />确认：{researchDisplayText(link.confirmation)}</p><p><ArrowDownRight size={12} />失效：{researchDisplayText(link.invalidation)}</p><OriginalRecord><p>{link.statement}</p><p>确认：{link.confirmation}</p><p>失效：{link.invalidation}</p></OriginalRecord><small>{technicalName(link.claim_type)} · {link.affected_horizons.map(horizonName).join(' / ')} · 证据 <EvidenceRefLinks refs={link.evidence_refs} runId={runId} /></small></div></div>)}</div>
}

function EvidenceRefLinks({ refs, runId }: { refs: string[]; runId: string }) {
  const evidenceReferences = useContext(EvidenceReferenceContext)
  if (!refs.length) return <>无</>
  return <>{refs.map((ref, index) => <span key={ref}>{index > 0 && '、'}<a href={evidenceRouteHref(runId, ref)} title={ref}>{evidenceReferenceLabel(evidenceReferences, runId, ref)}</a></span>)}</>
}

function ResearchMetric({ icon: Icon, label, value, detail, tone }: { icon: typeof Activity; label: string; value: string | number; detail: string; tone: string }) { return <div><span className={`research-metric-icon ${tone}`}><Icon size={15} /></span><div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div></div> }
function SectionHeading({ icon: Icon, title, meta }: { icon: typeof Activity; title: string; meta: string }) { return <header className="research-section-heading"><div><Icon size={15} /><h3>{title}</h3></div><span>{meta}</span></header> }
function Coverage({ label, value }: { label: string; value: number | null }) { const percent = value === null ? null : Math.round(value * 100); return <div className="coverage-meter"><div><span>{label}</span><strong>{percent === null ? '未知' : `${percent}%`}</strong></div>{percent !== null && <div><span style={{ width: `${percent}%` }} /></div>}</div> }
function ResearchState({ icon: Icon, text, danger = false }: { icon: typeof Activity; text: string; danger?: boolean }) { return <div className={`research-state ${danger ? 'danger' : ''}`}><Icon size={16} />{text}</div> }
function ResearchEmpty({ text }: { text: string }) { return <div className="research-empty"><SearchCheck size={15} />{text}</div> }

function statusTone(status: ResearchRunView['status']) { return status === 'completed' ? 'success' : status === 'failed' || status === 'rejected' || status === 'cancelled' ? 'danger' : status === 'degraded' || status === 'research_only' || status === 'retry_wait' ? 'warning' : 'running' }
function shortId(value: string) { return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }

function formatRelative(value: string) { const diff = Math.max(0, Date.now() - new Date(value).getTime()); const minutes = Math.round(diff / 60_000); return minutes < 1 ? '刚刚' : minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前` }
