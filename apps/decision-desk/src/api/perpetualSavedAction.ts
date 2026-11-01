import { z } from 'zod'
import { actionComparisonV2Schema, actionPlanRevisionV2Schema, comparisonCostPolicyV1Schema,
  decisionSnapshotV2Schema, decisionSourceRefV1Schema, publicationBoundTargetV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import type { SavedPerpetualReport } from './perpetualSavedReport'

const id = z.string().min(1), sha = z.string().regex(/^[a-f0-9]{64}$/), at = z.string().datetime({ offset: true })
const ref = decisionSourceRefV1Schema
const observed = z.object({ output_id: id, role: id, schema_version: id, payload_hash: sha, recorded_at: at }).strict()
const branch = z.object({ position_context: z.enum(['flat', 'long', 'short', 'unknown']),
  plan_id: id, revision_id: id, revision_hash: sha, outbox_id: z.number().int().positive().safe() }).strict()
const response = z.object({ status: z.literal('published'), opportunity_id: id, decision_id: id,
  snapshot_hash: sha, branches: z.tuple([branch]), qualification: z.literal('research_trial'),
  execution: z.literal('paper_only'), uncovered_branches: z.array(id), gaps: z.array(id), server_time: at,
  event_scenario_publication_ref: ref.optional(), event_scenario_plan_ref: ref.optional() }).strict()
const receipt = z.object({ schema_version: z.literal('action-command-receipt.v1'),
  owner_id: z.literal('system:action-publication:v1'), request_id: id, plan_id: id,
  operation: z.literal('publish'), payload_hash: sha, response, accepted_at: at, receipt_hash: sha }).strict()
const costs = z.object({ schema_version: z.literal('perpetual-cost-assumptions.v1'),
  purpose: z.literal('research_unit_only'), qualification: z.literal('research_trial'),
  financial_qualified: z.literal(false), unit_notional_usdt: z.literal('100'),
  base_cost_policy: comparisonCostPolicyV1Schema, base_cost_ref: ref,
  assumed_taker_rate: id, assumed_slippage_fraction_per_leg: id, funding_baseline: id,
  future_actual_funding_cash_usdt: z.null(), account_fee_rate_verified: z.literal(false) }).passthrough()
const derivation = z.object({ schema_version: z.literal('perpetual-publication-derivation.v1'),
  native_synthesis_ref: ref, review_authorization_ref: ref, admission_ref: ref, task_binding_ref: ref,
  mapped_original: observed, provider_original: observed, transport_original: observed,
  producer_generation: z.number().int().min(1), native_wire_originals_ref: ref, research_materials_ref: ref,
  native_paths_ref: ref, bound_paths_ref: ref, comparison_inputs_ref: ref, comparison_ref: ref,
  final_target_ref: ref, anchor_receipt_ref: ref, candidate_id_mapping: z.record(id),
  unavailable_candidates: z.array(z.object({ candidate_id: id, reviewed_candidate_ref: ref, reason: id }).strict()),
  changes: z.literal('one_assigned_target_rebinding_and_typed_datetime_wire_projection_only'),
  original_raw_forecast_retained: z.literal(true), probabilities_and_amplitudes_changed: z.literal(false),
  unit_basis: z.literal('100_USDT_comparison_not_balance_size_or_fill'),
  primary_position_state: z.enum(['flat', 'long', 'short', 'unknown']), uncovered_branches: z.array(id),
  gaps: z.array(id) }).strict()
const available = z.object({ schema_version: z.literal('perpetual-saved-action.v1'), status: z.literal('available'),
  binding: z.object({ owner_id: id, run_id: id, artifact_id: id, report_synthesis_ref: ref, admission_ref: ref }).strict(),
  accepted_at: at, command_receipt: receipt, snapshot: decisionSnapshotV2Schema,
  revision: actionPlanRevisionV2Schema, comparison: actionComparisonV2Schema, target: publicationBoundTargetV1Schema,
  cost_assumptions: costs, cost_assumptions_ref: ref, derivation,
  financial_qualified: z.literal(false), execution_authorized: z.literal(false) }).strict()
const unavailable = z.object({ schema_version: z.literal('perpetual-saved-action.v1'),
  status: z.literal('unavailable'), reason_code: z.literal('action_publication_not_saved'),
}).strict()
export const savedActionSchema = z.discriminatedUnion('status', [available, unavailable])
export type SavedPerpetualAction = z.infer<typeof savedActionSchema>
type Available = Extract<SavedPerpetualAction, { status: 'available' }>
export type SavedActionReportContext = Pick<SavedPerpetualReport,
  'owner_id' | 'run_id' | 'artifact_id' | 'published_at' | 'read_at'> & {
    packet: SavedPerpetualReport['payload']['perpetual_synthesis']
  }
const same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
function requireOriginal(condition: boolean) {
  if (!condition) throw new Error('perpetual_report_action_original_mismatch')
}
function micros(value: string) {
  const millis = Date.parse(value), fraction = /\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/.exec(value)?.[1] ?? ''
  requireOriginal(Number.isFinite(millis) && fraction.length <= 6)
  return BigInt(millis) * 1000n + BigInt(fraction.padEnd(6, '0').slice(3))
}
async function sealed(value: Record<string, unknown>, field: string) {
  const { [field]: seal, ...body } = value
  requireOriginal(seal === await hash(body))
}
async function checkHashes(value: Available) {
  const { snapshot: s, revision: r, comparison: c, cost_assumptions: cost } = value
  await Promise.all([sealed(s, 'snapshot_hash'), sealed(r, 'payload_hash'),
    sealed(c, 'payload_hash'), sealed(value.command_receipt, 'receipt_hash')])
  requireOriginal(c.binding_hash === value.derivation.comparison_inputs_ref.hash)
  const pairs = [[c, s.bindings.comparison], [value.target, s.bindings.final_target],
    [value.derivation, s.bindings.derivation], [cost, value.cost_assumptions_ref],
    [cost.base_cost_policy, cost.base_cost_ref], [r.selected_candidate, r.selected_candidate_ref]] as const
  for (const [body, reference] of pairs) requireOriginal(reference !== null && reference.hash === await hash(body))
}
function checkIdentity(value: Available, context: SavedActionReportContext) {
  const { snapshot: s, revision: r, comparison: c, target: t, binding: b, derivation: d } = value
  const primary = context.packet.synthesis.schema_version === 'research-decision-synthesis.v3'
    ? context.packet.synthesis.primary_v2 : context.packet.synthesis
  const duration = { '4h': 14400, '24h': 86400, '72h': 259200 }[primary.candidate_set.context.horizon]
  requireOriginal(b.owner_id === context.owner_id && b.run_id === context.run_id && b.artifact_id === context.artifact_id
    && s.run_id === context.run_id && s.context.owner_id === context.owner_id
    && same(s.context, primary.candidate_set.context) && same(r.context, s.context)
    && same(r.bindings, s.bindings) && s.opportunity_id === primary.opportunity_id
    && r.opportunity_id === s.opportunity_id && c.opportunity_id === s.opportunity_id && r.decision_id === s.id
    && [s.horizon, r.horizon, c.horizon, t.horizon].every(horizon => horizon === s.context.horizon)
    && [r.scope_id, c.scope_id, s.context.scope_id].every(scope => scope === s.scope_id)
    && t.duration_seconds === duration && micros(t.window_end) - micros(t.window_start) === BigInt(duration) * 1000000n
    && same(b.report_synthesis_ref, context.packet.binding.synthesis_ref)
    && same(d.admission_ref, b.admission_ref) && same(d.mapped_original, context.packet.binding.mapped_ref)
    && same(d.provider_original, context.packet.binding.provider_emission_ref)
    && s.source_result_ref === d.mapped_original.output_id && s.source_result_hash === d.mapped_original.payload_hash
    && same(d.native_synthesis_ref, s.bindings.native_synthesis)
    && same(d.review_authorization_ref, s.bindings.review_authorization)
    && same(d.comparison_ref, s.bindings.comparison) && same(d.final_target_ref, s.bindings.final_target)
    && same(d.anchor_receipt_ref, s.bindings.anchor_receipt) && same(c.target_ref, s.bindings.final_target)
    && same(t.candidate_target_ref, s.bindings.candidate_target) && same(t.anchor_rule_ref, s.bindings.anchor_rule)
    && same(t.instrument_ref, s.bindings.instrument)
    && same(c.bindings.cost, value.cost_assumptions.base_cost_ref)
    && same(s.bindings.candidate_set, primary.candidate_set_ref)
    && same(s.bindings.candidate_target, primary.candidate_set.candidate_target_ref)
    && same(r.selected_candidate.context, s.context) && c.forecast_as_of === primary.as_of)
  for (const key of ['settings', 'instrument', 'account', 'utility'] as const) {
    requireOriginal(same(c.bindings[key], s.bindings[key]))
  }
}
function checkReceipt(value: Available, context: SavedActionReportContext) {
  const { snapshot: s, revision: r, command_receipt: command, accepted_at: at } = value
  const result = command.response, branch = result.branches[0], time = micros(at)
  requireOriginal(micros(context.published_at) <= time && time <= micros(context.read_at)
    && micros(command.accepted_at) === time && micros(s.known_at) === time && micros(r.published_at) === time
    && micros(value.target.assigned_at) === time && micros(result.server_time) === time
    && command.plan_id === r.plan_id && result.opportunity_id === s.opportunity_id && result.decision_id === s.id
    && result.snapshot_hash === s.snapshot_hash && branch.plan_id === r.plan_id && branch.revision_id === r.revision_id
    && branch.revision_hash === r.payload_hash && branch.position_context === r.position_context
    && same(result.uncovered_branches, value.derivation.uncovered_branches)
    && same(result.gaps, value.derivation.gaps)
    && (result.event_scenario_plan_ref === undefined) === (result.event_scenario_publication_ref === undefined))
  if (result.event_scenario_plan_ref !== undefined) {
    requireOriginal(context.packet.synthesis.schema_version === 'research-decision-synthesis.v3'
      && same(result.event_scenario_plan_ref, context.packet.synthesis.event_plan_ref))
  }
}
async function checkSelection(value: Available, context: SavedActionReportContext) {
  const { revision: r, comparison: c, derivation: d } = value, synthesis = context.packet.synthesis
  const primary = synthesis.schema_version === 'research-decision-synthesis.v3' ? synthesis.primary_v2 : synthesis
  const request = context.packet.review_request
  const nativeRef = request.schema_version === 'action-review-request.v3'
    ? request.primary_review_request.synthesis_ref : request.synthesis_ref
  requireOriginal(same(d.native_synthesis_ref, nativeRef) && nativeRef.hash === await hash(primary)
    && c.selected_action_id !== null && d.candidate_id_mapping[c.selected_action_id] === r.selected_candidate.candidate_id)
  for (const row of c.candidate_results) {
    const index = primary.candidate_set.candidates.findIndex(candidate => candidate.candidate_id === d.candidate_id_mapping[row.candidate_id])
    const candidate = primary.candidate_set.candidates[index]
    requireOriginal(candidate !== undefined && candidate.action === row.action && candidate.target_side === row.target_side
      && candidate.position_branch === c.position_branch)
    if (row.candidate_id === c.selected_action_id) {
      requireOriginal(same(candidate, r.selected_candidate) && same(primary.candidate_set.candidate_refs[index], r.selected_candidate_ref))
    }
  }
}
export async function checkSavedPerpetualAction(value: SavedPerpetualAction | undefined, context: SavedActionReportContext) {
  if (value === undefined || value.status === 'unavailable') return
  checkIdentity(value, context)
  checkReceipt(value, context)
  await checkHashes(value)
  await checkSelection(value, context)
  requireOriginal(value.command_receipt.request_id === 'publish_' + await hash([context.run_id, value.snapshot.opportunity_id]))
}
