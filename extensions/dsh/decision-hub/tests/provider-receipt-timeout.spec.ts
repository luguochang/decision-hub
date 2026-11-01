// Controlled transport/SDK seam only. No Kernel, provider or financial qualification.
import { afterEach, expect, it, vi } from 'vitest'
import {
  type DshModelDispatchBinding, type ResearchProviderInputSubmit,
  type ResearchProviderToolEmissionSubmit, type ResearchProviderFinishSubmit,
} from '@decision-hub/contracts-ts'
import { HubClient, HubClientError } from '../src/host/hub-client.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { prepareProviderInput, recordedProviderStream } from '../src/host/provider-input.js'
import type { ModelDispatchOptions } from '../src/host/dsh-public.js'

const now = Date.parse('2026-10-08T03:00:00Z')
const rootId = `dsh_${'a'.repeat(64)}`
const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
  started_at: '2026-10-08T02:00:00Z', hub_base_url: 'http://hub.test', workspace_id: 'test', workspace_cwd_hash: 'c'.repeat(64) }
const options: ModelDispatchOptions = { sessionId: rootId, provider: 'synthetic', model: 'synthetic',
  messages: [] as ModelDispatchOptions['messages'], system: 'controlled input', tools: [] }
const binding: DshModelDispatchBinding = {
  schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'run_test', dsh_session_id: rootId,
  request_hash: 'b'.repeat(64), generation: 1, plugin_build_hash: 'd'.repeat(64), purpose: 'conversation',
  execution_session_id: null, parent_session_id: null, provider: 'synthetic', model: 'synthetic',
  reasoning_effort: null, temperature: null, max_tokens: null, stop_sequences_hash: null,
}
const ref = (role: string) => ({ output_id: `out_${role}`, role, schema_version: `${role}.v1`, payload_hash: 'f'.repeat(64), recorded_at: '2026-10-08T03:00:00Z' })
const inputAck = (p: ResearchProviderInputSubmit) => ({ schema_version: 'research-provider-input-recorded.v1' as const,
  reference: ref('input'), dispatch_id: p.dispatch_id, input_hash: p.input_hash, known_at: scope.started_at,
  producer_authenticated: true as const, proof_scope: 'dsh_llm_generate_options' as const })
const emissionAck = (p: ResearchProviderToolEmissionSubmit) => ({ schema_version: 'research-provider-tool-emission-recorded.v1' as const,
  reference: ref('emission'), input_ref: p.input_ref, run_id: p.run_id, generation: p.generation,
  root_session_id: p.root_session_id, execution_session_id: p.execution_session_id, dispatch_id: p.dispatch_id,
  call_id: p.call_id, tool_name: p.tool_name, raw_arguments_hash: p.raw_arguments_hash, arguments_hash: p.arguments_hash,
  candidate_hash: null, proof_scope: 'dsh_llm_generate_options' as const })
const finishAck = (p: ResearchProviderFinishSubmit) => ({ schema_version: 'research-provider-finish-recorded.v1' as const,
  reference: ref('finish'), input_ref: p.input_ref, dispatch_id: p.dispatch_id, finish_hash: p.finish_hash,
  status: p.status, known_at: scope.started_at, proof_scope: 'dsh_llm_generate_options' as const })
const tool = { type: 'block-end', index: 0, block: { type: 'tool-call', id: 'call_1', name: 'todo_write', arguments: '{}' } }
const terminal = { type: 'finish', reason: { kind: 'tool-calls' } }
async function collect(stream: AsyncIterable<unknown>) { const result = []; for await (const item of stream) result.push(item); return result }
function mockHub() {
  return {
    recordProviderInput: vi.fn<HubClient['recordProviderInput']>().mockImplementation(async p => inputAck(p)),
    recordProviderToolEmission: vi.fn<HubClient['recordProviderToolEmission']>().mockImplementation(async p => emissionAck(p)),
    recordProviderFinish: vi.fn<HubClient['recordProviderFinish']>().mockImplementation(async p => finishAck(p)),
  }
}
function freezeClock() { vi.useFakeTimers(); vi.setSystemTime(now) }
afterEach(() => vi.useRealTimers())

