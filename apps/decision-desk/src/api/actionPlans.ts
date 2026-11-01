import {
  decisionUiCurrentViewV2Schema, decisionUiPlanViewV1Schema,
  decisionUiCommandV1Schema, decisionUiCommandReceiptV1Schema,
  decisionUiTrackingViewV1Schema, decisionUiReviewViewV1Schema,
  type DecisionUiReviewViewV1,
  type DecisionUiCurrentViewV2, type DecisionUiPlanViewV1,
  type DecisionUiCommandV1, type DecisionUiCommandReceiptV1,
} from '../../../../packages/contracts_ts/src/generated/r2'
import { ApiRequestError, request } from './client'

export type { DecisionUiCurrentViewV2, DecisionUiPlanViewV1, DecisionUiCommandV1, DecisionUiCommandReceiptV1 }
export type ActionBranch = DecisionUiPlanViewV1['branch']
export type ActionReview = NonNullable<DecisionUiReviewViewV1['review']>
export type ActionHorizon = DecisionUiCurrentViewV2['horizon']
export type ActionPosition = ActionBranch['revision']['position_context']
export const ACTION_OWNER = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const ownerHeaders = () => ({ 'X-Owner-Id': ACTION_OWNER })

function checkBranch(branch: ActionBranch, scope: string, horizon: ActionHorizon) {
  const { head, revision } = branch
  if (revision.scope_id !== scope || revision.horizon !== horizon || head.plan_id !== revision.plan_id
    || (revision.action === 'enter_long' && revision.position_context !== 'flat')
    || (['reduce_long', 'exit_long'].includes(revision.action) && revision.position_context !== 'long')
    || (branch.latest_evaluation && (branch.latest_evaluation.plan_id !== head.plan_id
      || branch.latest_evaluation.revision_id !== revision.revision_id))) throw new Error('action_response_identity_mismatch')
}

export type ActionIntent = { owner_id: string; plan_id: string; command: DecisionUiCommandV1 }
export function checkActionReceipt(receipt: DecisionUiCommandReceiptV1, intent: ActionIntent) {
  if (receipt.owner_id !== intent.owner_id || receipt.request_id !== intent.command.request_id
    || receipt.plan_id !== intent.plan_id || receipt.operation !== intent.command.operation
    || receipt.response.operation !== receipt.operation || receipt.response.head.plan_id !== intent.plan_id
    || receipt.response.head.current_revision_id !== intent.command.expected_revision_id) {
    throw new Error('action_receipt_identity_mismatch')
  }
  return receipt
}

export const actionPlansApi = {
  tracking: async (scope: string, horizon: ActionHorizon, cursor?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ scope_id: scope, horizon })
    if (cursor) params.set('cursor', cursor)
    const view = decisionUiTrackingViewV1Schema.parse(await request(`/v1/actions/tracking?${params}`, { signal, headers: ownerHeaders() }))
    if (view.scope_id !== scope || view.horizon !== horizon || view.count < view.items.length
      || new Set(view.items.map(item => item.plan_id)).size !== view.items.length) throw new Error('action_response_identity_mismatch')
    return view
  },
  current: async (scope: string, horizon: ActionHorizon, signal?: AbortSignal) => {
    const params = new URLSearchParams({ scope_id: scope, horizon })
    const view = decisionUiCurrentViewV2Schema.parse(await request(`/v2/actions/current?${params}`, { signal, headers: ownerHeaders() }))
    if (view.scope_id !== scope || view.horizon !== horizon
      || view.still_tracking_count < view.still_tracking.length
      || new Set(view.still_tracking.map(item => item.plan_id)).size !== view.still_tracking.length
      || (view.latest_opportunity && (view.latest_opportunity.scope_id !== scope || view.latest_opportunity.horizon !== horizon))
      || (view.decision && (view.decision.scope_id !== scope || view.decision.horizon !== horizon))) throw new Error('action_response_identity_mismatch')
    const positions = new Set<string>()
    for (const branch of view.branches) {
      checkBranch(branch, scope, horizon)
      if (!view.decision || branch.decision.scope_id !== scope || branch.decision.horizon !== horizon
        || branch.revision.decision_id !== branch.decision.id
        || branch.revision.opportunity_id !== branch.decision.opportunity_id
        || branch.decision.opportunity_id !== view.decision.opportunity_id
        || positions.has(branch.revision.position_context)) throw new Error('action_response_identity_mismatch')
      positions.add(branch.revision.position_context)
    }
    return view
  },
  reviews: async (opportunityId: string, scope: string, horizon: ActionHorizon, signal?: AbortSignal) => {
    const params = new URLSearchParams({ opportunity_id: opportunityId })
    const view = decisionUiReviewViewV1Schema.parse(await request(`/v1/actions/reviews?${params}`, { signal, headers: ownerHeaders() }))
    if (view.opportunity_id !== opportunityId || view.scope_id !== scope || view.horizon !== horizon) throw new Error('action_response_identity_mismatch')
    const positions = new Set<string>()
    if (view.review) for (const branch of view.review.branches) {
      const { candidate, mechanism, current_head: head } = branch
      if (positions.has(branch.position_context) || (head && head.plan_id !== branch.plan_id)
        || branch.applied !== (branch.disposition === 'updated')
        || (candidate && (!mechanism || candidate.scope_id !== scope || candidate.horizon !== horizon || candidate.position_context !== branch.position_context))
        || (mechanism && (!candidate || mechanism.scope_id !== scope || mechanism.horizon !== horizon
          || mechanism.run_id !== view.review.run_id || mechanism.review_id !== candidate.mechanism_review_ref
          || Date.parse(mechanism.cutoff) !== Date.parse(candidate.as_of)))) throw new Error('action_response_identity_mismatch')
      positions.add(branch.position_context)
    }
    return view
  },
  plan: async (planId: string, revisionId: string, cursor?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ revision_id: revisionId })
    if (cursor) params.set('cursor', cursor)
    const view = decisionUiPlanViewV1Schema.parse(await request(`/v1/actions/plans/${encodeURIComponent(planId)}?${params}`, { signal, headers: ownerHeaders() }))
    if (view.branch.head.plan_id !== planId || view.branch.revision.revision_id !== revisionId
      || view.branch.revision.decision_id !== view.decision.id
      || view.branch.revision.opportunity_id !== view.opportunity.id
      || view.opportunity.scope_id !== view.decision.scope_id || view.opportunity.horizon !== view.decision.horizon
      || view.evaluations.some(item => item.plan_id !== planId || item.revision_id !== revisionId)) throw new Error('action_response_identity_mismatch')
    checkBranch(view.branch, view.decision.scope_id, view.decision.horizon)
    return view
  },
  command: async (intent: ActionIntent) => {
    if (intent.owner_id !== ACTION_OWNER) throw new Error('action_owner_mismatch')
    const command = decisionUiCommandV1Schema.parse(intent.command)
    const receipt = decisionUiCommandReceiptV1Schema.parse(await request(`/v1/actions/plans/${encodeURIComponent(intent.plan_id)}/commands`, {
      method: 'POST', headers: { ...ownerHeaders(), 'Content-Type': 'application/json', 'Idempotency-Key': command.request_id }, body: JSON.stringify(command),
    }))
    return checkActionReceipt(receipt, intent)
  },
  receipt: async (intent: ActionIntent, signal?: AbortSignal) => {
    if (intent.owner_id !== ACTION_OWNER) throw new Error('action_owner_mismatch')
    const receipt = decisionUiCommandReceiptV1Schema.parse(await request(`/v1/actions/commands/${encodeURIComponent(intent.command.request_id)}`, { signal, headers: ownerHeaders() }))
    return checkActionReceipt(receipt, intent)
  },
}

