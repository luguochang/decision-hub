// Synthetic SDK/callback fixtures exercise engineering boundaries only.
// They are not real Provider input, retained-context or financial receipts.
import { createHash } from 'node:crypto'
import { expect, it, vi } from 'vitest'
import { dshRunSessionLinkViewSchema, researchProviderInputRecordedSchema, researchProviderToolEmissionRecordedSchema,
  researchProviderFinishRecordedSchema, type ResearchProviderInputSubmit,
  type ResearchProviderToolEmissionSubmit, type ResearchProviderFinishSubmit } from '@decision-hub/contracts-ts'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { HubClient, HubClientError } from '../src/host/hub-client.js'
import { providerCanonicalJson, prepareProviderInput, registerProviderInputReader,
  verifySynthesisProviderInput, type ProviderInputHub } from '../src/host/provider-input.js'
import type { SynthesisReviewContext } from '../src/final-review-gate.js'
import type { ModelDispatchOptions } from '../src/host/dsh-public.js'
import type { HostContextPublic } from '../src/host/dsh-public.js'
import { DecisionHubHostBridge, resolveHostConfig } from '../src/host/bridge.js'
import { PLUGIN_BUILD_HASH } from '../src/generated-build-identity.js'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { LEGACY_SYNTHESIS_REF } from '../src/synthesis-protocol.js'
import { deterministicIds, type SessionCorrelations } from '../src/host/session-correlation.js'

const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const providerStagePublisher = Symbol.for('decision-hub.provider-stage-diagnostic.publisher.v1')
const providerDispatchRunner = Symbol.for('decision-hub.provider-dispatch-context.runner.v1')
const rootId = deterministicIds('run-test', 'b'.repeat(64)).sessionId
const link = { run_id: 'run-test', dsh_session_id: rootId, request_hash: 'b'.repeat(64), generation: 1, deadline_at: '2099-01-01T00:00:00Z' }
const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
  started_at: '2026-10-08T00:00:00Z', hub_base_url: 'http://hub.test', workspace_id: 'workspace-test', workspace_cwd_hash: 'c'.repeat(64) }
const ref = (role: string) => ({ output_id: `out-${role}`, role, schema_version: `${role}.v1`, payload_hash: 'd'.repeat(64), recorded_at: '2026-10-08T00:00:00Z' })
function inputAck(p: ResearchProviderInputSubmit) {
  return researchProviderInputRecordedSchema.parse({ schema_version: 'research-provider-input-recorded.v1',
    reference: ref('research_provider_input'), dispatch_id: p.dispatch_id, input_hash: p.input_hash,
    known_at: scope.started_at, producer_authenticated: true, proof_scope: 'dsh_llm_generate_options' })
}
function emissionAck(p: ResearchProviderToolEmissionSubmit) {
  return researchProviderToolEmissionRecordedSchema.parse({ schema_version: 'research-provider-tool-emission-recorded.v1',
    reference: ref('research_provider_tool_emission'), input_ref: p.input_ref,
    run_id: p.run_id, generation: p.generation, root_session_id: p.root_session_id,
    execution_session_id: p.execution_session_id, dispatch_id: p.dispatch_id, call_id: p.call_id, tool_name: p.tool_name,
    raw_arguments_hash: p.raw_arguments_hash, arguments_hash: p.arguments_hash,
    candidate_hash: p.tool_name === 'decision_hub_synthesis_submit' ? 'e'.repeat(64) : null,
    proof_scope: 'dsh_llm_generate_options' })
}
function finishAck(p: ResearchProviderFinishSubmit) {
  return researchProviderFinishRecordedSchema.parse({ schema_version: 'research-provider-finish-recorded.v1', reference: ref('research_provider_finish'),
    input_ref: p.input_ref, dispatch_id: p.dispatch_id, finish_hash: p.finish_hash, status: p.status,
    known_at: scope.started_at, proof_scope: 'dsh_llm_generate_options' })
}
function client() {
  return { linkBySession: vi.fn<HubClient['linkBySession']>().mockResolvedValue(link as Awaited<ReturnType<HubClient['linkBySession']>>),
    bindModel: vi.fn<HubClient['bindModel']>().mockResolvedValue(undefined),
    recordProviderInput: vi.fn<HubClient['recordProviderInput']>().mockImplementation(async p => inputAck(p)),
    recordProviderToolEmission: vi.fn<HubClient['recordProviderToolEmission']>().mockImplementation(async p => emissionAck(p)),
    recordProviderFinish: vi.fn<HubClient['recordProviderFinish']>().mockImplementation(async p => finishAck(p)),
    providerToolEmission: vi.fn<HubClient['providerToolEmission']>(),
  }
}
const options: ModelDispatchOptions = { sessionId: rootId, provider: 'synthetic-route', model: 'synthetic-model',
  messages: [{ role: 'user', content: [{ type: 'text', text: 'critical initial body' }] }] as ModelDispatchOptions['messages'],
  system: 'actual system fixture', tools: [] }
