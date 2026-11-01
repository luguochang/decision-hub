// Pure GenerateOptions/receipt seam, no model, socket or financial authority.
import { createHash } from 'node:crypto'
import { expect, it } from 'vitest'
import { dshModelDispatchBindingSchema } from '@decision-hub/contracts-ts'
import { prepareProviderInput, providerToolEmission } from '../src/host/provider-input.js'

const sha = (text: string) => createHash('sha256').update(text).digest('hex')
const binding = () => dshModelDispatchBindingSchema.parse({
  schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'synthetic-input-cap-run',
  dsh_session_id: 'synthetic-input-cap-root', request_hash: 'a'.repeat(64), generation: 1,
  plugin_build_hash: 'b'.repeat(64), execution_session_id: null, parent_session_id: null,
  provider: 'synthetic', model: 'no-model', purpose: 'conversation', reasoning_effort: null,
  temperature: null, max_tokens: 32768, stop_sequences_hash: null,
})
const scope = { host_instance_id: '11111111-1111-4111-8111-111111111111', process_id: 1,
  started_at: '2026-01-01T00:00:00Z', hub_base_url: 'http://synthetic.invalid',
  workspace_id: 'synthetic', workspace_cwd_hash: 'c'.repeat(64) }
function options(bytes: number) {
  const overhead = Buffer.byteLength(JSON.stringify({ messages: [], system: '', tools: [] }), 'utf8')
  return { sessionId: 'synthetic-input-cap-root', provider: 'synthetic', model: 'no-model',
    messages: [], system: 'x'.repeat(bytes - overhead), tools: [] }
}
function receiptHub() {
  const inputs: any[] = []
  return { inputs, async recordProviderInput(payload: any) {
    inputs.push(payload)
    return { dispatch_id: payload.dispatch_id, input_hash: payload.input_hash,
      reference: { output_id: 'synthetic-input', payload_hash: 'd'.repeat(64) } }
  }, async recordProviderToolEmission() { throw new Error('unused') },
  async recordProviderFinish() { throw new Error('unused') } }
}

it('records complete inputs above 1 MB and at 8 MiB without changing bytes', async () => {
  for (const bytes of [1_100_000, 8 * 1024 * 1024]) {
    const hub = receiptHub(), input = options(bytes)
    await prepareProviderInput(hub as any, binding(), input as any, scope, undefined, 1000,
      new Date(Date.now() + 60_000).toISOString())
    expect(hub.inputs).toHaveLength(1)
    expect(Buffer.byteLength(hub.inputs[0].input_json, 'utf8')).toBe(bytes)
    expect(hub.inputs[0].input_hash).toBe(sha(hub.inputs[0].input_json))
    expect(JSON.parse(hub.inputs[0].input_json)).toEqual({ messages: [], system: input.system, tools: [] })
  }
})

it('rejects complete input over 8 MiB before recording and retains the tool argument gate', async () => {
  const hub = receiptHub()
  await expect(prepareProviderInput(hub as any, binding(), options(8 * 1024 * 1024 + 1) as any,
    scope, undefined, 1000, new Date(Date.now() + 60_000).toISOString())).rejects.toThrow('dsh_provider_input_oversize')
  expect(hub.inputs).toHaveLength(0)
  expect(() => providerToolEmission({} as any, {} as any, { type: 'block-end', index: 0,
    block: { type: 'tool-call', id: 'synthetic-call', name: 'synthetic-tool',
      arguments: JSON.stringify({ value: 'x'.repeat(1_000_001) }) } })).toThrow('dsh_provider_tool_block_invalid')
})
