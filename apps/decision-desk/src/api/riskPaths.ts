import { z } from 'zod'
import { request } from './client'

const at = z.string().datetime({ offset: true })
const identity = z.string().min(1)
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const count = z.number().int().nonnegative()
const decimal = z.string().regex(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?$/)
  .refine(value => Number.isFinite(Number(value)), '数值无法核对')
const loss = decimal.refine(value => Number(value) >= 0 && Number(value) <= 1)
const delta = decimal.refine(value => Number(value) >= -1 && Number(value) <= 1)
const state = z.enum(['registered', 'running', 'paused', 'draining', 'completed', 'failed'])

export const pathTrainingReadinessSchema = z.object({
  schema_version: z.literal('path-training-readiness.v1'), scope_id: identity, as_of: at,
  status: z.enum(['training_pending', 'training_unavailable', 'training_partial', 'training_sufficient']),
  fit_job_state: z.enum(['pending', 'running', 'succeeded', 'missed', 'failed', 'expired']),
  frozen_fit: z.boolean(), read_as_of: at.nullable(), training_cutoff_at: at,
  requested_history_start_at: at.nullable(), accepted_windows: count.max(90).nullable(),
  excluded_windows: count.max(91).nullable(), baseline_n: count.max(90).nullable(),
  candidate_training_n: count.max(90).nullable(), low_group_n: count.max(90).nullable(), high_group_n: count.max(90).nullable(),
  minimum_training_windows: z.literal(60), minimum_group_windows: z.literal(30),
  baseline_sample_ready: z.boolean(), candidate_sample_ready: z.boolean(),
  available_history_start_at: at.nullable(), available_history_end_at: at.nullable(), available_candle_minutes: count.nullable(),
  history_basis: z.literal('native_ledger_metadata_at_fit_read_as_of'),
  accepted_history_start_at: at.nullable(), accepted_history_end_at: at.nullable(),
  exclusion_counts: z.record(count), reasons: identity.array(), fit_result_hash: hash.nullable(), model_hash: hash.nullable(),
  probability_qualified: z.literal(false), promotion_allowed: z.literal(false),
}).strict().superRefine((value, context) => {
  const counts = [value.accepted_windows, value.excluded_windows, value.baseline_n, value.candidate_training_n, value.low_group_n, value.high_group_n]
  const baselineReady = value.baseline_n !== null && value.baseline_n >= 60
  const candidateReady = value.candidate_training_n !== null && value.candidate_training_n >= 60
    && value.low_group_n !== null && value.low_group_n >= 30 && value.high_group_n !== null && value.high_group_n >= 30
  const extentValid = value.available_candle_minutes === 0
    ? value.available_history_start_at === null && value.available_history_end_at === null
    : value.available_candle_minutes !== null && value.available_candle_minutes > 0
      && value.available_history_start_at !== null && value.available_history_end_at !== null
      && Date.parse(value.available_history_start_at) < Date.parse(value.available_history_end_at)
      && value.read_as_of !== null && Date.parse(value.available_history_end_at) <= Date.parse(value.read_as_of)
  const expectedStatus = value.frozen_fit
    ? baselineReady && candidateReady ? 'training_sufficient' : baselineReady ? 'training_partial' : 'training_unavailable'
    : ['pending', 'running'].includes(value.fit_job_state) ? 'training_pending' : 'training_unavailable'
  const valid = value.frozen_fit
    ? value.fit_job_state === 'succeeded' && counts.every(item => item !== null)
      && value.read_as_of !== null && value.requested_history_start_at !== null && value.model_hash !== null && value.fit_result_hash !== null
      && value.accepted_windows === value.baseline_n && value.candidate_training_n! <= value.accepted_windows!
      && value.low_group_n! + value.high_group_n! === value.candidate_training_n && extentValid
      && Date.parse(value.read_as_of) <= Date.parse(value.training_cutoff_at) && Date.parse(value.read_as_of) <= Date.parse(value.as_of)
    : counts.every(item => item === null) && value.read_as_of === null && value.available_candle_minutes === null
      && value.available_history_start_at === null && value.available_history_end_at === null && value.fit_result_hash === null && value.model_hash === null
  if (!valid || value.status !== expectedStatus || value.baseline_sample_ready !== baselineReady || value.candidate_sample_ready !== candidateReady) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '冻结训练样本条件无法核对' })
  }
})
export type PathTrainingReadiness = z.infer<typeof pathTrainingReadinessSchema>

