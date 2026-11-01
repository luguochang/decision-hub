import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import * as ts from 'typescript'
import { afterEach, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { dshSessionStatusSchema } from '@decision-hub/contracts-ts'
import { ModelInvocationWatchdogs } from '../src/host/model-invocation-watchdog.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { freezeCommandBinding, sameCommandBinding } from '../src/host/command-cancellation.js'
import { HubClientError } from '../src/host/hub-client.js'

const now = Date.parse('2026-10-16T06:00:00Z'), root = `dsh_${'a'.repeat(64)}`
const build = 'c'.repeat(64), at = new Date(now).toISOString()
const options = { sessionId: root, provider: 'synthetic', model: 'synthetic', messages: [], tools: [] }
const ref = { output_id: 'out_input', role: 'input', schema_version: 'input.v1', payload_hash: 'f'.repeat(64), recorded_at: at }
const scope = { host_instance_id: '11111111-1111-1111-1111-111111111111', process_id: 1,
  started_at: at, hub_base_url: 'http://hub.test', workspace_id: 'private', workspace_cwd_hash: 'd'.repeat(64) }
function latch() { let release!: () => void; return { promise: new Promise<void>(r => { release = r }), release: () => release() } }
async function collect(rows: AsyncIterable<unknown>) { const values = []; for await (const value of rows) values.push(value); return values }

// Compile only the exact production methods with controlled public boundaries.
// No Bridge constructor, service, DB, model or HTTP is created by this gate.
function actualWrapper() {
  const wire = readFileSync(new URL('../src/host/bridge.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('actual-bridge.ts', wire, ts.ScriptTarget.Latest, true)
  const declaration = tree.statements.find(node => ts.isClassDeclaration(node) && node.members.some(member =>
    ts.isMethodDeclaration(member) && member.name.getText(tree) === 'modelStream')) as ts.ClassDeclaration
  const methods = ['modelStream', 'onSessionEvent', 'cancel', 'cancelResponseWait', 'pendingCancelStatus', 'stopCancelledCommand', 'clearModelStepTimer'].map(name => declaration.members.find(member =>
    ts.isMethodDeclaration(member) && member.name.getText(tree) === name)!.getText(tree)).join('\n')
  const errorClass = tree.statements.find(node => ts.isClassDeclaration(node) && node.name?.text === 'HostRouteError')!.getText(tree)
  const js = ts.transpileModule(`${errorClass}; class ActualWrapper { ${methods} }; return ActualWrapper`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText
  const Type = new Function('guardedModelStream', 'freezeCommandBinding', 'sameCommandBinding',
    'HubClientError', 'PLUGIN_BUILD_HASH', 'safeErrorMessage', 'consumeHarnessModel', 'z', 'readJsonBody', 'dshSessionStatusSchema', 'createHash', js)(
    guardedModelStream, freezeCommandBinding, sameCommandBinding, HubClientError, build,
    () => 'private-control', async () => undefined, z, async (request: any) => request.command, dshSessionStatusSchema, createHash)
  const wrapper = new Type()
  const correlation = { runId: 'run_test', sessionId: root, requestId: 'request_test', requestHash: 'b'.repeat(64),
    generation: 1, modelStepTimeoutMs: 600_000, deadlineAt: new Date(now + 3_600_000).toISOString(),
    terminalAt: null, modelStepTimeoutTriggered: false, errorCode: null, state: 'running', lastSeq: 203, modelStepTimeoutSource: 'submission' }
  const cancel = vi.fn(async (_input: { sessionId: string }) => undefined)
  const bind = vi.fn(async () => undefined)
  let currentCorrelation: typeof correlation | undefined = correlation
  Object.assign(wrapper, { modelStepTimers: new ModelInvocationWatchdogs(), modelStepOwners: new Map(),
    modelStepEventSeqs: new Map(), modelStepRoots: new Map(), modelStepReleasesByRoot: new Map(),
    correlations: { getRun: () => currentCorrelation, getSession: () => currentCorrelation },
    cancellations: { requested: () => false },
    activity: { beginModelInvocation: () => () => undefined, assertModelNotFenced: async () => undefined,
      assertNotFenced: () => undefined,
      modelStream: (_session: string, next: () => AsyncIterable<unknown>) => next(), scope: async () => scope },
    terminalDrains: { assertModelAdmission: () => undefined, closingForSession: () => false },
    ctx: { sessionController: { cancel }, logger: { info: vi.fn(), warn: vi.fn() } },
    config: { runtimeMode: 'replay', hubBaseUrl: 'http://hub.test', defaultWorkspaceCwd: 'private' },
    resolveWorkspace: async () => ({ id: 'private' }),
    hub: { linkBySession: async () => ({ run_id: correlation.runId, dsh_session_id: root,
      request_hash: correlation.requestHash, generation: 1, deadline_at: correlation.deadlineAt }), bindModel: bind,
      recordProviderInput: async (p: any) => ({ schema_version: 'research-provider-input-recorded.v1', reference: ref,
        dispatch_id: p.dispatch_id, input_hash: p.input_hash, known_at: at,
        producer_authenticated: true, proof_scope: 'dsh_llm_generate_options' }),
      recordProviderToolEmission: async () => { throw new Error('unexpected emission') },
      recordProviderFinish: async (p: any) => ({ schema_version: 'research-provider-finish-recorded.v1', reference: ref,
        input_ref: p.input_ref, dispatch_id: p.dispatch_id, finish_hash: p.finish_hash,
        status: p.status, known_at: at, proof_scope: 'dsh_llm_generate_options' }) },
  })
  wrapper.onSessionEvent(root, { type: 'step/start', seq: 1, time: now, data: {} })
  return { wrapper, cancel, bind, correlation,
    setCorrelation: (value: typeof correlation | undefined) => { currentCorrelation = value } }
}
afterEach(() => vi.useRealTimers())
function freeze() { vi.useFakeTimers(); vi.setSystemTime(now) }

it('actual managed finally closes receipt and iterator before tool wait crosses the Provider deadline', async () => {
  freeze()
  const { wrapper, cancel } = actualWrapper(), finishEntered = latch(), finishRelease = latch(), cleanupEntered = latch(), cleanupRelease = latch()
  const finish = wrapper.hub.recordProviderFinish
  wrapper.hub.recordProviderFinish = async (p: any) => { finishEntered.release(); await finishRelease.promise; return finish(p) }
  const next = vi.fn(async function* () {
    try { yield { type: 'finish', reason: { kind: 'stop' } } }
    finally { cleanupEntered.release(); await cleanupRelease.promise }
  })
  const pending = collect(wrapper.modelStream(options, next))
  await finishEntered.promise
  await vi.advanceTimersByTimeAsync(350_000)
  finishRelease.release(); await cleanupEntered.promise
  await vi.advanceTimersByTimeAsync(200_000)
  expect(cancel).not.toHaveBeenCalled()
  cleanupRelease.release(); await pending
  // The independent asynchronous tool is still progressing after Provider cleanup.
  await vi.advanceTimersByTimeAsync(100_000)
  expect(cancel).not.toHaveBeenCalled(); expect(next).toHaveBeenCalledOnce()
})

it.each(['receipt', 'iterator'] as const)('a finish chunk does not release the timer while owned %s is pending', async phase => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), entered = latch(), close = latch()
  if (phase === 'receipt') {
    const finish = wrapper.hub.recordProviderFinish
    wrapper.hub.recordProviderFinish = async (p: any) => { entered.release(); await close.promise; return finish(p) }
  }
  const next = async function* () {
    try { yield { type: 'finish', reason: { kind: 'stop' } } }
    finally { if (phase === 'iterator') { entered.release(); await close.promise } }
  }
  let done = false
  const pending = collect(wrapper.modelStream(options, next)).finally(() => { done = true })
  await entered.promise; await vi.advanceTimersByTimeAsync(600_000)
  expect(done).toBe(false); expect(cancel).toHaveBeenCalledOnce()
  expect(correlation.modelStepTimeoutTriggered).toBe(true)
  close.release(); await pending
})

it('starts the watchdog only after the input receipt and immediately before the real Provider delegate', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper()
  const receiptEntered = latch(), receiptRelease = latch(), delegateEntered = latch(), delegateRelease = latch()
  const recordInput = wrapper.hub.recordProviderInput
  wrapper.hub.recordProviderInput = async (p: any) => {
    receiptEntered.release(); await receiptRelease.promise; return recordInput(p)
  }
  const pending = collect(wrapper.modelStream(options, async function* () {
    delegateEntered.release(); await delegateRelease.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await receiptEntered.promise
  await vi.advanceTimersByTimeAsync(900_000)
  expect(cancel).not.toHaveBeenCalled()
  expect(correlation.modelStepTimeoutTriggered).toBe(false)
  receiptRelease.release(); await delegateEntered.promise
  await vi.advanceTimersByTimeAsync(599_999)
  expect(cancel).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2)
  expect(cancel).toHaveBeenCalledOnce()
  expect(correlation.modelStepTimeoutTriggered).toBe(true)
  delegateRelease.release(); await pending
})

it('clamps an active Provider watchdog to the immutable Task deadline', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), entered = latch(), close = latch()
  correlation.deadlineAt = new Date(now + 1_000).toISOString()
  const pending = collect(wrapper.modelStream(options, async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await entered.promise
  await vi.advanceTimersByTimeAsync(999); expect(cancel).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2); expect(cancel).toHaveBeenCalledOnce()
  expect(correlation.modelStepTimeoutTriggered).toBe(false)
  expect(correlation.errorCode).toBeNull()
  close.release(); await pending
})

it('classifies an equal Task and Provider deadline as Task expiry without model timeout', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), entered = latch(), close = latch()
  correlation.modelStepTimeoutMs = 1_000
  correlation.deadlineAt = new Date(now + 1_000).toISOString()
  const pending = collect(wrapper.modelStream(options, async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await entered.promise; await vi.advanceTimersByTimeAsync(1_001)
  expect(cancel).toHaveBeenCalledOnce()
  expect(correlation.modelStepTimeoutTriggered).toBe(false)
  expect(correlation.errorCode).toBeNull()
  close.release(); await pending
})

it.each([
  { expiry: 'Task', expected: 'DSH_PROVIDER_INPUT_DEADLINE_ELAPSED', modelMs: 600_000, taskMs: 1, cancelled: false },
  { expiry: 'model', expected: 'DSH_PROVIDER_CANCELLED', modelMs: 1, taskMs: 600_000, cancelled: true },
] as const)('keeps typed $expiry classification when the 1ms boundary crosses during timer acquisition', async scenario => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper()
  correlation.modelStepTimeoutMs = scenario.modelMs
  correlation.deadlineAt = new Date(now + scenario.taskMs).toISOString()
  const timers = wrapper.modelStepTimers as ModelInvocationWatchdogs
  wrapper.modelStepTimers = {
    acquire: (sessionId: string, deadline: number, elapsed: () => void) => {
      vi.setSystemTime(now + 2)
      return timers.acquire(sessionId, deadline, elapsed)
    },
    clearSession: (sessionId: string) => timers.clearSession(sessionId),
  }
  const next = vi.fn(async function* () { yield { type: 'finish', reason: { kind: 'stop' } } })
  const rows = await collect(wrapper.modelStream(options, next))
  expect(next).not.toHaveBeenCalled()
  expect(rows).toMatchObject([{ type: 'finish', reason: { failure: { code: scenario.expected } } }])
  expect(correlation.modelStepTimeoutTriggered).toBe(scenario.cancelled)
  expect(cancel).toHaveBeenCalledTimes(scenario.cancelled ? 1 : 0)
})

it('overlapping invocations on one step perform exactly one timeout transition and cancel', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper()
  const entered1 = latch(), entered2 = latch(), close1 = latch(), close2 = latch()
  const next = (entered: ReturnType<typeof latch>, close: ReturnType<typeof latch>) => async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
  const first = collect(wrapper.modelStream(options, next(entered1, close1)))
  const second = collect(wrapper.modelStream(options, next(entered2, close2)))
  await Promise.all([entered1.promise, entered2.promise])
  await vi.advanceTimersByTimeAsync(600_001)
  expect(cancel).toHaveBeenCalledOnce()
  expect(correlation.modelStepTimeoutTriggered).toBe(true)
  expect(correlation.errorCode).toBe('dsh_model_step_timeout')
  close1.release(); close2.release(); await Promise.all([first, second])
})

