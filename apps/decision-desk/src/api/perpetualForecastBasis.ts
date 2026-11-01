import { z } from 'zod'
import { decisionHorizonV2Schema, decisionSourceRefV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import type { PerpetualAdviceView } from './perpetualAdvice'

const decimal = z.string().regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/)
const probabilities = z.array(z.object({ outcome_id: z.enum(['up', 'neutral', 'down']), value: decimal }).strict()).length(3)
const historicalWindow = z.object({ status: z.literal('available'), probabilities, horizon_seconds: z.number().int().positive(),
  threshold_fraction: decimal, window_start: z.string().datetime({ offset: true }), window_end: z.string().datetime({ offset: true }),
}).passthrough()
// These are complete frozen source bodies: preserve all fields for their canonical hash.
const materials = z.object({ context: z.record(z.unknown()), direction_base_rate_ref: decisionSourceRefV1Schema,
  direction_base_rate_path: z.literal('/direction_base_rate'), direction_base_rate: z.object({ status: z.literal('available'),
    inst_id: z.string().min(1), horizon_seconds: z.number().int().positive(), threshold_fraction: decimal,
    as_of: z.string().datetime({ offset: true }), window_90d: historicalWindow }).passthrough(),
}).passthrough()
const derivation = z.object({ research_materials_ref: decisionSourceRefV1Schema, admission_ref: decisionSourceRefV1Schema,
  final_target_ref: decisionSourceRefV1Schema, comparison_ref: decisionSourceRefV1Schema }).passthrough()
export const perpetualForecastBasisSchema = z.object({ schema_version: z.literal('perpetual-forecast-basis.v1'),
  run_id: z.string().min(1), horizon: decisionHorizonV2Schema, research_materials_ref: decisionSourceRefV1Schema,
  research_materials: materials, derivation, market_distribution_ref: decisionSourceRefV1Schema,
  baseline_window: z.literal('window_90d'), baseline_probabilities: probabilities, model_probabilities: probabilities.nullable(),
  relation: z.enum(['equal', 'different', 'unknown']), max_absolute_difference_pp: decimal.nullable(),
  target_window: z.object({ window_start: z.string().datetime(), window_end: z.string().datetime(), threshold_fraction: decimal }).strict(),
}).strict()
export type PerpetualForecastBasis = z.infer<typeof perpetualForecastBasisSchema>
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const digits = (text: string) => (text.split('.')[1] ?? '').length
const scaled = (text: string, places: number) => BigInt(text.replace('.', '')) * 10n ** BigInt(places - digits(text))
const sameDecimal = (a: string, b: string) => {
  const places = Math.max(digits(a), digits(b))
  return scaled(a, places) === scaled(b, places)
}

export function compareBasisProbabilities(base: PerpetualForecastBasis['baseline_probabilities'], model: PerpetualForecastBasis['model_probabilities']) {
  const places = Math.max(...[...base, ...(model ?? [])].map(row => digits(row.value)))
  const unit = 10n ** BigInt(places)
  const read = (rows: typeof base) => {
    const values = new Map(rows.map(row => [row.outcome_id, scaled(row.value, places)]))
    if (rows.length !== 3 || values.size !== 3 || [...values.values()].some(v => v < 0n || v > unit)
      || [...values.values()].reduce((a, b) => a + b, 0n) !== unit) throw new Error('perpetual_forecast_basis_probabilities_invalid')
    return values
  }
  const prior = read(base)
  if (model === null) return { relation: 'unknown', difference: null }
  const estimate = read(model)
  const delta = [...prior].reduce((maximum, [key, value]) => {
    const change = value - estimate.get(key)!, absolute = change < 0n ? -change : change
    return absolute > maximum ? absolute : maximum
  }, 0n) * 100n
  const padded = delta.toString().padStart(places + 1, '0')
  const difference = places ? `${padded.slice(0, -places)}.${padded.slice(-places)}`.replace(/\.?0+$/, '') : padded
  return { relation: delta === 0n ? 'equal' : 'different', difference: difference || '0' }
}

export async function checkForecastBasis(value: PerpetualAdviceView) {
  const basis = value.forecast_basis
  if (!basis) return
  const { target, frozen_forecast: frozen, snapshot, market_distribution: market } = value
  if (!target || !frozen || !snapshot || !market || !snapshot.bindings.derivation) throw new Error('perpetual_forecast_basis_original_missing')
  const source = basis.research_materials, prior = source.direction_base_rate, window = prior.window_90d
  const comparison = compareBasisProbabilities(basis.baseline_probabilities, basis.model_probabilities)
  if (basis.run_id !== value.run_id || basis.horizon !== value.horizon
    || !same(source.context, snapshot.context) || prior.inst_id !== value.partition.instrument_id.replace(/^okx:/, '')
    || prior.horizon_seconds !== target.duration_seconds || window.horizon_seconds !== target.duration_seconds
    || !sameDecimal(prior.threshold_fraction, target.threshold_fraction) || !sameDecimal(window.threshold_fraction, target.threshold_fraction)
    || !same(basis.target_window, { window_start: target.window_start, window_end: target.window_end, threshold_fraction: target.threshold_fraction })
    || !same(basis.market_distribution_ref, frozen.market_distribution_ref)
    || !same(basis.derivation.research_materials_ref, basis.research_materials_ref)
    || !same(basis.derivation.final_target_ref, snapshot.bindings.final_target)
    || !same(basis.derivation.comparison_ref, snapshot.bindings.comparison)
    || !same(source.direction_base_rate_ref, basis.derivation.admission_ref)
    || !same(basis.baseline_probabilities, window.probabilities) || !same(basis.model_probabilities, market.direction_distribution.probabilities)
    || basis.relation !== comparison.relation || basis.max_absolute_difference_pp !== comparison.difference
    || Date.parse(window.window_start) >= Date.parse(window.window_end) || Date.parse(window.window_end) > Date.parse(prior.as_of)
    || Date.parse(prior.as_of) > Date.parse(frozen.forecast_as_of)
    || await hash(basis.derivation) !== snapshot.bindings.derivation.hash
    || await hash(source) !== basis.research_materials_ref.hash) throw new Error('perpetual_forecast_basis_binding_mismatch')
}
