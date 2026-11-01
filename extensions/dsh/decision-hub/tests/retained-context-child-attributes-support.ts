// Complete synthetic tool/Provider originals; no live request or qualification.
import { createHash } from 'node:crypto'
import { dshModelDispatchBindingSchema, dshRunSessionLinkViewSchema, evidenceCandidateSchema,
  researchCapabilityResultSchema, researchProviderToolEmissionRecordedSchema,
  researchSessionRequestSchema } from '@decision-hub/contracts-ts'
import type { SynthesisReviewContext } from '../src/final-review-gate.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'
import { readChildResearchSources, type ChildResearchPrefix } from '../src/host/child-research-context.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { providerCanonicalJson } from '../src/host/provider-input.js'

export const fixtureAt = Date.parse('2026-10-25T12:00:00Z')
export const build = 'b'.repeat(64)
export const digest = (value: unknown) => createHash('sha256').update(providerCanonicalJson(value)).digest('hex')
type Attributes = Record<string, string | number | boolean | null>
export const largeAttributes = { original_json: 'retained original '.repeat(1_210), synthetic: true }
const stamp = (offset: number) => new Date(fixtureAt + offset).toISOString()
export const event = (seq: number, type: string, data: Record<string, unknown>, extra = {}): SessionEventLike =>
  ({ seq, type, time: fixtureAt, data, ...extra })

function request(runId: string) {
  return researchSessionRequestSchema.parse({
    schema_version: 'research-session-request.v1', request_id: `research-request:${runId}`, run_id: runId,
    event_id: 'event-child-attributes', trigger_snapshot_id: 'trigger-child-attributes',
    domain_pack_ref: 'synthetic-pack', role_profile_ref: 'synthetic-profile', execution_mode: 'live',
    research_mode: 'current_decision', pit_cutoff_at: stamp(600_000), current_round: 1,
    evidence_refs: ['synthetic-input'], input_evidence: [{ evidence_id: 'synthetic-input', kind: 'market',
      authority: 'exchange', source_id: 'synthetic-source', source_url: null, published_at: stamp(-3_000),
      observed_at: stamp(-2_000), received_at: stamp(-1_500), content_hash: 'c'.repeat(64),
      excerpt: 'Synthetic frozen input.' }], evidence_requirements: [{ requirement_id: 'market_daily_strategy',
      description: 'Declared test requirement.', importance: 'hard', source_priority: ['synthetic-source'],
      authority_floor: 'official', preferred_capabilities: ['data.historical'], freshness_seconds: 300,
      minimum_independent_sources: 1, allowed_fallbacks: [], confidence_cap: .5 }],
    target_gaps: [], allowed_capabilities: ['data.historical'],
    execution_budget: { max_evidence_rounds: 2, max_tool_calls: 100, max_subagents: 2,
      total_deadline_seconds: 600, per_tool_timeout_seconds: 60, per_model_step_timeout_seconds: 600,
      max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: stamp(600_000),
    output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
    repair_instructions: null,
  })
}

export async function childAttributesFixture(attributes: readonly Attributes[] = Array.from({ length: 9 }, () => largeAttributes)) {
  const runId = 'run-child-attributes', requestHash = 'a'.repeat(64)
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash) }
  const child = 'owned-child-attributes', callId = 'historical-child-call'
  const evidence = evidenceCandidateSchema.parse({ evidence_id: 'historical-child-evidence',
    requirement_id: 'market_daily_strategy', kind: 'official', authority: 'official', source_id: 'synthetic-source',
    source_url: null, published_at: stamp(-3_000), observed_at: stamp(-2_000), received_at: stamp(-1_500),
    content_hash: 'd'.repeat(64), excerpt: 'Synthetic complete historical tool result.', structured_payload_ref: null,
    tool_call_id: callId, research_session_id: binding.sessionId, round: 1,
    quality: 'accepted', freshness_status: 'fresh', conflict_group: null })
  const pending = evidenceCandidateSchema.parse({ ...evidence, evidence_id: 'not-delivered-evidence', tool_call_id: 'pending-call' })
  const result = researchCapabilityResultSchema.parse({ schema_version: 'research-capability-result.v1',
    request_id: callId, capability_id: 'data.historical', provider: 'synthetic-source', evidence_candidates: [evidence],
    facts: attributes.map((body, index) => ({ schema_version: 'fact-envelope.v1', fact_id: `historical-fact-${index}`,
      evidence_id: evidence.evidence_id, requirement_id: evidence.requirement_id, metric_family: 'market.control',
      field: `control-${index}`, instrument: 'BTC-USDT', venue: 'synthetic', value: String(index), unit: 'count',
      window_start_at: stamp(-86_400_000), window_end_at: stamp(-3_000), event_offset: null,
      observed_at: evidence.observed_at, received_at: evidence.received_at, published_at: evidence.published_at,
      source_id: evidence.source_id, independence_group: 'synthetic-source', quality: 'accepted', delay_class: 'historical',
      payload_schema_ref: 'synthetic-historical-fact.v1', payload_hash: digest({ index, body }), attributes: body })),
    cost_usd: 0, completed_at: stamp(-1_000) })
  const args = { capability_id: 'data.historical', request_id: callId }
  const childEvents = [event(0, 'session/start', {}), event(1, 'tool/call', {
    turn: 0, step: 0, callId, name: 'decision_hub_research', arguments: JSON.stringify(args) }),
  event(2, 'tool/result', { turn: 0, step: 0, message: { id: 'complete-child-result', role: 'tool',
    source: { kind: 'tool', callId }, content: [{ type: 'tool-result', toolCallId: callId, isError: false,
      content: [{ type: 'text', text: JSON.stringify(result) }] }] } }, { surfaceOp: 'append', sourceEventSeqs: [1] })]
  const descriptor = { sessionId: child, parentSessionId: binding.sessionId, firstLiveSeq: 1, lifecycleId: 'owned-child-birth' }
  const proof = researchProviderToolEmissionRecordedSchema.parse({
    schema_version: 'research-provider-tool-emission-recorded.v1', reference: { output_id: 'provider-proof',
      role: 'provider-emission', schema_version: 'research-provider-tool-emission.v1', payload_hash: 'e'.repeat(64), recorded_at: stamp(-2_000) },
    input_ref: { output_id: 'provider-input', payload_hash: 'f'.repeat(64) }, run_id: runId, generation: 1,
    root_session_id: binding.sessionId, execution_session_id: child, dispatch_id: 'child-dispatch', call_id: callId,
    tool_name: 'decision_hub_research', raw_arguments_hash: digest(args), arguments_hash: digest(args),
    candidate_hash: null, proof_scope: 'dsh_llm_generate_options' })
  const readSources = (declared?: readonly ChildResearchPrefix[], events = childEvents) => readChildResearchSources({
    binding, descriptors: [descriptor], signal: new AbortController().signal, deadlineAt: stamp(600_000), declared,
    controller: { inspect: async id => {
      if (id !== child) throw new Error('unexpected child read')
      return { meta: { id: child, parentSession: binding.sessionId }, events }
    } }, hub: { providerToolEmission: async (run, generation, session, call) => {
      if (run !== runId || generation !== 1 || session !== child || call !== callId) throw new Error('unexpected Provider proof read')
      return proof
    } },
  })
  const context: SynthesisReviewContext = { binding, request: request(runId),
    events: [event(0, 'session/start', {}), event(1, 'user/message', { id: 'root-prompt', role: 'user',
      source: { kind: 'user', rpcId: binding.requestId }, content: [{ type: 'text', text: 'Synthetic canonical root prompt.' }] })],
    admittedEvidence: [evidence, pending], childResearchSources: await readSources() }
  return { context, result, evidence, pending, childEvents, proof, readSources }
}

