import { afterEach, expect, it, vi } from 'vitest'
import { pathCurrentTrialSchema, pathScoreViewSchema, pathTrainingReadinessSchema, riskPathsApi } from './riskPaths'
import { PATH_BTC, PATH_ETH, pathCurrentFixture, pathScoreFinalFixture, pathPredictionsFixture, pathScoreV2FinalFixture } from './riskPathsFixtures'

afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}
it('accepts unregistered and fixed insufficient final trial but rejects missing denominator and promotion', () => {
  expect(pathCurrentTrialSchema.parse({ ...pathCurrentFixture, trial: null }).trial).toBeNull()
  expect(pathScoreViewSchema.parse(pathScoreFinalFixture).report?.score.promotion_allowed).toBe(false)
  expect(() => pathCurrentTrialSchema.parse({ ...pathCurrentFixture, trial: { ...pathCurrentFixture.trial, missed_prediction_count: undefined } })).toThrow()
  expect(() => pathCurrentTrialSchema.parse({ ...pathCurrentFixture, trial: { ...pathCurrentFixture.trial, future_prediction_count: 0 } })).toThrow('分母')
  expect(() => pathScoreViewSchema.parse({ ...pathScoreFinalFixture, report: { ...pathScoreFinalFixture.report, score: { ...pathScoreFinalFixture.report!.score, promotion_allowed: true } } })).toThrow()
})
it.each(['NaN', 'Infinity', '-0.1', '1.1'])('rejects invalid Brier %s', value => {
  const score = pathScoreFinalFixture.report!.score
  expect(() => pathScoreViewSchema.parse({ ...pathScoreFinalFixture, report: { ...pathScoreFinalFixture.report, score: { ...score, targets: [{ ...score.targets[0], baseline_brier: value }] } } })).toThrow()
})
it('checks selected asset and trial identity and uses readonly encoded GET', async () => {
  const fetcher = response(pathCurrentFixture)
  await expect(riskPathsApi.current(PATH_BTC)).resolves.toEqual(pathCurrentFixture)
  expect(fetcher.mock.calls[0][0]).toContain(encodeURIComponent(PATH_BTC))
  expect(fetcher.mock.calls[0][1]?.method).toBeUndefined()
  await expect(riskPathsApi.current(PATH_ETH)).rejects.toThrow('当前资产不一致')
  response(pathPredictionsFixture)
  await expect(riskPathsApi.predictions(PATH_BTC, 'wrong-trial')).rejects.toThrow('当前试验不一致')
  response(pathScoreFinalFixture)
  await expect(riskPathsApi.score('wrong-trial')).rejects.toThrow('当前试验不一致')
})
it('refuses a score whose target assets or full denominator differ from registration', async () => {
  const report = pathScoreFinalFixture.report!
  response({ ...pathScoreFinalFixture, report: { ...report, score: { ...report.score, targets: [{ ...report.score.targets[0], scope_id: 'another-scope' }] } } })
  await expect(riskPathsApi.score(pathCurrentFixture.trial!.trial_id)).rejects.toThrow('登记样本不一致')
})
it('preserves v1 and validates the registered v2 insufficient interval and authentic alarm metrics shape', () => {
  expect(pathScoreViewSchema.parse(pathScoreFinalFixture).report?.score.schema_version).toBe('path-shadow-score.v1')
  const modern = pathScoreViewSchema.parse(pathScoreV2FinalFixture).report!.score
  expect(modern.ci_status).toBe('insufficient')
  const report = pathScoreV2FinalFixture.report!
  expect(() => pathScoreViewSchema.parse({ ...pathScoreV2FinalFixture, report: { ...report, score: { ...report.score, ci_status: 'available' } } })).toThrow()
  expect(() => pathScoreViewSchema.parse({ ...pathScoreV2FinalFixture, report: { ...report, report_version: 'path-shadow-evaluation.v1' } })).toThrow()
  expect(() => pathScoreViewSchema.parse({ ...pathScoreV2FinalFixture, report: { ...report, score: { ...report.score, alarm_originals: [{ original_ref: 'synthetic', original_hash: 'invalid' }] } } })).toThrow()
})

it('keeps zero frozen samples unavailable and rejects succeeded-as-ready or cross-asset training', () => {
  const training = {
    schema_version: 'path-training-readiness.v1', scope_id: PATH_BTC, as_of: '2026-10-10T00:00:10Z',
    status: 'training_unavailable', fit_job_state: 'succeeded', frozen_fit: true,
    read_as_of: '2026-10-09T23:50:00Z', training_cutoff_at: '2026-10-10T00:00:00Z',
    requested_history_start_at: '2026-07-12T00:00:00Z', accepted_windows: 0, excluded_windows: 90,
    baseline_n: 0, candidate_training_n: 0, low_group_n: 0, high_group_n: 0,
    minimum_training_windows: 60, minimum_group_windows: 30, baseline_sample_ready: false, candidate_sample_ready: false,
    available_history_start_at: null, available_history_end_at: null, available_candle_minutes: 0,
    history_basis: 'native_ledger_metadata_at_fit_read_as_of', accepted_history_start_at: null, accepted_history_end_at: null,
    exclusion_counts: { complete_24h_label_unavailable: 90 }, reasons: ['baseline_training_windows_insufficient'],
    fit_result_hash: 'a'.repeat(64), model_hash: 'b'.repeat(64), probability_qualified: false, promotion_allowed: false,
  }
  expect(pathTrainingReadinessSchema.parse(training).status).toBe('training_unavailable')
  expect(() => pathTrainingReadinessSchema.parse({ ...training, status: 'training_sufficient', baseline_sample_ready: true })).toThrow('训练样本')
  expect(() => pathTrainingReadinessSchema.parse({ ...training, probability_qualified: true })).toThrow()
  const summary = { ...pathCurrentFixture.trial, training_readiness: [training, { ...training, scope_id: PATH_ETH }] }
  expect(pathCurrentTrialSchema.parse({ ...pathCurrentFixture, trial: summary }).trial?.training_readiness).toHaveLength(2)
  expect(() => pathCurrentTrialSchema.parse({ ...pathCurrentFixture, trial: { ...summary, training_readiness: [training, training] } })).toThrow('训练资产')
  expect(pathCurrentTrialSchema.parse(pathCurrentFixture).trial?.training_readiness).toBeUndefined()
})
