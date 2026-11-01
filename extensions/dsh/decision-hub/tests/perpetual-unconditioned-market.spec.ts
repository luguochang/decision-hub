import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { actionReviewRequestV2Schema, researchDecisionSynthesisV2Schema } from '@decision-hub/contracts-ts'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'

// Controlled authored inputs only; no live Run or private artifact is required.
const synthetic = () => JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
function ordinaryResult(result: ReturnType<typeof preparePerpetualSynthesis>) {
  const { candidate, review_request } = result
  if (candidate.schema_version !== 'research-decision-synthesis.v2'
    || review_request.schema_version !== 'action-review-request.v2') {
    throw new Error('Expected the ordinary V2 preparation branch')
  }
  return { ...result, candidate, review_request }
}
const issues = (work: () => unknown): string[] => {
  try { work(); return [] } catch (error) {
    if (error && typeof error === 'object' && 'issues' in error) return (error.issues as { message: string }[]).map(item => item.message)
    throw error
  }
}

describe('ordinary market direction is not implicitly conditioned on an event', () => {
  it('prepares an ordinary market with a shorter candidate lifetime without changing economics', () => {
    const { draft, options } = synthetic()
    draft.market_event_conditioned = false
    draft.valid_until = '2026-01-01T00:50:00Z'
    for (const row of draft.direction_distribution.probabilities) if (row.outcome_id === 'neutral') row.outcome_id = 'flat'
    expect(issues(() => preparePerpetualSynthesis(draft, options))).toContain('decision_v2_comparison_direction_partition')
    for (const row of draft.direction_distribution.probabilities) if (row.outcome_id === 'flat') row.outcome_id = 'neutral'
    expect(issues(() => preparePerpetualSynthesis(draft, options))).toEqual([])
    const result = ordinaryResult(preparePerpetualSynthesis(draft, options))
    expect(result.candidate.market_distribution.event_scenario_ref).toBeNull()
    expect(result.review_request.synthesis.event_probability_ref).toEqual(result.candidate.event_probability_ref)
    expect(result.candidate.candidate_set.valid_until).toBe('2026-01-01T00:50:00Z')
    expect(result.review_request.allowed_domain.valid_until).toBe('2026-01-01T01:00:00Z')
    expect(result.candidate.market_distribution.direction_distribution.probabilities).toEqual(draft.direction_distribution.probabilities)
    expect(result.candidate.path_distribution.scenarios).toEqual(draft.path_distribution.scenarios)
  })

  it('preserves event-conditioned lineage and rejects substituted non-null refs', () => {
    const { draft, options } = synthetic()
    draft.market_event_conditioned = true
    const conditioned = ordinaryResult(preparePerpetualSynthesis(draft, options)).candidate
    expect(conditioned.market_distribution.event_scenario_ref).toEqual(conditioned.event_probability_ref)
    const wrong = structuredClone(conditioned)
    wrong.market_distribution.event_scenario_ref = { ref: 'unrelated:event', hash: 'f'.repeat(64) }
    const rejected = researchDecisionSynthesisV2Schema.safeParse(wrong)
    expect(rejected.success).toBe(false)
    if (!rejected.success) expect(rejected.error.issues.map(item => item.message)).toContain('decision_v2_candidate_synthesis_probability')
    draft.market_event_conditioned = false
    const ordinary = ordinaryResult(preparePerpetualSynthesis(draft, options)).candidate
    expect(ordinary.market_distribution.event_scenario_ref).toBeNull()
    expect(ordinary.event_probability).toEqual(conditioned.event_probability)
    expect(ordinary.path_distribution).toEqual(conditioned.path_distribution)
  })

  it('rejects review requests at either independent expiry boundary', () => {
    const { draft, options } = synthetic()
    const packet = ordinaryResult(preparePerpetualSynthesis(draft, options)).review_request
    const candidateExpired = structuredClone(packet)
    candidateExpired.allowed_domain.valid_until = '2027-01-01T00:00:00Z'
    candidateExpired.requested_at = candidateExpired.synthesis.candidate_set.valid_until
    const domainExpired = structuredClone(packet)
    domainExpired.allowed_domain.valid_until = domainExpired.requested_at
    for (const value of [candidateExpired, domainExpired]) {
      const rejected = actionReviewRequestV2Schema.safeParse(value)
      expect(rejected.success).toBe(false)
      if (!rejected.success) expect(rejected.error.issues.map(item => item.message)).toContain('decision_v2_candidate_request_expiry')
    }
  })
})