export function modelBinding(context: SynthesisReviewContext) {
  const { binding } = context
  const dispatch = dshModelDispatchBindingSchema.parse({ schema_version: 'dsh-model-dispatch-binding.v1',
    run_id: binding.runId, dsh_session_id: binding.sessionId, request_hash: binding.requestHash,
    generation: 1, plugin_build_hash: build, execution_session_id: null, parent_session_id: null,
    provider: 'synthetic', model: 'synthetic', purpose: 'conversation', reasoning_effort: null,
    temperature: null, max_tokens: 4096, stop_sequences_hash: null })
  const link = dshRunSessionLinkViewSchema.parse({ schema_version: 'dsh-run-session-link.v1',
    run_id: binding.runId, dsh_session_id: binding.sessionId, request_hash: binding.requestHash,
    generation: 1, state: 'running', upstream_identity: { source_commit: '2'.repeat(40),
      source_version: '0.1.2-alpha.2', package_versions: {}, plugin_build_hash: build },
    deadline_at: context.request.deadline_at, max_tool_calls: 100, tool_calls_started: 1, model_step_timeout_ms: 600_000,
    accepted_at: stamp(-1_000), last_seen_at: stamp(-900), terminal_at: null, last_seq: 2, error_code: null,
    trace_ref: null, result_ref: null, result_hash: null, created_at: stamp(-2_000), updated_at: stamp(-900) })
  return { dispatch, link }
}