export const pathTrialSummarySchema = z.object({
  trial_id: identity, state, scope_ids: identity.array().min(1),
  protocol_version: z.enum(['path-shadow-24h.v1', 'path-shadow-24h.v2']), shadow_days: z.literal(28),
  registered_at: at, first_cutoff_at: at, last_cutoff_at: at,
  last_window_end_at: at, label_deadline_at: at, finished_at: at.nullable(),
  conclusion: z.literal('insufficient_evidence').nullable(),
  expected_slot_count: count, expected_prediction_count: count,
  published_prediction_count: count, unpublished_prediction_count: count,
  future_prediction_count: count, missed_prediction_count: count,
  complete_label_count: count, pending_label_count: count,
  unavailable_mature_label_count: count, job_error_count: count,
  deadline_elapsed: z.boolean(), report_available: z.boolean(), promotion_allowed: z.literal(false),
  // Older projections lack this field; absence remains unknown, never zero or ready.
  training_readiness: pathTrainingReadinessSchema.array().optional(),
}).strict().superRefine((value, context) => {
  if (value.expected_prediction_count !== 2 * value.expected_slot_count
    || value.published_prediction_count + value.unpublished_prediction_count !== value.expected_prediction_count
    || value.future_prediction_count + value.missed_prediction_count !== value.unpublished_prediction_count
    || value.complete_label_count + value.pending_label_count + value.unavailable_mature_label_count !== value.expected_slot_count) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '试验全量分母无法核对' })
  }
  if (value.training_readiness && (value.training_readiness.length !== value.scope_ids.length
    || new Set(value.training_readiness.map(item => item.scope_id)).size !== value.scope_ids.length
    || value.training_readiness.some(item => !value.scope_ids.includes(item.scope_id)))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '训练资产与登记不一致' })
  }
})
export type PathTrialSummary = z.infer<typeof pathTrialSummarySchema>

export const pathCurrentTrialSchema = z.object({
  schema_version: z.literal('risk-path-current-trial.v1'), scope_id: identity, as_of: at,
  trial: pathTrialSummarySchema.nullable(),
}).strict()
export type PathCurrentTrial = z.infer<typeof pathCurrentTrialSchema>

const scopeSchema = z.object({
  schema_version: z.literal('market-path-scope.v1'), scope_id: identity, asset_class: z.literal('crypto'),
  instrument_kind: z.literal('spot'), venue: z.literal('okx'), instrument: z.enum(['BTC-USDT', 'ETH-USDT']),
  quote_currency: z.literal('USDT'), price_type: z.literal('trade'), price_unit: identity,
  volume_unit: identity, timezone: z.literal('UTC'), calendar_ref: z.literal('crypto-24x7.v1'), interval_seconds: z.literal(60),
}).strict().refine(value => value.scope_id === `crypto:spot:okx:${value.instrument}:trade:1m`
  && value.price_unit === `USDT/${value.instrument.split('-')[0]}`
  && value.volume_unit === value.instrument.split('-')[0], '预测资产身份无法核对')

const frequencySchema = z.object({
  direction: z.enum(['long', 'short']), threshold: z.enum(['0.02', '0.05']), n: count.max(90), k: count.max(90),
  probability: loss.nullable(), status: z.enum(['experimental_uncalibrated', 'insufficient']),
}).strict().refine(value => value.k <= value.n && (value.probability === null) === (value.status === 'insufficient'), '事件频率资格无法核对')

