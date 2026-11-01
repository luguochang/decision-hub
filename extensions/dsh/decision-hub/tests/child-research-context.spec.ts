import { createHash } from 'node:crypto'
import { expect, it } from 'vitest'
import { trustedSynthesisEvidence, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { makeRetainedRootContextMessage, verifyRetainedRootContext } from '../src/host/retained-context.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { providerCanonicalJson } from '../src/host/provider-input.js'
import { readChildResearchSources, declaredChildResearchDelivery } from '../src/host/child-research-context.js'
import { NativeConversationCensusCollector } from '../src/host/native-conversation-census.js'
import { evidenceCandidateSchema } from '@decision-hub/contracts-ts'
import { guardedModelStream } from '../src/host/model-dispatch.js'

const hash = (value: unknown) => createHash('sha256').update(providerCanonicalJson(value)).digest('hex')
const at = Date.now(), stamp = new Date(at - 1_000).toISOString(), build = 'b'.repeat(64)
const binding = { runId: 'run-child-control', requestHash: 'a'.repeat(64), generation: 1,
  ...deterministicIds('run-child-control', 'a'.repeat(64)) }
const child = 'child-owned-control'
const evidence = evidenceCandidateSchema.parse({ evidence_id: 'macro-child-original', requirement_id: 'macro_context', kind: 'official', authority: 'official',
  source_id: 'control-source', source_url: null, published_at: stamp, observed_at: stamp, received_at: stamp,
  content_hash: 'd'.repeat(64), excerpt: 'Authenticated child macro observation.', structured_payload_ref: null,
  tool_call_id: 'macro-call', research_session_id: binding.sessionId, round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null })
const result = { schema_version: 'research-capability-result.v1', request_id: 'macro-call', capability_id: 'official.macro',
  provider: 'control-source', evidence_candidates: [evidence], facts: [], cost_usd: 0, completed_at: stamp }
const event = (seq: number, type: string, data: any, extra = {}) => ({ seq, type, time: at, data, ...extra })
const args = { capability_id: 'official.macro', request_id: 'macro-call' }
const childEvents = [event(0, 'session/start', {}), event(1, 'tool/call', { turn: 0, step: 0, callId: 'macro-call', name: 'decision_hub_research', arguments: JSON.stringify(args) }),
  event(2, 'tool/result', { turn: 0, step: 0, message: { id: 'result-control', role: 'tool', source: { kind: 'tool', callId: 'macro-call' },
    content: [{ type: 'tool-result', toolCallId: 'macro-call', isError: false, content: [{ type: 'text', text: JSON.stringify(result) }] }] } },
  { surfaceOp: 'append', sourceEventSeqs: [1] })]
function fixture(): SynthesisReviewContext {
  return { binding, request: { run_id: binding.runId, request_id: `research-request:${binding.runId}`, execution_mode: 'live',
    research_mode: 'current_decision', output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
    deadline_at: new Date(at + 600_000).toISOString(), input_evidence: [], allowed_capabilities: ['official.macro'] } as any,
    events: [event(0, 'session/start', {}), event(1, 'user/message', { id: 'root-prompt', role: 'user',
      source: { kind: 'user', rpcId: binding.requestId }, content: [{ type: 'text', text: 'Canonical root prompt.' }] })],
    admittedEvidence: [evidence] as any } as any
}
const descriptor = { sessionId: child, parentSessionId: binding.sessionId, firstLiveSeq: 1, lifecycleId: 'owned-birth' }
const proof = { run_id: binding.runId, generation: binding.generation, root_session_id: binding.sessionId,
  execution_session_id: child, call_id: 'macro-call', tool_name: 'decision_hub_research', arguments_hash: hash(args) }
async function sources(events = childEvents, declared?: any, mutation = {}, descriptors = [descriptor]) {
  return readChildResearchSources({ binding, descriptors, signal: new AbortController().signal,
    deadlineAt: fixture().request.deadline_at, declared,
    controller: { inspect: async () => ({ meta: { id: child, parentSession: binding.sessionId }, events }) },
    hub: { providerToolEmission: async (_run, _generation, _session, callId) => {
      const call = events.find(row => row.type === 'tool/call' && row.data.callId === callId)!
      return { ...proof, call_id: callId, arguments_hash: hash(typeof call.data.arguments === 'string' ? JSON.parse(call.data.arguments) : call.data.arguments), ...mutation } as any
    } } })
}

it('includes legitimate child research without flattening its native events into the root', async () => {
  const context = { ...fixture(), childResearchSources: await sources() }, original = providerCanonicalJson(context.events)
  expect(trustedSynthesisEvidence(context).evidence.map(item => item.evidence_id)).toEqual([evidence.evidence_id])
  expect(trustedSynthesisEvidence(context).evidence[0]).toEqual(evidence)
  expect(providerCanonicalJson(context.events)).toBe(original)
})

it('projects an explicitly unread delivery gap instead of crashing on a durable original not yet returned', () => {
  const context = { ...fixture(), childResearchSources: [] } as any
  const message = makeRetainedRootContextMessage(context, build)
  const body = JSON.parse((message.content[1] as any).text)
  expect(body.schema_version).toBe('decision-hub-protected-context.v5')
  expect(body.evidence_candidates).toEqual([])
  expect(body.pending_evidence_delivery).toEqual([{ evidence_id: evidence.evidence_id,
    requirement_id: evidence.requirement_id, reason_code: 'native_delivery_pending' }])
})

it('keeps the delivered child prefix stable across later child progress, including final-review evidence IDs', async () => {
  const context = { ...fixture(), childResearchSources: await sources() }
  const message = makeRetainedRootContextMessage(context, build)
  const events = [...context.events, event(2, 'user/message', message)]
  const declared = declaredChildResearchDelivery(events, [message])!
  const lateEvidence = { ...evidence, evidence_id: 'late-not-delivered', tool_call_id: 'macro-call-late', content_hash: 'e'.repeat(64) }
  const later = [...childEvents, event(3, 'tool/call', { callId: 'macro-call-late', toolName: 'decision_hub_research', arguments: { ...args, request_id: 'macro-call-late' } }),
    event(4, 'tool/result', { callId: 'macro-call-late', result: { ...result, request_id: 'macro-call-late', evidence_candidates: [lateEvidence] } }, { surfaceOp: 'append', sourceEventSeqs: [3] })]
  const pinned = await sources(later, declared.prefixes)
  expect(pinned[0]!.prefix).toEqual(context.childResearchSources[0]!.prefix)
  const current = { ...context, events, childResearchSources: pinned, deliveredPendingEvidence: declared.pending,
    admittedEvidence: [...context.admittedEvidence, lateEvidence] as any }
  const dispatch = { run_id: binding.runId, dsh_session_id: binding.sessionId, request_hash: binding.requestHash,
    generation: binding.generation, plugin_build_hash: build, execution_session_id: null, parent_session_id: null } as any
  const link = { ...dispatch, upstream_identity: { plugin_build_hash: build }, deadline_at: context.request.deadline_at } as any
  expect(() => verifyRetainedRootContext({ sessionId: binding.sessionId, messages: [message] } as any, dispatch, link, current)).not.toThrow()
  expect(trustedSynthesisEvidence(current).ids).toEqual([evidence.evidence_id])
  const refreshed = { ...current, childResearchSources: await sources(later), deliveredPendingEvidence: undefined }
  expect(trustedSynthesisEvidence(refreshed).ids).toEqual(['late-not-delivered', evidence.evidence_id])
})

it.each(['prefix', 'Provider hash', 'Provider child', 'Provider root', 'Provider receipt'])( 'rejects changed %s attribution', async mutation => {
  const selected = await sources(), declared = selected.map(source => source.prefix)
  const events = mutation === 'prefix' ? childEvents.map(row => row.seq === 2 ? { ...row, time: at + 1 } : row) : childEvents
  const changed = mutation === 'Provider hash' ? { arguments_hash: '0'.repeat(64) }
    : mutation === 'Provider child' ? { execution_session_id: 'foreign-child' }
      : mutation === 'Provider root' ? { root_session_id: 'foreign-root' }
        : mutation === 'Provider receipt' ? { known_at: 'changed-immutable-receipt' } : {}
  await expect(sources(events, declared, changed)).rejects.toThrow('research_provider_native_context_invalid')
})

it('passes actual frozen model messages through the owned reader before the first Provider delegate', async () => {
  const before = { ...fixture(), childResearchSources: await sources() }
  const message = makeRetainedRootContextMessage(before, build)
  const context = { ...before, events: [...before.events, event(2, 'user/message', message)] }
  const messages = [message], order: string[] = []
  const ref = { output_id: 'input-control', role: 'model_input:control', schema_version: 'research-provider-input.v1', payload_hash: 'c'.repeat(64), recorded_at: stamp }
  const link = { run_id: binding.runId, dsh_session_id: binding.sessionId, request_hash: binding.requestHash,
    generation: 1, upstream_identity: { plugin_build_hash: build }, deadline_at: context.request.deadline_at }
  const hub = { linkBySession: async () => link, bindModel: async () => { order.push('binding') },
    recordProviderInput: async (value: any) => { order.push('input'); return { schema_version: 'research-provider-input-recorded.v1',
      reference: ref, dispatch_id: value.dispatch_id, input_hash: value.input_hash, known_at: stamp,
      producer_authenticated: true, proof_scope: 'dsh_llm_generate_options' } },
    recordProviderToolEmission: async () => { throw new Error('no tool emission in this control') },
    recordProviderFinish: async (value: any) => ({ schema_version: 'research-provider-finish-recorded.v1',
      reference: { ...ref, output_id: 'finish-control' }, dispatch_id: value.dispatch_id, input_ref: value.input_ref,
      finish_hash: value.finish_hash, status: value.status, known_at: stamp, proof_scope: 'dsh_llm_generate_options' }) }
  const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
    started_at: stamp, hub_base_url: 'http://synthetic.test', workspace_id: 'control', workspace_cwd_hash: 'f'.repeat(64) }
  const chunks: any[] = []
  for await (const chunk of guardedModelStream({ sessionId: binding.sessionId, provider: 'synthetic', model: 'synthetic', messages } as any,
    async function* () { order.push('delegate'); yield { type: 'finish', reason: { kind: 'stop' } } }, hub as any, build,
    undefined, undefined, async () => scope, async (_binding, _link, _signal, actual) => {
      expect(actual).toBe(messages); order.push('retained'); return context
    }, undefined, () => context.request.deadline_at)) chunks.push(chunk)
  expect(order).toEqual(['binding', 'retained', 'input', 'delegate'])
  expect(chunks.at(-1)?.reason.kind).toBe('stop')
})

