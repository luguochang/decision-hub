// Exact repository contract; shared dependency links must not select an older ROOT DTO.
import { historyProductStatusV2Schema, historyOwnerSessionPairV2Schema,
  historyOwnerSessionReplyV2Schema, type HistoryProductStatusV2,
  type HistoryOwnerSessionReplyV2 } from '../../../../packages/contracts_ts/src/generated/r2'
import { z } from 'zod'
import { request } from './client'

export type { HistoryProductStatusV2, HistoryOwnerSessionReplyV2 }
export const HISTORY_SCOPES = {
  'crypto:spot:okx:BTC-USDT:trade:1m': '比特币',
  'crypto:spot:okx:ETH-USDT:trade:1m': '以太坊',
} as const

export const historyStatusSchema = historyProductStatusV2Schema.superRefine((status, context) => {
  const scopes = status.data.scopes.map(row => row.scope_id)
  const models = status.models.map(row => row.scope_id)
  if (new Set(scopes).size !== 2 || new Set(models).size !== 2
    || models.some(scope => !(scope in HISTORY_SCOPES))
    || status.data.scopes.some(row => HISTORY_SCOPES[row.scope_id] !== row.asset_label)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '历史状态的资产身份无法核对。' })
  }
  if (status.forecast.scope_id !== null && !(status.forecast.scope_id in HISTORY_SCOPES)
    || status.collection?.jobs.items.some(row => !(row.scope_id in HISTORY_SCOPES))
    || status.excluded_windows.items.some(row => !(row.scope_id in HISTORY_SCOPES))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '预测记录的资产身份无法核对。' })
  }
  const forecast = status.forecast
  if (forecast.state === 'available_unqualified' && (forecast.forecast_id === null
    || forecast.scope_id === null || forecast.cutoff_at === null || forecast.threshold === null
    || forecast.baseline_n === null || forecast.baseline_n < 60
    || forecast.conditional_n === null || forecast.conditional_n < 30 || forecast.probabilities === null)
    || forecast.state !== 'available_unqualified' && forecast.probabilities !== null) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '预测记录的核验状态与内容不一致。' })
  }
  for (const page of [status.collection?.jobs, status.excluded_windows]) {
    if (page && (page.items.length > page.limit || page.items.length > Math.max(0, page.total - page.offset))) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: '历史记录分页无法核对。' })
    }
  }
})

export type HistoryStatusPageQuery = { jobOffset?: number; excludedOffset?: number }
export const historyApi = {
  status: async ({ jobOffset = 0, excludedOffset = 0 }: HistoryStatusPageQuery = {}, signal?: AbortSignal): Promise<HistoryProductStatusV2> => {
    if (!Number.isSafeInteger(jobOffset) || jobOffset < 0 || jobOffset > 1199
      || !Number.isSafeInteger(excludedOffset) || excludedOffset < 0 || excludedOffset > 179) {
      throw new Error('history_status_page_invalid')
    }
    const query = new URLSearchParams({ job_offset: String(jobOffset), job_limit: '30',
      excluded_offset: String(excludedOffset), excluded_limit: '30' })
    const status = historyStatusSchema.parse(await request(`/v1/history/status?${query}`, { credentials: 'include', signal }))
    if (status.collection && (status.collection.jobs.offset !== jobOffset || status.collection.jobs.limit !== 30)
      || status.excluded_windows.offset !== excludedOffset || status.excluded_windows.limit !== 30) {
      throw new Error('history_status_page_response_mismatch')
    }
    return status
  },
  pairOwner: async (pairingToken: string): Promise<HistoryOwnerSessionReplyV2> => {
    const input = historyOwnerSessionPairV2Schema.parse({ pairing_token: pairingToken })
    const session = historyOwnerSessionReplyV2Schema.parse(await request('/v1/history/owner-session', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    }))
    if (Date.parse(session.expires_at) <= Date.now()) throw new Error('history_owner_session_expired')
    return session
  },
}
