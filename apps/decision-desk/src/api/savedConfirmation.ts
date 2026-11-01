import { z } from 'zod'
import { ApiRequestError, requestResponse } from './client'
import { QUALITY_REQUEST_TIMEOUT_MS } from './researchQuality'

const id = z.string().min(1).max(128).refine(value => value === value.trim())
const digest = z.string().regex(/^[a-f0-9]{64}$/)
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER)
const version = count.min(1).max(Number.MAX_SAFE_INTEGER - 1)
const bundleBytes = count.min(1).max(16 * 1024 * 1024)
const materialBytes = count.min(1).max(192 * 1024 * 1024)
const commandId = z.string().regex(/^saved-confirmation:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/)

export const savedConfirmationCommandSchema = z.object({
  schema_version: z.literal('research-saved-confirmation-command.v1'), request_id: commandId, owner_id: id,
  expected_request_id: id, expected_generation: count.min(1), expected_state_version: version,
  expected_bundle_hash: digest, expected_bundle_bytes: bundleBytes,
  expected_material_hash: digest, expected_material_bytes: materialBytes,
  expected_report_hash: digest, expected_report_bytes: bundleBytes, expected_reader_hash: digest,
}).strict()
export function isSavedConfirmationReason(reason: string | null | undefined): boolean {
  return reason === 'publication_execution_compute_budget_exceeded'
    || reason === 'publication_full_frozen_original_changed'
}

export const savedConfirmationStatusSchema = z.object({
  schema_version: z.literal('research-saved-confirmation-status.v1'), run_id: id, request_id: id,
  generation: count, state_version: version,
  state: z.enum(['queued', 'prepared', 'confirmed', 'unconfirmed', 'cancelled', 'revoked']),
  reason_code: z.string().regex(/^[a-z0-9_]{1,160}$/).nullable(), eligible: z.boolean(),
  bundle_hash: digest, bundle_bytes: bundleBytes, material_hash: digest, material_bytes: materialBytes,
  report_hash: digest, report_bytes: bundleBytes, reader_hash: digest,
  saved_count: z.literal(1), ack_count: count.max(1), fenced: z.boolean(),
  owner_lease_present: z.boolean(), authorization_pending: z.boolean(),
}).strict().refine(value => !value.eligible || (value.state === 'unconfirmed' && value.generation >= 1
  && isSavedConfirmationReason(value.reason_code) && value.ack_count === 0
  && !value.fenced && !value.owner_lease_present && !value.authorization_pending))
export type SavedConfirmationStatus = z.infer<typeof savedConfirmationStatusSchema>
const replySchema = z.object({
  schema_version: z.literal('research-saved-confirmation-reply.v1'), run_id: id, request_id: commandId,
  expected_request_id: id, generation: count.min(1), previous_state_version: version,
  authorized_state_version: count.min(2), state: z.literal('unconfirmed'), authorization_status: z.literal('accepted'),
  report_confirmed: z.literal(false), automatic_retry: z.literal(false),
}).strict().refine(value => value.authorized_state_version === value.previous_state_version + 1)
export const savedConfirmationReceiptSchema = z.object({
  schema_version: z.literal('research-saved-confirmation-receipt.v1'), request_id: commandId, owner_id: id, run_id: id,
  command: savedConfirmationCommandSchema, result: replySchema, status: z.literal('accepted'),
  payload_hash: digest, owner_native_sha256: digest, recorded_at: z.string().datetime({ offset: true }),
}).strict().refine(value => value.request_id === value.command.request_id && value.owner_id === value.command.owner_id
  && value.result.run_id === value.run_id && value.result.request_id === value.request_id
  && value.result.expected_request_id === value.command.expected_request_id
  && value.result.generation === value.command.expected_generation
  && value.result.previous_state_version === value.command.expected_state_version
  && value.result.authorized_state_version === value.command.expected_state_version + 1)
export type SavedConfirmationReceipt = z.infer<typeof savedConfirmationReceiptSchema>
const intentSchema = z.object({
  schema_version: z.literal('research-saved-confirmation-intent.v1'), run_id: id, owner_id: id,
  command: savedConfirmationCommandSchema,
}).strict().refine(value => value.owner_id === value.command.owner_id)
export type SavedConfirmationIntent = z.infer<typeof intentSchema>
const storageScope = import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin'
const key = (run: string, owner: string) => `decision-hub.saved-confirmation.v1:${JSON.stringify([storageScope, run, owner])}`