export const pathForecastSchema = z.object({
  schema_version: z.literal('risk-path-forecast.v1'), scope: scopeSchema,
  model_version: z.enum(['BASE_EMPIRICAL_V1', 'COND_VOL_V1']), model_hash: hash, parameter_hash: hash,
  feature_hash: hash.nullable(), feature_latest_close_at: at.nullable(), feature_known_at: at.nullable(),
  feature_delay_seconds: count.nullable(), cutoff_at: at, published_at: at,
  publication_status: z.enum(['on_time', 'late']), window_start_at: at, window_end_at: at,
  anchor_policy: z.literal('scheduled_bar_open'), anchor_price: z.null(), selected_state: z.enum(['low', 'high']).nullable(),
  frequencies: frequencySchema.array().length(4),
  empirical_distribution: z.object({ n: count.max(90), long_mae_sorted: decimal.array(), short_mae_sorted: decimal.array(), quantile_upper_bound: z.null() }).strict().nullable(),
  calibration_status: z.enum(['experimental_uncalibrated', 'insufficient']), promotion_allowed: z.literal(false),
  reasons: identity.array(), payload_hash: hash,
}).strict().refine(value => new Set(value.frequencies.map(item => `${item.direction}:${item.threshold}`)).size === 4, '预测目标无法核对')

export const pathPredictionSchema = z.object({ prediction_id: identity, trial_id: identity, forecast: pathForecastSchema }).strict()
export const pathPredictionsSchema = z.object({
  schema_version: z.literal('risk-path-predictions.v1'), scope_id: identity, trial_id: identity.nullable(), as_of: at,
  predictions: pathPredictionSchema.array(),
}).strict()
export type PathPrediction = z.infer<typeof pathPredictionSchema>
export type PathPredictions = z.infer<typeof pathPredictionsSchema>

const scoreTargetSchema = z.object({
  scope_id: identity, direction: z.enum(['long', 'short']), threshold: z.enum(['0.02', '0.05']), primary: z.boolean(),
  expected_slots: count, baseline_scored: count, candidate_scored: count, paired_scored: count,
  baseline_brier: loss.nullable(), candidate_brier: loss.nullable(), paired_baseline_brier: loss.nullable(),
  paired_candidate_brier: loss.nullable(), paired_delta: delta.nullable(), independent_day_blocks: count,
  ci: z.null(), ci_status: z.literal('not_registered'),
}).strict()
const confidenceIntervalSchema = z.object({
  lower: delta, upper: delta, point_estimate: delta, confidence: z.literal('0.95'),
  protocol: z.literal('utc-day-paired-percentile-bootstrap.v2'), protocol_hash: hash,
  seed: z.literal(271828), replicates: z.literal(2000), independent_day_blocks: count.min(100),
  event_blocks: count.min(30), unit: z.literal('UTC-day'),
  estimator: z.literal('equal_day_mean_baseline_minus_candidate_brier'),
}).strict().refine(value => Number(value.lower) <= Number(value.upper), '区间上下限无法核对')
const scoreTargetV2Schema = scoreTargetSchema.extend({
  ci: confidenceIntervalSchema.nullable(), ci_status: z.enum(['available', 'insufficient']),
  ci_reasons: identity.array(),
}).refine(value => (value.ci !== null) === (value.ci_status === 'available'), '区间资格无法核对')
const scoreInferenceSchema = z.object({
  status: z.enum(['available', 'insufficient']), ci: confidenceIntervalSchema.nullable(),
  protocol: z.literal('utc-day-paired-percentile-bootstrap.v2'), protocol_hash: hash,
  confidence: z.literal('0.95'), seed: z.literal(271828), replicates: z.literal(2000),
  unit: z.literal('UTC-day'), estimator: z.literal('equal_day_mean_baseline_minus_candidate_brier'),
  expected_pairs: count, complete_pairs: count, independent_day_blocks: count, event_blocks: count.nullable(),
  reasons: identity.array(), promotion_allowed: z.literal(false),
}).strict().refine(value => value.complete_pairs <= value.expected_pairs
  && (value.ci !== null) === (value.status === 'available')
  && (!value.ci || (value.ci.protocol_hash === value.protocol_hash
    && value.ci.independent_day_blocks === value.independent_day_blocks
    && value.ci.event_blocks === value.event_blocks
    && value.complete_pairs === value.expected_pairs && value.reasons.length === 0)), '区间样本或原件无法核对')
