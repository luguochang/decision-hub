import { expect, it, vi } from 'vitest'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { HubClient, HubClientError } from '../src/host/hub-client.js'
import { dshModelDispatchBindingSchema } from '@decision-hub/contracts-ts'

const sessionId = `dsh_${'a'.repeat(64)}`
const link = {
  run_id: 'run-model', dsh_session_id: sessionId, request_hash: 'b'.repeat(64), generation: 1,
}
function hub() {
  return {
    linkBySession: vi.fn<HubClient['linkBySession']>().mockResolvedValue(link as Awaited<ReturnType<HubClient['linkBySession']>>),
    bindModel: vi.fn<HubClient['bindModel']>().mockResolvedValue(undefined),
  }
}
async function collect(stream: AsyncIterable<unknown>) {
  const chunks = []
  for await (const chunk of stream) chunks.push(chunk)
  return chunks
}

it('waits for the durable binding before the model stream and preserves its chunks', async () => {
  const client = hub()
  let release!: () => void
  client.bindModel.mockImplementation(() => new Promise<void>(resolve => { release = resolve }))
  const chunk = { type: 'text-delta', text: 'unchanged' }
  const next = vi.fn(async function* () { yield chunk })
  const pending = collect(guardedModelStream({
    sessionId, provider: 'actual-route', model: 'actual-model', maxTokens: 8192,
    reasoningEffort: 'high', stop: ['private-stop-text'],
  }, next, client, 'c'.repeat(64)))
  await vi.waitFor(() => expect(client.bindModel).toHaveBeenCalledOnce())
  expect(next).not.toHaveBeenCalled()
  expect(JSON.stringify(client.bindModel.mock.calls)).not.toContain('private-stop-text')
  expect(client.bindModel.mock.calls[0]?.[0]).toMatchObject({
    provider: 'actual-route', model: 'actual-model', max_tokens: 8192,
    reasoning_effort: 'high', temperature: null, purpose: 'conversation',
  })
  release()
  const result = await pending
  expect(result[0]).toBe(chunk)
  expect(next).toHaveBeenCalledOnce()
})

it.each(['lookup', 'binding'])('does not dispatch when %s fails', async phase => {
  const client = hub()
  if (phase === 'lookup') client.linkBySession.mockRejectedValue(new Error('unavailable'))
  else client.bindModel.mockRejectedValue(new HubClientError('dsh_model_configuration_changed_during_run', false))
  const next = vi.fn(async function* () { yield 1 })
  const result = await collect(guardedModelStream({ sessionId, provider: 'route', model: 'model' }, next, client, 'c'.repeat(64)))
  expect(result).toMatchObject([{ type: 'finish', reason: { kind: 'error', failure: {
    code: phase === 'lookup' ? 'DSH_MODEL_BINDING_UNAVAILABLE' : 'DSH_MODEL_CONFIGURATION_CHANGED_DURING_RUN',
  } } }])
  expect(next).not.toHaveBeenCalled()
})

it.each([{ sessionId: 'unmanaged' }, { sessionId, purpose: 'compaction' as const }, { sessionId, purpose: 'session-title' as const }])('leaves other model-selection policies outside this binding: %s', async scope => {
  const client = hub()
  const next = vi.fn(async function* () { yield 1 })
  expect(await collect(guardedModelStream({ ...scope, provider: 'route', model: 'model' }, next, client, 'c'.repeat(64)))).toEqual([1])
  expect(client.bindModel).not.toHaveBeenCalled()
  expect(client.linkBySession).not.toHaveBeenCalled()
})

it('rejects a mismatched session link before model dispatch', async () => {
  const client = hub()
  client.linkBySession.mockResolvedValue({ ...link, dsh_session_id: 'wrong' } as Awaited<ReturnType<HubClient['linkBySession']>>)
  const next = vi.fn(async function* () { yield 1 })
  const result = await collect(guardedModelStream({ sessionId, provider: 'route', model: 'model' }, next, client, 'c'.repeat(64)))
  expect(result).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_MODEL_DISPATCH_IDENTITY_MISMATCH' } } }])
  expect(next).not.toHaveBeenCalled()
})

