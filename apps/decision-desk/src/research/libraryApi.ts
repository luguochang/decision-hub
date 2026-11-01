import { request } from '../api/client'
import { analystClaimSchema, researchDocumentVersionSchema } from '../api/schemas'

const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'

export const libraryCommands = {
  recover: async (versionId: string, originalFileHash: string, requestId: string) => {
    const result = researchDocumentVersionSchema.parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(versionId)}/recover`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': owner, 'Idempotency-Key': requestId },
      body: JSON.stringify({ schema_version: 'document-recovery-command.v1', request_id: requestId, original_file_hash: originalFileHash }),
    }))
    if (result.supersedes_version_id !== versionId || result.version_id === versionId || result.original_file_hash !== originalFileHash) throw new Error('重新处理回执与这份原文不匹配，尚未确认结果。')
    return result
  },
  reviewClaim: async (claimId: string, decision: 'accept' | 'reject', reason: string, requestId: string) => {
    const result = analystClaimSchema.parse(await request<unknown>(`/v1/research-library/claims/${encodeURIComponent(claimId)}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': owner, 'Idempotency-Key': requestId },
      body: JSON.stringify({ schema_version: 'claim-review-command.v1', request_id: requestId, decision, reason }),
    }))
    if (result.claim_id !== claimId || result.status !== (decision === 'accept' ? 'accepted' : 'rejected')) throw new Error('审核回执与这条观点不匹配，尚未确认结果。')
    return result
  },
}
