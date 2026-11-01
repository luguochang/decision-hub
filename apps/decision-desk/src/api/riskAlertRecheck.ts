import { z } from 'zod'
import { ApiRequestError, request } from './client'

export const RECHECK_OWNER = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const date = z.string().datetime({ offset: true })
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const capacity = z.record(z.union([z.number().int(), z.string()]))
export const recheckCommandSchema = z.object({ request_id: z.string().min(1).max(128), expected_head_hash: hash }).strict()
export type RecheckCommand = z.infer<typeof recheckCommandSchema>
export const recheckReceiptSchema = z.object({
  schema_version: z.literal('risk-alert-recheck-receipt.v1'), request_id: z.string().min(1), owner_id: z.string().min(1),
  alert_id: z.string().min(1), scope_id: z.string().min(1), original_request_id: z.string().min(1), original_head_hash: hash,
  original_triggered_at: date, original_resolved_at: date.nullable(), accepted_at: date,
  research_request_id: z.string().min(1), trigger_key: z.string().min(1), admission_origin: z.literal('automatic'),
  mode: z.literal('historical_event_current_evidence'), automatic_new_rounds: z.literal(false),
}).strict()
export const recheckStateSchema = z.object({
  schema_version: z.literal('risk-alert-recheck-state.v1'), alert_id: z.string(), scope_id: z.string(),
  instrument: z.enum(['BTC-USDT', 'ETH-USDT']), observed_at: date, original_triggered_at: date,
  original_resolved_at: date.nullable(), original_request_id: z.string(), head_hash: hash,
  eligible: z.boolean(), reason_code: z.string().nullable(), capacity, receipt: recheckReceiptSchema.nullable(),
  research_request: z.object({
    schema_version: z.literal('risk-research-request.v1'), request_id: z.string(), scope_id: z.string(), instrument: z.enum(['BTC-USDT', 'ETH-USDT']),
    status: z.enum(['pending_admission', 'claiming', 'queued', 'completed', 'failed']), run_id: z.string().nullable(), run_status: z.string().nullable(),
    created_at: date, available_at: date, attempts: z.number().int().min(0).max(3), error_code: z.string().nullable(), trigger_refs: z.array(z.string()),
    updated_at: date, lease_generation: z.number().int().min(0).max(2_147_483_647), lease_expires_at: date.nullable(), run_created_at: date.nullable(), run_finished_at: date.nullable(),
  }).strict().nullable(),
  timeline: z.array(z.object({
    schema_version: z.literal('risk-alert-recheck-trace.v1'), alert_id: z.string(), research_request_id: z.string(),
    event: z.enum(['claimed', 'budget_blocked', 'admission_failed', 'queued', 'claim_expired']), recorded_at: date,
    attempt: z.number().int().min(1).max(3), lease_generation: z.number().int().min(1).max(2_147_483_647), request_status: z.string(),
    reason_code: z.string().nullable(), capacity, event_id: z.string().nullable(), run_id: z.string().nullable(),
  }).strict()).max(16), scope_preparation_required_on_claim: z.literal(true),
}).strict()
export type RecheckState = z.infer<typeof recheckStateSchema>
export type RecheckReceipt = z.infer<typeof recheckReceiptSchema>

