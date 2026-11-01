import { afterEach, expect, it, vi } from 'vitest'
import { forecastAlarmListSchema, forecastAlarmsApi, type ForecastAlarmReceipt, type ForecastAlarmView } from './forecastAlarms'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('rejects unqualified-list claims of probability qualification', () => {
  const empty = { schema_version: 'forecast-alarm-list.v1', scope_id: 'BTC', as_of: '2026-10-08T00:00:00Z', items: [],
    experimental: true, default_collapsed: true, probability_qualified: false, trading_authority: false }
  expect(forecastAlarmListSchema.parse(empty)).toEqual(empty)
  expect(() => forecastAlarmListSchema.parse({ ...empty, probability_qualified: true })).toThrow()
})
it('posts the original visibility parent and retains the request key on a response mismatch', async () => {
  const alarm = { alarm_id: 'alarm', payload_hash: 'a'.repeat(64) } as ForecastAlarmView['alarm']
  const parent = { receipt_id: 'original-display', payload_hash: 'c'.repeat(64), alarm_id: alarm.alarm_id, alarm_hash: alarm.payload_hash, owner_id: 'owner' } as ForecastAlarmReceipt
  const fetch = vi.fn().mockImplementation(async (_url, init) => {
    const command = JSON.parse(init.body)
    expect(command.display_receipt_id).toBe('original-display')
    expect(command.visible_duration_ms).toBe(1000)
    expect(new Headers(init.headers).get('X-Owner-Id')).toBe('owner')
    expect(new Headers(init.headers).get('Idempotency-Key')).toBe(command.request_id)
    return new Response(JSON.stringify({ schema_version: 'forecast-alarm-receipt.v1', receipt_id: 'receipt', alarm_id: 'wrong-alarm',
      alarm_hash: alarm.payload_hash, receipt_kind: 'visible', owner_id: 'owner', request_id: command.request_id,
      known_at: '2026-10-08T00:00:00Z', parent_receipt_id: 'original-display', parent_receipt_hash: parent.payload_hash, surface: 'pc', visible_duration_ms: 1000,
      evidence_source: 'pc_browser_reported', payload_hash: 'b'.repeat(64) }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetch)
  await expect(forecastAlarmsApi.exposure(alarm, parent)).rejects.toThrow('原件不一致')
  await expect(forecastAlarmsApi.exposure(alarm, parent)).rejects.toThrow('原件不一致')
  expect(JSON.parse(fetch.mock.calls[0][1].body).request_id).toBe(JSON.parse(fetch.mock.calls[1][1].body).request_id)
})
