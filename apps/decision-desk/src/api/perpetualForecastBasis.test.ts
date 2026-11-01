// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import fixture from '../../../../artifacts/d2-perp-advice-ui/pure-presentation-fixture.json'
import { settingsApi } from './decisionSettings'
import { perpetualAdviceApi, type PerpetualAdviceSelection } from './perpetualAdvice'
import { decisionBodyHash as hash } from './decisionOriginals'
import { compareBasisProbabilities } from './perpetualForecastBasis'

const selection = fixture.selection as PerpetualAdviceSelection
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'read', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function projected() {
  const value = structuredClone(fixture.value)
  const probabilities = [{ outcome_id: 'up', value: '0.34' }, { outcome_id: 'neutral', value: '0.33' }, { outcome_id: 'down', value: '0.33' }]
  const window = { status: 'available', probabilities, horizon_seconds: 14400, threshold_fraction: value.target.threshold_fraction,
    window_start: '2025-10-01T00:00:00Z', window_end: '2026-01-01T00:00:00Z' }
  const baseline = { status: 'available', inst_id: 'BTC-USDT-SWAP', horizon_seconds: 14400, threshold_fraction: value.target.threshold_fraction,
    as_of: '2026-01-01T00:00:00Z', window_90d: window }
  const admission = { ref: 'admission', hash: 'a'.repeat(64) }
  const materials = { context: value.snapshot.context, direction_base_rate: baseline, direction_base_rate_ref: admission, direction_base_rate_path: '/direction_base_rate' }
  const materialsRef = { ref: 'materials', hash: await hash(materials) }
  const derivation = { research_materials_ref: materialsRef, admission_ref: admission, final_target_ref: value.snapshot.bindings.final_target,
    comparison_ref: value.snapshot.bindings.comparison }
  const derivationRef = { ref: 'derivation', hash: await hash(derivation) }
  value.snapshot.bindings.derivation = derivationRef
  value.revision.bindings.derivation = derivationRef
  const { snapshot_hash: _s, ...s } = value.snapshot
  value.snapshot.snapshot_hash = await hash(s)
  const { payload_hash: _r, ...r } = value.revision
  value.revision.payload_hash = await hash(r)
  return { ...value, forecast_basis: { schema_version: 'perpetual-forecast-basis.v1', run_id: value.run_id, horizon: value.horizon,
    research_materials_ref: materialsRef, research_materials: materials, derivation, market_distribution_ref: value.frozen_forecast.market_distribution_ref,
    baseline_window: 'window_90d', baseline_probabilities: probabilities, model_probabilities: value.market_distribution.direction_distribution.probabilities,
    relation: 'different', max_absolute_difference_pp: '9.5', target_window: { window_start: value.target.window_start, window_end: value.target.window_end, threshold_fraction: value.target.threshold_fraction } } }
}
function serve(value: unknown) { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(value)))) }
it('accepts old advice and a strictly bound same-Run baseline projection', async () => {
  serve(fixture.value)
  expect((await perpetualAdviceApi.current(selection)).forecast_basis).toBeUndefined()
  const value = await projected(); serve(value)
  expect((await perpetualAdviceApi.current(selection)).forecast_basis?.relation).toBe('different')
})
it('rejects wrong Run, horizon, reference, probability, delta and source tampering', async () => {
  for (const mutate of [
    (b: Awaited<ReturnType<typeof projected>>['forecast_basis']) => { b.run_id = 'foreign' },
    b => { b.horizon = '24h' }, b => { b.market_distribution_ref.ref = 'foreign' },
    b => { b.baseline_probabilities[0].value = '0.35' }, b => { b.max_absolute_difference_pp = '0' },
    b => { b.research_materials.direction_base_rate.window_90d.window_end = '2027-01-01T00:00:00Z' },
    b => { b.research_materials_ref.hash = 'b'.repeat(64) },
    b => { Object.assign(b, { unexpected: true }) },
  ] as Array<(basis: Awaited<ReturnType<typeof projected>>['forecast_basis']) => void>) {
    const value = await projected(); mutate(value.forecast_basis); serve(value)
    await expect(perpetualAdviceApi.current(selection)).rejects.toThrow()
  }
})
it('compares exact decimal probabilities without interpreting small differences as baseline-only', () => {
  const base = [{ outcome_id: 'up' as const, value: '0.34' }, { outcome_id: 'neutral' as const, value: '0.33' }, { outcome_id: 'down' as const, value: '0.33' }]
  expect(compareBasisProbabilities(base, base)).toEqual({ relation: 'equal', difference: '0' })
  expect(compareBasisProbabilities(base, null)).toEqual({ relation: 'unknown', difference: null })
  const near = structuredClone(base); near[0].value = '0.341'; near[1].value = '0.329'
  expect(compareBasisProbabilities(base, near)).toEqual({ relation: 'different', difference: '0.1' })
  const far = structuredClone(base); far[0].value = '0.54'; far[1].value = '0.13'
  expect(compareBasisProbabilities(base, far)).toEqual({ relation: 'different', difference: '20' })
  const invalid = structuredClone(base); invalid[0].value = '0.35'
  expect(() => compareBasisProbabilities(invalid, null)).toThrow()
})
it.skipIf(!process.env.DH_FORECAST_BASIS_FIXTURE)('consumes the real Python authenticated publication projection', async () => {
  const value = JSON.parse(readFileSync(process.env.DH_FORECAST_BASIS_FIXTURE!, 'utf-8'))
  const selected = { owner_id: value.owner_id, partition: value.partition, settings_revision: value.settings_revision,
    settings_state_version: value.settings_state_version, horizon: value.horizon } as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: value.owner_id, csrf_token: 'read', expires_at: '2099-01-01T00:00:00Z' })
  serve(value)
  expect((await perpetualAdviceApi.current(selected)).forecast_basis?.run_id).toBe(value.run_id)
})

