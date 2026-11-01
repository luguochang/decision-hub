import { createServer, type Server, type RequestListener } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, expect, it, vi } from 'vitest'
import type { DshModelDispatchBinding } from '@decision-hub/contracts-ts'
import { HubClient } from '../src/host/hub-client.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'

const now = Date.parse('2026-10-16T01:00:00Z')
const root = `dsh_${'a'.repeat(64)}`
const binding: DshModelDispatchBinding = { schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'run_test',
  dsh_session_id: root, request_hash: 'b'.repeat(64), generation: 1, plugin_build_hash: 'c'.repeat(64),
  purpose: 'conversation', execution_session_id: null, parent_session_id: null,
  provider: 'synthetic', model: 'synthetic', reasoning_effort: null, temperature: null, max_tokens: null, stop_sequences_hash: null }
const servers: Server[] = []
afterEach(async () => { vi.useRealTimers(); await Promise.all(servers.splice(0).map(s => new Promise<void>(resolve => {
  s.closeAllConnections(); s.close(() => resolve()) }))) })
async function serve(handler: RequestListener) {
  const server = createServer(handler); servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}
async function collect(stream: AsyncIterable<unknown>) { const rows = []; for await (const row of stream) rows.push(row); return rows }

it('one real binding PUT survives 35s with the original step/Task ceiling and unchanged exact payload', async () => {
  let seen!: () => void, release!: () => void, calls = 0
  const arrived = new Promise<void>(resolve => { seen = resolve })
  const step = new Date(now + 600_000).toISOString(), task = new Date(now + 1_000_000).toISOString()
  const base = await serve((request, response) => {
    calls++; let body = ''
    request.on('data', chunk => { body += chunk })
    request.once('end', () => {
      expect(request.method).toBe('PUT'); expect(JSON.parse(body)).toEqual(binding)
      expect(request.headers['x-decision-hub-model-binding-deadline-at']).toBe(step)
      expect(request.headers['x-decision-hub-model-binding-budget-ms']).toBe('600000')
      release = () => response.end(JSON.stringify(binding)); seen()
    })
  })
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
  const client = new HubClient({ baseUrl: base, callbackKey: 'private-test', ownerId: 'private', timeoutMs: 10,
    attempts: 3, managedReadDeadline: () => task })
  const pending = client.bindModel(binding, undefined, undefined, step)
  await arrived; await vi.advanceTimersByTimeAsync(35_000); release()
  expect(await pending).toBeUndefined(); expect(calls).toBe(1)
})

it('selected detail and synthesis proof GETs survive 70s without lifting legacy managed reads', async () => {
  for (const phase of ['detail', 'preflight', 'emission', 'prompt', 'business'] as const) {
    vi.useRealTimers()
    let seen!: () => void, release!: () => void, calls = 0
    const arrived = new Promise<void>(resolve => { seen = resolve })
    const task = new Date(now + 600_000).toISOString()
    const base = await serve((request, response) => {
      calls++; let body = ''; request.on('data', chunk => { body += chunk })
      request.once('end', () => {
        expect(request.method).toBe('GET'); expect(body).toBe('')
        expect(request.headers['x-decision-hub-research-proof-deadline-at']).toBe(task)
        expect(request.headers['x-decision-hub-research-proof-budget-ms']).toBe('600000')
        release = () => { response.statusCode = 409; response.end(JSON.stringify({ detail: 'action_preflight_original_verification_failed' })) }
        seen()
      })
    })
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
    const client = new HubClient({ baseUrl: base, callbackKey: 'private-test', ownerId: 'private', timeoutMs: 10,
      attempts: 3, managedReadDeadline: () => task })
    const pending = phase === 'prompt' ? client.prompt('run_test', 1, undefined, task)
      : phase === 'business' ? client.businessStatus('run_test', undefined, task)
      : phase === 'detail' ? client.researchDetail('run_test', undefined, task)
      : phase === 'preflight' ? client.actionSynthesisPreflight('run_test', 1, root, 'call_test', undefined, task)
      : client.providerToolEmission('run_test', 1, root, 'call_test', undefined, task)
    const rejection = expect(pending).rejects.toMatchObject({ code: 'action_preflight_original_verification_failed' })
    await arrived; await vi.advanceTimersByTimeAsync(70_000); release(); await rejection
    expect(calls).toBe(1)
  }
})

it('binding captures the real step deadline before binding and does not ask for a renewed step after it', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
  const order: string[] = [], original = new Date(now + 600_000).toISOString()
  const hub = { linkBySession: vi.fn(async () => ({ ...binding, deadline_at: new Date(now + 1_000_000).toISOString() })),
    bindModel: vi.fn(async (_payload: unknown, _signal: unknown, _witness: unknown, deadline: string) => {
      order.push('bind'); expect(deadline).toBe(original); await vi.advanceTimersByTimeAsync(35_000); return binding
    }) }
  const next = vi.fn(async function* () { order.push('provider-next'); yield { type: 'finish', reason: { kind: 'stop' } } })
  const deadline = vi.fn(() => { order.push('actual-step-deadline'); return original })
  await collect(guardedModelStream({ sessionId: root, provider: 'synthetic', model: 'synthetic', messages: [], tools: [] },
    next, hub as never, binding.plugin_build_hash, undefined, undefined, undefined, undefined, undefined, deadline))
  expect(order).toEqual(['actual-step-deadline', 'bind', 'provider-next'])
  expect(deadline).toHaveBeenCalledOnce(); expect(next).toHaveBeenCalledOnce()
})

it('deadline and parent cancel close the single real request before return, with expired-original precedence', async () => {
  for (const mode of ['deadline', 'cancel'] as const) {
    vi.useRealTimers()
    let seen!: () => void, closed!: () => void, calls = 0
    const arrived = new Promise<void>(resolve => { seen = resolve })
    const socketClosed = new Promise<void>(resolve => { closed = resolve })
    const base = await serve((request, _response) => {
      calls++; request.socket.once('close', closed); request.resume(); request.once('end', seen)
    })
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
    const controller = new AbortController(), step = new Date(now + 100_000).toISOString()
    const client = new HubClient({ baseUrl: base, callbackKey: 'private-test', attempts: 3, timeoutMs: 10, ownerId: 'private' })
    const pending = client.bindModel(binding, controller.signal, undefined, step)
    const rejection = expect(pending).rejects.toMatchObject({ code: mode === 'deadline'
      ? 'dsh_model_binding_budget_elapsed' : 'dsh_model_binding_cancelled' })
    await arrived
    if (mode === 'deadline') { await vi.advanceTimersByTimeAsync(100_000); controller.abort() }
    else controller.abort()
    await rejection; await socketClosed; expect(calls).toBe(1)
  }
})

it('native binding preserves known-unacquired busy eligibility without replaying an unknown writer', async () => {
  vi.useFakeTimers(); vi.setSystemTime(now)
  for (const detail of ['dsh_model_binding_writer_busy', 'dsh_model_binding_commit_unconfirmed']) {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ detail }, { status: 409 }))
    const client = new HubClient({ baseUrl: 'http://isolated.invalid', callbackKey: 'private-test', ownerId: 'private',
      attempts: 3, timeoutMs: 10, fetchImpl })
    const error = await client.bindModel(binding, undefined, undefined, new Date(now + 600_000).toISOString()).catch(error => error)
    expect(error.retryable).toBe(detail === 'dsh_model_binding_writer_busy')
    expect(fetchImpl).toHaveBeenCalledOnce()
  }
})
