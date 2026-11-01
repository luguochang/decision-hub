import {
  perpetualSynthesisDraftV1Schema, type PerpetualSynthesisDraftV1, type DecisionModelAssumptionV1,
  type DecisionReviewOriginalsV1, type ResearchDecisionSynthesisV2, type ActionReviewRequestV2,
} from '@decision-hub/contracts-ts'
import { z } from 'zod'
import { bodyHash, canonicalJson, parsePerpetualSynthesis, parsePerpetualReviewPacket, validatePerpetualReviewOriginals } from './perpetual-synthesis-protocol.js'
import { primaryPerpetualSynthesis, type PerpetualSynthesis } from './perpetual-synthesis-protocol.js'
import { prepareEventPerpetualSynthesis } from './perpetual-event-preparation.js'

export interface PerpetualPreparationOptions {
  request_id: string
  binding: Pick<ResearchDecisionSynthesisV2, 'opportunity_id' | 'method_ref' | 'method_code_hash'> & Pick<ResearchDecisionSynthesisV2['candidate_set'], 'context' | 'candidate_target_ref' | 'anchor_rule_ref'>
  review_originals: DecisionReviewOriginalsV1
  requested_at: string
}
export const derivedRef = (prefix: string, body: unknown) => ({ ref: `${prefix}_${bodyHash(body)}`, hash: bodyHash(body) })
const CURRENT_PERPETUAL_METHOD_REF = 'decision-method:perpetual-assumed-unit-mean-net.v1'
const CURRENT_PERPETUAL_METHOD_CODE_HASH = '49e4ab99ae4a86616fbd335316ccc9d9a78b26e3c5ed168ff3df97e707011d03'

function authenticatedServerOriginal(catalog: DecisionReviewOriginalsV1, role: string, errorCode: string) {
  const rows = catalog.originals.filter(row => row.source_kind === 'server_material' && row.role === role)
  if (rows.length !== 1) throw new Error(errorCode)
  const row = rows[0]!
  let body: any
  try { body = JSON.parse(row.body_json) } catch { throw new Error(errorCode) }
  if (row.body_json !== canonicalJson(body) || row.source_ref.hash !== bodyHash(body)) throw new Error(errorCode)
  return { row, body, body_hash: bodyHash(body) }
}

/**
 * Live-only early mirror of the current publisher's selected-branch cost binding.
 * Python publication remains authoritative; this function neither ranks nor repairs.
 */
export function assertCurrentPerpetualPublicationCostBinding(
  value: PerpetualSynthesis,
  reviewOriginals: DecisionReviewOriginalsV1,
): void {
  const candidate = primaryPerpetualSynthesis(value)
  if (candidate.method_ref.ref !== CURRENT_PERPETUAL_METHOD_REF
    || candidate.method_code_hash !== CURRENT_PERPETUAL_METHOD_CODE_HASH) return

  const method = authenticatedServerOriginal(
    reviewOriginals, 'comparison_method', 'decision_hub_preparation_comparison_method_original_invalid',
  )
  if (method.body.schema_version !== 'perpetual-comparison-method.v1'
    || method.body.selection_kind !== 'assumed_unit_mean_net'
    || method.body.method_code_hash !== CURRENT_PERPETUAL_METHOD_CODE_HASH
    || method.body.method_code_hash !== candidate.method_code_hash
    || method.body_hash !== candidate.method_ref.hash
    || canonicalJson(method.row.original_ref) !== canonicalJson(candidate.method_ref)) {
    throw new Error('decision_hub_preparation_comparison_method_original_invalid')
  }

  const position = authenticatedServerOriginal(
    reviewOriginals, 'position_context', 'decision_hub_preparation_position_original_invalid',
  )
  const cost = authenticatedServerOriginal(
    reviewOriginals, 'cost_policy', 'decision_hub_preparation_cost_policy_original_invalid',
  )
  if (!cost.row.original_ref || cost.row.original_ref.hash !== cost.body_hash) {
    throw new Error('decision_hub_preparation_cost_policy_original_invalid')
  }
  const state = position.body?.state
  if (!['unknown', 'flat', 'long', 'short'].includes(state)) {
    throw new Error('decision_hub_preparation_position_state_invalid')
  }
  const selectedBranch = state === 'unknown' ? 'flat' : state
  const issues: z.ZodIssue[] = []
  candidate.candidate_set.candidates.forEach((row, index) => {
    if (row.position_branch !== selectedBranch || canonicalJson(row.cost_ref) === canonicalJson(cost.row.original_ref)) return
    issues.push({
      code: z.ZodIssueCode.custom,
      path: ['draft', 'candidates', index, 'cost_ref'],
      message: `decision_hub_preparation_candidate_cost_original_missing: candidate_id=${row.candidate_id}; selected_position_branch=${selectedBranch}; /draft/candidates/${index}/cost_ref must equal the exact authenticated catalog cost_policy.original_ref ${canonicalJson(cost.row.original_ref)}, not cost_policy.source_ref; no value was inferred or inserted.`,
    })
  })
  if (issues.length) throw new z.ZodError(issues)
}

