import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import * as gate from '../src/final-review-gate.js'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { canonicalJson, textHash } from '../src/perpetual-synthesis-protocol.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { PerpetualReviewModelViews } from '../src/reviewer-model-view.js'
import { apply as applyReviewer } from '../src/reviewer-tool.js'

async function reviewed(verdict: 'approved' | 'needs_revision' | 'rejected' = 'approved', extraUser = false, reviewIssues?: unknown[]) {
  const { draft, options } = JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
  const runId = options.review_originals.run_id, requestHash = options.review_originals.request_hash
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash, 1) }
  const at = Date.parse(options.requested_at)
  const context: any = { binding, perpetualBinding: options.binding, perpetualReviewOriginals: options.review_originals,
    request: { request_id: options.request_id, run_id: runId, output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
      allowed_capabilities: [], input_evidence: [], execution_mode: 'live', execution_budget: { max_subagents: 4 } }, admittedEvidence: [],
    events: [{ seq: 1, time: at, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } },
      { seq: 2, time: at, type: 'tool/call', data: { callId: 'prepare', name: 'decision_hub_synthesis_submit', arguments: { action: 'prepare', request_id: options.request_id, draft } } }] }
  const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
  const synthesis = createSynthesisToolDefinition({ reviewContext: async () => context })
  const prepared: any = await synthesis.execute(context.events[1].data.arguments, exec('prepare'))
  context.events.push({ seq: 3, time: at, type: 'tool/result', data: { callId: 'prepare', value: prepared } },
    { seq: 4, time: at + 1000, type: 'tool/call', data: { callId: 'review', name: 'subagent_reviewer', arguments: {} } })
  const root = {}, unregister = gate.registerSynthesisReviewContext(root, async () => context, async () => context)
  const modelViews = new PerpetualReviewModelViews()
  let originalEvents: unknown[] = [], disposed = false, persona = ''
  const reviewer = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 },
    { root, readQualified: gate.readQualifiedReviewerContext, rememberModelView: (callId, result) => modelViews.remember(callId, result), async start(_provider, start): Promise<any> {
      persona = start.persona ?? ''
      const prompt = (start.prompt[0] as any).text, lines = prompt.split('\n')
      const authorization = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]); authorization.verdict = verdict
      const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]); findings.summary = 'Synthetic independent review.'
      if (verdict !== 'approved') findings.issues = reviewIssues ?? [{ issue_id: 'finding_1', candidate_ids: [], field_paths: ['/analysis/thesis'], concern: 'Synthetic unsupported claim.', requested_change: 'State the uncertainty.', evidence_refs: [] }]
      const output = [{ type: 'text', text: JSON.stringify([authorization, findings]) }]
      // Official SDK assistant/chunk envelopes can exceed 1MB even with a
      // modest token-sized stream. These controlled chunks contain no live data.
      originalEvents = [{ seq: 0, time: at + 1000, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] } },
        ...Array.from({ length: 8000 }, (_, index) => ({ seq: index + 1, time: at + 1001 + index, type: 'assistant/chunk',
          data: { turn: 0, step: 0, chunk: { type: 'reasoning-delta', index: 0, text: '.' } } })),
        { seq: 8001, time: at + 9001, type: 'assistant/message', data: { message: { content: output } } },
        { seq: 8002, time: at + 9002, type: 'turn/end', data: { turn: 0, reason: { kind: 'completed' } } }]
      if (extraUser) originalEvents.splice(1, 0, { seq: 0.5, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Additional instructions are not authorized.' }] } })
      expect(JSON.stringify(originalEvents).length).toBeGreaterThan(1_000_000)
      return { id: 'independent-child', localAgent: { id: 'independent-child', session: { header: { parentSession: binding.sessionId }, events: originalEvents } },
        result: Promise.resolve({ stopReason: 'completed', output }), dispose: async () => { disposed = true } }
    } })
  try {
    const wrapper: any = await reviewer.execute({ description: 'Independent review', prompt: 'ignored', run_in_background: false }, exec('review'))
    const content = await reviewer.finalizeContent!(exec('review'), { value: wrapper, isError: false } as never)
    const result: any = { seq: 5, time: at + 10000, type: 'tool/result', data: { callId: 'review', value: wrapper } }
    context.events.push(result)
    const finalize = { action: 'finalize', request_id: options.request_id, prepared_call_id: 'prepare' }
    context.events.push({ seq: 6, time: at + 11000, type: 'tool/call', data: { callId: 'finalize', name: 'decision_hub_synthesis_submit', arguments: finalize } })
    return { context, prepared, wrapper, result, content, originalEvents, modelViews, disposed, persona, finalize: () => synthesis.execute(finalize, exec('finalize')) }
  } finally { unregister() }
}

