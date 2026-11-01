import { describe, expect, it, vi } from 'vitest'
import { dshSessionSubmitSchema, type DshSessionSubmit } from '@decision-hub/contracts-ts'
import type { HubClient } from '../src/host/hub-client.js'
import type { Correlation, SessionCorrelations } from '../src/host/session-correlation.js'
import type { CancellationAgent, CancellationServices } from '../src/host/command-cancellation.js'
import type { SessionControllerPublic } from '../src/host/dsh-public.js'
import { PLUGIN_BUILD_HASH } from '../src/generated-build-identity.js'
import { harness, promptPayload, request, submitPayload as rawSubmitPayload } from './lifecycle-harness.js'

function submitPayload(...args: Parameters<typeof rawSubmitPayload>): DshSessionSubmit {
  return dshSessionSubmitSchema.parse(rawSubmitPayload(...args))
}

// Actual product HTTP route + strict HubClient DTO parser; SDK activity is an
// isolated public seam. These controls do not claim real Provider cancellation.
function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>(release => { resolve = release })
  return { promise, resolve }
}

function link(payload: ReturnType<typeof submitPayload>, terminalAt: string | null = null) {
  return {
    schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
    dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
    state: terminalAt === null ? 'running' : 'completed', generation: payload.generation,
    upstream_identity: {
      source_commit: '0a53fb55bea101816fa226bb964ae2bed71c343b', source_version: '0.1.2-alpha.2',
      package_versions: { '@deepseek-ai/dsh': '0.1.2-alpha.2' }, plugin_build_hash: PLUGIN_BUILD_HASH,
    },
    deadline_at: payload.deadline_at, max_tool_calls: 12, tool_calls_started: 0,
    model_step_timeout_ms: payload.model_step_timeout_ms,
    accepted_at: null, last_seen_at: null, terminal_at: terminalAt, last_seq: 0,
    trace_ref: null, result_ref: null, result_hash: null, error_code: null,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  }
}

function privateState(app: ReturnType<typeof harness>) {
  // Test-only access to the actual mutable registry/HTTP client, never a proof
  // substitute. Deferred link responses still use the original strict reader.
  return app.bridge as unknown as { correlations: SessionCorrelations; hub: HubClient; cancellations: { requested(binding: Correlation): boolean } }
}

async function setup() {
  const app = harness()
  const payload = submitPayload()
  expect((await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))).status).toBe(202)
  app.state.link = link(payload)
  const parent: CancellationAgent = {
    id: payload.deterministic_session_id, status: 'idle', session: { header: {} },
    cancel: vi.fn(), whenIdle: vi.fn(async () => undefined),
  }
  const drain = vi.fn(async (_parents: readonly CancellationAgent[]) => undefined)
  const services: CancellationServices = {
    subagents: { drainContinuableDescendants: drain },
    jobs: { list: () => [], kill: () => 'already-finished', wait: async () => { throw new Error('no jobs') } },
  }
  let childRunning = false
  Object.assign(app.ctx, {
    agents: { get: (id: string) => id === parent.id ? parent : undefined },
    get: <K extends keyof CancellationServices>(name: K) => services[name],
  })
  const list: NonNullable<SessionControllerPublic['list']> = async () => ({ items: [
    { sessionId: parent.id, running: false },
    ...(childRunning ? [{ sessionId: 'child-a', parentSessionId: parent.id, running: true }] : []),
    // Unrelated activity is retained, not cancelled or included in this proof.
    { sessionId: 'another-run', running: true },
  ] })
  const control: NonNullable<SessionControllerPublic['control']> = async function* () {
    const lastSeq = app.sessions.inspections.get(parent.id)!.events.reduce((seq, event) => Math.max(seq, event.seq), 0)
    yield { type: 'baseline', value: {
      queues: { [parent.id]: [], 'another-run': ['pending'] },
      jobs: { [parent.id]: [], 'another-run': [{ id: 'foreign-job', status: 'running' }] },
      projections: { [parent.id]: { asOfSeq: lastSeq } },
    } }
  }
  Object.assign(app.sessions, { list, control })
  return { app, payload, parent, drain, setChildRunning: (running: boolean) => { childRunning = running } }
}

