// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { settingsApi } from './decisionSettings'
import { perpetualAdviceApi, type PerpetualAdviceSelection } from './perpetualAdvice'
import { perpetualForecastResultApi } from './perpetualForecastResult'
import fixture from '../../../../artifacts/d2-perp-advice-ui/pure-presentation-fixture.json'

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: fixture.selection.owner_id, csrf_token: 'local-test-session', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function adviceOriginal() {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(fixture.value))))
  return perpetualAdviceApi.current(fixture.selection as PerpetualAdviceSelection)
}
function observed() {
  const a = fixture.value, t = a.target
  return { schema_version: 'perpetual-forecast-result-view.v1', status: 'observed', reason_code: null,
    owner_id: a.owner_id, run_id: a.run_id, instrument_id: a.partition.instrument_id, horizon: a.horizon, read_at: '2026-01-01T05:00:00Z',
    admission_ref: { ref: 'fixture:admission', hash: 'a'.repeat(64) }, target_ref: a.snapshot.bindings.final_target, target: t,
    result: { experience_ref: { ref: 'perp_experience_fixture', hash: 'e'.repeat(64) },
      snapshot_ref: { ref: a.snapshot.id, hash: a.snapshot.snapshot_hash }, final_target_ref: a.snapshot.bindings.final_target,
      frozen_forecast_ref: a.snapshot.bindings.frozen_forecast, market_distribution_ref: a.frozen_forecast.market_distribution_ref,
      method_ref: t.method_ref, target_family_ref: t.target_family_ref, label_rule_ref: t.label_rule_ref,
      direction_estimate: a.market_distribution.direction_distribution, actual_category: 'up', actual_return_fraction: '0.006',
      multiclass_brier: '0.47915', known_at: '2026-01-01T04:27:00Z', recorded_at: '2026-01-01T04:28:00Z', gaps: [],
      calculation_method_hash: 'c'.repeat(64), qualification: 'research_trial', financial_qualified: false, skill_consumed: false, trade_outcome: false } }
}
it('accepts the strict projection bound to authenticated advice and rejects foreign Run, target hash and publication bindings', async () => {
  const advice = await adviceOriginal(), original = observed()
  const fetcher = vi.fn(async () => new Response(JSON.stringify(original)))
  vi.stubGlobal('fetch', fetcher)
  const accepted = await perpetualForecastResultApi.read(advice)
  expect(accepted.status).toBe('observed')
  if (accepted.status !== 'observed') throw new Error('expected observed result')
  expect(accepted.result.actual_return_fraction).toBe('0.006')
  expect(accepted.result.direction_estimate).toEqual(fixture.value.market_distribution.direction_distribution)
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/v1/decision-settings/research/results/run-pure')
  expect(init.method).toBe('GET'); expect(init.credentials).toBe('include')
  expect(new Headers(init.headers).has('X-Owner-Id')).toBe(false)
  const mutations: ((value: ReturnType<typeof observed>) => void)[] = [
    value => { value.run_id = 'foreign-run' },
    value => { value.owner_id = 'foreign-owner' },
    value => { value.target.threshold_fraction = '0.5' },
    value => { value.target_ref.hash = 'b'.repeat(64) },
    value => { value.result.snapshot_ref.ref = 'foreign-snapshot' },
    value => { value.result.final_target_ref.ref = 'foreign-target' },
    value => { value.result.frozen_forecast_ref.hash = 'b'.repeat(64) },
    value => { value.result.market_distribution_ref.ref = 'foreign-distribution' },
    value => { value.result.method_ref.ref = 'foreign-method' },
    value => { value.result.target_family_ref.ref = 'foreign-family' },
    value => { value.result.label_rule_ref.ref = 'foreign-label' },
    value => { value.result.direction_estimate.probabilities![0].value = '0.99' },
    value => { value.result.trade_outcome = true },
  ]
  for (const mutate of mutations) {
    const changed = structuredClone(original); mutate(changed)
    fetcher.mockImplementation(async () => new Response(JSON.stringify(changed)))
    await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow()
  }
  fetcher.mockImplementation(async () => new Response(JSON.stringify({ ...original, extra: true })))
  await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow()
})
it('enforces state and time meanings and never treats malformed observed data as pending', async () => {
  const advice = await adviceOriginal(), original = observed()
  const fetcher = vi.fn(async () => new Response(JSON.stringify(original)))
  vi.stubGlobal('fetch', fetcher)
  for (const status of ['not_due', 'pending'] as const) {
    const body = { ...original, status, reason_code: status === 'not_due' ? 'forecast_target_not_due' : 'forecast_result_not_recorded',
      read_at: status === 'not_due' ? '2026-01-01T04:25:00Z' : original.read_at, result: null }
    fetcher.mockImplementation(async () => new Response(JSON.stringify(body)))
    expect((await perpetualForecastResultApi.read(advice)).status).toBe(status)
  }
  for (const changed of [
    { ...original, status: 'pending', reason_code: 'forecast_result_not_recorded' },
    { ...original, status: 'not_due', reason_code: 'forecast_target_not_due', result: null },
    { ...original, status: 'unsupported', reason_code: 'forecast_horizon_unsupported', result: null },
    { ...original, result: { ...original.result, multiclass_brier: null } },
  ]) {
    fetcher.mockImplementation(async () => new Response(JSON.stringify(changed)))
    await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow()
  }
})
it('uses an independent 90-second deadline and forwards external cancellation without changing other request timeouts', async () => {
  const advice = await adviceOriginal()
  vi.useFakeTimers()
  const signals: AbortSignal[] = []
  vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    const signal = init!.signal!; signals.push(signal)
    signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
  })))
  const wait = perpetualForecastResultApi.read(advice)
  const timeout = expect(wait).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(89_999)
  expect(signals[0].aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(1)
  await timeout
  const controller = new AbortController(), cancelled = perpetualForecastResultApi.read(advice, controller.signal)
  const abort = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort(); await abort
  expect(signals[1].aborted).toBe(true)
})

it('preserves finite Decimal exponent strings and rejects nonfinite decimal projections', async () => {
  const advice = await adviceOriginal(), body = observed()
  // 1E-7 lies inside the existing ±0.005 neutral band. For the original
  // 0.435/0.295/0.270 forecast, the neutral-outcome Brier is 0.75915.
  body.result.actual_category = 'neutral'
  body.result.actual_return_fraction = '1E-7'
  body.result.multiclass_brier = '7.5915E-1'
  const fetcher = vi.fn(async () => new Response(JSON.stringify(body)))
  vi.stubGlobal('fetch', fetcher)
  const value = await perpetualForecastResultApi.read(advice)
  expect(value.status).toBe('observed')
  if (value.status !== 'observed') throw new Error('expected observed result')
  expect(value.result.actual_return_fraction).toBe('1E-7')
  expect(value.result.multiclass_brier).toBe('7.5915E-1')
  for (const field of ['actual_return_fraction', 'multiclass_brier'] as const) {
    for (const invalid of ['NaN', 'Infinity', '1E999']) {
      const changed = structuredClone(body); changed.result[field] = invalid
      fetcher.mockImplementation(async () => new Response(JSON.stringify(changed)))
      await expect(perpetualForecastResultApi.read(advice)).rejects.toThrow()
    }
  }
})