const terminal = { type: 'finish', reason: { kind: 'stop' } }
const tool = { type: 'block-end', index: 0, block: { type: 'tool-call', id: 'provider-call-1', name: 'decision_hub_synthesis_submit', arguments: '{"candidate":{"schema_version":"research-synthesis-candidate.v1"}}' } }
async function collect(stream: AsyncIterable<unknown>) { const rows = []; for await (const row of stream) rows.push(row); return rows }
function guarded(c: ReturnType<typeof client>, next: () => AsyncIterable<unknown>, opts = options) {
  return guardedModelStream(opts, next, c, 'f'.repeat(64), undefined, undefined, async () => scope)
}
const context = { binding: { runId: link.run_id, sessionId: rootId, requestHash: link.request_hash, requestId: deterministicIds(link.run_id, link.request_hash).requestId, generation: 1 },
  request: { output_schema_ref: LEGACY_SYNTHESIS_REF, deadline_at: link.deadline_at } } as SynthesisReviewContext

it('saves full actual public options before delegate and each complete block before yield', async () => {
  const c = client()
  let release!: () => void
  c.recordProviderToolEmission.mockImplementation(p => new Promise(resolve => { release = () => resolve(emissionAck(p)) }))
  const usage = { type: 'usage', usage: { inputTokens: 21, outputTokens: 8, cacheReadTokens: 4, reasoningTokens: 3 } }
  const finish = { ...terminal, replayState: { private: 'not archived' } }
  const next = vi.fn(async function* () { yield { type: 'tool-call-delta', index: 0, id: tool.block.id, argumentsDelta: '{' }; yield tool; yield usage; yield finish })
  const iterator = guarded(c, next)[Symbol.asyncIterator]()
  expect((await iterator.next()).value).toMatchObject({ type: 'tool-call-delta' })
  const pending = iterator.next()
  await vi.waitFor(() => expect(c.recordProviderToolEmission).toHaveBeenCalledOnce())
  expect(c.recordProviderFinish).not.toHaveBeenCalled()
  const input = c.recordProviderInput.mock.calls[0]![0]
  expect(JSON.parse(input.input_json)).toEqual({ messages: options.messages, system: options.system, tools: options.tools })
  expect(input.input_hash).toBe(sha(input.input_json))
  expect(input.host_scope).toEqual(scope)
  expect(c.recordProviderInput.mock.invocationCallOrder[0]).toBeLessThan(next.mock.invocationCallOrder[0]!)
  release()
  expect((await pending).value).toBe(tool)
  expect((await iterator.next()).value).toBe(usage)
  expect((await iterator.next()).value).toBe(finish)
  expect(c.recordProviderFinish).toHaveBeenCalledOnce() // before terminal is seen
  expect((await iterator.next()).done).toBe(true)
  const saved = c.recordProviderFinish.mock.calls[0]![0]
  expect(JSON.parse(saved.finish_json)).toEqual({ finish: terminal, usage_chunks: [usage] })
  expect(saved.status).toBe('completed')
  expect(saved.error_code).toBeNull()
  expect(saved.finish_json).not.toContain('not archived')
  expect(saved.finish_json).not.toContain('cost_usd')
  expect(next).toHaveBeenCalledOnce()
})

