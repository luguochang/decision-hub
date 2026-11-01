// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { historyOutcomeIndexV2Schema, type HistoryOutcomeIndexV2 } from '../../../../packages/contracts_ts/src/generated/history_outcome_index_v2'
import { historyOutcomeStatusV2Schema } from '../../../../packages/contracts_ts/src/generated/history_outcome_v2'
import * as indexApi from '../api/historyOutcomeEvaluations'
import * as clientApi from '../api/client'
import * as outcomeApi from '../api/historyOutcome'
import { historyApi, historyStatusSchema } from '../api/historyStatus'
import * as scheduleApi from '../api/historySchedule'
import * as forecastIndexApi from '../api/historyForecastIndex'
import outcomeFixture from './__fixtures__/history-outcome-control40.json'
import productFixture from './__fixtures__/history-product-status-v2.json'
import { HistoryOutcomeEntry } from './HistoryOutcomeEntry'
import { HistoryStatusPage } from './HistoryStatusPage'

// Synthetic index + already sealed strict outcome wire fixture only. No real
// owner, market label, producer, registration or financial admission is claimed.
const indexFixture = {
  schema_version: 'history-outcome-index.v2', owner_authenticated: true,
  actual_as_of: '2026-10-13T12:00:00Z', current_originals_verified: false,
  financial_qualified: false, promotion_allowed: false,
  evaluations: [
    { protocol_id: outcomeFixture.status.protocol_id, prepared_registered_at: '2026-10-10T10:00:00Z',
      independently_confirmed_registered_at: '2026-10-10T10:00:01Z', first_hour: '2026-10-10T11:00:00Z',
      last_hour: '2026-10-11T10:00:00Z', expected_total: 48, state: 'confirmed', fixture_mode: false },
    { protocol_id: 'heval2_' + '2'.repeat(48), prepared_registered_at: '2026-10-09T10:00:00Z',
      independently_confirmed_registered_at: null, first_hour: '2026-10-09T11:00:00Z',
      last_hour: '2026-10-10T10:00:00Z', expected_total: 48, state: 'prepared', fixture_mode: false },
  ],
}
const index = () => historyOutcomeIndexV2Schema.parse(structuredClone(indexFixture))
const empty = () => historyOutcomeIndexV2Schema.parse({ ...indexFixture, evaluations: [] })
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const pause = async () => { await new Promise(resolve => setTimeout(resolve, 20)) }
const button = (element: HTMLElement, label: string) => Array.from(element.querySelectorAll('button')).find(item => item.textContent === label)!

async function entry(check: (element: HTMLDivElement, render: (authRevision: number, stale?: boolean) => Promise<void>) => Promise<void>) {
  const fetch = vi.fn(() => { throw new Error('HTTP forbidden in isolated PC entry control') })
  vi.stubGlobal('fetch', fetch)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const render = async (authRevision: number, stale = false) => { await act(async () => {
    root.render(<HistoryOutcomeEntry authRevision={authRevision} ownerStatusStale={stale} />)
    await pause()
  }) }
  try { await render(0); await check(element, render); expect(fetch).not.toHaveBeenCalled() }
  finally { await act(async () => root.unmount()); element.remove() }
}

it('makes exactly one signed index GET and rejects invalid wire identity, scope, qualification and extra fields', async () => {
  const signal = new AbortController().signal
  const call = vi.spyOn(clientApi, 'requestResponse').mockImplementation(async (_path, read) => read(new Response(JSON.stringify(indexFixture))))
  const value = await indexApi.readHistoryOutcomeEvaluations(signal)
  expect(value.evaluations.map(item => item.protocol_id)).toEqual(indexFixture.evaluations.map(item => item.protocol_id))
  expect(call).toHaveBeenCalledOnce()
  expect(call.mock.calls[0][0]).toBe('/v1/history/evaluations')
  expect(call.mock.calls[0][2]).toEqual({ method: 'GET', credentials: 'include', signal })
  for (const modify of [
    (copy: typeof indexFixture) => { copy.evaluations.reverse() },
    (copy: typeof indexFixture) => { copy.evaluations[0].protocol_id = 'ungranted' },
    (copy: typeof indexFixture) => { copy.evaluations[0].expected_total = 47 },
    (copy: typeof indexFixture) => { copy.financial_qualified = true },
    (copy: typeof indexFixture) => { copy.evaluations[0].independently_confirmed_registered_at = null },
    (copy: typeof indexFixture) => { Object.assign(copy, { additional: true }) },
  ]) {
    const copy = structuredClone(indexFixture); modify(copy)
    call.mockImplementation(async (_path, read) => read(new Response(JSON.stringify(copy))))
    await expect(indexApi.readHistoryOutcomeEvaluations(signal)).rejects.toThrow()
  }
  expect(call).toHaveBeenCalledTimes(7)
})

