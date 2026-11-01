// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import * as transport from '../api/client'
import * as originals from '../api/historyForecast'
import { historyStatusSchema } from '../api/historyStatus'
import { HistoryForecastRecord } from './HistoryForecastRecord'
import { historyAssetRefreshReplyV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'

// Retained signed private API output with fixture_mode=true; no live forecast qualification.
const saved = JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-forecast-private-api02.json'), 'utf8'))
const status = historyStatusSchema.parse(JSON.parse(readFileSync(resolve(process.cwd(), 'src/product/__fixtures__/history-status-private-api02.json'), 'utf8')))
const forecast = { ...status.forecast, forecast_id: saved.forecast_id, cutoff_at: saved.cutoff_at }
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('reads only the selected saved original and rejects wrong asset, ID, cutoff, invalid probability and uncertain commit', async () => {
  const call = vi.spyOn(transport, 'requestResponse').mockResolvedValue(saved)
  const signal = new AbortController().signal
  const actual = await originals.readHistoryForecast(forecast, signal)
  expect(actual.fixture_mode).toBe(true)
  expect(call).toHaveBeenCalledWith(`/v1/history/forecasts/${saved.forecast_id}`, expect.any(Function),
    { credentials: 'include', signal }, undefined, { timeoutMs: 120_000 })
  for (const modify of [
    (v: typeof saved) => { v.asset = 'ETH' },
    (v: typeof saved) => { v.forecast_id = 'other-forecast' },
    (v: typeof saved) => { v.cutoff_at = '2026-10-08T00:00:00Z' },
    (v: typeof saved) => { v.frequencies[0].probabilities.baseline_long = '1.1' },
    (v: typeof saved) => { v.outcome = 'unconfirmed'; v.committed = null; v.frequencies = [] },
  ]) { const changed = structuredClone(saved); modify(changed); call.mockResolvedValue(changed); await expect(originals.readHistoryForecast(forecast)).rejects.toThrow() }
})

function delayedSavedOriginal(delayMs: number) {
  return vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    const timer = setTimeout(() => {
      init?.signal?.removeEventListener('abort', abort)
      resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }, delayMs)
    const abort = () => { clearTimeout(timer); reject(new DOMException('Caller cancelled original read', 'AbortError')) }
    if (init?.signal?.aborted) abort()
    else init?.signal?.addEventListener('abort', abort, { once: true })
  }))
}

it('accepts a 35-second fixture-clock original read once without a production request or retry', async () => {
  vi.useFakeTimers()
  const fetch = delayedSavedOriginal(35_000); vi.stubGlobal('fetch', fetch)
  const result = originals.readHistoryForecast(forecast).then(value => ({ value, error: null }), error => ({ value: null, error }))
  await vi.advanceTimersByTimeAsync(35_000)
  const actual = await result
  expect(actual.error).toBeNull()
  expect(actual.value?.forecast_id).toBe(saved.forecast_id)
  expect(actual.value?.fixture_mode).toBe(true)
  expect(fetch).toHaveBeenCalledOnce()
  expect(fetch.mock.calls[0][0]).toBe(`/v1/history/forecasts/${saved.forecast_id}`)
  expect(fetch.mock.calls[0][1]?.method).toBeUndefined()
})

it('forwards caller cancellation after 35 fixture-clock seconds and never restarts the read', async () => {
  vi.useFakeTimers()
  const fetch = delayedSavedOriginal(60_000); vi.stubGlobal('fetch', fetch)
  const caller = new AbortController()
  const result = originals.readHistoryForecast(forecast, caller.signal).then(() => null, error => error)
  await vi.advanceTimersByTimeAsync(35_000)
  caller.abort()
  expect((await result).name).toBe('AbortError')
  await vi.advanceTimersByTimeAsync(90_000)
  expect(fetch).toHaveBeenCalledOnce()
})

async function page(check: (element: HTMLDivElement, render: (stale: boolean) => Promise<void>) => Promise<void>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const fetch = vi.fn(() => { throw new Error('No live transport in UI control') }); vi.stubGlobal('fetch', fetch)
  const render = async (stale: boolean) => { await act(async () => {
    root.render(<QueryClientProvider client={client}><HistoryForecastRecord forecast={forecast} authRevision={0} statusStale={stale} /></QueryClientProvider>)
    await new Promise(resolve => setTimeout(resolve, 20))
  }) }
  try { await render(false); await check(element, render); expect(fetch).not.toHaveBeenCalled() }
  finally { await act(async () => root.unmount()); client.clear(); element.remove() }
}
async function click(element: HTMLElement, label: string) {
  const button = Array.from(element.querySelectorAll('button')).find(item => item.textContent?.includes(label))!
  await act(async () => { button.click(); await new Promise(resolve => setTimeout(resolve, 30)) })
  for (let index = 0; index < 5 && button.disabled; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
}

it('requires one explicit original read, displays both thresholds and sample counts, labels fixtures and hides numbers after a rejected recheck', async () => {
  const read = vi.spyOn(originals, 'readHistoryForecast').mockResolvedValue(historyAssetRefreshReplyV2Schema.parse(saved))
  await page(async element => {
    expect(read).not.toHaveBeenCalled()
    expect(element.querySelector('.history-risk-values')).toBeNull()
    await click(element, '核验并查看风险估计')
    expect(read).toHaveBeenCalledOnce()
    expect(element.textContent).toContain('测试数据：不代表真实市场预测')
    expect(element.textContent).toContain('逆向价格变动超过 2%')
    expect(element.textContent).toContain('逆向价格变动超过 5%')
    expect(element.textContent).toContain('历史基线 60 天；当前条件 30 天')
    expect(element.querySelectorAll('.history-risk-values dd').length).toBe(8)
    read.mockRejectedValue(new transport.ApiRequestError('history_owner_session_rejected', 'http', 403))
    await click(element, '重新核验本条记录')
    expect(read).toHaveBeenCalledTimes(2)
    expect(element.querySelector('.history-risk-values')).toBeNull()
    expect(element.textContent).toContain('本机验证已失效')
  })
})

it('does not expose a previously verified result or start a new original read when metadata becomes stale', async () => {
  const read = vi.spyOn(originals, 'readHistoryForecast').mockResolvedValue(historyAssetRefreshReplyV2Schema.parse(saved))
  await page(async (element, render) => {
    await click(element, '核验并查看风险估计')
    expect(element.querySelectorAll('.history-risk-values dd').length).toBe(8)
    await render(true)
    expect(element.querySelector('.history-risk-values')).toBeNull()
    expect(element.querySelector('button')?.disabled).toBe(true)
    expect(read).toHaveBeenCalledOnce()
  })
})
