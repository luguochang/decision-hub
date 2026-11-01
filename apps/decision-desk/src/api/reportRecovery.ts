import { z } from 'zod'
import { ApiRequestError, requestResponse } from './client'
import { QUALITY_REQUEST_TIMEOUT_MS, type ReportRecoveryStatus } from './researchQuality'

const id = z.string().min(1).refine(value => value.trim().length > 0)
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER)
export const reportRecoveryCommandSchema = z.object({
  schema_version: z.literal('research-report-recovery-command.v1'), request_id: id,
  expected_request_id: id, expected_generation: count.min(1), expected_state_version: count.min(1),
}).strict()
export type ReportRecoveryCommand = z.infer<typeof reportRecoveryCommandSchema>
export const reportRecoveryReceiptSchema = z.object({
  schema_version: z.literal('research-report-recovery-receipt.v1'), request_id: id, owner_id: id, run_id: id,
  command: reportRecoveryCommandSchema,
  result: z.object({
    schema_version: z.literal('research-report-recovery-reply.v1'), run_id: id, request_id: id,
    generation: count.min(1), state_version: count.min(1), state: z.literal('queued'),
    report_confirmed: z.literal(false), source_abi: z.literal('publication-source-snapshot-read.v2'),
    automatic_retry: z.literal(false),
  }).strict(),
  status: z.enum(['accepted', 'unconfirmed']), payload_hash: z.string().regex(/^[a-f0-9]{64}$/),
  recorded_at: z.string().datetime({ offset: true }),
}).strict()
export type ReportRecoveryReceipt = z.infer<typeof reportRecoveryReceiptSchema>
const intentSchema = z.object({
  schema_version: z.literal('research-report-recovery-intent.v1'), run_id: id, owner_id: id,
  command: reportRecoveryCommandSchema,
}).strict()
export type ReportRecoveryIntent = z.infer<typeof intentSchema>
const storageScope = import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin'
const key = (run: string, owner: string) => `decision-hub.report-recovery.v1:${JSON.stringify([storageScope, run, owner])}`

// The browser retains an immutable request, never a trusted server receipt.
export function readRecoveryIntent(run: string, owner: string): ReportRecoveryIntent | null {
  try {
    const raw = localStorage.getItem(key(run, owner))
    if (raw === null) return null
    const value = intentSchema.parse(JSON.parse(raw))
    if (value.run_id !== run || value.owner_id !== owner) throw new Error('identity')
    return value
  } catch { throw new Error('原恢复请求凭据无法读取。请保留现场，当前不能发送新恢复请求。') }
}

export async function saveRecoveryIntent(run: string, owner: string, status: ReportRecoveryStatus,
  confirmedPrevious?: ReportRecoveryReceipt | null): Promise<ReportRecoveryIntent> {
  if (status.run_id !== run || status.action !== 'owner_request_required' || (status.state !== 'cancelled' && !(status.state === 'unconfirmed' && status.reason_code === 'frozen_io_root_data_fallback'))
    || status.source_abi !== 'publication-source-snapshot-read.v2') throw new Error('恢复条件已变化，请重新核对。')
  if (!navigator.locks?.request) throw new Error('浏览器暂不能协调多个页面的恢复请求，未发送请求。已保留的原请求仍可读取回执。')
  // Same-origin tabs serialize only the short locator operation, never HTTP.
  return navigator.locks.request(key(run, owner), { mode: 'exclusive' }, () => {
    const previous = readRecoveryIntent(run, owner)
    if (previous) {
      if (previous.command.expected_request_id === status.request_id
        && previous.command.expected_generation === status.generation
        && previous.command.expected_state_version === status.state_version) return previous
      if (!confirmedPrevious || checkedRecoveryReceipt(confirmedPrevious, previous).status !== 'accepted') {
        throw new Error('另一页面的原恢复请求尚未确认，请先核对原回执，未发送新请求。')
      }
    }
    const value = intentSchema.parse({ schema_version: 'research-report-recovery-intent.v1', run_id: run,
      owner_id: owner, command: { schema_version: 'research-report-recovery-command.v1',
        request_id: `report-recovery:${crypto.randomUUID()}`, expected_request_id: status.request_id,
        expected_generation: status.generation, expected_state_version: status.state_version } })
    try {
      if (previous) localStorage.setItem(`${key(run, owner)}:request:${previous.command.request_id}`, JSON.stringify(previous))
      localStorage.setItem(key(run, owner), JSON.stringify(value))
      if (JSON.stringify(readRecoveryIntent(run, owner)) !== JSON.stringify(value)) throw new Error('write_readback')
      return value
    } catch { throw new Error('原恢复请求凭据尚未保存成功，未发送请求。请检查浏览器存储后重新核对。') }
  })
}

