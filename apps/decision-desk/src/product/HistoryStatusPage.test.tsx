// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import * as clientApi from '../api/client'
import { historyApi, historyStatusSchema, type HistoryProductStatusV2 } from '../api/historyStatus'
import { HistoryStatusPage } from './HistoryStatusPage'
import * as scheduleApi from '../api/historySchedule'
import * as forecastIndexApi from '../api/historyForecastIndex'
import * as outcomeIndexApi from '../api/historyOutcomeEvaluations'

// Synthetic UI control, validated separately by the canonical Python DTO.
// No real market coverage, owner admission, prediction or HTTP success is claimed.
const fixture = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-product-status-v2.json'), 'utf8'))
const view = (): HistoryProductStatusV2 => historyStatusSchema.parse(structuredClone(fixture))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function renderPage(check: (element: HTMLDivElement, navigate: ReturnType<typeof vi.fn>) => Promise<void> | void,
  initial?: HistoryProductStatusV2) {
  // The independent hourly metadata capability has its own focused controls.
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockRejectedValue(new clientApi.ApiRequestError('hourly_not_configured', 'http', 503))
  vi.spyOn(forecastIndexApi, 'readHistoryForecastIndex').mockRejectedValue(new clientApi.ApiRequestError('old_api', 'http', 404))
  vi.spyOn(outcomeIndexApi, 'readHistoryOutcomeEvaluations').mockRejectedValue(new clientApi.ApiRequestError('outcome_not_configured', 'http', 503))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  if (initial) client.setQueryData(['history-product-status-v2', 0, 0, 0], initial)
  const fetch = vi.fn(() => { throw new Error('Transport forbidden in isolated UI controls') })
  vi.stubGlobal('fetch', fetch)
  const navigate = vi.fn()
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><HistoryStatusPage onNavigate={navigate} /></QueryClientProvider>)
      await new Promise(resolve => setTimeout(resolve, 20))
    })
    for (let attempt = 0; attempt < 10 && element.textContent?.includes('正在读取两个资产'); attempt++) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    }
    await check(element, navigate)
    expect(fetch).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove() }
}
const button = (element: HTMLElement, label: string) => Array.from(element.querySelectorAll('button')).find(row => row.textContent === label)!

it('strictly reads the repository V2 contract and rejects mistaken qualification, asset and forecast identities', () => {
  expect(view().data.current_originals_verified).toBe(false)
  for (const change of [
    (v: typeof fixture) => { v.financial_qualified = true },
    (v: typeof fixture) => { v.identity_origin = 'harness_cookie' },
    (v: typeof fixture) => { v.data.scopes[1] = v.data.scopes[0] },
    (v: typeof fixture) => { v.data.scopes[0].asset_label = '以太坊' },
    (v: typeof fixture) => { v.forecast.state = 'verification_required'; v.forecast.probabilities = { baseline_long: '0.3', baseline_short: '0.3', conditional_long: '0.3', conditional_short: '0.3' } },
    (v: typeof fixture) => { v.forecast.state = 'available_unqualified' },
    (v: typeof fixture) => { v.actions.can_forecast = true },
    (v: typeof fixture) => { v.unrecognized_field = true },
  ]) { const copy = structuredClone(fixture); change(copy); expect(historyStatusSchema.safeParse(copy).success).toBe(false) }
})

it('uses only the independent status route, fixed bounded pages, cookie credentials and caller cancellation', async () => {
  const request = vi.spyOn(clientApi, 'request').mockResolvedValue(fixture)
  const signal = new AbortController().signal
  await historyApi.status({}, signal)
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('/v1/history/status?job_offset=0&job_limit=30&excluded_offset=0&excluded_limit=30',
    { credentials: 'include', signal })
  await expect(historyApi.status({ jobOffset: 1200 })).rejects.toThrow('page_invalid')
  expect(request).toHaveBeenCalledOnce()
  await expect(historyApi.status({ excludedOffset: 30 })).rejects.toThrow('response_mismatch')
})

it('pairs only explicit history credentials and refuses expired session replies without retaining CSRF', async () => {
  const reply = { csrf_token: 'c'.repeat(32), expires_at: new Date(Date.now() + 600000).toISOString() }
  const request = vi.spyOn(clientApi, 'request').mockResolvedValue(reply)
  const token = 't'.repeat(32)
  await historyApi.pairOwner(token)
  expect(request).toHaveBeenCalledWith('/v1/history/owner-session', { method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pairing_token: token }) })
  request.mockResolvedValue({ ...reply, expires_at: '2020-01-01T00:00:00Z' })
  await expect(historyApi.pairOwner(token)).rejects.toThrow('session_expired')
})

it('keeps unknown data unknown, separates sample/model/forecast gates, and preserves the old hourly-data entry', async () => {
  vi.spyOn(historyApi, 'status').mockRejectedValue(new Error('Unexpected read'))
  await renderPage(async (element, navigate) => {
    expect(element.textContent).toContain('尚无可交付预测')
    expect(element.textContent).toContain('尚未配置可用的生产权限')
    expect(element.textContent).toContain('原件尚未在本次读取重新核验')
    expect(element.textContent).toContain('首次获知记录完整，不代表这些资料在历史预测时点已经可用')
    expect(element.querySelector('tbody')?.textContent).toContain('未取得')
    expect(element.querySelector('tbody')?.textContent).not.toContain('0 天')
    expect(element.querySelectorAll('input').length).toBe(0)
    await act(async () => button(element, '查看原有小时行情与数据更新记录').click())
    expect(navigate).toHaveBeenCalledWith('data-jobs')
  }, view())
})

