import { createServer, type Server, type RequestListener } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, expect, it, vi } from 'vitest'
import type { DshModelDispatchBinding, ResearchProviderInputSubmit } from '@decision-hub/contracts-ts'
import { HubClient } from '../src/host/hub-client.js'
import { prepareProviderInput, recordedProviderStream } from '../src/host/provider-input.js'
import type { ModelDispatchOptions } from '../src/host/dsh-public.js'

const now = Date.parse('2026-10-16T01:00:00Z')
const root = `dsh_${'a'.repeat(64)}`
const binding: DshModelDispatchBinding = { schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'run_test',
  dsh_session_id: root, request_hash: 'b'.repeat(64), generation: 1, plugin_build_hash: 'c'.repeat(64),
  purpose: 'conversation', execution_session_id: null, parent_session_id: null,
  provider: 'synthetic', model: 'synthetic', reasoning_effort: null, temperature: null, max_tokens: null, stop_sequences_hash: null }
const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
  started_at: '2026-10-16T00:00:00Z', hub_base_url: 'http://hub.test', workspace_id: 'test', workspace_cwd_hash: 'd'.repeat(64) }
const options: ModelDispatchOptions = { sessionId: root, provider: 'synthetic', model: 'synthetic', messages: [], tools: [] }
const ref = { output_id: 'out_input', role: 'input', schema_version: 'input.v1', payload_hash: 'f'.repeat(64), recorded_at: scope.started_at }
const inputAck = (p: ResearchProviderInputSubmit) => ({ schema_version: 'research-provider-input-recorded.v1',
  reference: ref, dispatch_id: p.dispatch_id, input_hash: p.input_hash, known_at: scope.started_at,
  producer_authenticated: true, proof_scope: 'dsh_llm_generate_options' })
const servers: Server[] = []
afterEach(async () => { vi.useRealTimers(); await Promise.all(servers.splice(0).map(s => new Promise<void>(resolve => { s.closeAllConnections(); s.close(() => resolve()) }))) })
async function serve(handler: RequestListener) {
  const server = createServer(handler); servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}
async function collect(stream: AsyncIterable<unknown>) { const rows = []; for await (const row of stream) rows.push(row); return rows }

it('one real PUT per phase survives 70s auth under the same original step; balances decline and provider runs once', async () => {
  const requests: { budget: string; deadline: string; release: () => void }[] = []
  const signals = new Map<number, () => void>()
  const arrived = (index: number) => requests[index] ? Promise.resolve() : new Promise<void>(resolve => signals.set(index, resolve))
  const base = await serve((request, response) => {
    expect(request.method).toBe('PUT')
    let body = ''; request.on('data', chunk => { body += chunk })
    request.once('end', () => {
      const p = JSON.parse(body), path = request.url!
      const ack = path.endsWith('/finish') ? { schema_version: 'research-provider-finish-recorded.v1',
        reference: ref, input_ref: p.input_ref, dispatch_id: p.dispatch_id, finish_hash: p.finish_hash,
        status: p.status, known_at: scope.started_at, proof_scope: 'dsh_llm_generate_options' }
        : path.includes('/tool-emissions/') ? { schema_version: 'research-provider-tool-emission-recorded.v1',
          reference: ref, input_ref: p.input_ref, run_id: p.run_id, generation: p.generation,
          root_session_id: p.root_session_id, execution_session_id: p.execution_session_id,
          dispatch_id: p.dispatch_id, call_id: p.call_id, tool_name: p.tool_name,
          raw_arguments_hash: p.raw_arguments_hash, arguments_hash: p.arguments_hash,
          candidate_hash: null, proof_scope: 'dsh_llm_generate_options' } : inputAck(p)
      requests.push({ budget: String(request.headers['x-decision-hub-provider-input-budget-ms']),
        deadline: String(request.headers['x-decision-hub-provider-input-deadline-at']),
        release: () => { response.setHeader('content-type', 'application/json'); response.end(JSON.stringify(ack)) } })
      signals.get(requests.length - 1)?.()
    })
  })
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
  const taskDeadline = new Date(now + 1_000_000).toISOString(), stepDeadline = new Date(now + 600_000).toISOString()
  const client = new HubClient({ baseUrl: base, callbackKey: 'test-only', ownerId: 'test', attempts: 3, timeoutMs: 10,
    managedReadDeadline: () => taskDeadline })
  const first = prepareProviderInput(client, binding, options, scope, undefined, 60_000, taskDeadline, stepDeadline)
  await arrived(0); await vi.advanceTimersByTimeAsync(70_000); requests[0]!.release()
  const prepared = await first
  const next = vi.fn(async function* () {
    yield { type: 'block-end', index: 0, block: { type: 'tool-call', id: 'call_1', name: 'todo_write', arguments: '{}' } }
    yield { type: 'finish', reason: { kind: 'tool-calls' } }
  })
  const pending = collect(recordedProviderStream(options, next, client, prepared))
  await arrived(1); await vi.advanceTimersByTimeAsync(70_000); requests[1]!.release()
  await arrived(2); await vi.advanceTimersByTimeAsync(70_000); requests[2]!.release()
  expect(await pending).toHaveLength(2)
  expect(requests.map(x => x.deadline)).toEqual([stepDeadline, stepDeadline, stepDeadline])
  expect(requests.map(x => x.budget)).toEqual(['600000', '530000', '460000'])
  expect(next).toHaveBeenCalledOnce()
})

it('an expired original step starts no receipt or provider and never renews a window', async () => {
  vi.useFakeTimers(); vi.setSystemTime(now)
  const input = vi.fn(async () => { throw new Error('must not execute') })
  await expect(prepareProviderInput({ recordProviderInput: input } as never, binding, options, scope,
    undefined, 60_000, new Date(now + 600_000).toISOString(), new Date(now - 1).toISOString()))
    .rejects.toMatchObject({ code: 'dsh_provider_input_deadline_elapsed' })
  expect(input).not.toHaveBeenCalled()
})

it('deadline or parent cancel closes the single real pending PUT before returning; provider is never delegated', async () => {
  for (const mode of ['deadline', 'cancel'] as const) {
    vi.useRealTimers()
    let seen!: () => void, closed!: () => void, calls = 0
    const arrived = new Promise<void>(resolve => { seen = resolve })
    const socketClosed = new Promise<void>(resolve => { closed = resolve })
    const base = await serve((request, _response) => {
      calls++; request.socket.once('close', closed); request.resume(); request.once('end', seen)
    })
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] }); vi.setSystemTime(now)
    const controller = new AbortController(), next = vi.fn(async function* () { yield 'must-not-run' })
    const client = new HubClient({ baseUrl: base, callbackKey: 'test-only', attempts: 3, timeoutMs: 10, ownerId: 'test' })
    const pending = (async () => {
      const prepared = await prepareProviderInput(client, binding, { ...options, signal: controller.signal }, scope,
        controller.signal, 60_000, new Date(now + 600_000).toISOString(), new Date(now + 100_000).toISOString())
      return collect(recordedProviderStream(options, next, client, prepared))
    })()
    const rejected = expect(pending).rejects.toMatchObject({ code: mode === 'deadline'
      ? 'dsh_provider_input_transport_timeout' : 'dsh_provider_input_transport_cancelled' })
    await arrived
    if (mode === 'deadline') await vi.advanceTimersByTimeAsync(100_000)
    else controller.abort()
    await rejected; await socketClosed
    expect(calls).toBe(1); expect(next).not.toHaveBeenCalled()
  }
})
