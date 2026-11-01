// Synthetic originals authenticated through the real baseline reader; no Provider or production DB.
import { createHash } from 'node:crypto'
import { researchSessionRequestSchema, researchCapabilityResultSchema, dshModelDispatchBindingSchema,
  dshRunSessionLinkViewSchema } from '@decision-hub/contracts-ts'
import { readResearchBaseline } from '../src/host/baseline.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { DECISION_SYNTHESIS_REF } from '../src/synthesis-protocol.js'
import type { SynthesisReviewContext } from '../src/final-review-gate.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'

export function catalog(sessionId: string, deadlineAt: string, mode = 'live') {
  const content = { schema_version: 'research-query-catalog.v1', scope: {
    run_id: 'run-managed-control', dsh_session_id: sessionId, request_hash: 'a'.repeat(64), generation: 1,
    status: 'bound', method_hash: '1'.repeat(64), execution_mode: mode, deadline_at: deadlineAt,
  }, capabilities: [{ capability_id: 'spot_market_facts', allowed_domains: [], modes: [mode], routes: [] }] }
  const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
    : value !== null && typeof value === 'object' ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`
      : JSON.stringify(value)
  return { ...content, catalog_hash: createHash('sha256').update(canonical(content)).digest('hex') } as any
}

export const researchArgs = { capability_id: 'spot_market_facts', requirement_id: 'crypto_spot_confirmation',
  query: 'Read current committed BTC spot facts.', target_url: null, symbols: ['BTC-USDT'], fields: [],
  allowed_domains: [], max_results: 1, max_cost_usd: null }

export async function authenticatedFixture() {
  const now = Date.now(), stamp = (offset: number) => new Date(now + offset).toISOString()
  const runId = 'run-managed-control', requestHash = 'a'.repeat(64), buildHash = 'b'.repeat(64)
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash) }
  const request = researchSessionRequestSchema.parse({
    schema_version: 'research-session-request.v1', request_id: `research-request:${runId}`, run_id: runId,
    event_id: 'event-control', trigger_snapshot_id: 'trigger-control', domain_pack_ref: 'control', role_profile_ref: 'control',
    execution_mode: 'live', research_mode: 'current_decision', pit_cutoff_at: stamp(600_000), current_round: 1,
    evidence_refs: ['input-control'], input_evidence: [{ evidence_id: 'input-control', kind: 'market', authority: 'exchange',
      source_id: 'control-input', source_url: null, published_at: null, observed_at: stamp(-2_000), received_at: stamp(-1_900),
      content_hash: 'c'.repeat(64), excerpt: 'Synthetic frozen input.' }],
    evidence_requirements: ['macro_context', 'crypto_spot_confirmation'].map(requirement_id => ({
      requirement_id, description: 'Synthetic declared requirement.', importance: 'hard', source_priority: ['control-source'],
      authority_floor: 'official', preferred_capabilities: [requirement_id === 'macro_context' ? 'official.macro' : 'spot_market_facts'],
      freshness_seconds: 300, minimum_independent_sources: 1, allowed_fallbacks: [], confidence_cap: .5,
    })), target_gaps: [], allowed_capabilities: ['official.macro', 'spot_market_facts'],
    execution_budget: { max_evidence_rounds: 2, max_tool_calls: 12, max_subagents: 1, total_deadline_seconds: 600,
      per_tool_timeout_seconds: 60, per_model_step_timeout_seconds: 600, max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: stamp(600_000), output_schema_ref: DECISION_SYNTHESIS_REF, repair_instructions: null,
  })
  const macro = researchCapabilityResultSchema.parse({ schema_version: 'research-capability-result.v1',
    request_id: 'baseline-macro-control', capability_id: 'official.macro', provider: 'control-source',
    evidence_candidates: [{ evidence_id: 'macro-evidence-control', requirement_id: 'macro_context', kind: 'official',
      authority: 'official', source_id: 'control-source', source_url: null, published_at: stamp(-1_800),
      observed_at: stamp(-1_700), received_at: stamp(-1_600), content_hash: 'd'.repeat(64), excerpt: 'Synthetic admitted macro original.',
      structured_payload_ref: null, tool_call_id: 'baseline-macro-control', research_session_id: binding.sessionId,
      round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null }],
    facts: [], cost_usd: 0, completed_at: stamp(-1_500) })
  const reference = (role: string) => ({ output_id: `output-${role}`, role, schema_version: `${role}.v1`,
    payload_hash: 'f'.repeat(64), recorded_at: stamp(-1_400) })
  const delivery = { schema_version: 'research-baseline-delivery.v1', status: 'hard_gap', run_id: runId,
    research_session_id: binding.sessionId, generation: 1, request_hash: requestHash, method_hash: '1'.repeat(64),
    plan_ref: reference('baseline-plan'), batch_ref: reference('baseline-batch'), results: [macro],
    errors: [{ request_id: 'baseline-spot-control', capability_id: 'spot_market_facts', requirement_id: 'crypto_spot_confirmation',
      error_code: 'spot_research_original_stale' }],
    coverage: { status: 'insufficient', covered_requirement_ids: ['macro_context'], gaps: [{ requirement_id: 'crypto_spot_confirmation',
      importance: 'hard', reason_code: 'stale', query_hint: 'Read exact-asset committed data.',
      attempted_capabilities: ['spot_market_facts'], blocks_directional_output: true }], conflicts: [],
      hard_coverage_ratio: .5, soft_coverage_ratio: 0, assessed_at: stamp(-1_300) },
    remaining_tool_calls: 10, acquisition_close_at: stamp(480_000), recorded_at: stamp(-1_200), origin: 'server_baseline',
    skill_execution_verified: false, model_understanding_verified: false }
  const serverBaseline = await readResearchBaseline({ serviceUrl: 'http://fixture.invalid/execute', authKey: 'synthetic',
    fetchImpl: async () => Response.json(delivery), binding, request, signal: new AbortController().signal,
    deadlineAt: request.deadline_at })
  const admittedEvidence = [...macro.evidence_candidates]
  const context = (events: readonly SessionEventLike[]): SynthesisReviewContext => ({ binding, request,
    events: structuredClone([...events]), admittedEvidence: structuredClone(admittedEvidence), serverBaseline })
  const spot = (callId: string) => researchCapabilityResultSchema.parse({ schema_version: 'research-capability-result.v1',
    request_id: callId, capability_id: 'spot_market_facts', provider: 'control-exchange', evidence_candidates: [{
      ...macro.evidence_candidates[0], evidence_id: 'late-spot-evidence-control', requirement_id: 'crypto_spot_confirmation',
      kind: 'market', authority: 'exchange', source_id: 'control-exchange', tool_call_id: callId, content_hash: 'e'.repeat(64),
      observed_at: new Date().toISOString(), received_at: new Date().toISOString(), published_at: null,
      excerpt: 'Synthetic current spot original.',
    }], facts: [], cost_usd: 0, completed_at: new Date().toISOString() })
  const dispatch = dshModelDispatchBindingSchema.parse({ schema_version: 'dsh-model-dispatch-binding.v1', run_id: runId,
    dsh_session_id: binding.sessionId, request_hash: requestHash, generation: 1, plugin_build_hash: buildHash,
    execution_session_id: null, parent_session_id: null, provider: 'mock', model: 'mock', purpose: 'conversation',
    reasoning_effort: null, temperature: null, max_tokens: 4096, stop_sequences_hash: null })
  const link = dshRunSessionLinkViewSchema.parse({ schema_version: 'dsh-run-session-link.v1', run_id: runId,
    dsh_session_id: binding.sessionId, request_hash: requestHash, generation: 1, state: 'running',
    upstream_identity: { source_commit: '2'.repeat(40), source_version: '0.1.2-alpha.2', package_versions: {}, plugin_build_hash: buildHash },
    deadline_at: request.deadline_at, max_tool_calls: 12, tool_calls_started: 2, model_step_timeout_ms: 600_000,
    accepted_at: stamp(-1_000), last_seen_at: stamp(-900), terminal_at: null, last_seq: 0, error_code: null,
    trace_ref: null, result_ref: null, result_hash: null, created_at: stamp(-2_000), updated_at: stamp(-900) })
  return { binding, request, buildHash, context, admittedEvidence, spot, dispatch, link }
}
