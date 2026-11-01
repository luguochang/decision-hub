import { createHash } from 'node:crypto'
import { dshSessionResultSchema } from '@decision-hub/contracts-ts'
import { describe, expect, it, vi } from 'vitest'
import { commandDelegations, hasAcceptedSynthesisAfterPrompt, projectInspection } from '../src/host/session-result.js'
import type { Correlation } from '../src/host/session-correlation.js'
import type { SessionEventLike, SessionControllerPublic } from '../src/host/dsh-public.js'
import { harness, request, submitPayload } from './lifecycle-harness.js'

// Isolated protocol controls use the actual official event/control vocabulary.
// No provider, model, production database, or existing Method original is altered.
const now = Date.now()
const root = 'dsh-parent'
const child = 'child-one'
const correlation: Correlation = {
  runId: 'run-1', requestHash: 'a'.repeat(64), sessionId: root, requestId: 'req-1', generation: 1,
  modelStepTimeoutMs: 150000, modelStepTimeoutSource: 'submission', modelStepTimeoutTriggered: false,
  state: 'running', lastSeq: 0, errorCode: null, acceptedAt: null, terminalAt: null,
}
const event = (seq: number, type: string, data: Record<string, unknown>): SessionEventLike =>
  ({ seq, type, time: now + seq, data })
const prompt = (seq = 0, id = 'req-1') => event(seq, 'user/message', { source: { kind: 'user', rpcId: id } })
const call = (seq: number, name: string, callId: string, args: Record<string, unknown>) =>
  event(seq, 'tool/call', { name, callId, arguments: JSON.stringify(args) })
const result = (seq: number, callId: string, text: string, isError = false) => event(seq, 'tool/result', {
  message: { content: [{ type: 'tool-result', toolCallId: callId, isError, content: [{ type: 'text', text }] }] },
})
const assistant = (seq: number, text: string) => event(seq, 'assistant/message', { message: { content: [{ type: 'text', text }] } })
const end = (seq: number, kind = 'completed') => event(seq, 'turn/end', { reason: { kind } })
const settled = (seq: number, id = child) => event(seq, 'user/message', {
  source: { kind: 'subagent-settled', form: 'notice', senderSessionId: id, summary: 'settled' },
})
const started = () => [prompt(), call(1, 'subagent', 'delegate-1', { run_in_background: true }),
  result(2, 'delegate-1', `started subagent ${child}`), assistant(3, 'Waiting for roles'), end(4)]
const inspection = (events: SessionEventLike[]) => ({ meta: { id: root }, events })
const project = (events: SessionEventLike[], parentSeq = events.at(-1)!.seq, extra = {}) =>
  projectInspection(correlation, inspection(events), { commandLifecycle: true,
    activity: { parentSeq, parentRunning: false, queueCount: 0, activeJobCount: 0, ...extra } })