it('runs the Provider delegate hook once after the input receipt and before next', async () => {
  const c = client()
  const next = vi.fn(async function* () { yield terminal })
  const afterRecorded = vi.fn(async () => undefined)
  const beforeDelegate = vi.fn(() => {
    expect(c.recordProviderInput).toHaveBeenCalledOnce()
    expect(afterRecorded).toHaveBeenCalledOnce()
    expect(next).not.toHaveBeenCalled()
  })
  const rows = await collect(guardedModelStream(options, next, c, 'f'.repeat(64), undefined, undefined,
    async () => scope, undefined, afterRecorded, undefined, beforeDelegate))
  expect(rows).toEqual([terminal])
  expect(beforeDelegate).toHaveBeenCalledOnce()
  expect(c.recordProviderInput.mock.invocationCallOrder[0]).toBeLessThan(beforeDelegate.mock.invocationCallOrder[0]!)
  expect(afterRecorded.mock.invocationCallOrder[0]).toBeLessThan(beforeDelegate.mock.invocationCallOrder[0]!)
  expect(beforeDelegate.mock.invocationCallOrder[0]).toBeLessThan(next.mock.invocationCallOrder[0]!)
})

it.each([
  { name: 'no chunks', next: async function* () {}, expected: ['receipt_completed', 'delegate_started', 'stream_failed'] },
  { name: 'first nonterminal chunk', next: async function* () { yield { type: 'text-delta', text: 'private' } },
    expected: ['receipt_completed', 'delegate_started', 'first_sdk_chunk', 'stream_failed'] },
  { name: 'normal finish', next: async function* () { yield terminal },
    expected: ['receipt_completed', 'delegate_started', 'first_sdk_chunk', 'stream_finished'] },
  { name: 'stream exception', next: async function* () {
    throw new HubClientError('dsh_provider_stream_failed', false, new Error('private provider body'))
  },
    expected: ['receipt_completed', 'delegate_started', 'stream_failed'] },
] as const)('emits bounded Provider lifecycle stages for $name', async scenario => {
  const c = client(), stages: string[] = [], durable: Record<string, unknown>[] = []
  const dispatches: Record<string, unknown>[] = []
  Object.defineProperty(globalThis, providerStagePublisher, {
    configurable: true, value: (value: Record<string, unknown>) => durable.push(value),
  })
  Object.defineProperty(globalThis, providerDispatchRunner, {
    configurable: true, value: (value: Record<string, unknown>, operation: () => unknown) => {
      dispatches.push(value); return operation()
    },
  })
  try {
    await collect(guardedModelStream(options, scenario.next, c, 'f'.repeat(64), undefined, undefined,
      async () => scope, undefined, undefined, undefined, undefined, stage => stages.push(stage)))
  } finally {
    delete (globalThis as unknown as Record<symbol, unknown>)[providerStagePublisher]
    delete (globalThis as unknown as Record<symbol, unknown>)[providerDispatchRunner]
  }
  expect(stages).toEqual(scenario.expected)
  expect(durable.map(row => row.stage)).toEqual(['receipt_started', ...scenario.expected])
  for (const row of durable) {
    expect(Object.keys(row).every(key => [
      'schema_version', 'at', 'stage', 'run_hash', 'dispatch_id', 'input_hash', 'generation',
      'execution_session_hash', 'messages_bytes', 'system_bytes', 'tools_bytes', 'input_bytes', 'finish_bytes',
    ].includes(key))).toBe(true)
    expect(row.run_hash).toMatch(/^[a-f0-9]{64}$/)
    expect(row.execution_session_hash).toMatch(/^[a-f0-9]{64}$/)
  }
  expect(durable[0]).toMatchObject({
    messages_bytes: Buffer.byteLength(JSON.stringify(options.messages), 'utf8'),
    system_bytes: Buffer.byteLength(JSON.stringify(options.system), 'utf8'),
    tools_bytes: Buffer.byteLength(JSON.stringify(options.tools), 'utf8'),
    input_bytes: Buffer.byteLength(JSON.stringify({ messages: options.messages, system: options.system, tools: options.tools }), 'utf8'),
  })
  expect(dispatches.length).toBeGreaterThanOrEqual(3) // factory, iterator, and at least one next
  expect(new Set(dispatches.map(row => row.dispatch_id))).toEqual(new Set([durable[0]!.dispatch_id]))
  for (const row of dispatches) expect(Object.keys(row).sort()).toEqual([
    'dispatch_id', 'execution_session_hash', 'generation', 'input_hash', 'run_hash',
  ])
  expect(JSON.stringify({ stages, durable, dispatches })).not.toContain('private')
})

