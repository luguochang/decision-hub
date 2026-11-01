// Synthetic transport/session boundaries; exercise the real bridge context reader.
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { researchSessionRequestSchema } from '@decision-hub/contracts-ts'
import { DecisionHubHostBridge, resolveHostConfig } from '../src/host/bridge.js'
import { deterministicIds, type Correlation } from '../src/host/session-correlation.js'
import type { ModelAgentLineage, SessionEventLike } from '../src/host/dsh-public.js'
import type { HubClient } from '../src/host/hub-client.js'
import { LEGACY_SYNTHESIS_REF } from '../src/synthesis-protocol.js'

const V2 = 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2'
const V3 = 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3'
const ref = (name: string) => ({ ref: `synthetic:${name}`, hash: 'b'.repeat(64) })

function serverOriginals(runId: string, requestHash: string, stamp: string):
Awaited<ReturnType<HubClient['perpetualResearchBinding']>> {
  const body = '{"synthetic":true}'
  return { binding: { opportunity_id: 'synthetic-opportunity', context: {
    owner_id: 'synthetic-owner', account_kind: 'user_declared', account_id: 'unknown',
    instrument_id: 'okx:BTC-USDT-SWAP', scope_id: 'crypto:perp:okx:BTC-USDT-SWAP:trade:1m',
    profile_ref: ref('profile'), horizon: '4h', instrument_ref: ref('instrument'),
    settings_ref: ref('settings'), account_ref: null, position_ref: null, utility_ref: ref('utility'),
  }, candidate_target_ref: ref('target'), anchor_rule_ref: ref('anchor'),
  method_ref: ref('method'), method_code_hash: 'c'.repeat(64) },
  reviewOriginals: { schema_version: 'decision-review-originals.v1', run_id: runId,
    request_hash: requestHash, as_of: stamp, originals: [{
      source_ref: { ref: 'synthetic:server-original', hash: createHash('sha256').update(body).digest('hex') },
      original_ref: null, source_kind: 'server_material', role: 'control', body_json: body,
    }] } }
}

