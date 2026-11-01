import {
  researchDecisionSynthesisV1Schema, researchSynthesisCandidateSchema,
  type ResearchDecisionSynthesisV1, type ResearchSynthesisCandidate,
} from '@decision-hub/contracts-ts'

export const LEGACY_SYNTHESIS_REF = 'agentic_research.schema.yaml#/$defs/research_session_result'
export const DECISION_SYNTHESIS_REF = 'research_signal.schema.yaml#/$defs/research_decision_synthesis_v1'
export type SynthesisProtocol = 'legacy' | 'decision' | 'decision_v2'
export type SynthesisCandidate = ResearchSynthesisCandidate | ResearchDecisionSynthesisV1

/** Only frozen server request selection grants use of the new wire protocol. */
export function synthesisProtocol(ref: string): SynthesisProtocol {
  if (ref === LEGACY_SYNTHESIS_REF || ref === 'research-session-result.v1') return 'legacy'
  if (ref === DECISION_SYNTHESIS_REF) return 'decision'
  if (ref === 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2' || ref === 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3') return 'decision_v2'
  throw new Error('decision_hub_synthesis_protocol_unsupported')
}
export function isDecisionCandidate(value: SynthesisCandidate): value is ResearchDecisionSynthesisV1 {
  return value.schema_version === 'research-decision-synthesis.v1'
}
export function researchCandidate(value: SynthesisCandidate): ResearchSynthesisCandidate {
  return isDecisionCandidate(value) ? value.research_candidate_v1 : value
}
export function parseSynthesisCandidate(ref: string, value: unknown, normalize: (input: unknown) => unknown): SynthesisCandidate {
  if (synthesisProtocol(ref) === 'decision_v2') throw new Error('decision_hub_explicit_perpetual_synthesis_parser_required')
  if (synthesisProtocol(ref) === 'legacy') return researchSynthesisCandidateSchema.parse(normalize(value))
  const row = typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null
  // Representation repair is confined to the old nested object; never repair
  // action thresholds, evidence, identities, ordering or generated methods.
  const result = researchDecisionSynthesisV1Schema.parse(row
    ? { ...row, research_candidate_v1: normalize(row.research_candidate_v1) } : value)
  assertDecisionSynthesisRelations(result)
  assertDecisionActionCoverage(result)
  return result
}

/** Product completeness gate; shape parsing alone cannot satisfy both positions. */
export function assertDecisionActionCoverage(value: ResearchDecisionSynthesisV1): void {
  const scopes = [...new Set([...value.action_intents.map(item => item.scope_id), ...value.mechanism_reviews.map(item => item.scope_id)])]
  const horizons = value.research_candidate_v1.horizons.map(item => item.horizon)
  const issues: { scope_id: string | null; horizon: string | null; position_context: string | null; code: string }[] = []
  if (!scopes.length || !value.action_intents.length || !value.mechanism_reviews.length) {
    issues.push({ scope_id: scopes[0] ?? null, horizon: null, position_context: null, code: 'complete_action_and_mechanism_required' })
  }
  for (const scope of scopes) for (const horizon of horizons) for (const position of ['flat', 'long']) {
    if (!value.action_intents.some(item => item.scope_id === scope && item.horizon === horizon && item.position_context === position)) {
      issues.push({ scope_id: scope, horizon, position_context: position, code: 'declared_position_action_missing' })
    }
  }
  if (issues.length) throw new Error(`decision_hub_synthesis_action_coverage_invalid:${JSON.stringify({ issues: issues.slice(0, 16), omitted_issue_count: Math.max(0, issues.length - 16) })}; Supply a separate flat and long action for each declared research horizon in the complete wrapper. If evidence is insufficient, keep the corresponding branch with an explicit wait/hold choice and its actual unresolved basis, rather than deleting it. Do not invent prices, confidence, evidence, or reuse a prior review for changed content.`)
}

/** Pure consistency only; registered features/policies/native claim receipts
 * still require the server-owned application admission before publication. */
