import { studyEffectTrackingReceiptSchema, studyEffectTrackingRequestStatusSchema, studyEffectTrackingStatusSchema, type StudyEffectTrackingReceipt, type StudyEffectTrackingRequestStatus } from '@decision-hub/contracts-ts'
import { request, requestResponse } from './client'
import { productOwner } from './delivery'

export type EffectTrackingIntent = { owner: string; study: string; revision: string; run: string; artifact: string; id: string }
export type EffectTrackingRequestRead = { kind: 'request'; request: StudyEffectTrackingRequestStatus } | { kind: 'receipt'; receipt: StudyEffectTrackingReceipt } | { kind: 'absent' }
export type EffectTrackingRejectionCode = 'effect_tracking_plan_already_registered' | 'context_selection_implementation_unavailable'
export class EffectTrackingAdmissionRejection extends Error {
  readonly intent: Readonly<EffectTrackingIntent>
  constructor(readonly code: EffectTrackingRejectionCode, intent: EffectTrackingIntent) {
    super(code)
    this.name = 'EffectTrackingAdmissionRejection'
    this.intent = Object.freeze({ ...intent })
  }
}
const headers = { 'X-Owner-Id': productOwner }
const path = (study: string) => `/v1/studies/${encodeURIComponent(study)}/effect-tracking`
const horizons = ['30m', '24h', '72h']

function validReceipt(receipt: StudyEffectTrackingReceipt) {
  const windows = [...receipt.plans.map(plan => plan.horizon), ...receipt.missing_windows.map(window => window.horizon)]
  return windows.length === 3 && new Set(windows).size === 3 && horizons.every(value => windows.includes(value as '30m' | '24h' | '72h'))
    && new Set(receipt.plans.map(plan => plan.job_id)).size === receipt.plans.length
    && new Set(receipt.plans.map(plan => plan.outlook_id)).size === receipt.plans.length
    && new Set(receipt.plans.map(plan => plan.producer_request_id)).size === receipt.plans.length
    && Date.parse(receipt.start_at) === Date.parse(receipt.recorded_at)
    && Date.parse(receipt.end_at) - Date.parse(receipt.start_at) === 90 * 86_400_000
}

export function checkedEffectReceipt(value: unknown, intent: EffectTrackingIntent): StudyEffectTrackingReceipt {
  const parsed = studyEffectTrackingReceiptSchema.safeParse(value)
  if (!parsed.success) throw new Error('效果跟踪回执不完整，操作尚未确认。')
  const receipt = parsed.data
  if (intent.owner !== productOwner || receipt.owner_id !== intent.owner || receipt.study_id !== intent.study
    || receipt.revision_id !== intent.revision || receipt.source_run_id !== intent.run
    || receipt.artifact_id !== intent.artifact || receipt.request_id !== intent.id || !validReceipt(receipt)) {
    throw new Error('效果跟踪回执与这一版报告不一致，操作尚未确认。')
  }
  return receipt
}

export function checkedEffectRequestStatus(value: unknown, intent: EffectTrackingIntent): StudyEffectTrackingRequestStatus {
  const parsed = studyEffectTrackingRequestStatusSchema.safeParse(value)
  if (!parsed.success) throw new Error('效果跟踪受理记录不完整，原请求状态尚未确认。')
  const status = parsed.data
  if (intent.owner !== productOwner || status.owner_id !== intent.owner || status.study_id !== intent.study
    || status.revision_id !== intent.revision || status.source_run_id !== intent.run
    || status.artifact_id !== intent.artifact || status.request_id !== intent.id
    || Date.parse(status.updated_at) < Date.parse(status.accepted_at)
    || (status.state === 'completed') !== (status.final_receipt !== null)
    || (status.can_retry && status.state !== 'failed')
    || (status.state === 'failed' && status.error_code === null)) {
    throw new Error('效果跟踪受理记录与原请求不一致，操作尚未确认。')
  }
  if (status.final_receipt !== null) {
    const receipt = checkedEffectReceipt(status.final_receipt, intent)
    if (Date.parse(receipt.recorded_at) < Date.parse(status.accepted_at)
      || Date.parse(receipt.recorded_at) > Date.parse(status.updated_at)) {
      throw new Error('效果跟踪登记时间与原受理记录不一致，操作尚未确认。')
    }
  }
  return status
}

export const effectTracking = {
  start: async (intent: EffectTrackingIntent) => requestResponse(`${path(intent.study)}/requests`,
    async response => {
      const status = checkedEffectRequestStatus(await response.json(), intent)
      if (!((response.status === 200 && status.state === 'completed')
        || (response.status === 202 && status.state !== 'completed'))) {
        throw new Error('效果跟踪受理响应状态无法核对，原请求仍需确认。')
      }
      return status
    }, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json', 'Idempotency-Key': intent.id },
    body: JSON.stringify({ request_id: intent.id, revision_id: intent.revision }),
  }, (status, detail) => {
    if (status === 409 && (detail === 'effect_tracking_plan_already_registered' || detail === 'context_selection_implementation_unavailable')) {
      throw new EffectTrackingAdmissionRejection(detail, intent)
    }
    throw new Error(detail)
  }),
  requestStatus: async (intent: EffectTrackingIntent) => requestResponse<StudyEffectTrackingRequestStatus | null>(`${path(intent.study)}/requests/${encodeURIComponent(intent.id)}`,
    async response => {
      if (response.status !== 200) throw new Error('原受理记录响应状态无法核对。')
      return checkedEffectRequestStatus(await response.json(), intent)
    }, { headers }, (status, detail) => {
      if (status === 404 && detail === 'effect_tracking_request_not_found') return null
      throw new Error(detail)
    }),
  receipt: async (intent: EffectTrackingIntent) => requestResponse(`${path(intent.study)}/commands/${encodeURIComponent(intent.id)}`,
    async response => checkedEffectReceipt(await response.json(), intent), { headers },
    (status, detail) => {
      if (status === 404 && detail === 'effect_tracking_command_not_found') return null
      throw new Error(detail)
    }),
  read: async (intent: EffectTrackingIntent): Promise<EffectTrackingRequestRead> => {
    const status = await effectTracking.requestStatus(intent)
    if (status !== null) return { kind: 'request', request: status }
    // A missing new request is not a missing old final ACK. Read that original
    // endpoint separately so existing v1 browser journals remain recoverable.
    const receipt = await effectTracking.receipt(intent)
    return receipt === null ? { kind: 'absent' } : { kind: 'receipt', receipt }
  },
  status: async (study: string) => {
    const result = studyEffectTrackingStatusSchema.parse(await request<unknown>(path(study), { headers }))
    if (result.study_id !== study || result.owner_id !== productOwner) throw new Error('跟踪进度与当前主题不一致，请重新读取。')
    for (const group of result.groups) {
      if (group.receipt.owner_id !== productOwner || group.receipt.study_id !== study || !validReceipt(group.receipt)
        || group.windows.length !== group.receipt.plans.length
        || new Set(group.windows.map(window => window.plan.job_id)).size !== group.windows.length
        || group.windows.some(window => !group.receipt.plans.some(plan => JSON.stringify(plan) === JSON.stringify(window.plan)))
        || group.windows.some(window => (window.production_id === null) !== (window.production_state === null)
          || (window.production_id === null) !== (window.captured_count === null)
          || (window.production_id === null) !== (window.effective_count === null))) {
        throw new Error('跟踪进度中的原计划无法核对，请重新读取。')
      }
    }
    return result
  },
}
