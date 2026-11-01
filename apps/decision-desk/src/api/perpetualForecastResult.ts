import { z } from 'zod'
import { decisionHorizonV2Schema, decisionSourceRefV1Schema, probabilityEstimateV1Schema, publicationBoundTargetV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import type { PerpetualAdviceView } from './perpetualAdvice'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { settingsApi } from './decisionSettings'
import { requestResponse } from './client'

const id = z.string().min(1), at = z.string().datetime({ offset: true }), sha = z.string().regex(/^[a-f0-9]{64}$/)
const decimal = z.string().regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/).refine(value => Number.isFinite(Number(value)))
const zeroDecimal = /^-?0(?:\.0+)?(?:[eE][+-]?\d+)?$/
const observedSchema = z.object({
  experience_ref: decisionSourceRefV1Schema, snapshot_ref: decisionSourceRefV1Schema,
  final_target_ref: decisionSourceRefV1Schema, frozen_forecast_ref: decisionSourceRefV1Schema,
  market_distribution_ref: decisionSourceRefV1Schema, method_ref: decisionSourceRefV1Schema,
  target_family_ref: decisionSourceRefV1Schema, label_rule_ref: decisionSourceRefV1Schema,
  direction_estimate: probabilityEstimateV1Schema, actual_category: z.enum(['up', 'neutral', 'down']),
  actual_return_fraction: decimal, multiclass_brier: decimal.nullable(), known_at: at, recorded_at: at,
  gaps: z.array(id), calculation_method_hash: sha, qualification: z.literal('research_trial'),
  financial_qualified: z.literal(false), skill_consumed: z.literal(false), trade_outcome: z.literal(false),
}).strict()
const base = z.object({ schema_version: z.literal('perpetual-forecast-result-view.v1'),
  owner_id: id, run_id: id, instrument_id: id, horizon: decisionHorizonV2Schema, read_at: at,
  admission_ref: decisionSourceRefV1Schema, target_ref: decisionSourceRefV1Schema, target: publicationBoundTargetV1Schema,
}).strict()
const legacyViewSchema = z.discriminatedUnion('status', [
  base.extend({ status: z.literal('not_due'), reason_code: z.literal('forecast_target_not_due'), result: z.null() }).strict(),
  base.extend({ status: z.literal('pending'), reason_code: z.literal('forecast_result_not_recorded'), result: z.null() }).strict(),
  base.extend({ status: z.literal('unsupported'), reason_code: z.literal('forecast_horizon_unsupported'), result: z.null() }).strict(),
  base.extend({ status: z.literal('observed'), reason_code: z.null(), result: observedSchema }).strict(),
])
const logLossSchema = z.discriminatedUnion('status', [
  z.object({ status: z.literal('finite'), value: decimal.refine(value => !value.startsWith('-') || zeroDecimal.test(value)), reason: z.null() }).strict(),
  z.object({ status: z.literal('infinite'), value: z.null(), reason: z.literal('observed_outcome_probability_zero') }).strict(),
  z.object({ status: z.literal('unavailable'), value: z.null(), reason: z.literal('direction_probabilities_unavailable') }).strict(),
])
const scoringResultSchema = observedSchema.extend({
  log_loss: logLossSchema,
  reliability_observations: z.array(z.object({ outcome_id: z.enum(['up', 'neutral', 'down']),
    probability: decimal, observed: z.union([z.literal(0), z.literal(1)]) }).strict()),
  strata: z.object({ asset: z.enum(['BTC', 'ETH']), horizon: decisionHorizonV2Schema,
    event_family: id.nullable(), market_regime: id.nullable() }).strict(),
  // Read-time comparison with the run's frozen historical base rate; absent for older runs.
  base_rate_comparison: z.object({ model_brier: decimal.nullable(), base_rate_brier: decimal.nullable(),
    brier_skill: decimal.nullable(),
    reason: z.enum(['base_rate_unavailable', 'direction_probabilities_unavailable']).nullable() }).strict().nullable().optional(),
}).strict()
const scoringBase = base.extend({ schema_version: z.literal('perpetual-forecast-result-view.v2') }).strict()
const viewSchema = z.union([legacyViewSchema, z.discriminatedUnion('status', [
  scoringBase.extend({ status: z.literal('not_due'), reason_code: z.literal('forecast_target_not_due'), result: z.null() }).strict(),
  scoringBase.extend({ status: z.literal('pending'), reason_code: z.literal('forecast_result_not_recorded'), result: z.null() }).strict(),
  scoringBase.extend({ status: z.literal('observed'), reason_code: z.null(), result: scoringResultSchema }).strict(),
])])
export type PerpetualForecastResultView = z.infer<typeof viewSchema>
const same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
function requireOwner(owner: string) {
  if (settingsApi.session()?.owner_id !== owner) throw new Error('perpetual_forecast_result_owner_changed')
}
function mismatch(): never { throw new Error('perpetual_forecast_result_original_mismatch') }
async function checked(raw: unknown, advice: PerpetualAdviceView): Promise<PerpetualForecastResultView> {
  const parsed = viewSchema.safeParse(raw)
  if (!parsed.success) return mismatch()
  const value = parsed.data, target = advice.target, snapshot = advice.snapshot, frozen = advice.frozen_forecast
  if (!target || !snapshot || !frozen || !advice.market_distribution
    || value.owner_id !== advice.owner_id || value.run_id !== advice.run_id
    || value.instrument_id !== advice.partition.instrument_id || value.horizon !== advice.horizon
    || value.target.horizon !== value.horizon || !same(value.target, target)
    || !same(value.target_ref, snapshot.bindings.final_target)
    || await hash(value.target) !== value.target_ref.hash
    || Date.parse(value.read_at) < Date.parse(target.assigned_at)) return mismatch()
  const due = Date.parse(value.read_at) >= Date.parse(target.window_end)
  if (value.schema_version === 'perpetual-forecast-result-view.v1') {
    if (value.status === 'unsupported') {
      if (value.horizon === '4h') return mismatch()
    } else if (value.horizon !== '4h' || value.status === 'not_due' && due || value.status !== 'not_due' && !due) return mismatch()
  } else if (value.status === 'not_due' && due || value.status !== 'not_due' && !due) return mismatch()
  if (value.status === 'observed') {
    const result = value.result
    const pairs = [
      [result.snapshot_ref, { ref: snapshot.id, hash: snapshot.snapshot_hash }],
      [result.final_target_ref, value.target_ref], [result.frozen_forecast_ref, snapshot.bindings.frozen_forecast],
      [result.market_distribution_ref, frozen.market_distribution_ref], [result.method_ref, target.method_ref],
      [result.target_family_ref, target.target_family_ref], [result.label_rule_ref, target.label_rule_ref],
    ] as const
    if (pairs.some(([left, right]) => !same(left, right))
      || !same(result.direction_estimate, advice.market_distribution.direction_distribution)
      || Date.parse(result.known_at) < Date.parse(target.window_end)
      || Date.parse(result.recorded_at) < Date.parse(result.known_at)
      || Date.parse(value.read_at) < Date.parse(result.recorded_at)
      || result.multiclass_brier === null && result.gaps.length === 0) return mismatch()
    if (value.schema_version === 'perpetual-forecast-result-view.v2') {
      const score = value.result, rows = score.reliability_observations
      const probabilities: { outcome_id: string; value: string }[] | null = score.direction_estimate.probabilities
      if (value.instrument_id !== `okx:${score.strata.asset}-USDT-SWAP`
        || score.strata.horizon !== value.horizon) return mismatch()
      if (probabilities === null) {
        if (rows.length !== 0 || score.log_loss.status !== 'unavailable'
          || score.multiclass_brier !== null) return mismatch()
      } else {
        if (score.log_loss.status === 'unavailable' || rows.length !== 3
          || !(['up', 'neutral', 'down'] as const).every((outcome, index) => {
            const row = rows[index]
            return row.outcome_id === outcome
              && row.probability === probabilities.find(item => item.outcome_id === outcome)?.value
              && row.observed === (score.actual_category === outcome ? 1 : 0)
          })) return mismatch()
        const actualProbability = probabilities.find(item => item.outcome_id === score.actual_category)!.value
        // Check the zero-probability state without clipping or recomputing log loss.
        const actualProbabilityIsZero = zeroDecimal.test(actualProbability)
        if ((score.log_loss.status === 'infinite') !== actualProbabilityIsZero) return mismatch()
      }
    }
    // This is a projection: the server authenticates the complete experience hash
    // and replays archived closes. Do not claim to reconstruct that hash here.
  }
  requireOwner(advice.owner_id)
  return value
}
export const perpetualForecastResultApi = {
  read: async (advice: PerpetualAdviceView, signal?: AbortSignal): Promise<PerpetualForecastResultView> => {
    requireOwner(advice.owner_id)
    if (!advice.run_id || !advice.target || !advice.snapshot || !advice.frozen_forecast
      || !['available', 'expired'].includes(advice.status)) return mismatch()
    const raw: unknown = await requestResponse('/v1/decision-settings/research/results/' + encodeURIComponent(advice.run_id),
      response => response.json(), { method: 'GET', credentials: 'include', signal }, undefined, { timeoutMs: 90_000 })
    return checked(raw, advice)
  },
}
