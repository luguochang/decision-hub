import { z } from 'zod'
import { request } from './client'

const date = z.string().datetime({ offset: true })
const decimal = z.string().refine(value => value.trim() !== '' && Number.isFinite(Number(value)), '数值无法核对')
const probability = decimal.refine(value => Number(value) >= 0 && Number(value) <= 1, '概率范围无法核对')
const macroSeries = z.object({
  series_id: z.enum(['DGS10', 'DFII10', 'DTWEXBGS']), label: z.string(),
  unit: z.enum(['percent', 'index_jan_2006_100']), value: decimal,
  observation_date: z.string().date(), known_at: date, change_5_observations: decimal,
  source_url: z.string().url(), observation_ref: z.string(), content_hash: z.string().regex(/^[a-f0-9]{64}$/),
}).strict().superRefine((value, ctx) => {
  if (value.unit !== (value.series_id === 'DTWEXBGS' ? 'index_jan_2006_100' : 'percent')
    || value.source_url !== `https://fred.stlouisfed.org/graph/fredgraph.csv?id=${value.series_id}`
    || value.observation_date > value.known_at.slice(0, 10))
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: '宏观数据单位、日期或来源无法核对' })
})
export const eventMacroContextSchema = z.object({
  schema_version: z.literal('event-macro-context-view.v1'), as_of: date,
  state: z.enum(['unknown', 'headwind', 'tailwind', 'mixed']), series: z.array(macroSeries), gaps: z.array(z.string()),
}).strict().superRefine((value, ctx) => {
  if (new Set(value.series.map(s => s.series_id)).size !== value.series.length
    || value.series.some(s => new Date(s.known_at).getTime() > new Date(value.as_of).getTime()))
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: '宏观数据包含重复指标或未来信息' })
})
export type EventMacroContext = z.infer<typeof eventMacroContextSchema>
const item = z.object({
  event_key: z.string(), title: z.string(), reference_period: z.string().nullable(), scheduled_at: date,
  state: z.enum(['waiting_inputs', 'pending', 'running', 'published', 'failed', 'missed', 'not_scheduled']),
  forecast_id: z.string().nullable(), last_error_code: z.string().nullable(),
  next_attempt_at: date.nullable(), updated_at: date.nullable(),
}).strict()
export const eventPrevisionListSchema = z.object({
  schema_version: z.literal('event-prevision-list.v1'), as_of: date, events: z.array(item),
  uncovered_families: z.array(z.string()),
}).strict()
const estimate = z.object({
  target: z.string(), probability_status: z.enum(['estimated_uncalibrated', 'unavailable']), basis: z.string(),
  scenario_conditioned: z.boolean(), value: probability.nullable(), lower_95: probability.nullable(),
  upper_95: probability.nullable(), events: z.number().int().nonnegative(), entered: z.number().int().nonnegative(),
  successes: z.number().int().nonnegative(), assumptions: z.array(z.string()),
}).strict().superRefine((value, ctx) => {
  if ((value.value === null) !== (value.events === 0) || value.successes > value.events || value.entered > value.events)
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: '样本与概率不一致' })
})
export const currentEventConfirmationSchema = z.object({
  confirmation_protocol: z.literal('event-confirmation.v1'),
  strategy_target: z.literal('cpi_event_anchored_policy_net_profit_event_24h.v1'),
  policy_hash: z.string().regex(/^[a-f0-9]{64}$/),
  flat_entry: z.string(), flat_exit: z.string(), flat_invalidation: z.string(),
  long_entry: z.string(), long_invalidation: z.string(),
  probability_status: z.literal('unknown'), probability_gap: z.string(),
}).strict()
const plan = z.object({
  scenario_id: z.string(), event_probability: probability, main_chain: z.array(z.string()),
  counter_mechanism: z.string(), activation_required: z.array(z.string()),
  flat_entry: z.array(z.string()), flat_exit: z.array(z.string()), flat_invalidation: z.array(z.string()),
  held_long_entry: z.array(z.string()), held_long_invalidation: z.array(z.string()),
  asset_estimate: estimate, net_profit_estimate: estimate, armed: z.literal(false),
  current_confirmation: currentEventConfirmationSchema.nullable().optional(),
}).strict()
const asset = z.object({
  instrument: z.enum(['BTC-USDT', 'ETH-USDT']), state: z.enum(['available', 'missing', 'expired', 'outdated']),
  as_of: date.nullable(), published_at: date.nullable(), refresh_required_at: date.nullable(),
  entry_valid_from: date.nullable(), entry_valid_until: date.nullable(), exit_valid_until: date.nullable(),
  asset_estimate: estimate.nullable(), net_profit_estimate: estimate.nullable(),
  net_profit_given_entry_estimate: estimate.nullable(), plans: z.array(plan), gaps: z.array(z.string()),
  snapshot_ref: z.string().nullable(), snapshot_hash: z.string().nullable(), monitoring_armed: z.literal(false),
  macro_context: eventMacroContextSchema.nullable().optional(),
}).strict()
export const eventPrevisionDetailSchema = z.object({
  schema_version: z.literal('event-prevision-detail.v1'), as_of: date, event: item,
  snapshot_ref: z.string().nullable(), snapshot_hash: z.string().nullable(), estimate_as_of: date.nullable(),
  published_at: date.nullable(), probability_status: z.enum(['estimated_uncalibrated', 'missing']),
  baseline_kind: z.literal('prior_month'), headline_baseline: decimal.nullable(), core_baseline: decimal.nullable(),
  tolerance_pp: decimal.nullable(), target_definition: z.string().nullable(), sample_count: z.number().int().nonnegative(),
  scenarios: z.array(z.object({ scenario_id: z.string(), headline_band: z.enum(['cooler', 'near', 'hotter']),
    core_band: z.enum(['cooler', 'near', 'hotter']), probability, historical_matches: z.number().int().nonnegative(),
  }).strict()), assumptions: z.array(z.string()), assets: z.array(asset),
}).strict().superRefine((value, ctx) => {
  if (value.probability_status === 'missing') {
    if (value.scenarios.length || value.snapshot_ref !== null) ctx.addIssue({ code: z.ZodIssueCode.custom, message: '未生成预判却返回了情景' })
    return
  }
  const cells = new Set(value.scenarios.map(s => `${s.headline_band}:${s.core_band}`))
  const sum = value.scenarios.reduce((total, s) => total + Number(s.probability), 0)
  if (value.scenarios.length !== 9 || cells.size !== 9 || Math.abs(sum - 1) > 1e-12 || !value.published_at
    || new Date(value.published_at).getTime() >= new Date(value.event.scheduled_at).getTime())
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: '事前情景分布无法核对' })
})
export type EventPrevisionDetail = z.infer<typeof eventPrevisionDetailSchema>
export type EventProbabilityEstimate = z.infer<typeof estimate>
export const eventPrevisionsApi = {
  list: async (signal?: AbortSignal) => eventPrevisionListSchema.parse(await request('/v1/event-previsions', { signal })),
  detail: async (eventKey: string, signal?: AbortSignal) => eventPrevisionDetailSchema.parse(await request(`/v1/event-previsions/${encodeURIComponent(eventKey)}`, { signal })),
}