it.each([6_000, 26_500])('a %ims finish transport completes once with an independent finite budget', async durationMs => {
  freezeClock()
  const requests: { url: string; budget: string | null }[] = []
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const text = String(url); const payload = JSON.parse(String(init?.body))
    requests.push({ url: text, budget: new Headers(init?.headers).get('x-decision-hub-provider-input-budget-ms') })
    if (text.endsWith('/finish')) return await new Promise<Response>((resolve, reject) => {
      const timer = setTimeout(() => resolve(Response.json(finishAck(payload))), durationMs)
      init?.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')) }, { once: true })
    })
    return Response.json(text.includes('/tool-emissions/') ? emissionAck(payload) : inputAck(payload))
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const prepared = await prepareProviderInput(client, binding, options, scope, undefined, 30_000, new Date(now + 60_000).toISOString())
  const next = vi.fn(async function* () { yield tool; yield terminal })
  const pending = collect(recordedProviderStream(options, next, client, prepared))
  await vi.advanceTimersByTimeAsync(durationMs + 1)
  expect(await pending).toEqual([tool, terminal])
  expect(requests.map(x => x.budget)).toEqual(['30000', '60000', '60000'])
  expect(requests).toHaveLength(3)
  expect(next).toHaveBeenCalledOnce()
})

it('receipt IO uses the actual preparation remainder and declining Run deadline', async () => {
  freezeClock(); const hub = mockHub()
  const prepared = await prepareProviderInput(hub, binding, options, scope, undefined, 400, new Date(now + 4_000).toISOString())
  expect(hub.recordProviderInput.mock.calls[0]![2]).toBe(400)
  vi.setSystemTime(now + 1_000)
  expect(await collect(recordedProviderStream(options, async function* () { yield tool; yield terminal }, hub, prepared))).toEqual([tool, terminal])
  expect(hub.recordProviderToolEmission.mock.calls[0]![2]).toBe(3_000)
  expect(hub.recordProviderFinish.mock.calls[0]![2]).toBe(3_000)
})

it.each(['input', 'emission'] as const)('an actual 18.421s %s receipt fits its existing phase cap once', async phase => {
  freezeClock()
  const requests: { phase: string; budget: string | null }[] = []
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const path = String(url); const payload = JSON.parse(String(init?.body))
    const kind = path.endsWith('/finish') ? 'finish' : path.includes('/tool-emissions/') ? 'emission' : 'input'
    requests.push({ phase: kind, budget: new Headers(init?.headers).get('x-decision-hub-provider-input-budget-ms') })
    const ack = kind === 'finish' ? finishAck(payload) : kind === 'emission' ? emissionAck(payload) : inputAck(payload)
    if (kind !== phase) return Response.json(ack)
    return await new Promise<Response>((resolve, reject) => {
      const timer = setTimeout(() => resolve(Response.json(ack)), 18_421)
      init?.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')) }, { once: true })
    })
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const pending = (async () => {
    const prepared = await prepareProviderInput(client, binding, options, scope, undefined, 30_000, new Date(now + 120_000).toISOString())
    return await collect(recordedProviderStream(options, async function* () { yield tool; yield terminal }, client, prepared))
  })()
  await vi.advanceTimersByTimeAsync(18_422)
  expect(await pending).toEqual([tool, terminal])
  expect(requests).toEqual([{ phase: 'input', budget: '30000' }, { phase: 'emission', budget: '60000' }, { phase: 'finish', budget: '60000' }])
  expect(fetchImpl).toHaveBeenCalledTimes(3)
})

