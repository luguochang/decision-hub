import { z } from 'zod'
import { promotionDecisionCommandSchema, promotionDecisionResultSchema } from '@decision-hub/contracts-ts'
import type { PromotionDecisionCommand } from '../api/types'
import { acknowledgeRequest, listPendingRequests, pendingRequest, type PendingRequest } from '../research/pendingRequests'

const frozenDecisionSchema = z.object({
  domain_pack_ref: z.string().min(1),
  candidate_version: z.string().min(1),
  command: promotionDecisionCommandSchema.omit({ request_id: true }),
}).strict()

export type FrozenEvolutionDecision = z.infer<typeof frozenDecisionSchema>
export type PendingEvolutionDecision = { request: PendingRequest; payload: FrozenEvolutionDecision }

// These errors are emitted after the application's decision transaction rolls
// back. Transport errors, identity errors and reused keys never grant dismissal.
const businessRefusals = new Set(['promotion_gate_failed', 'candidate_not_rejectable',
  'active_candidate_cannot_be_rejected', 'candidate_already_active',
  'active_pointer_generation_conflict', 'candidate_execution_scope_unknown',
  'research_harness_deployment_required', 'rollback_pointer_not_found',
  'rollback_target_is_active', 'rollback_target_not_verified', 'rollback_target_not_available'])

function refusalKey(item: PendingEvolutionDecision): string {
  return `decision-hub.evolution-refusal.v1:${JSON.stringify([
    import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', item.payload.domain_pack_ref,
    item.payload.command.owner, item.request.id,
  ])}`
}

export function rememberEvolutionRefusal(item: PendingEvolutionDecision, error: unknown): void {
  if (!(error instanceof Error) || !businessRefusals.has(error.message)) return
  sessionStorage.setItem(refusalKey(item), JSON.stringify({ signature: item.request.signature, reason_code: error.message }))
}

export function evolutionRefusal(item: PendingEvolutionDecision): string | null {
  const value = sessionStorage.getItem(refusalKey(item))
  if (!value) return null
  const record: unknown = JSON.parse(value)
  if (!record || typeof record !== 'object' || !('signature' in record) || record.signature !== item.request.signature ||
    !('reason_code' in record) || typeof record.reason_code !== 'string' || !businessRefusals.has(record.reason_code)) {
    throw new Error('原拒绝凭据无法校验，请保留原请求并核对审计记录。')
  }
  return record.reason_code
}

export function dismissEvolutionRefusal(item: PendingEvolutionDecision): void {
  if (!evolutionRefusal(item)) throw new Error('没有可核对的业务拒绝凭据，不能关闭原请求。')
  acknowledgeRequest('evolution-decision', item.request.id)
  // Keep the original refusal annotation for this request in the tab. Closing
  // it does not declare a later request invalid or rewrite the frozen command.
}

export function evolutionPendingDecisions(domain: string, owner: string): PendingEvolutionDecision[] {
  return listPendingRequests('evolution-decision').map(request => {
    const result = frozenDecisionSchema.safeParse(request.payload)
    if (!result.success || !request.id.startsWith('evolution-decision:') || request.signature !== JSON.stringify(request.payload)) {
      throw new Error('原决定的提交凭据无法校验。已暂停新决定，请保留凭据并核对审计记录。')
    }
    return { request, payload: result.data }
  }).filter(item => item.payload.domain_pack_ref === domain && item.payload.command.owner === owner)
}

export function freezeEvolutionDecision(payload: FrozenEvolutionDecision): PendingEvolutionDecision {
  const normalized = frozenDecisionSchema.parse(payload)
  const existing = evolutionPendingDecisions(normalized.domain_pack_ref, normalized.command.owner)
  if (existing.length) {
    if (JSON.stringify(existing[0].payload) !== JSON.stringify(normalized)) {
      throw new Error('上次决定尚未确认，不能更换候选、决定内容或版本序号。请先核对原回执。')
    }
    return existing[0]
  }
  return { request: pendingRequest('evolution-decision', normalized), payload: normalized }
}

export function originalEvolutionCommand(pending: PendingEvolutionDecision): PromotionDecisionCommand {
  return { ...pending.payload.command, request_id: pending.request.id }
}

export function confirmEvolutionReceipt(pending: PendingEvolutionDecision, value: unknown): z.infer<typeof promotionDecisionResultSchema> {
  const result = promotionDecisionResultSchema.safeParse(value)
  if (!result.success) throw new Error('返回回执格式异常，决定结果尚未确认，已保留原请求。')
  const receipt = result.data.decision
  const expected = originalEvolutionCommand(pending)
  const generation = expected.expected_generation + (expected.decision === 'reject' ? 0 : 1)
  if (receipt.request_id !== expected.request_id || receipt.domain_pack_ref !== pending.payload.domain_pack_ref ||
    receipt.candidate_id !== expected.candidate_id || receipt.owner !== expected.owner ||
    receipt.decision !== expected.decision || receipt.reason !== expected.reason ||
    JSON.stringify(receipt.evaluation_refs) !== JSON.stringify(expected.evaluation_refs) ||
    receipt.resulting_generation !== generation) {
    throw new Error('回执与原决定不一致，已保留原请求，请核对审计记录。')
  }
  // The response's pointer can be historical. Only the exact decision acknowledges
  // this request; current asset status or a matching candidate never does.
  acknowledgeRequest('evolution-decision', pending.request.id)
  try { sessionStorage.removeItem(refusalKey(pending)) } catch {
    // A failed annotation cleanup cannot undo a verified server receipt.
  }
  return result.data
}
