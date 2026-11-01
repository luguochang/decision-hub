// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import * as clientApi from '../api/client'
import * as scheduleApi from '../api/historySchedule'
import * as forecastApi from '../api/historyForecast'
import { historyHourlyStatusV2Schema } from '../../../../packages/contracts_ts/src/generated/history_hourly_status_v2'
import { historyStatusSchema } from '../api/historyStatus'
import { HistoryHourlyStatus } from './HistoryHourlyStatus'

// Preserved actual signed private-API response; fixture_mode=true, never a
// delivered market prediction. Counterexamples below are isolated UI inputs.
const saved = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-hourly-status-private-api03.json'), 'utf8'))
const statusSaved = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-product-status-v2.json'), 'utf8'))
const status = () => historyStatusSchema.parse(structuredClone(statusSaved))
const view = () => historyHourlyStatusV2Schema.parse(structuredClone(saved))
const button = (element: HTMLElement, label: string) => Array.from(element.querySelectorAll('button')).find(row => row.textContent === label)!
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function render(check: (element: HTMLDivElement, advance: (ms: number) => Promise<void>) => Promise<void> | void,
  initialStatus = status()) {
  vi.useFakeTimers()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const fetch = vi.fn(() => { throw new Error('No real transport in isolated UI controls') })
  vi.stubGlobal('fetch', fetch)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><HistoryHourlyStatus
      authRevision={0} ownerStatusStale={false} models={initialStatus.models} existingForecast={initialStatus.forecast}
    /></QueryClientProvider>) })
    await advance(20)
    await check(element, advance)
    expect(fetch).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove() }
}

it('validates the saved signed API shape, exact BTC/ETH order, identity and false qualification', async () => {
  expect(view().scopes.map(row => row.state)).toEqual(['blocked', 'unconfirmed'])
  const request = vi.spyOn(clientApi, 'request').mockResolvedValue(saved)
  const signal = new AbortController().signal
  await scheduleApi.readHistorySchedule(signal)
  expect(request).toHaveBeenCalledWith('/v1/history/schedule', { credentials: 'include', signal })
  expect(request).toHaveBeenCalledOnce()
  for (const change of [
    (copy: typeof saved) => { copy.scopes.reverse() },
    (copy: typeof saved) => { copy.scopes[1].scope_id = copy.scopes[0].scope_id },
    (copy: typeof saved) => { copy.financial_qualified = true },
    (copy: typeof saved) => { copy.current_forecast_originals_verified = true },
    (copy: typeof saved) => { copy.scopes[0].probabilities = { baseline_long: '0.7' } },
  ]) { const copy = structuredClone(saved); change(copy); expect(historyHourlyStatusV2Schema.safeParse(copy).success).toBe(false) }
})

it('shows both actual blocked/unconfirmed records without claiming configured means online or using ids as forecasts', async () => {
  const read = vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(view())
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await render(element => {
    expect(read).toHaveBeenCalledOnce()
    expect(element.textContent).toContain('比特币')
    expect(element.textContent).toContain('以太坊')
    expect(element.textContent).toContain('尚无服务器授权的可信模型')
    expect(element.textContent).toContain('自动生产已暂停')
    expect(element.textContent).toContain('配置状态不代表 Worker 在线')
    expect(element.textContent).toContain('测试数据记录')
    expect(button(element, '核验并查看风险估计')).toBeUndefined()
    expect(original).not.toHaveBeenCalled()
  })
})

it('polls only fast metadata each 60 seconds and keeps original reads and production disabled', async () => {
  const copy = view()
  copy.scopes[0] = { ...copy.scopes[0], state: 'committed_verified', fixture_mode: false,
    forecast_id: `hforecast2_${'a'.repeat(48)}`, reason_code: null, reason_label: '结果已保存并在提交后核验通过。' }
  const read = vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(copy)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await render(async (element, advance) => {
    expect(element.textContent).toContain('当时保存及核验通过')
    expect(element.querySelector('.history-risk-values')).toBeNull()
    await advance(59_000)
    expect(read).toHaveBeenCalledOnce()
    await advance(1_000)
    expect(read).toHaveBeenCalledTimes(2)
    await advance(60_000)
    expect(read).toHaveBeenCalledTimes(3)
    expect(original).not.toHaveBeenCalled()
  })
})