function checkReceipt(view: RecheckReceipt, alert: string, scope: string, command?: RecheckCommand) {
  if (view.alert_id !== alert || view.scope_id !== scope || (command && (view.owner_id !== RECHECK_OWNER
    || view.request_id !== command.request_id || view.original_head_hash !== command.expected_head_hash))) {
    throw new Error('补查回执与原告警或原请求不一致，尚未确认。')
  }
  if (view.trigger_key !== `risk_alert_recheck:${view.request_id}`) throw new Error('补查触发记录无法核对。')
}
function checkState(view: RecheckState, alert: string, scope: string) {
  if (view.alert_id !== alert || view.scope_id !== scope || view.eligible !== (view.reason_code === null)) throw new Error('补查条件与原告警不一致。')
  if (view.receipt) {
    checkReceipt(view.receipt, alert, scope)
    if (view.receipt.original_request_id !== view.original_request_id || view.receipt.original_head_hash !== view.head_hash) throw new Error('原告警头与补查回执不一致。')
  }
  if (view.research_request && (!view.receipt || view.research_request.request_id !== view.receipt.research_request_id
    || view.research_request.scope_id !== scope || view.research_request.instrument !== view.instrument
    || view.research_request.trigger_refs.length !== 1 || view.research_request.trigger_refs[0] !== alert)) throw new Error('补查研究身份无法核对。')
  if (view.timeline.some(t => t.alert_id !== alert || t.research_request_id !== view.receipt?.research_request_id)) throw new Error('补查时间记录身份无法核对。')
  return view
}
const path = (alert: string) => `/v1/risk/alerts/${encodeURIComponent(alert)}/research-recheck`
export const riskAlertRecheckApi = {
  state: async (alert: string, scope: string, signal?: AbortSignal) => checkState(recheckStateSchema.parse(await request(path(alert), { signal })), alert, scope),
  receipt: async (alert: string, scope: string, command: RecheckCommand): Promise<RecheckReceipt | null> => {
    try {
      const view = recheckReceiptSchema.parse(await request(`${path(alert)}/commands/${encodeURIComponent(command.request_id)}?expected_head_hash=${encodeURIComponent(command.expected_head_hash)}`, { headers: { 'X-Owner-Id': RECHECK_OWNER } }))
      checkReceipt(view, alert, scope, command); return view
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 404 && error.message === 'risk_alert_recheck_receipt_not_found') return null
      throw error
    }
  },
  accept: async (alert: string, scope: string, command: RecheckCommand) => {
    const view = recheckReceiptSchema.parse(await request(path(alert), { method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-Owner-Id': RECHECK_OWNER, 'Idempotency-Key': command.request_id,
    }, body: JSON.stringify(recheckCommandSchema.parse(command)) }))
    checkReceipt(view, alert, scope, command); return view
  },
}

const intentSchema = z.object({ owner_id: z.string(), alert_id: z.string(), scope_id: z.string(), command: recheckCommandSchema }).strict()
const intents = new Map<string, RecheckCommand>()
const storageKey = (alert: string, scope: string) => `decision-hub:historical-alert-recheck:v1:${RECHECK_OWNER}:${scope}:${alert}`
export function readRecheckIntent(alert: string, scope: string): RecheckCommand | null {
  const key = storageKey(alert, scope)
  if (intents.has(key)) return intents.get(key)!
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const saved = intentSchema.parse(JSON.parse(raw))
    if (saved.owner_id !== RECHECK_OWNER || saved.alert_id !== alert || saved.scope_id !== scope) throw new Error()
    intents.set(key, saved.command); return saved.command
  } catch { throw new Error('本机原补查请求记录无法核对，请保留记录并核对审计；尚不能提交新请求。') }
}
export function saveRecheckIntent(alert: string, scope: string, command: RecheckCommand) {
  const key = storageKey(alert, scope)
  // Persistence must succeed before POST so a refresh can recover an unknown commit.
  try { localStorage.setItem(key, JSON.stringify({ owner_id: RECHECK_OWNER, alert_id: alert, scope_id: scope, command })) }
  catch { throw new Error('本机无法保留补查请求，本次尚未提交。请恢复浏览器存储后再核对条件。') }
  intents.set(key, command)
}

export function replaceUnacceptedRecheckIntent(alert: string, scope: string, old: RecheckCommand, next: RecheckCommand, observedAt: string) {
  const current = readRecheckIntent(alert, scope)
  if (!current || current.request_id !== old.request_id || current.expected_head_hash !== old.expected_head_hash
    || old.expected_head_hash === next.expected_head_hash || old.request_id === next.request_id) throw new Error('本机原补查请求已变化，本次未提交。')
  const key = `${storageKey(alert, scope)}:unaccepted:${old.request_id}`
  // The UI has explicitly verified GET 404 and a different eligible fresh head.
  // Preserve the original key separately before replacing the active intent.
  try {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({
      owner_id: RECHECK_OWNER, alert_id: alert, scope_id: scope, command: old,
      receipt_missing_verified_at: observedAt, replacement_head_hash: next.expected_head_hash,
      basis: 'original_command_GET_404_and_changed_eligible_head',
    }))
  } catch { throw new Error('本机无法保留原未受理请求，本次未提交。') }
  saveRecheckIntent(alert, scope, next)
}
