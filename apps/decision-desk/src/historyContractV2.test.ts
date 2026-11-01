import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'
import { historyProductStatusV2Schema, historyAssetRefreshReplyV2Schema } from '../../../packages/contracts_ts/src/generated/r2'

test('same saved API originals satisfy strict PC contracts and reject dishonest outcomes', () => {
  const directory = resolve(process.cwd(), '../../artifacts/market-risk-implementation-20261007/B2/history-dataset-v2')
  const status = JSON.parse(readFileSync(resolve(directory, 'pc-status-from-private-api02.json'), 'utf8'))
  const forecast = JSON.parse(readFileSync(resolve(directory, 'pc-forecast-from-private-api02.json'), 'utf8'))
  expect(historyProductStatusV2Schema.safeParse(status).success).toBe(true)
  expect(historyAssetRefreshReplyV2Schema.safeParse(forecast).success).toBe(true)
  expect(historyProductStatusV2Schema.safeParse({ ...status, actions: { ...status.actions, can_fit: true } }).success).toBe(false)
  expect(historyProductStatusV2Schema.safeParse({ ...status, hidden_extra: true }).success).toBe(false)
  expect(historyAssetRefreshReplyV2Schema.safeParse({ ...forecast, committed: null }).success).toBe(false)
  expect(historyAssetRefreshReplyV2Schema.safeParse({ ...forecast, frequencies: [] }).success).toBe(false)
  expect(historyAssetRefreshReplyV2Schema.safeParse({ ...forecast, outcome: 'unconfirmed' }).success).toBe(false)
})
