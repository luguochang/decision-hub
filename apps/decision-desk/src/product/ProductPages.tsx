import { EventPrevisionPanel } from './EventPrevisionPanel'
import { researchDisplayText } from './researchLanguage'
import { ResearchMarketPanel } from './ResearchMarketPanel'
import { RuntimePanel } from './RuntimePanel'
import { RiskDashboard } from './RiskDashboard'
import { researchServiceHealth } from './operationLabels'
import { calendarDay, calendarInNextDays, calendarTime, eventName } from './calendarTime'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CalendarDays, CircleAlert, Clock3, Database, FileText, History, Layers3, Network, Search, ShieldCheck } from 'lucide-react'
import { lazy, useMemo } from 'react'
import { isResearchActive, researchTaskScene, researchTitle } from './ResearchProgressPage'
import { api } from '../api/client'
import type { MacroEventView, OperationsOverview, ResearchRunView } from '../api/types'

const UsageQueuePanel = lazy(() => import('./UsageQueuePanel').then(module => ({ default: module.UsageQueuePanel })))
const DecisionSettingsPanel = lazy(() => import('./DecisionSettingsPanel').then(module => ({ default: module.DecisionSettingsPanel })))
const PerpetualPaperAccountPanel = lazy(() => import('./PerpetualPaperAccountPanel').then(module => ({ default: module.PerpetualPaperAccountPanel })))

export type ProductScene = 'home' | 'market' | 'research' | 'management' | 'settings'
type Navigate = (scene: string, params?: Record<string, string | undefined>) => void