it('does not promote copied seed calls, and rejects unregistered or foreign lineage', async () => {
  const context = fixture()
  const onlySeed = await readChildResearchSources({ binding, descriptors: [{ ...descriptor, firstLiveSeq: 3 }],
    signal: new AbortController().signal, deadlineAt: context.request.deadline_at,
    controller: { inspect: async () => ({ meta: { id: child, parentSession: binding.sessionId }, events: childEvents }) },
    hub: { providerToolEmission: async () => { throw new Error('seed must not request Provider proof') } } })
  expect(onlySeed).toEqual([])
  await expect(readChildResearchSources({ binding, descriptors: [{ ...descriptor, parentSessionId: 'foreign' }],
    signal: new AbortController().signal, deadlineAt: context.request.deadline_at,
    controller: { inspect: async () => { throw new Error('foreign must not inspect') } }, hub: {} as any })).rejects.toThrow('research_provider_native_context_invalid')
})

it('keeps same-ID native contradictions fatal while an unrelated unread original stays a named gap', async () => {
  const context = { ...fixture(), childResearchSources: await sources(),
    admittedEvidence: [{ ...evidence, content_hash: '0'.repeat(64) }] as any }
  expect(() => makeRetainedRootContextMessage(context, build)).toThrow('research_provider_native_context_invalid')
})

