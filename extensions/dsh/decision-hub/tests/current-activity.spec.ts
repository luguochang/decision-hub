import { createHash } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CurrentHostActivity, hostActivityRequestSchema, type HostActivityBinding, type HostActivityScope } from '../src/host/current-activity.js'
import type { HostContextPublic, SessionControlFramePublic, SessionSummaryPublic } from '../src/host/dsh-public.js'
import { deterministicIds } from '../src/host/session-correlation.js'

const binding: HostActivityBinding = {
  run_id: 'run-old', study_id: 'study-one', generation: 1, request_hash: 'a'.repeat(64),
  dsh_session_id: deterministicIds('run-old', 'a'.repeat(64)).sessionId,
  link_fingerprint: 'b'.repeat(64),
}
const scope: HostActivityScope = {
  host_instance_id: '28187b92-576d-4bea-84c2-0868c59a920a', process_id: 123,
  started_at: '2026-10-03T00:00:00.000Z', hub_base_url: 'http://hub.test',
  workspace_id: 'workspace-one', workspace_cwd_hash: 'c'.repeat(64),
}

function fixture(items: SessionSummaryPublic[] = [], baseline = { queues: {}, jobs: {} } as NonNullable<SessionControlFramePublic['value']>) {
  const released = vi.fn()
  const agents = new Map<string, { id: string; session: { header: { parentSession?: string } } }>()
  const controller = {
    create: vi.fn(), prompt: vi.fn(), cancel: vi.fn(), inspect: vi.fn(),
    list: vi.fn(async () => ({ items })),
    control: vi.fn(async function* (): AsyncIterable<SessionControlFramePublic> {
      try { yield { type: 'baseline', value: baseline } } finally { released() }
    }),
  }
  const ctx = {
    agents: { get: (id: string) => agents.get(id) }, sessionController: controller,
    webServer: { register: vi.fn(() => () => {}) },
    connection: { fetch: { register: vi.fn(() => async () => {}) } },
    workspaceRegistry: { resolveByPath: vi.fn() },
    on: vi.fn(() => () => {}), logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  } as HostContextPublic
  const activity = new CurrentHostActivity(ctx)
  const verify = vi.fn(async () => ({ businessStatus: 'failed' }))
  return { activity, controller, verify, released, agents }
}

afterEach(() => vi.useRealTimers())