it('mounts the new entry on the existing history page, explains zero grants and configured errors without registering or reading originals', async () => {
  const fast = vi.spyOn(indexApi, 'readHistoryOutcomeEvaluations').mockResolvedValue(empty())
  const status = vi.spyOn(outcomeApi, 'readHistoryOutcomeStatus')
  const score = vi.spyOn(outcomeApi, 'readHistoryOutcomeScore')
  const observation = vi.spyOn(outcomeApi, 'readHistoryOutcomeObservation')
  vi.spyOn(historyApi, 'status').mockResolvedValue(historyStatusSchema.parse(productFixture))
  vi.spyOn(scheduleApi, 'readHistorySchedule').mockRejectedValue(new clientApi.ApiRequestError('not_deployed', 'http', 503))
  vi.spyOn(forecastIndexApi, 'readHistoryForecastIndex').mockRejectedValue(new clientApi.ApiRequestError('not_deployed', 'http', 503))
  const fetch = vi.fn(() => { throw new Error('No live transport in page integration') })
  vi.stubGlobal('fetch', fetch)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['history-product-status-v2', 0, 0, 0], historyStatusSchema.parse(productFixture))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><HistoryStatusPage onNavigate={vi.fn()} /></QueryClientProvider>)
      await pause()
    })
    await act(pause)
    expect(element.querySelector('[aria-label="前瞻验证批次"]')).not.toBeNull()
    expect(element.textContent).toContain('尚未开始正式前瞻验证')
    expect(element.querySelectorAll('input')).toHaveLength(0)
    expect(fast).toHaveBeenCalledOnce()
    expect(status).not.toHaveBeenCalled(); expect(score).not.toHaveBeenCalled(); expect(observation).not.toHaveBeenCalled()
    fast.mockRejectedValueOnce(new clientApi.ApiRequestError('absent', 'http', 503))
    await act(async () => { button(element, '刷新验证列表').click(); await pause() })
    expect(element.textContent).toContain('服务尚未配置或部署')
    expect(fast).toHaveBeenCalledTimes(2)
    expect(score).not.toHaveBeenCalled(); expect(observation).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove() }
})

it('selects only server-listed batches, cancels old originals on refresh and discards late responses after authentication changes', async () => {
  const fast = vi.spyOn(indexApi, 'readHistoryOutcomeEvaluations').mockResolvedValue(index())
  const status = vi.spyOn(outcomeApi, 'readHistoryOutcomeStatus').mockImplementation(async protocolId =>
    historyOutcomeStatusV2Schema.parse({ ...outcomeFixture.status, protocol_id: protocolId }))
  let completeScore!: (value: outcomeApi.HistoryOutcomeScoreReplyV2) => void
  let scoreSignal: AbortSignal | undefined
  const score = vi.spyOn(outcomeApi, 'readHistoryOutcomeScore').mockImplementation((_protocol, _original, signal) => {
    scoreSignal = signal
    return new Promise(resolve => { completeScore = resolve })
  })
  const observation = vi.spyOn(outcomeApi, 'readHistoryOutcomeObservation')
  await entry(async (element, render) => {
    await act(pause)
    expect(status.mock.calls[0][0]).toBe(indexFixture.evaluations[0].protocol_id)
    expect(element.textContent).toContain('受阻：46')
    expect(element.textContent).toContain('1 / 48')
    expect(element.querySelectorAll('input')).toHaveLength(0)
    const select = element.querySelector<HTMLSelectElement>('select[aria-label="选择验证批次"]')!
    expect(select.options).toHaveLength(2)
    expect(select.textContent).not.toContain('heval2_')
    expect(score).not.toHaveBeenCalled(); expect(observation).not.toHaveBeenCalled()
    await act(async () => {
      select.value = indexFixture.evaluations[1].protocol_id
      select.dispatchEvent(new Event('change', { bubbles: true })); await pause()
    })
    await act(pause)
    expect(status.mock.calls.at(-1)?.[0]).toBe(indexFixture.evaluations[1].protocol_id)
    expect(element.textContent).toContain('不能当作已经开始的正式前瞻验证')
    await act(async () => { button(element, '读取评分原件').click(); await pause() })
    expect(score).toHaveBeenCalledOnce()
    let completeOld!: (value: HistoryOutcomeIndexV2) => void
    let completeNew!: (value: HistoryOutcomeIndexV2) => void
    let oldSignal: AbortSignal | undefined
    fast.mockImplementationOnce(signal => { oldSignal = signal; return new Promise(resolve => { completeOld = resolve }) })
    await act(async () => { button(element, '刷新验证列表').click(); await pause() })
    expect(scoreSignal?.aborted).toBe(true)
    expect(element.querySelector('[aria-label="预测结果验收"]')).toBeNull()
    fast.mockImplementationOnce(() => new Promise(resolve => { completeNew = resolve }))
    await render(1)
    expect(oldSignal?.aborted).toBe(true)
    await act(async () => { completeOld(index()); completeScore(outcomeFixture.scoreReply as outcomeApi.HistoryOutcomeScoreReplyV2); await pause() })
    expect(element.querySelector('select')).toBeNull()
    expect(element.querySelector('[aria-label="已核验评分"]')).toBeNull()
    await act(async () => { completeNew(empty()); await pause() })
    expect(element.textContent).toContain('尚未开始正式前瞻验证')
    fast.mockRejectedValueOnce(new clientApi.ApiRequestError('rejected', 'http', 403))
    await act(async () => { button(element, '刷新验证列表').click(); await pause() })
    expect(element.textContent).toContain('本机身份已失效')
    expect(score).toHaveBeenCalledOnce(); expect(observation).not.toHaveBeenCalled()
    await render(2, true)
    expect(element.textContent).toContain('历史身份状态尚未确认')
    expect(element.querySelector('select')).toBeNull()
  })
})