it('cancels root and child owners once each at one model deadline while writing the Run error once', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), child = 'child-agent'
  const rootEntered = latch(), childEntered = latch(), rootClose = latch(), childClose = latch()
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: root } } } : undefined }
  wrapper.onSessionEvent(child, { type: 'step/start', seq: 2, time: now, data: {} })
  let errorWrites = 0, storedError: string | null = null
  Object.defineProperty(correlation, 'errorCode', {
    configurable: true, get: () => storedError,
    set: (value: string | null) => { errorWrites++; storedError = value },
  })
  const stream = (entered: ReturnType<typeof latch>, close: ReturnType<typeof latch>) => async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
  const rootPending = collect(wrapper.modelStream(options, stream(rootEntered, rootClose)))
  const childPending = collect(wrapper.modelStream({ ...options, sessionId: child }, stream(childEntered, childClose)))
  await Promise.all([rootEntered.promise, childEntered.promise])
  await vi.advanceTimersByTimeAsync(600_001)
  expect(cancel).toHaveBeenCalledTimes(2)
  expect(cancel.mock.calls.map(([value]) => value.sessionId).sort()).toEqual([child, root].sort())
  expect(correlation.modelStepTimeoutTriggered).toBe(true)
  expect(storedError).toBe('dsh_model_step_timeout')
  expect(errorWrites).toBe(1)
  rootClose.release(); childClose.release(); await Promise.all([rootPending, childPending])
})