describe('current Host activity proof', () => {
  it('fences a verified terminal Run and records current absence without minting a terminal', async () => {
    const f = fixture()
    const result = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    expect(result).toMatchObject({ census_complete: true, records: [{
      ...binding, state: 'current_scope_inactive', presence: 'absent', running: false,
      queue_count: 0, active_job_count: 0, terminal_state: 'unknown',
    }] })
    expect(result.records[0]!.fence_id).toBeTruthy()
    expect(Date.parse(result.expires_at) - Date.parse(result.observed_at)).toBe(30_000)
    expect(f.released).toHaveBeenCalledOnce()
    expect(f.controller.create).not.toHaveBeenCalled()
    expect(f.controller.prompt).not.toHaveBeenCalled()
    expect(f.controller.cancel).not.toHaveBeenCalled()
    expect(f.controller.inspect).not.toHaveBeenCalled()
  })

  it('retains the fence id when an exact official cancellation changes only the Link fingerprint', async () => {
    const f = fixture()
    const before = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    const fenceId = before.records[0]!.fence_id
    const expected = deterministicIds(binding.run_id, binding.request_hash, binding.generation)
    f.activity.cancelCommand({
      runId: binding.run_id, sessionId: binding.dsh_session_id, requestId: expected.requestId,
      requestHash: binding.request_hash, generation: binding.generation,
    })
    const postCancel = { ...binding, link_fingerprint: 'd'.repeat(64) }
    const after = await f.activity.snapshot(scope, [postCancel], f.verify, 1000)
    expect(after.records[0]).toMatchObject({
      ...postCancel, state: 'current_scope_inactive', reason_code: 'host_activity_lineage_quiescent',
      fence_id: fenceId,
    })
    expect(fenceId).toBeTruthy()
  })

  it('rejects a Link fingerprint rebind without the exact official cancellation binding', async () => {
    const f = fixture()
    const before = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    const changed = { ...binding, link_fingerprint: 'd'.repeat(64) }
    const after = await f.activity.snapshot(scope, [changed], f.verify, 1000)
    expect(after.records[0]).toMatchObject({
      ...changed, state: 'unknown', reason_code: 'host_activity_fence_binding_changed',
      fence_id: before.records[0]!.fence_id,
    })
  })

  it('rejects any stable identity change even after the exact official cancellation', async () => {
    const f = fixture()
    const before = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    const expected = deterministicIds(binding.run_id, binding.request_hash, binding.generation)
    f.activity.cancelCommand({
      runId: binding.run_id, sessionId: binding.dsh_session_id, requestId: expected.requestId,
      requestHash: binding.request_hash, generation: binding.generation,
    })
    const changed = { ...binding, study_id: 'study-two', link_fingerprint: 'd'.repeat(64) }
    const after = await f.activity.snapshot(scope, [changed], f.verify, 1000)
    expect(after.records[0]).toMatchObject({
      ...changed, state: 'unknown', reason_code: 'host_activity_fence_binding_changed',
      fence_id: before.records[0]!.fence_id,
    })
  })

  it.each(['running', 'queue', 'job'] as const)('keeps %s activity in a descendant visible and does not cancel it', async kind => {
    const child = 'child-one'
    const f = fixture([
      { sessionId: binding.dsh_session_id, running: false },
      { sessionId: child, running: kind === 'running', parentSessionId: binding.dsh_session_id },
    ], {
      queues: { [binding.dsh_session_id]: [], [child]: kind === 'queue' ? [{ placement: 'context' }] : [] },
      jobs: { [binding.dsh_session_id]: [], [child]: [{ status: kind === 'job' ? 'stopping' : 'completed' }] },
    })
    const result = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    expect(result.records[0]).toMatchObject({ state: 'active', descendant_ids: [child], terminal_state: 'unknown' })
    expect(f.activity.isFenced(child)).toBe(true)
    expect(f.controller.cancel).not.toHaveBeenCalled()
  })

  it.each(['active-run', 'read-failure', 'wrong-session'] as const)('does not install a fence when original verification is %s', async kind => {
    const f = fixture()
    if (kind === 'active-run') f.verify.mockResolvedValue({ businessStatus: 'researching' })
    if (kind === 'read-failure') f.verify.mockRejectedValue(new Error('unreachable'))
    const target = kind === 'wrong-session' ? { ...binding, dsh_session_id: 'wrong' } : binding
    const result = await f.activity.snapshot(scope, [target], f.verify, 1000)
    expect(result.records[0]).toMatchObject({ state: 'unknown', fence_id: null, running: null })
    expect(f.activity.isFenced(target.dsh_session_id)).toBe(false)
  })

  it.each(['list-error', 'missing-control-row', 'orphan'] as const)('does not turn %s into inactive', async kind => {
    const f = kind === 'orphan' ? fixture([{ sessionId: 'child', parentSessionId: 'missing-parent', running: false }])
      : kind === 'missing-control-row' ? fixture([], { queues: { ghost: [] }, jobs: { ghost: [] } }) : fixture()
    if (kind === 'list-error') f.controller.list.mockRejectedValue(new Error('storage failed'))
    const result = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    expect(result).toMatchObject({ census_complete: false, records: [{ state: 'unknown', running: null, terminal_state: 'unknown' }] })
    expect(result.records[0]!.fence_id).toBeTruthy()
  })

  it('bounds an unresponsive official read without returning inactive', async () => {
    vi.useFakeTimers()
    const f = fixture()
    f.controller.list.mockImplementation(() => new Promise(() => {}))
    const pending = f.activity.snapshot(scope, [binding], f.verify, 20)
    await vi.advanceTimersByTimeAsync(21)
    expect(await pending).toMatchObject({ census_complete: false, records: [{ state: 'unknown' }] })
  })

  it('blocks newly attached descendants and provider delegation after a fence is installed', async () => {
    const f = fixture()
    await f.activity.snapshot(scope, [binding], f.verify, 1000)
    f.agents.set('new-child', { id: 'new-child', session: { header: { parentSession: binding.dsh_session_id } } })
    const next = vi.fn(async function* () { yield 'provider' })
    await expect(f.activity.modelStream('new-child', next)[Symbol.asyncIterator]().next()).rejects.toThrow('host_activity_lineage_fenced')
    expect(next).not.toHaveBeenCalled()
  })

  it('keeps an already delegated model stream active without cancelling it', async () => {
    const f = fixture()
    let release!: () => void
    const blocked = new Promise<void>(resolve => { release = resolve })
    const stream = f.activity.modelStream(binding.dsh_session_id, async function* () { await blocked; yield 1 })
    const iterator = stream[Symbol.asyncIterator]()
    const first = iterator.next()
    const snapshot = await f.activity.snapshot(scope, [binding], f.verify, 1000)
    expect(snapshot.records[0]).toMatchObject({ state: 'active', running: true })
    release()
    expect(await first).toEqual({ done: false, value: 1 })
    await iterator.return?.()
  })

  it('uses canonical cwd hashing and a distinct nonce for each Bridge instance', async () => {
    const first = fixture()
    const a = await first.activity.scope('http://hub.test/', process.cwd(), 'workspace-one')
    const b = await fixture().activity.scope('http://hub.test/', process.cwd(), 'workspace-one')
    const canonical = (await realpath(process.cwd())).replace(/\\/g, '/').toLowerCase()
    expect(a.workspace_cwd_hash).toBe(createHash('sha256').update(canonical).digest('hex'))
    expect(a.host_instance_id).not.toBe(b.host_instance_id)
    expect(a.process_id).toBe(process.pid)
  })

  it('rejects duplicate targets, unknown versions, and overlong batches', () => {
    const request = { schema_version: 'dsh-host-activity-request.v1', records: [binding] }
    expect(hostActivityRequestSchema.safeParse(request).success).toBe(true)
    expect(hostActivityRequestSchema.safeParse({ ...request, records: [binding, binding] }).success).toBe(false)
    expect(hostActivityRequestSchema.safeParse({ ...request, schema_version: 'v2' }).success).toBe(false)
    expect(hostActivityRequestSchema.safeParse({ ...request, records: Array.from({ length: 65 }, () => binding) }).success).toBe(false)
  })
})