it('keeps Provider stage sink failures out of execution', async () => {
  const c = client(), hostDiagnostic = vi.fn(() => { throw new Error('host diagnostic sink failed') })
  const durableDiagnostic = vi.fn(() => { throw new Error('durable diagnostic sink failed') })
  Object.defineProperty(globalThis, providerStagePublisher, { configurable: true, value: durableDiagnostic })
  try {
    await expect(collect(guardedModelStream(options, async function* () { yield terminal }, c, 'f'.repeat(64),
      undefined, undefined, async () => scope, undefined, undefined, undefined, undefined, hostDiagnostic)))
      .resolves.toEqual([terminal])
  } finally { delete (globalThis as unknown as Record<symbol, unknown>)[providerStagePublisher] }
  expect(hostDiagnostic).toHaveBeenCalledTimes(4)
  expect(durableDiagnostic).toHaveBeenCalledTimes(5)
})

it.each(['input', 'emission', 'finish'] as const)('refuses an incorrect %s receipt without releasing its protected block', async phase => {
  const c = client()
  if (phase === 'input') c.recordProviderInput.mockImplementation(async p => ({ ...inputAck(p), input_hash: '0'.repeat(64) }))
  if (phase === 'emission') c.recordProviderToolEmission.mockImplementation(async p => ({ ...emissionAck(p), execution_session_id: 'wrong' }))
  if (phase === 'finish') c.recordProviderFinish.mockImplementation(async p => ({ ...finishAck(p), finish_hash: '0'.repeat(64) }))
  const next = vi.fn(async function* () { yield tool; yield terminal })
  const rows = await collect(guarded(c, next))
  expect(rows.at(-1)).toMatchObject({ type: 'finish', reason: { kind: 'error', failure: {
    code: `DSH_PROVIDER_${phase === 'emission' ? 'EMISSION' : phase.toUpperCase()}_RECEIPT_MISMATCH`,
  } } })
  if (phase === 'input') expect(next).not.toHaveBeenCalled()
  if (phase === 'emission') expect(rows).not.toContain(tool)
  if (phase === 'finish') expect(rows).not.toContain(terminal)
  expect(c.recordProviderFinish.mock.calls.length).toBeLessThanOrEqual(1)
})

it('callback refusal leaves a safe cause and stops before executing the tool block', async () => {
  const c = client()
  c.recordProviderToolEmission.mockRejectedValue(new HubClientError('dsh_provider_emission_identity_mismatch', false, new Error('private raw body')))
  const rows = await collect(guarded(c, async function* () { yield tool; yield terminal }))
  expect(rows).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_PROVIDER_EMISSION_IDENTITY_MISMATCH' } } }])
  expect(JSON.stringify(rows)).not.toContain('private raw body')
  expect(c.recordProviderFinish.mock.calls[0]![0]).toMatchObject({ status: 'error', error_code: 'dsh_provider_emission_identity_mismatch' })
})

it('records missing terminal as interrupted and never invents a completed finish or usage', async () => {
  const c = client()
  const rows = await collect(guarded(c, async function* () { yield { type: 'text-delta', text: 'not a terminal' } }))
  expect(rows.at(-1)).toMatchObject({ reason: { failure: { code: 'DSH_PROVIDER_FINISH_MISSING' } } })
  const saved = c.recordProviderFinish.mock.calls[0]![0]
  expect(saved.status).toBe('interrupted')
  expect(JSON.parse(saved.finish_json)).toEqual({ finish: null, usage_chunks: [] })
})