const at = (value: string | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '暂无记录'
const runStatus: Record<ResearchRunView['status'], string> = {
  admitted: '已接收', queued: '排队中', researching: '研究中', retry_wait: '等待重试', completed: '研究完成', degraded: '受限完成', research_only: '仅供研究', rejected: '未通过审核', failed: '研究失败', cancelled: '已取消',
}
const origin: Record<ResearchRunView['admission_origin'], string> = { manual: '人工发起', automatic: '自动发现', scheduled_recheck: '定时复核', legacy: '历史任务' }
const eventStatus: Record<MacroEventView['status'], string> = { scheduled: '待发布', released: '已发布', revised: '已修订', cancelled: '已取消' }
export { eventName } from './calendarTime'
export const recentRunHeading = researchTitle
export function recentRunDescription(run: ResearchRunView) {
  if (run.status === 'failed') return '本次研究未完成。任务记录中保留了失败原因和可用的恢复动作。'
  if (run.status === 'cancelled') return '本次研究已停止，任务记录和已取得的材料仍保留。'
  if (run.status === 'rejected') return '本次研究未通过报告审核，请在任务记录中核对原因和已保存的材料。'
  if (!run.artifact_id && ['completed', 'degraded', 'research_only'].includes(run.status)) return '任务已结束，但没有已保存的报告。请在任务记录中查看结果和限制。'
  if (run.status === 'degraded') return '部分来源或工具不可用，本次结果受到限制。请在报告中核对证据和缺口。'
  if (run.status === 'research_only') return '已保存研究结果，但仍有证据或执行限制。请在报告中核对适用边界。'
  if (['admitted', 'queued', 'researching', 'retry_wait'].includes(run.status)) return '任务仍在进行；完成前不会把候选内容当作结论。'
  return '报告已保存。请阅读主要依据、反方证据和下一次复核条件。'
}

function SectionLink({ label, onClick }: { label: string; onClick: () => void }) { return <button className="product-link" type="button" onClick={onClick}>{label}<ArrowRight size={15} /></button> }
function Notice({ title, detail, danger = false }: { title: string; detail: string; danger?: boolean }) { return <div className={`product-notice ${danger ? 'danger' : ''}`}><CircleAlert size={18} /><div><strong>{title}</strong><p>{detail}</p></div></div> }
function serviceStatus(operations: OperationsOverview | undefined, role: OperationsOverview['services'][number]['role']) {
  if (role === 'research_worker') return !operations ? '状态未确认' : researchServiceHealth(operations.services).complete ? '已就绪' : '需检查'
  const services = operations?.services.filter((item) => item.role === role) ?? []
  if (!operations || services.length === 0) return '状态未确认'
  return services.every((item) => item.status === 'online') ? '已就绪' : '需检查'
}

export function ProductPages({ scene, params, onNavigate }: { scene: ProductScene; params?: URLSearchParams; onNavigate: Navigate }) {
  const operationsQuery = useQuery({ queryKey: ['product-operations'], queryFn: api.operations, retry: false, refetchInterval: 10_000 })
  const runsQuery = useQuery({ queryKey: ['product-recent-runs'], queryFn: api.researchRecentRuns, retry: false, refetchInterval: 30_000, enabled: scene === 'research' })
  const homeQuery = useQuery({ queryKey: ['product-home'], queryFn: api.researchHome, retry: false, refetchInterval: 30_000, enabled: scene === 'home' })
  const eventsQuery = useQuery({ queryKey: ['product-events', scene], queryFn: () => api.macroEvents(scene === 'home' ? 'upcoming' : 'all'), retry: false, refetchInterval: 15 * 60_000, enabled: scene === 'home' || scene === 'market' })
  const requestedRunId = scene === 'home' ? params?.get('run') : null
  const requestedRunQuery = useQuery({ queryKey: ['product-home-run', requestedRunId], queryFn: () => api.researchRun(requestedRunId!), retry: false, refetchInterval: 5_000, enabled: Boolean(requestedRunId) })
  const runs = useMemo(() => [...(runsQuery.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [runsQuery.data])
  const events = eventsQuery.data ?? []
  const operations = operationsQuery.isError ? undefined : operationsQuery.data

  if (scene === 'settings') return <div className="content-wrap product-page"><div className="product-heading"><div><h1>仓位与风险设置</h1><p>保存后回读服务端原件，比较更新状态单独核对。</p></div><button className="button secondary" onClick={() => onNavigate('home')}>返回今日研究</button></div><DecisionSettingsPanel /><PerpetualPaperAccountPanel /></div>

  if (scene === 'home') {
    const latest = requestedRunId ? requestedRunQuery.data?.run : homeQuery.data?.latest_report ?? homeQuery.data?.latest_run
    const active = homeQuery.data?.active_runs ?? []
    const originalHeadline = !requestedRunId ? homeQuery.data?.latest_report_headline : null
    const preview = !requestedRunId ? homeQuery.data?.latest_report_preview : null
    const previewMatches = preview && preview.run_id === latest?.run_id && preview.artifact_id === latest?.artifact_id && ['synthesis', 'perpetual', 'legacy', 'unverified', 'unavailable'].includes(preview.kind)
    const reportHeadline = !requestedRunId && homeQuery.data?.latest_report ? previewMatches ? preview.text : '旧版本报告摘要：综合判断来源未确认，原文仅作参考。' : null
    const perpetualReport = Boolean(!requestedRunId && previewMatches && preview.kind === 'perpetual' && latest?.research_profile === 'perpetual_decision_v2')
    const perpetualUnverified = Boolean(!requestedRunId && previewMatches && preview.kind === 'unverified' && latest?.research_profile === 'perpetual_decision_v2')
    const incompleteReport = Boolean(!requestedRunId && previewMatches && (preview.kind === 'unavailable' || perpetualUnverified))
    const previousReportRun = incompleteReport && latest?.parent_run_id && latest.parent_run_id !== latest.run_id ? latest.parent_run_id : null
    const homeBack = `#/home${params?.toString() ? `?${params.toString()}` : ''}`
    const latestAt = requestedRunId || !homeQuery.data?.latest_report ? latest?.updated_at : homeQuery.data.latest_report_at
    const nextEvent = events.filter((item) => item.status === 'scheduled').sort((a, b) => calendarDay(a).localeCompare(calendarDay(b)))[0]
    const sources = operations?.sources ?? []
    const lastSuccess = sources.map((item) => item.last_success_at).filter((item): item is string => Boolean(item)).sort().at(-1)
    const nextPoll = sources.map((item) => item.next_poll_at).filter((item): item is string => Boolean(item)).sort()[0]
    const sourceFailures = sources.filter((item) => item.status !== 'healthy' || item.consecutive_failures > 0)
    const researchServiceStatus = serviceStatus(operations, 'research_worker')
    const researchExecutionStatus = homeQuery.isLoading || homeQuery.isError || !homeQuery.data ? '状态未确认'
      : researchServiceStatus !== '已就绪' ? researchServiceStatus
      : homeQuery.data.active_count === 0 ? '已就绪'
      : active.some((item) => item.status === 'researching') ? '正在研究'
      : homeQuery.data.active_count > active.length ? '有进行中的研究' : '有研究任务待执行'
    return <div className="content-wrap product-page">
      <section className="product-card" aria-label="BTC/ETH 永续事前建议">
        <div className="product-card-head"><h2>BTC/ETH 永续事前建议</h2><span>4 小时 · 当前账户方案</span></div>
        <p>选择账户与合约，保存仓位和风险设置后，一键发起 4 小时研究。研究使用已保存设置，无需填写研究问题。</p>
        <button className="button primary" type="button" onClick={() => onNavigate('settings')}>查看已保存设置并研究<ArrowRight size={15} /></button>
        <p className="product-muted">研究排队不代表建议已发布。下方现货观察与历史报告按各自原协议展示。</p>
      </section>
      <div className="product-heading"><div><span className="product-kicker">加密资产 · 宏观持续研究</span><h1>今日研究</h1><p>先看系统最近检查了什么、研究到了哪里，再决定是否需要介入。</p></div><button className="button secondary" onClick={() => onNavigate('compose', { back: `#/${scene}${params?.toString() ? `?${params.toString()}` : ''}` })}>补充研究（BTC 模板）</button></div>
      <EventPrevisionPanel key={params?.get('event_key') ?? 'upcoming'} eventKey={params?.get('event_key')} />
      <SectionLink label="我的仓位与风险设置" onClick={() => onNavigate('settings')} />
      <RiskDashboard onNavigate={onNavigate} />
      <section className="product-monitor" aria-label="监控状态"><div><span>来源检查</span><strong>{operationsQuery.isLoading ? '正在读取来源状态' : operationsQuery.isError ? '状态未确认' : sourceFailures.length ? `${sourceFailures.length} 个来源需检查` : sources.length ? '最近采集正常' : '尚无来源状态'}</strong><small>最近成功：{at(lastSuccess)}{nextPoll ? ` · 下次计划：${at(nextPoll)}` : ''}</small></div><div><span>自动发现</span><strong>{serviceStatus(operations, 'realtime_worker')}</strong><small>发现进程与研究进程分别核对</small></div><div><span>研究执行</span><strong>{researchExecutionStatus}</strong><small>API、自动通道与用户通道分别核对</small></div><SectionLink label="查看完整状态" onClick={() => onNavigate('management')} /></section>
      {(operationsQuery.isError || homeQuery.isError || eventsQuery.isError) && <Notice danger title="部分实时信息暂不可用" detail="请在管理与审计查看具体链路；已保存的研究不会被伪装成今天的新发现。" />}
      {!eventsQuery.isLoading && !eventsQuery.isError && (!nextEvent || !calendarInNextDays(nextEvent, 30)) && <Notice title="近期事件覆盖不足" detail="当前日历没有未来 30 天内可确认的官方发布。来源运行中不代表日历完整，请先在来源状态核对采集覆盖。" />}
      {requestedRunQuery.isError && <Notice danger title="指定的研究任务不可用" detail="这个链接未找到对应任务。请从完整任务记录重新查找；页面不会替换成另一份报告。" />}
      {homeQuery.isLoading || requestedRunQuery.isLoading ? <div className="product-empty">正在读取研究账本…</div> : latest ? <section className="product-hero"><div className="product-hero-label"><span>{requestedRunId ? '指定的研究任务' : homeQuery.data?.latest_report ? incompleteReport ? '最近一次研究的保留材料' : '最近已保存的研究' : '最近的研究任务'}</span><span>{origin[latest.admission_origin]} · {at(latestAt)}</span></div><h2 className="product-home-report-title">{reportHeadline ? perpetualReport ? '最近已保存的永续事前研究' : perpetualUnverified ? '永续研究原件暂不可核验' : incompleteReport ? '本次未形成可用的综合判断' : '最近已保存的研究结论' : recentRunHeading(latest, requestedRunQuery.data?.research_focus)}</h2>{reportHeadline && <><p className="product-muted product-report-scope">{perpetualReport ? '这是已保存的永续事前研究记录；结论、场景概率、独立审核与适用限制以原报告核对结果为准。' : perpetualUnverified ? '已找到永续研究记录，但发布引用暂不可核验；请进入报告页核对原件。' : incompleteReport ? '这是项目最近保存的研究材料，尚未形成可用的综合判断；资产范围以原研究任务为准。' : '这是项目最近已保存的研究结论，资产范围以原研究任务为准。'}</p>{latest.research_profile !== 'perpetual_decision_v2' && <p className="product-report-task">原研究任务：{latest.event_title}</p>}<p className="product-report-preview" title="已保存的判断预览，完整条件见研究详情">{researchDisplayText(reportHeadline)}</p>{originalHeadline !== null && originalHeadline !== undefined && <details className="product-report-original"><summary>查看原报告摘要（非综合判断）</summary><p>{originalHeadline}</p></details>}<details className="product-report-question"><summary>查看任务标题</summary><p>{latest.event_title}</p></details></>}{Date.now() - new Date(latestAt ?? latest.updated_at).getTime() > 86_400_000 && <p className="product-muted">这份研究距今已超过 24 小时，请核对报告时间及当前适用性。</p>}<p>{incompleteReport ? '已保存的材料和未完成原因仍可查看。需要对照上一次研究时，可打开关联的上一版记录。' : recentRunDescription(latest)}</p><div className="product-hero-foot"><span className="product-status">{perpetualReport ? '已保存，待核对原报告' : perpetualUnverified ? '原件暂不可核验' : incompleteReport ? '未形成综合判断' : runStatus[latest.status]}</span><button className="button primary" onClick={() => onNavigate(researchTaskScene(latest.status, Boolean(latest.artifact_id)), { run: latest.run_id, back: homeBack })}>{perpetualReport ? '打开原报告核对' : incompleteReport ? '查看材料与限制' : latest.status === 'failed' ? '查看原因与恢复' : isResearchActive(latest.status) ? '查看研究进度' : latest.artifact_id ? '阅读结论与依据' : '查看任务详情'}<ArrowRight size={15} /></button>{previousReportRun && <button className="button secondary" onClick={() => onNavigate('report', { run: previousReportRun, back: homeBack })}>查看上一版研究</button>}<button className="button secondary" onClick={() => onNavigate('runs', { back: `#/${scene}` })}>查看任务记录</button></div></section> : !requestedRunId && !homeQuery.isError && <div className="product-empty"><h2>还没有研究任务</h2><p>监控状态和官方事件在下方可见；当系统发现符合准入条件的变化，研究任务会进入这里。</p></div>}
      <div className="product-grid"><section className="product-card"><div className="product-card-head"><h2>系统正在做什么</h2>{homeQuery.data && homeQuery.data.active_count > 3 && <span>共 {homeQuery.data.active_count} 项，显示最近 3 项</span>}<SectionLink label="全部任务" onClick={() => onNavigate('runs', { back: `#/${scene}` })} /></div>{homeQuery.isLoading ? <p className="product-muted">正在读取进行中的研究…</p> : homeQuery.isError ? <p className="product-muted">进行中的研究暂时无法确认，请稍后重试。</p> : active.length ? active.map((item) => <button className="product-row" key={item.run_id} onClick={() => onNavigate(researchTaskScene(item.status, Boolean(item.artifact_id)), { run: item.run_id, back: `#/${scene}` })}><div><strong>{recentRunHeading(item)}</strong><small>{origin[item.admission_origin]} · 创建 <time dateTime={item.created_at}>{at(item.created_at)}</time> · 最近更新 <time dateTime={item.updated_at}>{at(item.updated_at)}</time></small></div><span>{runStatus[item.status]}</span></button>) : <p className="product-muted">目前没有进行中的研究。历史任务仍可在“研究”中查看。</p>}</section><section className="product-card"><div className="product-card-head"><h2>接下来关注</h2><SectionLink label="事件日历" onClick={() => onNavigate('market')} /></div>{eventsQuery.isLoading ? <p className="product-muted">正在读取后续事件…</p> : eventsQuery.isError ? <p className="product-muted">后续事件暂时无法确认，请稍后重试。</p> : nextEvent ? <button className="product-row" onClick={() => onNavigate('event', { event: nextEvent.macro_event_id })}><div><strong>{eventName(nextEvent.family)}</strong><small>计划发布时间：{calendarTime(nextEvent)}</small></div><span>查看<ArrowRight size={14} /></span></button> : <p className="product-muted">当前日历没有可确认的下一项官方发布。</p>}</section></div>
    </div>
  }

  if (scene === 'market') return <div className="content-wrap product-page"><div className="product-heading"><div><span className="product-kicker">加密资产与全球宏观</span><h1>市场与事件</h1><p>先查看关键事件公布前的概率、传导和条件机会，再跟踪发布与修订。</p></div><SectionLink label="完整日历与筛选" onClick={() => onNavigate('calendar-all', { back: `#/${scene}` })} /></div><EventPrevisionPanel key={params?.get('event_key') ?? 'upcoming'} eventKey={params?.get('event_key')} /><div className="product-grid"><section className="product-card"><div className="product-card-head"><h2>官方事件</h2><span>{eventsQuery.isLoading ? '读取中' : `先展示 ${Math.min(events.length, 12)} 项`}</span></div>{eventsQuery.isError && <Notice danger title="事件暂不可用" detail="请稍后重试或查看来源状态。" />}{events.length ? events.slice(0, 12).map((item) => <button className="product-row" key={item.macro_event_id} onClick={() => onNavigate('event', { event: item.macro_event_id })}><div><strong>{eventName(item.family)}</strong><small>计划时间：{calendarTime(item)}</small></div><span>{eventStatus[item.status]}</span></button>) : !eventsQuery.isError && !eventsQuery.isLoading && <p className="product-muted">当前范围没有可显示的事件记录。</p>}</section><section className="product-card"><h2>数据覆盖</h2><SectionLink label="查看指标与依据" onClick={() => onNavigate('facts', { back: '#/market', as_of: new Date().toISOString() })} /><p className="product-muted">各资产的开通状态见下方范围卡片。黄金需要独立报价、交易日历、数据权限和效果验证。</p><div className="product-timeline"><span><CalendarDays size={17} />计划发布</span><span><Search size={17} />来源采集与核查</span><span><Layers3 size={17} />研究任务与报告</span></div><SectionLink label="来源状态" onClick={() => onNavigate('sources', { back: `#/${scene}` })} /></section></div><ResearchMarketPanel /></div>

  if (scene === 'research') return <div className="content-wrap product-page"><div className="product-heading"><div><span className="product-kicker">结论 · 任务 · 结果</span><h1>研究</h1><p>查看已保存报告、每次任务和到期结果。研究结论不自动授予交易权限。</p></div><button className="button secondary" onClick={() => onNavigate('compose', { back: `#/${scene}${params?.toString() ? `?${params.toString()}` : ''}` })}>发起一次研究</button></div><div className="product-grid"><section className="product-card"><div className="product-card-head"><h2>最近报告与任务</h2><SectionLink label="完整任务记录" onClick={() => onNavigate('runs', { back: `#/${scene}` })} /></div>{runsQuery.isLoading && <p className="product-muted" role="status">正在读取报告与任务…</p>}{runsQuery.isError && <Notice danger title="研究账本暂不可用" detail="无法读取任务状态，请在系统状态查看原因。" />}{runs.slice(0, 8).map((item) => <button className="product-row" key={item.run_id} onClick={() => onNavigate(researchTaskScene(item.status, Boolean(item.artifact_id)), { run: item.run_id, back: `#/${scene}` })}><div><strong>{recentRunHeading(item)}</strong><small>{origin[item.admission_origin]} · 创建 <time dateTime={item.created_at}>{at(item.created_at)}</time> · 最近更新 <time dateTime={item.updated_at}>{at(item.updated_at)}</time></small></div><span>{runStatus[item.status]}</span></button>)}{!runsQuery.isLoading && !runsQuery.isError && !runs.length && <p className="product-muted">目前没有可查看的研究记录。</p>}</section><section className="product-card"><h2>结果跟踪</h2><p className="product-muted">方向与操作概率先显示估计依据和样本不足，再用到期结果验证；未校准估计不能当作已证明的成功率。</p><div className="product-action-stack"><SectionLink label="报告收件箱与反馈" onClick={() => onNavigate('delivery')} /><SectionLink label="查看研究主题与变化" onClick={() => onNavigate('studies', { view: 'research', back: '#/research' })} /><SectionLink label="查看指标与依据" onClick={() => onNavigate('facts', { back: `#/${scene}`, as_of: new Date().toISOString() })} /><SectionLink label="查看结果跟踪" onClick={() => onNavigate('results', { back: '#/research' })} /><SectionLink label="查看任务与关联报告" onClick={() => onNavigate('runs', { back: `#/${scene}` })} /><SectionLink label="旧业务账本" onClick={() => onNavigate('Inbox')} /></div></section></div></div>

  return <div className="content-wrap product-page"><div className="product-heading"><div><span className="product-kicker">运行 · 数据 · 审计</span><h1>管理与审计</h1><p>保留现有能力和历史入口，按服务、来源、数据和业务结果分别核对。</p></div></div>{operationsQuery.isError && <Notice danger title="运行概况暂不可用" detail="请检查 Hub API；页面不会把未知状态显示为正常。" />}<div className="product-grid product-management-grid"><section className="product-card"><Network size={20} /><h2>服务与来源</h2><p>发现、研究执行、授权来源和最近成功检查。</p><strong>{serviceStatus(operations, 'research_worker')}</strong><SectionLink label="查看来源与服务" onClick={() => onNavigate('sources', { back: `#/${scene}` })} /></section><section className="product-card"><Database size={20} /><h2>数据更新与覆盖</h2><p>历史行情作业与失败补采需要单独检查。</p><SectionLink label="查看指标与依据" onClick={() => onNavigate('facts', { back: `#/${scene}`, as_of: new Date().toISOString() })} /><SectionLink label="查看数据更新记录" onClick={() => onNavigate('data-jobs')} /><SectionLink label="历史数据与预测能力" onClick={() => onNavigate('history-status')} /></section><section className="product-card"><History size={20} /><h2>业务账本</h2><p>保留事件、预测、审核和旧业务账本的完整记录。</p><SectionLink label="打开业务账本" onClick={() => onNavigate('Inbox')} /></section><section className="product-card"><ShieldCheck size={20} /><h2>演进与审核</h2><p>候选、评测、负责人审核及晋级命令仍可访问。</p><SectionLink label="打开演进工作台" onClick={() => onNavigate('Evolution')} /></section><section className="product-card"><FileText size={20} /><h2>研究任务</h2><p>每次人工发起、自动发现和定时复核都有独立记录。</p><SectionLink label="查看任务记录" onClick={() => onNavigate('runs', { back: `#/${scene}` })} /></section><section className="product-card"><Clock3 size={20} /><h2>设计与技术资料</h2><p>组件状态、页面目录和原生研究轨迹保留深链。</p><SectionLink label="查看页面目录" onClick={() => onNavigate('catalog')} /></section></div><SectionLink label="我的仓位与风险设置" onClick={() => onNavigate('settings')} /><UsageQueuePanel params={params} onNavigate={onNavigate} /><RuntimePanel /></div>
}
