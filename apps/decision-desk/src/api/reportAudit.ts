import { z } from 'zod'
import { researchSessionResultSchema } from '@decision-hub/contracts-ts'
import { ApiRequestError, requestResponse } from './client'
import { QUALITY_REQUEST_TIMEOUT_MS, researchQualitySchema, type ResearchQualityFullAudit } from './researchQuality'

const id = z.string().min(1).max(128).refine(value => value === value.trim())
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const count = z.number().int().positive().max(Number.MAX_SAFE_INTEGER)
const at = z.string().datetime({ offset: true })
const key = z.string().regex(/^report-audit:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/)
export const auditHeadSchema = z.object({ run_id: id, generation: count, state_version: count,
  identity_sha256: hash, bundle_sha256: hash, material_sha256: hash, report_sha256: hash }).strict()
const commandSchema = z.object({ schema_version: z.literal('research-report-audit-request.v1'),
  request_key: key, owner_id: id, expected_head: auditHeadSchema }).strict()
const receiptSchema = z.object({ schema_version: z.literal('research-report-audit-receipt.v1'), audit_id: hash,
  request: commandSchema, request_sha256: hash, accepted_at: at, epoch: hash, status: z.literal('accepted'),
  audit_confirmed: z.literal(false), automatic_retry: z.literal(false) }).strict()
const contextSchema = z.object({ schema_version: z.literal('research-report-audit-context.v1'), head: auditHeadSchema,
  active_audit_id: hash.nullable(), request_allowed: z.boolean(), automatic_retry: z.literal(false) }).strict()
export const auditStateSchema = z.object({ schema_version: z.literal('research-report-audit-state.v1'), receipt: receiptSchema,
  state: z.enum(['accepted', 'running', 'prepared', 'completed', 'failed', 'unknown']), version: count,
  started_at: at.nullable(), deadline_at: at.nullable(), finished_at: at.nullable(), native_exit_observed_at: at.nullable(),
  native_exited: z.boolean(), reason_code: z.string().regex(/^[a-z0-9_]{1,160}$/).nullable(),
  result_sha256: hash.nullable(), result_bytes: count.max(16 * 1024 * 1024).nullable(), audit_confirmed: z.boolean(),
}).strict().refine(value => value.audit_confirmed === (value.state === 'completed')
  && value.native_exited === (value.native_exit_observed_at !== null)
  && ((value.result_sha256 === null) === (value.result_bytes === null))
  && (value.state !== 'completed' || (value.native_exited && value.finished_at !== null
    && value.result_sha256 !== null && value.reason_code === null)))
const resultSchema = z.object({ schema_version: z.literal('research-report-audit-result.v1'), audit_id: hash,
  head: auditHeadSchema, comparison: z.literal('saved_bundle_full_quality'), checked_at: at,
  quality_json: z.string().min(1).max(16 * 1024 * 1024) }).strict()
const replySchema = z.object({ schema_version: z.literal('research-report-audit-result-reply.v1'),
  status: auditStateSchema, result: resultSchema }).strict()
