import { afterEach, expect, it, vi } from 'vitest'
import { TerminalQuiescence, type TerminalAuthority } from '../src/host/terminal-quiescence.js'
import { HubClient } from '../src/host/hub-client.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import type { HostContextPublic } from '../src/host/dsh-public.js'

const now = Date.parse('2026-10-16T06:00:00Z'), hash = 'a'.repeat(64)
const binding = { runId: 'run_private', requestHash: hash, generation: 1,
  ...deterministicIds('run_private', hash, 1) }
const deadline = new Date(now + 120_000).toISOString()
function latch() { let release!: () => void; return { promise: new Promise<void>(r => { release = r }), release: () => release() } }
function proof(): TerminalAuthority { return { schema_version: 'dsh-terminal-queue-authority.internal.v1',
  purpose: 'before_failed_terminal', run_id: binding.runId, dsh_session_id: binding.sessionId,
  generation: 1, request_hash: hash, original_hash: 'b'.repeat(64), terminal_hash: null,
  hub_status: 'running', lease_owner: 'private-owner', lease_expires_at: deadline,
  native_state: 'idle', terminal_at: null, observed_at: new Date().toISOString() } }
function context() {
  const cancel = vi.fn(), parent = { id: binding.sessionId, status: 'idle', cancel, whenIdle: async () => undefined }
  const ctx = { agents: { get: (id: string) => id === parent.id ? parent : undefined },
    get: (name: string) => name === 'subagents' ? { drainContinuableDescendants: async () => undefined }
      : { list: () => [], kill: vi.fn(), wait: vi.fn() },
    sessionController: { list: async () => ({ items: [{ sessionId: parent.id, running: false }] }),
      control: async function* () { yield { type: 'baseline', value: { queues: { [parent.id]: [] },
        jobs: { [parent.id]: [] }, projections: { [parent.id]: { asOfSeq: 8 } } } } },
      inspect: async () => ({ meta: { id: parent.id }, events: [{ seq: 8 }] }) },
  } as unknown as HostContextPublic
  return { ctx, cancel }
}
afterEach(() => vi.useRealTimers())
function freeze() { vi.useFakeTimers(); vi.setSystemTime(now) }

it('the actual failure drain retains one original Task deadline beyond the old thirty-second cap', async () => {
  freeze()
  const { ctx } = context(), entered = latch(), release = latch(), deadlines: unknown[] = []
  const authority = vi.fn(async (_run: string, _signal?: AbortSignal, limit?: string) => {
    deadlines.push(limit); if (deadlines.length === 1) { entered.release(); await release.promise }; return proof()
  })
  const drain = new TerminalQuiescence(ctx, authority, () => true, () => false)
  let settled = false
  const pending = drain.drain(binding, 'before_failed_terminal', 30_000, undefined, deadline).finally(() => { settled = true })
  await entered.promise; await vi.advanceTimersByTimeAsync(35_000)
  expect(settled).toBe(false); expect(authority).toHaveBeenCalledOnce()
  release.release()
  expect(await pending).toMatchObject({ quiet: true, last_seq: 8, run_id: binding.runId })
  expect(deadlines).toEqual([deadline, deadline, deadline, deadline])
})

it('matching owner cancel cooperatively waits for its original authority response without aborting HTTP', async () => {
  freeze()
  const { ctx, cancel } = context(), entered = latch(), release = latch()
  let signal!: AbortSignal, settled = false
  const authority = vi.fn(async (_run: string, current?: AbortSignal) => {
    signal = current!; entered.release(); await release.promise; return proof()
  })
  const drain = new TerminalQuiescence(ctx, authority, () => true, () => false)
  const pending = drain.drain(binding, 'before_failed_terminal', 30_000, undefined, deadline)
    .then(() => 'unexpected success', error => error.message).finally(() => { settled = true })
  await entered.promise
  drain.requestCancel({ ...binding, generation: 2 }); expect(signal.aborted).toBe(false)
  drain.requestCancel(binding); expect(signal.aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(4_000)
  expect(settled).toBe(false); expect(cancel).not.toHaveBeenCalled(); expect(authority).toHaveBeenCalledOnce()
  release.release(); expect(await pending).toBe('host_terminal_drain_authority_changed')
})

it('acknowledged historical cleanup retains its old bound and never consumes a Task extension', async () => {
  freeze()
  const { ctx } = context(), entered = latch(), deadlines: unknown[] = []
  const authority = async (_run: string, signal?: AbortSignal, limit?: string): Promise<TerminalAuthority> => {
    deadlines.push(limit); entered.release()
    return await new Promise((_resolve, reject) => signal!.addEventListener('abort', () => reject(new Error('bounded')), { once: true }))
  }
  const drain = new TerminalQuiescence(ctx, authority, () => true, () => false)
  const pending = drain.drain(binding, 'acknowledged_terminal', 30_000, undefined, deadline).catch(error => error.message)
  await entered.promise; await vi.advanceTimersByTimeAsync(30_000)
  expect(await pending).toBe('bounded'); expect(deadlines).toEqual([undefined])
})

it('Hub terminal authority sends the clamped original deadline once and keeps the legacy call headerless', async () => {
  freeze()
  const entered = latch(), release = latch(), seen: RequestInit[] = []
  const fetchImpl = vi.fn(async (_input: unknown, init?: RequestInit) => {
    seen.push(init!); entered.release(); await release.promise
    return new Response(JSON.stringify(proof()), { status: 200 })
  }) as unknown as typeof fetch
  const client = new HubClient({ baseUrl: 'http://private.test', callbackKey: 'private-fixture', ownerId: 'private',
    timeoutMs: 5_000, attempts: 3, fetchImpl, managedReadDeadline: () => deadline })
  const pending = client.terminalAuthority(binding.runId, undefined, new Date(now + 180_000).toISOString())
  await entered.promise; await vi.advanceTimersByTimeAsync(35_000)
  expect(seen[0].signal!.aborted).toBe(false)
  expect(new Headers(seen[0].headers).get('x-decision-hub-research-proof-deadline-at')).toBe(deadline)
  release.release(); expect((await pending).purpose).toBe('before_failed_terminal'); expect(fetchImpl).toHaveBeenCalledOnce()
  const legacy = client.terminalAuthority(binding.runId)
  expect((await legacy).purpose).toBe('before_failed_terminal')
  expect(new Headers(seen[1].headers).has('x-decision-hub-research-proof-deadline-at')).toBe(false)
})