it('cancels root and child owners once each at the Task clamp without marking model timeout', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), child = 'child-agent'
  const rootEntered = latch(), childEntered = latch(), rootClose = latch(), childClose = latch()
  correlation.deadlineAt = new Date(now + 1_000).toISOString()
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: root } } } : undefined }
  wrapper.onSessionEvent(child, { type: 'step/start', seq: 2, time: now, data: {} })
  const stream = (entered: ReturnType<typeof latch>, close: ReturnType<typeof latch>) => async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
  const rootPending = collect(wrapper.modelStream(options, stream(rootEntered, rootClose)))
  const childPending = collect(wrapper.modelStream({ ...options, sessionId: child }, stream(childEntered, childClose)))
  await Promise.all([rootEntered.promise, childEntered.promise])
  await vi.advanceTimersByTimeAsync(1_001)
  expect(cancel).toHaveBeenCalledTimes(2)
  expect(cancel.mock.calls.map(([value]) => value.sessionId).sort()).toEqual([child, root].sort())
  expect(correlation.modelStepTimeoutTriggered).toBe(false)
  expect(correlation.errorCode).toBeNull()
  rootClose.release(); childClose.release(); await Promise.all([rootPending, childPending])
})

it('same-step SDK retry keeps one Provider deadline and an older finally cannot clear the newer invocation token', async () => {
  freeze()
  const { wrapper, cancel, bind } = actualWrapper(), entered1 = latch(), close1 = latch(), entered2 = latch(), close2 = latch()
  const next = (entered: ReturnType<typeof latch>, close: ReturnType<typeof latch>) => async function* () {
    try { yield { type: 'finish', reason: { kind: 'stop' } } }
    finally { entered.release(); await close.promise }
  }
  const first = collect(wrapper.modelStream(options, next(entered1, close1)))
  await entered1.promise; await vi.advanceTimersByTimeAsync(100)
  const second = collect(wrapper.modelStream(options, next(entered2, close2)))
  await entered2.promise
  expect(bind.mock.calls.map((row: any) => row[3])).toEqual([
    new Date(now + 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()])
  close1.release(); await first
  await vi.advanceTimersByTimeAsync(599_900)
  expect(cancel).toHaveBeenCalledOnce()
  close2.release(); await second
})

it.each(['deleted', 'replaced'] as const)('fails closed when the command correlation is %s after the receipt', async mode => {
  freeze()
  const state = actualWrapper(), { wrapper, correlation } = state
  const input = wrapper.hub.recordProviderInput
  wrapper.hub.recordProviderInput = async (p: any) => {
    const receipt = await input(p)
    state.setCorrelation(mode === 'deleted' ? undefined : { ...correlation })
    return receipt
  }
  const next = vi.fn(async function* () { yield { type: 'finish', reason: { kind: 'stop' } } })
  const rows = await collect(wrapper.modelStream(options, next))
  expect(next).not.toHaveBeenCalled()
  expect(rows).toMatchObject([{ type: 'finish', reason: { failure: {
    code: 'DSH_MODEL_DISPATCH_IDENTITY_MISMATCH',
  } } }])
})

it.each(['terminal drain', 'activity fence'] as const)('blocks delegation when a %s starts after the receipt', async mode => {
  freeze()
  const { wrapper } = actualWrapper()
  let revoked = false
  wrapper.terminalDrains.closingForSession = () => mode === 'terminal drain' && revoked
  wrapper.activity.assertNotFenced = () => {
    if (mode === 'activity fence' && revoked) throw new Error('host_activity_lineage_fenced')
  }
  const input = wrapper.hub.recordProviderInput
  wrapper.hub.recordProviderInput = async (p: any) => { const receipt = await input(p); revoked = true; return receipt }
  const next = vi.fn(async function* () { yield { type: 'finish', reason: { kind: 'stop' } } })
  const rows = await collect(wrapper.modelStream(options, next))
  expect(next).not.toHaveBeenCalled()
  expect(rows).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_PROVIDER_CANCELLED' } } }])
})

it('revalidates child lineage after the receipt and blocks a reparented child before delegation', async () => {
  freeze()
  const { wrapper } = actualWrapper(), child = 'child-agent'
  let parent = root
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: parent } } } : undefined }
  const input = wrapper.hub.recordProviderInput
  wrapper.hub.recordProviderInput = async (p: any) => { const receipt = await input(p); parent = 'foreign-root'; return receipt }
  const next = vi.fn(async function* () { yield { type: 'finish', reason: { kind: 'stop' } } })
  const rows = await collect(wrapper.modelStream({ ...options, sessionId: child }, next))
  expect(next).not.toHaveBeenCalled()
  expect(rows).toMatchObject([{ type: 'finish', reason: { failure: { code: 'DSH_MODEL_DISPATCH_LINEAGE_INVALID' } } }])
})