it('emission cancellation stops the caller but records one bounded cancellation audit', async () => {
  freezeClock(); const controller = new AbortController(); const hub = mockHub()
  const prepared = await prepareProviderInput(hub, binding, options, scope, undefined, 30_000, new Date(now + 120_000).toISOString())
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const payload = JSON.parse(String(init?.body))
    if (String(url).endsWith('/finish')) return Response.json(finishAck(payload))
    return await new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    })
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const emitted: unknown[] = []
  const pending = (async () => { for await (const item of recordedProviderStream({ ...options, signal: controller.signal }, async function* () { yield tool; yield terminal }, client, prepared)) emitted.push(item) })()
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_provider_cancelled', retryable: false })
  await vi.advanceTimersByTimeAsync(10_000); controller.abort(); await assertion
  expect(fetchImpl).toHaveBeenCalledTimes(2)
  expect(JSON.parse(String(fetchImpl.mock.calls[1]![1]?.body)).status).toBe('cancelled')
  expect(new Headers(fetchImpl.mock.calls[1]![1]?.headers).get('x-decision-hub-provider-input-budget-ms')).toBe('60000')
  expect(emitted).toEqual([])
})

it('an unknown emission commit is never replayed and no unacknowledged tool is yielded', async () => {
  freezeClock(); const hub = mockHub()
  const prepared = await prepareProviderInput(hub, binding, options, scope, undefined, 30_000, new Date(now + 120_000).toISOString())
  let serverCommitted = false
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const payload = JSON.parse(String(init?.body))
    if (String(url).endsWith('/finish')) return Response.json(finishAck(payload))
    return await new Promise<Response>((_resolve, reject) => {
      setTimeout(() => { serverCommitted = true }, 20_000)
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    })
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const emitted: unknown[] = []
  const pending = (async () => { for await (const item of recordedProviderStream(options, async function* () { yield tool; yield terminal }, client, prepared)) emitted.push(item) })()
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_provider_emission_transport_timeout', retryable: false })
  await vi.advanceTimersByTimeAsync(60_001); await assertion
  expect(serverCommitted).toBe(true)
  expect(fetchImpl.mock.calls.filter(([url]) => String(url).includes('/tool-emissions/'))).toHaveLength(1)
  expect(JSON.parse(String(fetchImpl.mock.calls[1]![1]?.body)).status).toBe('error')
  expect(emitted).toEqual([])
})

it('expired Run time refuses receipt IO; it never grants a completed terminal', async () => {
  freezeClock(); const hub = mockHub()
  const prepared = await prepareProviderInput(hub, binding, options, scope, undefined, 30_000, new Date(now + 1_000).toISOString())
  vi.setSystemTime(now + 1_001)
  await expect(collect(recordedProviderStream(options, async function* () { yield terminal }, hub, prepared))).rejects.toMatchObject({ code: 'dsh_provider_finish_deadline_elapsed' })
  expect(hub.recordProviderFinish).not.toHaveBeenCalled()
})

it('missing actual Run deadline blocks delegation but old unbound model flow keeps its ABI', async () => {
  freezeClock(); const next = vi.fn(async function* () { yield terminal }); const hub = { ...mockHub(),
    linkBySession: vi.fn<HubClient['linkBySession']>().mockResolvedValue({ ...binding, deadline_at: null } as unknown as Awaited<ReturnType<HubClient['linkBySession']>>),
    bindModel: vi.fn<HubClient['bindModel']>().mockResolvedValue(undefined),
  }
  expect(await collect(guardedModelStream(options, next, hub, binding.plugin_build_hash, undefined, undefined, async () => scope))).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_PROVIDER_RUN_DEADLINE_UNAVAILABLE' } } }])
  expect(next).not.toHaveBeenCalled()
  expect(await collect(guardedModelStream(options, next, hub, binding.plugin_build_hash))).toEqual([terminal])
  expect(next).toHaveBeenCalledOnce()
})

