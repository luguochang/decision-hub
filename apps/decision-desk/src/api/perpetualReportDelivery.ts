/** Non-financial V6 report identities; actual review proof stays on the original server. */
import { z } from 'zod'
import { decisionReviewOriginalsV1Schema, decisionSourceRefV1Schema, perpetualReviewFindingsV1Schema, perpetualReviewFindingsV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'

const id = z.string().min(1), sha = z.string().regex(/^[a-f0-9]{64}$/)
const observed = z.object({ output_id: id, role: id, schema_version: id, payload_hash: sha, recorded_at: z.string().datetime({ offset: true }) }).strict()
const deliverySchema = z.object({ schema_version: z.literal('perpetual-report-delivery.v1'), run_id: id,
  decision_at: z.string().datetime({ offset: true }), status: z.enum(['complete', 'incomplete']), reasons: z.array(id),
  input_hashes: z.object({ synthesis: sha, review_request: sha, authorization: sha, findings: sha, catalog: sha, source_binding: sha }).strict(),
  source_refs: z.object({ mapped_ref: observed, provider_emission_ref: observed, synthesis_ref: decisionSourceRefV1Schema }).strict(),
  key_evidence_refs: z.array(decisionSourceRefV1Schema), counter_evidence_refs: z.array(decisionSourceRefV1Schema),
  financial_qualified: z.literal(false), execution_authorized: z.literal(false),
}).strict()
export const deliveryBindingSchema = z.object({ schema_version: z.literal('perpetual-report-binding.v2'),
  mapped_ref: observed, provider_emission_ref: observed, synthesis_ref: decisionSourceRefV1Schema,
  native_review_verdict: z.string().nullable(), financial_qualification: z.literal('unqualified'),
  native_review_wrapper_sha256: sha.nullable(), report_delivery: deliverySchema,
}).strict()
export const savedFindingsSchema = z.union([perpetualReviewFindingsV1Schema, perpetualReviewFindingsV2Schema])

type Packet = { binding: z.infer<typeof deliveryBindingSchema>; synthesis: unknown; review_request: unknown;
  authorization: { verdict: string; review_request_ref: z.infer<typeof decisionSourceRefV1Schema> } | null;
  review_findings: z.infer<typeof savedFindingsSchema> | null; review_originals: z.infer<typeof decisionReviewOriginalsV1Schema> }
type AssessmentRef = z.infer<typeof decisionSourceRefV1Schema>
type PrimaryAssessmentRefs = { market_distribution: { direction_distribution: { source_ref: AssessmentRef } };
  candidate_set: { candidates: { condition_refs: AssessmentRef[]; exit_ref: AssessmentRef; basis_refs: AssessmentRef[] }[] } }

export async function checkReportDelivery(view: { run_id: string; published_at: string }, packet: Packet,
  source: { report_delivery: unknown; candidate: { research_complete: unknown } }) {
  const binding = packet.binding, delivery = binding.report_delivery
  const same = (left: unknown, right: unknown) => canonical(left) === canonical(right)
  const reject = () => { throw new Error('perpetual_report_delivery_original_mismatch') }
  const complete = delivery.status === 'complete'
  if (delivery.run_id !== view.run_id || Date.parse(delivery.decision_at) !== Date.parse(view.published_at)
    || !same(source.report_delivery, delivery) || source.candidate.research_complete !== complete
    || complete !== (delivery.reasons.length === 0)
    || (packet.authorization === null) !== (binding.native_review_wrapper_sha256 === null)
    || !same(delivery.source_refs, { mapped_ref: binding.mapped_ref, provider_emission_ref: binding.provider_emission_ref,
      synthesis_ref: binding.synthesis_ref })) reject()
  const sourceBinding = Object.fromEntries(Object.entries(binding).filter(([key]) => key !== 'report_delivery'))
  sourceBinding.schema_version = 'perpetual-report-binding.v1'
  const inputs = { synthesis: packet.synthesis, review_request: packet.review_request, authorization: packet.authorization,
    findings: packet.review_findings, catalog: packet.review_originals, source_binding: sourceBinding }
  for (const key of ['synthesis', 'review_request', 'authorization', 'findings', 'catalog', 'source_binding'] as const) {
    if (delivery.input_hashes[key] !== await hash(inputs[key])) reject()
  }
  const originals = packet.review_originals.originals
  const available = new Map(originals.flatMap(row => [row.source_ref, row.original_ref]
    .filter((ref): ref is NonNullable<typeof ref> => ref !== null).map(ref => [canonical(ref), row] as const)))
  for (const ref of [...delivery.key_evidence_refs, ...delivery.counter_evidence_refs]) {
    if (!available.has(canonical(ref))) reject()
  }
  const findings = packet.review_findings
  if (findings !== null && (packet.authorization === null
    || !same(findings.review_request_ref, packet.authorization.review_request_ref))) reject()
  if (findings?.schema_version === 'perpetual-review-findings.v2') {
    // The original reader has authenticated this packet; only exact uses of
    // its primary declarations supplement the catalog, never evidence counts.
    const synthesis = packet.synthesis as PrimaryAssessmentRefs & { schema_version: string; primary_v2?: PrimaryAssessmentRefs }
    const primary = synthesis.schema_version === 'research-decision-synthesis.v3' ? synthesis.primary_v2 : synthesis
    const direction = primary?.market_distribution.direction_distribution.source_ref
    const protection = primary?.candidate_set.candidates.flatMap(candidate =>
      [...candidate.condition_refs, candidate.exit_ref, ...candidate.basis_refs]) ?? []
    for (const [name, check] of Object.entries(findings.report_assessment)) {
      if (check.evidence_refs.some(ref => !available.has(canonical(ref))
        && !(name === 'probability_completeness' && same(ref, direction))
        && !(name === 'cost_protection_and_conditions' && protection.some(bound => same(ref, bound))))) reject()
    }
  }
  if (!complete) return
  const countEvidence = (refs: z.infer<typeof decisionSourceRefV1Schema>[]) => new Set(refs.map(ref => {
    const row = available.get(canonical(ref))
    if (!row || !['frozen_input', 'run_evidence'].includes(row.source_kind)) return null
    const body = JSON.parse(row.body_json)
    return typeof body.content_hash === 'string' && /^[a-f0-9]{64}$/.test(body.content_hash) ? body.content_hash : null
  }).filter(value => value !== null)).size
  if (packet.authorization?.verdict !== 'approved' || binding.native_review_wrapper_sha256 === null
    || findings?.schema_version !== 'perpetual-review-findings.v2'
    || Object.values(findings.report_assessment).some(check => check.status !== 'passed' || check.evidence_refs.length === 0)
    || countEvidence(delivery.key_evidence_refs) < 3 || countEvidence(delivery.counter_evidence_refs) < 1) reject()
}