it('cancelled delegated work gets one independent bounded finish callback', async () => {
  const c = client(); const abort = new AbortController()
  const rows = await collect(guarded(c, async function* () { abort.abort(); yield tool }, { ...options, signal: abort.signal }))
  expect(rows).not.toContain(tool)
  expect(c.recordProviderToolEmission).not.toHaveBeenCalled()
  expect(c.recordProviderFinish.mock.calls[0]).toEqual([expect.objectContaining({ status: 'cancelled' }), undefined, 60_000, undefined])
})

it('binds exact synthesis root/call/arguments and restores reader from durable exact route', async () => {
  const c = client(); const root = {}
  const rows = await collect(guarded(c, async function* () { yield tool; yield terminal }))
  expect(rows).toContain(tool)
  const emission = c.recordProviderToolEmission.mock.calls[0]![0]
  c.providerToolEmission.mockResolvedValue(emissionAck(emission))
  const dispose = registerProviderInputReader(root, c)
  const args = JSON.parse(tool.block.arguments)
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, args, new AbortController().signal)).resolves.toMatchObject({ call_id: tool.block.id })
  expect(c.providerToolEmission).toHaveBeenCalledWith(link.run_id, 1, rootId, tool.block.id, expect.any(AbortSignal), context.request.deadline_at)
  await expect(verifySynthesisProviderInput(root, context, 'child', tool.block.id, args, new AbortController().signal)).rejects.toThrow('dsh_provider_synthesis_root_required')
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, { candidate: 'different' }, new AbortController().signal)).rejects.toThrow('dsh_provider_emission_identity_mismatch')
  c.providerToolEmission.mockRejectedValue(new HubClientError('host_hub_http_409', false))
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, args, new AbortController().signal)).rejects.toThrow('host_hub_http_409')
  dispose()
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, args, new AbortController().signal)).rejects.toThrow('dsh_provider_reader_unavailable')
  registerProviderInputReader(root, c)() // disposal supports actual Host restart
})

it('keeps authenticated Harness consumption independent while recording ordinary input and finish proofs', async () => {
  const c = client(); const root = {}
  c.linkBySession.mockResolvedValue({ ...link, harness_required: true } as Awaited<ReturnType<HubClient['linkBySession']>>)
  const harness = vi.fn().mockResolvedValue(undefined)
  const next = vi.fn(async function* () { yield terminal })
  expect(await collect(guardedModelStream(options, next, c, 'f'.repeat(64), undefined, harness, async () => scope))).toEqual([terminal])
  expect(harness).toHaveBeenCalledOnce()
  expect(c.bindModel).not.toHaveBeenCalled()
  expect(c.recordProviderInput).toHaveBeenCalledOnce()
  expect(c.recordProviderInput.mock.calls[0]![0]).toMatchObject({ root_session_id: rootId, execution_session_id: rootId })
  expect(c.recordProviderFinish).toHaveBeenCalledOnce()
  const dispose = registerProviderInputReader(root, c)
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, {}, new AbortController().signal)).resolves.toBeNull()
  expect(c.providerToolEmission).not.toHaveBeenCalled()
  dispose()
})

it('rejects lossless-JSON violations and oversize full inputs before delegation', async () => {
  const cycle: unknown[] = []; cycle.push(cycle)
  for (const value of [cycle, { n: Number.NaN }, { n: Number.MAX_SAFE_INTEGER + 1 }, { n: undefined }]) {
    expect(() => providerCanonicalJson(value)).toThrow('dsh_provider_json_invalid')
  }
  expect(providerCanonicalJson({ x: 1.0, y: -0, z: 1e-7, w: 1e-6 })).toBe('{"w":0.000001,"x":1,"y":0,"z":1e-7}')
  const c = client(); const next = vi.fn(async function* () { yield terminal })
  const rows = await collect(guarded(c, next, { ...options, system: '字'.repeat(2_800_000) }))
  expect(rows).toMatchObject([{ reason: { failure: { code: 'DSH_PROVIDER_INPUT_OVERSIZE' } } }])
  expect(next).not.toHaveBeenCalled()
  expect(c.recordProviderInput).not.toHaveBeenCalled()
})