export function checkedRecoveryReceipt(raw: unknown, intent: ReportRecoveryIntent, posted?: ReportRecoveryReceipt): ReportRecoveryReceipt {
  const value = reportRecoveryReceiptSchema.parse(raw)
  const command = intent.command
  if (value.run_id !== intent.run_id || value.owner_id !== intent.owner_id || value.request_id !== command.request_id
    || JSON.stringify(value.command) !== JSON.stringify(command) || value.result.run_id !== intent.run_id
    || value.result.request_id !== command.expected_request_id || value.result.generation !== command.expected_generation + 1
    || value.result.state_version !== command.expected_state_version + 1
    || (posted && (value.payload_hash !== posted.payload_hash || value.recorded_at !== posted.recorded_at
      || JSON.stringify(value.result) !== JSON.stringify(posted.result)))) {
    throw new Error('恢复回执与原任务、用户或原请求不一致。原请求已保留，尚未确认恢复结果。')
  }
  return value
}
const path = (intent: ReportRecoveryIntent) => `/v1/research/runs/${encodeURIComponent(intent.run_id)}/report/recovery/commands`
export const reportRecoveryApi = {
  receipt: async (intent: ReportRecoveryIntent, signal?: AbortSignal): Promise<ReportRecoveryReceipt | null> => {
    try {
      return checkedRecoveryReceipt(await requestResponse<unknown>(`${path(intent)}/${encodeURIComponent(intent.command.request_id)}`,
        response => response.json(), { signal, headers: { 'X-Owner-Id': intent.owner_id } }), intent)
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 404 && error.message === 'research_report_recovery_receipt_not_found') return null
      throw error
    }
  },
  submit: async (intent: ReportRecoveryIntent, signal?: AbortSignal): Promise<ReportRecoveryReceipt> => checkedRecoveryReceipt(
    await requestResponse<unknown>(path(intent), response => response.json(), {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Owner-Id': intent.owner_id,
        'Idempotency-Key': intent.command.request_id }, body: JSON.stringify(intent.command),
    }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent),
}

// Independent scheduled no-Saved recovery. Original V1 contract remains exact.
const preparationId79 = z.string().min(1).max(128).refine(value => value === value.trim())
const preparationDigest79 = z.string().regex(/^[a-f0-9]{64}$/)
const preparationCount79 = z.number().int().positive().max(Number.MAX_SAFE_INTEGER - 1)
const preparationKey79 = z.string().regex(/^preparation-recovery:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/)
export const preparationRecoveryStatusSchema = z.object({
  schema_version: z.literal('research-preparation-recovery-status.v2'), run_id: preparationId79,
  request_id: preparationId79, plan_id: preparationId79, generation: preparationCount79, state_version: preparationCount79,
  state: z.enum(['queued', 'prepared', 'confirmed', 'unconfirmed', 'cancelled', 'revoked']),
  reason_code: z.string().regex(/^[a-z0-9_]{1,160}$/).nullable(), eligible: z.boolean(),
  native_sha256: preparationDigest79.nullable(), owner_native_sha256: preparationDigest79,
  source_abi: z.literal('publication-source-snapshot-read.v2').nullable(), proof_scope: z.literal('metadata_prechecks_only'),
  automatic_retry: z.literal(false),
}).strict().refine(value => !value.eligible || (value.state === 'unconfirmed'
  && value.reason_code === 'frozen_io_undeclared_authorization_resource' && value.native_sha256 !== null && value.source_abi !== null))
