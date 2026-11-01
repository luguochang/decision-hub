import { z } from 'zod'
import { riskResearchOwnerReceiptSchema } from '@decision-hub/contracts-ts'
import { request } from '../api/client'

export const researchOwner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const identity = z.object({ version: z.literal(1), ownerId: z.string().min(1), scopeId: z.string().min(1),
  commandId: z.string().min(1).max(128), canonicalRequestId: z.string().min(1).nullable() }).strict()
export type ResearchFollowup = z.infer<typeof identity>
export const followupKey = (scopeId: string) => `decision-hub:research-followup:v1:${researchOwner}:${scopeId}`

export function loadFollowup(scopeId: string): ResearchFollowup | null {
  try {
    const value = identity.safeParse(JSON.parse(localStorage.getItem(followupKey(scopeId)) || 'null'))
    return value.success && value.data.ownerId === researchOwner && value.data.scopeId === scopeId ? value.data : null
  } catch { return null }
}
export function saveFollowup(value: ResearchFollowup) {
  const saved = identity.parse(value)
  if (saved.ownerId !== researchOwner) throw new Error('研究操作的使用者身份无法核对。')
  localStorage.setItem(followupKey(saved.scopeId), JSON.stringify(saved))
}
export async function readFollowup(value: ResearchFollowup, signal?: AbortSignal) {
  const saved = riskResearchOwnerReceiptSchema.parse(await request(
    `/v1/risk/scopes/${encodeURIComponent(value.scopeId)}/research/commands/${encodeURIComponent(value.commandId)}`,
    { signal, headers: { 'X-Owner-Id': researchOwner } }))
  if (saved.owner_id !== value.ownerId || saved.scope_id !== value.scopeId || saved.command_id !== value.commandId ||
      saved.request.scope_id !== value.scopeId || (value.canonicalRequestId !== null && saved.request.request_id !== value.canonicalRequestId) ||
      (saved.next_automatic_request && (saved.next_automatic_request.scope_id !== value.scopeId || saved.next_automatic_request.request_id === saved.request.request_id))) {
    throw new Error('本次研究回执与原操作身份不一致，尚未确认。')
  }
  return saved
}
