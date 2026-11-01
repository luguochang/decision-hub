import { useModalScrollLock } from './product/useModalScrollLock'
import { coverageUnavailableText } from './research/coverageLabels'
import { researchTaskScene, researchTitle } from './product/ResearchProgressPage'
import { assetBackLabel } from './product/assetFormat'
import { calendarDay, calendarStatus, calendarTime, eventName, calendarSource, calendarAuthority } from './product/calendarTime'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  CircleHelp,
  Code2,
  FileClock,
  FileSearch,
  Filter,
  GitBranch,
  Network,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Target,
  Upload,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import { restoredSubmissionText } from './research/submissionReceipts'
import { useSubmission } from './research/useSubmission'
import { SubmissionError, SubmissionRecovery } from './research/SubmissionRecovery'
import type { MacroEventView, ResearchDocumentVersion, ResearchRunView } from './api/types'
import { ResearchProgressPage } from './product/ResearchProgressPage'
import { ResearchLibraryPanel } from './research/ResearchLibraryPanel'
import { documentQualityBlocked } from './research/libraryRequest'
import { requirementNames, technicalLabels, researchTraceTitle, observationUnavailableExplanation, failureExplanation, technicalSummaryName } from './research/technicalPresentation'
import { useRunCommand } from './research/useRunCommand'
import { RunCommandRecovery } from './research/RunCommandRecovery'
import { RiskResearch } from './product/RiskResearch'

export type DesignScene =
  | 'home' | 'compose' | 'submitting' | 'queued' | 'researching' | 'progress' | 'failed'
  | 'home-loading' | 'home-unknown' | 'home-degraded' | 'home-empty'
  | 'runs' | 'calendar-all' | 'calendar-upcoming' | 'calendar-history' | 'event'
  | 'library' | 'upload' | 'document-uploading' | 'document-parsing' | 'document-quality'
  | 'document-review' | 'document-confirm' | 'document-confirmed' | 'document-ready'
  | 'document-linked' | 'document-failed' | 'sources' | 'states' | 'delivery'
  | 'catalog'
  | 'dsh' | 'dsh-conversation' | 'dsh-research' | 'dsh-report'

type DesignStatePageProps = {
  scene: DesignScene
  params: URLSearchParams
  onNavigate: (scene: string, params?: Record<string, string | undefined>) => void
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '未提供'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function shortId(value: string | null | undefined): string {
  if (!value) return '—'
  return value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value
}

function routeHref(scene: string, params: Record<string, string | undefined> = {}): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value) })
  const suffix = query.toString()
  return `#/${scene}${suffix ? `?${suffix}` : ''}`
}

const HISTORY_PAGE_SIZE = 50
const RESEARCH_OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'

function historyOffset(params: URLSearchParams): number {
  const value = Number(params.get('offset') ?? '0')
  return Number.isSafeInteger(value) && value >= 0 ? Math.floor(value / HISTORY_PAGE_SIZE) * HISTORY_PAGE_SIZE : 0
}

function HistoryPager({ offset, count, total, onPage }: { offset: number; count: number; total: number | null; onPage: (offset: number) => void }) {
  const hasNext = total === null ? count === HISTORY_PAGE_SIZE : offset + count < total
  return <div className="design-filter-row" aria-label="历史分页"><button className="button secondary" type="button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))}>上一页</button><span className="small">第 {Math.floor(offset / HISTORY_PAGE_SIZE) + 1} 页 · {count ? `${offset + 1}–${offset + count}` : '无记录'}{total !== null ? ` / 共 ${total} 条` : ''}</span><button className="button secondary" type="button" disabled={!hasNext} onClick={() => onPage(offset + HISTORY_PAGE_SIZE)}>下一页</button></div>
}

function goToDesignBack(value: string | undefined, fallbackScene: string, fallbackParams: Record<string, string | undefined>, onNavigate: DesignStatePageProps['onNavigate']): void {
  if (value?.startsWith('#/')) {
    window.history.pushState(null, '', value)
    window.dispatchEvent(new PopStateEvent('popstate'))
    return
  }
  onNavigate(fallbackScene, fallbackParams)
}

function useDesignDialogFocus(ref: React.RefObject<HTMLElement | null>): void {
  useModalScrollLock()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const selector = 'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'
    const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(selector)).filter((item) => item.offsetParent !== null)
    ;(focusables()[0] ?? dialog).focus({ preventScroll: true })
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) { event.preventDefault(); return }
      const index = items.indexOf(document.activeElement as HTMLElement)
      const next = event.shiftKey ? (index <= 0 ? items[items.length - 1] : items[index - 1]) : (index === items.length - 1 ? items[0] : items[index + 1])
      event.preventDefault()
      next.focus()
    }
    dialog.addEventListener('keydown', onKeyDown)
    return () => {
      dialog.removeEventListener('keydown', onKeyDown)
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [ref])
}

function stageForVersion(version: ResearchDocumentVersion | null | undefined): string {
  if (!version) return 'library'
  if (version.status === 'received') return 'document-uploading'
  if (version.status === 'parsing') return 'document-parsing'
  if (version.status === 'failed') return 'document-failed'
  if (documentQualityBlocked(version)) return 'document-quality'
  if (version.status === 'parsed' || version.status === 'correcting') return 'document-review'
  if (version.status === 'review') return 'document-confirm'
  if (version.status === 'ready') return 'document-ready'
  return 'library'
}

const researchFailureDetails: Record<string, string> = {
  research_method_binding_failed: '后台保存的终态错误为研究运行身份核验未完成；更早的执行原因需读取完整记录核对。',
  dsh_model_configuration_changed_during_run: '这次任务的模型配置与首次提交时不同，已阻止继续调用。请先核对配置，再发起新的研究。',
  research_provider_native_context_invalid: '研究输入未能通过核验，本次任务已停止。失败记录已保留，请在系统修复后再尝试。',
  dsh_model_binding_unavailable: '模型执行前的绑定校验未通过，本次未生成完整报告。请核对原始记录中的失败阶段，确认服务恢复后再发起研究。',
  dsh_model_binding_corrupt: '已保存的模型配置凭据校验失败，已停止继续调用；请核对原始记录。',
  dsh_model_binding_receipt_mismatch: '模型配置核验回执不匹配，已阻止继续调用。请核对服务配置与任务记录。',
  dsh_model_dispatch_identity_mismatch: '模型请求与任务身份不一致，已阻止继续调用。',
  dsh_model_dispatch_method_missing: '这次任务缺少执行前方法凭据，不能补记后继续执行。请重新发起研究。',
  dsh_model_dispatch_method_mismatch: '模型请求与已保存的研究方法不匹配，已阻止继续调用。',
  dsh_model_dispatch_lineage_invalid: '协作研究与原任务的关联核验失败，已阻止继续调用。请检查任务记录。',
  dsh_model_dispatch_run_terminal: '这次研究已经结束，不能继续发出模型请求。',
  cancelled_by_owner: '这次研究由用户主动停止，原任务与已收集材料保留。',
  scheduled_recheck_superseded: '这项旧复查已被更新的研究替代，无需重复执行。请返回主题查看最新报告。',
  stale_review_queue_cleanup: '这项过期复查已从等待队列停止，原记录仍保留。',
  study_tracking_paused: '主题暂停自动复查后，这项自动排队任务已停止。',
  provider_timeout: '研究服务等待超时，未生成可提交的结果。原任务与已收集的记录仍保留，可核对后重试。',
  source_unavailable: '本次研究所需的数据来源暂时无法访问，请查看来源状态后再重试。',
  dsh_sdk_unavailable: '本次执行时研究运行环境不可用，请先核对当前系统状态再重试。',
  dsh_provider_input_transport_timeout: '模型输入提交超时，本次未生成完整报告。原任务与已收集材料已保留；确认后台校验服务恢复后，可发起关联的新研究。',
  research_observation_unavailable: observationUnavailableExplanation,
  research_terminal_original_unavailable: '研究已结束，但最终结果的原始回执暂时无法核验，本次未发布完整报告。请先查看保留记录，等待后台恢复后刷新任务状态。',
  dsh_terminal_result_original_mismatch: '返回结果与原始执行记录不一致，系统已阻止发布。本次材料保留，需核对并修复后台后再发起研究。',
  research_worker_failed: '后台研究执行中断，本次未生成完整报告。原任务与已收集材料已保留；请展开原始记录核对失败原因。',
}

function researchFailureDetail(code: string, causeCode: string | null = null): string {
  return failureExplanation(code, causeCode) ?? researchFailureDetails[code] ?? '本次研究未正常完成。原任务与已收集材料已保留，请展开原始记录核对原因。'
}

export function DesignStatePage({ scene, params, onNavigate }: DesignStatePageProps) {
  if (scene === 'home' || scene === 'home-loading' || scene === 'home-unknown' || scene === 'home-degraded' || scene === 'home-empty') return <HomeStatePage scene={scene} params={params} onNavigate={onNavigate} />
  if (scene === 'queued' || scene === 'researching' || scene === 'submitting' || scene === 'progress') return <ResearchProgressPage back={params.get('back')} runId={params.get('run') ?? undefined} onNavigate={onNavigate} />
  if (scene === 'runs') return <RunsStatePage params={params} onNavigate={onNavigate} />
  if (scene === 'calendar-all' || scene === 'calendar-upcoming' || scene === 'calendar-history' || scene === 'event') return <CalendarStatePage scene={scene} params={params} onNavigate={onNavigate} />
  if (scene === 'library') return <ResearchLibraryPanel routeVersionId={params.get('version') ?? params.get('version_id') ?? undefined} onVersionState={(version) => onNavigate(stageForVersion(version), { version: version.version_id, back: routeHref('library') })} onOpenVersion={(versionId) => onNavigate('document-review', { version: versionId, back: routeHref('library') })} />
  if (scene.startsWith('document-') || scene === 'upload') return <LibraryStatePage scene={scene} params={params} onNavigate={onNavigate} />
  if (scene === 'sources') return <SourcesStatePage />
  if (scene === 'states') return <StatesStatePage onNavigate={onNavigate} />
  if (scene === 'delivery') return <DeliveryStatePage onNavigate={onNavigate} />
  if (scene === 'catalog') return <CatalogStatePage onNavigate={onNavigate} />
  if (scene.startsWith('dsh')) return <DshStatePage scene={scene} params={params} onNavigate={onNavigate} />
  return <ComposeStatePage scene={scene} params={params} onNavigate={onNavigate} />
}