export function assertDecisionSynthesisRelations(value: ResearchDecisionSynthesisV1): void {
  if (value.request_id !== value.research_candidate_v1.request_id) throw new Error('decision_hub_synthesis_request_mismatch')
  const reviews = new Map(value.mechanism_reviews.map(item => [item.review_id, item]))
  if (reviews.size !== value.mechanism_reviews.length) throw new Error('decision_hub_synthesis_duplicate_review')
  const branches = value.action_intents.map(item => JSON.stringify([item.scope_id, item.horizon, item.position_context]))
  if (new Set(branches).size !== branches.length) throw new Error('decision_hub_synthesis_duplicate_action_branch')
  for (const review of value.mechanism_reviews) {
    const nodes = new Set(review.nodes.map(item => item.id)), edges = new Set(review.edges.map(item => item.id))
    if (nodes.size !== review.nodes.length || edges.size !== review.edges.length) throw new Error('decision_hub_synthesis_mechanism_duplicate_identity')
    if (review.edges.some(item => !nodes.has(item.from_node) || !nodes.has(item.to_node) || item.from_node === item.to_node
      || item.lag !== null && item.lag.minimum_seconds > item.lag.maximum_seconds)) throw new Error('decision_hub_synthesis_mechanism_edge_invalid')
    if (review.priced_in_status !== 'unknown' && review.priced_in_refs.length === 0) throw new Error('decision_hub_synthesis_priced_in_refs_required')
  }
  const claimIds = new Set(value.claim_signal_refs)
  if (claimIds.size !== value.claim_signal_refs.length || [...claimIds].some(id => !/^claim_[A-Za-z0-9_-]+$/.test(id))) {
    throw new Error('decision_hub_synthesis_claim_reference_invalid')
  }
  const skillIds = value.skill_consumption_refs.map(item => item.version_id)
  if (new Set(skillIds).size !== skillIds.length) throw new Error('decision_hub_synthesis_skill_reference_invalid')
  const bindingIssues: { loc: string[]; code: string; actual: string; compared_to: string; expected: string | null }[] = []
  for (const [index, action] of value.action_intents.entries()) {
    const review = reviews.get(action.mechanism_review_ref)
    const location = ['action_intents', String(index)]
    if (!review) {
      bindingIssues.push({ loc: [...location, 'mechanism_review_ref'], code: 'mechanism_review_not_found',
        actual: action.mechanism_review_ref, compared_to: 'mechanism_reviews[].review_id', expected: null })
      continue
    }
    for (const [field, other] of [['scope_id', 'scope_id'], ['horizon', 'horizon'], ['as_of', 'cutoff']] as const) {
      const matches = field === 'as_of' ? Date.parse(action[field]) === Date.parse(review[other]) : action[field] === review[other]
      if (!matches) bindingIssues.push({ loc: [...location, field], code: 'mechanism_field_mismatch',
        actual: action[field], compared_to: `mechanism_reviews[review_id=${review.review_id}].${other}`, expected: review[other] })
    }
  }
  // The existing native publisher has one producer_cutoff for the complete
  // reviewed wrapper. Surface this requirement before spending its review.
  const cutoffs = new Set([...value.action_intents.map(item => Date.parse(item.as_of)), ...value.mechanism_reviews.map(item => Date.parse(item.cutoff))])
  if (cutoffs.size > 1 && !bindingIssues.length) {
    const counterpart = value.action_intents[0]?.as_of ?? value.mechanism_reviews[0]!.cutoff
    for (const [index, action] of value.action_intents.entries()) if (Date.parse(action.as_of) !== Date.parse(counterpart)) {
      bindingIssues.push({ loc: ['action_intents', String(index), 'as_of'], code: 'wrapper_information_cutoff_mismatch',
        actual: action.as_of, compared_to: 'one producer_cutoff for the complete reviewed wrapper', expected: counterpart })
    }
    for (const [index, review] of value.mechanism_reviews.entries()) if (Date.parse(review.cutoff) !== Date.parse(counterpart)) {
      bindingIssues.push({ loc: ['mechanism_reviews', String(index), 'cutoff'], code: 'wrapper_information_cutoff_mismatch',
        actual: review.cutoff, compared_to: 'one producer_cutoff for the complete reviewed wrapper', expected: counterpart })
    }
  }
  if (bindingIssues.length) throw new Error(`decision_hub_synthesis_mechanism_binding_invalid:${JSON.stringify({ issues: bindingIssues.slice(0, 16), omitted_issue_count: Math.max(0, bindingIssues.length - 16) })}; The compared value is the submitted counterpart, not an authenticated replacement. Keep the complete decision wrapper and all required position branches. Use one actual information cutoff shared by ALL action.as_of and mechanism.cutoff fields in this wrapper, using only evidence available by that cutoff; market observed_at and acquisition/execution deadlines are different fields. Correct the inconsistent fields before review; do not delete actions, use a missing reference, move the cutoff into the future, or reuse an earlier review for changed content.`)
  for (const [actionIndex, action] of value.action_intents.entries()) {
    if (action.claim_refs.some(id => !claimIds.has(id))) throw new Error('decision_hub_synthesis_claim_binding_invalid')
    if (!(Date.parse(action.as_of) < Date.parse(action.next_review_at)
      && Date.parse(action.next_review_at) <= Date.parse(action.valid_until))) throw new Error('decision_hub_synthesis_action_window_invalid')
    const conditions = [...action.entry, ...action.exit, ...action.invalidation]
    if (new Set(conditions.map(item => item.id)).size !== conditions.length) throw new Error('decision_hub_synthesis_condition_duplicate')
    const issues: { loc: string[]; code: string; actual: string; allowed: readonly string[] }[] = []
    for (const [name, purposes] of [['entry', ['entry']], ['exit', ['exit', 'risk_upgrade']], ['invalidation', ['invalidation']]] as const) {
      action[name].forEach((item, conditionIndex) => {
        const loc = ['action_intents', String(actionIndex), name, String(conditionIndex)]
        if (!(purposes as readonly string[]).includes(item.purpose)) issues.push({ loc: [...loc, 'purpose'],
          code: 'condition_purpose_invalid', actual: item.purpose, allowed: purposes })
        if (!(Date.parse(action.as_of) < Date.parse(item.valid_until) && Date.parse(item.valid_until) <= Date.parse(action.valid_until))) {
          issues.push({ loc: [...loc, 'valid_until'], code: 'condition_window_invalid', actual: item.valid_until,
            allowed: [`after ${action.as_of} and no later than ${action.valid_until}`] })
        }
      })
    }
    if (issues.length) throw new Error(`decision_hub_synthesis_condition_binding_invalid:${JSON.stringify({ issues: issues.slice(0, 16), omitted_issue_count: Math.max(0, issues.length - 16) })}; Correct only the reported fields. risk_upgrade belongs in exit; invalidation accepts only invalidation. Do not change a reviewed candidate to reuse its receipt.`)
  }
}