it.each(['matched', 'wrong-receipt', 'known-conflict', 'unknown-conflict'])('checks the real callback response without exposing arbitrary errors: %s', async scenario => {
  const payload = dshModelDispatchBindingSchema.parse({
    schema_version: 'dsh-model-dispatch-binding.v1', ...link,
    plugin_build_hash: 'c'.repeat(64), purpose: 'conversation',
    provider: 'route', model: 'model', reasoning_effort: null,
    temperature: null, max_tokens: 8192, stop_sequences_hash: null,
  })
  const fetchImpl = vi.fn(async () => {
    if (scenario === 'known-conflict') return Response.json({ detail: 'dsh_model_configuration_changed_during_run' }, { status: 409 })
    if (scenario === 'unknown-conflict') return Response.json({ detail: 'private-config-content' }, { status: 409 })
    return Response.json(scenario === 'matched' ? payload : { ...payload, model: 'wrong' })
  })
  const client = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'test-only', timeoutMs: 1000, attempts: 3, ownerId: 'test', fetchImpl })
  if (scenario === 'matched') await expect(client.bindModel(payload)).resolves.toBeUndefined()
  else await expect(client.bindModel(payload)).rejects.toThrow({
    'wrong-receipt': 'dsh_model_binding_receipt_mismatch',
    'known-conflict': 'dsh_model_configuration_changed_during_run',
    'unknown-conflict': 'host_hub_http_409',
  }[scenario])
  expect(fetchImpl).toHaveBeenCalledOnce()
})


it.each(['child', 'grandchild'])('binds %s through trusted Host lineage before dispatch', async childId => {
  const client = hub()
  const parents: Record<string, string> = { child: sessionId, grandchild: 'child' }
  const agents = { get: (id: string) => parents[id] ? { id, session: { header: { parentSession: parents[id] } } } : undefined }
  const next = vi.fn(async function* () { yield 1 })
  expect(await collect(guardedModelStream({ sessionId: childId, provider: 'route', model: 'child-model' }, next, client, 'c'.repeat(64), agents))).toEqual([1])
  expect(client.linkBySession).toHaveBeenCalledWith(sessionId)
  expect(client.bindModel.mock.calls[0]?.[0]).toMatchObject({
    dsh_session_id: sessionId, execution_session_id: childId, parent_session_id: parents[childId], model: 'child-model',
  })
  expect(client.bindModel.mock.invocationCallOrder[0]).toBeLessThan(next.mock.invocationCallOrder[0]!)
})

it.each(['missing', 'cycle', 'mismatch'])('blocks broken Host lineage: %s', async mode => {
  const client = hub()
  const agents = { get: (id: string) => id === 'child' ? {
    id: mode === 'mismatch' ? 'other' : id, session: { header: { parentSession: mode === 'cycle' ? id : 'missing' } },
  } : undefined }
  const next = vi.fn(async function* () { yield 1 })
  const chunks = await collect(guardedModelStream({ sessionId: 'child', provider: 'route', model: 'model' }, next, client, 'c'.repeat(64), agents))
  expect(chunks).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_MODEL_DISPATCH_LINEAGE_INVALID' } } }])
  expect(next).not.toHaveBeenCalled()
  expect(client.bindModel).not.toHaveBeenCalled()
})

it('does not reclassify an unrelated provider exception as a binding failure', async () => {
  const client = hub()
  const next = async function* () { throw new Error('ordinary-provider-failure') }
  await expect(collect(guardedModelStream({ sessionId: 'unmanaged', provider: 'route', model: 'model' }, next, client, 'c'.repeat(64)))).rejects.toThrow('ordinary-provider-failure')
})