it('applies the same Provider watchdog to an authenticated child execution', async () => {
  freeze()
  const { wrapper, cancel, correlation } = actualWrapper(), child = 'child-agent', entered = latch(), close = latch()
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: root } } } : undefined }
  wrapper.onSessionEvent(child, { type: 'step/start', seq: 2, time: now, data: {} })
  const pending = collect(wrapper.modelStream({ ...options, sessionId: child }, async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await entered.promise; await vi.advanceTimersByTimeAsync(600_001)
  expect(cancel).toHaveBeenCalledOnce()
  expect(cancel).toHaveBeenCalledWith({ sessionId: child })
  expect(correlation.modelStepTimeoutTriggered).toBe(true)
  close.release(); await pending
})

it('root revocation releases a child watchdog and prevents a late cancel', async () => {
  freeze()
  const { wrapper, cancel } = actualWrapper(), child = 'child-agent', entered = latch(), close = latch()
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: root } } } : undefined }
  const pending = collect(wrapper.modelStream({ ...options, sessionId: child }, async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await entered.promise
  wrapper.clearModelStepTimer(root)
  await vi.advanceTimersByTimeAsync(600_001)
  expect(cancel).not.toHaveBeenCalled()
  close.release(); await pending
})

it('an old child timer cannot mutate or cancel a replacement generation', async () => {
  freeze()
  const state = actualWrapper(), { wrapper, cancel, correlation } = state
  const child = 'child-agent', entered = latch(), close = latch()
  wrapper.ctx.agents = { get: (id: string) => id === child
    ? { id, session: { header: { parentSession: root } } } : undefined }
  const pending = collect(wrapper.modelStream({ ...options, sessionId: child }, async function* () {
    entered.release(); await close.promise
    yield { type: 'finish', reason: { kind: 'stop' } }
  }))
  await entered.promise
  const replacement = { ...correlation, generation: 2, modelStepTimeoutTriggered: false, errorCode: null }
  state.setCorrelation(replacement)
  await vi.advanceTimersByTimeAsync(600_001)
  expect(cancel).not.toHaveBeenCalled()
  expect(replacement.modelStepTimeoutTriggered).toBe(false)
  close.release(); await pending
})