export type PreparationRecoveryStatus = z.infer<typeof preparationRecoveryStatusSchema>
const preparationCommandSchema79 = z.object({
  schema_version: z.literal('research-preparation-recovery-command.v2'), request_id: preparationKey79, owner_id: preparationId79,
  expected_request_id: preparationId79, expected_plan_id: preparationId79, expected_generation: preparationCount79,
  expected_state_version: preparationCount79, expected_reason: z.literal('frozen_io_undeclared_authorization_resource'),
  expected_native_sha256: preparationDigest79, expected_owner_native_sha256: preparationDigest79,
}).strict()
const preparationReplySchema79 = z.object({
  schema_version: z.literal('research-preparation-recovery-reply.v2'), run_id: preparationId79, request_id: preparationId79,
  generation: preparationCount79, state_version: preparationCount79, state: z.literal('queued'), report_confirmed: z.literal(false),
  source_abi: z.literal('publication-source-snapshot-read.v2'), automatic_retry: z.literal(false),
  recovery_mode: z.enum(['diagnostic_gap_all_owned_archives.v1', 'scheduled_preparation_queue_only_snapshot_ownership.v1']), event_history_sha256: preparationDigest79,
  archive_ownership_sha256: preparationDigest79,
}).strict()
export const preparationRecoveryReceiptSchema = z.object({
  schema_version: z.literal('research-preparation-recovery-receipt.v2'), request_id: preparationKey79,
  owner_id: preparationId79, run_id: preparationId79, plan_id: preparationId79, command: preparationCommandSchema79,
  result: preparationReplySchema79, status: z.literal('accepted'), payload_hash: preparationDigest79,
  recorded_at: z.string().datetime({ offset: true }),
}).strict().refine(value => value.request_id === value.command.request_id && value.owner_id === value.command.owner_id
  && value.plan_id === value.command.expected_plan_id && value.result.run_id === value.run_id
  && value.result.request_id === value.command.expected_request_id
  && value.result.generation === value.command.expected_generation + 1 && value.result.state_version === value.command.expected_state_version + 1)
export type PreparationRecoveryReceipt = z.infer<typeof preparationRecoveryReceiptSchema>
const preparationIntentSchema79 = z.object({ schema_version: z.literal('research-preparation-recovery-intent.v2'),
  run_id: preparationId79, owner_id: preparationId79, command: preparationCommandSchema79 }).strict()
  .refine(value => value.owner_id === value.command.owner_id)
