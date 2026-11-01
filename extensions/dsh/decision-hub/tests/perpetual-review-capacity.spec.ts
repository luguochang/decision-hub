// Controlled regression only: one closed fixture cohort, no real Provider/source authority.
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'
import { bodyHash, buildPerpetualReviewerPrompt, canonicalJson, primaryPerpetualSynthesis, selectPerpetualReviewOriginals, validatePerpetualReviewOriginals } from '../src/perpetual-synthesis-protocol.js'

it('retains the complete additional catalog and review above the old local event cap', () => {
  const fixtureBytes = readFileSync(new URL('../../../../artifacts/d2-event-scenario-action-plans/native-event-fixture.json', import.meta.url), 'utf8')
  const { draft, options } = JSON.parse(fixtureBytes)
  const prepared = preparePerpetualSynthesis(draft, options)
  const primary = primaryPerpetualSynthesis(prepared.candidate)
  const assumptionRefs = prepared.assumptions.flatMap(row => row.evidence_refs)
  const requiredRefs = [...assumptionRefs, ...primary.reasoning_refs,
    ...primary.analysis.main_chain.flatMap(row => row.evidence_refs), ...primary.analysis.opposite_chain.flatMap(row => row.evidence_refs)]
  // The original capacity generator extracted this complete catalog from actual_prompt.
  const selected = selectPerpetualReviewOriginals(prepared.candidate, options.review_originals, assumptionRefs)
  expect(options.review_originals.originals).toHaveLength(23)
  expect(selected.originals).toHaveLength(19)
  expect(selected.originals.filter(row => row.source_kind === 'server_material'))
    .toEqual(options.review_originals.originals.filter((row: any) => row.source_kind === 'server_material'))
  const originals = structuredClone(selected)
  const policies = originals.originals.filter((row: any) => row.source_kind === 'server_material' && row.role === 'policies')
  expect(policies).toHaveLength(1)
  const policy = policies[0], originalPolicy = JSON.parse(policy.body_json)
  const controlledPadding = 'x'.repeat(180000)
  const paddedPolicy = { ...originalPolicy, controlled_transport_padding_not_market_evidence: controlledPadding }
  policy.body_json = canonicalJson(paddedPolicy)
  policy.source_ref = { ref: `controlled-regression:transport-capacity:policies:${bodyHash(paddedPolicy)}`, hash: bodyHash(paddedPolicy) }
  expect(policy.original_ref).toBeNull()
  expect(policy.source_ref.ref).toContain('controlled-regression:')
  const { controlled_transport_padding_not_market_evidence: padding, ...preservedPolicy } = JSON.parse(policy.body_json)
  expect(padding).toBe(controlledPadding)
  expect(preservedPolicy).toEqual(originalPolicy)
  const value = validatePerpetualReviewOriginals(prepared.candidate, originals, requiredRefs)
  const prompt = buildPerpetualReviewerPrompt(prepared.review_request, '', options.requested_at, value, prepared.assumptions)
  expect(Buffer.byteLength(canonicalJson(value), 'utf8')).toBeGreaterThan(524288)
  expect(Buffer.byteLength(prompt, 'utf8')).toBeGreaterThan(524288)
  expect(Buffer.byteLength(prompt, 'utf8')).toBeLessThan(800000)
  expect(prompt).toContain(canonicalJson(value))
  expect(prompt).toContain('x'.repeat(180000))
  expect(() => buildPerpetualReviewerPrompt(prepared.review_request, 'x'.repeat(800000), options.requested_at, value)).toThrow('decision_hub_perpetual_review_input_oversized')
  expect(canonicalJson(options.review_originals)).toBe(canonicalJson(JSON.parse(fixtureBytes).options.review_originals))
  console.log(JSON.stringify({catalog_utf8: Buffer.byteLength(canonicalJson(value), 'utf8'), prompt_utf8: Buffer.byteLength(prompt, 'utf8'), prompt_characters: [...prompt].length, fixture_kind: 'controlled_capacity_regression', model_invoked: false}))
})
