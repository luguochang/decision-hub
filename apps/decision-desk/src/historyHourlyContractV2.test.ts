import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'
import { historyHourlyStatusV2Schema } from '../../../packages/contracts_ts/src/generated/history_hourly_status_v2'

test('actual signed schedule response has strict same PC contract', () => {
  const path = resolve(process.cwd(), '../../artifacts/market-risk-implementation-20261007/B2/history-product-periodic-v2/actual-signed-hourly-status03.json')
  const value = JSON.parse(readFileSync(path, 'utf8'))
  expect(historyHourlyStatusV2Schema.safeParse(value).success).toBe(true)
  expect(historyHourlyStatusV2Schema.safeParse({ ...value, hidden_extra: true }).success).toBe(false)
  expect(historyHourlyStatusV2Schema.safeParse({ ...value, current_forecast_originals_verified: true }).success).toBe(false)
  expect(historyHourlyStatusV2Schema.safeParse({ ...value, scopes: [...value.scopes].reverse() }).success).toBe(false)
  expect(historyHourlyStatusV2Schema.safeParse({ ...value, observed_at: '2026-10-09T16:02:05' }).success).toBe(false)
})
