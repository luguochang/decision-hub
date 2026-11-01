import { describe, expect, it, vi } from 'vitest'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { researchSynthesisCandidateSchema } from '@decision-hub/contracts-ts'

const link = { link_id: 'main', claim_type: 'inference', statement: 'A bounded claim.',
  evidence_refs: ['ev-1'], confirmation: 'Check the actual source.', invalidation: 'Source contradicts the claim.',
  affected_horizons: ['24h'] }
const candidate = { schema_version: 'research-synthesis-candidate.v1', request_id: 'request-1',
  causal_case: { case_id: 'case-1', thesis: 'A bounded thesis.', main_chain: [link], opposite_chain: [{ ...link, link_id: 'opposite' }],
    unresolved_questions: [], evidence_refs: ['ev-1'] }, horizons: [{ horizon: '24h', action: 'no_trade',
    subjective_probability: 0.5, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: 'Wait for confirmation.',
    invalidation: 'Source contradicts the claim.', expires_at: '2026-10-10T20:00:00Z', next_review_at: '2026-10-10T19:00:00Z',
    missing_facts: [], confidence_cap_reason: 'Not calibrated.' }] }
const execution = { agent: { id: 'root' }, callId: 'call-1', signal: new AbortController().signal } as never

describe('model-visible synthesis contract from canonical codegen', () => {
  it('exposes the actual nested required fields before the model submits', () => {
    const parameters = createSynthesisToolDefinition().parameters as unknown as {
      properties: { candidate: { required: string[]; properties: { causal_case: { oneOf: Array<{ properties: Record<string, { items: { required: string[] } }> }> }; horizons: { items: { required: string[] } } } } }
    }
    const schema = parameters.properties.candidate
    expect(schema.required).toEqual(['schema_version', 'request_id', 'causal_case', 'horizons'])
    expect(schema.properties.causal_case.oneOf[0]?.properties.main_chain?.items.required).toEqual([
      'link_id', 'claim_type', 'statement', 'evidence_refs', 'confirmation', 'invalidation', 'affected_horizons',
    ])
    expect(schema.properties.horizons.items.required).toContain('invalidation')
    expect(schema.properties.horizons.items.required).toContain('confidence_cap_reason')
  })

  it('rejects missing invalidation after reading frozen protocol and before provider execution', async () => {
    const read = vi.fn().mockResolvedValue({ request: { output_schema_ref: 'agentic_research.schema.yaml#/$defs/research_session_result' } })
    const tool = createSynthesisToolDefinition({ reviewContext: read })
    const broken = structuredClone(candidate)
    delete (broken.causal_case.main_chain[0] as Partial<typeof link>).invalidation
    await expect(tool.execute({ candidate: broken }, execution)).rejects.toThrow('invalidation')
    expect(read).toHaveBeenCalledOnce()
    expect(broken.causal_case.main_chain[0]).not.toHaveProperty('invalidation')
  })

  it('rejects invented field names and preserves strict canonical value checks', async () => {
    const tool = createSynthesisToolDefinition()
    const drift = structuredClone(candidate) as typeof candidate & { causal_case: { base_case?: string } }
    drift.causal_case.base_case = 'Invented field'
    await expect(tool.execute({ candidate: drift }, execution)).rejects.toThrow('base_case')
    const outside = structuredClone(candidate); outside.horizons[0]!.subjective_probability = 1.2
    await expect(tool.execute({ candidate: outside }, execution)).rejects.toThrow()
    const badDate = structuredClone(candidate); badDate.horizons[0]!.expires_at = 'tomorrow'
    await expect(tool.execute({ candidate: badDate }, execution)).rejects.toThrow()
  })

  it('returns the unchanged complete canonical candidate through the legacy boundary', async () => {
    const expected = researchSynthesisCandidateSchema.parse(candidate)
    await expect(createSynthesisToolDefinition().execute({ candidate }, execution)).resolves.toEqual(expected)
    expect(candidate.causal_case.main_chain[0]).toEqual(link)
  })
})
