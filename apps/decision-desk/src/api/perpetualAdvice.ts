import { z } from 'zod'
import { accountSnapshotV2Schema, actionComparisonV2Schema, actionPlanRevisionV2Schema, comparisonCostPolicyV1Schema,
  conditionalMarketDistributionV1Schema, decisionSettingsPartitionV1Schema, decisionSnapshotV2Schema, decisionSourceRefV1Schema,
  eventProbabilityV1Schema, frozenForecastV1Schema, positionContextV2Schema, probabilityEstimateV1Schema, publicationBoundTargetV1Schema,
  utilityPolicyV2Schema, decisionHorizonV2Schema, eventScenarioActionPlanV1Schema, eventScenarioReviewScopeV1Schema,
  scenarioBranchSelectionV1Schema, type DecisionHorizonV2, type DecisionSettingsCurrentViewV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { settingsApi } from './decisionSettings'
import { request } from './client'
import { checkForecastBasis, perpetualForecastBasisSchema } from './perpetualForecastBasis'

const selectionSchema = z.object({ owner_id: z.string().min(1), partition: decisionSettingsPartitionV1Schema,
  settings_revision: decisionSourceRefV1Schema, settings_state_version: z.number().int().min(1), horizon: decisionHorizonV2Schema }).strict()
export type PerpetualAdviceSelection = z.infer<typeof selectionSchema>
// Local read transport: the nested originals retain their canonical generated DTOs.
const costSchema = z.object({ schema_version: z.literal('perpetual-cost-assumptions.v1'), purpose: z.literal('research_unit_only'),
  qualification: z.literal('research_trial'), financial_qualified: z.literal(false), unit_notional_usdt: z.literal('100'),
  base_cost_policy: comparisonCostPolicyV1Schema, base_cost_ref: decisionSourceRefV1Schema,
  assumed_taker_rate: z.string(), assumed_slippage_fraction_per_leg: z.string(), funding_baseline: z.string(),
  future_actual_funding_cash_usdt: z.null(), account_fee_rate_verified: z.literal(false),
}).passthrough()
const eventSelectionOriginalSchema = z.object({ schema_version: z.literal('event-scenario-watch-selection.v1'),
  owner_id: z.string(), event_plan_id: z.string(), origin_run_id: z.string(), publication_ref: decisionSourceRefV1Schema,
  plan_revision_ref: decisionSourceRefV1Schema,
  selection: scenarioBranchSelectionV1Schema, recorded_at: z.string().datetime(),
}).passthrough()
const eventScenarioViewSchema = z.object({ plan: eventScenarioActionPlanV1Schema, plan_ref: decisionSourceRefV1Schema,
  origin_run_id: z.string().min(1),
  review_scope: eventScenarioReviewScopeV1Schema, review_authorization_ref: decisionSourceRefV1Schema,
  publication_ref: decisionSourceRefV1Schema, selection: scenarioBranchSelectionV1Schema.nullable(),
  selection_ref: decisionSourceRefV1Schema.nullable(), selection_original: eventSelectionOriginalSchema.nullable(),
  read_at: z.string().datetime(), current_state: z.enum(['active', 'expired', 'invalidated']),
}).strict()
const viewSchema = selectionSchema.extend({ schema_version: z.enum(['perpetual-advice-current.v1', 'perpetual-advice-current.v2']),
  status: z.enum(['available', 'expired', 'invalidated', 'not_started', 'researching', 'failed', 'awaiting_publication', 'settings_changed']),
  run_id: z.string().min(1).nullable(), report_id: z.string().min(1).nullable(), snapshot: decisionSnapshotV2Schema.nullable(),
  revision: actionPlanRevisionV2Schema.nullable(), comparison: actionComparisonV2Schema.nullable(), frozen_forecast: frozenForecastV1Schema.nullable(),
  target: publicationBoundTargetV1Schema.nullable(), cost_assumptions: costSchema.nullable(), cost_assumptions_ref: decisionSourceRefV1Schema.nullable(),
  event_probability: eventProbabilityV1Schema.nullable(), market_distribution: conditionalMarketDistributionV1Schema.nullable(), candidate_probability: probabilityEstimateV1Schema.nullable(),
  event_scenario_plan: eventScenarioViewSchema.nullable().optional(),
  forecast_basis: perpetualForecastBasisSchema.nullable().optional(),
  context: z.object({ account_snapshot: accountSnapshotV2Schema.nullable(), position_context: positionContextV2Schema.nullable(), utility_policy: utilityPolicyV2Schema.nullable() }).strict().nullable(),
  gaps: z.array(z.string()), updated_at: z.string().datetime(), qualification: z.literal('research_trial'), execution: z.literal('paper_only'),
}).strict()
export type PerpetualAdviceView = z.infer<typeof viewSchema>
export function perpetualAdviceSelection(current: DecisionSettingsCurrentViewV1 | null, horizon: DecisionHorizonV2 = '4h'): PerpetualAdviceSelection | null {
  if (!current?.revision.values || current.revision.operation !== 'upsert') return null
  const revision = current.revision
  return selectionSchema.parse({ owner_id: revision.owner_id, partition: revision.partition, settings_revision: { ref: revision.revision_id, hash: revision.payload_hash }, settings_state_version: current.state_version, horizon })
}
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
function requireOwner(owner: string) { if (settingsApi.session()?.owner_id !== owner) throw new Error('perpetual_advice_owner_mismatch') }
async function sealed(value: Record<string, unknown>, field: string) {
  const { [field]: seal, ...body } = value
  if (seal !== await hash(body)) throw new Error('perpetual_advice_original_hash_mismatch')
}
async function checked(raw: unknown, selection: PerpetualAdviceSelection): Promise<PerpetualAdviceView> {
  const value = viewSchema.parse(raw)
  if (value.schema_version !== (selection.horizon === '4h' ? 'perpetual-advice-current.v1' : 'perpetual-advice-current.v2')) throw new Error('perpetual_advice_version_mismatch')
  const duration = { '4h': 14400, '24h': 86400, '72h': 259200 }[selection.horizon]
  for (const key of ['owner_id', 'partition', 'settings_revision', 'settings_state_version', 'horizon'] as const) {
    if (!same(value[key], selection[key])) throw new Error('perpetual_advice_identity_mismatch')
  }
  if (value.status === 'available' || value.status === 'expired' || value.status === 'invalidated') {
    const { snapshot: s, revision: r, comparison: c, frozen_forecast: f, target: t, cost_assumptions: cost,
      cost_assumptions_ref: costRef, context, event_probability: event, market_distribution: market, candidate_probability: probability } = value
    if (!s || !r || !c || !f || !t || !cost || !costRef || !context?.account_snapshot || !context.position_context || !context.utility_policy || !event || !market || !probability
      || !value.run_id || !value.report_id) throw new Error('perpetual_advice_original_missing')
    const identity = s.context
    // Calculator action keys are a server mapping; reviewed candidate IDs remain original.
    const candidate = r.selected_candidate, selectedResult = c.candidate_results.find(row => row.candidate_id === c.selected_action_id)
    if (identity.owner_id !== value.owner_id || identity.account_kind !== selection.partition.account_kind || identity.account_id !== selection.partition.account_id
      || identity.instrument_id !== selection.partition.instrument_id || identity.profile_ref.ref !== selection.partition.profile_ref
      || !same(identity.settings_ref, selection.settings_revision) || !same(r.context, identity) || !same(r.bindings, s.bindings)
      || s.run_id !== value.run_id || r.decision_id !== s.id || r.opportunity_id !== s.opportunity_id || c.opportunity_id !== s.opportunity_id || f.opportunity_id !== s.opportunity_id
      || identity.horizon !== selection.horizon || s.horizon !== selection.horizon || r.horizon !== selection.horizon || c.horizon !== selection.horizon || t.horizon !== selection.horizon || t.duration_seconds !== duration
      || r.scope_id !== s.scope_id || c.scope_id !== s.scope_id || identity.scope_id !== s.scope_id
      || Date.parse(t.window_end) - Date.parse(t.window_start) !== duration * 1000 || Date.parse(r.published_at) !== Date.parse(t.assigned_at)
      || Date.parse(s.known_at) > Date.parse(value.updated_at) || Date.parse(r.published_at) > Date.parse(value.updated_at)
      || !same(candidate.context, identity) || !selectedResult || selectedResult.action !== candidate.action || selectedResult.target_side !== candidate.target_side
      || c.candidate_results.length !== 3 || new Set(c.candidate_results.map(row => row.candidate_id)).size !== 3
      || !same(c.bindings.settings, selection.settings_revision) || !same(c.target_ref, s.bindings.final_target) || !same(f.final_target_ref, c.target_ref)
      || !same(f.candidate_target_ref, s.bindings.candidate_target) || !same(f.anchor_receipt_ref, s.bindings.anchor_receipt)
      || !same(c.bindings.forecast, s.bindings.frozen_forecast) || !same(c.bindings.account, s.bindings.account)
      || !same(c.bindings.utility, s.bindings.utility) || !same(c.bindings.cost, cost.base_cost_ref)
      || !same(c.market_distribution.direction_distribution.probabilities, market.direction_distribution.probabilities)
      || !same(c.event_probability.occurrence_distribution.probabilities, event.occurrence_distribution.probabilities)
      || !same(c.event_probability.outcome_given_occurrence.probabilities, event.outcome_given_occurrence.probabilities)
      || value.status === 'available' && (Date.parse(value.updated_at) >= Date.parse(t.window_end) || Date.parse(value.updated_at) >= Date.parse(r.valid_until))) throw new Error('perpetual_advice_original_join_mismatch')
    await Promise.all([sealed(s, 'snapshot_hash'), sealed(r, 'payload_hash'), sealed(c, 'payload_hash'), sealed(context.utility_policy, 'hash')])
    const bodies = [[t, c.target_ref], [f, s.bindings.frozen_forecast], [c, s.bindings.comparison], [r.selected_candidate, r.selected_candidate_ref],
      [cost, costRef], [cost.base_cost_policy, cost.base_cost_ref], [event, f.event_probability_ref], [market, f.market_distribution_ref], [probability, f.candidate_probability_ref],
      [context.account_snapshot, s.bindings.account]] as const
    for (const [body, ref] of bodies) if (!ref || await hash(body) !== ref.hash) throw new Error('perpetual_advice_original_hash_mismatch')
    if (s.bindings.position && await hash(context.position_context) !== s.bindings.position.hash) throw new Error('perpetual_advice_original_hash_mismatch')
    if (!same(context.utility_policy.account_ref, context.account_snapshot.original) || !same(context.account_snapshot.owner_id, value.owner_id)
      || context.utility_policy.policy_ref !== s.bindings.utility.ref || context.utility_policy.hash !== s.bindings.utility.hash
      || context.account_snapshot.account_id !== identity.account_id || context.account_snapshot.account_kind !== identity.account_kind
      || context.position_context.state !== r.position_context || candidate.position_branch !== c.position_branch
      || context.position_context.instrument_id !== identity.instrument_id || !same(context.position_context.instrument_ref, identity.instrument_ref)) throw new Error('perpetual_advice_account_mismatch')
  } else if ([value.snapshot, value.revision, value.comparison, value.frozen_forecast, value.target].some(item => item !== null)) {
    throw new Error('perpetual_advice_unavailable_has_advice')
  }
  if (value.event_scenario_plan) {
    const event = value.event_scenario_plan, plan = event.plan, scope = event.review_scope
    const context = plan.context
    if (context.owner_id !== value.owner_id || context.account_kind !== selection.partition.account_kind
      || context.account_id !== selection.partition.account_id || context.instrument_id !== selection.partition.instrument_id
      || context.profile_ref.ref !== selection.partition.profile_ref || context.horizon !== selection.horizon
      || !same(context.settings_ref, selection.settings_revision) || !same(scope.plan_ref, event.plan_ref)
      || !same(scope.method_ref, plan.method_ref) || scope.method_code_hash !== plan.method_code_hash
      || scope.verdict !== 'approved' || Date.parse(scope.reviewed_at) >= Date.parse(plan.freeze_before)
      || (event.selection === null) !== (event.selection_ref === null)
      || (event.selection === null) !== (event.selection_original === null)
      || await hash(plan) !== event.plan_ref.hash) throw new Error('perpetual_advice_event_plan_binding_mismatch')
    for (const branch of plan.branches) {
      const { branch_ref, ...body } = branch
      if (await hash(body) !== branch_ref.hash) throw new Error('perpetual_advice_event_branch_hash_mismatch')
      for (const position of ['flat', 'long', 'short'] as const) {
        const result = branch.comparisons_by_position[position]
        if (await hash(result.comparison) !== result.comparison_ref.hash
          || await hash(branch.comparison_inputs_by_position[position]) !== result.inputs_ref.hash) throw new Error('perpetual_advice_event_comparison_hash_mismatch')
      }
    }
    if (event.selection && event.selection_original && event.selection_ref) {
      const actual = event.selection, original = event.selection_original
      if (!same(actual.plan_ref, event.plan_ref) || !same(original.selection, actual)
        || original.owner_id !== value.owner_id || original.origin_run_id !== event.origin_run_id
        || original.event_plan_id !== plan.plan_id || !same(original.publication_ref, event.publication_ref)
        || !same(actual.review_authorization_ref, event.review_authorization_ref)
        || Date.parse(actual.observed_at) > Date.parse(event.read_at)
        || await hash(original) !== event.selection_ref.hash) throw new Error('perpetual_advice_event_selection_hash_mismatch')
    }
  }
  await checkForecastBasis(value)
  requireOwner(selection.owner_id)
  return value
}
export const perpetualAdviceApi = {
  current: async (input: PerpetualAdviceSelection, signal?: AbortSignal) => {
    const selection = selectionSchema.parse(input); requireOwner(selection.owner_id)
    const query = new URLSearchParams({ ...selection.partition, expected_settings_ref: selection.settings_revision.ref, expected_settings_hash: selection.settings_revision.hash,
      expected_settings_state_version: String(selection.settings_state_version), horizon: selection.horizon })
    return checked(await request(`/v1/decision-settings/advice/current?${query}`, { method: 'GET', credentials: 'include', signal }), selection)
  },
}