describe('owner cancellation Host routing', () => {
  it('rejects old generation with zero SDK stop operations', async () => {
    const f = await setup()
    const registry = privateState(f.app).correlations
    registry.getRun('run-1')!.state = 'completed'
    const next = submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2)
    registry.admit(next)
    f.app.state.link = link(next)
    const value = await f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    expect(value.status).toBe(409)
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(f.app.state.terminalBodies).toEqual([])
  })

  it('rejects a generation change during the strict fresh Hub Link await', async () => {
    const f = await setup()
    const state = privateState(f.app)
    const gate = deferred()
    const reached = deferred()
    const readLink = state.hub.link.bind(state.hub)
    vi.spyOn(state.hub, 'link').mockImplementation(async runId => {
      reached.resolve()
      await gate.promise
      return readLink(runId)
    })
    const cancelling = f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    await reached.promise
    const original = state.correlations.getRun('run-1')!
    original.state = 'completed'
    const next = submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2)
    // Deliberate registry admission seam exercises the frozen binding guard;
    // the public submit path separately rejects this under a cancel latch.
    state.correlations.admit(next)
    f.app.state.link = link(next)
    gate.resolve()
    expect((await cancelling).status).toBe(409)
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(f.app.state.terminalBodies).toEqual([])
  })

  it('holds a completed inspection behind the intent while fresh Link is pending', async () => {
    const f = await setup()
    const gate = deferred()
    const reached = deferred()
    const state = privateState(f.app)
    const readLink = state.hub.link.bind(state.hub)
    vi.spyOn(state.hub, 'link').mockImplementation(async runId => {
      reached.resolve()
      await gate.promise
      return readLink(runId)
    })
    const cancelling = f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    await reached.promise
    f.app.sessions.inspections.get(f.parent.id)!.events.push(
      { seq: 2, type: 'assistant/message', time: Date.now(), data: { message: { content: [{ type: 'text', text: 'late final' }] } } },
      { seq: 3, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    const status = await f.app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(status.json).toMatchObject({ state: 'idle', error_code: 'host_cancel_pending' })
    expect(f.app.state.terminalBodies).toEqual([])
    const next = submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2)
    expect((await f.app.call(request('PUT', '/decision-hub/v1/runs/run-1', next))).status).toBe(409)
    gate.resolve()
    expect((await cancelling).json.state).toBe('cancelled')
    await vi.waitFor(() => expect(f.app.state.terminalBodies).toHaveLength(1))
  })

  it('does not let a late original terminal response mark the next generation terminal', async () => {
    const f = await setup()
    const gate = deferred()
    f.app.state.terminalGate = gate.promise
    f.app.sessions.inspections.get(f.parent.id)!.events.push(
      { seq: 2, type: 'assistant/message', time: Date.now(), data: { message: { content: [{ type: 'text', text: 'first final' }] } } },
      { seq: 3, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    const checking = f.app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    await vi.waitFor(() => expect(f.app.state.terminalBodies).toHaveLength(1))
    const next = submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2)
    f.app.state.prompt = promptPayload('run-1', f.payload.request_hash, 2)
    f.app.state.link = link(next)
    expect((await f.app.call(request('PUT', '/decision-hub/v1/runs/run-1', next))).status).toBe(202)
    gate.resolve()
    await checking
    const current = privateState(f.app).correlations.getRun('run-1')!
    expect(current.generation).toBe(2)
    expect(current.terminalAt).toBeNull()
    expect(f.app.state.terminalBodies).toHaveLength(1)
    expect((await f.app.call(request('GET', '/decision-hub/v1/runs/run-1'))).json.generation).toBe(2)
  })

  it('authenticates queued cancellation without inventing a turn/end or Result', async () => {
    const f = await setup()
    const original = JSON.stringify(f.app.sessions.inspections.get(f.parent.id)!.events)
    const value = await f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    expect(value.status).toBe(202)
    expect(value.json.state).toBe('cancelled')
    await vi.waitFor(() => expect(f.app.state.terminalBodies).toHaveLength(1))
    expect(f.app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'cancelled', result_ref: null, result_hash: null })
    expect(JSON.stringify(f.app.sessions.inspections.get(f.parent.id)!.events)).toBe(original)
    expect(f.drain.mock.calls[0]?.[0]?.[0]).toBe(f.parent)
  })

  it('does not authenticate cancelled when a descendant is still active after drain ack', async () => {
    const f = await setup()
    f.setChildRunning(true)
    const value = await f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    expect(value.status).toBe(202)
    expect(value.json).toMatchObject({ state: 'idle', error_code: 'host_cancel_pending' })
    expect(f.app.state.terminalBodies).toEqual([])
  })

  it('joins the in-flight terminal callback without relabelling its acknowledged result', async () => {
    const f = await setup()
    const gate = deferred()
    f.app.state.terminalGate = gate.promise
    f.app.sessions.inspections.get(f.parent.id)!.events.push(
      { seq: 2, type: 'assistant/message', time: Date.now(), data: { message: { content: [{ type: 'text', text: 'final' }] } } },
      { seq: 3, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    const checking = f.app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    await vi.waitFor(() => expect(f.app.state.terminalBodies).toHaveLength(1))
    let cancelSettled = false
    const cancelling = f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
      .finally(() => { cancelSettled = true })
    try {
      const state = privateState(f.app)
      await vi.waitFor(() => expect(state.cancellations.requested(state.correlations.getRun('run-1')!)).toBe(true))
      expect(cancelSettled).toBe(false)
      expect(f.drain).not.toHaveBeenCalled()
      expect(f.parent.cancel).not.toHaveBeenCalled()
      expect(f.app.state.terminalBodies).toHaveLength(1)
    } finally { gate.resolve(); await checking }
    expect(await cancelling).toMatchObject({ status: 202, json: { state: 'completed' } })
    expect(f.app.state.terminalBodies).toHaveLength(1)
    expect(f.app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'completed', generation: 1 })
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
  })

  it('does not rewrite an acknowledged terminal and does not block its legal next generation', async () => {
    const f = await setup()
    const acknowledged = new Date().toISOString()
    f.app.state.link = link(f.payload, acknowledged)
    f.app.sessions.inspections.get(f.parent.id)!.events.push(
      { seq: 2, type: 'assistant/message', time: Date.now(), data: { message: { content: [{ type: 'text', text: 'already final' }] } } },
      { seq: 3, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    expect((await f.app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))).status).toBe(202)
    expect(privateState(f.app).correlations.getRun('run-1')).toMatchObject({
      state: 'completed', terminalAt: acknowledged, lastSeq: 0, errorCode: null,
    })
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(f.app.state.terminalBodies).toEqual([])
    const next = submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2)
    f.app.state.prompt = promptPayload('run-1', f.payload.request_hash, 2)
    f.app.state.link = link(next)
    expect((await f.app.call(request('PUT', '/decision-hub/v1/runs/run-1', next))).status).toBe(202)
  })

  it('rejects a mismatched acknowledged Link without poisoning the local terminal tuple', async () => {
    const f = await setup()
    const registry = privateState(f.app).correlations
    const original = registry.getRun('run-1')!
    const acknowledged = new Date().toISOString()
    Object.assign(original, { terminalAt: acknowledged, state: 'running', lastSeq: 7, errorCode: 'dsh_turn_error' })
    const before = structuredClone(original)
    f.app.state.link = { ...link(f.payload, acknowledged), request_hash: 'b'.repeat(64),
      state: 'failed', last_seq: 23, error_code: 'host_deadline_elapsed' }
    const response = await f.app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(response.status).toBeGreaterThanOrEqual(400)
    expect(registry.getRun('run-1')).toBe(original)
    expect(original).toEqual(before)
    expect(f.app.state.terminalBodies).toEqual([])
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
  })

  it('does not restore an old cancelled business intent onto a changed generation', async () => {
    const f = await setup()
    const app = harness()
    app.state.link = link(f.payload)
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'cancelled', gate_status: null,
      coverage_status: null, hard_coverage_ratio: null, stop_reason_code: null, stop_reason_detail: null, failures: [],
    }
    Object.assign(app.ctx, { agents: f.app.ctx.agents, get: f.app.ctx.get })
    const reached = deferred()
    const release = deferred()
    const state = privateState(app)
    const readBusiness = state.hub.businessStatus.bind(state.hub)
    vi.spyOn(state.hub, 'businessStatus').mockImplementation(async runId => {
      reached.resolve()
      await release.promise
      return readBusiness(runId)
    })
    const reading = app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    await reached.promise
    state.correlations.getRun('run-1')!.state = 'completed'
    state.correlations.admit(submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2))
    release.resolve()
    expect((await reading).status).toBe(409)
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })

  it('does not overwrite a concurrent admission with an older cold Link response', async () => {
    const f = await setup()
    const app = harness()
    app.state.link = link(f.payload)
    Object.assign(app.ctx, { agents: f.app.ctx.agents, get: f.app.ctx.get })
    const reached = deferred()
    const release = deferred()
    const state = privateState(app)
    const readLink = state.hub.link.bind(state.hub)
    vi.spyOn(state.hub, 'link').mockImplementation(async runId => {
      const original = await readLink(runId)
      reached.resolve()
      await release.promise
      return original
    })
    const cancelling = app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    await reached.promise
    state.correlations.admit(submitPayload('run-1', f.payload.request_hash, f.payload.deadline_at, 2))
    release.resolve()
    expect((await cancelling).status).toBe(409)
    expect(state.correlations.getRun('run-1')?.generation).toBe(2)
    expect(f.drain).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })
})
