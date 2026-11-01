import { z } from 'zod'
import { actionReviewAuthorizationV2Schema, actionReviewAuthorizationV3Schema, actionReviewRequestV2Schema, actionReviewRequestV3Schema, decisionModelAssumptionV1Schema, decisionReviewOriginalsV1Schema, decisionSourceRefV1Schema, researchDecisionSynthesisV2Schema, researchDecisionSynthesisV3Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { ApiRequestError, requestResponse } from './client'
import { settingsApi } from './decisionSettings'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { checkSavedPerpetualAction, savedActionSchema } from './perpetualSavedAction'
import { checkReportDelivery, deliveryBindingSchema, savedFindingsSchema } from './perpetualReportDelivery'

const id = z.string().min(1), sha = z.string().regex(/^[a-f0-9]{64}$/), at = z.string().datetime({ offset: true })
const observed = z.object({ output_id: id, role: id, schema_version: id, payload_hash: sha, recorded_at: at }).strict()
const packetBase = z.object({ schema_version: z.literal('perpetual-research-report.v1'),
  binding: z.object({ schema_version: z.literal('perpetual-report-binding.v1'), mapped_ref: observed, provider_emission_ref: observed, synthesis_ref: decisionSourceRefV1Schema, native_review_verdict: z.string().nullable(), financial_qualification: z.literal('unqualified') }).strict(),
  review_originals: decisionReviewOriginalsV1Schema, financial_qualified: z.literal(false),
}).strict()
const packetSchema = z.union([
  packetBase.extend({ synthesis: researchDecisionSynthesisV2Schema, review_request: actionReviewRequestV2Schema,
    authorization: actionReviewAuthorizationV2Schema.nullable() }).strict(),
  packetBase.extend({ synthesis: researchDecisionSynthesisV3Schema, review_request: actionReviewRequestV3Schema,
    authorization: actionReviewAuthorizationV3Schema.nullable() }).strict(),
  packetBase.extend({ schema_version: z.literal('perpetual-research-report.v2'), binding: deliveryBindingSchema,
    synthesis: researchDecisionSynthesisV2Schema, review_request: actionReviewRequestV2Schema,
    authorization: actionReviewAuthorizationV2Schema.nullable(), review_findings: savedFindingsSchema.nullable() }).strict(),
  packetBase.extend({ schema_version: z.literal('perpetual-research-report.v2'), binding: deliveryBindingSchema,
    synthesis: researchDecisionSynthesisV3Schema, review_request: actionReviewRequestV3Schema,
    authorization: actionReviewAuthorizationV3Schema.nullable(), review_findings: savedFindingsSchema.nullable() }).strict(),
])
const payloadSchema = z.object({ facts: z.array(z.string()), inferences: z.array(z.string()), counter_thesis: z.string(), uncertainty: z.array(z.string()), transmission_chain: z.array(z.string()), citations: z.array(z.string()), perpetual_synthesis: packetSchema }).strict()
const artifactSchema = z.object({ artifact_id: id, run_id: id, event_id: id, gate_status: z.literal('research_only'), headline: id, summary: z.string(), payload_json: id, created_at: at }).strict()
const definitionBinding = z.object({ owner_id: id, run_id: id, mapped_ref: observed, provider_emission_ref: observed, synthesis_ref: decisionSourceRefV1Schema }).strict()
const definitionSchema = z.object({ role: z.enum(['event_definition', 'occurrence_target', 'outcome_target']),
  definition_ref: decisionSourceRefV1Schema, definition: decisionModelAssumptionV1Schema }).strict()
const prepareEmission = observed.extend({ schema_version: z.literal('research-provider-tool-emission.v1'),
  output_id: z.string().regex(/^model_emit_[a-f0-9]{40}$/), role: z.string().regex(/^model_emit:[a-f0-9]{40}$/) }).strict()
const candidateDefinitionSchema = z.object({ role: z.enum(['entry_condition', 'exit_policy', 'basis']),
  definition_ref: decisionSourceRefV1Schema, kind: z.enum(['declared_assumption', 'review_original']), body_json: id }).strict()
const candidateDefinitionsSchema = z.object({ schema_version: z.literal('perpetual-candidate-definitions.v1'),
  binding: definitionBinding.extend({ prepare_emission_ref: prepareEmission.nullable() }).strict(),
  definitions: z.array(candidateDefinitionSchema) }).strict()
const probabilityDefinitionsSchema = z.discriminatedUnion('status', [
  z.object({ schema_version: z.literal('perpetual-probability-definitions.v1'), status: z.literal('available'),
    binding: definitionBinding.extend({ prepare_emission_ref: prepareEmission }).strict(),
    definitions: z.tuple([definitionSchema, definitionSchema, definitionSchema]) }).strict(),
  z.object({ schema_version: z.literal('perpetual-probability-definitions.v1'), status: z.literal('unavailable'),
    binding: definitionBinding, reason_code: z.literal('prepared_probability_definitions_unavailable') }).strict(),
])
const viewSchema = z.object({ schema_version: z.literal('perpetual-saved-report.v1'), read_scope: z.literal('full_saved_perpetual_original'),
  owner_id: id, run_id: id, event_id: id, artifact_id: id, source_json: id, source_sha256: sha, publication_ref: observed,
  artifact_json: id, artifact_sha256: sha, published_at: at, read_at: at,
  financial_qualified: z.literal(false), execution_authorized: z.literal(false), bundle_export_status: z.literal('unsupported_perpetual_version'),
  probability_definitions: probabilityDefinitionsSchema,
  candidate_definitions: candidateDefinitionsSchema.optional(),
  action_publication: savedActionSchema.optional(),
}).strict()
export type SavedPerpetualReport = z.infer<typeof viewSchema> & { artifact: z.infer<typeof artifactSchema>; payload: z.infer<typeof payloadSchema> }
export const primarySavedPerpetualSynthesis = (value: z.infer<typeof packetSchema>['synthesis']) =>
  value.schema_version === 'research-decision-synthesis.v3' ? value.primary_v2 : value
export const perpetualReportPath = (run: string) => `/v1/decision-settings/research/runs/${encodeURIComponent(run)}/report`
export type SavedReportReadErrorKind = 'identity' | 'integrity' | 'read'
export function classifySavedReportReadError(error: unknown): SavedReportReadErrorKind {
  if (error instanceof ApiRequestError) {
    if (error.kind === 'http' && (error.status === 401 || error.status === 403)) return 'identity'
    if (error.kind === 'http' && error.status === 409 && error.message === 'perpetual_report_original_unavailable') return 'integrity'
    return 'read'
  }
  if (error instanceof Error && error.message === 'perpetual_report_owner_changed') return 'identity'
  if (error instanceof z.ZodError || error instanceof SyntaxError
    || error instanceof Error && error.message.startsWith('perpetual_report_')) return 'integrity'
  return 'read'
}

async function textHash(value: string) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(bytes), item => item.toString(16).padStart(2, '0')).join('')
}
async function checkProbabilityDefinitions(view: z.infer<typeof viewSchema>, packet: z.infer<typeof packetSchema>) {
  const definitions = view.probability_definitions, binding = definitions.binding
  const same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
  if (binding.owner_id !== view.owner_id || binding.run_id !== view.run_id
    || !same(binding.mapped_ref, packet.binding.mapped_ref) || !same(binding.provider_emission_ref, packet.binding.provider_emission_ref)
    || !same(binding.synthesis_ref, packet.binding.synthesis_ref)) throw new Error('perpetual_report_definition_identity_mismatch')
  if (definitions.status === 'unavailable') return
  const prepare = definitions.binding.prepare_emission_ref
  if (prepare.role !== `model_emit:${prepare.output_id.slice('model_emit_'.length)}`
    || Date.parse(prepare.recorded_at) > Date.parse(binding.provider_emission_ref.recorded_at)) throw new Error('perpetual_report_definition_prepare_mismatch')
  const event = primarySavedPerpetualSynthesis(packet.synthesis).event_probability
  const roles = ['event_definition', 'occurrence_target', 'outcome_target'] as const
  const references = [event.event_definition_ref, event.occurrence_target_ref, event.outcome_target_ref]
  if (!same(event.occurrence_distribution.target_ref, references[1]) || !same(event.outcome_given_occurrence.target_ref, references[2])) throw new Error('perpetual_report_definition_target_mismatch')
  for (let index = 0; index < roles.length; index += 1) {
    const row = definitions.definitions[index], digest = await hash(row.definition)
    if (row.role !== roles[index] || row.definition.role !== roles[index] || !same(row.definition_ref, references[index])
      || row.definition_ref.hash !== digest || row.definition_ref.ref !== `decision_model_assumption_${digest}`) throw new Error('perpetual_report_definition_original_mismatch')
  }
}
async function checkCandidateDefinitions(view: z.infer<typeof viewSchema>, packet: z.infer<typeof packetSchema>) {
  const definitions = view.candidate_definitions
  if (!definitions) return // Historical responses remain readable, without invented definitions.
  const binding = definitions.binding, same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
  if (binding.owner_id !== view.owner_id || binding.run_id !== view.run_id
    || !same(binding.mapped_ref, packet.binding.mapped_ref) || !same(binding.provider_emission_ref, packet.binding.provider_emission_ref)
    || !same(binding.synthesis_ref, packet.binding.synthesis_ref)) throw new Error('perpetual_report_candidate_definition_identity_mismatch')
  const prepare = binding.prepare_emission_ref
  if (prepare && (prepare.role !== `model_emit:${prepare.output_id.slice('model_emit_'.length)}`
    || Date.parse(prepare.recorded_at) > Date.parse(binding.provider_emission_ref.recorded_at))) throw new Error('perpetual_report_candidate_definition_prepare_mismatch')
  const expected = new Set<string>(), seen = new Set<string>()
  for (const candidate of primarySavedPerpetualSynthesis(packet.synthesis).candidate_set.candidates) {
    for (const [role, references] of [['entry_condition', candidate.condition_refs], ['exit_policy', candidate.exit_ref ? [candidate.exit_ref] : []], ['basis', candidate.basis_refs]] as const) {
      for (const reference of references) expected.add(canonical([role, reference]))
    }
  }
  for (const row of definitions.definitions) {
    const key = canonical([row.role, row.definition_ref]), body: unknown = JSON.parse(row.body_json)
    if (!expected.has(key) || seen.has(key) || canonical(body) !== row.body_json) throw new Error('perpetual_report_candidate_definition_reference_mismatch')
    seen.add(key)
    if (row.kind === 'declared_assumption') {
      const parsed = decisionModelAssumptionV1Schema.parse(body), digest = await hash(parsed)
      if (!prepare || parsed.role !== row.role || row.definition_ref.hash !== digest
        || row.definition_ref.ref !== `decision_model_assumption_${digest}`) throw new Error('perpetual_report_candidate_definition_original_mismatch')
    } else {
      const direct = packet.review_originals.originals.filter(original => same(original.source_ref, row.definition_ref))
      const originals = direct.length ? direct : packet.review_originals.originals.filter(original => same(original.original_ref, row.definition_ref))
      if (new Set(originals.map(original => original.body_json)).size !== 1 || originals[0]?.body_json !== row.body_json) {
        throw new Error('perpetual_report_candidate_definition_original_mismatch')
      }
    }
  }
}
async function checkEventPacket(packet: z.infer<typeof packetSchema>) {
  const synthesis = packet.synthesis
  if (synthesis.schema_version !== 'research-decision-synthesis.v3') return
  const request = packet.review_request, authorization = packet.authorization
  if (request.schema_version !== 'action-review-request.v3'
    || authorization !== null && authorization.schema_version !== 'action-review-authorization.v3') throw new Error('perpetual_report_event_version_mismatch')
  const same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
  const bound = async (reference: { ref: string; hash: string }, body: unknown, prefix: string) => {
    const digest = await hash(body)
    return reference.hash === digest && reference.ref === prefix + digest
  }
  if (!await bound(synthesis.primary_v2_ref, synthesis.primary_v2, 'research_decision_synthesis_')
    || !same(request.primary_review_request.synthesis, synthesis.primary_v2)
    || !same(request.primary_review_request.synthesis_ref, synthesis.primary_v2_ref)
    || !await bound(request.primary_review_request_ref, request.primary_review_request, 'action_review_request_')
    || !same(request.event_plan_domain.plan_ref, synthesis.event_plan_ref)
    || !await bound(request.event_plan_domain_ref, request.event_plan_domain, 'event_scenario_review_domain_')) throw new Error('perpetual_report_event_original_mismatch')
  if (authorization !== null) {
    const scope = Object.fromEntries(Object.entries(authorization.event_plan_scope).filter(([key]) => !['schema_version', 'reviewed_at', 'verdict'].includes(key)))
    const outerRef = { ref: 'action_review_request_' + await hash(request), hash: await hash(request) }
    if (!same(authorization.review_request_ref, outerRef) || !same(authorization.reviewer_input_ref, outerRef)
      || !same(authorization.synthesis_ref, packet.binding.synthesis_ref)
      || !same(authorization.primary_authorization.review_request_ref, request.primary_review_request_ref)
      || !same(authorization.primary_authorization.reviewer_input_ref, request.primary_review_request_ref)
      || !same(authorization.primary_authorization.synthesis_ref, synthesis.primary_v2_ref)
      || !same(scope, request.event_plan_domain)
      || authorization.verdict !== authorization.primary_authorization.verdict || authorization.verdict !== authorization.event_plan_scope.verdict
      || authorization.reviewed_at !== authorization.primary_authorization.reviewed_at || authorization.reviewed_at !== authorization.event_plan_scope.reviewed_at
      || Date.parse(authorization.reviewed_at) >= Date.parse(request.event_plan_domain.freeze_before)) throw new Error('perpetual_report_event_review_mismatch')
  }
}
export async function readSavedPerpetualReport(run: string, signal?: AbortSignal): Promise<SavedPerpetualReport | null> {
  let raw: unknown
  try {
    raw = await requestResponse(perpetualReportPath(run), response => response.json(),
      { credentials: 'include', signal }, undefined, { timeoutMs: null })
  }
  catch (error) {
    if (error instanceof ApiRequestError && error.status === 404 && error.message === 'perpetual_report_not_applicable') return null
    throw error
  }
  const view = viewSchema.parse(raw), artifact = artifactSchema.parse(JSON.parse(view.artifact_json))
  const payload = payloadSchema.parse(JSON.parse(artifact.payload_json)), source = JSON.parse(view.source_json)
  const packet = payload.perpetual_synthesis
  const session = settingsApi.session()
  if (session && view.owner_id !== session.owner_id) throw new Error('perpetual_report_owner_changed')
  if (view.run_id !== run || artifact.run_id !== run || artifact.artifact_id !== view.artifact_id || artifact.event_id !== view.event_id
    || primarySavedPerpetualSynthesis(packet.synthesis).candidate_set.context.owner_id !== view.owner_id
    || source.schema_version !== (packet.schema_version === 'perpetual-research-report.v2' ? 'research-publication-source.v6' : 'research-publication-source.v5') || source.run_id !== run || source.event_id !== view.event_id || source.artifact_id !== view.artifact_id
    || view.publication_ref.role !== 'research_publication_source' || view.publication_ref.schema_version !== source.schema_version
    || view.publication_ref.output_id !== 'publication_' + view.source_sha256.slice(0, 40) || view.publication_ref.payload_hash !== view.source_sha256
    || view.source_sha256 !== await textHash(view.source_json) || canonical(source) !== view.source_json
    || view.artifact_sha256 !== await textHash(view.artifact_json) || canonical(artifact) !== view.artifact_json || source.artifact_hash !== await hash(artifact)
    || source.candidate.headline !== artifact.headline || source.candidate.summary !== artifact.summary
    || Object.entries(payload).some(([key, value]) => canonical(source.candidate[key]) !== canonical(value))
    || canonical(source.perpetual_binding) !== canonical(packet.binding)
    || canonical(packet.review_request.synthesis) !== canonical(packet.synthesis)
    || canonical(packet.review_request.synthesis_ref) !== canonical(packet.binding.synthesis_ref)
    || packet.binding.synthesis_ref.hash !== await hash(packet.synthesis)
    || packet.binding.native_review_verdict !== (packet.authorization?.verdict ?? null)
    || Date.parse(view.published_at) !== Date.parse(artifact.created_at) || Date.parse(view.published_at) !== Date.parse(view.publication_ref.recorded_at)
    || Date.parse(view.read_at) < Date.parse(view.published_at)) throw new Error('perpetual_report_original_mismatch')
  if (packet.schema_version === 'perpetual-research-report.v2') await checkReportDelivery(view, packet, source)
  await checkProbabilityDefinitions(view, packet)
  await checkCandidateDefinitions(view, packet)
  await checkEventPacket(packet)
  await checkSavedPerpetualAction(view.action_publication, { ...view, packet })
  const current = settingsApi.session()
  if (current && current.owner_id !== view.owner_id) throw new Error('perpetual_report_owner_changed')
  return { ...view, artifact, payload }
}