it('previous refs are exact per session; no process-local chain survives a reader restart', async () => {
  const c = client()
  const binding = { schema_version: 'dsh-model-dispatch-binding.v1' as const, ...link, plugin_build_hash: 'f'.repeat(64), purpose: 'conversation' as const,
    execution_session_id: null, parent_session_id: null,
    provider: options.provider, model: options.model, reasoning_effort: null, temperature: null, max_tokens: null, stop_sequences_hash: null }
  await prepareProviderInput(c, binding, options, scope, undefined, 5_000, link.deadline_at)
  await prepareProviderInput(c, binding, options, scope, undefined, 5_000, link.deadline_at)
  expect(c.recordProviderInput.mock.calls[0]![0].previous_input_ref).toBeNull()
  expect(c.recordProviderInput.mock.calls[1]![0].previous_input_ref).toEqual({ output_id: ref('research_provider_input').output_id, payload_hash: ref('research_provider_input').payload_hash })
  const restarted = client()
  await prepareProviderInput(restarted, binding, options, scope, undefined, 5_000, link.deadline_at)
  expect(restarted.recordProviderInput.mock.calls[0]![0].previous_input_ref).toBeNull()
})

it('actual Hub callback paths use authenticated one-attempt PUT and exact emission GET', async () => {
  const c = client()
  await collect(guarded(c, async function* () { yield tool; yield terminal }))
  const input = c.recordProviderInput.mock.calls[0]![0]; const emission = c.recordProviderToolEmission.mock.calls[0]![0]
  const finish = c.recordProviderFinish.mock.calls[0]![0]
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const path = new URL(String(url)).pathname
    if (init?.method === 'GET') return Response.json(emissionAck(emission))
    if (path.endsWith('/finish')) return Response.json(finishAck(finish))
    if (path.includes('/tool-emissions/')) return Response.json(emissionAck(emission))
    return Response.json(inputAck(input))
  })
  const realClient = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic-secret', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  await realClient.recordProviderInput(input)
  await realClient.recordProviderToolEmission(emission)
  await realClient.recordProviderFinish(finish)
  await realClient.providerToolEmission(link.run_id, 1, rootId, tool.block.id)
  expect(fetchImpl).toHaveBeenCalledTimes(4)
  expect(String(fetchImpl.mock.calls[0]![0])).toContain(`/provider-inputs/${input.dispatch_id}`)
  expect(String(fetchImpl.mock.calls[1]![0])).toContain(`/provider-inputs/${input.dispatch_id}/tool-emissions/${tool.block.id}`)
  expect(String(fetchImpl.mock.calls[2]![0])).toContain(`/provider-inputs/${input.dispatch_id}/finish`)
  const read = new URL(String(fetchImpl.mock.calls[3]![0]))
  expect(read.pathname).toBe(`/v1/dsh/sessions/${link.run_id}/provider-tool-emissions/${tool.block.id}`)
  expect(read.searchParams.get('generation')).toBe('1')
  expect(read.searchParams.get('execution_session_id')).toBe(rootId)
  const headers = new Headers(fetchImpl.mock.calls[0]![1]?.headers)
  expect(headers.get('x-decision-hub-bridge-key')).toBe('synthetic-secret')
})

