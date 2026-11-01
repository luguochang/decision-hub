import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import * as ts from 'typescript'
import { expect, it, vi } from 'vitest'
import { SkillHostCoordinator } from '../src/host/skill-host.js'
import { taskSkillCapability } from '../src/host/task-skill-visibility.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { providerCanonicalJson, prepareProviderInput } from '../src/host/provider-input.js'
import { makeSelectedSkillMessage, parseSelectedSkillDelivery, selectedSkillMessageIndex,
  selectedSkillProofFromProviderInput, replaceHistoricalSelectedSkillMessage } from '../src/host/selected-skill-delivery.js'
import { DECISION_SYNTHESIS_REF } from '../src/synthesis-protocol.js'
import type { HubClient } from '../src/host/hub-client.js'

const root = `dsh_${'a'.repeat(64)}`, hash = 'b'.repeat(64), at = '2026-10-16T00:00:00Z'
const identity = { runId: 'run_private', generation: 1, requestHash: hash, rootSessionId: root, executionSessionId: root }
const command = { runId: identity.runId, generation: 1, requestHash: hash, sessionId: root, requestId: 'private-command' }
const request = { execution_mode: 'live', research_mode: 'current_decision', output_schema_ref: DECISION_SYNTHESIS_REF }
const deadline = new Date(Date.now() + 3_600_000).toISOString()
const digest = (body: string) => createHash('sha256').update(body).digest('hex')
function envelope() {
  const body = '# Exact selected method\nUse admitted originals and causal counterevidence.'
  return { schema_version: 'research-selected-skill-delivery.v1', delivery_mode: 'selected_package_provider_input.v1',
    run_id: identity.runId, generation: 1, request_hash: hash, root_session_id: root,
    task_request_hash: 'c'.repeat(64), method_binding_hash: 'd'.repeat(64),
    selection_ref: { output_id: 'selected_private', role: 'research_skill_selection', schema_version: 'research-skill-selection.v3', payload_hash: hash, recorded_at: at },
    selected_event_ref: { event_id: 'selected_event_private', payload_hash: hash },
    delivery: { schema_version: 'research-skill-load.v2', status: 'delivered', use_id: 'use_private', skill_version_id: 'skill_private',
      version_hash: hash, package_hash: hash, workflow_hash: hash, compiled_plan_hash: hash,
      method_body: body, method_body_hash: digest(body),
      next_tool_call: { tool_name: 'decision_hub_research', node_id: 'spot_evidence', arguments: {
        capability_id: 'spot_market_facts', requirement_id: 'spot', query: 'BTC', target_url: null,
        symbols: ['BTC-USDT'], fields: ['last_price'], allowed_domains: [], max_results: 1, max_cost_usd: null } },
      instructions: 'First call the named ordinary tool with these exact business arguments, then bind its actual call ID through skill execute_step. Do not add fields or raise max_results. Additional analysis must use a separate ordinary research call. Delivery alone does not prove loaded, executed, consumed, or financial value.',
    } }
}
function latch() { let release!: () => void; return { promise: new Promise<void>(r => { release = r }), release: () => release() } }
async function collect(stream: AsyncIterable<unknown>) { const rows = []; for await (const row of stream) rows.push(row); return rows }

