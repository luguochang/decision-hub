import { createHash } from 'node:crypto'
import { expect, it } from 'vitest'
import type { DshSessionResult } from '@decision-hub/contracts-ts'
import { matchesAcknowledgedResult, projectAcknowledgedResult, projectInspection } from '../src/host/session-result.js'
import { acknowledgedLink, baseline, event, fixture } from './support/reviewer-instruction-host-binding.js'
import { harness, request } from './lifecycle-harness.js'
import { deterministicIds } from '../src/host/session-correlation.js'

it('derives both review cutoffs from full originals before filtering and never borrows the later baseline', () => {
  const f = fixture(), before = JSON.stringify(f.inspection)
  const result = projectInspection(f.correlation, f.inspection).result as any
  expect(result.reviewer_instruction_bindings).toMatchObject({
    schema_version: 'dsh-reviewer-instruction-bindings.v1', run_id: f.correlation.runId,
    request_hash: f.correlation.requestHash, request_id: f.correlation.requestId,
    dsh_session_id: f.correlation.sessionId, generation: 1,
    bindings: [
      { reviewer_call_id: 'review-negative', reviewer_call_seq: 3, reviewer_generation: 1,
        reviewer_request_id: f.correlation.requestId, parent_prefix_last_seq: 2, baseline_event: { seq: 1 } },
      { reviewer_call_id: 'review-approved', reviewer_call_seq: 8, reviewer_generation: 1,
        reviewer_request_id: f.correlation.requestId, parent_prefix_last_seq: 7, baseline_event: { seq: 7 } },
    ],
  })
  expect(result.reviewer_instruction_bindings.bindings[1].parent_prefix_sha256)
    .toBe(createHash('sha256').update(JSON.stringify(f.inspection.events.slice(0, 8))).digest('hex'))
  expect(result.reviewer_instruction_bindings.bindings[1].baseline_event.data).toEqual(f.inspection.events[7]!.data)
  expect(JSON.stringify(f.inspection)).toBe(before)
  expect(JSON.parse(result.events_json)[0].payload.event.seq).toBe(2)
  expect(result.result_hash).toBe(createHash('sha256').update('final answer').update('\0').update(result.events_json).digest('hex'))
})

it('binds historical reviewer calls to their original generation RPC before the active command', () => {
  const f = fixture(), originalRequestId = f.correlation.requestId
  f.correlation.generation = 2
  f.correlation.requestId = deterministicIds(f.correlation.runId, f.correlation.requestHash, 2).requestId
  f.inspection.events = [event(0, 'turn/start', {}), baseline(1),
    event(2, 'user/message', { source: { kind: 'user', rpcId: originalRequestId } }),
    event(3, 'tool/call', { name: 'subagent_reviewer', callId: 'review-negative' }),
    event(4, 'tool/result', { callId: 'review-negative' }),
    event(5, 'user/message', { source: { kind: 'user', rpcId: f.correlation.requestId } }),
    event(6, 'tool/call', { name: 'subagent_reviewer', callId: 'review-approved' }),
    event(7, 'tool/result', { callId: 'review-approved' }),
    event(8, 'assistant/message', { message: { content: [{ type: 'text', text: 'final answer' }] } }),
    event(9, 'turn/end', { reason: { kind: 'completed' } })]
  const result = projectInspection(f.correlation, f.inspection).result!
  expect(result.reviewer_instruction_bindings!.bindings).toMatchObject([
    { reviewer_call_id: 'review-negative', reviewer_generation: 1, reviewer_request_id: originalRequestId },
    { reviewer_call_id: 'review-approved', reviewer_generation: 2, reviewer_request_id: f.correlation.requestId },
  ])
})

