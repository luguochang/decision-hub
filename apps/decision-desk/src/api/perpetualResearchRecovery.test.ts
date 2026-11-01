// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import fixture from './decisionSettingsReceiptFixture.json'
import { settingsApi } from './decisionSettings'
import { decisionBodyHash as hash } from './decisionOriginals'
import { API_REQUEST_TIMEOUT_MS } from './client'
import { perpetualResearchCommandV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { perpetualResearch, savePerpetualResearchIntent, type PerpetualResearchIntent } from './perpetualResearch'

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: fixture.intent.owner_id, csrf_token: 'test-csrf', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

async function original() {
  const command = perpetualResearchCommandV2Schema.parse({ schema_version: 'perpetual-research-command.v2', request_id: 'original-24h-request', partition: fixture.intent.command.partition,
    expected_settings_revision: fixture.receipt.revision, expected_settings_state_version: 1, horizon: '24h' })
  const intent = { owner_id: fixture.intent.owner_id, command, command_hash: await hash(command) }
  await savePerpetualResearchIntent(intent)
  return intent
}
async function accepted(intent: PerpetualResearchIntent, changed = {}) {
  const body = { schema_version: 'perpetual-research-receipt.v2', horizon: '24h', owner_id: intent.owner_id, request_id: intent.command.request_id,
    command_hash: intent.command_hash, partition: intent.command.partition, settings_revision: intent.command.expected_settings_revision,
    event_id: 'event-original', study_id: 'study-original', run_id: 'run-original', admission_ref: { ref: 'admission-original', hash: 'a'.repeat(64) },
    opportunity_id: 'opportunity-original', accepted_at: '2026-10-19T21:58:54Z', status: 'queued', financial_qualified: false, ...changed }
  return { ...body, payload_hash: await hash(body) }
}
function assertSingleOriginalRead(fetcher: ReturnType<typeof vi.fn>, intent: PerpetualResearchIntent) {
  expect(fetcher.mock.calls.map(([, init]) => init.method)).toEqual(['POST', 'GET'])
  expect(fetcher.mock.calls[1][0]).toBe('/v1/decision-settings/research/commands/' + encodeURIComponent(intent.command.request_id))
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(intent.command)
  expect(new Headers(fetcher.mock.calls[0][1].headers).get('Idempotency-Key')).toBe(intent.command.request_id)
}

it('automatically recovers an accepted original after a lost POST response with one GET and no second POST', async () => {
  const intent = await original(), receipt = await accepted(intent), saved = Array.from({ length: localStorage.length }, (_, i) => [localStorage.key(i), localStorage.getItem(localStorage.key(i)!)])
  const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(new Response(JSON.stringify(receipt)))
  vi.stubGlobal('fetch', fetcher)
  await expect(perpetualResearch.command(intent)).resolves.toEqual(receipt)
  assertSingleOriginalRead(fetcher, intent)
  for (const [key, value] of saved) expect(localStorage.getItem(key!)).toBe(value)
})

it('uses the same recovery after the actual bounded POST timer expires, without changing its 30 second budget', async () => {
  const intent = await original(), receipt = await accepted(intent)
  vi.useFakeTimers()
  const fetcher = vi.fn((_url: unknown, init: RequestInit) => init.method === 'POST'
    ? new Promise<Response>((_resolve, reject) => init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
    : Promise.resolve(new Response(JSON.stringify(receipt))))
  vi.stubGlobal('fetch', fetcher)
  const outcome = perpetualResearch.command(intent).then(value => ({ value }), error => ({ error }))
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
  expect(await outcome).toEqual({ value: receipt })
  assertSingleOriginalRead(fetcher, intent)
  expect(API_REQUEST_TIMEOUT_MS).toBe(30_000)
  expect(vi.getTimerCount()).toBe(0)
})

it.each(['missing', 'unavailable', 'foreign-account', 'foreign-key'])('does not invent acceptance when the one recovery GET returns %s', async kind => {
  const intent = await original()
  const reply = kind === 'missing' ? new Response(JSON.stringify({ detail: 'perpetual_research_receipt_missing' }), { status: 404 })
    : kind === 'unavailable' ? new Response(JSON.stringify({ detail: 'unavailable' }), { status: 503 })
      : new Response(JSON.stringify(await accepted(intent, kind === 'foreign-key' ? { request_id: 'foreign-request' } : { partition: { ...intent.command.partition, account_id: 'foreign-account' } })))
  const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(reply)
  vi.stubGlobal('fetch', fetcher)
  await expect(perpetualResearch.command(intent)).rejects.toThrow(kind === 'missing' ? 'perpetual_research_receipt_missing' : kind === 'unavailable' ? 'unavailable' : 'perpetual_research_receipt_identity_mismatch')
  assertSingleOriginalRead(fetcher, intent)
})

it('does not read a previous owner original after the session changes during the uncertain POST', async () => {
  const intent = await original()
  const fetcher = vi.fn(async () => {
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: 'different-owner', csrf_token: 'other', expires_at: '2099-01-01T00:00:00Z' })
    throw new TypeError('lost response')
  })
  vi.stubGlobal('fetch', fetcher)
  await expect(perpetualResearch.command(intent)).rejects.toThrow()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('keeps an explicit HTTP refusal separate from an uncertain transport outcome', async () => {
  const intent = await original(), fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: 'study_research_active' }), { status: 409 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(perpetualResearch.command(intent)).rejects.toThrow('study_research_active')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('does not present a valid closed-unaccepted receipt as an accepted research', async () => {
  const intent = await original()
  const body = { schema_version: 'perpetual-research-closure-receipt.v1', owner_id: intent.owner_id, request_id: intent.command.request_id,
    command_hash: intent.command_hash, command: intent.command, status: 'closed_unaccepted', reason_code: 'owner_closed_unaccepted', closed_at: '2026-10-19T00:00:00Z' }
  const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(new Response(JSON.stringify({ ...body, payload_hash: await hash(body) })))
  vi.stubGlobal('fetch', fetcher)
  await expect(perpetualResearch.command(intent)).rejects.toMatchObject({ kind: 'network' })
  assertSingleOriginalRead(fetcher, intent)
})
