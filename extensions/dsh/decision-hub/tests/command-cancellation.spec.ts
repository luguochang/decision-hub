import { describe, expect, it, vi } from 'vitest'
import {
  CommandCancellation, type CancellationAgent, type CancellationContext,
  type CancellationJob, type CancellationServices,
} from '../src/host/command-cancellation.js'
import { deterministicIds } from '../src/host/session-correlation.js'

// These are operation/identity controls against the public SDK seam. They do
// not prove a real provider stopped or replace the official forest drain tests.
function setup() {
  const ids = deterministicIds('run-owner', 'a'.repeat(64), 1)
  const binding = { runId: 'run-owner', requestHash: 'a'.repeat(64), generation: 1, ...ids }
  let finishDrain!: () => void
  const drain = new Promise<void>(resolve => { finishDrain = resolve })
  const parent: CancellationAgent = {
    id: binding.sessionId, status: 'idle', cancel: vi.fn(), whenIdle: vi.fn(async () => undefined),
  }
  let registry: CancellationAgent | undefined = parent
  const subagents = { drainContinuableDescendants: vi.fn((_parents: readonly CancellationAgent[]) => drain) }
  let snapshots: CancellationJob[] = []
  const jobs = {
    list: vi.fn(() => snapshots), kill: vi.fn(() => 'requested' as const),
    wait: vi.fn(async (id: string): Promise<CancellationJob> => ({ id, ownerSession: parent.id, status: 'killed' })),
  }
  const services: CancellationServices = { subagents, jobs }
  const ctx: CancellationContext = {
    agents: { get: () => registry },
    get: <K extends keyof CancellationServices>(name: K) => services[name],
  }
  return {
    binding, parent, subagents, jobs, ctx, finishDrain,
    replace: (agent: CancellationAgent | undefined) => { registry = agent },
    setJobs: (values: CancellationJob[]) => { snapshots = values },
  }
}