export function actionIntentKey(scope: string, horizon: ActionHorizon) {
  return `decision-hub:action-intent.v1:${ACTION_OWNER}:${scope}:${horizon}`
}
export function readActionIntent(key: string): ActionIntent | null {
  const raw = sessionStorage.getItem(key)
  if (!raw) return null
  const item = JSON.parse(raw) as ActionIntent
  if (!item || item.owner_id !== ACTION_OWNER || typeof item.plan_id !== 'string' || !item.plan_id.trim()
    || Object.keys(item).sort().join() !== 'command,owner_id,plan_id') throw new Error('action_intent_storage_invalid')
  return { owner_id: item.owner_id, plan_id: item.plan_id, command: decisionUiCommandV1Schema.parse(item.command) }
}
export function saveActionIntent(key: string, intent: ActionIntent) {
  const serialized = JSON.stringify(intent)
  sessionStorage.setItem(key, serialized)
  if (sessionStorage.getItem(key) !== serialized) throw new Error('action_intent_storage_invalid')
  readActionIntent(key)
}
export function actionErrorMessage(error: unknown): string {
  const code = error instanceof Error ? error.message : ''
  const labels: Record<string, string> = {
    action_response_identity_mismatch: '返回内容与所选资产或方案版本不一致，当前内容已隐藏。',
    action_receipt_identity_mismatch: '回执与原操作不一致，结果尚未确认。',
    action_plan_expired: '这个方案已到期，请读取新的方案。',
    action_revision_superseded: '方案已更新，请先恢复原操作回执，再读取最新版本。',
    action_state_version_conflict: '监控状态已发生变化，请核对原回执后刷新。',
    action_terminal_plan_restart_forbidden: '这个方案已经结束，不能重新启动监控。',
    action_product_disabled: '行动方案尚未启用，原研究报告仍可查看。',
    action_intent_storage_invalid: '无法核对本机保存的原操作，尚未提交新操作。',
    action_owner_mismatch: '当前操作身份与原请求不一致。',
  }
  if (labels[code]) return labels[code]
  if (error instanceof ApiRequestError && error.status === 503) return '行动方案服务尚未就绪，请在管理与审计核对运行状态。'
  if (error instanceof ApiRequestError && error.status === 404) return '尚未找到这次操作的已保存回执，可以继续核对或重试原操作。'
  return '当前结果尚未确认，请重新读取。原操作会保留，避免重复提交。'
}

export function isDefinitivelyRejectedAction(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError && error.status === 409 && new Set([
    'action_state_version_conflict', 'action_revision_superseded', 'action_plan_expired',
    'action_terminal_plan_restart_forbidden',
  ]).has(error.message)
}