it('writes only bounded structured Provider stage identities to the Host sink', async () => {
  freeze()
  const { wrapper } = actualWrapper()
  const privateOptions = { ...options, messages: [{ role: 'user', content: [{ type: 'text', text: 'private-prompt' }] }] }
  await collect(wrapper.modelStream(privateOptions, async function* () {
    yield { type: 'finish', reason: { kind: 'stop' }, private: 'private-chunk' }
  }))
  const rows = wrapper.ctx.logger.info.mock.calls.map((call: [string]) => JSON.parse(call[0].split(' ', 2)[1]!))
  expect(rows.map((row: any) => row.stage)).toEqual([
    'receipt_completed', 'delegate_started', 'first_sdk_chunk', 'stream_finished',
  ])
  for (const row of rows) {
    expect(Object.keys(row).sort()).toEqual([
      'at', 'dispatch_id', 'generation', 'input_hash', 'run_id_hash', 'schema_version', 'stage',
    ])
    expect(row.run_id_hash).toMatch(/^[a-f0-9]{64}$/)
    expect(row.input_hash).toMatch(/^[a-f0-9]{64}$/)
  }
  expect(JSON.stringify(wrapper.ctx.logger.info.mock.calls)).not.toContain('private')
})


it('actual Stop joins the same terminal authority/owned drain; four seconds returns pending without a parallel cancel', async () => {
  freeze()
  const { wrapper, correlation } = actualWrapper()
  const abortNotified = latch(), nativeAuthorityClosed = latch(), nativeStopClosed = latch(), stopEntered = latch()
  let requested = false, terminalClosed = false
  wrapper.terminalInFlight = new Map(); wrapper.cancelStops = new Map()
  wrapper.activity.cancelCommand = vi.fn()
  wrapper.terminalDrains.requestCancel = vi.fn(() => abortNotified.release())
  wrapper.cancellations = { begin: vi.fn(() => { requested = true }), requested: () => requested,
    discardUnstarted: vi.fn(), errorCode: () => null,
    stop: vi.fn(async () => { expect(terminalClosed).toBe(true); stopEntered.release(); await nativeStopClosed.promise }) }
  wrapper.hub.link = vi.fn(async () => ({ run_id: correlation.runId, dsh_session_id: root,
    generation: 1, request_hash: correlation.requestHash, terminal_at: null, state: 'idle', last_seq: 206, error_code: 'dsh_model_step_timeout' }))
  wrapper.inspect = vi.fn(async () => ({ status: { current: 'native-quiet' } }))
  wrapper.reconcileSession = vi.fn(async () => undefined)
  const replies: { status: number; body: any }[] = []
  wrapper.json = (_res: unknown, status: number, body: any) => { replies.push({ status, body }) }
  const key = `${correlation.runId}:1:failed`
  const originalTerminal = (async () => {
    await abortNotified.promise
    await nativeAuthorityClosed.promise
    terminalClosed = true
    throw new Error('host_terminal_drain_cancelled')
  })().finally(() => wrapper.terminalInFlight.delete(key))
  wrapper.terminalInFlight.set(key, originalTerminal)
  const request = { command: { generation: 1, reason: 'owner' } }
  const first = wrapper.cancel(correlation.runId, request, {})
  await abortNotified.promise
  const bindingKey = `${correlation.runId}:${root}:1:${correlation.requestHash}`
  const originalStop = wrapper.cancelStops.get(bindingKey)
  expect(originalStop).toBeDefined()
  await vi.advanceTimersByTimeAsync(4_000); await first
  expect(replies[0]).toMatchObject({ status: 202, body: { error_code: 'host_cancel_pending' } })
  const second = wrapper.cancel(correlation.runId, request, {})
  await vi.advanceTimersByTimeAsync(4_000); await second
  expect(wrapper.cancelStops.get(bindingKey)).toBe(originalStop)
  expect(replies[1]).toMatchObject({ status: 202, body: { error_code: 'host_cancel_pending' } })
  expect(wrapper.cancellations.stop).not.toHaveBeenCalled()
  nativeAuthorityClosed.release(); await stopEntered.promise
  expect(wrapper.cancellations.stop).toHaveBeenCalledOnce()
  let stopped = false; void originalStop.then(() => { stopped = true })
  await Promise.resolve(); expect(stopped).toBe(false)
  nativeStopClosed.release(); await originalStop
  expect(stopped).toBe(true); expect(wrapper.cancellations.stop).toHaveBeenCalledOnce()
})