it('does not accept a copied or fabricated child source as an owned original', async () => {
  const original = await sources()
  expect(() => trustedSynthesisEvidence({ ...fixture(), childResearchSources: structuredClone(original) })).toThrow('research_provider_native_context_invalid')
})

it('reuses birth-observed census descriptors even after the child is disposed, without diagnostic tool bodies', () => {
  const collector = new NativeConversationCensusCollector({ run_id: binding.runId, generation: 1,
    root_session_id: binding.sessionId, request_hash: binding.requestHash, plugin_build_hash: build }, true)
  const root = { id: binding.sessionId, status: 'idle', session: { id: binding.sessionId, header: {}, firstLiveSeq: 0, seq: 0, events: [] } } as any
  const owned = { id: child, status: 'idle', session: { id: child, header: { parentSession: binding.sessionId }, firstLiveSeq: 1,
    seq: childEvents.length, events: childEvents } } as any
  collector.created(root); collector.created(owned); collector.disposed(owned)
  expect(collector.researchSourceDescriptors()).toMatchObject([{ sessionId: child, parentSessionId: binding.sessionId, firstLiveSeq: 1 }])
})

function childResidency() {
  const collector = new NativeConversationCensusCollector({ run_id: binding.runId, generation: 1,
    root_session_id: binding.sessionId, request_hash: binding.requestHash, plugin_build_hash: build }, true)
  const root = { id: binding.sessionId, status: 'idle', session: { id: binding.sessionId, header: {}, firstLiveSeq: 0, seq: 0, events: [] } } as any
  // Constructor seed is index0; official firstLiveSeq is the pre-marker log length.
  const events = [event(0, 'session/start', {}), event(1, 'session/end-seed', {}),
    event(2, 'turn/start', { turn: 1 }), event(3, 'step/start', { turn: 1, step: 0 }),
    event(4, 'step/end', { turn: 1, step: 0 }), event(5, 'turn/end', { turn: 1, reason: { kind: 'completed' } })]
  const original = { id: child, status: 'idle', session: { id: child,
    header: { parentSession: binding.sessionId, seedLength: 1 }, firstLiveSeq: 1, seq: events.length, events } } as any
  collector.created(root); collector.created(original)
  const resumed = { id: child, status: 'running', session: { id: child, header: { ...original.session.header },
    firstLiveSeq: events.length, seq: events.length + 1, events: [...events, event(events.length, 'session/end-seed', {})] } } as any
  return { collector, original, resumed }
}

