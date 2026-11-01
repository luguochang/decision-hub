import { describe, expect, it } from 'vitest'
import {
  SYNTHESIS_TOOL_NAME,
  createSynthesisToolDefinition,
  normalizeSynthesisCandidate,
} from '../src/synthesis-tool.js'

function candidate(): Record<string, unknown> {
  return {
    schema_version: 'research-synthesis-candidate.v1',
    request_id: 'research-request:run-1',
    causal_case: null,
    horizons: [],
  }
}

function execution(hasAgent = true): never {
  return {
    callId: 'capture-1',
    rootCallId: 'capture-1',
    name: SYNTHESIS_TOOL_NAME,
    arguments: { candidate: candidate() },
    signal: new AbortController().signal,
    token: Symbol('tool-execution'),
    deferContext() {},
    concludeTurn() {},
    ...(hasAgent ? { agent: { id: 'dsh-session-1' } } : {}),
  } as never
}

describe('Decision Hub synthesis capture tool', () => {
  it('returns only the codegen-validated canonical synthesis object', async () => {
    const definition = createSynthesisToolDefinition()

    expect(definition.name).toBe(SYNTHESIS_TOOL_NAME)
    await expect(definition.execute({ candidate: candidate() }, execution())).resolves.toEqual(
      candidate(),
    )
  })

  it('fails closed for schema drift and calls outside an Agent session', async () => {
    const definition = createSynthesisToolDefinition()

    await expect(definition.execute(
      { candidate: { ...candidate(), extra: true } },
      execution(),
    )).rejects.toThrow()
    await expect(definition.execute(
      { candidate: candidate() },
      execution(false),
    )).rejects.toThrow('decision_hub_synthesis_agent_required')
  })

  it('repairs duplicate and omitted evidence summaries without inventing evidence', async () => {
    const submitted = {
      schema_version: 'research-synthesis-candidate.v1',
      request_id: 'research-request:run-1',
      causal_case: {
        case_id: 'case-1',
        thesis: 'Wait for confirmation.',
        main_chain: [{
          link_id: 'main-1',
          claim_type: 'fact',
          statement: 'Two venues agree on the current spot price.',
          evidence_refs: ['ev-1', 'ev-1', 'ev-2'],
          confirmation: 'The spread remains small.',
          invalidation: 'Either venue becomes stale.',
          affected_horizons: ['30m'],
        }],
        opposite_chain: [{
          link_id: 'opposite-1',
          claim_type: 'scenario',
          statement: 'The venue agreement can break.',
          evidence_refs: ['ev-2'],
          confirmation: 'The spread widens.',
          invalidation: 'The spread remains small.',
          affected_horizons: ['30m'],
        }],
        unresolved_questions: ['Cross-asset confirmation is unavailable.'],
      },
      horizons: [{
        horizon: '30m',
        action: 'no_trade',
        subjective_probability: 0.5,
        probability_status: 'uncalibrated',
        evidence_refs: ['ev-1', 'ev-1'],
        trigger: 'Wait for OI confirmation.',
        invalidation: 'Evidence becomes stale.',
        expires_at: '2026-09-14T07:00:00Z',
        next_review_at: '2026-09-14T06:50:00Z',
        missing_facts: ['Live macro transmission'],
        confidence_cap_reason: 'Probability is not calibrated.',
      }],
    }

    const normalized = normalizeSynthesisCandidate(submitted) as {
      causal_case: { evidence_refs: string[]; main_chain: Array<{ evidence_refs: string[] }> }
      horizons: Array<{ evidence_refs: string[] }>
    }
    expect(normalized.causal_case.evidence_refs).toEqual(['ev-1', 'ev-2'])
    expect(normalized.causal_case.main_chain[0]?.evidence_refs).toEqual(['ev-1', 'ev-2'])
    expect(normalized.horizons[0]?.evidence_refs).toEqual(['ev-1'])

    const definition = createSynthesisToolDefinition()
    await expect(definition.execute({ candidate: submitted }, execution())).resolves.toEqual(
      normalized,
    )
  })

  it('moves a provider-nested horizons array to the contract top level', async () => {
    const submitted = {
      schema_version: 'research-synthesis-candidate.v1',
      request_id: 'research-request:run-1',
      causal_case: {
        case_id: 'case-1',
        thesis: 'The daily trend is constructive but timing is uncertain.',
        main_chain: [{
          link_id: 'main-1',
          claim_type: 'fact',
          statement: 'Daily trend remains constructive.',
          evidence_refs: ['ev-1'],
          confirmation: 'The trend holds.',
          invalidation: 'The trend reverses.',
          affected_horizons: ['24h'],
        }],
        opposite_chain: [{
          link_id: 'opposite-1',
          claim_type: 'scenario',
          statement: 'The trend can reverse.',
          evidence_refs: ['ev-1'],
          confirmation: 'Momentum fades.',
          invalidation: 'Momentum strengthens.',
          affected_horizons: ['24h'],
        }],
        unresolved_questions: [],
        horizons: [{
          horizon: '24h',
          action: 'no_trade',
          subjective_probability: 0.5,
          probability_status: 'uncalibrated',
          evidence_refs: ['ev-1'],
          trigger: 'Wait for a fresh confirmation.',
          invalidation: 'The daily trend reverses.',
          expires_at: '2026-09-21T12:00:00Z',
          next_review_at: '2026-09-21T11:00:00Z',
          missing_facts: [],
          confidence_cap_reason: 'Insufficient calibration data.',
        }],
      },
    }

    const normalized = normalizeSynthesisCandidate(submitted) as Record<string, unknown>
    expect(normalized.horizons).toEqual((submitted.causal_case as Record<string, unknown>).horizons)
    expect(normalized.causal_case).not.toHaveProperty('horizons')

    const definition = createSynthesisToolDefinition()
    await expect(definition.execute({ candidate: submitted }, execution())).resolves.toEqual(
      normalized,
    )
  })
})
