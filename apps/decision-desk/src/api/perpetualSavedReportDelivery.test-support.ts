// Synthetic V6 presentation only: no native research or delivery qualification claim.
import fixture from '../../../../artifacts/d2-perpetual-saved-report/synthetic-presentation-fixture.json'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'

type Wire = typeof fixture & { probability_definitions: Record<string, unknown> }
export function reportParts(wire: Wire) {
  const source = JSON.parse(wire.source_json), artifact = JSON.parse(wire.artifact_json)
  const payload = JSON.parse(artifact.payload_json)
  return { wire, source, artifact, payload, packet: payload.perpetual_synthesis }
}

export async function reseal(parts: ReturnType<typeof reportParts>): Promise<Wire> {
  const { wire, source, artifact, payload, packet } = parts
  artifact.payload_json = canonical(payload)
  source.candidate.perpetual_synthesis = packet
  source.perpetual_binding = packet.binding
  source.artifact_hash = await hash(artifact)
  const artifact_json = canonical(artifact), source_json = canonical(source)
  const source_sha256 = await hash(source)
  return { ...wire, artifact_json, artifact_sha256: await hash(artifact), source_json, source_sha256,
    publication_ref: { ...wire.publication_ref, schema_version: source.schema_version,
      output_id: 'publication_' + source_sha256.slice(0, 40), payload_hash: source_sha256 } }
}

export async function deliveryWire(): Promise<Wire> {
  const wire = structuredClone(fixture), source = JSON.parse(wire.source_json)
  const artifact = JSON.parse(wire.artifact_json), payload = JSON.parse(artifact.payload_json)
  const packet = payload.perpetual_synthesis
  const sourceBinding = { ...packet.binding, native_review_wrapper_sha256: null }
  const delivery = { schema_version: 'perpetual-report-delivery.v1', run_id: wire.run_id,
    decision_at: wire.published_at, status: 'incomplete', reasons: ['report_assessment_v2_missing'],
    input_hashes: { synthesis: await hash(packet.synthesis), review_request: await hash(packet.review_request),
      authorization: await hash(packet.authorization), findings: await hash(null),
      catalog: await hash(packet.review_originals), source_binding: await hash(sourceBinding) },
    source_refs: { mapped_ref: packet.binding.mapped_ref, provider_emission_ref: packet.binding.provider_emission_ref,
      synthesis_ref: packet.binding.synthesis_ref }, key_evidence_refs: [], counter_evidence_refs: [],
    financial_qualified: false, execution_authorized: false }
  packet.schema_version = 'perpetual-research-report.v2'
  packet.binding = { ...sourceBinding, schema_version: 'perpetual-report-binding.v2', report_delivery: delivery }
  packet.review_findings = null
  source.schema_version = 'research-publication-source.v6'
  source.report_delivery = delivery
  source.candidate.research_complete = false
  const probability_definitions = { schema_version: 'perpetual-probability-definitions.v1', status: 'unavailable',
    binding: { owner_id: wire.owner_id, run_id: wire.run_id, mapped_ref: packet.binding.mapped_ref,
      provider_emission_ref: packet.binding.provider_emission_ref, synthesis_ref: packet.binding.synthesis_ref },
    reason_code: 'prepared_probability_definitions_unavailable' }
  return reseal({ wire: { ...wire, probability_definitions }, source, artifact, payload, packet })
}

export async function changedDelivery(change: (value: ReturnType<typeof JSON.parse>) => void) {
  const parts = reportParts(await deliveryWire())
  change(parts.packet.binding.report_delivery)
  parts.source.report_delivery = parts.packet.binding.report_delivery
  parts.source.candidate.research_complete = parts.source.report_delivery.status === 'complete'
  return reseal(parts)
}