it('live managed-child Bridge installs the reader, requires root recovery and records resolved workspace/process scope', async () => {
  const root = {}; const listeners = new Map<string, Function>()
  const inputs: ResearchProviderInputSubmit[] = []; const emitted: ResearchProviderToolEmissionSubmit[] = []
  const workspace = { id: 'actual-workspace-fixture', sessionIds: [rootId] }
  const resolveByPath = vi.fn().mockResolvedValue(workspace)
  const view = { schema_version: 'dsh-run-session-link.v1', ...link, state: 'running',
    upstream_identity: { source_commit: '0a53fb55bea101816fa226bb964ae2bed71c343b', source_version: '0.1.2-alpha.2',
      package_versions: { '@deepseek-ai/dsh': '0.1.2-alpha.2' }, plugin_build_hash: PLUGIN_BUILD_HASH },
    deadline_at: new Date(Date.now() + 60_000).toISOString(), max_tool_calls: 12, tool_calls_started: 0, model_step_timeout_ms: 60_000,
    accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 0, error_code: null,
    trace_ref: null, result_ref: null, result_hash: null,
    created_at: scope.started_at, updated_at: scope.started_at }
  const recovered = dshRunSessionLinkViewSchema.parse(view)
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const path = new URL(String(url)).pathname
    if (path === '/health/ready') return Response.json({ ready: true })
    if (path.includes('/by-session/')) return Response.json(view)
    if (path.includes('/provider-tool-emissions/')) return Response.json(emissionAck(emitted[0]!))
    const body = JSON.parse(String(init?.body))
    if (path.endsWith('/finish')) return Response.json(finishAck(body))
    if (path.includes('/tool-emissions/')) { emitted.push(body); return Response.json(emissionAck(body)) }
    if (path.includes('/provider-inputs/')) { inputs.push(body); return Response.json(inputAck(body)) }
    return Response.json(body) // model binding callback returns its exact original
  })
  const ctx = { root, webServer: { register: () => () => {} }, connection: { fetch: { register: () => async () => {} } },
    agents: { get: (id: string) => id === 'provider-child' ? { id, session: { header: { parentSession: rootId } } } : undefined },
    sessionController: {}, workspaceRegistry: { resolveByPath }, on: (name: string, callback: Function) => {
      listeners.set(name, callback); return () => { listeners.delete(name) }
    }, logger: { info() {}, warn() {}, error() {} } } as unknown as HostContextPublic
  const bridge = new DecisionHubHostBridge(ctx, resolveHostConfig({ hubBaseUrl: 'http://hub.test', inboundKey: 'synthetic-only',
    callbackKey: 'synthetic-only', defaultWorkspaceCwd: process.cwd(), pluginBuildHash: PLUGIN_BUILD_HASH, fetchImpl }))
  const dispose = bridge.start()
  const blocked = vi.fn(async function* () { yield terminal })
  expect(await collect(listeners.get('llm/stream')!(options, blocked))).toMatchObject([
    { reason: { failure: { code: 'DSH_MODEL_DISPATCH_IDENTITY_MISMATCH' } } },
  ])
  expect(blocked).not.toHaveBeenCalled(); expect(inputs).toEqual([])
  // This isolated hook fixture uses the real public recovery codec, not HTTP admission.
  ;(bridge as unknown as { correlations: SessionCorrelations }).correlations.recover(recovered)
  const childTool = { ...tool, block: { ...tool.block, name: 'todo_write', arguments: '{}' } }
  const result = await collect(listeners.get('llm/stream')!({ ...options, sessionId: 'provider-child' }, async function* () { yield childTool; yield terminal }))
  expect(result).toEqual([childTool, terminal])
  expect(inputs).toHaveLength(1)
  expect(inputs[0]!).toMatchObject({ root_session_id: rootId, execution_session_id: 'provider-child', parent_session_id: rootId })
  expect(inputs[0]!.host_scope).toMatchObject({ process_id: process.pid, workspace_id: workspace.id, hub_base_url: 'http://hub.test' })
  expect(inputs[0]!.host_scope.workspace_cwd_hash).toMatch(/^[a-f0-9]{64}$/)
  expect(resolveByPath).toHaveBeenCalledWith(process.cwd())
  await expect(verifySynthesisProviderInput(root, context, 'provider-child', tool.block.id, {}, new AbortController().signal)).rejects.toThrow('dsh_provider_synthesis_root_required')
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, {}, new AbortController().signal)).rejects.toThrow('dsh_provider_emission_identity_mismatch')
  dispose()
  await expect(verifySynthesisProviderInput(root, context, rootId, tool.block.id, {}, new AbortController().signal)).rejects.toThrow('dsh_provider_reader_unavailable')
  bridge.start()() // registry disposal allows a normal Host lifecycle restart
})