/** Exact Bridge pre-step seam, with controlled public readers and no constructor/service. */
function bridge(coordinator: SkillHostCoordinator, selected: unknown) {
  const wire = readFileSync(new URL('../src/host/bridge.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('bridge.ts', wire, ts.ScriptTarget.Latest, true)
  const declaration = tree.statements.find(node => ts.isClassDeclaration(node)
    && node.members.some(member => ts.isMethodDeclaration(member) && member.name.getText(tree) === 'selectedSkillPreStep')) as ts.ClassDeclaration
  const method = declaration.members.find(member => ts.isMethodDeclaration(member)
    && member.name.getText(tree) === 'selectedSkillPreStep')!.getText(tree)
  const js = ts.transpileModule(`class BridgeSeam { ${method} }; return BridgeSeam`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText
  const Type = new Function('taskSkillCapability', 'selectedSkillMessageIndex', 'makeSelectedSkillMessage', 'replaceHistoricalSelectedSkillMessage', js)(
    taskSkillCapability, selectedSkillMessageIndex, makeSelectedSkillMessage, replaceHistoricalSelectedSkillMessage)
  const result = new Type()
  result.skills = coordinator; result.hub = { selectedSkillDelivery: vi.fn(async () => selected) }; result.ctx = {}
  return result
}
function coordinator(record = vi.fn(async () => undefined)) {
  const tool = vi.fn(async (_scope: unknown, _call: string, args: { operation: string }) => args.operation === 'load' ? envelope().delivery : { status: 'selected' })
  return { value: new SkillHostCoordinator({ tool, record }, async () => identity), record, tool }
}
const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
  started_at: at, hub_base_url: 'http://private.test', workspace_id: 'private', workspace_cwd_hash: hash }
function providerHub(input: (value: any) => Promise<any>) {
  // This controlled public boundary supplies only fields read by the managed
  // wrapper; it is not a fixture for full Hub link/original authentication.
  return { linkBySession: vi.fn(async () => ({ run_id: identity.runId, dsh_session_id: root, generation: 1, request_hash: hash, deadline_at: deadline } as Awaited<ReturnType<HubClient['linkBySession']>>)),
    bindModel: vi.fn(async () => undefined), recordProviderInput: input,
    recordProviderToolEmission: vi.fn(async () => { throw new Error('unexpected emission') }),
    recordProviderFinish: vi.fn(async (p: any) => ({ schema_version: 'research-provider-finish-recorded.v1', dispatch_id: p.dispatch_id,
      input_ref: p.input_ref, finish_hash: p.finish_hash, status: p.status, known_at: at,
      reference: { output_id: 'finish_private', role: 'model_finish:private', schema_version: 'research-provider-finish.v1', payload_hash: hash, recorded_at: at }, proof_scope: 'dsh_llm_generate_options' } as const)) }
}
function inputAck(p: any) {
  return { schema_version: 'research-provider-input-recorded.v1', dispatch_id: p.dispatch_id, input_hash: p.input_hash,
    reference: { output_id: 'input_private', role: 'model_input:private', schema_version: 'research-provider-input.v1', payload_hash: hash, recorded_at: at },
    known_at: at, producer_authenticated: true, proof_scope: 'dsh_llm_generate_options' }
}

it('actual pre-step and managed input deliver the selected method before the first model, with no list/load round trip', async () => {
  const inputEntered = latch(), inputReply = latch(), loadedEntered = latch(), loadedReply = latch(), order: string[] = []
  const c = coordinator(vi.fn(async (_identity: unknown, record: any) => {
    order.push('loaded'); expect(record.stage).toBe('loaded'); expect(record.proof.kind).toBe('selected_skill_provider_input.v1')
    expect(record.proof).not.toHaveProperty('call_id'); expect(record.proof).not.toHaveProperty('emission')
    loadedEntered.release(); await loadedReply.promise
  }))
  const b = bridge(c.value, envelope()), agent = { id: root, session: { deriveMessages: () => [] } }, signal = new AbortController().signal
  const step = await b.selectedSkillPreStep({ kind: 'enter', messages: [] }, request, agent, command, signal, deadline)
  expect(step.messages).toHaveLength(1); expect(c.record).not.toHaveBeenCalled(); expect(c.tool).not.toHaveBeenCalled()
  const hub = providerHub(async p => { order.push('input'); inputEntered.release(); await inputReply.promise; return inputAck(p) })
  const next = vi.fn(async function* () { order.push('provider'); yield { type: 'finish', reason: { kind: 'stop' } } })
  const pending = collect(guardedModelStream({ sessionId: root, provider: 'private', model: 'private', messages: step.messages, tools: [], signal },
    next, hub, hash, undefined, undefined, async () => scope, undefined,
    (input, current) => c.value.afterRecordedProviderInput(input, current), () => deadline))
  await inputEntered.promise; expect(c.record).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled()
  inputReply.release(); await loadedEntered.promise
  expect(next).not.toHaveBeenCalled(); loadedReply.release(); await pending
  expect(order).toEqual(['input', 'loaded', 'provider']); expect(next).toHaveBeenCalledOnce(); expect(c.tool).not.toHaveBeenCalled()
})

it('a selected envelope is unique, exact, root-scoped and reused without duplicate SDK messages', async () => {
  const c = coordinator(), b = bridge(c.value, envelope()), signal = new AbortController().signal
  const first = await b.selectedSkillPreStep({ kind: 'enter', messages: [] }, request, { id: root, session: { deriveMessages: () => [] } }, command, signal, deadline)
  const second = await b.selectedSkillPreStep({ kind: 'enter', messages: [] }, request, { id: root, session: { deriveMessages: () => first.messages } }, command, signal, deadline)
  expect(second.messages).toEqual([]); expect(b.hub.selectedSkillDelivery).toHaveBeenCalledOnce()
  const delivery = parseSelectedSkillDelivery(envelope(), identity)
  expect(() => selectedSkillMessageIndex([...first.messages, ...first.messages], delivery)).toThrow('skill_selected_delivery_invalid')
  const changed = structuredClone(first.messages[0]); changed.source = { kind: 'user' }
  expect(() => selectedSkillMessageIndex([changed], delivery)).toThrow('skill_selected_delivery_invalid')
  expect(() => parseSelectedSkillDelivery(envelope(), { ...identity, executionSessionId: 'child_private' })).toThrow('skill_selected_delivery_invalid')
})

it('only an explicit authenticated legacy response retains list/load; an unknown reader failure never downgrades', async () => {
  const e = envelope(), legacy = { schema_version: 'research-selected-skill-delivery-mode.v1', delivery_mode: 'legacy-tool',
    run_id: identity.runId, generation: 1, request_hash: hash, root_session_id: root,
    selection_ref: e.selection_ref, selected_event_ref: e.selected_event_ref }
  const c = coordinator(), b = bridge(c.value, legacy), signal = new AbortController().signal, initial = { kind: 'enter', messages: [] }
  expect(await b.selectedSkillPreStep(initial, request, { id: root }, command, signal, deadline)).toBe(initial)
  await c.value.execute({ operation: 'list' }, { agent: {}, callId: 'native-list', signal })
  await c.value.execute({ operation: 'load', skill_version_id: 'skill_private' }, { agent: {}, callId: 'native-load', signal })
  expect(c.tool.mock.calls.map(call => call[2].operation)).toEqual(['list', 'load']); expect(c.record).not.toHaveBeenCalled()
  const failed = bridge(coordinator().value, null); failed.hub.selectedSkillDelivery.mockRejectedValue(new Error('private-reader-failed'))
  await expect(failed.selectedSkillPreStep(initial, request, { id: root }, command, signal, deadline)).rejects.toThrow('private-reader-failed')
})

it('an unrecorded or changed Provider input cannot create loaded, while the actual acknowledged input can', async () => {
  const c = coordinator(), delivery = c.value.rememberSelectedDelivery(identity, envelope()), message = makeSelectedSkillMessage(delivery)
  expect(c.record).not.toHaveBeenCalled()
  const binding = { schema_version: 'dsh-model-dispatch-binding.v1', run_id: identity.runId, dsh_session_id: root,
    generation: 1, request_hash: hash, plugin_build_hash: hash, purpose: 'conversation', execution_session_id: null,
    parent_session_id: null, provider: 'private', model: 'private', reasoning_effort: null, temperature: null, max_tokens: null, stop_sequences_hash: null } as const
  const hub = providerHub(async p => inputAck(p))
  const prepared = await prepareProviderInput(hub, binding, { sessionId: root, provider: 'private', model: 'private', messages: [message], tools: [] }, scope, undefined, 60_000, deadline, deadline)
  const forged = { ...prepared, receipt: { ...prepared.receipt, producer_authenticated: false } }
  expect(() => selectedSkillProofFromProviderInput(forged as typeof prepared, delivery)).toThrow('skill_selected_delivery_invalid')
  await expect(c.value.afterRecordedProviderInput({ ...prepared, inputJson: providerCanonicalJson({ messages: [], system: null, tools: [] }) }, new AbortController().signal)).rejects.toThrow('skill_selected_delivery_invalid')
  expect(c.record).not.toHaveBeenCalled()
  await c.value.afterRecordedProviderInput(prepared, new AbortController().signal)
  await c.value.afterRecordedProviderInput(prepared, new AbortController().signal)
  expect(c.record).toHaveBeenCalledOnce()
})

it('repair replaces only the exact older native surface, preserving history and requiring a new actual input', async () => {
  const previous = parseSelectedSkillDelivery(envelope(), identity), old = makeSelectedSkillMessage(previous)
  const currentIdentity = { ...identity, generation: 2 }, next = { ...envelope(), generation: 2,
    selection_ref: { ...envelope().selection_ref, output_id: 'repair_private', schema_version: 'research-skill-generation-binding.v2' },
    selected_event_ref: { event_id: 'repair_event_private', payload_hash: hash } }
  const c = coordinator(), b = bridge(c.value, next), signal = new AbortController().signal
  const original = { type: 'user/message', seq: 4, time: 1, data: old }
  const events: any[] = [original]; let surface: any[] = [old]
  const session = { id: root, header: {}, events, deriveMessages: () => surface,
    append: vi.fn((type: string, data: any, options?: any) => {
      const event = { type, seq: events.length + 4, time: 2, data }; events.push(event)
      if (type === 'user/message') {
        expect(options).toEqual({ surfaceOp: { op: 'replace', start: 4, end: 4 }, sourceEventSeqs: [4] })
        surface = [data]
      }
      return event
    }) }
  b.ctx = { get: () => ({ estimateMessage: () => 20 }) }
  const decision = await b.selectedSkillPreStep({ kind: 'enter', messages: [] }, request, { id: root, session },
    { ...command, generation: 2 }, signal, deadline)
  expect(decision.messages).toEqual([]); expect(session.append).toHaveBeenCalledTimes(2)
  expect(events[0]).toBe(original); expect(events[0].data).toBe(old)
  const delivered = parseSelectedSkillDelivery(next, currentIdentity)
  expect(selectedSkillMessageIndex(surface, delivered)).toBe(0)
  expect(c.record).not.toHaveBeenCalled()
  expect(() => selectedSkillMessageIndex([old], delivered)).toThrow('skill_selected_delivery_invalid')
  expect(() => replaceHistoricalSelectedSkillMessage(delivered, { ...session, deriveMessages: () => [old] }, undefined)).toThrow('skill_selected_delivery_invalid')
  const changed = { ...old, content: [{ type: 'text' as const, text: providerCanonicalJson({ ...next, generation: 2 }) }] }
  expect(() => replaceHistoricalSelectedSkillMessage(delivered, { ...session, deriveMessages: () => [changed] }, { estimateMessage: () => 20 })).toThrow('skill_selected_delivery_invalid')
  const binding = { schema_version: 'dsh-model-dispatch-binding.v1', run_id: identity.runId, dsh_session_id: root,
    generation: 2, request_hash: hash, plugin_build_hash: hash, purpose: 'conversation', execution_session_id: null,
    parent_session_id: null, provider: 'private', model: 'private', reasoning_effort: null, temperature: null,
    max_tokens: null, stop_sequences_hash: null } as const
  const hub = providerHub(async p => inputAck(p))
  const oldInput = await prepareProviderInput(hub, binding,
    { sessionId: root, provider: 'private', model: 'private', messages: [old], tools: [] }, scope, undefined, 60_000, deadline, deadline)
  await expect(c.value.afterRecordedProviderInput(oldInput, signal)).rejects.toThrow('skill_selected_delivery_invalid')
  expect(c.record).not.toHaveBeenCalled()
  const newInput = await prepareProviderInput(hub, binding,
    { sessionId: root, provider: 'private', model: 'private', messages: surface, tools: [] }, scope, undefined, 60_000, deadline, deadline)
  await c.value.afterRecordedProviderInput(newInput, signal)
  expect(c.record).toHaveBeenCalledOnce()
})