export type AuditIntent = z.infer<typeof commandSchema>
export type AuditState = z.infer<typeof auditStateSchema>
const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const scope = import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin'
const locator = (run: string) => `decision-hub.report-audit.v1:${JSON.stringify([scope, run, owner])}`
const path = (run: string) => `/v1/research/runs/${encodeURIComponent(run)}/report/audits`
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([name, item]) => JSON.stringify(name) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
async function sha(raw: string) {
  if (!crypto.subtle) throw new Error('浏览器尚不能核对完整核验回执，原请求保留。')
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw))))
    .map(value => value.toString(16).padStart(2, '0')).join('')
}
export function readAuditIntent(run: string): AuditIntent | null {
  const raw = localStorage.getItem(locator(run))
  if (raw === null) return null
  const value = commandSchema.parse(JSON.parse(raw))
  if (value.owner_id !== owner || value.expected_head.run_id !== run) throw new Error('原核验请求身份无法核对，请保留现场。')
  return value
}
async function checkedState(raw: unknown, intent: AuditIntent): Promise<AuditState> {
  const value = auditStateSchema.parse(raw)
  if (canonical(value.receipt.request) !== canonical(intent)
    || value.receipt.request_sha256 !== await sha(canonical(intent))
    || value.receipt.audit_id !== await sha(canonical([intent.expected_head.run_id, owner, intent.request_key]))) {
    throw new Error('完整核验回执与原请求不一致，尚未确认结果。')
  }
  return value
}
async function getState(intent: AuditIntent, signal?: AbortSignal): Promise<AuditState | null> {
  try {
    return checkedState(await requestResponse<unknown>(`${path(intent.expected_head.run_id)}/${encodeURIComponent(intent.request_key)}`,
      response => response.json(), { signal, headers: { 'X-Owner-Id': owner } }, undefined,
      { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }), intent)
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404 && error.message === 'research_report_audit_not_found') return null
    throw error
  }
}
export const reportAuditApi = {
  status: getState,
  async begin(run: string, signal?: AbortSignal): Promise<AuditIntent> {
    let previous = readAuditIntent(run)
    const prior = previous ? await getState(previous, signal) : null
    if (previous && prior && prior.state !== 'accepted' && (!['completed', 'failed'].includes(prior.state) || !prior.native_exited)) return previous
    if (previous && prior?.state === 'accepted') {
      const accepted = receiptSchema.parse(await requestResponse<unknown>(path(run), response => response.json(), {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Owner-Id': owner, 'Idempotency-Key': previous.request_key },
        body: JSON.stringify(previous),
      }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
      if (canonical(accepted) !== canonical(prior.receipt)) throw new Error('原核验受理回执已变化，当前不能确认。')
      return previous
    }
    const context = contextSchema.parse(await requestResponse<unknown>(`${path(run)}/context`, response => response.json(),
      { signal }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
    if (context.head.run_id !== run) throw new Error('原报告身份无法核对。')
    if (previous && prior === null && canonical(previous.expected_head) !== canonical(context.head)) {
      throw new Error('原请求尚无回执且报告状态已变化，当前不能发送新键。')
    }
    if (!context.request_allowed) throw new Error('完整核验槽正在使用或旧进程结果待核对，原请求保留。')
    if (!navigator.locks?.request) throw new Error('浏览器暂不能协调核验请求，未发送新请求。')
    const intent = await navigator.locks.request(locator(run), { mode: 'exclusive' }, () => {
      const actual = readAuditIntent(run)
      if (canonical(actual) !== canonical(previous)) throw new Error('另一页面已改变核验请求，请读取原回执。')
      if (actual && prior === null) return actual
      const next = commandSchema.parse({ schema_version: 'research-report-audit-request.v1', owner_id: owner,
        request_key: `report-audit:${crypto.randomUUID()}`, expected_head: context.head })
      if (actual) localStorage.setItem(`${locator(run)}:request:${actual.request_key}`, JSON.stringify(actual))
      localStorage.setItem(locator(run), JSON.stringify(next))
      if (canonical(readAuditIntent(run)) !== canonical(next)) throw new Error('请求键尚未持久保存，未发送请求。')
      return next
    })
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const accepted = receiptSchema.parse(await requestResponse<unknown>(path(run), response => response.json(), {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Owner-Id': owner, 'Idempotency-Key': intent.request_key },
      body: JSON.stringify(intent),
    }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
    if (canonical(accepted.request) !== canonical(intent) || accepted.request_sha256 !== await sha(canonical(intent))) {
      throw new Error('受理回执尚未核对，原请求键保留；受理不代表完整核验通过。')
    }
    return intent
  },
  async result(intent: AuditIntent, signal?: AbortSignal): Promise<ResearchQualityFullAudit> {
    const reply = replySchema.parse(await requestResponse<unknown>(`${path(intent.expected_head.run_id)}/${encodeURIComponent(intent.request_key)}/result`,
      response => response.json(), { signal, headers: { 'X-Owner-Id': owner } }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS }))
    const status = await checkedState(reply.status, intent)
    const raw = canonical(reply.result)
    const view = researchQualitySchema.parse(JSON.parse(reply.result.quality_json))
    if (!status.audit_confirmed || reply.result.audit_id !== status.receipt.audit_id
      || canonical(reply.result.head) !== canonical(intent.expected_head)
      || new TextEncoder().encode(raw).byteLength !== status.result_bytes || await sha(raw) !== status.result_sha256
      || view.run_id !== intent.expected_head.run_id || view.status !== 'available' || !view.decision
      || view.as_of !== reply.result.checked_at || view.decision.full_report_hash !== intent.expected_head.report_sha256) {
      throw new Error('完整核验结果尚未通过原请求与字节核对。')
    }
    researchSessionResultSchema.parse(JSON.parse(view.decision.full_report_json))
    return { view, state_version: intent.expected_head.state_version, comparison: reply.result.comparison, checked_at: reply.result.checked_at }
  },
}
