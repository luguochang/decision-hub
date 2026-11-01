import { riskTimelinePointSchema, riskQuoteViewSchema, riskSummaryViewSchema } from '@decision-hub/contracts-ts'
import { afterEach, expect, it, vi } from 'vitest'
import { riskApi } from './risk'
import { riskSummaryFixture, riskTimelineFixture, riskScopeFixture, RISK_AT } from './riskFixtures'

afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }))) }

it.each(['1', '44000.1', '0.00001'])('canonical Zod prices accept decimal %s', price => {
  expect(riskTimelinePointSchema.parse({ at: RISK_AT, price }).price).toBe(price)
  expect(riskQuoteViewSchema.parse({ ...riskSummaryFixture.quote, price }).price).toBe(price)
})
it.each(['NaN', 'Infinity', '-1', '1e3', '.1'])('canonical Zod prices reject %s', price => {
  expect(() => riskTimelinePointSchema.parse({ at: RISK_AT, price })).toThrow()
})
it('requires explicit unknown and historical observed-path semantics', () => {
  expect(riskSummaryViewSchema.parse(riskSummaryFixture).risk_level).toBe('unknown')
  expect(() => riskSummaryViewSchema.parse({ ...riskSummaryFixture, risk_level: undefined })).toThrow()
  expect(() => riskSummaryViewSchema.parse({ ...riskSummaryFixture, window: { ...riskSummaryFixture.window, kind: 'future_guarantee' } })).toThrow()
})
it('rejects a wrong-scope summary and a zero quote instead of treating it as a success', async () => {
  response({ ...riskSummaryFixture, scope_id: 'another-scope' })
  await expect(riskApi.summary(riskScopeFixture.scope_id)).rejects.toThrow('当前资产不一致')
  response({ ...riskSummaryFixture, quote: { ...riskSummaryFixture.quote, price: '0' } })
  await expect(riskApi.summary(riskScopeFixture.scope_id)).rejects.toThrow('行情价格无法核对')
})
it('requires explicit timeline gaps and matching time bounds', async () => {
  response({ ...riskTimelineFixture, missing_ranges: undefined })
  await expect(riskApi.timeline(riskScopeFixture.scope_id, riskTimelineFixture.start, riskTimelineFixture.end)).rejects.toThrow()
  response({ ...riskTimelineFixture, end: '2026-10-08T00:01:00Z' })
  await expect(riskApi.timeline(riskScopeFixture.scope_id, riskTimelineFixture.start, riskTimelineFixture.end)).rejects.toThrow('时间范围')
})
it('uses the original command identity and refuses a different alert receipt', async () => {
  response({ schema_version: 'market-risk-receipt.v1', alert_id: 'other', request_id: 'request-one', status: 'recorded', recorded_at: RISK_AT })
  await expect(riskApi.acknowledge('alert_fixture', 'request-one', RISK_AT)).rejects.toThrow('原操作不一致')
  const fetch = vi.mocked(globalThis.fetch)
  const init = fetch.mock.calls[0][1]!
  expect(JSON.parse(String(init.body))).toEqual({ request_id: 'request-one', client_time: RISK_AT })
  expect(new Headers(init.headers).get('Idempotency-Key')).toBe('request-one')
  expect(new Headers(init.headers).get('X-Owner-Id')).toBe('owner')
})