export function readSavedConfirmationIntent(run: string, owner: string): SavedConfirmationIntent | null {
  try {
    const raw = localStorage.getItem(key(run, owner))
    if (raw === null) return null
    const parsed = intentSchema.safeParse(JSON.parse(raw))
    if (!parsed.success || parsed.data.run_id !== run || parsed.data.owner_id !== owner) throw new Error('identity')
    return parsed.data
  } catch { throw new Error('原补确认请求凭据无法读取。请保留现场，当前不能发送新请求。') }
}
function expectedCommand(status: SavedConfirmationStatus, owner: string, requestId: string) {
  return savedConfirmationCommandSchema.parse({
    schema_version: 'research-saved-confirmation-command.v1', request_id: requestId, owner_id: owner,
    expected_request_id: status.request_id, expected_generation: status.generation, expected_state_version: status.state_version,
    expected_bundle_hash: status.bundle_hash, expected_bundle_bytes: status.bundle_bytes,
    expected_material_hash: status.material_hash, expected_material_bytes: status.material_bytes,
    expected_report_hash: status.report_hash, expected_report_bytes: status.report_bytes, expected_reader_hash: status.reader_hash,
  })
}
export function sameSavedConfirmationTarget(intent: SavedConfirmationIntent, status: SavedConfirmationStatus): boolean {
  return intent.run_id === status.run_id
    && JSON.stringify(intent.command) === JSON.stringify(expectedCommand(status, intent.owner_id, intent.command.request_id))
}
export function checkedSavedConfirmationReceipt(raw: unknown, intent: SavedConfirmationIntent,
  posted?: SavedConfirmationReceipt): SavedConfirmationReceipt {
  const value = savedConfirmationReceiptSchema.parse(raw)
  if (value.run_id !== intent.run_id || value.owner_id !== intent.owner_id
    || JSON.stringify(value.command) !== JSON.stringify(intent.command)
    || (posted && JSON.stringify(value) !== JSON.stringify(savedConfirmationReceiptSchema.parse(posted)))) {
    throw new Error('补确认回执与原任务、用户或保存材料不一致。原请求已保留，尚未确认结果。')
  }
  return value
}
export async function saveSavedConfirmationIntent(run: string, owner: string, raw: SavedConfirmationStatus,
  confirmedPrevious?: SavedConfirmationReceipt | null): Promise<SavedConfirmationIntent> {
  const status = savedConfirmationStatusSchema.parse(raw)
  if (status.run_id !== run || !status.eligible) throw new Error('补确认条件已变化，请重新核对。')
  if (!navigator.locks?.request) throw new Error('浏览器暂不能协调多个页面的请求，未发送新请求。原请求仍可读取回执。')
  // Serialize only the immutable locator operation; no HTTP runs inside this lock.
  return navigator.locks.request(key(run, owner), { mode: 'exclusive' }, () => {
    const previous = readSavedConfirmationIntent(run, owner)
    if (previous && sameSavedConfirmationTarget(previous, status)) return previous
    if (previous && !confirmedPrevious) throw new Error('原补确认请求尚未确认，请先核对原回执，未发送新请求。')
    if (previous) checkedSavedConfirmationReceipt(confirmedPrevious, previous)
    const value = intentSchema.parse({ schema_version: 'research-saved-confirmation-intent.v1', run_id: run, owner_id: owner,
      command: expectedCommand(status, owner, `saved-confirmation:${crypto.randomUUID()}`) })
    try {
      if (previous) localStorage.setItem(`${key(run, owner)}:request:${previous.command.request_id}`, JSON.stringify(previous))
      localStorage.setItem(key(run, owner), JSON.stringify(value))
      if (JSON.stringify(readSavedConfirmationIntent(run, owner)) !== JSON.stringify(value)) throw new Error('write_readback')
      return value
    } catch { throw new Error('补确认请求凭据尚未保存成功，未发送请求。请检查浏览器存储后重新核对。') }
  })
}
const base = (run: string) => `/v1/research/runs/${encodeURIComponent(run)}/report/saved-confirmation`
export const savedConfirmationApi = {
  status: async (run: string, owner: string, stateVersion: number, signal?: AbortSignal): Promise<SavedConfirmationStatus> => {
    const value = savedConfirmationStatusSchema.parse(await requestResponse<unknown>(`${base(run)}/status`,
      response => response.json(), { signal, headers: { 'X-Owner-Id': owner } }, undefined,
      { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
    if (value.run_id !== run || value.state_version !== stateVersion) throw new Error('报告状态已变化，请重新读取报告状态后核对。')
    return value
  },
  receipt: async (intent: SavedConfirmationIntent, signal?: AbortSignal): Promise<SavedConfirmationReceipt | null> => {
    try {
      return checkedSavedConfirmationReceipt(await requestResponse<unknown>(
        `${base(intent.run_id)}/commands/${encodeURIComponent(intent.command.request_id)}`, response => response.json(),
        { signal, headers: { 'X-Owner-Id': intent.owner_id } }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent)
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 404 && error.message === 'research_saved_confirmation_receipt_not_found') return null
      throw error
    }
  },
  submit: async (intent: SavedConfirmationIntent, signal?: AbortSignal): Promise<SavedConfirmationReceipt> => checkedSavedConfirmationReceipt(
    await requestResponse<unknown>(`${base(intent.run_id)}/commands`, response => response.json(), {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Owner-Id': intent.owner_id,
        'Idempotency-Key': intent.command.request_id }, body: JSON.stringify(intent.command),
    }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent),
}
