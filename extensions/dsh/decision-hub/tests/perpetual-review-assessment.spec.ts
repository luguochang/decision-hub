import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { perpetualReviewFindingsV2Schema } from '@decision-hub/contracts-ts'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'
import { buildPerpetualReviewerPrompt, perpetualFindingsFromOutput } from '../src/perpetual-synthesis-protocol.js'

const checks = ['frozen_information', 'probability_completeness', 'evidence_balance', 'cost_protection_and_conditions'] as const

function fixture() {
  const { draft, options } = JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
  const prepared = preparePerpetualSynthesis(draft, options), packet = prepared.review_request
  const prompt = buildPerpetualReviewerPrompt(packet, '', options.requested_at, options.review_originals, prepared.assumptions)
  const lines = prompt.split('\n'), fixed = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
  fixed.verdict = 'approved'
  const ref = options.review_originals.originals[0].source_ref
  const findings = { schema_version: 'perpetual-review-findings.v2', review_request_ref: fixed.review_request_ref,
    summary: 'Synthetic protocol check, not actual research qualification.', issues: [] as any[],
    report_assessment: Object.fromEntries(checks.map(key => [key, {
      status: 'passed', reason: 'Synthetic reviewed original supplied for protocol testing.', evidence_refs: [ref],
    }])) as Record<string, any> }
  return { packet, prompt, fixed, findings, originals: options.review_originals }
}

function expectedPrompt(prompt: string, version: string): string {
  const lines = prompt.split('\n'), at = lines.indexOf('FINDINGS_FIXED_FIELDS_JSON')
  const fixed = JSON.parse(lines[at + 1]!)
  fixed.schema_version = version
  if (version.endsWith('.v1')) delete fixed.report_assessment
  lines[at + 1] = JSON.stringify(fixed)
  return lines.join('\n')
}

const output = (authorization: unknown, findings: unknown) => [{ type: 'text', text: JSON.stringify([authorization, findings]) }]

describe('versioned independent content assessment', () => {
  it('the generated typed mirror enforces passed support rather than only the caller', () => {
    const f = fixture()
    expect(perpetualReviewFindingsV2Schema.safeParse(f.findings).success).toBe(true)
    f.findings.report_assessment.frozen_information.evidence_refs = []
    expect(perpetualReviewFindingsV2Schema.safeParse(f.findings).success).toBe(false)
  })
  it('the new producer explicitly requests v2 rather than deriving checks from approval', () => {
    const { prompt } = fixture(), lines = prompt.split('\n')
    const fixed = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
    expect(fixed.schema_version).toBe('perpetual-review-findings.v2')
    expect(prompt).toContain('frozen_information')
    expect(prompt).toContain('cost_protection_and_conditions')
  })

  it('preserves approved with incomplete content as a different finding', () => {
    const f = fixture()
    f.findings.report_assessment.cost_protection_and_conditions = {
      status: 'incomplete', reason: 'Synthetic protection original is missing.', evidence_refs: [],
    }
    const restored = perpetualFindingsFromOutput(f.packet, output(f.fixed, f.findings), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v2'))
    expect(restored).toEqual(f.findings)
    expect(f.fixed.verdict).toBe('approved')
  })

  it('preserves actual negative review and complete structured reasons', () => {
    const f = fixture()
    f.fixed.verdict = 'needs_revision'
    f.findings.issues = [{ issue_id: 'missing-protection', candidate_ids: [], field_paths: ['/analysis/thesis'],
      concern: 'Synthetic missing protection.', requested_change: 'Supply the supported plan.', evidence_refs: [] }]
    f.findings.report_assessment.cost_protection_and_conditions.status = 'failed'
    expect(perpetualFindingsFromOutput(f.packet, output(f.fixed, f.findings), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v2'))).toEqual(f.findings)
  })

  it('retains a historical v1 result without inventing assessment fields', () => {
    const f = fixture()
    const old = { schema_version: 'perpetual-review-findings.v1', review_request_ref: f.fixed.review_request_ref,
      summary: 'Historical synthetic independent feedback.', issues: [] }
    const before = JSON.stringify(old)
    expect(perpetualFindingsFromOutput(f.packet, output(f.fixed, old), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v1'))).toEqual(old)
    expect(JSON.stringify(old)).toBe(before)
  })

  it('rejects downgrade to v1 when the authenticated prompt requested v2', () => {
    const f = fixture()
    const old = { schema_version: 'perpetual-review-findings.v1', review_request_ref: f.fixed.review_request_ref,
      summary: 'Synthetic downgrade attempt.', issues: [] }
    expect(() => perpetualFindingsFromOutput(f.packet, output(f.fixed, old), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v2'))).toThrow()
  })

  it.each(checks)('requires named check %s', key => {
    const f = fixture()
    delete f.findings.report_assessment[key]
    expect(() => perpetualFindingsFromOutput(f.packet, output(f.fixed, f.findings), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v2'))).toThrow()
  })

  it.each(['empty_reason', 'unknown_status', 'foreign_ref', 'empty_passed_refs', 'pc_claim', 'wrong_packet'])('rejects %s', attack => {
    const f = fixture(), check = f.findings.report_assessment.frozen_information
    if (attack === 'empty_reason') check.reason = ' '
    if (attack === 'unknown_status') check.status = 'approved'
    if (attack === 'foreign_ref') check.evidence_refs = [{ ref: 'foreign-original', hash: '0'.repeat(64) }]
    if (attack === 'empty_passed_refs') check.evidence_refs = []
    if (attack === 'pc_claim') f.findings.report_assessment.pc_readable = { ...check }
    if (attack === 'wrong_packet') f.findings.review_request_ref = { ref: 'other-packet', hash: '0'.repeat(64) }
    expect(() => perpetualFindingsFromOutput(f.packet, output(f.fixed, f.findings), f.originals,
      expectedPrompt(f.prompt, 'perpetual-review-findings.v2'))).toThrow()
  })
})
