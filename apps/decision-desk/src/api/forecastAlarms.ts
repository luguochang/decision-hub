import { z } from 'zod'
import { request } from './client'
import { productOwner } from './delivery'
import { riskRequestId } from './risk'

const at = z.string().datetime({ offset: true })
const id = z.string().min(1)
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const decimal = z.string().refine(value => value.trim() !== '' && Number.isFinite(Number(value)))
const alarmSchema = z.object({
  schema_version: z.literal('forecast-alarm.v1'), alarm_policy: z.literal('shadow-forecast-ui-receipts.v1'),
  alarm_id: id, trial_id: id, prediction_id: id, prediction_hash: hash,
  method: z.enum(['BASE_EMPIRICAL_V1', 'COND_VOL_V1']), scope_id: id,
  cutoff_at: at, published_at: at, window_start_at: at, window_end_at: at,
  direction: z.enum(['long', 'short']), threshold: z.enum(['0.02', '0.05']),
  frequency: decimal.refine(value => Number(value) >= .2 && Number(value) <= 1), known_at: at,
  experimental: z.literal(true), probability_qualified: z.literal(false), trading_authority: z.literal(false), payload_hash: hash,
}).strict().refine(value => Date.parse(value.cutoff_at) <= Date.parse(value.published_at)
  && Date.parse(value.published_at) <= Date.parse(value.known_at)
  && Date.parse(value.known_at) <= Date.parse(value.window_start_at)
  && Date.parse(value.window_start_at) < Date.parse(value.window_end_at), '预警发布时间无法核对')
export const forecastAlarmListSchema = z.object({
  schema_version: z.literal('forecast-alarm-list.v1'), scope_id: id, as_of: at,
  items: z.object({ alarm: alarmSchema, display_status: z.enum(['not_recorded', 'displayed']),
    exposure_status: z.enum(['not_recorded', 'visible']), acknowledged: z.boolean() }).strict().array(),
  experimental: z.literal(true), default_collapsed: z.literal(true), probability_qualified: z.literal(false), trading_authority: z.literal(false),
}).strict().superRefine((value, context) => {
  if (new Set(value.items.map(item => item.alarm.alarm_id)).size !== value.items.length
    || value.items.some(item => item.alarm.scope_id !== value.scope_id || Date.parse(item.alarm.known_at) > Date.parse(value.as_of)
      || (item.exposure_status === 'visible' && item.display_status !== 'displayed')
      || (item.acknowledged && item.exposure_status !== 'visible'))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '预警资产或回执链无法核对' })
  }
})
export const forecastAlarmReceiptSchema = z.object({
  schema_version: z.literal('forecast-alarm-receipt.v1'), receipt_id: id, alarm_id: id, alarm_hash: hash,
  receipt_kind: z.enum(['displayed', 'visible', 'acknowledged']), owner_id: id, request_id: id,
  known_at: at, parent_receipt_id: id.nullable(), parent_receipt_hash: hash.nullable(), surface: z.literal('pc'), visible_duration_ms: z.number().int().min(1000).nullable(),
  evidence_source: z.literal('pc_browser_reported'), payload_hash: hash,
}).strict().refine(value => value.receipt_kind === 'displayed' ? value.parent_receipt_id === null && value.parent_receipt_hash === null
  : value.parent_receipt_id !== null && value.parent_receipt_hash !== null && (value.receipt_kind !== 'visible' || value.visible_duration_ms !== null), '显示回执原件链无法核对')
export type ForecastAlarmView = z.infer<typeof forecastAlarmListSchema>['items'][number]
export type ForecastAlarmReceipt = z.infer<typeof forecastAlarmReceiptSchema>
type ReceiptKind = ForecastAlarmReceipt['receipt_kind']

async function save(alarm: ForecastAlarmView['alarm'], kind: ReceiptKind, parent?: ForecastAlarmReceipt): Promise<ForecastAlarmReceipt> {
  if (parent && (parent.alarm_id !== alarm.alarm_id || parent.alarm_hash !== alarm.payload_hash || parent.owner_id !== productOwner)) throw new Error('前序回执与当前预警不一致。')
  const requestId = riskRequestId(`forecast:${productOwner}:${alarm.alarm_id}:${kind}:${parent?.receipt_id ?? ''}:${parent?.payload_hash ?? ''}`)
  const body = { owner_id: productOwner, request_id: requestId,
    ...(kind === 'visible' ? { display_receipt_id: parent?.receipt_id, visible: true, surface: 'pc', visible_duration_ms: 1000 } : {}),
    ...(kind === 'acknowledged' ? { exposure_receipt_id: parent?.receipt_id } : {}),
  }
  const operation = kind === 'displayed' ? 'display' : kind === 'visible' ? 'exposure' : 'acknowledge'
  const receipt = forecastAlarmReceiptSchema.parse(await request(`/v1/risk/forecast-alarms/${encodeURIComponent(alarm.alarm_id)}/${operation}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': productOwner, 'Idempotency-Key': requestId }, body: JSON.stringify(body),
  }))
  if (receipt.alarm_id !== alarm.alarm_id || receipt.alarm_hash !== alarm.payload_hash || receipt.owner_id !== productOwner
    || receipt.request_id !== requestId || receipt.receipt_kind !== kind || receipt.parent_receipt_id !== (parent?.receipt_id ?? null)
    || receipt.parent_receipt_hash !== (parent?.payload_hash ?? null)) {
    throw new Error('预警回执与本次原件不一致，结果尚未确认。')
  }
  return receipt
}
export const forecastAlarmsApi = {
  list: async (scopeId: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ owner_id: productOwner, limit: '50' })
    const value = forecastAlarmListSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/forecast-alarms?${params}`, { signal, headers: { 'X-Owner-Id': productOwner } }))
    if (value.scope_id !== scopeId) throw new Error('预警列表与当前资产不一致。')
    return value
  },
  display: (alarm: ForecastAlarmView['alarm']) => save(alarm, 'displayed'),
  exposure: (alarm: ForecastAlarmView['alarm'], parent: ForecastAlarmReceipt) => save(alarm, 'visible', parent),
  acknowledge: (alarm: ForecastAlarmView['alarm'], parent: ForecastAlarmReceipt) => save(alarm, 'acknowledged', parent),
}
