import { describe, expect, it, vi } from 'vitest'
import type { DshRunSessionLinkView, DshSessionCompletion, DshSessionResult } from '@decision-hub/contracts-ts'
import type { HubClient } from '../src/host/hub-client.js'
import type { Correlation, SessionCorrelations } from '../src/host/session-correlation.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'
import { acknowledgedResultCursor, matchesAcknowledgedResult, projectAcknowledgedResult,
  projectAcknowledgedStatus, projectInspection } from '../src/host/session-result.js'
import { PLUGIN_BUILD_HASH } from '../src/generated-build-identity.js'
import { harness, request } from './lifecycle-harness.js'

// Artificial protocol fixtures reproduce the observed cursor/state differences.
// All Hub/SDK seams are in-memory; no network, model, production DB or live SDK.
const epoch = Date.parse('2026-10-08T19:40:00Z')
const event = (seq: number, type: string, data: Record<string, unknown>): SessionEventLike =>
  ({ seq, type, time: epoch + seq, data })

function fixture(code = 'dsh_provider_input_transport_timeout', cursor = 1033, drained = 1038) {
  const run = 'run-fixture', hash = 'a'.repeat(64), ids = deterministicIds(run, hash)
  const correlation: Correlation = {
    runId: run, requestHash: hash, sessionId: ids.sessionId, requestId: ids.requestId, generation: 1,
    modelStepTimeoutMs: 150000, modelStepTimeoutSource: 'submission', modelStepTimeoutTriggered: false,
    state: 'running', lastSeq: 0, errorCode: null, acceptedAt: null, terminalAt: null,
  }
  const events = [
    event(1, 'user/message', { source: { kind: 'user', rpcId: ids.requestId } }),
    event(2, 'turn/start', { turn: 1 }),
    event(3, 'tool/call', { name: 'research_capability_execute', callId: 'research-1' }),
    event(4, 'tool/result', { message: { content: [{ type: 'tool-result', toolCallId: 'research-1',
      content: [{ type: 'text', text: 'fixture trusted result' }] }] } }),
    event(5, 'assistant/message', { message: { content: [{ type: 'text', text: 'partial fixture findings' }] } }),
    event(cursor - 4, 'turn/end', { turn: 1, reason: { kind: 'error', error: { code: code.toUpperCase() } } }),
    event(cursor, 'agent/inbox/spliced', { target: 'next-turn', inserted: [] }),
  ]
  const inspection = { meta: { id: ids.sessionId }, events }
  const projected = projectInspection(correlation, inspection, { commandLifecycle: true,
    activity: { parentSeq: cursor, parentRunning: false, queueCount: 0, activeJobCount: 0 } })
  expect(projected.status).toMatchObject({ state: 'failed', error_code: code, last_seq: cursor })
  expect(projected.result).not.toBeNull()
  const result = projected.result!, completion = projected.completion!
  const link: DshRunSessionLinkView = {
    schema_version: 'dsh-run-session-link.v1', run_id: run, dsh_session_id: ids.sessionId,
    request_hash: hash, state: 'failed', generation: 1,
    upstream_identity: { source_commit: '0a53fb55bea101816fa226bb964ae2bed71c343b',
      source_version: '0.1.2-alpha.2', package_versions: { '@deepseek-ai/dsh': '0.1.2-alpha.2' },
      plugin_build_hash: PLUGIN_BUILD_HASH },
    deadline_at: new Date(Date.now() + 60000).toISOString(), max_tool_calls: 12, tool_calls_started: 1,
    model_step_timeout_ms: 150000, accepted_at: null, last_seen_at: null,
    terminal_at: completion.completed_at, last_seq: drained, trace_ref: result.trace_ref,
    result_ref: completion.result_ref, result_hash: result.result_hash, error_code: code,
    created_at: new Date(epoch).toISOString(), updated_at: new Date(epoch + drained).toISOString(),
  }
  correlation.terminalAt = link.terminal_at
  correlation.state = link.state
  correlation.lastSeq = link.last_seq
  correlation.errorCode = link.error_code
  return { correlation, inspection, result, completion, link }
}