const alarmLeadTimeSchema = z.object({
  matched_count: count.min(1), minimum_seconds: decimal.refine(value => Number(value) >= 0),
  median_seconds: decimal.refine(value => Number(value) >= 0), resolution_seconds: z.literal(60), conservative: z.literal(true),
}).strict().refine(value => Number(value.minimum_seconds) <= Number(value.median_seconds), '提前量无法核对')
const alarmMetricsSchema = z.object({
  precision: loss.nullable(), recall: loss.nullable(),
  false_episodes_per_asset_day: decimal.refine(value => Number(value) >= 0).nullable(),
  lead_time: alarmLeadTimeSchema.nullable(), status: z.enum(['evaluated', 'partial', 'unknown']),
  reason: z.enum(['forecast_alarm_and_authenticated_delivery_exposure_not_registered', 'matched_forecast_alarm_originals', 'mature_alarm_or_label_unavailable']),
}).strict()
const pathScoreBaseSchema = z.object({
  schema_version: z.literal('path-shadow-score.v1'), expected_slot_count: count, expected_prediction_count: count,
  published_prediction_count: count, unpublished_prediction_count: count, on_time_prediction_count: count, late_prediction_count: count,
  complete_label_count: count, partial_label_count: count, unavailable_label_count: count,
  independent_paired_day_blocks: count, observed_primary_event_day_blocks: count, independent_event_blocks: z.null(),
  minimum_time_blocks: z.literal(100), minimum_event_blocks: z.literal(30), targets: scoreTargetSchema.array(),
  exclusion_count_basis: z.literal('target_evaluations'),
  exclusions: z.object({ scope_id: identity, cutoff_at: at, direction: z.enum(['long', 'short']), threshold: z.enum(['0.02', '0.05']), reasons: identity.array() }).strict().array(),
  exclusion_counts: z.record(count),
  alarm_metrics: alarmMetricsSchema,
  ci: z.null(), ci_status: z.literal('not_registered'), promotion_allowed: z.literal(false), conclusion: z.literal('insufficient_evidence'), reasons: identity.array(),
}).strict()
const pathScoreV2Schema = pathScoreBaseSchema.extend({
  schema_version: z.literal('path-shadow-score.v2'), independent_event_blocks: count.nullable(),
  targets: scoreTargetV2Schema.array(), ci: confidenceIntervalSchema.nullable(),
  ci_status: z.enum(['available', 'insufficient']), inference: scoreInferenceSchema,
  alarm_originals: z.object({ original_ref: identity, original_hash: hash }).strict().array(),
})
export const pathScoreSchema = z.discriminatedUnion('schema_version', [pathScoreBaseSchema, pathScoreV2Schema])
  .superRefine((value, context) => {
    if (value.published_prediction_count + value.unpublished_prediction_count !== value.expected_prediction_count
      || value.expected_prediction_count !== 2 * value.expected_slot_count
      || value.on_time_prediction_count + value.late_prediction_count !== value.published_prediction_count
      || value.complete_label_count + value.partial_label_count + value.unavailable_label_count !== value.expected_slot_count) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: '结项全量分母无法核对' })
    }
    if (value.schema_version === 'path-shadow-score.v2'
      && (value.ci_status !== value.inference.status || JSON.stringify(value.ci) !== JSON.stringify(value.inference.ci))) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: '区间与固定评价回执无法核对' })
    }
  })