it.each(['bare', 'provenance', 'range', 'changed', 'delta', 'duplicate', 'sparse', 'call-id', 'session', 'request'])
('rejects invalid parent instruction proof: %s', mode => {
  const f = fixture(), events = f.inspection.events as any[]
  if (mode === 'bare') events[6] = event(6, 'step/start', {})
  if (mode === 'provenance') events[6].sourceEventSeqs = [5]
  if (mode === 'range') events[6].surfaceOp.end = 5
  if (mode === 'changed') events[1].data.content[0].text += ' changed'
  if (mode === 'delta') events[1].data.source.baseline = false
  if (mode === 'duplicate') events[5] = baseline(5)
  if (mode === 'sparse') events.splice(0, 1)
  if (mode === 'call-id') events[8].data.callId = 'review-negative'
  if (mode === 'session') f.inspection.meta.id = 'wrong-session'
  if (mode === 'request') f.correlation.requestHash = 'd'.repeat(64)
  expect(() => projectInspection(f.correlation, f.inspection)).toThrow()
})

it('retains legacy omission when parent has no agent instruction baseline', () => {
  const f = fixture()
  f.inspection.events = f.inspection.events.map(row => row.type === 'user/message' && row.data.role === 'user'
    ? event(row.seq, 'step/start', {}) : row)
  expect(projectInspection(f.correlation, f.inspection).result).not.toHaveProperty('reviewer_instruction_bindings')
})

it('requires native reread for bound results and compares concrete projection despite unchanged old result hash', () => {
  const f = fixture(), result = projectInspection(f.correlation, f.inspection).result!, link = acknowledgedLink(f, result)
  f.correlation.terminalAt = link.terminal_at
  expect(matchesAcknowledgedResult(result, link)).toBe(false)
  expect(matchesAcknowledgedResult(result, link, f.inspection)).toBe(true)
  expect(projectAcknowledgedResult(f.correlation, f.inspection, link)).toEqual(result)
  for (const key of ['run', 'session', 'generation', 'request', 'call-id', 'call-seq', 'prefix-hash', 'prefix-cutoff', 'baseline', 'content', 'missing', 'duplicate']) {
    const tampered = structuredClone(result)
    const proof = tampered.reviewer_instruction_bindings!, binding = proof.bindings[1]!
    if (key === 'run') proof.run_id += 'wrong'
    if (key === 'session') proof.dsh_session_id += 'wrong'
    if (key === 'generation') proof.generation++
    if (key === 'request') proof.request_id += 'wrong'
    if (key === 'call-id') binding.reviewer_call_id += 'wrong'
    if (key === 'call-seq') binding.reviewer_call_seq++
    if (key === 'prefix-hash') binding.parent_prefix_sha256 = 'd'.repeat(64)
    if (key === 'prefix-cutoff') binding.parent_prefix_last_seq++
    if (key === 'baseline') binding.baseline_event.seq = 10
    if (key === 'content') binding.baseline_event.data.content[0]!.text += 'wrong'
    if (key === 'missing') delete tampered.reviewer_instruction_bindings
    if (key === 'duplicate') proof.bindings.push(structuredClone(binding))
    expect(tampered.result_hash).toBe(result.result_hash)
    expect(matchesAcknowledgedResult(tampered, link, f.inspection), key).toBe(false)
  }
})

it('cached binding tampering uses the official native reread and returns the newly derived original projection', async () => {
  const f = fixture(), result = projectInspection(f.correlation, f.inspection).result!, link = acknowledgedLink(f, result)
  const app = harness()
  app.state.link = link
  app.sessions.inspections.set(link.dsh_session_id, f.inspection)
  const bridge = app.bridge as unknown as { results: Map<string, DshSessionResult> }
  const tampered = structuredClone(result)
  tampered.reviewer_instruction_bindings!.bindings[1]!.parent_prefix_sha256 = 'd'.repeat(64)
  bridge.results.set(link.run_id, tampered)
  const reply = await app.call(request('GET', `/decision-hub/v1/runs/${link.run_id}/result`))
  expect(reply.status).toBe(200)
  expect(reply.json).toEqual(result)
  expect(app.sessions.inspect).toHaveBeenCalledOnce()
})
