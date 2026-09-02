// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { researchRunDetailViewSchema } from '../api/schemas'
import { ResearchRunDetail } from './ResearchPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const now = '2026-08-29T12:00:00Z'
const budget = { max_evidence_rounds: 3, max_tool_calls: 12, max_subagents: 6, total_deadline_seconds: 180, per_tool_timeout_seconds: 20, per_model_step_timeout_seconds: 60, max_structured_repairs: 1, max_estimated_cost_usd: 1 }
const coverage = { status: 'insufficient' as const, covered_requirement_ids: ['event_identity'], gaps: [{ requirement_id: 'market_transmission', importance: 'hard' as const, reason_code: 'missing' as const, query_hint: 'Find DXY and Treasury response', attempted_capabilities: ['market.cross_asset'], blocks_directional_output: true }], conflicts: [], hard_coverage_ratio: 0.5, soft_coverage_ratio: 1, assessed_at: now }
const link = (id: string, statement: string) => ({ link_id: id, claim_type: 'inference' as const, statement, evidence_refs: ['ev-1'], confirmation: `${id} confirmation`, invalidation: `${id} invalidation`, affected_horizons: ['30m' as const, '24h' as const] })

const detail = researchRunDetailViewSchema.parse({
  schema_version: 'research-run-detail-view.v1',
  run: { schema_version: 'research-run-view.v2', run_id: 'run-research-1', event_id: 'event-1', event_title: 'Federal Reserve speech changes the inflation risk balance', admission_origin: 'manual', priority: 'high', status: 'degraded', stage: 'done', runtime_id: 'dsh', profile_ref: 'crypto_macro.manager.v1', current_round: 2, budget, coverage, current_action: 'Stopped at the bounded evidence limit.', stop_reason: { code: 'critical_data_unavailable', detail: 'Market transmission remains unverified.', bounded: true, remaining_hard_gaps: ['market_transmission'] }, failure: { error_code: 'research_capability_timeout', origin: 'transport', cause_code: 'deadline', capability_id: 'market.cross_asset', tool_call_id: 'call-1', retryable: true, deadline_ms: 20000 }, latest_sequence_no: 4, artifact_id: 'art-1', available_at: null, parent_run_id: null, created_at: now, updated_at: now },
  trigger_snapshot: null,
  decision_snapshot: null,
  evidence: [],
  rounds: [{ round: 1, plan: { plan_id: 'plan-1', objective: 'Verify policy delta and market transmission', tasks: [{ task_id: 'task-1', capability_id: 'market.cross_asset', objective: 'Measure transmission', question: 'Did DXY and yields confirm the policy shock?', input_evidence_refs: ['ev-1'], output_schema_ref: 'evidence-candidate.v1', depends_on: [], success_condition: 'Two fresh market observations', priority: 1 }], required_capabilities: ['market.cross_asset'], created_at: now }, tool_invocations: [{ tool_call_id: 'call-1', capability_id: 'market.cross_asset', tool_name: 'market.cross_asset', query_summary: 'Measure rates and USD transmission', started_at: now, finished_at: '2026-08-29T12:00:01Z', status: 'failed', attempt: 1, latency_ms: 1000, cost_usd: null, error_code: 'research_capability_timeout', error: { error_code: 'research_capability_timeout', origin: 'transport', cause_code: 'deadline', capability_id: 'market.cross_asset', tool_call_id: 'call-1', retryable: true, deadline_ms: 20000 } }], tool_results: [], new_evidence_refs: [], coverage, started_at: now, finished_at: '2026-08-29T12:00:01Z' }],
  causal_case: { case_id: 'case-1', thesis: 'The speech is hawkish, but transmission is not yet confirmed.', main_chain: [link('main-1', 'Policy language raises the expected rate path.')], opposite_chain: [link('counter-1', 'The message may already be priced in.')], unresolved_questions: ['Will yields hold the move?'], evidence_refs: ['ev-1'] },
  horizons: [
    { horizon: '30m', action: 'no_trade', subjective_probability: 0.52, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '30m trigger', invalidation: '30m invalidation', expires_at: '2026-08-29T12:30:00Z', next_review_at: '2026-08-29T12:10:00Z', missing_facts: ['DXY'], confidence_cap_reason: 'Cross-asset confirmation missing' },
    { horizon: '24h', action: 'short', subjective_probability: 0.58, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '24h trigger', invalidation: '24h invalidation', expires_at: '2026-08-30T12:00:00Z', next_review_at: '2026-08-29T18:00:00Z', missing_facts: [], confidence_cap_reason: null },
    { horizon: '72h', action: 'neutral', subjective_probability: 0.5, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '72h trigger', invalidation: '72h invalidation', expires_at: '2026-09-01T12:00:00Z', next_review_at: '2026-08-30T12:00:00Z', missing_facts: ['PCE'], confidence_cap_reason: 'Macro release pending' },
  ],
  trace: [{ schema_version: 'research-trace-event.v1', run_id: 'run-research-1', research_session_id: 'product:run-research-1', sequence_no: 4, event_type: 'session_stopped', occurred_at: now, stage: 'done', summary: 'Market transmission remains unverified.', reference_type: 'stop_reason', reference_id: 'critical_data_unavailable', status: 'degraded', error_code: null }],
  total_tool_calls: 2, total_subagents: 1, total_tokens: 1200, estimated_cost_usd: 0.18, scheduled_recheck_at: '2026-08-29T18:00:00Z',
})

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
})

