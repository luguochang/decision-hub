// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import * as transport from '../api/client'
import * as indexApi from '../api/historyForecastIndex'
import * as originals from '../api/historyForecast'
import { historyStatusSchema } from '../api/historyStatus'
import { historyAssetRefreshReplyV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { historyForecastIndexV2Schema } from '../../../../packages/contracts_ts/src/generated/history_forecast_index_v2'
import { HistoryForecastSelector } from './HistoryForecastSelector'

// Synthetic two-asset metadata, canonical-Python validated separately. The BTC
// saved reply is a retained private fixture; ETH below is explicitly synthetic.
const fixture = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-forecast-index-control39.json'), 'utf8'))
const index = () => historyForecastIndexV2Schema.parse(structuredClone(fixture))
const btc = historyAssetRefreshReplyV2Schema.parse(JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-forecast-private-api02.json'), 'utf8')))
const eth = historyAssetRefreshReplyV2Schema.parse({ ...btc, asset: 'ETH', forecast_id: fixture.scopes[1].forecast_id })
const status = historyStatusSchema.parse(JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-status-private-api02.json'), 'utf8')))
const fallback = { ...status.forecast, scope_id: fixture.scopes[1].scope_id, forecast_id: eth.forecast_id, cutoff_at: eth.cutoff_at }
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (element: HTMLElement, label: string) => Array.from(element.querySelectorAll('button')).find(item => item.textContent === label)!
async function settle(condition: () => boolean) {
  for (let attempt = 0; attempt < 15 && !condition(); attempt++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(condition()).toBe(true)
}
async function click(element: HTMLElement, label: string) { await act(async () => { button(element, label).click(); await new Promise(resolve => setTimeout(resolve, 10)) }) }
async function page(check: (element: HTMLDivElement) => Promise<void>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const fetch = vi.fn(() => { throw new Error('No live transport in selector controls') }); vi.stubGlobal('fetch', fetch)
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><HistoryForecastSelector fallbackForecast={fallback}
      authRevision={0} statusStale={false} statusAsOf={status.as_of} /></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 15)) })
    await settle(() => !element.textContent?.includes('正在读取两个资产'))
    await check(element); expect(fetch).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove() }
}

it('uses exact ordered metadata without probabilities, fixed GET credentials and caller cancellation', async () => {
  const request = vi.spyOn(transport, 'request').mockResolvedValue(fixture)
  const signal = new AbortController().signal
  const result = await indexApi.readHistoryForecastIndex(signal)
  expect(result.scopes.map(row => row.asset)).toEqual(['BTC', 'ETH'])
  expect(request).toHaveBeenCalledWith('/v1/history/forecast-index', { credentials: 'include', signal })
  expect(request).toHaveBeenCalledOnce()
  for (const modify of [
    (copy: typeof fixture) => { copy.scopes.reverse() },
    (copy: typeof fixture) => { copy.scopes[0].probabilities = { baseline_long: '0.9' } },
    (copy: typeof fixture) => { copy.scopes[0].forecast_id = 'other' },
    (copy: typeof fixture) => { copy.financial_qualified = true },
    (copy: typeof fixture) => { copy.scopes[0].state = 'not_generated' },
    (copy: typeof fixture) => { copy.scopes[0].recorded_at = '2026-10-09T15:36:00Z' },
    (copy: typeof fixture) => { copy.extra = true },
  ]) { const copy = structuredClone(fixture); modify(copy); expect(historyForecastIndexV2Schema.safeParse(copy).success).toBe(false) }
})

it('selects either saved asset and requires a new explicit read on every asset switch', async () => {
  vi.spyOn(indexApi, 'readHistoryForecastIndex').mockResolvedValue(index())
  const read = vi.spyOn(originals, 'readHistoryForecast').mockImplementation(async forecast => forecast.scope_id?.includes(':BTC-USDT:') ? btc : eth)
  await page(async element => {
    expect(read).not.toHaveBeenCalled(); expect(element.querySelector('.history-risk-values')).toBeNull()
    await click(element, '比特币 BTC')
    expect(element.textContent).toContain('最近保存记录：比特币')
    expect(read).not.toHaveBeenCalled()
    await click(element, '核验并查看风险估计')
    await settle(() => element.querySelectorAll('.history-risk-values dd').length === 8)
    expect(read.mock.calls[0][0].forecast_id).toBe(btc.forecast_id)
    await click(element, '以太坊 ETH')
    expect(element.querySelector('.history-risk-values')).toBeNull()
    await click(element, '核验并查看风险估计')
    await settle(() => element.querySelectorAll('.history-risk-values dd').length === 8)
    expect(read.mock.calls[1][0].forecast_id).toBe(eth.forecast_id)
    await click(element, '比特币 BTC')
    expect(element.querySelector('.history-risk-values')).toBeNull()
    await click(element, '核验并查看风险估计')
    await settle(() => read.mock.calls.length === 3)
    expect(read.mock.calls[2][0].forecast_id).toBe(btc.forecast_id)
  })
})

it('cancels an in-progress original when switching assets and never produces or retries it', async () => {
  vi.spyOn(indexApi, 'readHistoryForecastIndex').mockResolvedValue(index())
  let cancelled = false
  const read = vi.spyOn(originals, 'readHistoryForecast').mockImplementation((forecast, signal) => {
    if (forecast.scope_id?.includes(':BTC-USDT:')) return Promise.resolve(btc)
    return new Promise((_, reject) => signal?.addEventListener('abort', () => {
      cancelled = true; reject(new DOMException('Cancelled by asset switch', 'AbortError'))
    }, { once: true }))
  })
  await page(async element => {
    await click(element, '核验并查看风险估计')
    await settle(() => read.mock.calls.length === 1)
    await click(element, '比特币 BTC')
    expect(cancelled).toBe(true); expect(element.querySelector('.history-risk-values')).toBeNull()
    expect(read).toHaveBeenCalledOnce()
    await click(element, '核验并查看风险估计')
    await settle(() => element.querySelectorAll('.history-risk-values dd').length === 8)
    expect(read).toHaveBeenCalledTimes(2)
  })
})

it('hides verified numbers when the metadata identity session is rejected', async () => {
  const readIndex = vi.spyOn(indexApi, 'readHistoryForecastIndex').mockResolvedValue(index())
  vi.spyOn(originals, 'readHistoryForecast').mockResolvedValue(eth)
  await page(async element => {
    await click(element, '核验并查看风险估计')
    await settle(() => element.querySelectorAll('.history-risk-values dd').length === 8)
    readIndex.mockRejectedValue(new transport.ApiRequestError('owner_rejected', 'http', 403))
    await click(element, '刷新资产记录')
    await settle(() => element.textContent?.includes('本机验证已失效') === true)
    expect(element.querySelector('.history-risk-values')).toBeNull()
  })
})

it('keeps an older single-record server readable without offering another asset or producing a forecast', async () => {
  vi.spyOn(indexApi, 'readHistoryForecastIndex').mockRejectedValue(new transport.ApiRequestError('old_api', 'http', 404))
  const read = vi.spyOn(originals, 'readHistoryForecast').mockResolvedValue(eth)
  await page(async element => {
    expect(element.textContent).toContain('按资产选择尚未提供')
    expect(button(element, '比特币 BTC').disabled).toBe(true)
    expect(read).not.toHaveBeenCalled()
    await click(element, '核验并查看风险估计')
    await settle(() => element.querySelectorAll('.history-risk-values dd').length === 8)
    expect(read).toHaveBeenCalledOnce()
  })
})
