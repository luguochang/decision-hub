import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { perpetualReviewFindingsV1Schema } from '@decision-hub/contracts-ts'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'
import { buildPerpetualReviewerPrompt, perpetualAuthorizationFromOutput, perpetualFindingsFromOutput } from '../src/perpetual-synthesis-protocol.js'

function fixture() {
  const { draft, options } = JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
  const prepared = preparePerpetualSynthesis(draft, options)
  const packet = prepared.review_request
  const prompt = buildPerpetualReviewerPrompt(packet, '', options.requested_at, options.review_originals, prepared.assumptions)
  const lines = prompt.split('\n')
  const fixed = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
  const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
  return { packet, prompt, lines, fixed, findings, originals: options.review_originals }
}

describe('canonical required issue fields in the actual reviewer prompt', () => {
  it('exposes every required canonical field and explicit unique nonempty issue_id semantics', () => {
    const { lines, prompt } = fixture()
    const marker = lines.indexOf('FINDINGS_ISSUE_REQUIRED_FIELDS_JSON')
    expect(marker).toBeGreaterThanOrEqual(0)
    const shape = perpetualReviewFindingsV1Schema.innerType().shape.issues.element.shape
    const required = Object.entries(shape).filter(([, schema]) => !schema.isOptional()).map(([key]) => key)
    expect(JSON.parse(lines[marker + 1]!)).toEqual(required)
    expect(required).toEqual(['issue_id', 'candidate_ids', 'field_paths', 'concern', 'requested_change', 'evidence_refs'])
    expect(prompt).toContain('issue_id is a required nonempty string')
    expect(prompt).toContain('unique across all issues')
    expect(prompt).toContain('finding_1')
    expect(prompt).toContain('candidate_ids is an array')
    expect(prompt).toContain('field_paths is an array')
    expect(prompt).toContain('concern and requested_change are required nonempty strings')
    expect(prompt).toContain('evidence_refs is an array')
  })

  it('preserves exact packet refs and valid negative findings without manufacturing approval', () => {
    const { packet, prompt, lines, fixed, findings, originals } = fixture()
    expect(JSON.parse(lines[lines.indexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON') + 1]!)).toEqual(packet)
    expect(findings.review_request_ref).toEqual(fixed.review_request_ref)
    expect(prompt).toContain('negative verdict requires at least one concrete issue')
    expect(fixed.verdict).toBe('needs_revision')
    findings.issues = [{ issue_id: 'finding_1', candidate_ids: [], field_paths: ['/analysis/thesis'],
      concern: 'Synthetic missing support.', requested_change: 'State uncertainty explicitly.', evidence_refs: [] }]
    const output = [{ type: 'text', text: JSON.stringify([fixed, findings]) }]
    expect(perpetualAuthorizationFromOutput(packet, output).verdict).toBe('needs_revision')
    expect(perpetualFindingsFromOutput(packet, output, originals, prompt)).toEqual(findings)
  })

  it('continues to reject missing or duplicate issue IDs instead of filling them in', () => {
    const { packet, fixed, findings, originals, prompt } = fixture()
    const issue = { candidate_ids: [], field_paths: ['/analysis/thesis'], concern: 'Synthetic concern.',
      requested_change: 'Make the claim conditional.', evidence_refs: [] }
    findings.issues = [issue]
    const output = () => [{ type: 'text', text: JSON.stringify([fixed, findings]) }]
    expect(() => perpetualFindingsFromOutput(packet, output(), originals, prompt)).toThrow()
    findings.issues = [{ ...issue, issue_id: 'finding_1' }, { ...issue, issue_id: 'finding_1' }]
    expect(() => perpetualFindingsFromOutput(packet, output(), originals, prompt)).toThrow('findings_binding_invalid')
    expect(findings.issues.map((row: any) => row.issue_id)).toEqual(['finding_1', 'finding_1'])
  })
})