function privateState(app: ReturnType<typeof harness>) {
  return app.bridge as unknown as {
    correlations: SessionCorrelations; hub: HubClient; results: Map<string, DshSessionResult>;
    terminalDrains: { drain: (...args: unknown[]) => Promise<{ last_seq: number }> };
    maybeTerminal: (correlation: Correlation, completion: DshSessionCompletion, result: DshSessionResult) => Promise<void>;
    onStatus: (sessionId: string, running: boolean) => Promise<void>;
  }
}

describe('acknowledged terminal original snapshot', () => {
  it('returns the drained ETH failure status and recovers original partial bytes on restart', () => {
    const f = fixture()
    const before = JSON.stringify(f.result)
    f.inspection.events.push(event(1038, 'turn/end', { reason: { kind: 'aborted' } }))
    expect(projectAcknowledgedStatus(f.correlation, f.link)).toMatchObject({
      state: 'failed', last_seq: 1038, error_code: 'dsh_provider_input_transport_timeout',
    })
    expect(acknowledgedResultCursor(f.link)).toBe(1033)
    expect(projectAcknowledgedResult(f.correlation, f.inspection, f.link)).toEqual(f.result)
    expect(matchesAcknowledgedResult(f.result, f.link)).toBe(true)
    expect(JSON.stringify(f.result)).toBe(before)
    expect(f.result.finish_reason).toBe('error')
  })

  it('retains BTC failed provider cause despite later native aborted/cancelled projection', () => {
    const f = fixture('dsh_model_binding_unavailable', 1171, 1182)
    f.inspection.events.push(event(1182, 'turn/end', { reason: { kind: 'aborted' } }))
    // The stale native projection is not terminal authority, regardless of
    // which historical turn/end an old projection happens to select.
    expect(projectAcknowledgedStatus(f.correlation, f.link)).toMatchObject({
      state: 'failed', last_seq: 1182, error_code: 'dsh_model_binding_unavailable',
    })
    expect(projectAcknowledgedResult(f.correlation, f.inspection, f.link)).toEqual(f.result)
  })

  it('reads fresh authenticated Hub link for status without native inspection or a second callback', async () => {
    const f = fixture('dsh_model_binding_unavailable', 1171, 1182), app = harness()
    app.state.link = f.link
    const state = privateState(app)
    state.correlations.recover(f.link)
    app.sessions.inspections.set(f.link.dsh_session_id, f.inspection)
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${f.link.run_id}`))
    expect(reply.status).toBe(200)
    expect(reply.json).toMatchObject({ state: 'failed', last_seq: 1182, error_code: f.link.error_code })
    expect(app.sessions.inspect).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
    await state.onStatus(f.link.dsh_session_id, false)
    expect(state.correlations.getRun(f.link.run_id)?.state).toBe('failed')
  })

  it('returns final drained status after callback and preserves original cached result', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    const active = { ...f.link, state: 'running' as const, terminal_at: null, last_seq: 1033,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null }
    app.state.link = active
    const correlation = state.correlations.recover(active)
    app.sessions.inspections.set(f.link.dsh_session_id, f.inspection)
    vi.spyOn(state.terminalDrains, 'drain').mockResolvedValue({ last_seq: 1038 })
    vi.spyOn(state.hub, 'terminal').mockImplementation(async completion => {
      app.state.terminalBodies.push(completion)
      app.state.link = { ...f.link, last_seq: completion.last_seq }
    })
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${f.link.run_id}`))
    expect(reply.json).toMatchObject({ state: 'failed', last_seq: 1038, error_code: f.link.error_code })
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'failed', last_seq: 1038,
      result_hash: f.result.result_hash, trace_ref: f.result.trace_ref, completed_at: f.result.finished_at })
    expect(correlation).toMatchObject({ state: 'failed', lastSeq: 1038, errorCode: f.link.error_code })
    expect(state.results.get(f.link.run_id)).toEqual(f.result)
  })

  it('serves identical cache and restart results through actual isolated Host route', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    app.state.link = f.link
    state.correlations.recover(f.link)
    f.inspection.events.push(event(1038, 'turn/end', { reason: { kind: 'aborted' } }))
    app.sessions.inspections.set(f.link.dsh_session_id, f.inspection)
    const path = `/decision-hub/v1/runs/${f.link.run_id}/result`
    const restarted = await app.call(request('GET', path))
    expect(restarted.status).toBe(200)
    expect(restarted.json).toEqual(f.result)
    app.sessions.inspect.mockClear()
    const cached = await app.call(request('GET', path))
    expect(cached.json).toEqual(restarted.json)
    expect(app.sessions.inspect).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })

  it('cannot invent a result for an acknowledged status without result pointers', () => {
    const f = fixture(), link = { ...f.link, trace_ref: null, result_ref: null, result_hash: null }
    expect(projectAcknowledgedStatus(f.correlation, link).state).toBe('failed')
    expect(() => projectAcknowledgedResult(f.correlation, f.inspection, link)).toThrow()
  })

  it('returns unavailable for an identity-matched failed terminal with strict null result pointers', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    state.correlations.recover(f.link)
    app.state.link = { ...f.link, result_ref: null, result_hash: null }
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${f.link.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_unavailable' } } })
    expect(app.sessions.inspect).not.toHaveBeenCalled()
  })

  it('returns unavailable for an identity-matched cancelled terminal with strict null result pointers', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    const cancelled = { ...f.link, state: 'cancelled' as const, error_code: 'dsh_provider_cancelled',
      result_ref: null, result_hash: null }
    state.correlations.recover(cancelled)
    app.state.link = cancelled
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${cancelled.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_unavailable' } } })
    expect(app.sessions.inspect).not.toHaveBeenCalled()
  })

  it('keeps a completed terminal with strict null result pointers classified as binding mismatch', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    const completed = { ...f.link, state: 'completed' as const, error_code: null,
      result_ref: null, result_hash: null }
    state.correlations.recover(completed)
    app.state.link = completed
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${completed.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_binding_mismatch' } } })
    expect(app.sessions.inspect).not.toHaveBeenCalled()
  })

  it('reads a fresh Link before returning active result unavailability', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    const active = { ...f.link, state: 'running' as const, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null }
    state.correlations.recover(active)
    app.state.link = active
    app.sessions.inspections.set(active.dsh_session_id, { meta: { id: active.dsh_session_id }, events: [] })
    const fresh = vi.spyOn(state.hub, 'link')
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${active.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_unavailable' } } })
    expect(fresh).toHaveBeenCalledWith(active.run_id)
  })

  it('checks fresh active Link identity before the unavailable early return', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    const active = { ...f.link, state: 'running' as const, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null }
    state.correlations.recover(active)
    app.state.link = { ...active, request_hash: 'b'.repeat(64) }
    app.sessions.inspections.set(active.dsh_session_id, { meta: { id: active.dsh_session_id }, events: [] })
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${active.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_binding_mismatch' } } })
  })

  it.each(['ref_without_hash', 'hash_without_ref', 'wrong_ref', 'wrong_trace', 'terminal_cause'] as const)
  ('keeps authenticated terminal result metadata drift classified as binding mismatch: %s', async field => {
    const f = fixture(), app = harness(), state = privateState(app)
    state.correlations.recover(f.link)
    const link = structuredClone(f.link)
    if (field === 'ref_without_hash') link.result_hash = null
    if (field === 'hash_without_ref') link.result_ref = null
    if (field === 'wrong_ref') link.result_ref = 'dsh-host://runs/foreign/result'
    if (field === 'wrong_trace') link.trace_ref = link.trace_ref! + '&changed=1'
    if (field === 'terminal_cause') link.error_code = 'dsh_model_binding_unavailable'
    app.state.link = link
    const reply = await app.call(request('GET', `/decision-hub/v1/runs/${f.link.run_id}/result`))
    expect(reply).toMatchObject({ status: 409, json: { error: { code: 'host_terminal_result_binding_mismatch' } } })
    expect(app.sessions.inspect).not.toHaveBeenCalled()
  })

  it('retains owner cancellation and native partial finish time without relabelling it completed', () => {
    const f = fixture(), terminal = new Date(epoch + 1038).toISOString()
    const link = { ...f.link, state: 'cancelled' as const, terminal_at: terminal, error_code: null }
    const correlation = { ...f.correlation, terminalAt: terminal, state: 'cancelled' as const, errorCode: null }
    expect(projectAcknowledgedStatus(correlation, link).state).toBe('cancelled')
    expect(projectAcknowledgedResult(correlation, f.inspection, link)).toEqual(f.result)
    expect(matchesAcknowledgedResult({ ...f.result, finished_at: new Date(epoch + 2).toISOString() }, link)).toBe(false)
    expect(matchesAcknowledgedResult({ ...f.result, started_at: new Date(epoch).toISOString() }, link)).toBe(false)
    expect(matchesAcknowledgedResult({ ...f.result, finish_reason: 'completed' }, link)).toBe(false)
    expect(matchesAcknowledgedResult(f.result, { ...f.link, state: 'completed', error_code: null })).toBe(false)
  })

  it.each(['hash', 'bytes', 'generation', 'session', 'trace', 'time'])('rejects cache %s tampering', field => {
    const f = fixture(), result = structuredClone(f.result)
    if (field === 'hash') result.result_hash = 'b'.repeat(64)
    if (field === 'bytes') result.events_json += ' '
    if (field === 'generation') result.generation += 1
    if (field === 'session') result.dsh_session_id += '-other'
    if (field === 'trace') result.trace_ref += '&extra=1'
    if (field === 'time') result.finished_at = new Date(epoch + 1038).toISOString()
    expect(matchesAcknowledgedResult(result, f.link)).toBe(false)
  })

  it.each(['hash', 'generation', 'request', 'session', 'trace', 'leading_zero', 'too_large', 'time', 'cause'])
  ('rejects restart %s tampering', field => {
    const f = fixture(), link = structuredClone(f.link)
    if (field === 'hash') link.result_hash = 'b'.repeat(64)
    if (field === 'generation') link.generation += 1
    if (field === 'request') link.request_hash = 'b'.repeat(64)
    if (field === 'session') link.dsh_session_id += '-other'
    if (field === 'trace') link.trace_ref += '&extra=1'
    if (field === 'leading_zero') link.trace_ref = link.trace_ref!.replace('=1033', '=01033')
    if (field === 'too_large') link.trace_ref = link.trace_ref!.replace('=1033', '=1039')
    if (field === 'time') link.terminal_at = new Date(epoch + 1038).toISOString()
    if (field === 'cause') link.error_code = 'dsh_model_binding_unavailable'
    expect(() => projectAcknowledgedResult(f.correlation, f.inspection, link)).toThrow()
  })

  it('rejects registry generation replacement during acknowledged link read', async () => {
    const f = fixture(), app = harness(), state = privateState(app)
    app.state.link = f.link
    state.correlations.recover(f.link)
    vi.spyOn(state.hub, 'link').mockImplementation(async () => {
      state.correlations.recover({ ...f.link, generation: 2 })
      return f.link
    })
    expect((await app.call(request('GET', `/decision-hub/v1/runs/${f.link.run_id}`))).status).toBe(409)
    expect(app.sessions.inspect).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })
})
