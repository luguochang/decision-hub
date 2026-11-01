import { feedbackSchema, readCommandReceiptSchema, readReceiptViewSchema, reportDeliveryPageSchema, revisionFeedbackEvaluationSchema, revisionFeedbackEvaluationBasisSchema, studyTrackingReceiptSchema, type ReadCommandReceipt, type RevisionFeedbackEvaluation } from '@decision-hub/contracts-ts'
import { request, requestResponse } from './client'

export const productOwner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const ownerHeaders = { 'X-Owner-Id': productOwner }
export type RevisionReadRequest = { revision: string; read: boolean; id: string; owner: string }
export type StudyTrackingRequest = { owner: string; study: string; id: string; expected: 'active' | 'paused'; status: 'active' | 'paused' }
export function checkedStudyTrackingReceipt(value: unknown, submitted: StudyTrackingRequest) {
  const parsed = studyTrackingReceiptSchema.safeParse(value)
  if (!parsed.success) throw new Error('自动复查操作回执格式不完整，原请求尚未确认。')
  const receipt = parsed.data
  if (submitted.owner !== productOwner || receipt.request_id !== submitted.id || receipt.study_id !== submitted.study
    || receipt.previous_status !== submitted.expected || receipt.status !== submitted.status) {
    throw new Error('自动复查操作回执与原请求不一致，尚未确认。')
  }
  return receipt
}
export function checkedRevisionReadReceipt(value: unknown, submitted: RevisionReadRequest) {
  const parsed = readReceiptViewSchema.safeParse(value)
  if (!parsed.success) throw new Error('阅读标记回执格式不完整，保存尚未确认。请核对原请求回执。')
  const receipt = parsed.data
  if (receipt.revision_id !== submitted.revision || receipt.owner_id !== submitted.owner
    || (receipt.read_at !== null) !== submitted.read) {
    throw new Error('阅读标记回执与原请求不一致，保存尚未确认。请核对原请求回执。')
  }
  // This old DTO does not carry a request identity. It is not sufficient to
  // acknowledge or remove a persisted command.
  return receipt
}

export async function checkedReadCommandReceipt(value: unknown, submitted: RevisionReadRequest): Promise<ReadCommandReceipt | null> {
  if (value === null) return null
  const parsed = readCommandReceiptSchema.safeParse(value)
  if (!parsed.success) throw new Error('原阅读请求回执格式不完整，保存尚未确认。')
  const receipt = parsed.data
  if (receipt.request_id !== submitted.id || receipt.revision_id !== submitted.revision
    || receipt.owner_id !== submitted.owner || submitted.owner !== productOwner
    || receipt.command.request_id !== submitted.id || receipt.command.read !== submitted.read
    || receipt.confirmation_basis !== 'stored_command_hash') {
    throw new Error('原阅读请求回执与冻结意图不一致，保存尚未确认。')
  }
  checkedRevisionReadReceipt(receipt.result, submitted)
  if (!globalThis.crypto?.subtle) throw new Error('当前浏览器无法核对原请求指纹，保存尚未确认。')
  // Match ProductDeliveryService._fingerprint: UTF-8 JSON, sorted object keys,
  // compact separators. The command has only read and request_id, in that order.
  const body = JSON.stringify([submitted.owner, submitted.revision, 'read_receipt', { read: submitted.read, request_id: submitted.id }])
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body))
  const expected = [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('')
  if (receipt.payload_hash !== expected) throw new Error('原阅读请求回执指纹无法核对，保存尚未确认。')
  return receipt
}
export type RevisionFeedbackRequest = { revision: string; verdict: 'useful' | 'not_useful' | 'incorrect' | 'needs_review'; notes: string; id: string; owner: string }
export function checkedRevisionFeedbackReceipt(value: unknown, submitted: RevisionFeedbackRequest) {
  const parsed = feedbackSchema.safeParse(value)
  if (!parsed.success) throw new Error('反馈回执格式不完整，保存尚未确认。请核对原请求回执。')
  const receipt = parsed.data
  if (submitted.owner !== productOwner || receipt.request_id !== submitted.id || receipt.target_type !== 'report_revision'
    || receipt.target_id !== submitted.revision || receipt.created_by !== submitted.owner
    || receipt.verdict !== submitted.verdict || receipt.notes !== submitted.notes) {
    throw new Error('反馈回执与原请求不一致，保存尚未确认。请核对原请求回执。')
  }
  return receipt
}

