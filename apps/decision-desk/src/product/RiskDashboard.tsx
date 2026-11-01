import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { riskApi, riskRequestId, riskReceiptIntent, clearRiskRequestId, riskErrorMessage, type RiskScope, type RiskSummaryView, type RiskTimelineView, type RiskMonitorStatus } from '../api/risk'
import './risk.css'
import { RiskResearch } from './RiskResearch'
import { ActionPlanPanel } from './ActionPlanPanel'
import { RiskAlertRecheck } from './RiskAlertRecheck'
import { RiskExposure } from './RiskExposure'
import { RiskPathScore } from './RiskPathScore'
import { MarketMemory } from './MarketMemory'
import { ResearchQuality } from './ResearchQuality'
import { MarketEnhancements } from './MarketEnhancements'
import { ForecastAlarmPanel } from './ForecastAlarmPanel'
import { ApiRequestError } from '../api/client'
import { readHistorySchedule, type HistoryHourlyStatusV2 } from '../api/historySchedule'
import { HISTORY_SCOPES, type HistoryProductStatusV2 } from '../api/historyStatus'
import { historyForecastStatusV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { HistoryForecastRecord } from './HistoryForecastRecord'
import './HistoryStatusPage.css'

type Navigate = (scene: string, params?: Record<string, string | undefined>) => void
const time = (value: string | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '尚无记录'
const number = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value)
const percentage = (value: number | null) => value === null ? '尚未量化' : `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)}%`
const quality = { fresh: '数据按时更新', stale: '数据已陈旧', missing: '数据不足', invalid: '数据未通过校验' }
const severity = { info: '变化提示', warning: '风险提示', critical: '重要风险提示' }
const monitorState = { active: '正在监测', paused: '已暂停', degraded: '监测受限', expired: '已到期', unknown: '状态未确认' }
const truth = { true: '变化条件已触发', false: '变化条件未触发，不等于市场安全', unknown: '条件尚无法判断', error: '本次检查失败' }
function observedValue(value: number | null, unit: string, absolute = false): string {
  if (value === null) return '尚未确认'
  const amount = absolute ? Math.abs(value) : value
  if (unit === 'fraction') return percentage(amount * 100)
  if (unit === '%') return percentage(amount)
  if (unit === 'USDT') return `${number(amount)} USDT`
  if (unit === 'bps' || unit === 'bp') return `${number(amount)} 基点`
  return '数值单位尚未核实，请查看审计记录'
}

const homeHistoryScopes = {
  'crypto:spot:okx:BTC-USDT:trade:1m': 'crypto:spot:okx:BTC-USDT:trade:1m',
  'crypto:spot:okx:ETH-USDT:trade:1m': 'crypto:spot:okx:ETH-USDT:trade:1m',
} as const
const hourlyStates: Record<HistoryHourlyStatusV2['scopes'][number]['state'], string> = {
  not_configured: '小时流程尚未配置', not_due: '等待首次小时记录', reserved: '本小时执行中，结果待确认',
  blocked: '该小时条件不足', committed_verified: '已保存，提交时核验通过',
  committed_readback_unconfirmed: '已保存，回执待确认', unconfirmed: '执行状态尚未确认',
}
function historyAuthenticationRejected(error: unknown) {
  return error instanceof ApiRequestError && (error.status === 401
    || (error.status === 403 && error.message === 'history_owner_session_rejected'))
}

/** Homepage entry reads only fast saved metadata. The existing explicit reader owns original verification. */
export function HomeHistoryForecast({ scopeId, onNavigate }: { scopeId: string; onNavigate: Navigate }) {
  const historyScope = Object.prototype.hasOwnProperty.call(homeHistoryScopes, scopeId)
    ? homeHistoryScopes[scopeId as keyof typeof homeHistoryScopes] : null
  const query = useQuery({ queryKey: ['home-history-hourly-status-v2', historyScope],
    queryFn: ({ signal }) => readHistorySchedule(signal), enabled: Boolean(historyScope),
    retry: false, staleTime: 0, refetchOnWindowFocus: false, refetchIntervalInBackground: false,
    refetchInterval: state => historyAuthenticationRejected(state.state.error) ? false : 60_000 })
  const status = query.isError ? undefined : query.data
  const row = status?.scopes.find(item => item.scope_id === historyScope)
  const metadata: HistoryProductStatusV2['forecast'] | null = row
    && ['committed_verified', 'committed_readback_unconfirmed', 'unconfirmed'].includes(row.state)
    && row.forecast_id && row.cutoff_at ? historyForecastStatusV2Schema.parse({
      state: 'verification_required', forecast_id: row.forecast_id, scope_id: row.scope_id, cutoff_at: row.cutoff_at,
      threshold: null, baseline_n: null, conditional_n: null, probabilities: null,
      blocked_reason_label: '这条已保存记录尚未在本次读取核验原件。',
    }) : null
  const recover = () => onNavigate('history-status', { scope: historyScope ?? undefined, focus: 'hourly', back: '#/home' })
  if (!historyScope) return null
  return <section className="product-card history-section" aria-label="当前资产小时风险记录">
    <div className="product-card-head"><h2>{HISTORY_SCOPES[historyScope]} · 小时风险记录</h2>
      <button className="button secondary" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新本小时记录</button></div>
    <p>先查看已保存的本小时执行情况，再主动核验这条记录的模型与分钟原件。每分钟只更新快状态，不生成预测，不自动读取概率。</p>
    {query.isPending && <p role="status">正在读取当前资产的小时执行记录…</p>}
    {query.isError && <p role="alert">{historyAuthenticationRejected(query.error)
      ? '本机验证已失效，已隐藏记录与风险数值。请到历史能力页恢复本机验证。'
      : query.error instanceof ApiRequestError && query.error.status === 403
        ? '当前记录没有读取授权，已隐藏记录与风险数值。重新验证不会补齐读取授权，请检查本机历史能力配置。'
      : query.error instanceof ApiRequestError && query.error.status === 503
        ? '小时能力服务尚未配置，当前没有可确认的预测记录。请检查本机启动与历史能力状态。'
        : '小时快状态读取失败，当前记录与风险数值已隐藏。请刷新或核对历史能力状态。'}</p>}
    {row && <>
      <strong>{hourlyStates[row.state]}</strong>
      <p>最近小时槽：{time(row.hour_at)} · 观察截点：{time(row.cutoff_at)}（北京时间）。</p>
      {row.hour_at && Math.floor(Date.parse(row.hour_at) / 3_600_000) !== Math.floor(Date.parse(status!.observed_at) / 3_600_000)
        && <p className="risk-warning">这是此前小时的记录，不能代替当前小时的执行结果。</p>}
      <p>{row.reason_label}</p>
      {row.fixture_mode && <p className="risk-warning">测试数据记录，不能作为真实市场预测。</p>}
      <p className="product-muted">快状态只说明当时是否保存及核验；风险频率以本次原件核验结果为准。金融有效性与历史时点可用性尚未通过。</p>
      {!metadata && <p>当前没有可读取的已保存预测身份与观察截点。不会使用其他资产或旧试验的记录代替。</p>}
    </>}
    {metadata && <HistoryForecastRecord
      key={`${scopeId}:${metadata.forecast_id}:${metadata.cutoff_at}`}
      forecast={metadata} statusStale={query.isError} authRevision={0} onAuthenticationRequired={recover} />}
    <button className="product-link" onClick={recover}>{historyAuthenticationRejected(query.error) ? '恢复本机验证并返回首页' : '查看本机验证与完整历史流程'}</button>
  </section>
}

export function RiskSummary({ view, loading, failed, onNavigate }: { view?: RiskSummaryView; loading: boolean; failed: boolean; onNavigate: Navigate }) {
  if (!view) return <section className="product-card" aria-label="资产风险摘要"><h2>变化条件与风险</h2><p>{loading ? '正在读取当前资产的观察结果…' : '风险状态未确认。尚无可核验的行情路径与量化判断。'}</p></section>
  const level = failed || view.data_status !== 'fresh' ? 'unknown' : view.risk_level
  const label = { unknown: '未来风险待评估', normal: '当前已量化条件正常', elevated: '出现值得关注的变化', high: '已触发重要风险条件' }[level]
  return <section className="product-card" aria-label="资产风险摘要">
    <div className="product-card-head"><h2>变化条件与风险</h2><span className={`risk-badge risk-${level}`}>{label}</span></div>
    <h3>{view.headline}</h3><p>{view.explanation}</p>
    {failed && <p role="alert" className="risk-warning">刷新失败；下方保留上次读取结果，当前风险状态未确认。</p>}
    <div className="risk-metrics">
      <div><span>最近一分钟收盘</span><strong>{view.quote ? `${number(Number(view.quote.price))} USDT` : '暂无可核验价格'}</strong><small>{view.quote ? `${view.quote.source_name} · ${time(view.quote.quoted_at)}` : '缺少行情时不会填入示例价格'}</small></div>
      <div><span>24 小时涨跌</span><strong>{view.quote?.change_24h_pct === null || !view.quote ? '未覆盖' : percentage(view.quote.change_24h_pct)}</strong><small>分钟收盘价，非实时成交报价</small></div>
      <div><span>24小时内相对起点最大下跌</span><strong>{percentage(view.window?.max_adverse_move_pct ?? null)}</strong><small>固定起点，非高点回撤；不代表未来损失上限</small></div>
      <div><span>分钟路径覆盖</span><strong>{view.window ? `${view.window.samples} / ${view.window.expected_samples}` : '未覆盖'}</strong><small>{quality[view.data_status]}{view.window ? ` · ${percentage(view.window.coverage_ratio * 100)}` : ''}</small></div>
    </div>
    <div className="risk-research"><h3>研究依据与主要反方</h3><p>{view.research.artifact_id ? '已保存本资产的研究材料；综合判断是否完整、研究质量及量化风险资格，请打开原报告核验。' : view.research.summary ?? '当前没有可用于这个资产的综合研究判断。'}</p>
      {view.research.status !== 'qualified' && <p className="risk-warning">{view.research.status === 'legacy_unquantified' ? '现有报告尚未提供量化路径风险。' : view.research.status === 'partial' ? '研究资格尚未核验或仍有缺口，量化风险资格尚未确认。' : '当前尚无具备量化风险资格的研究。'}</p>}
      <p>{view.strongest_counterargument ?? (view.research.artifact_id ? '首页摘要尚未读取报告中的反方内容，请打开研究报告核对。' : view.research.run_id ? '关联研究尚未形成可供首页核对的反方内容。' : '当前尚无关联研究，反方内容未评估。')}</p>
      {view.research.run_id && <button type="button" className="button secondary" onClick={() => onNavigate(view.research.artifact_id ? 'report' : 'progress', { run: view.research.run_id!, back: '#/home' })}>{view.research.artifact_id ? '阅读研究报告与限制' : '查看关联研究进度'}</button>}
    </div>
    <small className="product-muted">结果读取时间：{time(view.as_of)}（北京时间）</small>
  </section>
}

// Never bridge a missing minute or a declared missing range with a price line.
export function riskPathSegments(view: RiskTimelineView): { at: string; price: string }[][] {
  const segments: { at: string; price: string }[][] = []
  let current: { at: string; price: string }[] = []
  for (const point of view.points) {
    const previous = current.at(-1)
    const gap = previous && ((view.aggregation?.method !== 'extreme_samples' && Date.parse(point.at) - Date.parse(previous.at) > 60_000) || view.missing_ranges.some(range => Date.parse(range.start_at) < Date.parse(point.at) && Date.parse(range.end_at) > Date.parse(previous.at)))
    if (gap) { segments.push(current); current = [] }
    current.push(point)
  }
  if (current.length) segments.push(current)
  return segments
}

export function RiskTimeline({ view, loading, failed, onAlert, onNavigate }: { view?: RiskTimelineView; loading: boolean; failed: boolean; onAlert: (alertId: string) => void; onNavigate?: Navigate }) {
  const values = view?.points.flatMap(point => [Number(point.low ?? point.price), Number(point.high ?? point.price)]) ?? []
  const low = values.length ? Math.min(...values) : 0
  const high = values.length ? Math.max(...values) : 1
  const start = view ? Date.parse(view.start) : 0
  const end = view ? Date.parse(view.end) : 1
  const x = (at: string) => 20 + 620 * (Date.parse(at) - start) / Math.max(end - start, 1)
  const y = (price: string) => 145 - 115 * (Number(price) - low) / Math.max(high - low, high * .00001, .00001)
  return <section className="product-card" aria-label="分钟价格路径">
    <div className="product-card-head"><h2>分钟价格与关键记录</h2><span>{failed ? '读取失败' : view ? view.data_status === 'fresh' && view.missing_ranges.length ? '存在分钟数据缺口' : quality[view.data_status] : loading ? '读取中' : '数据未确认'}</span></div>
    {failed && <p role="alert">当前路径暂不可用；保留的历史曲线不能代表最新行情。</p>}
    {view && <p className="product-muted">曲线核对时间：{time(view.as_of)}（北京时间）。{view.aggregation ? `已核对 ${view.aggregation.source_points} 根分钟原件；` : ''}更新时间不代表窗口数据完整。</p>}
    {!view?.points.length ? <p>{loading ? '正在读取分钟路径…' : '尚无完整可核验的分钟价格路径。'}</p> : <>
      <svg className="risk-path" viewBox="0 0 660 180" role="img" aria-label="一分钟收盘价格曲线，缺口处断开">
        <line x1="20" y1="145" x2="640" y2="145" stroke="#dce6f1" />
        {view.points.filter(point => point.low !== undefined && point.high !== undefined).map(point => <line key={point.at} x1={x(point.at)} x2={x(point.at)} y1={y(point.low!)} y2={y(point.high!)} stroke="#9ab7d5" opacity=".6" />)}
        {riskPathSegments(view).map((segment, index) => segment.length > 1 ? <polyline key={index} fill="none" stroke="#32669b" strokeWidth="2" points={segment.map(point => `${x(point.at)},${y(point.price)}`).join(' ')} /> : <circle key={index} cx={x(segment[0].at)} cy={y(segment[0].price)} r="3" fill="#32669b" />)}
        <text x="20" y="172" fill="#61748b" fontSize="11">{time(view.start)}</text><text x="640" y="172" textAnchor="end" fill="#61748b" fontSize="11">{time(view.end)}</text>
      </svg>
      <p className="product-muted">观察范围 {number(low)}–{number(high)} USDT；只展示已观察路径。深线为收盘价，浅线为分钟高低价。{view.aggregation?.method === 'extreme_samples' ? `共 ${view.aggregation.source_points} 根分钟原件，显示 ${view.aggregation.output_points} 个极值抽样点；抽样间隔不是缺数。` : '缺失分钟留空。'}</p>
    </>}
    {Boolean(view?.missing_ranges.length) && <details className="risk-warning"><summary>{view!.missing_ranges.length} 段分钟数据缺口</summary><ul>{view!.missing_ranges.map((range, index) => <li key={index}>{time(range.start_at)} 至 {time(range.end_at)}</li>)}</ul></details>}
    {view?.alerts.map(alert => <button key={alert.alert_id} type="button" className="product-row" onClick={() => onAlert(alert.alert_id)}><div><strong>{alert.headline}</strong><small>{time(alert.triggered_at)}</small></div><span>查看告警</span></button>)}
    {Boolean(view?.markers?.length) && <details><summary>已保存的事实、事件、报告与中断（{view!.markers!.length} 条）</summary><p className="product-muted">按系统收录或产出时间排列。时间重合不证明因果关系，全球事件的相关性仍需研究。</p>{view!.markers!.map(marker => <div key={`${marker.kind}:${marker.marker_id}`} className="product-row"><div><strong>{marker.label}</strong><small>系统记录：{time(marker.at)}{marker.occurred_at ? ` · 来源发生／计划时间：${time(marker.occurred_at)}` : ''}</small><details className="risk-audit"><summary>原始记录标识</summary>{marker.source_ref}</details></div>{marker.run_id && onNavigate && <button type="button" className="button secondary" onClick={() => onNavigate(marker.artifact_id ? 'report' : 'progress', { run: marker.run_id!, back: '#/home' })}>查看记录</button>}</div>)}</details>}
    {view?.markers_truncated && <p className="risk-warning">此窗口记录较多，列表仅展示近期报告 50 条、事实 100 条、事件及中断各 50 条。原件仍在库中。</p>}
  </section>
}

export function MonitorStatus({ monitor, disabled, onChanged }: { monitor: RiskMonitorStatus; disabled: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const action = monitor.status === 'paused' ? 'resume' : 'pause'
  const canCommand = !disabled && ['active', 'paused', 'degraded'].includes(monitor.status)
  useEffect(() => {
    if (disabled) return
    // The actual fetched state resolves a response-loss window. The next
    // opposite cycle must not replay an earlier already-applied command.
    if (monitor.status === 'paused') clearRiskRequestId(`monitor:${monitor.plan_id}:pause`)
    if (monitor.status === 'active') clearRiskRequestId(`monitor:${monitor.plan_id}:resume`)
  }, [monitor.plan_id, monitor.status, disabled])
  async function command() {
    if (!canCommand || busy) return
    setBusy(true); setError(null)
    const key = `monitor:${monitor.plan_id}:${action}`
    try {
      await riskApi.monitorCommand(monitor.plan_id, action, riskRequestId(key))
      clearRiskRequestId(key)
      onChanged()
    } catch (error) { setError(riskErrorMessage(error)) }
    finally { setBusy(false) }
  }
  return <div className="risk-monitor-row">
    <div><strong>{disabled ? '监控状态未确认' : monitorState[monitor.status]}</strong><p>{monitor.last_truth ? truth[monitor.last_truth] : '尚未完成一次可核验的条件检查'}</p><small>最近检查：{time(monitor.last_evaluated_at)} · 下次计划：{time(monitor.next_evaluation_at)}</small></div>
    <button type="button" className="button secondary" disabled={!canCommand || busy} onClick={() => { void command() }}>{busy ? '正在核对回执…' : action === 'resume' ? '恢复监测' : '暂停监测'}</button>
    {error && <p role="alert">{error}</p>}
    {monitor.error_code && <details className="risk-audit"><summary>检查失败记录</summary><code>{monitor.error_code}</code></details>}
  </div>
}

function useVisible(ref: React.RefObject<HTMLElement | null>) {
  const [intersects, setIntersects] = useState(false)
  const [foreground, setForeground] = useState(document.visibilityState === 'visible')
  useEffect(() => {
    const visibility = () => setForeground(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', visibility)
    const node = ref.current
    if (!node) return () => document.removeEventListener('visibilitychange', visibility)
    const fallback = () => {
      const box = node.getBoundingClientRect()
      setIntersects(box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < window.innerHeight && box.right > 0 && box.left < window.innerWidth)
    }
    const observer = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver(entries => setIntersects(entries.some(entry => entry.isIntersecting)), { threshold: .1 }) : null
    if (observer) observer.observe(node)
    else { fallback(); window.addEventListener('scroll', fallback); window.addEventListener('resize', fallback) }
    return () => { observer?.disconnect(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('scroll', fallback); window.removeEventListener('resize', fallback) }
  }, [ref])
  return intersects && foreground
}

export function RiskAlertDetail({ alertId, scopeId, onClose, onNavigate }: { alertId: string; scopeId: string; onClose: () => void; onNavigate: Navigate }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['risk-alert', alertId], queryFn: ({ signal }) => riskApi.alert(alertId, signal), retry: false })
  const ref = useRef<HTMLElement | null>(null)
  const visible = useVisible(ref)
  const sent = useRef(false)
  const mounted = useRef(false)
  const [displayRetry, setDisplayRetry] = useState(0)
  const [displayError, setDisplayError] = useState(false)
  const [displayTime, setDisplayTime] = useState<string | null>(null)
  const [ackBusy, setAckBusy] = useState(false)
  const [ackTime, setAckTime] = useState<string | null>(null)
  const [ackError, setAckError] = useState(false)
  const alert = query.data?.scope_id === scopeId ? query.data : undefined
  const research = alert?.research_request
  const researchIdentityMatches = !research || (research.scope_id === alert?.scope_id && research.run_id === alert?.report_run_id)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (!alert || !visible || sent.current || query.isError) return
    sent.current = true
    const intent = riskReceiptIntent(`display:${alert.alert_id}`)
    void riskApi.displayReceipt(alert.alert_id, intent.request_id, intent.client_time).then(receipt => { if (mounted.current) { setDisplayError(false); setDisplayTime(receipt.recorded_at) } }).catch(() => { if (mounted.current) setDisplayError(true) })
  }, [alert?.alert_id, Boolean(alert), visible, query.isError, displayRetry])
  async function acknowledge() {
    if (!alert || ackBusy || ackTime || alert.acknowledged_at) return
    setAckBusy(true); setAckError(false)
    try {
      const intent = riskReceiptIntent(`ack:${alert.alert_id}`)
      const receipt = await riskApi.acknowledge(alert.alert_id, intent.request_id, intent.client_time)
      setAckTime(receipt.recorded_at)
      await client.invalidateQueries({ queryKey: ['risk-summary', scopeId] })
    } catch { setAckError(true) }
    finally { setAckBusy(false) }
  }
  return <section className="product-card risk-alert-detail" ref={ref} aria-label="告警详情">
    <div className="product-card-head"><h2>告警详情</h2><button type="button" className="button secondary" onClick={onClose}>关闭详情</button></div>
    {!alert ? <><p role={query.isError || query.data ? 'alert' : undefined}>{query.isLoading ? '正在读取告警记录…' : '告警详情无法核对，尚未记录阅读或确认。'}</p><button className="button secondary" type="button" onClick={() => { void query.refetch() }}>重新读取详情</button></> : <>
      <span className={`risk-badge risk-${alert.severity}`}>{severity[alert.severity]} · {alert.status === 'resolved' ? '条件已解除' : '仍需关注'}</span>
      <h3>{alert.headline}</h3><p>{alert.explanation}</p><p>触发条件：{alert.condition_label}</p><p>观测变动：{observedValue(alert.observed_value, alert.value_unit)} · 触发门槛：{alert.value_unit === 'fraction' || alert.value_unit === '%' ? '绝对变动达到 ' : '达到 '}{observedValue(alert.threshold_value, alert.value_unit, true)}</p>
      <p>首次触发：{time(alert.triggered_at)}{alert.resolved_at ? ` · 解除时间：${time(alert.resolved_at)}` : ''}</p>
      {query.isError && <p role="alert">最新读取失败；当前显示上次记录，请重新读取后核对。</p>}
      {!researchIdentityMatches ? <p role="alert">这条告警的研究请求身份无法核对，尚不能打开关联执行记录。</p> : research ? <div aria-label="这条告警的研究请求">
        <p><strong>{research.status === 'failed' ? '这条告警的研究请求未完成' : research.status === 'pending_admission' ? '这条告警的研究请求等待准入' : research.status === 'claiming' ? '这条告警的研究请求正在核验条件' : research.run_id ? '这条告警已关联研究执行记录' : '这条告警的研究请求状态待核对'}</strong></p>
        {research.status === 'failed' && !research.run_id && research.error_code === 'research_admission_budget_exhausted' && <p role="alert">已记录 {research.attempts} 次准入核验，本次返回准入次数已用尽；请求已结束，当前未关联研究报告。计划时间到达不代表此请求会自动恢复。</p>}
        {!research.run_id && <p>本次请求没有关联研究执行标识；当前资产的其他报告不能作为这条告警的处理结果。</p>}
        {research.run_id && <p>关联执行标识不代表报告已经保存或确认；请打开原执行记录核对。</p>}
        <details className="risk-audit"><summary>关联研究请求记录</summary><p>请求：{research.request_id} · 核验次数：{research.attempts}/3</p><p>原请求状态：<code>{research.status}</code></p>{research.error_code && <p>失败记录：<code>{research.error_code}</code></p>}</details>
      </div> : <p className="product-muted">{alert.research_request === undefined ? '此版本未返回这条告警的研究请求状态，尚不能确认关联请求的处理结果。' : '这条告警当前没有关联研究请求；其他资产研究报告不代表已处理本次告警。'}</p>}
      {researchIdentityMatches && research?.status === 'failed' && !research.run_id && research.attempts === 3 && research.error_code === 'research_admission_budget_exhausted'
        && <RiskAlertRecheck key={`${scopeId}:${alert.alert_id}`} alertId={alert.alert_id} scopeId={scopeId} onNavigate={onNavigate} />}
      {displayError && <div role="alert"><p>阅读回执尚未确认，告警记录仍保留。</p><button type="button" className="button secondary" disabled={!visible} onClick={() => { sent.current = false; setDisplayRetry(value => value + 1) }}>重试阅读回执</button></div>}
      <div className="risk-actions"><button type="button" className="button primary" disabled={ackBusy || Boolean(ackTime || alert.acknowledged_at) || query.isError} onClick={() => { void acknowledge() }}>{ackBusy ? '正在保存确认…' : ackTime || alert.acknowledged_at ? '已确认知悉' : '确认知悉此告警'}</button>{researchIdentityMatches && alert.report_run_id && <button type="button" className="button secondary" onClick={() => onNavigate('report', { run: alert.report_run_id!, back: '#/home' })}>查看关联研究执行记录</button>}</div>
      {(ackTime || alert.acknowledged_at) && <p>知悉回执：{time(ackTime || alert.acknowledged_at)}。确认知悉不会解除监测条件。</p>}
      {ackError && <p role="alert">确认结果尚未核对，请刷新或重试原操作。</p>}
      <details className="risk-audit"><summary>审计信息</summary><dl><dt>告警记录</dt><dd>{alert.alert_id}</dd><dt>监控计划</dt><dd>{alert.plan_id}</dd><dt>资产范围</dt><dd>{alert.scope_id}</dd><dt>历史显示回执</dt><dd>{time(alert.displayed_at ?? displayTime)}</dd><dt>原始观测值</dt><dd>{alert.observed_value ?? '未知'}</dd><dt>原始门槛</dt><dd>{alert.threshold_value ?? '未知'}</dd><dt>原始单位</dt><dd>{alert.value_unit || '未记录'}</dd></dl></details>
    </>}
  </section>
}