it('reports an unconfigured service as undelivered and disables unusable pairing', async () => {
  vi.spyOn(historyApi, 'status').mockRejectedValue(new clientApi.ApiRequestError('history_owner_not_configured', 'http', 503))
  await renderPage(async element => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(element.textContent).toContain('当前没有可验收的自动历史预测流程')
    expect(button(element, '验证本机身份').disabled).toBe(true)
    expect(element.querySelector('input')?.type).toBe('password')
    expect(element.textContent).not.toContain('模型已登记')
  })
})

it('pairs after explicit user action, clears the token and reads a fresh independent status', async () => {
  const status = vi.spyOn(historyApi, 'status')
    .mockRejectedValueOnce(new clientApi.ApiRequestError('history_owner_session_rejected', 'http', 403))
    .mockResolvedValue(view())
  const pair = vi.spyOn(historyApi, 'pairOwner').mockResolvedValue({ csrf_token: 'c'.repeat(32),
    expires_at: new Date(Date.now() + 600000).toISOString() })
  await renderPage(async element => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    const input = element.querySelector('input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 't'.repeat(32))
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => { button(element, '验证本机身份').click(); await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(pair).toHaveBeenCalledOnce()
    expect(status).toHaveBeenCalledTimes(2)
    for (let index = 0; index < 10 && !element.textContent?.includes('尚无可交付预测'); index++) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    }
    expect(element.textContent).toContain('尚无可交付预测')
    expect(element.textContent).not.toContain('c'.repeat(32))
    expect(element.querySelector('input')).toBeNull()
  })
})

it('marks a failed refresh as an old snapshot and never keeps an old authenticated result after session rejection', async () => {
  const read = vi.spyOn(historyApi, 'status').mockRejectedValue(new clientApi.ApiRequestError('network', 'network'))
  await renderPage(async element => {
    await act(async () => { button(element, '刷新状态').click(); await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(element.textContent).toContain('以下为上次读取的快照')
    expect(element.textContent).toContain('（上次快照）')
    read.mockRejectedValue(new clientApi.ApiRequestError('history_owner_session_rejected', 'http', 403))
    await act(async () => { button(element, '刷新状态').click(); await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(element.textContent).not.toContain('原件与首次获知覆盖')
    expect(element.textContent).toContain('独立本机验证')
  }, view())
})

it('shows saved forecast identity without probabilities and keeps bounded excluded pagination', async () => {
  const copy = view()
  copy.forecast = { ...copy.forecast, state: 'verification_required', forecast_id: 'fixture-forecast',
    scope_id: copy.data.scopes[1].scope_id, cutoff_at: copy.as_of }
  copy.excluded_windows = { total: 31, offset: 0, limit: 30, items: Array.from({ length: 30 }, (_, index) => ({
    scope_id: copy.data.scopes[1].scope_id, cutoff_at: copy.as_of, reason_code: `fixture-excluded-${index}`,
    reason_label: '分钟原件不足', missing_minutes: 1440,
  })) }
  const next = structuredClone(copy); next.excluded_windows = { ...copy.excluded_windows, offset: 30, items: [copy.excluded_windows.items[0]] }
  const read = vi.spyOn(historyApi, 'status').mockResolvedValue(next)
  await renderPage(async element => {
    expect(element.textContent).toContain('最近保存记录：以太坊')
    expect(element.textContent).toContain('已有预测记录仍待原件核验，当前不展示概率')
    expect(element.querySelector('.history-risk-values')).toBeNull()
    await act(async () => { button(element, '下一页').click(); await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(read).toHaveBeenCalledWith({ jobOffset: 0, excludedOffset: 30 }, expect.any(AbortSignal))
    expect(element.textContent).toContain('本页 31–31，共 31 项')
    expect(button(element, '下一页').disabled).toBe(true)
  }, historyStatusSchema.parse(copy))
})

it('accepts the preserved real signed private-API DTO without turning its fixture model into delivered prediction', async () => {
  const saved = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-status-private-api02.json'), 'utf8'))
  const status = historyStatusSchema.parse(saved)
  expect(status.models[0].joint_days).toBe(60)
  expect(status.models[0].method_qualified).toBe(false)
  expect(status.forecast.probabilities).toBeNull()
  vi.spyOn(historyApi, 'status').mockRejectedValue(new Error('No live status read'))
  await renderPage(element => {
    expect(element.textContent).toContain('已有模型原件待核验')
    expect(element.textContent).toContain('最近保存记录：比特币')
    expect(element.textContent).toContain('尚无可交付预测')
    expect(element.querySelector('.history-risk-values')).toBeNull()
  }, status)
})

it('connects the real management and App entry while retaining old data-job and research routes', () => {
  const management = readFileSync(resolve(process.cwd(), 'src/product/ProductPages.tsx'), 'utf8')
  const app = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8')
  expect(management).toContain('label="历史数据与预测能力" onClick={() => onNavigate(\'history-status\')}')
  expect(management).toContain('label="查看数据更新记录" onClick={() => onNavigate(\'data-jobs\')}')
  expect(app).toContain("appRoute.scene === 'history-status' ? <HistoryStatusPage onNavigate={navigate} />")
  expect(app).toContain("appRoute.scene === 'data-jobs' ? <DataJobsPage")
})
