// Generated from contracts/schemas/*.schema.yaml. Do not edit.
import { z } from 'zod'

type IndexScopeRelations = {
  state: 'not_granted' | 'not_generated' | 'verification_required';
  model_id: string | null; forecast_id: string | null;
  cutoff_at: string | null; recorded_at: string | null;
}
function indexUtcMicros(value: string): bigint | null {
  const match = /^(?!0000)(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value)
  if (!match) return null
  const seconds = Date.parse(match[1] + 'Z')
  if (!Number.isFinite(seconds)) return null
  return BigInt(seconds) * 1000n + BigInt((match[2] ?? '').padEnd(6, '0'))
}
function indexScopeRelations(value: IndexScopeRelations, ctx: z.RefinementCtx): void {
  const none = value.forecast_id === null && value.cutoff_at === null && value.recorded_at === null
  let valid = value.state === 'not_granted' ? value.model_id === null && none
    : value.state === 'not_generated' ? value.model_id !== null && none
    : value.model_id !== null && value.forecast_id !== null && value.cutoff_at !== null && value.recorded_at !== null
  if (valid && value.state === 'verification_required') {
    const cutoff = indexUtcMicros(value.cutoff_at!)
    const recorded = indexUtcMicros(value.recorded_at!)
    valid = cutoff !== null && recorded !== null && cutoff % 60000000n === 0n && cutoff <= recorded
  }
  if (!valid) ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'history_forecast_index_state_identity_or_clock_invalid' })
}
function indexObservedRelations(value: { observed_at: string; scopes: readonly IndexScopeRelations[] }, ctx: z.RefinementCtx): void {
  const observed = indexUtcMicros(value.observed_at)
  if (observed === null || value.scopes.some(item => item.recorded_at !== null &&
      (indexUtcMicros(item.recorded_at) === null || indexUtcMicros(item.recorded_at)! > observed))) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'history_forecast_index_future_record_invalid' })
  }
}


export const historyForecastIndexBtcV2Schema = /* @__PURE__ */ (() => (z.object({ "asset": z.literal("BTC"), "scope_id": z.literal("crypto:spot:okx:BTC-USDT:trade:1m"), "asset_label": z.literal("比特币"), "model_id": z.union([z.string().regex(new RegExp("^hfit2_[a-f0-9]{48}$")), z.null()]), "state": z.enum(["not_granted","not_generated","verification_required"]), "forecast_id": z.union([z.string().regex(new RegExp("^hforecast2_[a-f0-9]{48}$")), z.null()]), "cutoff_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "recorded_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "reason_label": z.string().min(1).max(512), "probabilities": z.null() }).strict().superRefine(indexScopeRelations)))()
export type HistoryForecastIndexBtcV2 = z.infer<typeof historyForecastIndexBtcV2Schema>

export const historyForecastIndexEthV2Schema = /* @__PURE__ */ (() => (z.object({ "asset": z.literal("ETH"), "scope_id": z.literal("crypto:spot:okx:ETH-USDT:trade:1m"), "asset_label": z.literal("以太坊"), "model_id": z.union([z.string().regex(new RegExp("^hfit2_[a-f0-9]{48}$")), z.null()]), "state": z.enum(["not_granted","not_generated","verification_required"]), "forecast_id": z.union([z.string().regex(new RegExp("^hforecast2_[a-f0-9]{48}$")), z.null()]), "cutoff_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "recorded_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "reason_label": z.string().min(1).max(512), "probabilities": z.null() }).strict().superRefine(indexScopeRelations)))()
export type HistoryForecastIndexEthV2 = z.infer<typeof historyForecastIndexEthV2Schema>

export const historyForecastIndexV2Schema = /* @__PURE__ */ (() => (z.object({ "schema_version": z.literal("history-forecast-index.v2"), "owner_authenticated": z.literal(true), "observed_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")), "current_originals_verified": z.literal(false), "financial_qualified": z.literal(false), "historical_pit_verified": z.literal(false), "scopes": z.tuple([z.object({ "asset": z.literal("BTC"), "scope_id": z.literal("crypto:spot:okx:BTC-USDT:trade:1m"), "asset_label": z.literal("比特币"), "model_id": z.union([z.string().regex(new RegExp("^hfit2_[a-f0-9]{48}$")), z.null()]), "state": z.enum(["not_granted","not_generated","verification_required"]), "forecast_id": z.union([z.string().regex(new RegExp("^hforecast2_[a-f0-9]{48}$")), z.null()]), "cutoff_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "recorded_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "reason_label": z.string().min(1).max(512), "probabilities": z.null() }).strict().superRefine(indexScopeRelations), z.object({ "asset": z.literal("ETH"), "scope_id": z.literal("crypto:spot:okx:ETH-USDT:trade:1m"), "asset_label": z.literal("以太坊"), "model_id": z.union([z.string().regex(new RegExp("^hfit2_[a-f0-9]{48}$")), z.null()]), "state": z.enum(["not_granted","not_generated","verification_required"]), "forecast_id": z.union([z.string().regex(new RegExp("^hforecast2_[a-f0-9]{48}$")), z.null()]), "cutoff_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "recorded_at": z.string().datetime({ offset: true }).regex(new RegExp("^(?!0000)\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|\\+00:00)$")).nullable(), "reason_label": z.string().min(1).max(512), "probabilities": z.null() }).strict().superRefine(indexScopeRelations)]) }).strict().superRefine(indexObservedRelations)))()
export type HistoryForecastIndexV2 = z.infer<typeof historyForecastIndexV2Schema>
