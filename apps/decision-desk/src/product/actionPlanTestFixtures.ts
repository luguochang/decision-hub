// Synthetic component fixtures only; never requested by the product API.
import { decisionUiCurrentViewV1Schema, decisionUiCurrentViewV2Schema, decisionUiCommandReceiptV1Schema, decisionUiPlanViewV1Schema, decisionUiReviewViewV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import type { ActionIntent, ActionPosition } from '../api/actionPlans'

export const ACTION_SCOPE = 'crypto:spot:okx:BTC-USDT:trade:1m'
const hash = 'a'.repeat(64)
const asof = '2026-10-13T00:00:00Z'
const until = '2099-10-14T00:00:00Z'

function branch(position: ActionPosition) {
  const plan = `plan-${position}`, revision = `revision-${position}`
  return {
    head: { schema_version: 'decision-action-plan-head.v1', plan_id: plan, current_revision_id: revision,
      state_version: 1, state: 'armed', monitor_state: 'active', next_due: asof, valid_until: until,
      lease_owner: null, lease_generation: 1, lease_expires_at: null, last_input_watermark: null, enter_streak: 0 },
    revision: { schema_version: 'decision-action-plan-revision.v1', revision_id: revision, plan_id: plan,
      decision_id: 'decision-old', opportunity_id: 'opportunity-old', revision_no: 1, previous_id: null,
      scope_id: ACTION_SCOPE, horizon: '24h', as_of: asof, published_at: asof, valid_until: until,
      action: position === 'flat' ? 'enter_long' : position === 'long' ? 'reduce_long' : 'wait',
      position_context: position, advice_qualification: 'research_trial', decision_status: 'actionable',
      entry: [{ id: `condition-${position}`, purpose: 'entry', expression: { op: 'compare', feature: 'close', scope_id: ACTION_SCOPE, unit: 'USDT/BTC', cmp: 'gte', threshold: '85000', max_age_seconds: 120 }, min_fresh_samples: 2, max_age_seconds: 120, evaluation_seconds: 60, calendar_ref: 'crypto-24x7.v1', valid_until: until, original_text: '等待连续两次新鲜分钟收盘确认', input_requirements: [{ metric: 'close', scope: ACTION_SCOPE, source: 'okx', unit: 'USDT/BTC' }] }],
      exit: [], invalidation: [], size_policy: { kind: 'fixed_scenario', amount: '2500', unit: 'USDT' },
      cost_policy_ref: 'cost', cost_policy_hash: hash, target_spec_ref: 'target', target_spec_hash: hash,
      basis_refs: [{ ref: 'evidence-original', hash }], reason: '美元流动性与成交量尚待共同确认，按不同仓位处理。', payload_hash: hash },
    probability: position === 'long' ? { target_id: 'target', value: 0.61, kind: 'subjective', provenance_ref: null } : null,
    causal_steps: [{ from_description: '美元流动性收紧', hypothesis: '资金成本可能压制风险资产，但需求回升可能抵消影响。', to_description: '现货买盘承压', support_refs: ['support-original'], counter_refs: ['counter-original'] }],
    unresolved: ['ETF 流量尚未更新'], priced_in_status: 'unknown', next_review_at: until,
    latest_evaluation: { schema_version: 'decision-condition-evaluation.v2', id: `evaluation-${position}`, plan_id: plan,
      revision_id: revision, tick_at: asof, as_of: asof, evaluated_at: asof, lease_generation: 1, input_ref: 'input-original', input_hash: hash, truth: 'unknown', status: 'ok',
      per_condition: [{ id: `condition-${position}`, truth: 'unknown', status: 'ok', reason: null, input_refs: ['minute-one'], raw_truth: 'true', confirmation_count: 1, last_bar_at: asof, confirmed: false }],
      state_before: 'armed', state_after: 'armed', evaluation_hash: hash, state_version_before: 1, state_version_after: 1, monitor_state_before: 'active', monitor_state_after: 'active' },
    first_display_at: null, first_ack_at: null, expired: false,
  }
}

export function currentActionFixture() {
  const original = decisionUiCurrentViewV1Schema.parse({
    schema_version: 'decision-current-view.v1', scope_id: ACTION_SCOPE, horizon: '24h', as_of: asof,
    enabled: true, availability: 'available',
    latest_opportunity: { schema_version: 'decision-opportunity.v1', id: 'opportunity-new', key: 'new-event', scope_id: ACTION_SCOPE,
      horizon: '24h', kind: 'event', event_ref: 'new-event', registered_at: asof, time_start: asof, time_end: until,
      target_spec_hash: hash, recipe_hash: hash, cohort: 'prospective', status: 'missed', reason: '本次独立研究未完成，等待来源恢复。' },
    decision: { schema_version: 'decision-snapshot.v1', id: 'decision-old', opportunity_id: 'opportunity-old', run_id: 'run-original', source_result_ref: 'original-result', source_result_hash: hash,
      decision_cutoff_at: asof, known_at: asof, scope_id: ACTION_SCOPE, horizon: '24h',
      hypotheses: [{ direction: 'range', evidence_refs: ['support-original'], counter_refs: ['counter-original'], rationale: '资金变化与需求回升存在竞争机制。' }],
      factor_refs: [], direction_status: 'eligible', path_status: 'experimental', probability: { target_id: 'target', value: null, kind: 'unavailable', provenance_ref: null }, gaps: [], skill_refs: [], snapshot_hash: hash },
    branches: [branch('flat'), branch('long'), branch('unknown')], gap_codes: [],
    still_tracking_count: 0, still_tracking: [], tracking_next_cursor: null,
  })
  return decisionUiCurrentViewV2Schema.parse({ ...original, schema_version: 'decision-current-view.v2',
    branches: original.branches.map(item => ({ ...item, decision: original.decision })) })
}

export function actionReceiptFixture(intent: ActionIntent) {
  const head = currentActionFixture().branches.find(item => item.head.plan_id === intent.plan_id)!.head
  return decisionUiCommandReceiptV1Schema.parse({ schema_version: 'action-command-receipt.v1', owner_id: intent.owner_id,
    request_id: intent.command.request_id, plan_id: intent.plan_id, operation: intent.command.operation,
    payload_hash: hash, response: { status: 'recorded', operation: intent.command.operation, server_time: asof, head }, accepted_at: asof, receipt_hash: hash })
}

export function actionDetailFixture() {
  const current = currentActionFixture()
  const { decision: _branchDecision, ...detailBranch } = current.branches[0]
  return decisionUiPlanViewV1Schema.parse({ schema_version: 'decision-plan-view.v1', as_of: asof, branch: detailBranch,
    decision: current.decision, opportunity: { ...current.latest_opportunity, id: 'opportunity-old', status: 'decided' },
    evaluations: [current.branches[0].latest_evaluation], next_cursor: null })
}

export function actionReviewFixture() {
  const current = currentActionFixture()
  const mechanism = { schema_version: 'research-mechanism-review.v1', review_id: 'review-mechanism', run_id: 'run-review', cutoff: asof,
    scope_id: ACTION_SCOPE, horizon: '24h',
    nodes: [{ id: 'dollar', kind: 'expectation', description: '美元利率预期变化', refs: ['evidence-review'] },
      { id: 'flow', kind: 'observable', description: '风险资产资金流变化', refs: [] }],
    edges: [{ id: 'edge-review', from_node: 'dollar', to_node: 'flow', hypothesis: '利率变化可能影响买盘，需结合反方流量核实。',
      lag: null, support_refs: ['evidence-review'], counter_refs: ['evidence-counter'], testable_feature_refs: [], status: 'hypothesis' }],
    alternative_refs: [], priced_in_status: 'unknown', priced_in_refs: [], unresolved: ['最新流量仍待核实'], affected_plan_refs: [] }
  return decisionUiReviewViewV1Schema.parse({ schema_version: 'action-review-view.v1', opportunity_id: 'opportunity-old',
    scope_id: ACTION_SCOPE, horizon: '24h', as_of: asof, review: { run_id: 'run-review', request_id: 'review-request', receipt_hash: hash,
      sidecar_ref: 'review-sidecar', sidecar_hash: hash, completed_at: asof, status: 'published',
      branches: current.branches.map((item, index) => ({ position_context: item.revision.position_context,
        plan_id: item.head.plan_id, revision_id: item.revision.revision_id, revision_hash: hash, outbox_id: index + 1,
        disposition: index === 0 ? 'retained' : 'updated', applied: index !== 0,
        reason: index === 0 ? 'triggered_protection_carryover_pending' : 'same_opportunity_revision',
        next_review_at: index === 2 ? null : until, current_head: item.head,
        candidate: index === 2 ? null : { schema_version: 'research-action-candidate.v1', scope_id: ACTION_SCOPE, horizon: '24h',
          as_of: asof, valid_until: until, next_review_at: until, position_context: item.revision.position_context,
          action: index === 0 ? 'wait' : 'reduce_long', probability: item.probability ?? { target_id: 'target', value: null, kind: 'unavailable', provenance_ref: null },
          entry: item.revision.entry, exit: [], invalidation: [], size_policy_ref: 'size', cost_policy_ref: 'cost', target_spec_ref: 'target',
          evidence_refs: ['evidence-review'], claim_refs: [], mechanism_review_ref: 'review-mechanism',
          reason: index === 0 ? '复核建议等待新的买盘确认。' : '复核建议按条件降低多仓风险。' },
        mechanism: index === 2 ? null : mechanism,
      })),
    } })
}