describe('Research Command Center detail', () => {
  it('renders sufficiency, causal counter-chain, distinct horizons and normalized trace', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={detail} commandReason="Owner review" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    expect(container.textContent).toContain('market_transmission')
    expect(container.textContent).toContain('用户提交')
    expect(container.textContent).toContain('高优先级')
    expect(container.textContent).toContain('Main chain')
    expect(container.textContent).toContain('Counter chain')
    expect(container.textContent).toContain('30m trigger')
    expect(container.textContent).toContain('24h trigger')
    expect(container.textContent).toContain('72h trigger')
    expect(container.textContent).toContain('Session Stopped')
    expect(container.textContent).toContain('research_capability_timeout')
    expect(container.textContent).toContain('market.cross_asset')
    expect(container.textContent).toContain('retryable')
    expect(container.textContent).not.toContain('{"')
    expect(container.querySelectorAll('.horizon-grid article')).toHaveLength(3)
    await act(async () => { root.unmount() })
  })

  it('explains synthesis attestation failure and renders bounded evidence summaries', async () => {
    const failedDetail = researchRunDetailViewSchema.parse({
      ...detail,
      run: {
        ...detail.run,
        status: 'failed',
        failure: {
          error_code: 'dsh_evidence_unattested',
          origin: 'orchestration',
          cause_code: 'synthesis_attestation',
          capability_id: null,
          tool_call_id: null,
          retryable: false,
          deadline_ms: null,
        },
      },
      evidence: [
        {
          evidence_id: 'ev-market-1', requirement_id: 'crypto_spot_confirmation', kind: 'market', authority: 'exchange', source_id: 'coinex-public', source_url: 'https://api.coinex.com/v2/spot/ticker?market=BTCUSDT', published_at: null, observed_at: now, received_at: now, content_hash: '1'.repeat(64), excerpt: '{"market":"BTCUSDT","values":{"spot_price":"78719","spot_volume":"368.4"}}', structured_payload_ref: null, tool_call_id: 'call-market', research_session_id: 'dsh-session-1', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
        },
        {
          evidence_id: 'ev-official-1', requirement_id: 'event_identity', kind: 'official', authority: 'official', source_id: 'federal-reserve', source_url: 'https://www.federalreserve.gov/example', published_at: null, observed_at: now, received_at: now, content_hash: '2'.repeat(64), excerpt: '<html><body><nav>Skip to main content Main Menu Careers FAQs</nav><main>Chairman testimony confirmed the policy event and its publication time.</main></body></html>', structured_payload_ref: null, tool_call_id: 'call-official', research_session_id: 'dsh-session-1', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
        },
      ],
      rounds: [],
      causal_case: null,
      horizons: [],
    })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={failedDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    expect(container.textContent).toContain('Synthesis evidence attestation failed')
    expect(container.textContent).toContain('2 条 Evidence 已保留')
    expect(container.textContent).toContain('spot_price 78719')
    expect(container.textContent).toContain('Chairman testimony confirmed')
    expect(container.textContent).not.toContain('{"market"')
    expect(container.textContent).not.toContain('<html>')
    await act(async () => { root.unmount() })
  })
})