/** Mechanical references and authenticated research-unit scope, without economic judgment. */
export function preparePerpetualSynthesis(raw: unknown, options: PerpetualPreparationOptions) {
  return (raw as { schema_version?: string } | null)?.schema_version === 'perpetual-synthesis-draft.v2'
    ? prepareEventPerpetualSynthesis(raw, options) : prepareOrdinaryPerpetualSynthesis(raw, options)
}

export function prepareOrdinaryPerpetualSynthesis(raw: unknown, options: PerpetualPreparationOptions) {
  const draft: PerpetualSynthesisDraftV1 = perpetualSynthesisDraftV1Schema.parse(raw), catalog = options.review_originals
  const server = (role: string): any => {
    const rows = catalog.originals.filter(row => row.source_kind === 'server_material' && row.role === role)
    if (rows.length !== 1) throw new Error('decision_hub_preparation_server_original_missing')
    const row = rows[0]!, body = JSON.parse(row.body_json)
    if (canonicalJson(body) !== row.body_json || bodyHash(body) !== row.source_ref.hash) throw new Error('decision_hub_preparation_server_body_mismatch')
    return body
  }
  const target = server('candidate_target'), rule = server('anchor_rule'), policy = server('allowed_domain_policy'), binding = options.binding
  const costAssumptions = server('cost_assumptions')
  const costPolicy = authenticatedServerOriginal(catalog, 'cost_policy', 'decision_hub_preparation_cost_policy_original_invalid')
  const researchUnit = costAssumptions.schema_version === 'perpetual-cost-assumptions.v1' && costAssumptions.purpose === 'research_unit_only'
    ? z.object({ schema_version: z.literal('perpetual-cost-assumptions.v1'), purpose: z.literal('research_unit_only'),
      unit_notional_usdt: z.string().regex(/^(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?$/)
        .refine(value => !/^0(?:\.0+)?$/.test(value)) }).passthrough().parse(costAssumptions).unit_notional_usdt
    : null
  const boundResearchUnit = researchUnit !== null && costPolicy.row.original_ref !== null
    && costPolicy.row.original_ref.hash === costPolicy.body_hash
    && canonicalJson(costAssumptions.base_cost_ref) === canonicalJson(costPolicy.row.original_ref)
    && canonicalJson(costAssumptions.base_cost_policy) === canonicalJson(costPolicy.body)
  if (bodyHash(target) !== binding.candidate_target_ref.hash || bodyHash(rule) !== binding.anchor_rule_ref.hash
    || canonicalJson(policy.method_ref) !== canonicalJson(binding.method_ref) || policy.method_code_hash !== binding.method_code_hash) throw new Error('decision_hub_preparation_server_binding_mismatch')
  if (Date.parse(draft.valid_until) > Math.min(Date.parse(target.valid_until), Date.parse(policy.valid_until))) throw new Error('decision_hub_preparation_expiry_outside_domain')
  const available = new Set(catalog.originals.flatMap(row => [canonicalJson(row.source_ref), ...(row.original_ref ? [canonicalJson(row.original_ref)] : [])]))
  for (const value of [binding.candidate_target_ref, binding.anchor_rule_ref, binding.method_ref, ...Object.entries(binding.context).filter(([key]) => key.endsWith('_ref')).map(([,value]) => value), target.conditioning_ref]) if (value) available.add(canonicalJson(value))
  const requireOriginal = (reference: any) => {
    if (reference !== null && !available.has(canonicalJson(reference))) throw new Error('decision_hub_preparation_original_unavailable')
    return reference
  }
  const assumptions = new Map<string, DecisionModelAssumptionV1>()
  for (const row of draft.assumptions) {
    if (assumptions.has(row.assumption_id)) throw new Error('decision_hub_preparation_duplicate_assumption')
    row.evidence_refs.forEach(requireOriginal); assumptions.set(row.assumption_id, row)
  }
  const resolve = (reference: any, roles: readonly string[]): any => {
    if (reference === null) return null
    if ('local_id' in reference) {
      const body = assumptions.get(reference.local_id)
      if (!body) throw new Error('decision_hub_preparation_assumption_unavailable')
      if (!roles.includes(body.role)) throw new Error('decision_hub_preparation_assumption_role_mismatch')
      return derivedRef('decision_model_assumption', body)
    }
    return requireOriginal(reference)
  }
  const estimate = (value: any, targetRole: string) => ({ ...value, target_ref: resolve(value.target_ref, [targetRole]), source_ref: resolve(value.source_ref, ['probability_source']), conditioning_ref: resolve(value.conditioning_ref, ['conditioning','event_definition']), model_ref: requireOriginal(value.model_ref), calibration_ref: requireOriginal(value.calibration_ref) })
  const byId = new Map(draft.candidates.map(row => [row.candidate_id, row])), built = new Map<string, any>(), visiting = new Set<string>()
  if (byId.size !== draft.candidates.length) throw new Error('decision_hub_preparation_duplicate_candidate')
  const candidateBody = (id: string): any => {
    if (visiting.has(id)) throw new Error('decision_hub_preparation_candidate_cycle')
    if (built.has(id)) return built.get(id)
    const row = byId.get(id)
    if (!row) throw new Error('decision_hub_preparation_candidate_child_unavailable')
    visiting.add(id)
    const { nested_candidate_id, ...fields } = row
    const nested = nested_candidate_id === null ? null : derivedRef('action_candidate', candidateBody(nested_candidate_id))
    const body = { ...fields, schema_version:'action-candidate.v2', context:binding.context, valid_until:draft.valid_until, execution:'paper_only', nested_action_ref:nested,
      unit_notional_usdt:boundResearchUnit && row.size_mode === 'unit_notional'
        && row.quantity_before === null && row.quantity_after === null
        && canonicalJson(row.cost_ref) === canonicalJson(costPolicy.row.original_ref) ? researchUnit : row.unit_notional_usdt,
      condition_refs:row.condition_refs.map(ref=>resolve(ref,['entry_condition'])), size_policy_ref:resolve(row.size_policy_ref,['size_policy']), exit_ref:resolve(row.exit_ref,['exit_policy']), target_refs:row.target_refs.map(ref=>resolve(ref,['action_target'])), basis_refs:row.basis_refs.map(ref=>resolve(ref,['basis'])),
      cost_ref:requireOriginal(row.cost_ref), funding_ref:requireOriginal(row.funding_ref), margin_ref:requireOriginal(row.margin_ref), fill_ref:requireOriginal(row.fill_ref) }
    built.set(id,body); visiting.delete(id); return body
  }
  const candidates = draft.candidates.map(row => candidateBody(row.candidate_id))
  const set = {schema_version:'action-candidate-set.v2',candidate_set_id:draft.candidate_set_id,context:binding.context,as_of:draft.as_of,valid_until:draft.valid_until,
    candidate_target_ref:binding.candidate_target_ref,anchor_rule_ref:binding.anchor_rule_ref,candidates,candidate_refs:candidates.map(row=>derivedRef('action_candidate',row))}
  const event = { ...draft.event_probability, schema_version:'event-probability.v1',
    event_definition_ref:resolve(draft.event_probability.event_definition_ref,['event_definition']), occurrence_target_ref:resolve(draft.event_probability.occurrence_target_ref,['occurrence_target']),outcome_target_ref:resolve(draft.event_probability.outcome_target_ref,['outcome_target']),joint_partition_ref:resolve(draft.event_probability.joint_partition_ref,['joint_partition']),observation_rule_ref:resolve(draft.event_probability.observation_rule_ref,['observation_rule']),
    occurrence_distribution:estimate(draft.event_probability.occurrence_distribution,'occurrence_target'),outcome_given_occurrence:estimate(draft.event_probability.outcome_given_occurrence,'outcome_target')}
  if (event.outcome_given_occurrence.kind === 'unavailable'
    && event.outcome_given_occurrence.probabilities === null
    && event.outcome_given_occurrence.conditioning_ref === null) {
    event.outcome_given_occurrence.conditioning_ref = event.event_definition_ref
  }
  // The same canonical EventProbability relation, located at its authored
  // field so a rejected tool call can be corrected without changing economics.
  if (canonicalJson(event.outcome_given_occurrence.conditioning_ref) !== canonicalJson(event.event_definition_ref)) {
    throw new z.ZodError([{ code: z.ZodIssueCode.custom,
      path: ['draft', 'event_probability', 'outcome_given_occurrence', 'conditioning_ref'],
      message: 'decision_v2_comparison_event_target: outcome_given_occurrence.conditioning_ref must equal event_probability.event_definition_ref, including when kind=unavailable and probabilities=null. This identifies the conditional target; it does not supply a probability or require market_event_conditioned=true.',
    }])
  }
  const eventRef=derivedRef('event_probability',event)
  const paths={...draft.path_distribution,market_target_ref:binding.candidate_target_ref,conditioning_ref:target.conditioning_ref,fill_weight_assumption_ref:resolve(draft.path_distribution.fill_weight_assumption_ref,['fill_weight_assumption'])}
  const pathsRef=derivedRef('assumed_unit_paths',paths)
  const direction={...draft.direction_distribution,target_ref:binding.candidate_target_ref,conditioning_ref:target.conditioning_ref,source_ref:resolve(draft.direction_distribution.source_ref,['probability_source']),model_ref:requireOriginal(draft.direction_distribution.model_ref),calibration_ref:requireOriginal(draft.direction_distribution.calibration_ref)}
  const market={schema_version:'conditional-market-distribution.v1',instrument_ref:binding.context.instrument_ref,market_target_ref:binding.candidate_target_ref,event_scenario_ref:draft.market_event_conditioned?eventRef:null,conditioning_ref:target.conditioning_ref,path_distribution_ref:pathsRef,direction_distribution:direction}
  const setRef=derivedRef('action_candidate_set',set), marketRef=derivedRef('market_distribution',market)
  const domain={schema_version:'action-comparison-allowed-domain.v1',candidate_set_ref:setRef,candidate_target_ref:binding.candidate_target_ref,anchor_rule_ref:binding.anchor_rule_ref,event_probability_ref:eventRef,market_distribution_ref:marketRef,
    method_ref:policy.method_ref,method_code_hash:policy.method_code_hash,variables:policy.variables,invalidation_refs:policy.invalidation_refs,valid_until:policy.valid_until}
  const domainRef=derivedRef('action_allowed_domain',domain)
  draft.economic_input_refs.forEach(requireOriginal); draft.reasoning_refs.forEach(requireOriginal)
  const candidate=parsePerpetualSynthesis({schema_version:'research-decision-synthesis.v2',request_id:options.request_id,opportunity_id:binding.opportunity_id,as_of:draft.as_of,known_at_max:draft.known_at_max,
    candidate_set:set,candidate_set_ref:setRef,candidate_target:target,anchor_rule:rule,event_probability:event,event_probability_ref:eventRef,market_distribution:market,market_distribution_ref:marketRef,path_distribution:paths,
    economic_input_refs:[...draft.economic_input_refs,pathsRef],method_ref:binding.method_ref,method_code_hash:binding.method_code_hash,allowed_domain_ref:domainRef,comparison_ref:null,target_binding:'pending_anchor',reasoning_refs:draft.reasoning_refs,
    analysis:{...draft.analysis,schema_version:'decision-causal-analysis.v1',candidate_target_ref:binding.candidate_target_ref},gaps:draft.gaps}) as ResearchDecisionSynthesisV2
  validatePerpetualReviewOriginals(candidate,catalog)
  const review_request=parsePerpetualReviewPacket({schema_version:'action-review-request.v2',synthesis_ref:derivedRef('research_decision_synthesis',candidate),synthesis:candidate,allowed_domain_ref:domainRef,allowed_domain:domain,requested_at:options.requested_at},candidate) as ActionReviewRequestV2
  return {candidate,review_request,assumptions:draft.assumptions,source_draft_hash:bodyHash(draft),normalized_synthesis_hash:bodyHash(candidate),normalized_review_request_hash:bodyHash(review_request)}
}