it('authenticates a disposed child cold resume while retaining its birth floor and frozen research prefix', async () => {
  const { collector, original, resumed } = childResidency()
  const before = collector.researchSourceDescriptors()
  collector.disposed(original); collector.created(resumed)
  collector.event(resumed.session, resumed.session.events.at(-1))
  expect(collector.researchSourceDescriptors()).toEqual(before)
  // V1 diagnostic still describes its original residency rather than claiming new coverage.
  expect(collector.snapshot().sessions.find(row => row.session_id === child)?.missing_codes).toContain('native_session_reentered')
  const delivered = await sources(childEvents, undefined, {}, before as typeof descriptor[])
  const pinned = await sources([...childEvents, event(3, 'session/end-seed', {}), event(4, 'turn/start', { turn: 2 })],
    delivered.map(row => row.prefix), {}, collector.researchSourceDescriptors() as typeof descriptor[])
  expect(pinned.map(row => row.prefix)).toEqual(delivered.map(row => row.prefix))
})

it('allows reopening an untouched end-seed without inventing an extra seed marker', () => {
  const { collector, original, resumed } = childResidency(), before = collector.researchSourceDescriptors()
  collector.disposed(original); collector.created(resumed)
  resumed.status = 'idle'; collector.disposed(resumed)
  const reopened = { ...resumed, session: { ...resumed.session, firstLiveSeq: resumed.session.seq } }
  collector.created(reopened); collector.created(reopened)
  expect(collector.researchSourceDescriptors()).toEqual(before)
})

it.each(['prefix', 'header', 'parent', 'seed', 'floor', 'overlap', 'stale disposal', 'stale event'])(
  'rejects child continuation with changed %s identity', mutation => {
    const { collector, original, resumed } = childResidency()
    if (mutation !== 'overlap') collector.disposed(original)
    if (mutation === 'prefix') resumed.session.events = resumed.session.events.map((row: any) => row.seq === 4 ? { ...row, time: at + 1 } : row)
    if (mutation === 'header') resumed.session.header = { ...resumed.session.header, agentPreset: 'foreign' }
    if (mutation === 'parent') resumed.session.header.parentSession = 'foreign'
    if (mutation === 'seed') resumed.session.events[0] = event(0, 'session/start', { changed: true })
    if (mutation === 'floor') resumed.session.firstLiveSeq--
    collector.created(resumed)
    if (mutation === 'stale disposal') collector.disposed(original)
    if (mutation === 'stale event') collector.event(original.session, original.session.events.at(-1))
    expect(() => collector.researchSourceDescriptors()).toThrow('research_provider_native_context_invalid')
  })

it('does not promote a missing-birth child when its session later reenters the registry', () => {
  const { collector, original } = childResidency()
  collector.unavailable('unknown-control-child', binding.sessionId)
  collector.created({ ...original, id: 'unknown-control-child', session: { ...original.session, id: 'unknown-control-child' } })
  expect(collector.researchSourceDescriptors().map(row => row.sessionId)).toEqual([child])
})
