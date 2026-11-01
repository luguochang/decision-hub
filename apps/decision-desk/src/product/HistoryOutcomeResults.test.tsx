// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from './__fixtures__/history-outcome-control40.json'
import { historyOutcomeStatusV2Schema, historyOutcomeScoreReplyV2Schema, historyOutcomeObservationReplyV2Schema } from '../../../../packages/contracts_ts/src/generated/history_outcome_v2'
import * as outcomeApi from '../api/historyOutcome'
import * as clientApi from '../api/client'
import { HistoryOutcomeResults } from './HistoryOutcomeResults'

// Synthetic wire data; the false qualification fields do not prove real source
// authentication. This control calls no HTTP, producer or historical model.
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const status = () => historyOutcomeStatusV2Schema.parse(structuredClone(fixture.status))
const score = () => historyOutcomeScoreReplyV2Schema.parse(structuredClone(fixture.scoreReply))
const button = (element: HTMLElement, label: string) => Array.from(element.querySelectorAll('button')).find(row => row.textContent === label)!
const pause = async () => { await new Promise(resolve => setTimeout(resolve, 20)) }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function renderPage(check: (element: HTMLDivElement, change: (protocol: string | null) => Promise<void>) => Promise<void>) {
  const fetch = vi.fn(() => { throw new Error('Network forbidden in isolated UI controls') })
  vi.stubGlobal('fetch', fetch)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const change = async (protocol: string | null) => { await act(async () => {
    root.render(<HistoryOutcomeResults protocolId={protocol} />); await pause()
  }) }
  try { await change(fixture.status.protocol_id); await check(element, change); expect(fetch).not.toHaveBeenCalled() }
  finally { await act(async () => root.unmount()); element.remove() }
}

it('strictly aligns fixed census, time, pair and acceptance semantics with the public Python contract', () => {
  expect(status().expected_total).toBe(48)
  expect(score().score.cases[0].acceptance).not.toBeNull()
  expect(historyOutcomeObservationReplyV2Schema.parse(fixture.observationReply).observation.accepted_at).toBeNull()
  for (const value of [
    { ...fixture.status, financial_qualified: true }, { ...fixture.status, owner_authenticated: 1 },
    { ...fixture.status, publication_counts: { ...fixture.status.publication_counts, blocked: 45 } },
    { ...fixture.status, paired_count: 3 }, { ...fixture.status, independently_confirmed_registered_at: null },
    { ...fixture.status, latest_score: { ...fixture.status.latest_score, identifier: 'heval2_' + 'e'.repeat(48) } },
    { ...fixture.status, opportunities: [fixture.status.opportunities[1], fixture.status.opportunities[0], ...fixture.status.opportunities.slice(2)] },
    { ...fixture.status, opportunities: [{ ...fixture.status.opportunities[0], actual_cutoff_at: '2026-10-10T11:00:00.000001Z' }, ...fixture.status.opportunities.slice(1)] },
    { ...fixture.status, actual_as_of: '2026-10-13T12:00:00' }, { ...fixture.status, unexpected: true },
  ]) expect(historyOutcomeStatusV2Schema.safeParse(value).success).toBe(false)
  for (const replacement of [
    { ...fixture.scoreReply.score, cases: [{ ...fixture.scoreReply.score.cases[0], acceptance: null }, ...fixture.scoreReply.score.cases.slice(1)] },
    { ...fixture.scoreReply.score, fixture_mode: true },
    { ...fixture.scoreReply.score, targets: [{ ...fixture.scoreReply.score.targets[0], conditional_brier: null }, ...fixture.scoreReply.score.targets.slice(1)] },
    { ...fixture.scoreReply.score, targets: [{ ...fixture.scoreReply.score.targets[0], baseline_brier: '1.000000000000000000000000001' }, ...fixture.scoreReply.score.targets.slice(1)] },
    { ...fixture.scoreReply.score, source_files: [['synthetic.py', 'invalid']] },
  ]) expect(historyOutcomeScoreReplyV2Schema.safeParse({ ...fixture.scoreReply, score: replacement }).success).toBe(false)
  for (const replacement of [
    { ...fixture.observationReply.observation, accepted_at: '2026-10-13T12:00:02Z' },
    { ...fixture.observationReply.observation, observed_count: 1439 },
    { ...fixture.observationReply.observation, classification: 'late_diagnostic' },
    { ...fixture.observationReply.observation, anchor_price: '-1E-28' },
  ]) expect(historyOutcomeObservationReplyV2Schema.safeParse({ ...fixture.observationReply, observation: replacement }).success).toBe(false)
})