const reportBaseSchema = z.object({
  report_id: identity, trial_id: identity, report_version: z.literal('path-shadow-evaluation.v1'), computed_at: at, protocol_hash: hash,
  score: pathScoreSchema,
  prediction_refs: z.object({ prediction_id: identity, payload_hash: hash }).strict().array(),
  observation_refs: z.object({ observation_id: identity, payload_hash: hash, complete: z.boolean() }).strict().array(), payload_hash: hash,
}).strict()
const reportSchema = z.discriminatedUnion('report_version', [reportBaseSchema, reportBaseSchema.extend({
  report_version: z.literal('path-shadow-evaluation.v2'),
  alarm_score_original: z.object({ original_ref: identity, original_hash: hash }).strict().nullable(),
})]).refine(value => value.report_version.endsWith('.v1') === value.score.schema_version.endsWith('.v1'), '报告与分数版本无法核对')
export const pathScoreViewSchema = z.object({
  schema_version: z.literal('risk-path-score-view.v1'), as_of: at, trial: pathTrialSummarySchema,
  report: reportSchema.nullable(), receipt_verification: z.literal('stored_payload_hash'),
}).strict()
export type PathScoreView = z.infer<typeof pathScoreViewSchema>

function verifyScope(scopeId: string, actual: string) {
  if (scopeId !== actual) throw new Error('预测记录与当前资产不一致，请刷新核对。')
}
function verifyTrial(trialId: string, actual: string) {
  if (trialId !== actual) throw new Error('预测成绩与当前试验不一致，请刷新核对。')
}

export const riskPathsApi = {
  list: async (scopeId?: string, signal?: AbortSignal) => {
    const params = scopeId ? `?scope_id=${encodeURIComponent(scopeId)}` : ''
    const result = z.object({ schema_version: z.literal('risk-path-trials.v1'), as_of: at, trials: pathTrialSummarySchema.array() }).strict()
      .parse(await request<unknown>(`/v1/risk/path-trials${params}`, { signal }))
    if (scopeId && result.trials.some(item => !item.scope_ids.includes(scopeId))) throw new Error('试验与当前资产不一致，请刷新核对。')
    return result
  },
  current: async (scopeId: string, signal?: AbortSignal): Promise<PathCurrentTrial> => {
    const result = pathCurrentTrialSchema.parse(await request<unknown>(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/path-trial`, { signal }))
    verifyScope(scopeId, result.scope_id)
    if (result.trial && !result.trial.scope_ids.includes(scopeId)) throw new Error('试验与当前资产不一致，请刷新核对。')
    return result
  },
  predictions: async (scopeId: string, trialId: string, signal?: AbortSignal): Promise<PathPredictions> => {
    const result = pathPredictionsSchema.parse(await request<unknown>(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/predictions?trial_id=${encodeURIComponent(trialId)}&limit=100`, { signal }))
    verifyScope(scopeId, result.scope_id)
    if (result.trial_id !== trialId) throw new Error('预测记录与当前试验不一致，请刷新核对。')
    result.predictions.forEach(item => { verifyTrial(trialId, item.trial_id); verifyScope(scopeId, item.forecast.scope.scope_id) })
    return result
  },
  score: async (trialId: string, signal?: AbortSignal): Promise<PathScoreView> => {
    const result = pathScoreViewSchema.parse(await request<unknown>(`/v1/risk/path-trials/${encodeURIComponent(trialId)}/score`, { signal }))
    verifyTrial(trialId, result.trial.trial_id)
    if (result.report) {
      verifyTrial(trialId, result.report.trial_id)
      if (result.report.report_version.endsWith('.v1') !== result.trial.protocol_version.endsWith('.v1')
        || result.report.score.expected_slot_count !== result.trial.expected_slot_count
        || result.report.score.targets.some(item => !result.trial.scope_ids.includes(item.scope_id))) {
        throw new Error('结项成绩与登记样本不一致，请刷新核对。')
      }
    }
    return result
  },
}

export function riskPathErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : ''
  if (message === 'risk_path_scope_not_found') return '当前资产尚未接入路径预测试验。'
  if (message === 'risk_path_object_not_found') return '该预测或试验回执不存在，请刷新核对。'
  if (message === 'risk_path_receipt_unverified' || error instanceof z.ZodError) return '预测回执无法核验，当前成绩未确认。请刷新后核对。'
  return /[\u4e00-\u9fff]/.test(message) ? message : '预测数据读取失败，当前成绩未确认。请刷新重试。'
}