it('keeps compaction out of ordinary input proof and respects the remaining configured budget', async () => {
  const c = client(); const next = vi.fn(async function* () { yield terminal })
  expect(await collect(guarded(c, next, { ...options, purpose: 'compaction' }))).toEqual([terminal])
  expect(c.recordProviderInput).not.toHaveBeenCalled()
  const originalDeadline = new Date(Date.now() + 500).toISOString()
  await collect(guardedModelStream(options, next, c, 'f'.repeat(64), undefined, undefined, async () => scope,
    undefined, undefined, () => originalDeadline))
  expect(c.recordProviderInput.mock.calls[0]![3]).toBe(originalDeadline)
  const remaining = c.recordProviderInput.mock.calls[0]![2]
  expect(remaining).toBeGreaterThan(0)
  expect(remaining).toBeLessThan(1_000)
})

it('unknown SDK finish reasons fail closed instead of reporting completion', async () => {
  const c = client()
  const rows = await collect(guarded(c, async function* () { yield { type: 'finish', reason: { kind: 'unknown-new-kind' } } }))
  expect(rows).toMatchObject([{ reason: { kind: 'error', failure: { code: 'DSH_PROVIDER_FINISH_INVALID' } } }])
  expect(c.recordProviderFinish.mock.calls[0]![0]).toMatchObject({ status: 'error', error_code: 'dsh_provider_finish_invalid' })
  expect(JSON.parse(c.recordProviderFinish.mock.calls[0]![0].finish_json).finish.reason.kind).toBe('unknown-new-kind')
})

it('actual synthesis plugin hook receives exact execution call and fails before the final-review gate', async () => {
  const providerInput = vi.fn().mockRejectedValue(new HubClientError('dsh_provider_emission_identity_mismatch', false))
  const definition = createSynthesisToolDefinition({ reviewContext: async () => context, providerInput })
  const args = { candidate: { schema_version: 'research-synthesis-candidate.v1', request_id: 'research-request:run-test', causal_case: null, horizons: [] } }
  const signal = new AbortController().signal
  await expect(definition.execute(args, { agent: { id: rootId }, callId: 'exact-native-call', signal } as never)).rejects.toThrow('dsh_provider_emission_identity_mismatch')
  expect(providerInput).toHaveBeenCalledWith(context, rootId, 'exact-native-call', args, signal)
})

it.each(['known', 'unknown', 'missing', 'non-json'] as const)('bounded callback error preserves only approved safe codes: %s', async kind => {
  const c = client()
  await collect(guarded(c, async function* () { yield terminal }))
  const input = c.recordProviderInput.mock.calls[0]![0]
  const response = () => kind === 'non-json' ? new Response('private non-json body')
    : Response.json({ detail: kind === 'known' ? 'research_provider_callback_conflict' : 'private content must not escape' }, { status: kind === 'missing' ? 404 : 409 })
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response())
  const realClient = new HubClient({ baseUrl: 'http://hub.test', callbackKey: 'synthetic-only', timeoutMs: 1_000, attempts: 3, ownerId: 'test', fetchImpl })
  await expect(realClient.recordProviderInput(input)).rejects.toThrow(kind === 'known' ? 'research_provider_callback_conflict' : kind === 'non-json' ? 'dsh_provider_input_receipt_mismatch' : 'dsh_provider_callback_refused')
  // Return a fresh Response: actual fetch calls also have independent bodies.
  fetchImpl.mockResolvedValue(response())
  await expect(realClient.providerToolEmission(link.run_id, 1, rootId, tool.block.id)).rejects.toThrow(kind === 'known' ? 'research_provider_callback_conflict' : kind === 'non-json' ? 'dsh_provider_emission_receipt_mismatch' : 'dsh_provider_emission_unavailable')
  expect(fetchImpl).toHaveBeenCalledTimes(2) // attempts=3 cannot repeat the writer
})
