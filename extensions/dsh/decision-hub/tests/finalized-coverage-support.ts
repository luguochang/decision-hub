// Controlled synthetic originals only. Never read/copy a private live Run here.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { foldSurface, isSurfaceEligibleType } from '@deepseek-ai/dsh-session/surface'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import { evidenceCandidateSchema, researchCapabilityResultSchema, researchProviderToolEmissionRecordedSchema,
  researchSessionRequestSchema, type EvidenceCandidate, type ResearchCapabilityResult } from '@decision-hub/contracts-ts'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { registerSynthesisReviewContext, readQualifiedReviewerContext, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { declaredChildResearchDelivery, readChildResearchSources, type ChildResearchPrefix } from '../src/host/child-research-context.js'
import { makeRetainedRootContextMessage, replaceRetainedRootContextMessage } from '../src/host/retained-context.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { providerCanonicalJson, registerProviderInputReader, verifySynthesisProviderInput } from '../src/host/provider-input.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'
import type { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'

type FactEnvelope = ResearchCapabilityResult['facts'][number]
export const coverageAt = Date.parse('2026-01-01T00:10:00Z')
export const coverageBuild = 'b'.repeat(64)
export const textDigest = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex')
export const objectDigest = (value: unknown) => textDigest(providerCanonicalJson(value))
const stamp = (offset: number) => new Date(coverageAt + offset).toISOString()
export const nativeEvent = (seq: number, type: string, data: Record<string, unknown>, extra = {}): SessionEventLike =>
  ({ seq, type, time: coverageAt + seq, data, ...(isSurfaceEligibleType(type) ? { surfaceOp: 'append' } : {}), ...extra })

type MutableContext = Omit<SynthesisReviewContext, 'events' | 'admittedEvidence' | 'childResearchSources'> & {
  events: SessionEventLike[]; admittedEvidence: EvidenceCandidate[]
  childResearchSources?: SynthesisReviewContext['childResearchSources']
}
type PreparationOptions = Parameters<typeof preparePerpetualSynthesis>[1]
interface AuthoredFixture { draft: Record<string, unknown>; options: PreparationOptions }
export interface CoverageFixtureOptions {
  child?: 'accepted' | 'candidate' | 'none'
  facts?: boolean
  duplicateRoot?: boolean
  replacementCount?: number
}
export interface CoverageWire {
  schema_version: string
  binding: SynthesisReviewContext['binding']
  finalize_call_id: string
  finalize_call_seq: number
  root_cutoff: { before_seq: number; events_sha256: string }
  source_snapshot: { message_id: string; message_seq: number; body_sha256: string }
  child_prefixes: ChildResearchPrefix[]
  evidence_sources: { evidence_id: string; capability_id: string; execution_session_id: string; native_call_id: string }[]
  evidence_candidates: EvidenceCandidate[]
  facts: FactEnvelope[]
}
export interface FinalizedWrapper {
  schema_version: string
  candidate: unknown
  review_request: unknown
  preparation: unknown
  review_gate: { status: string; binding: SynthesisReviewContext['binding'] }
  coverage_delivery?: CoverageWire
}

function authoredFixture(): AuthoredFixture {
  return JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
}
function requestFor(binding: SynthesisReviewContext['binding']) {
  return researchSessionRequestSchema.parse({ schema_version: 'research-session-request.v1',
    request_id: `research-request:${binding.runId}`, run_id: binding.runId, event_id: 'synthetic-coverage-event',
    trigger_snapshot_id: 'synthetic-coverage-trigger', domain_pack_ref: 'synthetic-coverage', role_profile_ref: 'synthetic',
    execution_mode: 'live', research_mode: 'current_decision', pit_cutoff_at: stamp(600_000), current_round: 1,
    evidence_refs: ['synthetic-frozen-input'], input_evidence: [{ evidence_id: 'synthetic-frozen-input', kind: 'market',
      authority: 'exchange', source_id: 'synthetic', source_url: null, published_at: null, observed_at: stamp(-10_000),
      received_at: stamp(-9_000), content_hash: 'c'.repeat(64), excerpt: 'Synthetic frozen input, no financial qualification.' }],
    evidence_requirements: ['market_regime_context', 'root_context'].map(requirement_id => ({ requirement_id,
      description: 'Controlled child delivery requirement.', importance: 'hard', source_priority: ['official'],
      authority_floor: 'official', preferred_capabilities: ['data.historical'], allowed_fallbacks: [],
      freshness_seconds: 86400, minimum_independent_sources: 1, confidence_cap: 0.5 })),
    target_gaps: [], allowed_capabilities: ['data.historical'], execution_budget: { max_evidence_rounds: 2,
      max_tool_calls: 100, max_subagents: 4, total_deadline_seconds: 600, per_tool_timeout_seconds: 60,
      per_model_step_timeout_seconds: 150, max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: stamp(600_000), output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
    repair_instructions: null })
}
function observation(binding: SynthesisReviewContext['binding'], child: boolean, quality: 'accepted' | 'candidate') {
  return evidenceCandidateSchema.parse({ evidence_id: child ? 'child-daily-evidence' : 'root-only-evidence',
    requirement_id: child ? 'market_regime_context' : 'root_context', kind: 'official', authority: 'official',
    source_id: 'synthetic-daily', source_url: null, published_at: stamp(-5000), observed_at: stamp(-4000),
    received_at: stamp(-3000), content_hash: (child ? 'd' : 'e').repeat(64),
    excerpt: 'Controlled synthetic original; not a real market observation.', structured_payload_ref: null,
    // Provider-scoped identity intentionally differs from actual native call ID.
    tool_call_id: child ? 'provider-query-daily' : 'provider-query-root', research_session_id: binding.sessionId,
    round: 1, quality, freshness_status: 'fresh', conflict_group: null })
}
const dailyFields = ['period_close', 'sma_20', 'sma_60', 'return_20_pct', 'realized_vol_20_pct',
  'volume_ratio_20', 'rolling_high_20', 'rolling_low_20', 'row_count']
export const receiptAttributes = { synthetic: true, dataset_id: 'synthetic.daily',
  daily_execution_receipt_json: JSON.stringify({ schema_version: 'synthetic-daily-execution.v1',
    source_kind: 'controlled-test', original: 'complete synthetic execution receipt '.repeat(40) }),
  daily_strategy_receipt_json: JSON.stringify({ schema_version: 'synthetic-strategy.v1', bar_count: 89, probability: null }) }
function capability(evidence: EvidenceCandidate, queryId: string, withFacts = true) {
  return researchCapabilityResultSchema.parse({ schema_version: 'research-capability-result.v1',
    request_id: queryId, capability_id: 'data.historical', provider: 'synthetic-daily', evidence_candidates: [evidence],
    facts: withFacts ? dailyFields.map((field, index) => ({ schema_version: 'fact-envelope.v1',
      fact_id: `${evidence.evidence_id}-fact-${index}`, evidence_id: evidence.evidence_id,
      requirement_id: evidence.requirement_id, metric_family: 'crypto.spot.history', field,
      instrument: 'BTC-USDT', venue: 'synthetic', value: String(index), unit: 'count',
      window_start_at: stamp(-86400_000), window_end_at: stamp(-5000), event_offset: null,
      observed_at: evidence.observed_at, received_at: evidence.received_at, published_at: evidence.published_at,
      source_id: evidence.source_id, independence_group: 'synthetic-daily', quality: evidence.quality,
      delay_class: 'historical', payload_schema_ref: 'synthetic-fact.v1', payload_hash: objectDigest({ field, index }),
      attributes: receiptAttributes })) : [], cost_usd: 0, completed_at: stamp(-2000) })
}
function emission(binding: SynthesisReviewContext['binding'], session: string, call: string,
  args: unknown, tool = 'decision_hub_research') {
  return researchProviderToolEmissionRecordedSchema.parse({ schema_version: 'research-provider-tool-emission-recorded.v1',
    reference: { output_id: `synthetic-proof:${call}`, role: 'provider-emission',
      schema_version: 'research-provider-tool-emission.v1', payload_hash: 'f'.repeat(64), recorded_at: stamp(-1000) },
    input_ref: { output_id: 'synthetic-provider-input', payload_hash: 'a'.repeat(64) }, run_id: binding.runId,
    generation: binding.generation, root_session_id: binding.sessionId, execution_session_id: session,
    dispatch_id: `synthetic-dispatch:${session}`, call_id: call, tool_name: tool,
    raw_arguments_hash: objectDigest(args), arguments_hash: objectDigest(args), candidate_hash: null,
    proof_scope: 'dsh_llm_generate_options' })
}
function capabilityEvents(result: ReturnType<typeof capability>, callId: string): SessionEventLike[] {
  return [nativeEvent(0, 'session/start', {}), nativeEvent(1, 'tool/call', { callId,
    name: 'decision_hub_research', arguments: { capability_id: 'data.historical', request_id: result.request_id } }),
  nativeEvent(2, 'tool/result', { message: { id: `result:${callId}`, role: 'tool', source: { kind: 'tool', callId },
    content: [{ type: 'tool-result', toolCallId: callId, isError: false,
      content: [{ type: 'text', text: JSON.stringify(result) }] }] } }, { surfaceOp: 'append', sourceEventSeqs: [1] })]
}
function childOriginals(binding: SynthesisReviewContext['binding'], result: ReturnType<typeof capability>) {
  const childId = 'synthetic-authenticated-research-child', callId = 'actual-child-native-call'
  const events = capabilityEvents(result, callId)
  const descriptor = { sessionId: childId, parentSessionId: binding.sessionId, firstLiveSeq: 1, lifecycleId: 'synthetic-owned-birth' }
  const read = (declared?: readonly ChildResearchPrefix[], current = events) => readChildResearchSources({ binding,
    descriptors: [descriptor], declared, signal: new AbortController().signal, deadlineAt: stamp(600_000),
    controller: { inspect: async session => {
      if (session !== childId) throw new Error('Unexpected synthetic child inspect')
      return { meta: { id: childId, parentSession: binding.sessionId }, events: current }
    } }, hub: { providerToolEmission: async (run, generation, session, nativeCall) => {
      if (run !== binding.runId || generation !== binding.generation || session !== childId) throw new Error('Unexpected synthetic source')
      const original = current.find(event => event.type === 'tool/call' && event.data.callId === nativeCall)
      if (!original) throw new Error('No actual synthetic native call')
      return emission(binding, childId, nativeCall, original.data.arguments)
    } } })
  return { childId, callId, events, read }
}

/** Use the official surface fold, not latest-message/count-one heuristics. */
export function activeRootMessages(context: SynthesisReviewContext): UserMessage[] {
  const nodes = new Set(foldSurface(context.events as Parameters<typeof foldSurface>[0]).nodes)
  return context.events.filter(event => nodes.has(event.seq) && event.type === 'user/message')
    .map(event => event.data as unknown as UserMessage)
}
function rootDeclaration(context: MutableContext, replacements: number) {
  const first = makeRetainedRootContextMessage(context, coverageBuild)
  context.events.push(nativeEvent(context.events.length, 'user/message', { ...first }))
  const session = { id: context.binding.sessionId, header: {}, events: context.events,
    deriveMessages: () => activeRootMessages(context), append: (type: string, data: unknown, extra = {}) => {
      const row = nativeEvent(context.events.length, type, { ...data as Record<string, unknown> }, extra)
      context.events.push(row)
      return row
    } }
  for (let index = 0; index < replacements; index++) {
    const proposed = makeRetainedRootContextMessage(context, coverageBuild)
    if (!replaceRetainedRootContextMessage(context, proposed, session, { estimateMessage: () => 1 })) {
      throw new Error('Synthetic official replacement was not applied')
    }
  }
  return { first, active: activeRootMessages(context).filter(message => message.source.kind === 'plugin').at(-1)! }
}
function appendCall(context: MutableContext, id: string, name: string, args: unknown, time: number) {
  const event = { ...nativeEvent(context.events.length, 'tool/call', { callId: id, name, arguments: args }), time }
  context.events.push(event)
  return event
}
async function independentlyReview(context: MutableContext, exec: (id: string) => never) {
  const reviewer = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer',
    backgroundMode: 'one-shot', persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 }, {
    root: reviewRoots.get(context)!, readQualified: readQualifiedReviewerContext, async start(_provider, input): Promise<any> {
      const prompt = (input.prompt[0] as { text: string }).text, lines = prompt.split('\n')
      const authorization = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
      authorization.verdict = 'approved'
      const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
      const output = [{ type: 'text', text: JSON.stringify([authorization, findings]) }]
      const events = [{ ...nativeEvent(0, 'user/message', { source: { kind: 'user' },
        content: [{ type: 'text', text: prompt }] }), time: coverageAt + 1000 },
      { ...nativeEvent(1, 'assistant/message', { message: { content: output } }), time: coverageAt + 1001 },
      { ...nativeEvent(2, 'turn/end', { reason: { kind: 'completed' } }), time: coverageAt + 1002 }]
      return { id: 'synthetic-independent-reviewer', localAgent: { id: 'synthetic-independent-reviewer',
        session: { header: { parentSession: context.binding.sessionId }, events } },
      result: Promise.resolve({ stopReason: 'completed', output }), dispose: async () => {} }
    } })
  return reviewer.execute({ description: 'Controlled independent coverage review', prompt: 'ignored', run_in_background: false }, exec('review'))
}
const reviewRoots = new WeakMap<SynthesisReviewContext, object>()

export async function coverageHarness(settings: CoverageFixtureOptions = {}) {
  const { draft, options } = authoredFixture(), runId = options.review_originals.run_id
  const binding = { runId, requestHash: options.review_originals.request_hash, generation: 1,
    ...deterministicIds(runId, options.review_originals.request_hash, 1) }
  const childEvidence = observation(binding, true, settings.child === 'candidate' ? 'candidate' : 'accepted')
  const childResult = capability(childEvidence, 'declared-child-query', settings.facts !== false)
  const rootEvidence = observation(binding, false, 'accepted')
  const rootResult = settings.duplicateRoot ? { ...childResult, request_id: 'declared-root-query' }
    : capability(rootEvidence, 'declared-root-query', false)
  const child = childOriginals(binding, childResult), rootEvents = capabilityEvents(rootResult, 'actual-root-native-call')
  const context: MutableContext = { binding, request: requestFor(binding), perpetualBinding: options.binding,
    perpetualReviewOriginals: options.review_originals,
    admittedEvidence: settings.duplicateRoot ? [childEvidence] : [rootResult.evidence_candidates[0]!, childEvidence],
    events: [nativeEvent(0, 'session/start', {}), nativeEvent(1, 'user/message', { id: 'synthetic-root-prompt', role: 'user',
      source: { kind: 'user', rpcId: binding.requestId }, content: [{ type: 'text', text: 'Controlled canonical product request.' }] }),
    { ...rootEvents[1]!, seq: 2 }, nativeEvent(3, 'tool/result', rootEvents[2]!.data,
      { surfaceOp: 'append', sourceEventSeqs: [2] })],
    childResearchSources: settings.child === 'none' ? [] : await child.read() }
  const declaration = rootDeclaration(context, settings.replacementCount ?? 0), root = {}
  reviewRoots.set(context, root)
  const unregister = registerSynthesisReviewContext(root, async () => context, async () => context)
  const unbind = registerProviderInputReader(root, { linkBySession: async () => ({ run_id: binding.runId,
    generation: 1, dsh_session_id: binding.sessionId, request_hash: binding.requestHash, harness_required: false } as never),
  providerToolEmission: async (_run, _gen, session, id) => {
    const call = context.events.find(event => event.type === 'tool/call' && event.data.callId === id)
    if (!call || session !== binding.sessionId) throw new Error('No actual synthesis Provider emission')
    return emission(binding, session, id, call.data.arguments, 'decision_hub_synthesis_submit')
  } })
  const dispose = () => { unregister(); unbind(); reviewRoots.delete(context) }
  try {
    return await prepareAndReview({ context, draft, declaration, child, childResult, dispose })
  } catch (error) { dispose(); throw error }
}
async function prepareAndReview(input: { context: MutableContext; draft: Record<string, unknown>;
  declaration: ReturnType<typeof rootDeclaration>; child: ReturnType<typeof childOriginals>;
  childResult: ReturnType<typeof capability>; dispose: () => void }) {
  const { context, draft } = input
  const exec = (id: string) => ({ agent: { id: context.binding.sessionId }, callId: id,
    signal: new AbortController().signal } as never)
  const synthesis = createSynthesisToolDefinition({ reviewContext: async () => context,
    providerInput: (current, session, id, args, signal) => verifySynthesisProviderInput(reviewRoots.get(context)!, current, session, id, args, signal) })
  const prepare = { action: 'prepare', request_id: context.request.request_id, draft }
  appendCall(context, 'prepare', 'decision_hub_synthesis_submit', prepare, coverageAt + 100)
  const prepared = await synthesis.execute(prepare, exec('prepare')) as unknown as FinalizedWrapper
  context.events.push({ ...nativeEvent(context.events.length, 'tool/result', { callId: 'prepare', value: prepared }), time: coverageAt + 101 })
  appendCall(context, 'review', 'subagent_reviewer', {}, coverageAt + 1000)
  const review = await independentlyReview(context, exec)
  context.events.push({ ...nativeEvent(context.events.length, 'tool/result', { callId: 'review', value: review }), time: coverageAt + 1003 })
  const args = { action: 'finalize', request_id: context.request.request_id, prepared_call_id: 'prepare' }
  const finalCall = appendCall(context, 'finalize', 'decision_hub_synthesis_submit', args, coverageAt + 2000)
  return { ...input, synthesis, prepared, review, args, finalCall, exec,
    finalize: async () => await synthesis.execute(args, exec('finalize')) as unknown as FinalizedWrapper,
    declared: () => declaredChildResearchDelivery(context.events, activeRootMessages(context)) }
}