function HomeStatePage({ scene, params, onNavigate }: { scene: Extract<DesignScene, 'home' | 'home-loading' | 'home-unknown' | 'home-degraded' | 'home-empty'>; params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const runsQuery = useQuery({ queryKey: ['design-home-runs'], queryFn: api.researchRuns, refetchInterval: 5_000, retry: false, enabled: scene === 'home' })
  const eventsQuery = useQuery({ queryKey: ['design-home-events'], queryFn: () => api.macroEvents('all'), refetchInterval: 15 * 60_000, retry: false, enabled: scene === 'home' })
  const healthQuery = useQuery({ queryKey: ['design-home-health'], queryFn: api.health, refetchInterval: 10_000, retry: false, enabled: scene === 'home' })
  const operationsQuery = useQuery({ queryKey: ['design-home-operations'], queryFn: api.operations, refetchInterval: 5_000, retry: false, enabled: scene === 'home' })
  const homeRuns = useMemo(() => (runsQuery.data ?? []).slice(0, 5), [runsQuery.data])
  const selectedRunId = params.get('run') ?? homeRuns[0]?.run_id
  const detailQuery = useQuery({ queryKey: ['design-home-detail', selectedRunId], queryFn: () => api.researchRun(selectedRunId!), enabled: scene === 'home' && Boolean(selectedRunId), retry: false })
  const worker = operationsQuery.data?.services.find((item) => item.service_id === 'hub-research-worker')
  const workerState = operationsQuery.isLoading ? '读取中' : operationsQuery.isError ? '未知' : worker?.status ?? '未知'
  if (scene === 'home') return <HomeDashboard runs={homeRuns} events={eventsQuery.data ?? []} detail={detailQuery.data} selectedRunId={selectedRunId} loading={runsQuery.isLoading || eventsQuery.isLoading} failed={runsQuery.isError || eventsQuery.isError} workerState={workerState} onNavigate={onNavigate} onSelectRun={(runId) => onNavigate('home', { run: runId })} />
  const state = scene === 'home-loading'
    ? { label: '工作台加载中', detail: '按 worker、日历和任务分别反馈，不用整页空白或假报告。', tone: 'blue' as const, icon: Activity }
    : scene === 'home-unknown'
      ? { label: '服务状态未知', detail: '心跳未确认在线；未知状态不会渲染成健康。', tone: 'amber' as const, icon: CircleHelp }
      : scene === 'home-degraded'
        ? { label: '来源降级', detail: '部分来源不可用，显示影响范围、最后成功和下一次检查。', tone: 'amber' as const, icon: AlertTriangle }
        : { label: '暂无研究任务', detail: '当前没有真实任务；可以开始一次研究或上传资料。', tone: 'blue' as const, icon: Search }
  return <div className="content-wrap design-state-page"><PageHeading eyebrow="RESEARCH COMMAND CENTER" title={state.label} copy="Decision Hub 不用演示数据填充当前状态；下一步操作保持明确。" action={<span className={`status-pill ${state.tone === 'amber' ? 'warning' : 'neutral'}`}><span className="status-dot" />{scene === 'home-loading' ? '读取中' : scene === 'home-unknown' ? '未知' : scene === 'home-degraded' ? '降级' : '空态'}</span>} /><StateNotice title={state.label} detail={state.detail} tone={state.tone} icon={state.icon} /><div className="design-home-actions"><button className="button primary" onClick={() => onNavigate('compose')}><Sparkles size={14} />获取最新操作建议</button><button className="button secondary" onClick={() => onNavigate('library')}><Upload size={14} />上传研究资料</button></div>{scene === 'home-degraded' && <section className="panel design-home-state-panel"><header className="panel-header"><div><h2>受影响范围</h2><p>降级不会覆盖已保存报告，也不会自动授权交易。</p></div></header><div className="design-source-list"><SourceRow label="来源采集" value="degraded" detail="部分来源失败或超时" /><SourceRow label="已保存报告" value="durable" detail="历史结论仍可复核" /><SourceRow label="交易权限" value="blocked" detail="研究方向不等于执行许可" /></div></section>}{scene === 'home-loading' && <div className="design-loading-grid"><span /><span /><span /><span /></div>}</div>
}

function HomeDashboard({ runs, events, detail, selectedRunId, loading, failed, workerState, onNavigate, onSelectRun }: { runs: ResearchRunView[]; events: MacroEventView[]; detail?: Awaited<ReturnType<typeof api.researchRun>>; selectedRunId?: string; loading: boolean; failed: boolean; workerState: string; onNavigate: DesignStatePageProps['onNavigate']; onSelectRun: (runId: string) => void }) {
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? runs[0]
  const activeRun = selectedRun ?? detail?.run
  const coverage = selectedRun?.coverage
  const workerTone = workerState === 'online' ? 'success' : workerState === 'degraded' ? 'warning' : workerState === 'offline' ? 'danger' : 'neutral'
  const workerLabel = workerState === 'online' ? '在线' : workerState === 'degraded' ? '降级' : workerState === 'offline' ? '离线' : workerState === '读取中' ? '读取中' : '未知'
  return <div className="content-wrap design-state-page home-dashboard"><PageHeading eyebrow="DURABLE AGENTIC RESEARCH" title="Research Command Center" copy="从研究问题到证据与结论，在一个工作台完成。" action={<span className={`status-pill ${workerTone}`}><span className="status-dot" />{workerLabel}</span>} /><section className="home-compose-bar"><Sparkles size={18} /><button type="button" onClick={() => onNavigate('compose')}>补充研究关注点，或留空使用 BTC / 宏观模板…</button><button className="home-compose-submit" type="button" onClick={() => onNavigate('compose')} aria-label="开始分析"><ArrowRight size={17} /></button><div className="home-compose-chips"><button type="button" onClick={() => onNavigate('compose', { focus: '美元指数和 ETF 资金流' })}>美元指数与 ETF 资金流</button><button type="button" onClick={() => onNavigate('compose', { focus: '证据缺口与反方限制' })}>证据缺口与反方限制</button></div></section><div className="home-metric-grid"><button className="home-primary-action" onClick={() => onNavigate('compose')}><Sparkles size={21} /><strong>获取最新操作建议</strong><small>空输入也能开始，模板已就绪</small><ArrowRight size={18} /></button><HomeMetric icon={Target} label="研究范围" value="BTC / 宏观" hint="不展示未经确认的实时行情" /><HomeMetric icon={FileSearch} label="证据覆盖" value={coverage ? `${Math.round(coverage.hard_coverage_ratio * 100)}%` : '未知'} hint={coverage ? `${coverage.gaps.length} 个缺口` : '等待真实 Run'} /><HomeMetric icon={ShieldAlert} label="执行权限" value="未授权交易" hint="研究方向不等于执行许可" /></div><div className="home-dashboard-grid"><section className="panel home-calendar-card"><div className="panel-header"><div><h2>宏观事件日历</h2><p>官方时间与发布状态</p></div><button className="panel-link" onClick={() => onNavigate('calendar-all')}>全部 <ArrowRight size={13} /></button></div>{loading && <div className="workspace-state"><Activity size={16} />正在同步事件与任务…</div>}{!loading && events.length === 0 && <div className="workspace-state"><CalendarDays size={16} />暂无真实事件</div>}{events.slice(0, 4).map((event) => <button className="home-calendar-row" key={event.macro_event_id} onClick={() => onNavigate('event', { event: event.macro_event_id, scope: 'all', back: routeHref('home') })}><span className="home-date"><b>{calendarDay(event).slice(8) || '—'}</b><small>{`${calendarDay(event).slice(5, 7)}月`}</small></span><span><strong>{event.family}</strong><small>{calendarTime(event)} · {event.source_id}</small></span><span className={`status-pill ${event.status === 'released' ? 'success' : 'warning'}`}>{calendarStatus(event)}</span></button>)}</section><section className="panel home-runs-card"><div className="panel-header"><div><h2>Research Runs</h2><p>最近任务与父子关系</p></div><button className="panel-link" onClick={() => onNavigate('runs')}>查看全部 <ArrowRight size={13} /></button></div>{!loading && runs.length === 0 && <div className="workspace-state"><Search size={16} />暂无研究任务</div>}{runs.map((run) => <button className={`home-run-row ${run.run_id === selectedRunId ? 'selected' : ''}`} key={run.run_id} onClick={() => onSelectRun(run.run_id)}><span className="home-run-dot" /><span><strong>{run.event_title}</strong><small>{run.admission_origin} · {formatDate(run.created_at)}</small></span><RunStatus run={run} /><ArrowRight size={13} /></button>)}</section><section className="panel home-report-card"><div className="panel-header"><div><h2>{activeRun?.event_title ?? '当前研究报告'}</h2><p>{activeRun ? shortId(activeRun.run_id) : '等待选择 Run'}</p></div>{activeRun && <RunStatus run={activeRun} />}</div>{failed && <div className="workspace-state danger"><AlertTriangle size={16} />首页数据不可用，未使用演示数据。</div>}{!failed && !detail && <div className="workspace-state"><Activity size={16} />{activeRun ? '正在读取报告…' : '选择一个 Run 查看报告'}</div>}{detail && <><div className="home-report-callout"><span className="eyebrow">最新研究结论</span><h3>{detail.action_summary.headline}</h3><p>{detail.action_summary.rationale}</p><button className="text-button" onClick={() => onNavigate(detail.run.status === 'research_only' ? 'report-incomplete' : 'report', { run: detail.run.run_id, back: routeHref('home', { run: detail.run.run_id }) })}>打开完整研究报告 <ArrowRight size={13} /></button></div><div className="home-report-stats"><span><strong>{detail.evidence.length}</strong><small>证据条目</small></span><span><strong>{coverage ? `${Math.round(coverage.hard_coverage_ratio * 100)}%` : '—'}</strong><small>硬要求覆盖</small></span><span><strong>{detail.action_summary.trade_authorized ? '已授权' : '未授权'}</strong><small>执行权限</small></span></div></>}</section></div><p className="home-dashboard-footnote">所有状态、日历和进度均来自真实 API；缺失数据会明确显示，不用演示内容补齐。</p></div>
}

function HomeMetric({ icon: Icon, label, value, hint }: { icon: typeof Target; label: string; value: string; hint: string }) { return <div className="home-metric-card"><Icon size={18} /><small>{label}</small><strong>{value}</strong><span>{hint}</span></div> }

function PageHeading({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy: string; action?: React.ReactNode }) {
  return <section className="page-heading design-state-heading"><div><div className="eyebrow"><span className="live-dot" />{eyebrow}</div><h1>{title}</h1><p>{copy}</p></div>{action}</section>
}

function BreadcrumbBack({ label, href }: { label: string; href: string }) {
  return <a className="design-backlink" href={href}><ArrowRight size={13} className="back-arrow" />{label}</a>
}

function StateNotice({ title, detail, tone = 'blue', icon: Icon = CircleHelp }: { title: string; detail: string; tone?: 'blue' | 'amber' | 'green' | 'red'; icon?: typeof Activity }) {
  return <div className={`design-state-notice ${tone}`}><Icon size={17} /><div><strong>{title}</strong><span>{detail}</span></div></div>
}

function ComposeStatePage({ scene, params, onNavigate }: DesignStatePageProps) {
  const composeBack = assetBackLabel(params.get('back')) ? params.get('back')! : '#/research'
  const [focus, setFocus] = useState(params.get('focus') ?? restoredSubmissionText('research-submit') ?? '')
  const [entryMode, setEntryMode] = useState<'decision' | 'materials'>(() =>
    params.get('focus')?.trim() || restoredSubmissionText('research-submit')?.trim() ? 'materials' : 'decision')
  const [decisionAsset, setDecisionAsset] = useState<'BTC' | 'ETH'>('BTC')
  const requestedFocus = params.get('focus')
  useEffect(() => {
    if (requestedFocus !== null) {
      setFocus(requestedFocus)
      if (requestedFocus.trim()) setEntryMode('materials')
    }
  }, [requestedFocus])
  const failedRunId = params.get('run') ?? params.get('run_id')
  const failedRunQuery = useQuery({
    queryKey: ['design-failed-run', failedRunId], enabled: scene === 'failed' && Boolean(failedRunId),
    retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const value = await api.run(failedRunId!)
      if (value.run_id !== failedRunId || value.strategy_version !== 'research.v1') throw new Error('原任务的研究身份或类型无法核对。')
      return value
    },
  })
  const failedDetailQuery = useQuery({
    queryKey: ['design-failed-run-details', failedRunId], enabled: scene === 'failed' && Boolean(failedRunId), retry: false,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const value = await api.researchRun(failedRunId!)
      if (value.run.run_id !== failedRunId) throw new Error('完整记录的原任务身份无法核对。')
      return value
    },
  })
  const failedRun = failedRunQuery.data && failedRunQuery.data.run_id === failedRunId && failedRunQuery.data.strategy_version === 'research.v1' ? failedRunQuery.data : undefined
  const failedDetail = failedDetailQuery.data?.run.run_id === failedRunId ? failedDetailQuery.data : undefined
  const failedPerpetual = failedDetail?.run.research_profile === 'perpetual_decision_v2'
  const canRetry = Boolean(!failedPerpetual && failedDetailQuery.isSuccess && !failedDetailQuery.isFetching && failedDetail?.run.status === failedRun?.status && failedRun && !failedRunQuery.isError && !failedRunQuery.isFetching && ['failed', 'cancelled', 'degraded'].includes(failedRun.status))
  const failedDetailReadState = failedDetailQuery.isFetching
    ? failedDetail ? '正在重新读取完整记录；下方保留上次成功读取的快照。' : '正在读取完整任务与材料记录。'
    : failedDetailQuery.isError
      ? failedDetail ? '本次完整记录读取失败；下方保留上次成功读取的快照。' : '本次完整记录读取失败，尚未取得完整记录。'
      : failedDetail ? '完整记录已读取。' : '完整材料与调用数量尚未读取。'
  const failedTaskText = failedPerpetual ? '按已保存设置发起的永续研究'
    : failedDetail?.research_focus?.trim() ? failedDetail.research_focus
    : failedDetail?.run.event_title ? `任务标题：${failedDetail.run.event_title}` : ''
  const failedTaskPlaceholder = failedDetail ? '完整记录未提供研究问题或任务标题；原记录仍由后台保留。'
    : failedDetailQuery.isFetching ? '正在读取原任务的完整记录…'
      : failedDetailQuery.isError ? '本次完整记录读取失败，原任务仍由后台保留。' : '原研究问题尚未读取；后台保留原任务，可按需读取完整记录。'
  const failedDetailCounts = failedDetail ? `材料记录：${Array.isArray(failedDetail.evidence) ? `${failedDetail.evidence.length} 条` : '未提供计数'} · 工具调用：${typeof failedDetail.total_tool_calls === 'number' && Number.isSafeInteger(failedDetail.total_tool_calls) && failedDetail.total_tool_calls >= 0 ? `${failedDetail.total_tool_calls} 次` : '未提供计数'}。` : ''
  const failedDetailIsCurrent = Boolean(failedDetail && failedRun && failedDetail.run.status === failedRun.status
    && Date.parse(failedDetail.run.updated_at) >= Date.parse(failedRun.updated_at))
  const detailFailure = failedDetailIsCurrent ? failedDetail?.run.failure : undefined
  const detailErrorCode = detailFailure?.error_code ?? failedRun?.error_code ?? ''
  const detailExplanation = failureExplanation(detailErrorCode, detailFailure?.cause_code ?? null, detailFailure)
  const detailErrorSummary = !['failed', 'cancelled', 'degraded'].includes(detailErrorCode) ? technicalSummaryName(detailErrorCode, '', '') : ''
  const detailStopSummary = technicalSummaryName(failedDetail?.run.stop_reason?.detail,
    '完整记录未提供单独的任务停止说明；已保存的基础终态错误仍保留。', '停止依据尚无中文说明，请展开原始任务记录核对。')
  const detailCauseSummary = detailFailure?.cause_code && !['failed', 'cancelled', 'degraded'].includes(detailFailure.cause_code)
    ? technicalSummaryName(detailFailure.cause_code, '未记录', '原因尚无中文说明，请展开原始任务记录核对') : null
  const failedReason = failedDetail
    ? `${failedRun?.status === 'cancelled'
      ? failedDetailIsCurrent ? detailStopSummary : '后台已保存本次停止状态。'
      : detailExplanation ?? (detailErrorSummary ? `${detailErrorSummary}；原任务与失败记录保留。` : failedDetailIsCurrent ? detailStopSummary : researchFailureDetail(failedRun?.error_code ?? ''))}${detailCauseSummary ? ` ${failedRun?.status === 'cancelled' ? '另有阶段记录' : '已保存的执行原因'}：${detailCauseSummary}。${failedRun?.status === 'cancelled' ? '阶段记录与最终停止状态分别保留。' : ''}` : ''} ${failedDetailIsCurrent ? '' : '完整记录快照与当前基础任务状态不一致或较早，停止依据以基础状态为准；原快照仍保留。'} ${failedDetailReadState}`
    : `${failedRun?.status === 'cancelled' ? '后台已保存本次停止状态。' : researchFailureDetail(failedRun?.error_code ?? '')} 当前仅显示基础任务保存的终态错误；${failedDetailReadState}`
  const submit = useSubmission('research-submit', { text: focus.trim() }, JSON.stringify([scene, params.toString()]),
    (payload, key) => api.submitResearch((payload as { text: string }).text, key),
    (result, _original, originHash) => {
      const originalBack = new URLSearchParams(originHash.split('?')[1]).get('back')
      onNavigate('queued', { run: result.run_id, back: originalBack && assetBackLabel(originalBack) ? originalBack : composeBack })
    })
  const retry = useRunCommand(scene === 'failed' ? failedRunId : null, RESEARCH_OWNER_ID, receipt => {
    if (receipt.result.target_run_id) onNavigate('queued', { run: receipt.result.target_run_id, ...(assetBackLabel(params.get('back')) ? { back: params.get('back')! } : {}) })
  })
  const pendingSubmit = submit.originalPending
  const running = ['submitting', 'queued', 'researching'].includes(scene)
  const statusCopy: Record<string, { title: string; detail: string; tone: 'blue' | 'amber' | 'green' | 'red' }> = {
    submitting: { title: '正在提交研究请求', detail: '保持同一研究意图和幂等键，按钮暂时锁定。', tone: 'blue' },
    queued: { title: '研究已排队', detail: 'Run 已登记，可以安全离开页面；离开不会取消任务。', tone: 'blue' },
    researching: { title: '正在研究', detail: '正在收集证据、检查覆盖并等待独立复核。', tone: 'blue' },
    failed: { title: '研究任务未完成', detail: '请核对原任务的原因和状态，再决定是否重试。', tone: 'red' },
  }
  const modes = scene === 'compose' && <div className="design-chip-row" aria-label="研究用途">
    <button type="button" aria-pressed={entryMode === 'decision' && !pendingSubmit} disabled={Boolean(pendingSubmit)} onClick={() => setEntryMode('decision')}>当前操作方案</button>
    <button type="button" aria-pressed={entryMode === 'materials' || Boolean(pendingSubmit)} onClick={() => setEntryMode('materials')}>专题材料分析</button>
  </div>
  if (scene === 'compose' && entryMode === 'decision' && !pendingSubmit) {
    const scopeId = `crypto:spot:okx:${decisionAsset}-USDT:trade:1m`
    return <div className="content-wrap design-state-page">
      <BreadcrumbBack label={assetBackLabel(composeBack)!} href={composeBack} />
      <PageHeading eyebrow="当前市场 · 决策研究" title="获取当前操作方案" copy="从最新行情、宏观因素和已保存证据出发，研究方向、操作条件及风险。请求保存在后台，稍后可回来查看。" />
      {modes}
      <div className="design-split wide-aside"><section className="stack">
        <section className="panel pad"><label>研究资产 <select value={decisionAsset} onChange={event => setDecisionAsset(event.target.value as 'BTC' | 'ETH')} aria-label="决策研究资产"><option value="BTC">比特币 BTC</option><option value="ETH">以太坊 ETH</option></select></label><p className="small">按所选资产核验真实行情、运行状态和现货研究范围，条件具备后进入决策分析。</p></section>
        <RiskResearch scopeId={scopeId} onNavigate={onNavigate} title={`${decisionAsset} 当前决策研究`} actionLabel={`开始 ${decisionAsset} 决策研究`} primaryAction />
      </section><aside className="stack design-side-stack"><section className="panel pad"><h2>研究与回看</h2><div className="design-timeline"><div><span>1</span><div><strong>保存本次请求</strong><p>原请求和处理状态可回看，等待期间无需重复提交。</p></div></div><div><span>2</span><div><strong>核验真实研究条件</strong><p>行情与运行条件核验通过后，按当前资产冻结研究范围。</p></div></div><div><span>3</span><div><strong>查看方案和证据</strong><p>研究完成后核对方向、条件、风险、反方和数据缺口。</p></div></div></div><a className="button secondary" href={routeHref('runs')}>查看已有研究任务</a></section></aside></div>
    </div>
  }
  return <div className="content-wrap design-state-page">
    <BreadcrumbBack label={assetBackLabel(composeBack)!} href={composeBack} />
    <PageHeading eyebrow={scene === 'failed' ? '原任务 · 市场研究' : 'BTC · 宏观研究'} title={scene === 'failed' ? '查看未完成的研究' : '专题材料分析'} copy={scene === 'failed' ? '可以先查看停止原因、已保留的材料与调用记录，再决定是否重新研究。' : '填写本次资料或关注点，整理 BTC / 宏观模板下的证据和解释。当前操作方案使用独立的决策入口。'} action={<span className="status-pill neutral">{scene === 'failed' ? '新旧任务可追溯' : '无需填写专业参数'}</span>} />
    {modes}
    {scene === 'compose' && <p className="small">这里整理专题材料与证据。当前资产的操作方案请使用“当前操作方案”；已有未确认提交会先保留并核对原请求。</p>}
    {scene !== 'compose' && <StateNotice {...statusCopy[scene]} icon={scene === 'failed' ? AlertTriangle : Activity} />}
    <div className="design-split wide-aside">
      <section className="panel design-form-panel"><header className="panel-header"><div><h2>{scene === 'failed' ? '原研究任务' : '研究关注点'}</h2><p>{scene === 'failed' ? failedPerpetual ? '本次研究按已保存设置自动生成，无需手动填写研究问题。原任务和资料由后台保留。' : '原研究问题和资料由后台保留；先核对基础任务状态，完整记录按需读取。重新研究的范围与资料权限仍由后台核验。' : '写下本次要整理的资料或关注点。'}</p></div><span className="status-pill neutral">{scene === 'failed' ? '只读' : '选填'}</span></header><div className="design-form-body"><textarea value={scene === 'failed' ? failedTaskText : focus} onChange={(event) => setFocus(event.target.value)} readOnly={running || scene === 'failed' || submit.busy} maxLength={5000} placeholder={scene === 'failed' ? failedTaskPlaceholder : '例如：重点关注美元指数、ETF 资金流和当前证据缺口…'} aria-label={scene === 'failed' ? '原研究任务' : '研究关注点'} />{scene !== 'failed' && <><div className="design-form-meta"><span>{focus.length} / 5000</span><span>材料研究保留原文与提交回执</span></div><div className="design-chip-row"><button type="button" onClick={() => setFocus('美元指数和 ETF 资金流')} disabled={running || submit.busy}>美元指数和 ETF 资金流</button><button type="button" onClick={() => setFocus('关键证据与反方限制')} disabled={running || submit.busy}>关键证据与反方限制</button><button type="button" onClick={() => setFocus('短期保持观望的原因')} disabled={running || submit.busy}>短期保持观望的原因</button></div></>}{scene === 'failed' && <StateNotice title={failedRun?.status === 'cancelled' ? '停止原因' : '未完成原因'} detail={!failedRunId ? '链接缺少原任务，无法发起重试。' : failedRunQuery.isError ? '原任务当前状态暂时无法核对；下方保留记录不代表最新状态，请刷新后再决定是否重新研究。' : failedRun?.status === 'cancelled' ? failedReason : failedRun && !['failed', 'cancelled', 'degraded'].includes(failedRun.status) ? '基础记录显示任务尚未处于可重新研究的失败终态；请返回进度页核对当前执行。' : failedRun ? failedReason : '正在读取原任务基础状态…'} tone="red" icon={AlertTriangle} />}{scene === 'failed' && <button className="button secondary" disabled={!failedRunId || failedRunQuery.isFetching} onClick={() => void failedRunQuery.refetch()}>刷新任务状态</button>}{scene === 'failed' && failedRun && <button className="button secondary" onClick={() => onNavigate('report', { run: failedRun.run_id, back: composeBack })}>查看已保留的材料与调用记录</button>}{scene === 'failed' && failedRun && <><p role="status">{failedPerpetual ? '本次永续研究按已保存设置自动生成，无需手动填写研究问题。' : failedRun.headline ? `已保存的任务摘要：${failedRun.headline}` : failedDetail ? failedDetail.research_focus?.trim() ? '已读取后台保存的研究问题。' : failedDetail.run.event_title ? '已读取后台保存的任务标题；未保存单独的研究问题。' : '完整记录未提供研究问题或任务标题。' : '基础记录未提供研究问题；原任务与问题由后台保留。'} {failedDetailReadState} {failedDetailCounts}</p><button className="button secondary" disabled={failedDetailQuery.isFetching || failedRunQuery.isError || failedRunQuery.isFetching} onClick={() => void failedDetailQuery.refetch()}>{failedDetailQuery.isFetching ? '正在读取完整记录…' : '读取完整任务与停止依据'}</button>{failedDetailQuery.isError && <p role="alert">{failedDetail ? '本次完整记录更新失败，保留上次成功读取的完整快照与基础任务状态。' : '完整记录暂时无法核对，已保留基础任务状态；可稍后再次读取，不影响核对已保存的终态错误。'}</p>}<details><summary>原始任务记录</summary><p>基础任务状态：{failedRun.status} · 最近保存：{formatDate(failedRun.updated_at)}</p><p>已保存终态错误：{failedRun.error_code ?? '未提供错误码'}</p>{failedDetail ? <>{failedDetail.run.stop_reason && <p>完整记录中的任务停止依据：{failedDetail.run.stop_reason.detail}</p>}<p>完整错误记录：{failedDetail.run.failure?.error_code ?? failedDetail.run.stop_reason?.code ?? '未提供错误码'}{failedDetail.run.failure?.cause_code && ` · ${failedDetail.run.failure.cause_code}`}{failedDetail.run.failure && ` · 来源：${failedDetail.run.failure.origin}`}</p>{failedDetail.run.current_action && <p>最近工作记录（不替代任务停止依据）：{failedDetail.run.current_action}</p>}{failedPerpetual && <><p>原任务标题：{failedDetail.run.event_title}</p>{failedDetail.research_focus && <p>原始研究输入：{failedDetail.research_focus}</p>}{failedRun.headline && <p>原任务摘要：{failedRun.headline}</p>}</>}</> : <p>{failedDetailReadState} 基础错误码不等于最早的执行原因。</p>}</details></>}{scene === 'failed' && failedPerpetual && <p>原研究与回执保持不变；请返回建议与模拟，核对当前已保存账户设置后显式发起一次新研究。</p>}{scene === 'failed' && !failedPerpetual && failedDetail?.run.failure?.retryable === false && <p role="status">该错误不支持自动重试；下方操作会主动创建关联原任务的新研究。</p>}{scene === 'failed' && !failedPerpetual && failedRun && !canRetry && <p className="form-error" role="status">此任务当前不能重试；请返回任务记录核对最新状态。</p>}{scene === 'compose' && pendingSubmit && !submit.busy && <p role="status">有一笔尚未确认的提交。继续提交会核对原请求；也可先<a href={routeHref('runs')}>查看任务记录</a>。</p>}{scene === 'compose' ? <button className="button primary design-main-action" onClick={submit.execute} disabled={submit.busy || submit.blocked || (!focus.trim() && !pendingSubmit)}><Sparkles size={15} />{submit.busy ? '正在提交…' : '整理材料与证据'}</button> : scene === 'failed' && failedPerpetual ? <a className="button primary design-main-action" href="#/settings">按当前已保存设置发起新研究</a> : scene === 'failed' ? <button className="button primary design-main-action" onClick={() => { if (canRetry) retry.execute('retry', '用户从失败任务页请求重试原 Run') }} disabled={!canRetry || retry.busy || retry.locked}><RefreshCw size={15} />{retry.busy ? '正在核对…' : '重新尝试研究'}</button> : <a className="button primary design-main-action" href={routeHref('report', { run: params.get('run') ?? undefined })}>查看当前研究 <ArrowRight size={15} /></a>}{scene === 'failed' && <RunCommandRecovery state={retry} />}<SubmissionError error={submit.error} />{scene === 'compose' && <SubmissionRecovery state={submit} />}</div></section>
      <aside className="stack design-side-stack"><section className="panel pad"><h2>{scene === 'failed' ? '原任务范围核对' : '这次研究会包含'}</h2><div className="design-timeline"><div><span>1</span><div><strong>资料与证据</strong><p>核对来源、时效和必要证据要求</p></div></div><div><span>2</span><div><strong>{scene === 'failed' ? '原任务范围' : '方向与候选'}</strong><p>{scene === 'failed' ? '资产、市场和分析期限沿用原任务，具体范围以已保存记录为准。' : '7 天日线参考，以及 30 分钟、24 小时、72 小时研究判断'}</p></div></div><div><span>3</span><div><strong>限制与复核</strong><p>反方依据、数据缺口与适用限制；方向效果需后续观察</p></div></div></div><StateNotice title="没有默认交易授权" detail="研究结果不会自动触发买入、卖出或权限变更。" tone="amber" icon={ShieldAlert} /></section>{scene === 'researching' && <section className="panel pad"><div className="design-stat-grid"><span><small>当前轮次</small><strong>进行中</strong></span><span><small>工具调用</small><strong>实时</strong></span><span><small>最近活动</small><strong>刚刚</strong></span></div><p className="small">可进入技术详情查看规范化轨迹。</p></section>}</aside>
    </div>
  </div>
}