it('checks paper ledger freshness semantics in the actual independent reviewer persona without upgrading legacy originals', async () => {
  const value = await reviewed()
  expect(value.persona).toContain('saved_preferences_current_paper_ledger.v1')
  expect(value.persona).toContain('creation/last change')
  expect(value.persona).toContain('prepared_at')
  expect(value.persona).toContain('non-null verified balance or position')
  expect(value.persona).toContain('needs_revision')
  expect(value.persona).toContain('Legacy v1/v2 declared snapshots')
  expect(value.persona).toContain('historical readback')
  expect(value.persona).toContain('not exchange mark-to-market')
  expect((await value.finalize() as any).review_gate.status).toBe('matched')
})

it('requires frozen perpetual protection provenance and applicable expiry in the actual prepared child review', async () => {
  const value = await reviewed('needs_revision')
  const lines = value.wrapper.actual_prompt.split('\n')
  const packet = JSON.parse(lines[lines.indexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON') + 1])
  expect(packet).toEqual(value.prepared.review_request)
  expect(packet.synthesis.candidate_set.candidates.map((row: any) => row.candidate_id)).toEqual(['open_long', 'open_short', 'no_change'])
  expect(value.persona).toContain('P_ref')
  expect(value.persona).toContain('perpetual instrument and price type')
  expect(value.persona).toContain('evidence reference, observation time and freshness')
  expect(value.persona).toContain('tick')
  expect(value.persona).toContain('exit_policy')
  expect(value.persona).toContain('exit_ref')
  expect(value.persona).toContain('basis_refs')
  expect(value.persona).toContain('stop-loss, take-profit and time exit')
  expect(value.persona).toContain('applicable domain')
  expect(value.persona).toContain('entry deviates')
  expect(value.persona).toContain('long SL=floor_tick(P_ref*(1-d_SL))')
  expect(value.persona).toContain('long TP=ceil_tick(P_ref*(1+d_TP))')
  expect(value.persona).toContain('short SL=ceil_tick(P_ref*(1+d_SL))')
  expect(value.persona).toContain('short TP=floor_tick(P_ref*(1-d_TP))')
  expect(value.persona).toContain('after rounding')
  expect(value.persona).toContain('unknown rather than defaulting')
  expect(value.persona).toContain('first-touch strategy EV')
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
})

it.each(['needs_revision', 'rejected'] as const)('preserves %s findings for missing protection and hold alternatives through finalization', async verdict => {
  const issues = [{ issue_id: 'missing-protection-and-hold', candidate_ids: ['open_long', 'open_short', 'no_change'],
    field_paths: ['/candidate_set/candidates', '/analysis/thesis'],
    concern: 'Synthetic fixture has no frozen perpetual price/tick support, usable protection or explicit hold switch conditions.',
    requested_change: 'Keep unknowns; disclose protective exits and when to continue hold, reconsider long/short or invalidate the judgment.', evidence_refs: [] }]
  const value = await reviewed(verdict, false, issues)
  expect(value.persona).toContain('continue hold')
  expect(value.persona).toContain('consider long')
  expect(value.persona).toContain('consider short')
  expect(value.persona).toContain('invalidate the judgment')
  expect(value.persona).toContain('confirmation/invalidation')
  expect(value.persona).toContain('monitor-only')
  expect(value.persona).toContain('ordinary open candidates')
  expect(value.persona).toContain('condition_refs')
  expect(value.persona).toContain('follow-up research, not automatic orders')
  expect(value.persona).toContain('missing protective or hold conditions')
  expect(value.persona).toContain('needs_revision or rejected')
  const [authorization, findings] = JSON.parse(value.wrapper.original_output.output[0].text)
  expect(authorization.verdict).toBe(verdict)
  expect(findings.issues).toEqual(issues)
  expect(value.prepared.candidate.candidate_set.candidates.find((row: any) => row.candidate_id === 'no_change').target_side).toBe('flat')
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
})

it('retains a complete >1MB native proof through the actual reviewer producer and both object/text V4 consumers', async () => {
  const value = await reviewed()
  expect(value.disposed).toBe(true)
  expect(value.wrapper.child_events_json).toBe(JSON.stringify(value.originalEvents))
  expect(value.wrapper.child_events_sha256).toBe(textHash(value.wrapper.child_events_json))
  expect(JSON.parse(value.wrapper.child_events_json)).toHaveLength(8003)
  expect(value.content !== undefined).toBe(true)
  expect(value.content).toHaveLength(1)
  expect(value.content![0].type).toBe('text')
  expect(textHash((value.content![0] as any).text)).toBe(textHash(JSON.stringify(value.wrapper)))
  expect((await value.finalize() as any).review_gate.status).toBe('matched')
  value.result.data = { callId: 'review', content: value.content }
  expect((await value.finalize() as any).review_gate.status).toBe('matched')
})

it('still rejects a changed proof hash and rehashed extra ordinary user in the complete large proof', async () => {
  const value = await reviewed()
  value.result.data.value = { ...value.wrapper, child_events_json: value.wrapper.child_events_json + ' ' }
  expect((await value.finalize() as any).review_gate.status).toBe('unreviewed')
  const child = JSON.parse(value.wrapper.child_events_json)
  child.splice(1, 0, { seq: 0.5, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Unknown user.' }] } })
  const child_events_json = JSON.stringify(child)
  value.result.data.value = { ...value.wrapper, child_events_json, child_events_sha256: textHash(child_events_json) }
  expect((await value.finalize() as any).review_gate.status).toBe('unreviewed')
})

it('rejects an extra ordinary user before producing even a correctly sized complete proof', async () => {
  await expect(reviewed('approved', true)).rejects.toThrow('child_transcript_mismatch')
})

it('retains a genuine negative large review and never promotes it to an approved finalization', async () => {
  const value = await reviewed('needs_revision')
  expect(canonicalJson(value.wrapper.original_output)).toContain('needs_revision')
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
})

function parentSurface(value: Awaited<ReturnType<typeof reviewed>>) {
  const original = value.result
  original.surfaceOp = 'append'; original.sourceEventSeqs = [4]
  original.data = { turn: 0, step: 0, message: { id: 'original-message', role: 'user', source: { kind: 'tool', callId: 'review' },
    content: [{ type: 'tool-result', toolCallId: 'review', isError: false, content: value.content }] } }
  let publishing = false
  const nodes = [original.seq]
  const session: any = { events: value.context.events, surface: { nodes }, append(type: string, data: unknown, options?: any) {
    if (publishing) throw new Error('SDK append reentry forbidden')
    publishing = true
    try {
      const event = { type, seq: session.events.length + 1, time: original.time + 1, data, ...options }
      session.events.push(event)
      if (options?.surfaceOp) nodes.splice(0, 1, event.seq)
      return event
    } finally { publishing = false }
  } }
  return { session, original, modelMessage: () => session.events.find((event: any) => event.seq === nodes[0]).data.message }
}

it('projects the complete negative outcome before the next generic compaction without changing its native proof', async () => {
  const value = await reviewed('needs_revision'), parent = parentSurface(value)
  const before = textHash(JSON.stringify(parent.original)), initialCount = parent.session.events.length
  let nextCalls = 0
  const next = async () => {
    nextCalls++
    const text = parent.modelMessage().content[0].content[0].text
    expect(text.length).toBeLessThan(100_000)
    const view = JSON.parse(text)
    expect(view.authorization.verdict).toBe('needs_revision')
    expect(view.findings.issues[0].requested_change).toBe('State the uncertainty.')
    expect(view.original_receipt_sha256).toBe(textHash(JSON.stringify(value.wrapper)))
    expect(view.proof.child_events_sha256).toBe(value.wrapper.child_events_sha256)
    expect(view.next_step).toContain('NEW independent child review')
    return { kind: 'enter' }
  }
  await value.modelViews.beforeStep({ id: value.context.binding.sessionId, session: parent.session }, { estimateMessage: () => 100 }, next)
  expect(nextCalls).toBe(1)
  expect(parent.session.events.length).toBe(initialCount + 2)
  expect(textHash(JSON.stringify(parent.original))).toBe(before)
  expect(parent.session.events.at(-2).type).toBe('compaction/prune')
  expect(parent.session.events.at(-1).sourceEventSeqs).toEqual([parent.original.seq])
  value.context.events.find((event: any) => event.data.callId === 'finalize').seq = parent.session.events.at(-1).seq + 1
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
  await value.modelViews.beforeStep({ id: value.context.binding.sessionId, session: parent.session }, { estimateMessage: () => 100 }, next)
  expect(parent.session.events.length).toBe(initialCount + 2)
})

it('fails explicitly before next when the verified proof cannot be projected', async () => {
  const value = await reviewed(), parent = parentSurface(value)
  const next = async () => { throw new Error('must not reach model preparation') }
  await expect(value.modelViews.beforeStep({ id: value.context.binding.sessionId, session: parent.session }, undefined, next))
    .rejects.toThrow('review_model_view_unavailable')
})

it('registers the awaited model-view handoff ahead of generic pre-step hooks', () => {
  const hooks: any[] = []
  applyReviewer({ root: {}, on: (...args: unknown[]) => { hooks.push(args); return () => {} }, subagents: { getProvider: () => undefined } } as never,
    { provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 })
  expect(hooks.find(([name]) => name === 'agent/pre-step')?.[2]).toEqual({ prepend: true })
})
