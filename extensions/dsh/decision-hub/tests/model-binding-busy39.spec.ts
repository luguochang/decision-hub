import { expect, it, vi } from 'vitest'
import { HubClient, HubClientError } from '../src/host/hub-client.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { dshModelDispatchBindingSchema } from '@decision-hub/contracts-ts'

const link = { run_id: `run_${'a'.repeat(32)}`, dsh_session_id: `dsh_${'b'.repeat(64)}`, request_hash: 'c'.repeat(64), generation: 1 }
const payload = dshModelDispatchBindingSchema.parse({ schema_version: 'dsh-model-dispatch-binding.v1', ...link,
  plugin_build_hash: 'd'.repeat(64), purpose: 'conversation', execution_session_id: null, parent_session_id: null,
  provider: 'private-control', model: 'private-model', reasoning_effort: null, temperature: null, max_tokens: 8192, stop_sequences_hash: null })

function client(detail: string) {
  const fetchImpl = vi.fn(async () => Response.json({ detail }, { status: 409 }))
  const hub = new HubClient({ baseUrl: 'http://isolated.invalid', callbackKey: 'private-only', timeoutMs: 1000,
    attempts: 3, ownerId: 'private-owner', fetchImpl })
  return { hub, fetchImpl }
}

it('preserves the pre-BEGIN typed recoverable cause with exactly one request and no model dispatch', async () => {
  const first = client('dsh_model_binding_writer_busy')
  const error = await first.hub.bindModel(payload).catch(error => error)
  expect(error).toBeInstanceOf(HubClientError)
  expect(error.code).toBe('dsh_model_binding_writer_busy')
  expect(error.retryable).toBe(true)
  expect(first.fetchImpl).toHaveBeenCalledOnce()
  const second = client('dsh_model_binding_writer_busy')
  const next = vi.fn(async function* () { yield { type: 'text-delta', text: 'must not dispatch' } })
  const chunks = []
  const bridge = { bindModel: second.hub.bindModel.bind(second.hub),
    linkBySession: vi.fn().mockResolvedValue(link as Awaited<ReturnType<HubClient['linkBySession']>>) }
  for await (const item of guardedModelStream({ sessionId: link.dsh_session_id, provider: 'private-control', model: 'private-model' },
    next, bridge, 'd'.repeat(64))) chunks.push(item)
  expect(chunks).toMatchObject([{ type: 'finish', reason: { kind: 'error', failure: { code: 'DSH_MODEL_BINDING_WRITER_BUSY' } } }])
  expect(second.fetchImpl).toHaveBeenCalledOnce()
  expect(next).not.toHaveBeenCalled()
})

it.each(['dsh_model_binding_commit_unconfirmed', 'database is locked', 'dsh_model_configuration_changed_during_run'])(
  'never grants busy retry eligibility to another conflict: %s', async detail => {
    const { hub, fetchImpl } = client(detail)
    const error = await hub.bindModel(payload).catch(error => error)
    expect(error).toBeInstanceOf(HubClientError)
    expect(error.code).not.toBe('dsh_model_binding_writer_busy')
    expect(error.retryable).toBe(false)
    expect(fetchImpl).toHaveBeenCalledOnce()
  })