describe('original research command lifecycle', () => {
  it('retains first-turn plus outstanding child as running even with a momentarily empty queue', () => {
    const value = project(started())
    expect(value.status.state).toBe('running')
    expect(value.completion).toBeNull()
    expect(value.result).toBeNull()
  })

  it('requires real settlement rather than a report relay or another child notice', () => {
    for (const notice of [event(5, 'user/message', { source: { kind: 'subagent-report', senderSessionId: child } }), settled(5, 'other-child')]) {
      expect(project([...started(), notice, end(6)]).status.state).toBe('running')
    }
  })

  it('waits through notice and its unfinished continuation, then uses the final turn time', () => {
    const rows = [...started(), settled(5)]
    expect(project(rows).status.state).toBe('running')
    rows.push(event(6, 'turn/start', { turn: 2 }), assistant(7, 'actual final synthesis'))
    expect(project(rows, 7, { parentRunning: true }).status.state).toBe('running')
    rows.push(end(8))
    const value = project(rows)
    expect(value.status.state).toBe('completed')
    expect(value.result?.final_response).toBe('actual final synthesis')
    expect(value.result?.finished_at).toBe(new Date(now + 8).toISOString())
  })

  it.each([{ queueCount: 1 }, { activeJobCount: 1 }, { parentRunning: true }])('refuses incomplete control state %j', state => {
    expect(project([...started(), settled(5), assistant(6, 'final'), end(7)], 7, state).status.state).toBe('running')
  })

  it('rejects a stale parent cursor and unavailable SDK activity', () => {
    const rows = [...started(), settled(5), assistant(6, 'final'), end(7)]
    expect(project(rows, 6).status.state).toBe('running')
    expect(projectInspection(correlation, inspection(rows), { commandLifecycle: true }).status.state).toBe('running')
  })

  it('preserves failed-child notice for actual manager handling without treating it as a result', () => {
    const failed = settled(5)
    failed.data.source = { kind: 'subagent-settled', form: 'notice', senderSessionId: child, summary: 'failed before finished' }
    expect(project([...started(), failed]).completion).toBeNull()
    expect(project([...started(), failed, end(6, 'error')]).status.state).toBe('failed')
  })

  it('does not turn a missing final into the old waiting assistant answer', () => {
    const rows = [...started(), settled(5), event(6, 'turn/start', { turn: 2 }), end(7)]
    const value = project(rows)
    expect(value.status.state).toBe('failed')
    expect(value.status.error_code).toBe('host_terminal_result_unavailable')
    expect(value.result).toBeNull()
  })

  it('retains unconfirmed abort pending and preserves the model watchdog failure', () => {
    expect(project([...started(), end(5, 'aborted')]).status.error_code).toBe('host_command_activity_pending')
    expect(project([...started(), end(5, 'aborted')]).completion).toBeNull()
    const value = projectInspection({ ...correlation, modelStepTimeoutTriggered: true }, inspection(started()), { commandLifecycle: true })
    expect(value.status.error_code).toBe('dsh_model_step_timeout')
    expect(value.status.state).toBe('failed')
  })

  it('requires declared roles to settle even when a synthesis was already accepted', () => {
    const rows = [...started(), call(5, 'decision_hub_synthesis_submit', 'synth-1', {}), result(6, 'synth-1', 'accepted'), end(7)]
    expect(project(rows).status.state).toBe('running')
    expect(project(rows).completion).toBeNull()
    expect(project([...rows, end(8, 'aborted')]).status.error_code).toBe('host_command_activity_pending')
    expect(project([...rows, end(8, 'aborted')]).completion).toBeNull()
    expect(project([...rows, settled(8), end(9)]).status.state).toBe('completed')
  })

  it.each(['aborted', 'error'])('does not relabel raw %s as success without settled current activity', kind => {
    const rows = [...started(), settled(5), call(6, 'decision_hub_synthesis_submit', 'synth-1', {}), result(7, 'synth-1', 'accepted'),
      event(8, 'turn/end', { reason: { kind, ...(kind === 'error' ? { error: { code: 'EMPTY_RESPONSE' } } : {}) } })]
    const value = project(rows, 8, { queueCount: 1 })
    expect(value.status.state).toBe(kind === 'aborted' ? 'idle' : 'failed')
    if (kind === 'aborted') expect(value.completion).toBeNull()
    expect(project(rows).status.state).toBe('completed')
  })

  it('does not borrow a later request synthesis to settle the earlier command', () => {
    const rows = [...started(), prompt(5, 'req-2'), call(6, 'decision_hub_synthesis_submit', 'synth-2', {}), result(7, 'synth-2', 'accepted'), end(8)]
    expect(hasAcceptedSynthesisAfterPrompt(inspection(rows), 'req-1')).toBe(false)
    expect(hasAcceptedSynthesisAfterPrompt(inspection(rows), 'req-2')).toBe(true)
    expect(project(rows).status.state).toBe('running')
  })

  it('binds one-shot jobs to a real notice or a successful terminal job_output receipt', () => {
    const rows = [prompt(), call(1, 'subagent_reviewer', 'review-1', { run_in_background: true }),
      result(2, 'review-1', 'started background subagent job job-1'), assistant(3, 'waiting'), end(4)]
    expect(project(rows).status.state).toBe('running')
    const notice = event(5, 'user/message', { source: { kind: 'plugin', plugin: 'tool-jobs', form: 'notice' },
      content: [{ type: 'text', text: 'background job job-1 (subagent: reviewer) finished [status: failed].' }] })
    expect(project([...rows, notice, assistant(6, 'bounded conclusion'), end(7)]).status.state).toBe('completed')
    expect(project([...rows, call(5, 'job_output', 'read-job', { job_id: 'job-1' }),
      result(6, 'read-job', 'review output\n[status: completed, complete]'), assistant(7, 'final'), end(8)]).status.state).toBe('completed')
    expect(project([...rows, call(5, 'job_output', 'read-job', { job_id: 'job-1' }),
      result(6, 'read-job', 'still running\n[status: running]'), end(7)]).status.state).toBe('running')
  })

  it('does not carry gen1 delegated children into a distinct gen2 prompt', () => {
    const rows = [...started(), prompt(5, 'req-2'), assistant(6, 'repair final'), end(7)]
    expect(commandDelegations(inspection(rows), 'req-2').childIds).toEqual([])
    expect(projectInspection({ ...correlation, requestId: 'req-2', generation: 2 }, inspection(rows), { commandLifecycle: true }).result?.final_response).toBe('repair final')
    expect(projectInspection(correlation, inspection(rows), { commandLifecycle: true }).status.state).toBe('running')
  })

  it('keeps the historical acknowledged turn projection unchanged without the future command option', () => {
    expect(projectInspection(correlation, inspection([...started(), settled(5), assistant(6, 'later'), end(7, 'error')])).status.state).toBe('completed')
  })

  it('uses the actual one-shot foreground default when reviewer omits the scheduling flag', () => {
    const rows = [prompt(), call(1, 'subagent_reviewer', 'review-foreground', {}),
      result(2, 'review-foreground', 'actual independent review output'), assistant(3, 'final'), end(4)]
    expect(commandDelegations(inspection(rows), 'req-1')).toEqual({ childIds: [], jobIds: [], unresolved: false, lastNoticeSeq: -1 })
    expect(project(rows).status.state).toBe('completed')
  })

  it('uses the continuable background default and rejects invalid scheduling arguments', () => {
    const rows = [prompt(), call(1, 'subagent', 'implicit-background', {}),
      result(2, 'implicit-background', `started subagent ${child}`), assistant(3, 'waiting'), end(4)]
    expect(commandDelegations(inspection(rows), 'req-1').childIds).toEqual([child])
    expect(project(rows).status.state).toBe('running')
    for (const value of [null, 'false', 1]) {
      const bad = [prompt(), call(1, 'subagent_reviewer', 'bad-args', { run_in_background: value }), result(2, 'bad-args', 'output'), end(3)]
      expect(commandDelegations(inspection(bad), 'req-1').unresolved).toBe(true)
    }
  })

  it('does not wait forever for a delegation that was rejected before it started', () => {
    const rows = [prompt(), call(1, 'subagent', 'bad-child', { run_in_background: true }), result(2, 'bad-child', 'rejected', true), assistant(3, 'limited conclusion'), end(4)]
    expect(commandDelegations(inspection(rows), 'req-1').unresolved).toBe(false)
    expect(project(rows).status.state).toBe('completed')
  })

  it('the actual Host prevents first-turn terminal callback, then returns the stable final exactly once', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const parent = app.sessions.inspections.get(payload.deterministic_session_id)!
    parent.events.push(...started().slice(1))
    const list = vi.fn(() => { throw new Error('Host census is not required') })
    Object.assign(app.sessions, { list })
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1'))).json.state).toBe('running')
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))).status).toBe(409)
    expect(app.state.terminalBodies).toEqual([])
    parent.events.push(settled(5), event(6, 'turn/start', { turn: 2 }), assistant(7, 'actual structured result'), end(8))
    app.sessions.inspections.set(child, { meta: { id: child, parentSession: payload.deterministic_session_id } as { id: string }, events: [] })
    Object.assign(app.ctx, { agents: { get: (id: string) => id === payload.deterministic_session_id ? { id, status: 'idle' as const } : undefined } })
    let queue: unknown[] = [ { id: 'notice-unclaimed' } ]
    const returned = vi.fn()
    const control: NonNullable<SessionControllerPublic['control']> = async function* () {
      try { yield { type: 'baseline', value: { queues: { [payload.deterministic_session_id]: queue }, jobs: { [payload.deterministic_session_id]: [] },
        projections: { [payload.deterministic_session_id]: { asOfSeq: 8 } } } } } finally { returned() }
    }
    Object.assign(app.sessions, { control })
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1'))).json.state).toBe('running')
    expect(app.state.terminalBodies).toEqual([])
    queue = []
    const final = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(final.status).toBe(200)
    expect(final.json.final_response).toBe('actual structured result')
    expect(final.json.finished_at).toBe(new Date(now + 8).toISOString())
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(returned).toHaveBeenCalledTimes(2)
    expect(list).not.toHaveBeenCalled()
    expect(app.sessions.prompt).toHaveBeenCalledOnce()
    expect(app.sessions.create).toHaveBeenCalledOnce()
    await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(app.state.terminalBodies).toHaveLength(1)
  })

  it('does not extend the original submit deadline', async () => {
    const app = harness()
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() - 1).toISOString())
    expect((await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))).status).toBe(408)
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('does not automatically cancel the parent after an early synthesis while its declared child is pending', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const parent = app.sessions.inspections.get(payload.deterministic_session_id)!
    const accepted = result(6, 'early-synthesis', 'accepted')
    parent.events.push(...started().slice(1), call(5, 'decision_hub_synthesis_submit', 'early-synthesis', {}), accepted, end(7))
    const before = app.sessions.inspect.mock.calls.length
    await app.emit('session/event', { id: payload.deterministic_session_id }, accepted)
    await vi.waitFor(() => { expect(app.sessions.inspect.mock.calls.length).toBeGreaterThan(before) })
    expect(app.sessions.cancel).not.toHaveBeenCalled()
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1'))).json.state).toBe('running')
    expect(app.state.terminalBodies).toEqual([])
    expect(app.sessions.prompt).toHaveBeenCalledOnce()
  })
})


it('preserves complete large native result proof and keeps result field guards', () => {
  const proof = '原始证据'.repeat(2_100_000)
  const rows = [prompt(), call(1, 'decision_hub_research', 'research-original', {}),
    result(2, 'research-original', proof), assistant(3, '原研究已结束'), end(4)]
  const projected = project(rows)
  const original = projected.result!
  expect(original.events_json.length).toBeGreaterThan(8_388_608)
  const exported = dshSessionResultSchema.parse(JSON.parse(JSON.stringify(original)))
  const notifications = JSON.parse(exported.events_json)
  expect(notifications.map((item: { payload: { event: SessionEventLike } }) => item.payload.event)).toEqual(rows)
  expect(exported.result_hash).toBe(createHash('sha256').update(exported.final_response).update('\0').update(exported.events_json).digest('hex'))
  for (const invalid of [null, 1, [], '', 'x']) {
    expect(dshSessionResultSchema.safeParse({ ...exported, events_json: invalid }).success).toBe(false)
  }
  expect(dshSessionResultSchema.safeParse({ ...exported, run_id: '' }).success).toBe(false)
  expect(dshSessionResultSchema.safeParse({ ...exported, result_hash: 'wrong' }).success).toBe(false)
})