it('maps verified scope/id/T to the existing explicit original reader, without starting a producer', async () => {
  const copy = view()
  copy.scopes[1] = { ...copy.scopes[1], state: 'committed_verified', fence_present: false,
    forecast_id: `hforecast2_${'b'.repeat(48)}`, reason_code: null, reason_label: '当时提交核验通过。' }
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(copy)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast').mockRejectedValue(new clientApi.ApiRequestError('unconfirmed', 'http', 409))
  await render(async (element, advance) => {
    expect(original).not.toHaveBeenCalled()
    await act(async () => button(element, '核验并查看风险估计').click())
    await advance(20)
    expect(original).toHaveBeenCalledOnce()
    expect(original).toHaveBeenCalledWith(expect.objectContaining({ forecast_id: copy.scopes[1].forecast_id,
      scope_id: copy.scopes[1].scope_id, cutoff_at: copy.scopes[1].cutoff_at, probabilities: null }), expect.any(AbortSignal))
    expect(element.textContent).toContain('当前无法展示风险估计')
    await advance(60_000)
    expect(original).toHaveBeenCalledOnce()
    expect(element.querySelector('.history-risk-values')).toBeNull()
  })
})

it('keeps a missing forecast id unreadable and avoids duplicating the same existing risk record', async () => {
  const copy = view()
  copy.scopes[0] = { ...copy.scopes[0], state: 'committed_verified', forecast_id: null }
  copy.scopes[1] = { ...copy.scopes[1], state: 'committed_verified', forecast_id: `hforecast2_${'c'.repeat(48)}` }
  const current = status()
  current.forecast = { ...current.forecast, state: 'verification_required', forecast_id: copy.scopes[1].forecast_id,
    scope_id: copy.scopes[1].scope_id, cutoff_at: copy.scopes[1].cutoff_at }
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(copy)
  const original = vi.spyOn(forecastApi, 'readHistoryForecast')
  await render(element => {
    expect(element.textContent).toContain('缺少可核对的预测身份或截点')
    expect(element.textContent).toContain('在下方风险记录中列出')
    expect(button(element, '核验并查看风险估计')).toBeUndefined()
    expect(original).not.toHaveBeenCalled()
  }, current)
})

it('marks failed metadata refresh as an old snapshot and hides records after rejected owner session', async () => {
  const read = vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValueOnce(view())
    .mockRejectedValueOnce(new clientApi.ApiRequestError('network', 'network'))
    .mockRejectedValue(new clientApi.ApiRequestError('owner_rejected', 'http', 403))
  await render(async (element, advance) => {
    await advance(60_000)
    expect(element.textContent).toContain('以下为上次快照')
    expect(element.textContent).toContain('比特币')
    await advance(60_000)
    expect(element.textContent).toContain('小时记录已隐藏')
    expect(element.textContent).not.toContain('比特币')
    await advance(120_000)
    expect(read).toHaveBeenCalledTimes(3)
  })
})

it('shows actual supplied missing-sample counts and unfitted model without turning configuration into delivery', async () => {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockResolvedValue(view())
  const current = status()
  current.models[0] = { ...current.models[0], state: 'blocked_insufficient_samples', joint_days: 53,
    low_days: 26, high_days: 27, count_gate_met: false }
  await render(element => {
    expect(element.textContent).toContain('有效共同样本 53 天')
    expect(element.textContent).toContain('该小时首次执行时的记录')
    expect(element.textContent).toContain('之后的授权或数据变化不会改写这条历史原因')
    expect(element.textContent).toContain('低 / 高波动组 26 天 / 27 天')
    expect(element.textContent).toContain('数量门尚未满足')
    expect(element.textContent).toContain('模型尚未拟合')
    expect(element.querySelector('.history-risk-values')).toBeNull()
  }, current)
})

it('clearly reports an unconfigured schedule service without pretending that the worker is running', async () => {
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockRejectedValue(new clientApi.ApiRequestError('not_configured', 'http', 503))
  await render(element => {
    expect(element.textContent).toContain('小时流程服务尚未配置')
    expect(element.textContent).not.toContain('已配置每小时流程')
    expect(element.querySelector('.history-hourly-assets')).toBeNull()
  })
})