it.each(['input', 'emission', 'finish'] as const)('reports %s transport timeout without retry or a model-binding cause', async phase => {
  freezeClock(); const fixture = mockHub()
  const prepared = await prepareProviderInput(fixture, binding, options, scope, undefined, 30_000, new Date(now + 60_000).toISOString())
  await collect(recordedProviderStream(options, async function* () { yield tool; yield terminal }, fixture, prepared))
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => await new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  }))
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const pending = phase === 'input' ? client.recordProviderInput(fixture.recordProviderInput.mock.calls[0]![0], undefined, 100)
    : phase === 'emission' ? client.recordProviderToolEmission(fixture.recordProviderToolEmission.mock.calls[0]![0], undefined, 100)
      : client.recordProviderFinish(fixture.recordProviderFinish.mock.calls[0]![0], undefined, 100)
  const assertion = expect(pending).rejects.toMatchObject({ code: `dsh_provider_${phase}_transport_timeout`, retryable: false })
  await vi.advanceTimersByTimeAsync(101); await assertion
  expect(fetchImpl).toHaveBeenCalledOnce()
})

it('real server refusal is preserved and never turned into a longer transport allowance', async () => {
  freezeClock(); const fixture = mockHub()
  const prepared = await prepareProviderInput(fixture, binding, options, scope, undefined, 30_000, new Date(now + 60_000).toISOString())
  await collect(recordedProviderStream(options, async function* () { yield terminal }, fixture, prepared))
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ detail: 'research_provider_current_execution_mismatch' }, { status: 409 }))
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  await expect(client.recordProviderFinish(fixture.recordProviderFinish.mock.calls[0]![0], undefined, 15_000)).rejects.toEqual(expect.objectContaining({ code: 'research_provider_current_execution_mismatch' }))
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(new HubClientError('controlled', false).retryable).toBe(false)
})

it('all legacy receipt phases accept 30s and 60s and reject values above the current 60s wire cap', async () => {
  freezeClock(); const fixture = mockHub()
  const prepared = await prepareProviderInput(fixture, binding, options, scope, undefined, 30_000, new Date(now + 120_000).toISOString())
  await collect(recordedProviderStream(options, async function* () { yield tool; yield terminal }, fixture, prepared))
  const input = fixture.recordProviderInput.mock.calls[0]![0]
  const emission = fixture.recordProviderToolEmission.mock.calls[0]![0]
  const finish = fixture.recordProviderFinish.mock.calls[0]![0]
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const body = JSON.parse(String(init?.body)); const path = String(url)
    return Response.json(path.endsWith('/finish') ? finishAck(body) : path.includes('/tool-emissions/') ? emissionAck(body) : inputAck(body))
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  await client.recordProviderFinish(finish, undefined, 30_000)
  await client.recordProviderFinish(finish, undefined, 60_000)
  await client.recordProviderInput(input, undefined, 30_000)
  await client.recordProviderToolEmission(emission, undefined, 30_000)
  await client.recordProviderInput(input, undefined, 60_000)
  await client.recordProviderToolEmission(emission, undefined, 60_000)
  await expect(client.recordProviderFinish(finish, undefined, 60_001)).rejects.toMatchObject({ retryable: false })
  await expect(client.recordProviderInput(input, undefined, 60_001)).rejects.toMatchObject({ retryable: false })
  await expect(client.recordProviderToolEmission(emission, undefined, 60_001)).rejects.toMatchObject({ retryable: false })
  expect(fetchImpl).toHaveBeenCalledTimes(6)
})

it('a finish timeout with unknown server commit sends once and emits no completed terminal', async () => {
  freezeClock(); const hub = mockHub()
  const prepared = await prepareProviderInput(hub, binding, options, scope, undefined, 30_000, new Date(now + 120_000).toISOString())
  let serverCommitted = false
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => await new Promise<Response>((_resolve, reject) => {
    setTimeout(() => { serverCommitted = true }, 20_000)
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  }))
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  const emitted: unknown[] = []
  const pending = (async () => { for await (const item of recordedProviderStream(options, async function* () { yield terminal }, client, prepared)) emitted.push(item) })()
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_provider_finish_transport_timeout', retryable: false })
  await vi.advanceTimersByTimeAsync(60_001); await assertion
  expect(serverCommitted).toBe(true)
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(emitted).toEqual([])
})