export type PreparationRecoveryIntent = z.infer<typeof preparationIntentSchema79>
const preparationLocator79 = (run: string, owner: string) => `decision-hub.preparation-recovery.v2:${JSON.stringify([storageScope, run, owner])}`
export function readPreparationRecoveryIntent(run: string, owner: string): PreparationRecoveryIntent | null {
  try {
    const raw = localStorage.getItem(preparationLocator79(run, owner))
    if (raw === null) return null
    const value = preparationIntentSchema79.parse(JSON.parse(raw))
    if (value.run_id !== run || value.owner_id !== owner) throw new Error('identity')
    return value
  } catch { throw new Error('原计划报告准备恢复请求无法读取，请保留现场；未发送新请求。') }
}
function preparationCommand79(status: PreparationRecoveryStatus, owner: string, request: string) {
  return preparationCommandSchema79.parse({ schema_version: 'research-preparation-recovery-command.v2', request_id: request, owner_id: owner,
    expected_request_id: status.request_id, expected_plan_id: status.plan_id, expected_generation: status.generation,
    expected_state_version: status.state_version, expected_reason: status.reason_code,
    expected_native_sha256: status.native_sha256, expected_owner_native_sha256: status.owner_native_sha256 })
}
export function samePreparationRecoveryTarget(intent: PreparationRecoveryIntent, status: PreparationRecoveryStatus): boolean {
  return status.eligible && intent.run_id === status.run_id
    && JSON.stringify(intent.command) === JSON.stringify(preparationCommand79(status, intent.owner_id, intent.command.request_id))
}
export function checkedPreparationRecoveryReceipt(raw: unknown, intent: PreparationRecoveryIntent,
  posted?: PreparationRecoveryReceipt): PreparationRecoveryReceipt {
  const value = preparationRecoveryReceiptSchema.parse(raw)
  if (value.run_id !== intent.run_id || value.owner_id !== intent.owner_id || JSON.stringify(value.command) !== JSON.stringify(intent.command)
    || (posted && JSON.stringify(value) !== JSON.stringify(preparationRecoveryReceiptSchema.parse(posted)))) {
    throw new Error('计划报告准备恢复回执与原Run、计划、拥有者或请求不一致，原键保留。')
  }
  return value
}
export async function savePreparationRecoveryIntent(run: string, owner: string, raw: PreparationRecoveryStatus,
  previousReceipt?: PreparationRecoveryReceipt | null, notFoundIntent?: PreparationRecoveryIntent | null): Promise<PreparationRecoveryIntent> {
  const status = preparationRecoveryStatusSchema.parse(raw)
  if (status.run_id !== run || !status.eligible) throw new Error('准备恢复条件已变化，请重新核对。')
  if (!navigator.locks?.request) throw new Error('浏览器暂不能协调多个页面，未发送新请求。原请求仍可读取。')
  return navigator.locks.request(preparationLocator79(run, owner), { mode: 'exclusive' }, () => {
    const previous = readPreparationRecoveryIntent(run, owner)
    if (previous && samePreparationRecoveryTarget(previous, status)) return previous
    if (previous && !previousReceipt && (!notFoundIntent || JSON.stringify(notFoundIntent) !== JSON.stringify(previous))) {
      throw new Error('原准备恢复请求尚未确认，不能发送另一键。')
    }
    if (previous && previousReceipt) checkedPreparationRecoveryReceipt(previousReceipt, previous)
    const value = preparationIntentSchema79.parse({ schema_version: 'research-preparation-recovery-intent.v2', run_id: run, owner_id: owner,
      command: preparationCommand79(status, owner, `preparation-recovery:${crypto.randomUUID()}`) })
    if (previous) localStorage.setItem(`${preparationLocator79(run, owner)}:request:${previous.command.request_id}`, JSON.stringify(previous))
    localStorage.setItem(preparationLocator79(run, owner), JSON.stringify(value))
    if (JSON.stringify(readPreparationRecoveryIntent(run, owner)) !== JSON.stringify(value)) throw new Error('请求键尚未保存，未发送请求。')
    return value
  })
}
const preparationPath79 = (run: string) => `/v1/research/runs/${encodeURIComponent(run)}/report/preparation-recovery`
export const preparationRecoveryApi = {
  status: async (run: string, owner: string, signal?: AbortSignal): Promise<PreparationRecoveryStatus> => {
    const value = preparationRecoveryStatusSchema.parse(await requestResponse<unknown>(`${preparationPath79(run)}/status`,
      response => response.json(), { signal, headers: { 'X-Owner-Id': owner } }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
    if (value.run_id !== run) throw new Error('计划报告准备恢复状态身份不一致。')
    return value
  },
  receipt: async (intent: PreparationRecoveryIntent, signal?: AbortSignal): Promise<PreparationRecoveryReceipt | null> => {
    try {
      return checkedPreparationRecoveryReceipt(await requestResponse<unknown>(`${preparationPath79(intent.run_id)}/commands/${encodeURIComponent(intent.command.request_id)}`,
        response => response.json(), { signal, headers: { 'X-Owner-Id': intent.owner_id } }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent)
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 404 && error.message === 'research_preparation_recovery_receipt_not_found') return null
      throw error
    }
  },
  submit: async (intent: PreparationRecoveryIntent, signal?: AbortSignal): Promise<PreparationRecoveryReceipt> => checkedPreparationRecoveryReceipt(
    await requestResponse<unknown>(`${preparationPath79(intent.run_id)}/commands`, response => response.json(), {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Owner-Id': intent.owner_id, 'Idempotency-Key': intent.command.request_id },
      body: JSON.stringify(intent.command),
    }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent),
}