export function RiskDashboard({ onNavigate }: { onNavigate: Navigate }) {
  const scopes = useQuery({ queryKey: ['risk-scopes'], queryFn: ({ signal }) => riskApi.scopes(signal), retry: false })
  const [chosen, setChosen] = useState(() => {
    try { return sessionStorage.getItem('market-risk-selected-scope.v1') ?? '' }
    catch { return '' }
  })
  const [selectedAlert, setSelectedAlert] = useState<string | null>(null)
  const [qualityOpen, setQualityOpen] = useState(false)
  const [rangeEnd, setRangeEnd] = useState(() => Math.floor(Date.now() / 60_000) * 60_000)
  const [rangeMinutes, setRangeMinutes] = useState(30)
  const available = scopes.data?.items ?? []
  const scope: RiskScope | undefined = available.find(item => item.scope_id === chosen) ?? available.find(item => item.enabled) ?? available[0]
  const scopeId = scope?.scope_id ?? ''
  const end = new Date(rangeEnd).toISOString()
  const start = new Date(rangeEnd - rangeMinutes * 60_000).toISOString()
  const summary = useQuery({ queryKey: ['risk-summary', scopeId], queryFn: ({ signal }) => riskApi.summary(scopeId, signal), enabled: Boolean(scopeId), retry: false, refetchInterval: 30_000 })
  const timeline = useQuery({ queryKey: ['risk-timeline', scopeId, start, end], queryFn: ({ signal }) => riskApi.timeline(scopeId, start, end, signal), enabled: Boolean(scopeId), retry: false })
  useEffect(() => { const timer = setInterval(() => setRangeEnd(Math.floor(Date.now() / 60_000) * 60_000), 60_000); return () => clearInterval(timer) }, [])
  useEffect(() => setSelectedAlert(null), [scopeId])
  useEffect(() => setQualityOpen(false), [scopeId, summary.data?.research.run_id])
  function refresh() { setRangeEnd(Math.floor(Date.now() / 60_000) * 60_000); void summary.refetch(); void timeline.refetch(); if (selectedAlert) void client.invalidateQueries({ queryKey: ['risk-alert', selectedAlert] }) }
  const client = useQueryClient()
  function chooseScope(value: string) {
    setChosen(value)
    try { sessionStorage.setItem('market-risk-selected-scope.v1', value) } catch { /* Optional view preference. */ }
  }
  return <section className="market-risk-dashboard" aria-label="资产风险观察">
    <div className="risk-toolbar"><div><strong>资产风险观察</strong><p>24 小时研究结合过去 30 分钟变化；条件告警与方向预测分别核对。</p></div><label>观察资产<select aria-label="观察资产" value={scopeId} disabled={!available.length} onChange={event => chooseScope(event.target.value)}>{available.map(item => <option key={item.scope_id} value={item.scope_id}>{item.label}</option>)}</select></label><button type="button" className="button secondary" disabled={!scopeId || summary.isFetching || timeline.isFetching} onClick={refresh}>刷新风险数据</button></div>
    {scopes.isError ? <div className="product-notice danger" role="alert"><p>资产风险范围暂不可用，当前风险状态未确认。</p><button className="button secondary" type="button" onClick={() => { void scopes.refetch() }}>重新读取资产</button></div> : !scope ? <div className="product-notice"><p>{scopes.isLoading ? '正在读取已配置的资产范围…' : '尚未配置可核验的资产风险范围。'}</p></div> : <>
      <p className="product-muted">{scope.label} · {scope.venue.toUpperCase()} 现货 · 一分钟收盘价{!scope.enabled ? ' · 监控尚未启用' : ''}</p>
      <ActionPlanPanel key={`action:${scopeId}`} scopeId={scopeId} onNavigate={onNavigate} />
      <HomeHistoryForecast key={`current-hour:${scopeId}`} scopeId={scopeId} onNavigate={onNavigate} />
      <RiskSummary view={summary.data} loading={summary.isLoading} failed={summary.isError} onNavigate={onNavigate} />
      <RiskResearch key={scopeId} scopeId={scopeId} onNavigate={onNavigate} />
      {summary.data?.research.run_id && summary.data.research.artifact_id && <details className="product-card" open={qualityOpen} onToggle={event => setQualityOpen(event.currentTarget.open)}><summary>核对这份研究的资格与缺口</summary><ResearchQuality key={summary.data.research.run_id} runId={summary.data.research.run_id} visible={qualityOpen} onOpenReport={() => onNavigate('report', { run: summary.data!.research.run_id!, back: '#/home' })} /></details>}
      <label className="risk-range">路径观察范围<select aria-label="路径观察范围" value={rangeMinutes} onChange={event => setRangeMinutes(Number(event.target.value))}><option value="30">过去 30 分钟</option><option value="1440">过去 24 小时</option><option value="10080">过去 7 天</option></select></label>
      <div className="product-grid"><RiskTimeline view={timeline.data} loading={timeline.isLoading} failed={timeline.isError} onAlert={setSelectedAlert} onNavigate={onNavigate} /><section className="product-card" aria-label="风险监控状态"><h2>持续监测</h2><p>每 60 秒检查一次变化条件。未触发告警不等于未来风险为零。</p>{summary.data?.monitors.length ? summary.data.monitors.map(monitor => <MonitorStatus key={monitor.plan_id} monitor={monitor} disabled={summary.isError} onChanged={() => { void summary.refetch() }} />) : <p>{summary.isLoading ? '正在读取监控计划…' : '当前没有可核验的监控计划，自动预警能力尚未确认。'}</p>}</section></div>
      <RiskExposure />
      <p className="product-muted">下方是此前登记的方法试验与历史成绩，保留供参照；它们不能代替上方当前资产的小时记录与原件核验。</p>
      <RiskPathScore key={`score:${scopeId}`} scopeId={scopeId} />
      <ForecastAlarmPanel key={`forecast-alarms:${scopeId}`} scopeId={scopeId} />
      <MarketMemory key={`memory:${scopeId}`} scopeId={scopeId} onNavigate={onNavigate} />
      <MarketEnhancements key={`enhancements:${scopeId}`} scopeId={scopeId} />
      {Boolean(summary.data?.recent_alerts.length) && <section className="product-card" aria-label="最近风险告警"><h2>最近告警</h2>{summary.data!.recent_alerts.map(alert => <button key={alert.alert_id} type="button" className="product-row" onClick={() => setSelectedAlert(alert.alert_id)}><div><strong>{alert.headline}</strong><small>{severity[alert.severity]} · {time(alert.triggered_at)} · {alert.acknowledged_at ? '已知悉' : '待知悉'}</small></div><span>查看详情</span></button>)}</section>}
      {selectedAlert && <RiskAlertDetail key={selectedAlert} alertId={selectedAlert} scopeId={scopeId} onClose={() => setSelectedAlert(null)} onNavigate={onNavigate} />}
      <details className="risk-audit"><summary>风险范围审计</summary><p>资产范围：{scopeId} · {scope.instrument} · 成交价口径。显示回执仅在详情实际进入可见区域后记录；列表发布不代表用户已看到。</p></details>
    </>}
  </section>
}
