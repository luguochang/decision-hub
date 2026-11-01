import {
  perpetualSynthesisDraftV2Schema,
  type PerpetualSynthesisDraftV2, type ResearchDecisionSynthesisV3, type ActionReviewRequestV3,
} from '@decision-hub/contracts-ts'
import { prepareOrdinaryPerpetualSynthesis, derivedRef, type PerpetualPreparationOptions } from './perpetual-preparation.js'
import { bodyHash, canonicalJson, parsePerpetualSynthesis, parsePerpetualReviewPacket, validatePerpetualReviewOriginals, resolveEventScenarioPlan, eventScenarioReviewDomain } from './perpetual-synthesis-protocol.js'

/** The existing source-call binding reader supplies Python's exact calculation.
 * This boundary preserves it; no TS substitute for the frozen calculator exists.
 */
export function prepareEventPerpetualSynthesis(raw: unknown, options: PerpetualPreparationOptions) {
  const draft: PerpetualSynthesisDraftV2 = perpetualSynthesisDraftV2Schema.parse(raw)
  const catalog = options.review_originals
  const server = (role: string) => {
    const rows = catalog.originals.filter(row => row.source_kind === 'server_material' && row.role === role)
    if (rows.length !== 1) throw new Error('decision_hub_preparation_event_plan_original_missing')
    const row = rows[0]!, body = JSON.parse(row.body_json)
    if (canonicalJson(body) !== row.body_json || row.source_ref.hash !== bodyHash(body)) throw new Error('decision_hub_preparation_event_plan_original_mismatch')
    return body
  }
  const material = server('event_scenario_plan')
  if (material.source_draft_hash !== bodyHash(draft)) throw new Error('decision_hub_preparation_event_plan_source_draft_mismatch')
  const primaryCatalog = { ...catalog, originals: catalog.originals.filter(row => row.source_kind !== 'server_material' || !['event_scenario_plan', 'event_scenario_method'].includes(row.role)) }
  const primary = prepareOrdinaryPerpetualSynthesis(draft.primary_draft, { ...options, review_originals: primaryCatalog })
  const candidate = parsePerpetualSynthesis({ schema_version: 'research-decision-synthesis.v3',
    request_id: primary.candidate.request_id, opportunity_id: primary.candidate.opportunity_id,
    as_of: primary.candidate.as_of, known_at_max: primary.candidate.known_at_max,
    primary_v2: primary.candidate, primary_v2_ref: derivedRef('research_decision_synthesis', primary.candidate),
    event_plan_manifest_ref: material.event_plan_manifest_ref,
    event_plan_ref: material.event_plan_ref, event_plan_draft_ref: material.event_plan_draft_ref,
  }) as ResearchDecisionSynthesisV3
  const { plan } = resolveEventScenarioPlan(candidate, catalog)
  if (Date.parse(options.requested_at) >= Date.parse(plan.freeze_before)) throw new Error('decision_hub_preparation_event_plan_original_mismatch')
  const domain = eventScenarioReviewDomain(plan, candidate.event_plan_ref)
  const review_request = parsePerpetualReviewPacket({ schema_version: 'action-review-request.v3',
    synthesis: candidate, synthesis_ref: derivedRef('research_decision_synthesis', candidate),
    primary_review_request: primary.review_request, primary_review_request_ref: derivedRef('action_review_request', primary.review_request),
    event_plan_domain: domain, event_plan_domain_ref: derivedRef('event_scenario_review_domain', domain), requested_at: options.requested_at,
  }, candidate) as ActionReviewRequestV3
  validatePerpetualReviewOriginals(candidate, catalog)
  return { candidate, review_request, assumptions: primary.assumptions, source_draft_hash: bodyHash(draft),
    normalized_synthesis_hash: bodyHash(candidate), normalized_review_request_hash: bodyHash(review_request),
    primary_v2_hash: bodyHash(primary.candidate), primary_review_request_hash: bodyHash(primary.review_request),
    event_plan_draft_hash: material.event_plan_draft_ref.hash, event_plan_hash: bodyHash(plan) }
}