describe('scoped owner cancellation operation', () => {
  it('passes the exact registry parent and waits for drain before readiness', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    cancel.begin(f.binding)
    expect(cancel.requested(f.binding)).toBe(true)
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    await Promise.resolve()
    expect(f.subagents.drainContinuableDescendants).toHaveBeenCalledWith([f.parent])
    expect(f.subagents.drainContinuableDescendants.mock.calls[0]?.[0]?.[0]).toBe(f.parent)
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
    f.finishDrain()
    await stopping
    expect(f.parent.cancel).toHaveBeenCalledTimes(2)
    expect(f.parent.cancel).toHaveBeenLastCalledWith({ kind: 'user' }, { keepInbox: false })
    expect(f.parent.whenIdle).toHaveBeenCalledOnce()
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(true)
  })

  it('coalesces concurrent stop requests under the original binding', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    const one = cancel.stop(f.ctx, f.binding, 1000)
    const two = cancel.stop(f.ctx, f.binding, 1000)
    await Promise.resolve()
    expect(f.subagents.drainContinuableDescendants).toHaveBeenCalledOnce()
    f.finishDrain()
    await Promise.all([one, two])
  })

  it('rejects an invalid command identity before calling any SDK operation', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    await expect(cancel.stop(f.ctx, { ...f.binding, generation: 2 }, 1000))
      .rejects.toThrow('host_cancel_binding_mismatch')
    expect(f.subagents.drainContinuableDescendants).not.toHaveBeenCalled()
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(f.jobs.kill).not.toHaveBeenCalled()
  })

  it('requires the runtime service capability and never marks a missing one settled', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    await expect(cancel.stop({ agents: f.ctx.agents }, f.binding, 1000))
      .rejects.toThrow('host_cancel_capability_unavailable')
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
  })

  it('does not adopt a replacement same-id Agent while draining', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    await Promise.resolve()
    f.replace({ ...f.parent })
    f.finishDrain()
    await expect(stopping).rejects.toThrow('host_cancel_owner_changed')
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
  })

  it('does not turn a timed-out waiter into a successful cancellation', async () => {
    vi.useFakeTimers()
    try {
      const f = setup()
      const cancel = new CommandCancellation()
      const stopping = cancel.stop(f.ctx, f.binding, 30)
      const rejection = expect(stopping).rejects.toThrow('host_cancel_pending')
      await vi.advanceTimersByTimeAsync(30)
      await rejection
      expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
      f.finishDrain()
      await Promise.resolve()
      await Promise.resolve()
      await cancel.stop(f.ctx, f.binding, 30)
      expect(cancel.readyForFreshQuiescence(f.binding)).toBe(true)
      expect(f.subagents.drainContinuableDescendants).toHaveBeenCalledOnce()
    } finally { vi.useRealTimers() }
  })

  it('never kills unowned or another parent jobs', async () => {
    const f = setup()
    f.setJobs([
      { id: 'subagent-1', ownerSession: f.parent.id, status: 'running' },
      { id: 'subagent-2', ownerSession: 'other-run', status: 'running' },
      { id: 'bash-3', status: 'running' },
    ])
    const cancel = new CommandCancellation()
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    await Promise.resolve()
    expect(f.jobs.kill).toHaveBeenCalledTimes(1)
    expect(f.jobs.kill).toHaveBeenCalledWith('subagent-1', f.parent, 'decision-hub owner cancellation')
    f.setJobs([{ id: 'subagent-1', ownerSession: f.parent.id, status: 'killed' }])
    f.finishDrain()
    await stopping
  })

  it('keeps a cleanup failure sticky after the registry becomes empty', async () => {
    const f = setup()
    f.subagents.drainContinuableDescendants.mockImplementation(async () => { throw new AggregateError([], 'cleanup') })
    const cancel = new CommandCancellation()
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('cleanup')
    f.setJobs([])
    f.subagents.drainContinuableDescendants.mockResolvedValue(undefined)
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('host_cancel_cleanup_unconfirmed')
    expect(f.subagents.drainContinuableDescendants).toHaveBeenCalledOnce()
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
  })

  it('does not treat a failed job record as released resources', async () => {
    const f = setup()
    f.setJobs([{ id: 'subagent-1', ownerSession: f.parent.id, status: 'running' }])
    f.jobs.wait.mockResolvedValue({ id: 'subagent-1', ownerSession: f.parent.id, status: 'failed' })
    const cancel = new CommandCancellation()
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    f.finishDrain()
    await expect(stopping).rejects.toThrow('host_cancel_job_release_unconfirmed')
    f.setJobs([])
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('host_cancel_job_release_unconfirmed')
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
  })

  it('retains an initially failed owned job as unknown after it disappears', async () => {
    const f = setup()
    f.setJobs([{ id: 'subagent-1', ownerSession: f.parent.id, status: 'failed' }])
    const cancel = new CommandCancellation()
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('host_cancel_job_release_unconfirmed')
    f.setJobs([])
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('host_cancel_job_release_unconfirmed')
    expect(f.jobs.kill).not.toHaveBeenCalled()
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
    f.finishDrain()
  })

  it('can discard only an unstarted intent when the original callback is already acknowledged', async () => {
    const f = setup()
    const cancel = new CommandCancellation()
    cancel.begin(f.binding)
    cancel.discardUnstarted(f.binding)
    expect(cancel.requested(f.binding)).toBe(false)
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    await Promise.resolve()
    cancel.discardUnstarted(f.binding)
    expect(cancel.requested(f.binding)).toBe(true)
    f.finishDrain()
    await stopping
  })

  it('rechecks a previously unavailable service before any SDK side effects and can complete when it becomes ready', async () => {
    const f = setup()
    let ready = false
    const services: CancellationServices = { subagents: f.subagents, jobs: f.jobs }
    f.ctx.get = <K extends keyof CancellationServices>(name: K) => ready ? services[name] : undefined
    const cancel = new CommandCancellation()
    await expect(cancel.stop(f.ctx, f.binding, 1000)).rejects.toThrow('host_cancel_capability_unavailable')
    expect(f.parent.cancel).not.toHaveBeenCalled()
    expect(f.subagents.drainContinuableDescendants).not.toHaveBeenCalled()
    expect(cancel.requested(f.binding)).toBe(true)
    expect(cancel.readyForFreshQuiescence(f.binding)).toBe(false)
    ready = true
    const stopping = cancel.stop(f.ctx, f.binding, 1000)
    f.finishDrain()
    await stopping
    expect(f.subagents.drainContinuableDescendants).toHaveBeenCalledOnce()
    expect(cancel.readyForFreshQuiescence(f.binding, f.ctx)).toBe(true)
  })
})
