import { describe, expect, it, vi } from 'vitest'
import { readCommandQuiescence } from '../src/host/command-cancellation.js'
import { CurrentHostActivity } from '../src/host/current-activity.js'
import type { ModelAgentLineage, SessionControlFramePublic, SessionControllerPublic, SessionEventLike } from '../src/host/dsh-public.js'
import type { Correlation } from '../src/host/session-correlation.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { projectInspection } from '../src/host/session-result.js'
import { harness } from './lifecycle-harness.js'

// Current official public state seams only; no Provider or historical receipt
// is fabricated. The real scoped-drain SDK tests remain a separate gate.
function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>(release => { resolve = release })
  return { promise, resolve }
}

const ids = deterministicIds('run-owner', 'a'.repeat(64))
const binding = { runId: 'run-owner', requestHash: 'a'.repeat(64), generation: 1, ...ids }
const correlation: Correlation = {
  ...binding, state: 'running', lastSeq: 0, acceptedAt: null, terminalAt: null, errorCode: null,
  modelStepTimeoutMs: 150000, modelStepTimeoutSource: 'submission', modelStepTimeoutTriggered: false,
}

function quietFixture() {
  const app = harness()
  const parent: ModelAgentLineage = { id: ids.sessionId, status: 'idle', session: { header: {} } }
  const child: { id: string; status: 'idle' | 'running'; session: { header: { parentSession: string } } } = {
    id: 'child-one', status: 'idle', session: { header: { parentSession: parent.id } },
  }
  const agents = new Map<string, ModelAgentLineage>([[parent.id, parent], [child.id, child]])
  Object.assign(app.ctx, { agents: { get: (id: string) => agents.get(id) } })
  const baseline: NonNullable<SessionControlFramePublic['value']> = {
    queues: { [parent.id]: [], [child.id]: [], 'foreign-root': ['still queued'] },
    jobs: { [parent.id]: [], [child.id]: [], 'foreign-root': [{ status: 'running' }] },
    projections: { [parent.id]: { asOfSeq: 0 } },
  }
  const list: NonNullable<SessionControllerPublic['list']> = async () => ({ items: [
    { sessionId: parent.id, running: false },
    { sessionId: child.id, parentSessionId: parent.id, running: false },
    { sessionId: 'foreign-root', running: true },
  ] })
  const control: NonNullable<SessionControllerPublic['control']> = async function* () {
    yield { type: 'baseline', value: baseline }
  }
  const inspect: SessionControllerPublic['inspect'] = async id => ({
    meta: { id, ...(id === child.id ? { parentSession: parent.id } : {}) }, events: [],
  })
  Object.assign(app.sessions, { list, control, inspect })
  return { app, parent, child, agents, baseline, inspect }
}