function checkedFeedbackEvaluation(value: unknown, revision: string, sourceRunId?: string, submitted?: RevisionFeedbackRequest): RevisionFeedbackEvaluation {
  const parsed = revisionFeedbackEvaluationSchema.safeParse(value)
  if (!parsed.success) throw new Error('当时评估记录格式不完整，原反馈仍按其保存回执确认。')
  const receipt = parsed.data
  if (receipt.status === 'available' && (!receipt.binding || !receipt.value
    || receipt.binding.source_run_id !== receipt.value.run_id
    || receipt.binding.artifact_id !== receipt.value.artifact_id
    || receipt.binding.evaluation_version !== receipt.value.evaluation_version)) {
    throw new Error('当时评估记录内部的原件关联无法核对。')
  }
  if (receipt.owner_id !== productOwner || receipt.revision_id !== revision
    || receipt.feedback.created_by !== productOwner || receipt.feedback.target_type !== 'report_revision'
    || receipt.feedback.target_id !== revision
    || (sourceRunId && receipt.binding?.source_run_id && receipt.binding.source_run_id !== sourceRunId)
    || (receipt.status === 'available' && sourceRunId && receipt.value?.run_id !== sourceRunId)) {
    throw new Error('当时评估记录与这一版报告或所属用户不一致。')
  }
  if (submitted) checkedRevisionFeedbackReceipt(receipt.feedback, submitted)
  return receipt
}
function command<T extends { request_id: string }>(path: string, payload: T) {
  return request(path, { method: 'POST', headers: { ...ownerHeaders, 'Content-Type': 'application/json', 'Idempotency-Key': payload.request_id }, body: JSON.stringify(payload) })
}
export const delivery = {
  reports: async (options: { offset?: number; unread?: boolean; run?: string }) => {
    const params = new URLSearchParams({ limit: '12', offset: String(options.offset ?? 0), unread_only: String(Boolean(options.unread)) })
    if (options.run) params.set('run_id', options.run)
    return reportDeliveryPageSchema.parse(await request(`/v1/product/reports?${params}`, { headers: ownerHeaders }))
  },
  read: async (revision: string, read: boolean, requestId: string) => checkedRevisionReadReceipt(await command(`/v1/product/reports/${encodeURIComponent(revision)}/read`, { request_id: requestId, read }), { revision, read, id: requestId, owner: productOwner }),
  readCommandReceipt: async (submitted: RevisionReadRequest) => {
    if (submitted.owner !== productOwner) throw new Error('阅读凭据所属用户无法核对，尚未发送请求。')
    const path = `/v1/product/reports/${encodeURIComponent(submitted.revision)}/read/commands/${encodeURIComponent(submitted.id)}?read=${submitted.read ? 'true' : 'false'}`
    // Only a successful 200/null is absent. HTTP 404 and other errors propagate.
    return checkedReadCommandReceipt(await request(path, { headers: ownerHeaders }, 200), submitted)
  },
  tracking: async (study: string, status: 'active' | 'paused', expected: 'active' | 'paused', requestId: string) => checkedStudyTrackingReceipt(await command(`/v1/studies/${encodeURIComponent(study)}/tracking`, { request_id: requestId, status, expected_status: expected }), { owner: productOwner, study, id: requestId, expected, status }),
  trackingCommandReceipt: async (submitted: StudyTrackingRequest) => {
    if (submitted.owner !== productOwner) throw new Error('自动复查凭据所属用户无法核对，尚未发送请求。')
    return requestResponse<ReturnType<typeof checkedStudyTrackingReceipt> | null>(`/v1/studies/${encodeURIComponent(submitted.study)}/tracking-commands/${encodeURIComponent(submitted.id)}`, async response => {
      if (response.status !== 200) throw new Error('自动复查回执响应状态无法核对，原请求尚未确认。')
      return checkedStudyTrackingReceipt(await response.json(), submitted)
    }, { headers: ownerHeaders }, (status, detail) => {
      if (status === 404 && detail === 'study_tracking_command_not_found') return null
      throw new Error(detail)
    })
  },
  feedback: async (revision: string, verdict: RevisionFeedbackRequest['verdict'], notes: string, requestId: string) => checkedRevisionFeedbackReceipt(await command('/v1/workbench/feedback', { request_id: requestId, target_type: 'report_revision', target_id: revision, created_by: productOwner, verdict, notes }), { revision, verdict, notes, id: requestId, owner: productOwner }),
  feedbackEvaluation: async (submitted: RevisionFeedbackRequest, sourceRunId?: string) => {
    if (submitted.owner !== productOwner) throw new Error('反馈凭据所属用户无法核对，尚未发送请求。')
    const query = new URLSearchParams({ request_id: submitted.id })
    const value = await request(`/v1/product/reports/${encodeURIComponent(submitted.revision)}/feedback-evaluation/requests?${query}`, { headers: ownerHeaders }, 200)
    return checkedFeedbackEvaluation(value, submitted.revision, sourceRunId, submitted)
  },
  latestFeedbackEvaluation: async (revision: string, sourceRunId?: string) => {
    const value = await request(`/v1/product/reports/${encodeURIComponent(revision)}/feedback-evaluation`, { headers: ownerHeaders }, 200)
    return value === null ? null : checkedFeedbackEvaluation(value, revision, sourceRunId)
  },
  feedbackEvaluationBasis: async (receipt: RevisionFeedbackEvaluation) => {
    const checked = checkedFeedbackEvaluation(receipt, receipt.revision_id)
    const binding = checked.binding
    if (!binding) throw new Error('这笔历史反馈未记录当时评估依据。')
    const query = new URLSearchParams({ request_id: checked.feedback.request_id })
    const value = await request(`/v1/product/reports/${encodeURIComponent(checked.revision_id)}/feedback-evaluation/basis?${query}`, { headers: ownerHeaders }, 200)
    const parsed = revisionFeedbackEvaluationBasisSchema.safeParse(value)
    if (!parsed.success) throw new Error('当时评估依据格式不完整。')
    const basis = parsed.data
    if (basis.owner_id !== productOwner || basis.revision_id !== checked.revision_id
      || basis.request_id !== checked.feedback.request_id || basis.binding_id !== binding.binding_id
      || basis.basis_ref !== binding.basis_ref || basis.basis_hash !== binding.basis_hash) {
      throw new Error('当时评估依据与这笔原反馈不一致。')
    }
    return basis
  },
}