function canonicalRequest(outputSchema: string, runId: string, deadline: string) {
  return researchSessionRequestSchema.parse({ schema_version: 'research-session-request.v1',
    request_id: `research-request:${runId}`, run_id: runId, event_id: 'synthetic-event',
    trigger_snapshot_id: 'synthetic-trigger', domain_pack_ref: 'control', role_profile_ref: 'control',
    execution_mode: 'replay', research_mode: 'current_decision', pit_cutoff_at: deadline,
    current_round: 1, evidence_refs: ['synthetic-input'], input_evidence: [{
      evidence_id: 'synthetic-input', kind: 'market', authority: 'exchange', source_id: 'control-input',
      source_url: null, published_at: null, observed_at: deadline, received_at: deadline,
      content_hash: 'f'.repeat(64), excerpt: 'Synthetic frozen input; no financial qualification.',
    }], evidence_requirements: [{ requirement_id: 'macro_context', description: 'Synthetic control.',
      importance: 'hard', source_priority: ['control-source'], authority_floor: 'official',
      preferred_capabilities: ['official.macro'], freshness_seconds: 300,
      minimum_independent_sources: 1, allowed_fallbacks: [], confidence_cap: 0.5 }],
    target_gaps: [], allowed_capabilities: ['official.macro'],
    execution_budget: { max_evidence_rounds: 2, max_tool_calls: 12, max_subagents: 1,
      total_deadline_seconds: 600, per_tool_timeout_seconds: 60,
      per_model_step_timeout_seconds: 150, max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: deadline, output_schema_ref: outputSchema, repair_instructions: null,
  })
}

function contextFixture(outputSchema: string, invalidChild = false) {
  const runId = 'run-perpetual-context', requestHash = 'a'.repeat(64)
  const at = Date.now(), deadline = new Date(at + 600_000).toISOString()
  const ids = deterministicIds(runId, requestHash)
  const request = canonicalRequest(outputSchema, runId, deadline)
  const prompt = 'Canonical control request (untrusted text is replaced by typed references):\n'
    + JSON.stringify(request) + '\nBEGIN_UNTRUSTED_RESEARCH_DATA\n[]\nEND_UNTRUSTED_RESEARCH_DATA'
  const events: SessionEventLike[] = [{ seq: 0, time: at, type: 'user/message', data: {
    source: { kind: 'user', rpcId: ids.requestId }, content: [{ type: 'text', text: prompt }],
  } }]
  const agent: ModelAgentLineage = { id: ids.sessionId, status: 'running',
    session: { id: ids.sessionId, header: {}, deriveMessages: () => [] } }
  const correlation: Correlation = { ...ids, runId, requestHash, generation: 1,
    deadlineAt: deadline, modelStepTimeoutMs: 150_000, modelStepTimeoutSource: 'submission',
    modelStepTimeoutTriggered: false, state: 'running', lastSeq: 0, errorCode: null,
    acceptedAt: null, terminalAt: null }
  const originals = serverOriginals(runId, requestHash, new Date(at).toISOString())
  const bridge: DecisionHubHostBridge = Object.create(DecisionHubHostBridge.prototype)
  Object.assign(bridge, contextDependencies(agent, correlation, events, originals, invalidChild))
  return { originals, resolve: () => bridge.resolveSynthesisReviewContext(
    agent, request.request_id, new AbortController().signal) }
}

function contextDependencies(agent: ModelAgentLineage, correlation: Correlation,
  events: SessionEventLike[], originals: Awaited<ReturnType<HubClient['perpetualResearchBinding']>>,
  invalidChild: boolean) {
  const childId = 'synthetic-owned-child', sourceCommit = 'd'.repeat(40), sourceVersion = 'synthetic'
  const pluginBuildHash = 'e'.repeat(64)
  const link = { run_id: correlation.runId, dsh_session_id: agent.id,
    request_hash: correlation.requestHash, generation: 1, state: 'running', terminal_at: null,
    deadline_at: correlation.deadlineAt,
    upstream_identity: { source_commit: sourceCommit, source_version: sourceVersion,
      plugin_build_hash: pluginBuildHash } }
  const descriptor = { sessionId: childId, parentSessionId: invalidChild ? 'foreign' : agent.id,
    firstLiveSeq: 0, lifecycleId: 'synthetic-owned-birth' }
  return { config: resolveHostConfig({ sourceCommit, sourceVersion, pluginBuildHash }),
    ctx: { agents: { get: (id: string) => id === agent.id ? agent : undefined },
      sessionController: { inspect: async (id: string) => id === agent.id
        ? { meta: { id }, events } : { meta: { id, parentSession: agent.id },
          events: [{ seq: 0, time: events[0]!.time, type: 'session/start', data: {} }] } } },
    correlations: { getSession: (id: string) => id === agent.id ? correlation : undefined },
    cancellations: { requested: () => false }, terminalDrains: { closingForSession: () => false },
    activity: { assertNotFenced: () => {} },
    nativeCensuses: new Map([[agent.id, { correlation,
      collector: { researchSourceDescriptors: () => [descriptor] } }]]),
    hub: { linkBySession: async () => link,
      researchDetail: async () => ({ run: { run_id: correlation.runId }, evidence: [] }),
      businessStatus: async () => ({ status: 'researching' }),
      perpetualResearchBinding: async () => originals },
  }
}

describe('bridge selection of authenticated perpetual review context', () => {
  it.each([V2, V3])('retains admission, originals and explicit child delivery selection for %s', async schema => {
    const fixture = contextFixture(schema)
    const result = await fixture.resolve()
    expect(result.perpetualBinding).toEqual(fixture.originals.binding)
    expect(result.perpetualReviewOriginals).toEqual(fixture.originals.reviewOriginals)
    // No child prefix was delivered into the model's actual messages.
    expect(result.childResearchSources).toEqual([])
    expect(Object.isFrozen(result.perpetualReviewOriginals)).toBe(true)
  })
  it.each([V2, V3])('rejects a foreign child source for %s', async schema => {
    await expect(contextFixture(schema, true).resolve()).rejects.toThrow('research_provider_native_context_invalid')
  })
  it('keeps the ordinary legacy context free of perpetual admission and child sources', async () => {
    const result = await contextFixture(LEGACY_SYNTHESIS_REF, true).resolve()
    expect(result.perpetualBinding).toBeUndefined()
    expect(result.perpetualReviewOriginals).toBeUndefined()
    expect(result.childResearchSources).toBeUndefined()
  })
  it('rejects an unknown output schema instead of granting a default perpetual binding', async () => {
    await expect(contextFixture('synthetic:unsupported-synthesis').resolve())
      .rejects.toThrow('decision_hub_synthesis_protocol_unsupported')
  })
})