function RunsStatePage({ params, onNavigate }: { params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const query = params.get('q') ?? ''
  const status = params.get('status') ?? 'all'
  const origin = params.get('origin') ?? 'all'
  const offset = historyOffset(params)
  const filters = { q: query || undefined, status: status === 'all' ? undefined : status, origin: origin === 'all' ? undefined : origin }
  const runsQuery = useQuery({ queryKey: ['design-runs', offset, query, status, origin], queryFn: () => api.researchRunsPage({ limit: HISTORY_PAGE_SIZE, offset, ...filters }), refetchInterval: 5_000, retry: false })
  const runs = runsQuery.data?.items ?? []
  const parentBack = assetBackLabel(params.get('back')) ? params.get('back')! : '#/research'
  const navigationFilters = { ...filters, back: parentBack }
  const sync = (next: Record<string, string | undefined>) => onNavigate('runs', { ...navigationFilters, ...next })
  const listBack = routeHref('runs', { ...navigationFilters, offset: offset ? String(offset) : undefined })
  return <div className="content-wrap design-state-page"><BreadcrumbBack label={assetBackLabel(parentBack) ?? '返回研究'} href={parentBack} /><PageHeading eyebrow="研究历史" title="全部研究任务" copy="每次提交、定时复查与重试都有独立记录，父子关系可追踪。" action={<a className="button primary" href={routeHref('compose')}><FileSearch size={14} />发起研究</a>} />
    <section className="panel design-filter-panel"><div className="design-filter-row"><label className="design-search"><Search size={15} /><input value={query} onChange={(event) => sync({ q: event.target.value || undefined })} placeholder="搜索任务标题、Run ID 或关注点…" aria-label="搜索研究任务" /></label><label>任务状态<select value={status} onChange={(event) => sync({ status: event.target.value === 'all' ? undefined : event.target.value })}><option value="all">全部状态</option><option value="completed">已完成</option><option value="researching">研究中</option><option value="queued">已排队</option><option value="failed">失败</option><option value="research_only">仅研究</option></select></label><label>触发方式<select value={origin} onChange={(event) => sync({ origin: event.target.value === 'all' ? undefined : event.target.value })}><option value="all">全部方式</option><option value="manual">用户提交</option><option value="automatic">自动发现</option><option value="scheduled_recheck">定时复查</option><option value="legacy">历史兼容</option></select></label><button className="button secondary" type="button" onClick={() => onNavigate('runs', { back: parentBack })}><RefreshCw size={14} />重置</button></div></section>
    <section className="panel design-table-panel">{runsQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取研究任务…</div>}{runsQuery.isError && <div className="workspace-state danger"><AlertTriangle size={17} />研究任务不可用，未使用演示数据替代。</div>}{!runsQuery.isLoading && !runsQuery.isError && runs.length === 0 && <div className="workspace-state"><Search size={17} />没有匹配的任务，调整筛选不会删除记录。</div>}{runs.length > 0 && <div className="table-wrap"><table className="design-data-table"><thead><tr><th>研究任务</th><th>状态</th><th>触发 / 时间</th><th>必需证据覆盖</th><th>父任务</th><th /></tr></thead><tbody>{runs.map((run) => <tr key={run.run_id}><td><button type="button" className="design-table-link" onClick={() => onNavigate(researchTaskScene(run.status, Boolean(run.artifact_id)), { run: run.run_id, back: listBack })}><strong>{run.event_title}</strong><small>{shortId(run.run_id)}</small></button></td><td><RunStatus run={run} /></td><td><span className="mono">{{ manual: '用户提交', automatic: '自动发现', scheduled_recheck: '定时复查', legacy: '历史兼容' }[run.admission_origin] ?? '其他方式'}</span><small>{formatDate(run.created_at)}</small></td><td><RunHistoryCoverage run={run} /></td><td>{run.parent_run_id ? <span className="mono">{shortId(run.parent_run_id)}</span> : <span className="small">根任务</span>}</td><td><button className="text-button" type="button" onClick={() => onNavigate(researchTaskScene(run.status, Boolean(run.artifact_id)), { run: run.run_id, back: listBack })}>查看 <ArrowRight size={13} /></button></td></tr>)}</tbody></table></div>}</section>
    {!runsQuery.isLoading && !runsQuery.isError && <HistoryPager offset={offset} count={runs.length} total={runsQuery.data?.total ?? null} onPage={(next) => onNavigate('runs', { ...navigationFilters, offset: next ? String(next) : undefined })} />}
  </div>
}

function RunHistoryCoverage({ run }: { run: ResearchRunView }) {
  const coverage = ['saved_result', 'saved_request', 'saved_task_request', 'saved_checkpoint'].includes(run.coverage_basis ?? '')
    ? run.coverage : null
  const gapSummary = !coverage ? '缺口未核验'
    : coverage.gaps.some(gap => gap.importance !== 'hard' && gap.importance !== 'soft') ? '缺口分类未核验'
      : `必需缺口 ${coverage.gaps.filter(gap => gap.importance === 'hard').length} 个 · 补充缺口 ${coverage.gaps.filter(gap => gap.importance === 'soft').length} 个`
  return <><span className="coverage-chip">{coverage ? `${Math.round(coverage.hard_coverage_ratio * 100)}%` : coverageUnavailableText(run)}</span><small>{gapSummary}</small></>
}

function RunStatus({ run }: { run: ResearchRunView }) {
  const statusLabel: Record<ResearchRunView['status'], string> = { admitted: '已接收', queued: '排队中', researching: '研究中', retry_wait: '等待重试', completed: '已完成', degraded: '有界降级', research_only: '仅研究', rejected: '已拒绝', failed: '失败', cancelled: '已取消' }
  const tone = ['failed', 'rejected', 'cancelled'].includes(run.status) ? 'danger' : ['degraded', 'research_only', 'retry_wait'].includes(run.status) ? 'warning' : run.status === 'completed' ? 'success' : 'neutral'
  return <span className={`status-pill ${tone}`}><span className="status-dot" />{statusLabel[run.status]}</span>
}

function CalendarStatePage({ scene, params, onNavigate }: { scene: DesignScene; params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const baseScene = scene === 'event'
    ? params.get('scope') === 'history' ? 'calendar-history' : params.get('scope') === 'upcoming' ? 'calendar-upcoming' : 'calendar-all'
    : scene
  const scope = baseScene === 'calendar-history' ? 'history' : baseScene === 'calendar-upcoming' ? 'upcoming' : 'all'
  const query = params.get('q') ?? ''
  const family = params.get('family') ?? 'all'
  const offset = historyOffset(params)
  const filters = { q: query || undefined, family: family === 'all' ? undefined : family }
  const parentBack = scene !== 'event' && assetBackLabel(params.get('back')) ? params.get('back')! : undefined
  const navigationFilters = { ...filters, ...(parentBack ? { back: parentBack } : {}) }
  const eventsQuery = useQuery({ queryKey: ['design-calendar', scope, offset, query, family], queryFn: () => api.macroEventsPage({ limit: HISTORY_PAGE_SIZE, offset, scope, ...filters }), refetchInterval: 15 * 60_000, retry: false })
  const familiesQuery = useQuery({ queryKey: ['calendar-families'], queryFn: () => api.macroEventFamilies(), staleTime: 15 * 60_000, retry: false })
  const events = eventsQuery.data?.items ?? []
  const eventId = params.get('event') ?? params.get('event_id')
  const eventQuery = useQuery({ queryKey: ['design-calendar-event', eventId], queryFn: () => api.macroEvent(eventId!), enabled: scene === 'event' && Boolean(eventId), retry: false })
  const eventFamilies = [...new Set([...(familiesQuery.data ?? []), ...events.map((item) => item.family), ...(family === 'all' ? [] : [family])])]
  useEffect(() => {
    if (scene !== 'event') return
    const closeOnEscape = (keyboardEvent: KeyboardEvent) => { if (keyboardEvent.key === 'Escape') goToDesignBack(params.get('back') ?? undefined, baseScene, { ...navigationFilters, offset: offset ? String(offset) : undefined }, onNavigate) }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [baseScene, onNavigate, params, scene, query, family, offset])
  const listBack = routeHref(baseScene, { ...navigationFilters, offset: offset ? String(offset) : undefined })
  const pageBack = scene === 'event'
    ? params.get('back')?.startsWith('#/') ? params.get('back')! : listBack
    : parentBack ?? routeHref('home')
  return <div className="content-wrap design-state-page"><BreadcrumbBack label={assetBackLabel(pageBack) ?? '返回上一页'} href={pageBack} /><PageHeading eyebrow="市场日历" title="宏观事件日历" copy="官方时间与发布状态独立记录；历史版本保留，修订不会覆盖复核依据。" action={<span className="status-pill neutral">{eventsQuery.isLoading ? '同步中' : `${eventsQuery.data?.total ?? events.length} 项`}</span>} />
    <section className="panel design-calendar-panel">
      <div className="design-tab-row"><button className={baseScene === 'calendar-all' ? 'active' : ''} onClick={() => onNavigate('calendar-all', navigationFilters)}>全部</button><button className={baseScene === 'calendar-upcoming' ? 'active' : ''} onClick={() => onNavigate('calendar-upcoming', navigationFilters)}>即将发生</button><button className={baseScene === 'calendar-history' ? 'active' : ''} onClick={() => onNavigate('calendar-history', navigationFilters)}>历史发布</button></div>
      <div className="design-filter-row"><label className="design-search"><Search size={15} /><input value={query} onChange={(change) => onNavigate(baseScene, { ...navigationFilters, q: change.target.value || undefined })} placeholder="搜索事件、来源或主题…" aria-label="搜索宏观事件" /></label><label>事件类别<select aria-label="事件类别" value={family} onChange={(change) => onNavigate(baseScene, { ...navigationFilters, family: change.target.value === 'all' ? undefined : change.target.value })}><option value="all">全部事件</option>{eventFamilies.map((item) => <option value={item} key={item}>{eventName(item)}</option>)}</select></label><span className="small"><Filter size={13} />来源、前值、预期、实际和意外</span></div>
      <div className="calendar-list-actions"><button type="button" className="button secondary" disabled={eventsQuery.isFetching || familiesQuery.isFetching} onClick={() => { void eventsQuery.refetch(); void familiesQuery.refetch() }}>{eventsQuery.isFetching || familiesQuery.isFetching ? '正在刷新…' : '刷新日历'}</button>{(query || family !== 'all') && <button type="button" className="button secondary" onClick={() => onNavigate(baseScene, { ...(parentBack ? { back: parentBack } : {}) })}>清除筛选</button>}</div>
      {familiesQuery.isError && <div className="workspace-state danger" role="alert">事件类别暂时无法完整读取，当前选项可能不全。可刷新重试；已显示的事件仍可查看。</div>}
      {eventsQuery.isError && <div className="workspace-state danger" role="alert"><AlertTriangle size={17} />事件日历不可用，请刷新重试；当前保留的列表可能不是最新记录。</div>}
      {!eventsQuery.isLoading && !eventsQuery.isError && events.length === 0 && <div className="workspace-state"><CalendarDays size={17} />{query || family !== 'all' ? '没有匹配当前筛选的事件，可清除筛选后查看。' : scope === 'history' ? '当前没有历史事件记录。' : '当前范围没有事件记录。'}</div>}
      {events.length > 0 && <div className="design-event-table"><div className="design-event-head"><span>事件</span><span>发布时间</span><span>状态</span><span>前值 / 预期 / 实际</span><span>来源</span></div>{events.map((item) => <button type="button" className="design-event-row" key={item.macro_event_id} onClick={() => onNavigate('event', { event: item.macro_event_id, scope, ...filters, offset: offset ? String(offset) : undefined, back: listBack })}><span><strong>{eventName(item.family)}</strong></span><span className="mono">{calendarTime(item)}</span><RunEventStatus event={item} /><span className="mono">{item.prior_value ?? '—'} / {item.consensus_value ?? '—'} / {item.actual_value ?? '—'}</span><span><strong>{calendarSource(item.source_id)}</strong><small>{calendarAuthority(item.source_authority)}</small></span><ArrowRight size={14} /></button>)}</div>}
    </section>
    {!eventsQuery.isLoading && !eventsQuery.isError && scene !== 'event' && <HistoryPager offset={offset} count={events.length} total={eventsQuery.data?.total ?? null} onPage={(next) => onNavigate(baseScene, { ...navigationFilters, offset: next ? String(next) : undefined })} />}
    {scene === 'event' && eventQuery.isLoading && <div className="workspace-state" role="status"><Activity size={17} />正在读取事件详情…</div>}
    {scene === 'event' && eventQuery.isError && <StateNotice title="事件详情不可用" detail="按事件 ID 读取失败；没有从当前列表猜测或替换为其他事件。" tone="red" icon={AlertTriangle} />}
    {scene === 'event' && !eventQuery.isLoading && !eventQuery.isError && <EventStateDrawer params={params} onNavigate={onNavigate} event={eventQuery.data ?? null} onClose={() => goToDesignBack(params.get('back') ?? undefined, baseScene, { ...navigationFilters, offset: offset ? String(offset) : undefined }, onNavigate)} />}
  </div>
}

function RunEventStatus({ event }: { event: MacroEventView }) { const tone = event.status === 'released' ? 'success' : event.status === 'cancelled' ? 'danger' : event.revision_flag ? 'warning' : 'neutral'; return <span className={`status-pill ${tone}`}><span className="status-dot" />{calendarStatus(event)}</span> }

function EventStateDrawer({ event, onClose, params, onNavigate }: { event: MacroEventView | null; onClose: () => void; params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const dialogRef = useRef<HTMLElement>(null)
  useDesignDialogFocus(dialogRef)
  return <div className="drawer-backdrop" onClick={onClose}><aside ref={dialogRef} className="run-drawer context-drawer design-event-drawer" role="dialog" aria-modal="true" aria-label="宏观事件详情" onClick={(click) => click.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow"><CalendarDays size={13} />事件档案</span><h2>{event ? eventName(event.family) : '事件不可用'}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭事件详情"><X size={17} /></button></div>{!event ? <StateNotice title="事件不可用" detail="该事件已不在当前事件账本中。" tone="red" icon={AlertTriangle} /> : <><div className="drawer-meta"><RunEventStatus event={event} /><details><summary>核对事件标识</summary><p className="mono">{event.macro_event_id} · {event.family} · {event.source_id} · {event.source_authority}</p></details></div><section className="drawer-section"><h3>发布时间与来源</h3><div className="detail-grid"><div className="detail-cell"><span>计划时间</span><strong>{calendarTime(event)}</strong></div><div className="detail-cell"><span>更新时间</span><strong>{formatDate(event.updated_at)}</strong></div><div className="detail-cell"><span>来源</span><strong>{calendarSource(event.source_id)}</strong></div><div className="detail-cell"><span>来源类型</span><strong>{calendarAuthority(event.source_authority)}</strong></div></div></section><section className="drawer-section"><h3>数值快照</h3><div className="detail-grid"><div className="detail-cell"><span>前值</span><strong>{event.prior_value ?? '—'}</strong></div><div className="detail-cell"><span>预期</span><strong>{event.consensus_value ?? '—'}</strong></div><div className="detail-cell"><span>实际</span><strong>{event.actual_value ?? '待发布'}</strong></div><div className="detail-cell"><span>意外</span><strong>{event.surprise ?? '待发布'}</strong></div></div>{event.revision_flag && <StateNotice title="存在修订版本" detail="历史值不会被当前修订覆盖。" tone="amber" icon={RefreshCw} />}</section>{event.source_url && <section className="drawer-section"><a className="source-link" href={event.source_url} target="_blank" rel="noreferrer">打开官方来源 <ArrowRight size={14} /></a></section>}<EventArchivePanel key={event.macro_event_id} eventId={event.macro_event_id} params={params} onNavigate={onNavigate} /></>}</aside></div>
}

function LibraryStatePage({ params, onNavigate }: { scene: DesignScene; params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const requestedBack = params.get('back')
  const backScene = requestedBack?.split('?')[0]
  const libraryBack = requestedBack && backScene && ['#/evidence', '#/report', '#/report-incomplete', '#/graph', '#/graph-node', '#/technical', '#/library'].includes(backScene) ? requestedBack : routeHref('library')
  const backLabel = libraryBack.startsWith('#/evidence') ? '返回证据详情' : libraryBack.startsWith('#/library') ? '返回资料列表' : '返回研究报告'
  return <><div className="content-wrap library-back"><BreadcrumbBack label={backLabel} href={libraryBack} /></div><ResearchLibraryPanel routeVersionId={params.get('version') ?? params.get('version_id') ?? undefined} onVersionRefresh={(version) => {
    const href = routeHref(stageForVersion(version), { version: version.version_id, back: libraryBack })
    if (window.location.hash !== href) {
      window.history.replaceState(null, '', href)
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    }
  }} onVersionState={(version) => onNavigate(stageForVersion(version), { version: version.version_id, back: libraryBack })} onOpenVersion={(versionId) => onNavigate('document-review', { version: versionId, back: libraryBack })} /></>
}

function SourcesStatePage() {
  const healthQuery = useQuery({ queryKey: ['design-health'], queryFn: api.health, refetchInterval: 10_000, retry: false })
  const operationsQuery = useQuery({ queryKey: ['design-operations'], queryFn: api.operations, refetchInterval: 10_000, retry: false })
  const sources = healthQuery.data?.sources ?? []
  const healthTone = healthQuery.isLoading ? 'neutral' : healthQuery.isError || !healthQuery.data ? 'warning' : healthQuery.data.status === 'degraded' ? 'warning' : 'success'
  const healthLabel = healthQuery.isLoading ? '读取中' : healthQuery.isError ? '未知' : healthQuery.data?.status ?? '未知'
  return <div className="content-wrap design-state-page"><BreadcrumbBack label="返回研究工作台" href={routeHref('home')} /><PageHeading eyebrow="SERVICE & SOURCES" title="服务与数据源" copy="运行健康、来源质量和影响范围分别显示；未知状态不会被渲染成健康。" action={<span className={`status-pill ${healthTone}`}><span className="status-dot" />{healthLabel}</span>} /><div className="design-source-grid"><section className="panel"><header className="panel-header"><div><h2>运行服务</h2><p>Worker 心跳和可用模式</p></div></header><div className="design-source-list"><SourceRow label="Research worker" value={operationsQuery.data?.services.find((item) => item.service_id === 'hub-research-worker')?.status ?? 'unknown'} detail={operationsQuery.data?.runtime.runtime_id ?? '运行时未确认'} /><SourceRow label="Hub API" value={operationsQuery.data?.services.find((item) => item.service_id === 'hub-api')?.status ?? 'unknown'} detail={operationsQuery.data?.runtime.mode ?? 'mode unknown'} /><SourceRow label="研究账本" value="durable" detail="Run、游标和 outbox 持久化" /></div></section><section className="panel"><header className="panel-header"><div><h2>来源采集</h2><p>只显示真实健康响应</p></div></header><div className="design-source-list">{healthQuery.isError && <StateNotice title="健康接口不可用" detail="没有使用缓存数据补写来源状态。" tone="red" icon={AlertTriangle} />}{sources.length === 0 && !healthQuery.isLoading && <StateNotice title="没有来源状态" detail="当前没有可展示的来源健康信息。" tone="amber" icon={CircleHelp} />}{sources.map((source) => <SourceRow key={source.source_id} label={source.source_id} value={source.status} detail={source.error_code ?? '最近检查成功'} />)}</div></section></div></div>
}

function SourceRow({ label, value, detail }: { label: string; value: string; detail: string }) { const tone = value === 'healthy' || value === 'online' || value === 'ok' || value === 'durable' ? 'success' : value === 'degraded' ? 'warning' : 'neutral'; return <div className="design-source-row"><div><strong>{label}</strong><span>{detail}</span></div><span className={`status-pill ${tone}`}>{value}</span></div> }

function StatesStatePage({ onNavigate }: { onNavigate: DesignStatePageProps['onNavigate'] }) {
  const tokens = [['品牌', '#2563EB'], ['背景', '#F3F8FF'], ['主文字', '#122247'], ['次文字', '#516585'], ['成功', '#087A55'], ['警告', '#9C5907'], ['错误', '#BC3049'], ['审计', '#703CD2']]
  const states = [['loading', '读取中', '明确指出正在读取的资源'], ['research_only', '仅供研究', '方向结论不是交易执行授权'], ['no_trade', '暂不交易', '已完成分析但条件不足'], ['uncalibrated', '概率未校准', '不显示伪造置信度'], ['degraded', '来源降级', '显示影响范围和重试性'], ['synthesis_incomplete', '结论不完整', '研究结束不等于论证完整']]
  return <div className="content-wrap design-state-page"><BreadcrumbBack label="返回研究工作台" href={routeHref('home')} /><PageHeading eyebrow="DESIGN SYSTEM" title="组件与状态规范" copy="统一令牌、交互反馈和业务状态边界，作为本项目生产 UI 的验收板。" /><section className="panel pad design-token-panel"><h2>颜色与语义</h2><div className="design-swatches">{tokens.map(([label, color]) => <div key={label}><span style={{ background: color }} /><strong>{label}</strong><small>{color}</small></div>)}</div></section><div className="design-two-col"><section className="panel pad"><h2>控件与反馈</h2><div className="design-demo-actions"><a className="button primary" href={routeHref('compose')}><Sparkles size={14} />开始分析</a><button className="button secondary" onClick={() => onNavigate('evidence', { evidence: 'demo' })}><FileSearch size={14} />查看证据</button><button className="button secondary" disabled>条件未满足</button></div><StateNotice title="危险操作需要明确原因" detail="取消、重试和复查写入同一 Run 的审计轨迹。" tone="amber" icon={ShieldAlert} /></section><section className="panel pad"><h2>可区分的业务状态</h2><div className="design-state-grid">{states.map(([code, label, detail]) => <div key={code}><span className="mono">{code}</span><strong>{label}</strong><p>{detail}</p></div>)}</div></section></div></div>
}

function DeliveryStatePage({ onNavigate }: { onNavigate: DesignStatePageProps['onNavigate'] }) {
  return <div className="content-wrap design-state-page"><BreadcrumbBack label="返回研究工作台" href={routeHref('home')} /><PageHeading eyebrow="DESIGN HANDOFF" title="交付说明" copy="UI Design Package V2 的生产落地边界、接口来源和检查入口。" action={<span className="status-pill success"><span className="status-dot" />V2.1</span>} /><div className="design-two-col"><section className="panel pad"><h2>已接入生产交互</h2><div className="design-state-grid"><div><span className="mono">ROUTES</span><strong>38 个桌面状态</strong><p>首页、研究报告、事件日历、资料流水线、来源状态和 DSH 上下文均有可直达 hash 路由。</p></div><div><span className="mono">DATA</span><strong>真实 API 优先</strong><p>Run、Evidence、事件、资料版本和服务健康来自现有 Hub 合同，失败时显示明确状态。</p></div><div><span className="mono">CONTEXT</span><strong>返回来源保留</strong><p>任务筛选、日历范围、Evidence、图谱节点和 DSH 下钻保存来源路径。</p></div><div><span className="mono">RESPONSIVE</span><strong>移动布局</strong><p>导航、三列首页、事件表、资料步进器和图谱检查器在 900px / 600px 断点收敛。</p></div></div></section><section className="panel pad"><h2>接口边界</h2><StateNotice title="不伪造未定义写接口" detail="资料解析、修正和 Research Run 只调用 contracts-ts 已声明的方法；设计包中没有后端合同的能力不会被客户端臆造。" tone="amber" icon={ShieldAlert} /><div className="design-demo-actions"><button className="button primary" onClick={() => onNavigate('catalog')}><FileSearch size={14} />检查 38 个状态</button><button className="button secondary" onClick={() => onNavigate('sources')}><Network size={14} />查看服务健康</button></div></section></div></div>
}

export const designCatalog: Array<{ id: string; title: string; group: string; copy: string }> = [
  { id: 'home', title: '研究工作台', group: 'Research', copy: '在线、降级、空态和未知服务状态' },
  { id: 'compose', title: '获取最新操作建议', group: 'Research', copy: '输入关注点并提交研究请求' },
  { id: 'submitting', title: '正在提交', group: 'Research', copy: '幂等提交锁定和状态反馈' },
  { id: 'queued', title: '已排队', group: 'Research', copy: '离开页面不会取消任务' },
  { id: 'researching', title: '研究中', group: 'Research', copy: '轮次、工具调用和技术详情入口' },
  { id: 'failed', title: '研究失败', group: 'Research', copy: '保留输入和可审计重试' },
  { id: 'runs', title: 'Research Runs', group: 'Research', copy: '任务、状态、来源和父子关系' },
  { id: 'calendar-all', title: '宏观日历', group: 'Calendar', copy: '全部官方事件' },
  { id: 'calendar-upcoming', title: '即将发生', group: 'Calendar', copy: '未来发布窗口' },
  { id: 'calendar-history', title: '历史发布', group: 'Calendar', copy: '已发布和修订版本' },
  { id: 'event', title: '事件详情', group: 'Calendar', copy: '来源、数值快照和修订提示' },
  { id: 'report', title: '研究报告', group: 'Report', copy: '多周期结论、证据和 Gate' },
  { id: 'report-incomplete', title: '结论不完整', group: 'Report', copy: '研究结束但论证仍有缺口' },
  { id: 'technical', title: '技术详情', group: 'Report', copy: '规范化轨迹和 DSH 入口' },
  { id: 'evidence', title: 'Evidence 详情', group: 'Report', copy: '证据身份、时效和来源' },
  { id: 'graph', title: '决策图谱', group: 'Report', copy: '节点筛选、列表和全屏画布' },
  { id: 'graph-node', title: '图谱节点检查器', group: 'Report', copy: '当前节点上下文和 Evidence 下钻' },
  { id: 'library', title: '资料研究库', group: 'Library', copy: '版本、质量闸门和 Run 关联' },
  { id: 'upload', title: '上传资料', group: 'Library', copy: '登记文件并进入处理流水线' },
  { id: 'document-uploading', title: '资料上传中', group: 'Library', copy: '收到版本回执' },
  { id: 'document-parsing', title: '资料解析中', group: 'Library', copy: '解析完成后进入审核' },
  { id: 'document-quality', title: '资料质量不足', group: 'Library', copy: '阻断问题回到原文定位' },
  { id: 'document-review', title: '语义修正审核', group: 'Library', copy: '逐条处理修正建议' },
  { id: 'document-confirm', title: '确认审核范围', group: 'Library', copy: '显式确认本次审核范围' },
  { id: 'document-confirmed', title: '质量复检中', group: 'Library', copy: '独立质量闸门复检' },
  { id: 'document-ready', title: '可用于研究', group: 'Library', copy: '接入新 Run 的软证据' },
  { id: 'document-linked', title: '资料已连接', group: 'Library', copy: '资料版本和 Run 双向关联' },
  { id: 'document-failed', title: '资料解析失败', group: 'Library', copy: '保留原文件并支持重试' },
  { id: 'home-loading', title: '工作台加载中', group: 'System', copy: '按资源反馈加载状态' },
  { id: 'home-unknown', title: '服务状态未知', group: 'System', copy: '未知不会伪装成健康' },
  { id: 'home-degraded', title: '来源降级', group: 'System', copy: '影响范围和最后成功清晰可见' },
  { id: 'home-empty', title: '暂无研究任务', group: 'System', copy: '真实空账本和下一步行动' },
  { id: 'sources', title: '服务与数据源', group: 'System', copy: '运行服务、来源健康和错误' },
  { id: 'states', title: '组件与状态规范', group: 'System', copy: '令牌、业务状态和交互反馈' },
  { id: 'dsh', title: 'DSH · 技术轨迹', group: 'DSH', copy: '当前 Run 的公开运行摘要' },
  { id: 'dsh-conversation', title: 'DSH · 对话摘要', group: 'DSH', copy: '不含逐字思维链的摘要' },
  { id: 'dsh-research', title: 'DSH · 主动研究', group: 'DSH', copy: '子任务、轮次和工具调用' },
  { id: 'dsh-report', title: 'DSH · 原生报告', group: 'DSH', copy: '技术引用回到 Hub 报告' },
]

function CatalogStatePage({ onNavigate }: { onNavigate: DesignStatePageProps['onNavigate'] }) {
  return <div className="content-wrap design-state-page"><BreadcrumbBack label="返回研究工作台" href={routeHref('home')} /><PageHeading eyebrow="DESIGN CATALOG" title="页面与状态目录" copy="Decision Hub UI Design Package V2 的 38 个桌面页面与状态都从这里可直接检查。" action={<span className="status-pill success"><span className="status-dot" />38 个桌面状态</span>} /><div className="design-catalog-grid">{designCatalog.map((item) => <button type="button" className="design-catalog-card" key={item.id} onClick={() => onNavigate(item.id)}><span className="eyebrow">{item.group}</span><strong>{item.title}</strong><small>{item.id}</small><p>{item.copy}</p><ArrowRight size={15} /></button>)}</div></div>
}

const dshLabels: Record<string, string> = {
  ...technicalLabels,
  admitted: '已接收', queued: '排队中', researching: '研究中', retry_wait: '等待重试',
  completed: '已完成', research_only: '仅研究', rejected: '未形成报告',
  degraded: '有保留', cancelled: '已取消', denied: '已拒绝执行',
  admission: '输入准入', synthesis: '综合判断', gate: '发布检查', monitoring: '持续观察',
}

function dshLabel(value: string, fallback: string): string {
  return dshLabels[value] ?? fallback
}

function DshOriginalRecord({ value }: { value: unknown }) {
  return <details className="dsh-original-record"><summary>查看原始记录</summary><pre className="asset-json">{JSON.stringify(value, null, 2)}</pre></details>
}

function DshStatePage({ scene, params, onNavigate }: { scene: DesignScene; params: URLSearchParams; onNavigate: DesignStatePageProps['onNavigate'] }) {
  const runId = params.get('run') ?? params.get('run_id')
  const runsQuery = useQuery({ queryKey: ['dsh-runs'], queryFn: api.researchRuns, retry: false })
  const selectedRunId = runId ?? runsQuery.data?.[0]?.run_id
  const detailQuery = useQuery({ queryKey: ['dsh-detail', selectedRunId], queryFn: () => api.researchRun(selectedRunId!), enabled: Boolean(selectedRunId), retry: false })
  const detail = detailQuery.data
  const tab = scene === 'dsh-conversation' ? 'conversation' : scene === 'dsh-research' ? 'research' : scene === 'dsh-report' ? 'report' : 'trace'
  const backHref = params.get('back')?.startsWith('#/') ? params.get('back')! : routeHref('technical', { run: selectedRunId })
  const tabParams = { run: selectedRunId, back: params.get('back') ?? undefined }
  const currentHref = routeHref(scene, tabParams)
  const noRun = !runsQuery.isLoading && !runsQuery.isError && !selectedRunId
  return <div className="content-wrap design-state-page">
    <BreadcrumbBack label="返回研究技术详情" href={backHref} />
    <PageHeading eyebrow="DSH 研究记录" title={tab === 'trace' ? 'DSH · 技术轨迹' : tab === 'conversation' ? 'DSH · 对话摘要' : tab === 'research' ? 'DSH · 主动研究' : 'DSH · 原生报告'} copy="查看这次研究的运行记录与报告引用；模型原文按保存内容展示。" action={<span className="status-pill neutral">研究编号 {shortId(selectedRunId)}</span>} />
    <section className="panel dsh-context-panel">
      <div className="design-tab-row">
        <button className={tab === 'trace' ? 'active' : ''} onClick={() => onNavigate('dsh', tabParams)}>技术轨迹</button>
        <button className={tab === 'conversation' ? 'active' : ''} onClick={() => onNavigate('dsh-conversation', tabParams)}>对话摘要</button>
        <button className={tab === 'research' ? 'active' : ''} onClick={() => onNavigate('dsh-research', tabParams)}>主动研究</button>
        <button className={tab === 'report' ? 'active' : ''} onClick={() => onNavigate('dsh-report', tabParams)}>原生报告</button>
      </div>
      {runsQuery.isError && !runId && <div className="workspace-state danger"><AlertTriangle size={17} />研究任务列表不可用，暂时无法选择研究记录。</div>}
      {noRun && <div className="workspace-state"><Search size={17} />暂无可关联的研究任务。<button className="button primary" onClick={() => onNavigate('compose')}>发起研究</button></div>}
      {detailQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取这次研究的 DSH 记录…</div>}
      {detailQuery.isError && <div className="workspace-state danger" role="alert"><AlertTriangle size={17} />这次研究的 DSH 记录暂时不可用。<button className="button secondary" onClick={() => void detailQuery.refetch()}>重新读取记录</button></div>}
      {detail && tab === 'trace' && <div className="dsh-log-list">
        {detail.trace.length === 0 ? <div className="workspace-state"><FileClock size={17} />尚无已保存的运行轨迹。</div> : detail.trace.map((event) => <article key={event.sequence_no}>
          <span className={`trace-mark ${event.status}`} /><div>
            <strong>{researchTraceTitle(event, detail.run?.run_id ?? '', dshLabel(event.event_type, '其他运行记录'))}</strong>
            <small>{dshLabel(event.stage, '其他阶段')} · {dshLabel(event.status, '状态待核对')} · {formatDate(event.occurred_at)}{event.error_code ? ` · ${dshLabel(event.error_code, '存在未完成原因，见原始记录')}` : ''}</small>
            <DshOriginalRecord value={event} />
          </div>
        </article>)}
      </div>}
      {detail && tab === 'conversation' && <div className="dsh-summary-card">
        <StateNotice title="公开运行摘要" detail="研究结论、保留材料与实际调用分别核对。" tone="blue" icon={Network} />
        <p>{detail.action_summary.headline}</p>
        <dl><div><dt>状态</dt><dd>{dshLabel(detail.run.status, '状态待核对')}</dd></div><div><dt>当前阶段</dt><dd>{dshLabel(detail.run.stage, '其他阶段')}</dd></div><div><dt>保留材料</dt><dd>{detail.evidence.length} 条</dd></div><div><dt>已采纳证据</dt><dd>{detail.evidence.filter(item => item.quality === 'accepted').length} 条</dd></div><div><dt>取证调用</dt><dd>{detail.total_tool_calls} 次</dd></div></dl>
        <DshOriginalRecord value={{ run: detail.run, action_summary: detail.action_summary, total_tool_calls: detail.total_tool_calls }} />
      </div>}
      {detail && tab === 'research' && <div className="dsh-log-list">
        {detail.rounds.length === 0 ? <div className="workspace-state"><FileClock size={17} />尚无已保存的研究轮次。</div> : detail.rounds.map((round) => <article key={round.round}><GitBranch size={15} /><div>
          <strong>第 {round.round} 轮研究</strong>
          {round.plan.tasks.length ? <ul>{round.plan.tasks.map((task, index) => <li key={task.task_id ?? index}>{requirementNames[task.requirement_id] ?? '未知要求，请查看原始记录'}</li>)}</ul> : <p>没有结构化任务</p>}
          <small>{round.tool_invocations.length} 条工具调用记录</small>
          <DshOriginalRecord value={round} />
        </div></article>)}
      </div>}
      {detail && tab === 'report' && <div className="dsh-summary-card">
        <StateNotice title="已保存的报告引用" detail="阅读完整结论、证据缺口与复核意见，请打开对应研究报告。" tone="amber" icon={Code2} />
        <p>{detail.action_summary.rationale}</p>
        <DshOriginalRecord value={detail.action_summary} />
        <button className="button primary" onClick={() => onNavigate('report', { run: selectedRunId, back: currentHref })}>打开 Hub 研究报告 <ArrowRight size={14} /></button>
      </div>}
    </section>
  </div>
}
import { EventArchivePanel } from './product/EventArchivePanel'
