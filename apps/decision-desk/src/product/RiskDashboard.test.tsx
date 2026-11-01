// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { riskApi, type RiskSummaryView } from '../api/risk'
import { riskScopeFixture, riskSummaryFixture, riskTimelineFixture, riskAlertFixture, RISK_AT } from '../api/riskFixtures'
import { RiskDashboard, RiskAlertDetail, RiskTimeline, riskPathSegments, HomeHistoryForecast } from './RiskDashboard'
import * as scheduleApi from '../api/historySchedule'
import * as forecastApi from '../api/historyForecast'
import { historyApi } from '../api/historyStatus'
import { ApiRequestError } from '../api/client'
import { riskScopesViewSchema } from '@decision-hub/contracts-ts'
import { historyHourlyStatusV2Schema } from '../../../../packages/contracts_ts/src/generated/history_hourly_status_v2'
import { historyAssetRefreshReplyV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { HistoryStatusPage } from './HistoryStatusPage'
import savedHourly from './__fixtures__/history-hourly-status-private-api03.json'
import savedOriginal from './__fixtures__/history-forecast-private-api02.json'
import actualScopes47 from './__fixtures__/risk-scopes-actual-api47.json'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
let client: QueryClient
const observers: IntersectionObserverCallback[] = []
let visibility: DocumentVisibilityState = 'visible'
async function mount(element: React.ReactNode) {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) { observers.push(callback) }
    observe() { /* explicit test intersection */ }
    disconnect() { /* no observer after unmount */ }
  })
  await act(async () => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>))
}
async function until(check: () => boolean) {
  for (let index = 0; index < 60 && !check(); index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  expect(check()).toBe(true)
}
function button(text: string) { return [...container.querySelectorAll('button')].find(item => item.textContent === text)! }
function defaults(view: RiskSummaryView = riskSummaryFixture) {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(historyHourlyStatusV2Schema.parse(savedHourly))
  vi.spyOn(riskApi, 'scopes').mockResolvedValue({ schema_version: 'market-risk-scopes.v1', as_of: RISK_AT, items: [riskScopeFixture] })
  const summary = vi.spyOn(riskApi, 'summary').mockResolvedValue(view)
  vi.spyOn(riskApi, 'timeline').mockImplementation(async (scopeId, start, end) => ({ ...riskTimelineFixture, scope_id: scopeId, start, end }))
  return summary
}
afterEach(async () => {
  vi.useRealTimers()
  if (root) await act(async () => root.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
  observers.length = 0; visibility = 'visible'; sessionStorage.clear()
  window.location.hash = ''
})

const btcRiskScope = 'crypto:spot:okx:BTC-USDT:trade:1m'
const ethRiskScope = 'crypto:spot:okx:ETH-USDT:trade:1m'
function hourlyCurrent() {
  const value = historyHourlyStatusV2Schema.parse(structuredClone(savedHourly))
  value.scopes[0] = { ...value.scopes[0], state: 'committed_verified', forecast_id: savedOriginal.forecast_id,
    fixture_mode: true, reason_code: null, reason_label: '该小时提交时核验通过。' }
  value.scopes[1] = { ...value.scopes[1], state: 'committed_verified', forecast_id: `hforecast2_${'b'.repeat(48)}`,
    fixture_mode: true, reason_code: null, reason_label: '该小时提交时核验通过。' }
  return value
}
const homeCard = () => container.querySelector('[aria-label="当前资产小时风险记录"]') as HTMLElement

it.each(riskScopesViewSchema.parse(actualScopes47).items)('actual API asset $instrument exposes the saved-hour entry in the full dashboard', async actualScope => {
  const history = hourlyCurrent()
  const summary = defaults({ ...riskSummaryFixture, scope_id: actualScope.scope_id })
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(history)
  vi.spyOn(riskApi, 'scopes').mockResolvedValue({ ...riskScopesViewSchema.parse(actualScopes47), items: [actualScope] })
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await mount(<RiskDashboard onNavigate={vi.fn()} />)
  await until(() => Boolean(homeCard()?.textContent?.includes('已保存，提交时核验通过')))
  expect(summary).toHaveBeenCalledWith(actualScope.scope_id, expect.any(AbortSignal))
  expect(homeCard().textContent).toContain(actualScope.instrument === 'BTC-USDT' ? '比特币 · 小时风险记录' : '以太坊 · 小时风险记录')
  expect(homeCard().textContent).toContain('已保存，提交时核验通过')
  expect(history.scopes.find(item => item.scope_id === actualScope.scope_id)).toBeDefined()
  expect(button('核验并查看风险估计')).toBeDefined()
  expect(original).not.toHaveBeenCalled()
})

it('homepage saved-hour entry polls only metadata and explicitly reads the selected original, without production', async () => {
  vi.useFakeTimers()
  const view = hourlyCurrent()
  const schedule = vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(view)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockRejectedValue(new ApiRequestError('invalid', 'http', 409))
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await mount(<HomeHistoryForecast scopeId={btcRiskScope} onNavigate={vi.fn()} />)
  await act(async () => { await vi.advanceTimersByTimeAsync(20) })
  expect(homeCard().textContent).toContain('比特币 · 小时风险记录')
  expect(homeCard().textContent).not.toContain('以太坊 · 小时风险记录')
  expect(homeCard().querySelector('.history-risk-values')).toBeNull()
  expect(original).not.toHaveBeenCalled()
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  expect(schedule).toHaveBeenCalledTimes(2)
  expect(original).not.toHaveBeenCalled()
  await act(async () => button('核验并查看风险估计').click())
  await act(async () => { await vi.advanceTimersByTimeAsync(20) })
  expect(original).toHaveBeenCalledWith(expect.objectContaining({ forecast_id: view.scopes[0].forecast_id,
    scope_id: view.scopes[0].scope_id, cutoff_at: view.scopes[0].cutoff_at, probabilities: null }), expect.any(AbortSignal))
  expect(original).toHaveBeenCalledTimes(1)
  expect(fetch).not.toHaveBeenCalled()
  vi.useRealTimers()
})

it('switching homepage asset removes prior numeric results and selects only the matching saved identity', async () => {
  const view = hourlyCurrent()
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(view)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockResolvedValue(historyAssetRefreshReplyV2Schema.parse({
    ...savedOriginal, cutoff_at: view.scopes[0].cutoff_at,
  }))
  await mount(<HomeHistoryForecast key="BTC" scopeId={btcRiskScope} onNavigate={vi.fn()} />)
  await until(() => Boolean(button('核验并查看风险估计')))
  await act(async () => button('核验并查看风险估计').click())
  await until(() => Boolean(homeCard().querySelector('.history-risk-values')))
  await act(async () => root.render(<QueryClientProvider client={client}><HomeHistoryForecast key="ETH" scopeId={ethRiskScope} onNavigate={vi.fn()} /></QueryClientProvider>))
  await until(() => Boolean(homeCard()?.textContent?.includes('以太坊 · 小时风险记录')))
  expect(homeCard().querySelector('.history-risk-values')).toBeNull()
  expect(original).toHaveBeenCalledTimes(1)
  await until(() => Boolean(button('核验并查看风险估计')))
  original.mockRejectedValueOnce(new ApiRequestError('blocked', 'http', 409))
  await act(async () => button('核验并查看风险估计').click())
  await until(() => original.mock.calls.length === 2)
  expect(original).toHaveBeenLastCalledWith(expect.objectContaining({ forecast_id: view.scopes[1].forecast_id,
    scope_id: view.scopes[1].scope_id, cutoff_at: view.scopes[1].cutoff_at }), expect.any(AbortSignal))
})

it('homepage missing saved identity stays unreadable, with no fallback to another asset', async () => {
  const view = hourlyCurrent(); view.scopes[1].forecast_id = null
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(view)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await mount(<HomeHistoryForecast scopeId={ethRiskScope} onNavigate={vi.fn()} />)
  await until(() => Boolean(homeCard()?.textContent?.includes('当前没有可读取')))
  expect(button('核验并查看风险估计')).toBeUndefined()
  expect(original).not.toHaveBeenCalled()
})

it('homepage refuses unsupported scope without reading history or producing anything', async () => {
  const schedule = vi.spyOn(scheduleApi, 'readHistorySchedule')
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await mount(<HomeHistoryForecast scopeId="okx:spot:GOLD-USDT:trade" onNavigate={vi.fn()} />)
  expect(homeCard()).toBeNull(); expect(schedule).not.toHaveBeenCalled(); expect(original).not.toHaveBeenCalled()
})

it('homepage expired metadata authorization hides saved records and offers the existing pairing page', async () => {
  const schedule = vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(hourlyCurrent())
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockResolvedValue(historyAssetRefreshReplyV2Schema.parse(savedOriginal))
  const navigate = vi.fn()
  await mount(<HomeHistoryForecast scopeId={btcRiskScope} onNavigate={navigate} />)
  await until(() => Boolean(button('核验并查看风险估计')))
  await act(async () => button('核验并查看风险估计').click())
  await until(() => Boolean(homeCard().querySelector('.history-risk-values')))
  schedule.mockRejectedValueOnce(new ApiRequestError('expired', 'http', 401))
  await act(async () => button('刷新本小时记录').click())
  await until(() => Boolean(button('恢复本机验证并返回首页')))
  expect(homeCard().querySelector('.history-risk-values')).toBeNull()
  expect(button('核验并查看风险估计')).toBeUndefined()
  await act(async () => button('恢复本机验证并返回首页').click())
  expect(navigate).toHaveBeenCalledWith('history-status', { scope: 'crypto:spot:okx:BTC-USDT:trade:1m', focus: 'hourly', back: '#/home' })
})

it('homepage rejected owner session 403 hides numeric results and routes to existing verification', async () => {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(hourlyCurrent())
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockResolvedValueOnce(historyAssetRefreshReplyV2Schema.parse(savedOriginal))
  const navigate = vi.fn()
  await mount(<HomeHistoryForecast scopeId={btcRiskScope} onNavigate={navigate} />)
  await until(() => Boolean(button('核验并查看风险估计')))
  await act(async () => button('核验并查看风险估计').click())
  await until(() => Boolean(homeCard().querySelector('.history-risk-values')))
  original.mockRejectedValueOnce(new ApiRequestError('history_owner_session_rejected', 'http', 403))
  await act(async () => button('重新核验本条记录').click())
  await until(() => Boolean(button('恢复本机验证后重新核验')))
  expect(homeCard().querySelector('.history-risk-values')).toBeNull()
  await act(async () => button('恢复本机验证后重新核验').click())
  expect(navigate).toHaveBeenCalledWith('history-status', { scope: 'crypto:spot:okx:BTC-USDT:trade:1m', focus: 'hourly', back: '#/home' })
})

it('homepage missing forecast grant 403 hides numbers without sending valid identity to pairing', async () => {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(hourlyCurrent())
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockResolvedValueOnce(historyAssetRefreshReplyV2Schema.parse(savedOriginal))
  const navigate = vi.fn()
  await mount(<HomeHistoryForecast scopeId={btcRiskScope} onNavigate={navigate} />)
  await until(() => Boolean(button('核验并查看风险估计')))
  await act(async () => button('核验并查看风险估计').click())
  await until(() => Boolean(homeCard().querySelector('.history-risk-values')))
  original.mockRejectedValueOnce(new ApiRequestError('history_forecast_read_not_granted', 'http', 403))
  await act(async () => button('重新核验本条记录').click())
  await until(() => Boolean(homeCard().textContent?.includes('重新验证不会补齐读取授权')))
  expect(homeCard().querySelector('.history-risk-values')).toBeNull()
  expect(button('恢复本机验证后重新核验')).toBeUndefined()
  expect(navigate).not.toHaveBeenCalled()
})

it('homepage metadata grant rejection 403 does not offer session recovery', async () => {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockRejectedValue(new ApiRequestError('history_forecast_read_not_granted', 'http', 403))
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await mount(<HomeHistoryForecast scopeId={btcRiskScope} onNavigate={vi.fn()} />)
  await until(() => Boolean(homeCard()?.textContent?.includes('重新验证不会补齐读取授权')))
  expect(button('恢复本机验证并返回首页')).toBeUndefined()
  expect(button('核验并查看风险估计')).toBeUndefined()
  expect(original).not.toHaveBeenCalled()
})

it('history recovery accepts only whitelisted scope/focus and returns to home without changing pairing', async () => {
  window.location.hash = '#/history-status?scope=crypto%3Aspot%3Aokx%3AETH-USDT%3Atrade%3A1m&focus=hourly&back=%23%2Fhome'
  vi.spyOn(historyApi, 'status').mockRejectedValue(new ApiRequestError('expired', 'http', 401))
  const pair = vi.spyOn(historyApi, 'pairOwner')
  const navigate = vi.fn()
  await mount(<HistoryStatusPage onNavigate={navigate} />)
  await until(() => Boolean(container.textContent?.includes('独立本机验证')))
  expect(container.textContent).toContain('从首页进入：以太坊')
  await act(async () => button('返回资产风险首页').click())
  expect(navigate).toHaveBeenCalledWith('home'); expect(pair).not.toHaveBeenCalled()
  window.location.hash = '#/history-status?scope=__proto__&focus=hourly&back=https%3A%2F%2Fexample.com'
  await act(async () => root.render(<QueryClientProvider client={client}><HistoryStatusPage onNavigate={navigate} /></QueryClientProvider>))
  expect(container.textContent).not.toContain('从首页进入：')
  await act(async () => button('返回管理与审计').click())
  expect(navigate).toHaveBeenLastCalledWith('management')
})

it('does not confuse extreme sampling with missing minutes and keeps an actual gap', () => {
  const view = { ...riskTimelineFixture, points: [
    { at: '2026-10-07T23:31:00Z', price: '100', low: '50', high: '101' },
    { at: '2026-10-07T23:36:00Z', price: '100', low: '99', high: '200' },
    { at: '2026-10-07T23:42:00Z', price: '100', low: '99', high: '101' },
  ], aggregation: { method: 'extreme_samples' as const, source_points: 3000, output_points: 3, bucket_minutes: 4, path_hash: 'a'.repeat(64), provenance_hash: 'b'.repeat(64) }, missing_ranges: [{ start_at: '2026-10-07T23:40:00Z', end_at: '2026-10-07T23:41:00Z' }] }
  expect(riskPathSegments(view).map(segment => segment.length)).toEqual([2, 1])
})

it('shows unknown and unquantified legacy risk, without inventing confidence or hiding the report', async () => {
  defaults({ ...riskSummaryFixture, research: { ...riskSummaryFixture.research, run_id: 'old-real-run', artifact_id: 'old-real-artifact' } })
  const navigate = vi.fn()
  await mount(<RiskDashboard onNavigate={navigate} />)
  await until(() => Boolean(button('阅读研究报告与限制')))
  expect(container.textContent).toContain('路径风险尚未量化')
  expect(container.textContent).toContain('现有报告尚未提供量化路径风险')
  expect(container.textContent).toContain('最强反方')
  expect(container.textContent).toContain('不等于市场安全')
  expect(container.textContent).toContain('非实时成交报价')
  await act(async () => button('阅读研究报告与限制').click())
  expect(navigate).toHaveBeenCalledWith('report', { run: 'old-real-run', back: '#/home' })
})

it('refreshes the selected scope and keeps a failed refresh explicitly unknown', async () => {
  const summary = defaults()
  await mount(<RiskDashboard onNavigate={vi.fn()} />)
  await until(() => Boolean(container.textContent?.includes('44,000.1')))
  summary.mockRejectedValueOnce(new Error('offline'))
  await act(async () => button('刷新风险数据').click())
  await until(() => Boolean(container.textContent?.includes('刷新失败')))
  expect(container.textContent).toContain('当前风险状态未确认')
  expect(container.textContent).toContain('44,000.1')
  summary.mockResolvedValue({ ...riskSummaryFixture, data_status: 'stale' })
  await act(async () => button('刷新风险数据').click())
  await until(() => Boolean(container.textContent?.includes('数据已陈旧')))
  expect(container.textContent).toContain('路径风险尚未量化')
})

it('selects ETH without showing BTC data while the selected asset is loading', async () => {
  defaults()
  const eth = { ...riskScopeFixture, scope_id: 'okx:spot:ETH-USDT:trade', label: '以太坊 ETH', instrument: 'ETH-USDT' }
  vi.mocked(riskApi.scopes).mockResolvedValue({ schema_version: 'market-risk-scopes.v1', as_of: RISK_AT, items: [riskScopeFixture, eth] })
  vi.mocked(riskApi.summary).mockImplementation(async scopeId => ({ ...riskSummaryFixture, scope_id: scopeId, quote: { ...riskSummaryFixture.quote!, price: scopeId === eth.scope_id ? '3000.5' : '44000.1' }, monitors: [], recent_alerts: [] }))
  vi.mocked(riskApi.timeline).mockImplementation(async (scopeId, start, end) => ({ ...riskTimelineFixture, scope_id: scopeId, start, end, points: scopeId === eth.scope_id ? [{ at: start, price: '3000.5' }] : riskTimelineFixture.points }))
  await mount(<RiskDashboard onNavigate={vi.fn()} />)
  await until(() => Boolean(container.textContent?.includes('44,000.1')))
  const select = container.querySelector('select')!
  await act(async () => { select.value = eth.scope_id; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await until(() => Boolean(container.textContent?.includes('3,000.5')))
  expect(container.textContent).not.toContain('44,000.1')
  expect(riskApi.summary).toHaveBeenLastCalledWith(eth.scope_id, expect.any(AbortSignal))
})

it('can pause and resume, re-reading actual state rather than claiming success optimistically', async () => {
  defaults()
  let status: 'active' | 'paused' = 'active'
  vi.mocked(riskApi.summary).mockImplementation(async () => ({ ...riskSummaryFixture, monitors: [{ ...riskSummaryFixture.monitors[0], status }] }))
  const command = vi.spyOn(riskApi, 'monitorCommand').mockImplementation(async (planId, action, requestId) => {
    status = action === 'pause' ? 'paused' : 'active'
    return { schema_version: 'market-risk-monitor-command-receipt.v1', plan_id: planId, request_id: requestId, action, status: 'applied', monitor: { ...riskSummaryFixture.monitors[0], status } }
  })
  await mount(<RiskDashboard onNavigate={vi.fn()} />)
  await until(() => Boolean(button('暂停监测')))
  await act(async () => button('暂停监测').click())
  await until(() => Boolean(button('恢复监测')))
  expect(container.textContent).toContain('已暂停')
  await act(async () => button('恢复监测').click())
  await until(() => Boolean(button('暂停监测')))
  expect(command.mock.calls.map(item => item[1])).toEqual(['pause', 'resume'])
  expect(command.mock.calls[0][2]).not.toEqual(command.mock.calls[1][2])
})

it('leaves path gaps disconnected and states that the observed path is historical', async () => {
  const view = { ...riskTimelineFixture, points: [{ at: '2026-10-07T23:30:00Z', price: '44000' }, { at: '2026-10-07T23:31:00Z', price: '44000.1' }, { at: '2026-10-07T23:33:00Z', price: '43999.8' }], missing_ranges: [{ start_at: '2026-10-07T23:32:00Z', end_at: '2026-10-07T23:33:00Z' }] }
  expect(riskPathSegments(view).map(segment => segment.length)).toEqual([2, 1])
  await mount(<RiskTimeline view={view} loading={false} failed={false} onAlert={vi.fn()} />)
  expect(container.querySelectorAll('polyline')).toHaveLength(1)
  expect(container.querySelectorAll('circle')).toHaveLength(1)
  expect(container.textContent).toContain('1 段分钟数据缺口')
  expect(container.textContent).toContain('只展示已观察路径')
})

it('explains disabled automatic monitoring in Chinese and keeps a failed resume paused', async () => {
  defaults({ ...riskSummaryFixture, monitors: [{ ...riskSummaryFixture.monitors[0], status: 'paused' }] })
  vi.spyOn(riskApi, 'monitorCommand').mockRejectedValue(new Error('risk_monitor_not_enabled'))
  await mount(<RiskDashboard onNavigate={vi.fn()} />)
  await until(() => Boolean(button('恢复监测')))
  await act(async () => button('恢复监测').click())
  await until(() => Boolean(container.textContent?.includes('自动风险监测尚未启用')))
  expect(container.textContent).toContain('已暂停')
  expect(container.textContent).not.toContain('risk_monitor_not_enabled')
  expect(button('恢复监测').disabled).toBe(false)
})

it('does not record a listed/hidden alert as displayed, then records once on actual visible detail', async () => {
  vi.spyOn(riskApi, 'alert').mockResolvedValue(riskAlertFixture)
  const display = vi.spyOn(riskApi, 'displayReceipt').mockImplementation(async (id, requestId) => ({ schema_version: 'market-risk-receipt.v1', alert_id: id, request_id: requestId, status: 'recorded', recorded_at: RISK_AT }))
  visibility = 'hidden'
  await mount(<RiskAlertDetail alertId="alert_fixture" scopeId={riskScopeFixture.scope_id} onClose={vi.fn()} onNavigate={vi.fn()} />)
  await until(() => Boolean(container.textContent?.includes(riskAlertFixture.headline)))
  await act(async () => observers.forEach(callback => callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)))
  expect(display).not.toHaveBeenCalled()
  visibility = 'visible'
  await act(async () => document.dispatchEvent(new Event('visibilitychange')))
  await until(() => display.mock.calls.length === 1)
  await until(() => [...container.querySelectorAll('dt')].find(item => item.textContent === '历史显示回执')?.nextElementSibling?.textContent !== '尚无记录')
  expect([...container.querySelectorAll('dt')].find(item => item.textContent === '历史显示回执')?.nextElementSibling?.textContent).toContain('2026')
  await act(async () => { visibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange')); visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')) })
  expect(display).toHaveBeenCalledTimes(1)
})

it('keeps the original acknowledgement identity on uncertain retry and does not turn acknowledgement into resolution', async () => {
  vi.spyOn(riskApi, 'alert').mockResolvedValue({ ...riskAlertFixture, observed_value: -0.016, threshold_value: 0.015, value_unit: 'fraction' })
  const ack = vi.spyOn(riskApi, 'acknowledge').mockRejectedValueOnce(new Error('response lost')).mockImplementation(async (id, requestId) => ({ schema_version: 'market-risk-receipt.v1', alert_id: id, request_id: requestId, status: 'already_recorded', recorded_at: RISK_AT }))
  await mount(<RiskAlertDetail alertId="alert_fixture" scopeId={riskScopeFixture.scope_id} onClose={vi.fn()} onNavigate={vi.fn()} />)
  await until(() => Boolean(button('确认知悉此告警')))
  const mainCopy = [...container.querySelectorAll('p')].map(item => item.textContent).join(' ')
  expect(mainCopy).toContain('观测变动：-1.6%')
  expect(mainCopy).toContain('绝对变动达到 1.5%')
  expect(mainCopy).not.toContain('fraction')
  expect(mainCopy).not.toContain('0.016')
  await act(async () => button('确认知悉此告警').click())
  await until(() => Boolean(container.textContent?.includes('确认结果尚未核对')))
  await act(async () => button('确认知悉此告警').click())
  await until(() => Boolean(button('已确认知悉')))
  expect(ack).toHaveBeenCalledTimes(2)
  expect(ack.mock.calls[0][1]).toBe(ack.mock.calls[1][1])
  expect(ack.mock.calls[0][2]).toBe(ack.mock.calls[1][2])
  expect(button('已确认知悉').disabled).toBe(true)
  expect(container.textContent).toContain('确认知悉不会解除监测条件')
})