describe('cancellation fresh official quiescence and dispatch fencing', () => {
  it('accepts its quiet lineage without cancelling or hiding another busy Run', async () => {
    const f = quietFixture()
    expect(await readCommandQuiescence(f.app.ctx, f.app.sessions, f.parent.id, new AbortController().signal))
      .toEqual({ parentSeq: 0, lastSeq: 0, descendantIds: [f.child.id] })
    expect(f.app.sessions.cancel).not.toHaveBeenCalled()
    expect(f.app.sessions.create).not.toHaveBeenCalled()
  })

  it.each(['running', 'replacement'] as const)('rejects descendant %s after the baseline while final inspect awaits', async fault => {
    const f = quietFixture()
    const reached = deferred()
    const release = deferred()
    Object.assign(f.app.sessions, { inspect: async (id: string) => {
      reached.resolve()
      await release.promise
      return f.inspect(id)
    } })
    const reading = readCommandQuiescence(f.app.ctx, f.app.sessions, f.parent.id, new AbortController().signal)
    await reached.promise
    if (fault === 'running') f.child.status = 'running'
    else f.agents.set(f.child.id, { ...f.child })
    release.resolve()
    expect(await reading).toBeNull()
  })

  it.each(['queued', 'failed-job', 'stale-cursor'] as const)('cannot certify %s as quiet', async fault => {
    const f = quietFixture()
    if (fault === 'queued') f.baseline.queues = { ...f.baseline.queues, [f.child.id]: ['settlement notice'] }
    if (fault === 'failed-job') f.baseline.jobs = { ...f.baseline.jobs, [f.child.id]: [{ status: 'failed' }] }
    if (fault === 'stale-cursor') Object.assign(f.app.sessions, { inspect: async (id: string) => ({
      meta: { id }, events: [{ seq: 1, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'aborted' } } }],
    }) })
    expect(await readCommandQuiescence(f.app.ctx, f.app.sessions, f.parent.id, new AbortController().signal)).toBeNull()
  })

  it('fences new root and descendant dispatch while retaining an actual in-flight stream count', async () => {
    const f = quietFixture()
    const activity = new CurrentHostActivity(f.app.ctx)
    const release = deferred()
    const provider = vi.fn(async function* () { yield 'started'; await release.promise; yield 'finished' })
    const original = activity.modelStream(f.parent.id, provider)[Symbol.asyncIterator]()
    expect((await original.next()).value).toBe('started')
    activity.cancelCommand(binding)
    expect(activity.hasLineageModelStreams([f.parent.id, f.child.id])).toBe(true)
    const blocked = vi.fn(async function* () { yield 'must never delegate' })
    for (const id of [f.parent.id, f.child.id]) {
      await expect(activity.modelStream(id, blocked)[Symbol.asyncIterator]().next()).rejects.toThrow('host_activity_lineage_fenced')
    }
    expect(blocked).not.toHaveBeenCalled()
    // All purposes share this wrapper. Foreign lineage remains dispatchable.
    const unrelated = activity.modelStream('foreign-root', async function* () { yield 'foreign' })[Symbol.asyncIterator]()
    expect((await unrelated.next()).value).toBe('foreign')
    await unrelated.return?.()
    release.resolve()
    expect((await original.next()).value).toBe('finished')
    await original.return?.()
    expect(activity.hasLineageModelStreams([f.parent.id, f.child.id])).toBe(false)
    expect(provider).toHaveBeenCalledOnce()
  })

  it('keeps accepted-synthesis stop completed but owner cancellation has priority and preserves raw audit', () => {
    const now = Date.now()
    const events: SessionEventLike[] = [
      { seq: 0, type: 'user/message', time: now, data: { source: { kind: 'user', rpcId: ids.requestId } } },
      { seq: 1, type: 'tool/call', time: now + 1, data: { name: 'decision_hub_synthesis_submit', callId: 'synth-1', arguments: '{}' } },
      { seq: 2, type: 'tool/result', time: now + 2, data: { message: { content: [{ type: 'tool-result', toolCallId: 'synth-1', isError: false, content: [{ type: 'text', text: 'accepted' }] }] } } },
      { seq: 3, type: 'turn/end', time: now + 3, data: { reason: { kind: 'aborted' } } },
    ]
    const original = JSON.stringify(events)
    const inspection = { meta: { id: ids.sessionId }, events }
    expect(projectInspection(correlation, inspection, { commandLifecycle: true }).status.state).toBe('completed')
    const pending = projectInspection(correlation, inspection, { commandLifecycle: true, ownerCancellation: { pending: true } })
    expect(pending.status.error_code).toBe('host_cancel_pending')
    expect(pending.completion).toBeNull()
    const observedAt = new Date(now + 4).toISOString()
    const cancelled = projectInspection(correlation, inspection, { commandLifecycle: true, ownerCancellation: { pending: false, observedAt } })
    expect(cancelled.completion).toMatchObject({ terminal_status: 'cancelled', completed_at: observedAt })
    expect(JSON.stringify(events)).toBe(original)
  })

  it('does not promote an unproven raw aborted command to cancelled', () => {
    const events: SessionEventLike[] = [
      { seq: 0, type: 'user/message', time: Date.now(), data: { source: { kind: 'user', rpcId: ids.requestId } } },
      { seq: 1, type: 'turn/end', time: Date.now(), data: { reason: { kind: 'aborted' } } },
    ]
    const value = projectInspection(correlation, { meta: { id: ids.sessionId }, events }, { commandLifecycle: true })
    expect(value.status.error_code).toBe('host_command_activity_pending')
    expect(value.completion).toBeNull()
    expect(value.result).toBeNull()
  })

  it('settles a drained owner cancellation without waiting for a new parent turn or consumed child notice', () => {
    const now = Date.now()
    const events: SessionEventLike[] = [
      { seq: 0, type: 'user/message', time: now, data: { source: { kind: 'user', rpcId: ids.requestId } } },
      { seq: 1, type: 'tool/call', time: now + 1, data: { name: 'subagent', callId: 'child-call', arguments: '{}' } },
      { seq: 2, type: 'tool/result', time: now + 2, data: { message: { content: [{ type: 'tool-result', toolCallId: 'child-call', isError: false, content: [{ type: 'text', text: 'started subagent child-one' }] }] } } },
      { seq: 3, type: 'turn/end', time: now + 3, data: { reason: { kind: 'completed' } } },
    ]
    const inspection = { meta: { id: ids.sessionId }, events }
    expect(projectInspection(correlation, inspection, { commandLifecycle: true }).completion).toBeNull()
    const value = projectInspection(correlation, inspection, { commandLifecycle: true,
      ownerCancellation: { pending: false, observedAt: new Date(now + 4).toISOString() } })
    expect(value.status.state).toBe('cancelled')
    expect(value.completion?.terminal_status).toBe('cancelled')
    expect(value.result).toBeNull()
  })
})
