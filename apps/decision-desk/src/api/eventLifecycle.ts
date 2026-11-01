import { z } from 'zod'
import { request } from './client'

const date = z.string().datetime({ offset: true })
const number = z.string().refine(v => Number.isFinite(Number(v)))
const evaluation = z.object({ reason_codes: z.array(z.string()), state_after: z.string(),
  confirmation_inputs: z.object({ event_return: number.nullable().optional(),
    minute_volume_ratio: number.nullable().optional() }).passthrough().nullable().optional(),
}).passthrough()
const position = z.object({
  monitor_id: z.string(), position_context: z.enum(['flat', 'long']),
  state: z.enum(['waiting', 'triggered', 'invalidated', 'expired', 'missing']),
  next_due: date.nullable(), latest_evaluation: evaluation.nullable(),
  confirmation_protocol: z.string().nullable().optional(),
  strategy_target: z.string().nullable().optional(), rule_summary: z.string().nullable().optional(),
  strategy_probability_status: z.string().nullable().optional(),
  strategy_probability_gap: z.string().nullable().optional(),
}).passthrough()
const notice = z.object({
  notice_id: z.string(), event_key: z.string(), subject: z.string(), body: z.string(),
  created_at: date, delivery_status: z.string(), display_at: date.nullable(), ack_at: date.nullable(),
}).passthrough()
const outcome = z.object({
  state: z.enum(['waiting', 'partial', 'complete', 'missing']),
  event_score: z.object({ label: z.string(), brier: number, log_loss: z.union([number, z.number()]) }).nullable(),
  assets: z.array(z.object({ instrument: z.string(), state: z.string(), return_24h: number.nullable(),
    strategy_net_return: number.nullable(), held_long_reduce_at: date.nullable(),
    held_long_reduction_value_fraction: number.nullable(), gaps: z.array(z.string()) }).passthrough()),
  gaps: z.array(z.string()),
}).passthrough()
export const eventLifecycleSchema = z.object({
  schema_version: z.literal('event-lifecycle-view.v1'), event_key: z.string(), as_of: date,
  scheduled_at: date, reference_period: z.string().nullable(), forecast_id: z.string().nullable(),
  coverage: z.array(z.object({ slot_kind: z.string(), due_at: date,
    state: z.enum(['pending', 'ready', 'late', 'missing', 'missed']),
    forecast_published_at: date.nullable() }).passthrough()),
  realization: z.object({ state: z.enum(['realized', 'awaiting_release', 'awaiting_actual']),
    realization_id: z.string().nullable(), scenario_id: z.string().nullable(),
    known_at: date.nullable(), recorded_at: date.nullable(),
    metrics: z.array(z.object({ metric_id: z.string(), value: number.nullable(), unit: z.string() })) }),
  assets: z.array(z.object({ instrument: z.enum(['BTC-USDT', 'ETH-USDT']), positions: z.array(position) })),
  notices: z.array(notice), outcome: outcome.nullable(), outcome_due_at: date,
  sources: z.array(z.object({ source_id: z.string(), label: z.string(), state: z.string(),
    last_success_at: date.nullable(), error_code: z.string().nullable() })),
}).strict()
export type EventLifecycle = z.infer<typeof eventLifecycleSchema>
export type EventNotice = z.infer<typeof notice>
const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const receipt = z.object({ command_id: z.string(), notice_id: z.string(), command: z.enum(['display', 'ack']), recorded_at: date })
export const eventLifecycleApi = {
  detail: async (key: string, signal?: AbortSignal) => eventLifecycleSchema.parse(await request(`/v1/event-lifecycles/${encodeURIComponent(key)}`, { signal, headers: { 'X-Owner-Id': owner } })),
  command: async (noticeId: string, command: 'display' | 'ack') => {
    const requestId = `event-${command}:${noticeId}`
    const value = receipt.parse(await request(`/v1/event-notices/${encodeURIComponent(noticeId)}/commands`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': owner, 'Idempotency-Key': requestId },
      body: JSON.stringify({ request_id: requestId, command }),
    }))
    if (value.notice_id !== noticeId || value.command !== command) throw new Error('提醒确认回执无法核对。')
    return value
  },
}