it('uses one signed GET, binds the full result to its saved reference and never retries an invalid response', async () => {
  const signal = new AbortController().signal
  const mock = vi.spyOn(clientApi, 'requestResponse').mockImplementation(async (_path, read) => read(new Response(JSON.stringify(fixture.status))))
  await outcomeApi.readHistoryOutcomeStatus(fixture.status.protocol_id, 0, signal)
  expect(mock).toHaveBeenCalledOnce()
  expect(mock.mock.calls[0][0]).toBe(`/v1/history/evaluations/${fixture.status.protocol_id}/status?offset=0&limit=30`)
  expect(mock.mock.calls[0][2]).toEqual({ method: 'GET', credentials: 'include', signal })
  mock.mockImplementation(async (_path, read) => read(new Response(JSON.stringify(fixture.scoreReply))))
  await outcomeApi.readHistoryOutcomeScore(fixture.status.protocol_id, fixture.status.latest_score, signal)
  expect(mock.mock.calls[1][4]).toEqual({ timeoutMs: 120_000 })
  await expect(outcomeApi.readHistoryOutcomeScore(fixture.status.protocol_id, { ...fixture.status.latest_score, payload_hash: 'e'.repeat(64) }, signal)).rejects.toThrow('response_identity_mismatch')
  expect(mock).toHaveBeenCalledTimes(3)
  await expect(outcomeApi.readHistoryOutcomeStatus(fixture.status.protocol_id, 48)).rejects.toThrow('page_invalid')
  expect(mock).toHaveBeenCalledTimes(3)
})

it('shows the full denominator and requires an explicit full original read before displaying four paired scores', async () => {
  vi.spyOn(outcomeApi, 'readHistoryOutcomeStatus').mockResolvedValue(status())
  const full = vi.spyOn(outcomeApi, 'readHistoryOutcomeScore').mockResolvedValue(score())
  const observation = vi.spyOn(outcomeApi, 'readHistoryOutcomeObservation').mockResolvedValue(historyOutcomeObservationReplyV2Schema.parse(fixture.observationReply))
  await renderPage(async element => {
    expect(element.querySelector('tbody td')?.textContent).toBe('2026年10月10日 11:00 UTC')
    expect(element.textContent).toContain('1 / 48')
    expect(element.textContent).toContain('受阻：46')
    expect(element.textContent).toContain('迟到发布：1')
    expect(element.textContent).toContain('尚未重新核验完整原件')
    expect(element.querySelectorAll('input')).toHaveLength(0)
    expect(full).not.toHaveBeenCalled(); expect(observation).not.toHaveBeenCalled()
    await act(async () => { button(element, '读取评分原件').click(); await pause() })
    expect(full).toHaveBeenCalledOnce()
    expect(element.querySelector('[aria-label="已核验评分"] tbody')?.children).toHaveLength(4)
    expect(element.textContent).toContain('0.2175')
    expect(element.textContent).toContain('发布迟到')
    await act(async () => { button(element, '读取该路径原件').click(); await pause() })
    expect(observation).toHaveBeenCalledOnce()
    expect(element.textContent).toContain('1440 / 1440')
    expect(element.textContent).toContain('主体不声明接纳时间')
  })
})

it('cancels full reads, rejects late completion and pages only the fixed 48 opportunities', async () => {
  const fast = vi.spyOn(outcomeApi, 'readHistoryOutcomeStatus').mockResolvedValue(status())
  let complete!: (value: outcomeApi.HistoryOutcomeScoreReplyV2) => void
  const full = vi.spyOn(outcomeApi, 'readHistoryOutcomeScore').mockReturnValue(new Promise(resolve => { complete = resolve }))
  await renderPage(async element => {
    await act(async () => { button(element, '读取评分原件').click(); await pause() })
    const signal = full.mock.calls[0][2]!
    await act(async () => button(element, '取消原件读取').click())
    expect(signal.aborted).toBe(true)
    await act(async () => { complete(score()); await pause() })
    expect(element.querySelector('[aria-label="已核验评分"]')).toBeNull()
    fast.mockResolvedValue(historyOutcomeStatusV2Schema.parse({ ...fixture.status, pagination: { offset: 30, limit: 30, total: 48 }, opportunities: fixture.opportunities.slice(30) }))
    await act(async () => { button(element, '下一页机会').click(); await pause() })
    expect(fast.mock.calls[1][1]).toBe(30)
    expect(element.textContent).toContain('第 31—48 条 / 48')
  })
})

it('clears verified results when permission or configured protocol changes and does not automatically reread', async () => {
  const fast = vi.spyOn(outcomeApi, 'readHistoryOutcomeStatus').mockResolvedValue(status())
  const full = vi.spyOn(outcomeApi, 'readHistoryOutcomeScore').mockResolvedValue(score())
  await renderPage(async (element, change) => {
    await act(async () => { button(element, '读取评分原件').click(); await pause() })
    expect(element.querySelector('[aria-label="已核验评分"]')).not.toBeNull()
    fast.mockRejectedValue(new clientApi.ApiRequestError('history_outcome_protocol_not_granted', 'http', 403))
    await act(async () => { button(element, '刷新评估状态').click(); await pause() })
    expect(element.textContent).toContain('当前身份未获此评估的读取权限')
    expect(element.querySelector('[aria-label="已核验评分"]')).toBeNull()
    await change(null)
    expect(element.textContent).toContain('尚未配置可读取的评估')
    expect(full).toHaveBeenCalledOnce()
  })
})
