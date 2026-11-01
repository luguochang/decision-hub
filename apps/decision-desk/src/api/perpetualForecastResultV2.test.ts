// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { settingsApi } from './decisionSettings'
import { perpetualAdviceApi, type PerpetualAdviceSelection } from './perpetualAdvice'
import { perpetualForecastResultApi } from './perpetualForecastResult'
import fixture from './fixtures/perpetualForecastResult.v2/ETH24h.json'
import btc4h from './fixtures/perpetualForecastResult.v2/BTC4h.json'
import eth4h from './fixtures/perpetualForecastResult.v2/ETH4h.json'
import btc24h from './fixtures/perpetualForecastResult.v2/BTC24h.json'
import btc72h from './fixtures/perpetualForecastResult.v2/BTC72h.json'
import eth72h from './fixtures/perpetualForecastResult.v2/ETH72h.json'
import infinite from './fixtures/perpetualForecastResult.v2/infinite.json'
import unavailable from './fixtures/perpetualForecastResult.v2/unavailable.json'

// Original pure publisher -> 1,441 captured boundary minutes -> persisted V2 ->
// original Query projection. Synthetic inputs; no actual mature forecast claim.
const selection: PerpetualAdviceSelection = {
  owner_id: fixture.advice.owner_id,
  partition: fixture.advice.partition as PerpetualAdviceSelection['partition'],
  settings_revision: fixture.advice.settings_revision,
  settings_state_version: fixture.advice.settings_state_version,
  horizon: '24h',
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id,
    csrf_token: 'synthetic-read-session', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function originals(wire: { advice: unknown; result: unknown } = fixture) {
  const a = wire.advice as typeof fixture.advice
  const selected = { ...selection, owner_id: a.owner_id, partition: a.partition,
    settings_revision: a.settings_revision, settings_state_version: a.settings_state_version,
    horizon: a.horizon } as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: a.owner_id,
    csrf_token: 'synthetic-read-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(wire.advice))))
  const advice = await perpetualAdviceApi.current(selected)
  const fetcher = vi.fn(async () => new Response(JSON.stringify(wire.result)))
  vi.stubGlobal('fetch', fetcher)
  return { advice, fetcher }
}
it.each([
  ['BTC', '4h', btc4h], ['ETH', '4h', eth4h], ['BTC', '24h', btc24h],
  ['ETH', '24h', fixture], ['BTC', '72h', btc72h], ['ETH', '72h', eth72h],
] as const)('reads original %s %s V2 scores, observations and unknown strata', async (_asset, horizon, wire) => {
  const { advice } = await originals(wire)
  const value = await perpetualForecastResultApi.read(advice)
  expect(value).toEqual(wire.result)
  expect(value.horizon).toBe(horizon)
  expect(value.status).toBe('observed')
})
it.each([['infinite', infinite], ['unavailable', unavailable]] as const)(
  'preserves the original %s state without replacing its value or observations', async (status, wire) => {
    const { advice } = await originals(wire)
    const value = await perpetualForecastResultApi.read(advice)
    expect(value).toEqual(wire.result)
    expect(value.status).toBe('observed')
    if (value.status !== 'observed' || value.schema_version !== 'perpetual-forecast-result-view.v2') throw new Error('expected V2 observation')
    expect(value.result.log_loss.status).toBe(status)
    expect(value.result.log_loss.value).toBeNull()
    if (status === 'unavailable') expect(value.result.reliability_observations).toEqual([])
  },
)
it('keeps V2 not-due and pending distinct at the original target boundary', async () => {
  const { advice, fetcher } = await originals()
  for (const status of ['not_due', 'pending'] as const) {
    const body = { ...fixture.result, status, result: null,
      reason_code: status === 'not_due' ? 'forecast_target_not_due' : 'forecast_result_not_recorded',
      read_at: status === 'not_due'
        ? new Date(Date.parse(fixture.result.target.window_end) - 1).toISOString() : fixture.result.read_at }
    fetcher.mockImplementation(async () => new Response(JSON.stringify(body)))
    expect((await perpetualForecastResultApi.read(advice)).status).toBe(status)
  }
})
it('rejects V2 score shape, strata, reliability and original identity substitutions', async () => {
  const { advice, fetcher } = await originals()
  const mutations: ((value: typeof fixture.result) => void)[] = [
    value => { value.result.strata.asset = 'BTC' },
    value => { value.result.strata.horizon = '72h' },
    value => { value.result.reliability_observations.pop() },
    value => { value.result.reliability_observations[0].outcome_id = 'neutral' },
    value => { value.result.reliability_observations[0].probability = '0.9' },
    value => { value.result.reliability_observations[0].observed = 1 },
    value => { value.result.log_loss.value = 'Infinity' },
    value => { value.result.log_loss.status = 'infinite' },
    value => { value.owner_id = 'another-owner' },
    value => { value.run_id = 'another-run' },
    value => { value.result.snapshot_ref.ref = 'another-snapshot' },
    value => { value.result.final_target_ref.hash = 'a'.repeat(64) },
    value => { value.result.financial_qualified = true },
  ]
  for (const mutate of mutations) {
    const changed = structuredClone(fixture.result); mutate(changed)
    fetcher.mockImplementation(async () => new Response(JSON.stringify(changed)))
    await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow('perpetual_forecast_result_original_mismatch')
  }
  fetcher.mockImplementation(async () => new Response(JSON.stringify({ ...fixture.result,
    status: 'unsupported', reason_code: 'forecast_horizon_unsupported', result: null })))
  await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow('perpetual_forecast_result_original_mismatch')
})
it('rejects an infinite log loss claim when the original observed-outcome probability is positive', async () => {
  const { advice, fetcher } = await originals()
  const body = { ...fixture.result, result: { ...fixture.result.result,
    log_loss: { status: 'infinite', value: null, reason: 'observed_outcome_probability_zero' } } }
  fetcher.mockImplementation(async () => new Response(JSON.stringify(body)))
  await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow('perpetual_forecast_result_original_mismatch')
})
it('rejects a negative log loss even when its decimal magnitude underflows Number', async () => {
  const { advice, fetcher } = await originals()
  const body = { ...fixture.result, result: { ...fixture.result.result,
    log_loss: { ...fixture.result.result.log_loss, value: '-1E-1000' } } }
  fetcher.mockImplementation(async () => new Response(JSON.stringify(body)))
  await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow('perpetual_forecast_result_original_mismatch')
})
it('rejects a zero Brier substituted for the original unknown-probability result', async () => {
  const { advice, fetcher } = await originals(unavailable)
  const body = { ...unavailable.result, result: { ...unavailable.result.result, multiclass_brier: '0' } }
  fetcher.mockImplementation(async () => new Response(JSON.stringify(body)))
  await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow('perpetual_forecast_result_original_mismatch')
})
