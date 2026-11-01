import type { Correlation } from './session-correlation.js'
import { deterministicIds } from './session-correlation.js'
import type { ModelAgentLineage, SessionControllerPublic, SessionSummaryPublic } from './dsh-public.js'

/** The public Agent contract. The object must come from the actual registry. */
export interface CancellationAgent extends ModelAgentLineage {
  readonly status: 'idle' | 'running'
  cancel(cause: { kind: 'user' }, options: { keepInbox: false }): void
  whenIdle(): Promise<void>
}

export interface CancellationJob {
  id: string
  ownerSession?: string
  status: 'running' | 'stopping' | 'completed' | 'killed' | 'failed'
}

/** Thin public service faces; no Agent is constructed or copied at this seam. */
export interface CancellationServices {
  subagents: {
    drainContinuableDescendants(parents: readonly CancellationAgent[]): Promise<void>
  }
  jobs: {
    list(caller: CancellationAgent): CancellationJob[]
    kill(id: string, caller: CancellationAgent, reason: string): 'requested' | 'already-finished'
    wait(id: string, timeoutMs: number, caller: CancellationAgent, signal?: AbortSignal): Promise<CancellationJob>
  }
}

export interface CancellationContext {
  agents?: { get(id: string): ModelAgentLineage | undefined }
  get?<K extends keyof CancellationServices>(name: K): CancellationServices[K] | undefined
}

export type CancellationBinding = Readonly<Pick<Correlation, 'runId' | 'sessionId' | 'requestId' | 'requestHash' | 'generation'>>
type Binding = CancellationBinding
export interface CancellationResourceWitness {
  schema_version: 'dsh-owner-cancel-resource-witness.v1'
  binding: Binding
  requested: boolean
  operation_state: 'unobserved' | 'pending' | 'succeeded' | 'failed'
  scoped_drain: { state: 'not_started' | 'pending' | 'succeeded' | 'failed'; started_at: string | null; settled_at: string | null }
  owner_identity: 'same' | 'changed_or_absent' | 'unobserved'
  reason_code: string
  coverage_scope: string
  physical_release_authenticated: false
}
interface Cancellation {
  readonly binding: Binding
  operation?: Promise<void>
  settled: boolean
  /** A failed cleanup is never replaced by an empty registry on a later read. */
  failure: string | null
  sideEffectsStarted: boolean
  readonly stoppedJobIds: Set<string>
  owner?: CancellationAgent
  scopedDrain?: { state: 'pending' | 'succeeded' | 'failed'; started_at: string; settled_at: string | null }
}

function key(binding: Binding): string {
  return `${binding.runId}:${binding.generation}`
}

export function sameCommandBinding(a: Binding, b: Binding): boolean {
  return a.runId === b.runId && a.sessionId === b.sessionId && a.requestId === b.requestId
    && a.requestHash === b.requestHash && a.generation === b.generation
}

export function freezeCommandBinding(value: Binding): Binding {
  return Object.freeze({
    runId: value.runId, sessionId: value.sessionId, requestId: value.requestId,
    requestHash: value.requestHash, generation: value.generation,
  })
}

function isCancellationAgent(agent: ModelAgentLineage | undefined): agent is CancellationAgent {
  return agent !== undefined
    && 'cancel' in agent && typeof agent.cancel === 'function'
    && 'whenIdle' in agent && typeof agent.whenIdle === 'function'
    && (agent.status === 'idle' || agent.status === 'running')
}

function actualAgent(ctx: CancellationContext, binding: Binding): CancellationAgent {
  const agent = ctx.agents?.get(binding.sessionId)
  if (agent === undefined || agent.id !== binding.sessionId
    || !isCancellationAgent(agent)) {
    throw new Error('host_cancel_capability_unavailable')
  }
  // These checks narrow the real registry object; there is no JSON/DTO cast.
  return agent
}

async function bounded<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) throw new Error('host_cancel_pending')
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(new Error('host_cancel_pending'))
    signal.addEventListener('abort', aborted, { once: true })
    operation.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted))
  })
}

/**
 * One owner cancellation per immutable command binding. This is an operation
 * latch, never a persisted proof, historical receipt, or replacement for a fresh
 * official list/control quiescence check at the terminal callback boundary.
 */
export class CommandCancellation {
  private readonly commands = new Map<string, Cancellation>()

  begin(binding: Binding): void {
    const expected = deterministicIds(binding.runId, binding.requestHash, binding.generation)
    if (expected.sessionId !== binding.sessionId || expected.requestId !== binding.requestId) {
      throw new Error('host_cancel_binding_mismatch')
    }
    const previous = this.commands.get(key(binding))
    if (previous !== undefined) {
      if (!sameCommandBinding(previous.binding, binding)) throw new Error('host_cancel_binding_changed')
      return
    }
    // Called after strict generation/identity validation, before the first await.
    this.commands.set(key(binding), {
      binding: freezeCommandBinding(binding), settled: false, failure: null, sideEffectsStarted: false, stoppedJobIds: new Set(),
    })
  }

  requested(binding: Binding): boolean {
    const value = this.commands.get(key(binding))
    if (value === undefined) return false
    if (!sameCommandBinding(value.binding, binding)) throw new Error('host_cancel_binding_changed')
    return true
  }

  requestedForSession(sessionId: string): boolean {
    return [...this.commands.values()].some(command => command.binding.sessionId === sessionId)
  }

  discardUnstarted(binding: Binding): void {
    const value = this.commands.get(key(binding))
    if (value !== undefined && sameCommandBinding(value.binding, binding)
      && value.operation === undefined && !value.settled && value.failure === null) {
      this.commands.delete(key(binding))
    }
  }

  readyForFreshQuiescence(binding: Binding, ctx?: CancellationContext): boolean {
    const value = this.commands.get(key(binding))
    return value !== undefined && sameCommandBinding(value.binding, binding)
      && value.settled && value.failure === null
      && (ctx === undefined || ctx.agents?.get(binding.sessionId) === value.owner)
  }

  errorCode(binding: Binding): string | null {
    const value = this.commands.get(key(binding))
    return value?.failure ?? (value === undefined ? null : 'host_cancel_pending')
  }

  /** Observes this Host's actual operation; it grants no permission or per-child release. */
  resourceWitness(binding: Binding, ctx: CancellationContext): CancellationResourceWitness {
    const value = this.commands.get(key(binding))
    if (value !== undefined && !sameCommandBinding(value.binding, binding)) throw new Error('host_cancel_binding_changed')
    const failed = value?.failure !== null && value?.failure !== undefined && value.failure !== 'host_cancel_pending'
    return {
      schema_version: 'dsh-owner-cancel-resource-witness.v1', binding: freezeCommandBinding(binding),
      requested: value !== undefined,
      operation_state: value === undefined ? 'unobserved' : failed ? 'failed' : value.settled ? 'succeeded' : 'pending',
      scoped_drain: value?.scopedDrain ? { ...value.scopedDrain } : { state: 'not_started', started_at: null, settled_at: null },
      owner_identity: value?.owner === undefined ? 'unobserved'
        : ctx.agents?.get(binding.sessionId) === value.owner ? 'same' : 'changed_or_absent',
      reason_code: value?.failure ?? (value?.settled ? 'host_cancel_operation_completed' : 'host_cancel_operation_unconfirmed'),
      coverage_scope: 'Official exact-registry-root continuable drain promise and existing parent-job/idle operation; no per-child queue/job or arbitrary one-shot disposal witness',
      physical_release_authenticated: false,
    }
  }

  async stop(ctx: CancellationContext, binding: Binding, timeoutMs: number): Promise<void> {
    this.begin(binding)
    const value = this.commands.get(key(binding))!
    const retryablePreflight = value.failure === 'host_cancel_capability_unavailable' && !value.sideEffectsStarted
    if (value.failure !== null && value.failure !== 'host_cancel_pending' && !retryablePreflight) throw new Error(value.failure)
    if (value.failure === 'host_cancel_pending' || retryablePreflight) {
      // Only a timed wait with work still live may be retried. Cleanup failures
      // remain sticky, including a formerly observed job becoming failed/absent.
      value.failure = null
      value.operation = undefined
    }
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error('host_cancel_wait_invalid')
    if (value.operation === undefined) {
      // Install the promise synchronously before any await so concurrent requests
      // never start a second drain. Capability failures leave the latch pending.
      value.operation = Promise.resolve().then(() => this.stopOnce(ctx, value, timeoutMs)).then(() => {
        value.settled = true
      }, error => {
        value.failure = error instanceof Error && error.message.startsWith('host_cancel_')
          ? error.message : 'host_cancel_cleanup_unconfirmed'
        throw error
      })
      // A bounded waiter may time out while teardown is still running. Observe
      // its later failure without issuing a callback or claiming it was stopped.
      void value.operation.catch(() => undefined)
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try { await bounded(value.operation, controller.signal) } finally { clearTimeout(timer) }
  }

  private async stopOnce(ctx: CancellationContext, value: Cancellation, timeoutMs: number): Promise<void> {
    const binding = value.binding
    const parent = actualAgent(ctx, binding)
    value.owner = parent
    const subagents = ctx.get?.('subagents')
    const jobs = ctx.get?.('jobs')
    if (typeof subagents?.drainContinuableDescendants !== 'function'
      || typeof jobs?.list !== 'function' || typeof jobs.kill !== 'function' || typeof jobs.wait !== 'function') {
      throw new Error('host_cancel_capability_unavailable')
    }
    const assertOwner = () => {
      if (ctx.agents?.get(binding.sessionId) !== parent) throw new Error('host_cancel_owner_changed')
    }
    assertOwner()
    // The official method closes this exact parent's continuable admission
    // synchronously, before yielding. It includes admitted materializations and
    // descendants below already-disposed intermediate one-shot parents.
    value.sideEffectsStarted = true
    const scopedDrain = { state: 'pending' as 'pending' | 'succeeded' | 'failed', started_at: new Date().toISOString(), settled_at: null as string | null }
    value.scopedDrain = scopedDrain
    let drained: Promise<void>
    try { drained = subagents.drainContinuableDescendants([parent]) } catch (error) {
      scopedDrain.state = 'failed'; scopedDrain.settled_at = new Date().toISOString()
      throw error
    }
    // This records the official promise outcome, including AggregateError. It
    // never substitutes registry absence or changes the original cleanup await.
    void drained.then(() => {
      scopedDrain.state = 'succeeded'; scopedDrain.settled_at = new Date().toISOString()
    }, () => {
      scopedDrain.state = 'failed'; scopedDrain.settled_at = new Date().toISOString()
    })
    // Attach rejection handling before a synchronous parent/job error can exit.
    void drained.catch(() => undefined)
    parent.cancel({ kind: 'user' }, { keepInbox: false })
    const owned = jobs.list(parent).filter(job => job.ownerSession === parent.id)
    if (owned.length > 64) throw new Error('host_cancel_jobs_unbounded')
    if (owned.some(job => job.status === 'failed')) throw new Error('host_cancel_job_release_unconfirmed')
    for (const id of value.stoppedJobIds) {
      const previous = owned.find(job => job.id === id)
      if (previous === undefined || previous.status === 'failed') {
        throw new Error('host_cancel_job_release_unconfirmed')
      }
    }
    const waits: Promise<CancellationJob>[] = []
    for (const job of owned) {
      if (job.status !== 'running' && job.status !== 'stopping') continue
      value.stoppedJobIds.add(job.id)
      assertOwner()
      jobs.kill(job.id, parent, 'decision-hub owner cancellation')
      waits.push(jobs.wait(job.id, timeoutMs, parent))
    }
    const [, snapshots] = await Promise.all([drained, Promise.all(waits)])
    assertOwner()
    if (snapshots.some(job => job.ownerSession !== parent.id || job.status === 'failed')) {
      // failed can mean cleanup failed/record was force-failed, not resource exit.
      throw new Error('host_cancel_job_release_unconfirmed')
    }
    if (snapshots.some(job => job.status !== 'completed' && job.status !== 'killed')) {
      throw new Error('host_cancel_pending')
    }
    // Scoped drain injects durable settlement notices without waking its closing
    // parent. Cancel those unclaimed inbox entries, preserving the saved audit.
    parent.cancel({ kind: 'user' }, { keepInbox: false })
    await parent.whenIdle()
    assertOwner()
    if (parent.status !== 'idle' || jobs.list(parent).some(job => job.ownerSession === parent.id
      && (job.status === 'running' || job.status === 'stopping'))) {
      throw new Error('host_cancel_work_remains')
    }
  }
}

export interface CancellationQuiescence {
  readonly parentSeq: number
  readonly lastSeq: number
  readonly descendantIds: readonly string[]
}

/** Fresh official current state only. This never returns an activity receipt. */
export async function readCommandQuiescence(
  ctx: CancellationContext, controller: SessionControllerPublic,
  parentId: string, signal: AbortSignal,
): Promise<CancellationQuiescence | null> {
  const parent = ctx.agents?.get(parentId)
  const list = controller.list
  const control = controller.control
  if (!parent || parent.id !== parentId || parent.status !== 'idle' || !list || !control) return null
  try {
    const listed = await bounded(list.call(controller, {}, signal), signal)
    if (!Array.isArray(listed.items) || listed.items.length > 4096) return null
    const summaries = new Map<string, SessionSummaryPublic>()
    for (const row of listed.items) {
      if (!row || !row.sessionId || typeof row.running !== 'boolean' || summaries.has(row.sessionId)
        || (row.parentSessionId !== undefined && !row.parentSessionId)) return null
      summaries.set(row.sessionId, row)
    }
    if (!summaries.has(parentId)) return null
    // A complete rooted tree cannot be claimed across a missing parent/cycle.
    for (const row of summaries.values()) {
      const seen = new Set<string>()
      let id: string | undefined = row.sessionId
      while (id) {
        if (seen.has(id) || seen.size >= 128 || !summaries.has(id)) return null
        seen.add(id)
        id = summaries.get(id)?.parentSessionId
      }
    }
    const lineage = new Set([parentId])
    for (let changed = true; changed;) {
      changed = false
      for (const row of summaries.values()) {
        if (row.parentSessionId && lineage.has(row.parentSessionId) && !lineage.has(row.sessionId)) {
          if (lineage.size >= 65) return null
          lineage.add(row.sessionId)
          changed = true
        }
      }
    }
    const liveOwners = new Map<string, ModelAgentLineage | undefined>()
    for (const id of lineage) {
      const row = summaries.get(id)!
      if (row.running) return null
      const live = ctx.agents?.get(id)
      liveOwners.set(id, live)
      if (live && (live.id !== id || live.status !== 'idle'
        || (id !== parentId && live.session?.header?.parentSession !== row.parentSessionId))) return null
      if (id !== parentId && !live) {
        const cold = await bounded(controller.inspect(id, signal), signal)
        if (cold.meta.id !== id || cold.meta.parentSession !== row.parentSessionId) return null
      }
    }
    const iterator = control.call(controller, signal)[Symbol.asyncIterator]()
    let parentSeq: number
    try {
      const first = await bounded(iterator.next(), signal)
      if (first.done || first.value.type !== 'baseline' || !first.value.value) return null
      const { queues, jobs, projections } = first.value.value
      if (!queues || !jobs || !Object.hasOwn(queues, parentId) || !Object.hasOwn(jobs, parentId)) return null
      const seq = projections?.[parentId]?.asOfSeq
      if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 0) return null
      parentSeq = seq
      for (const id of lineage) {
        const live = ctx.agents?.get(id)
        if (live && (!Object.hasOwn(queues, id) || !Object.hasOwn(jobs, id))) return null
        const queue = queues[id] ?? []
        const ownedJobs = jobs[id] ?? []
        if (!Array.isArray(queue) || queue.length !== 0 || !Array.isArray(ownedJobs)
          || ownedJobs.some(job => !job || !['completed', 'killed'].includes(job.status))) return null
      }
    } finally { await bounded(Promise.resolve(iterator.return?.()), signal) }
    const inspection = await bounded(controller.inspect(parentId, signal), signal)
    const lastSeq = inspection.events.reduce((seq, event) => Math.max(seq, event.seq), 0)
    if (inspection.meta.id !== parentId || lastSeq > parentSeq
      || ctx.agents?.get(parentId) !== parent || parent.status !== 'idle') return null
    // Control/inspection are awaits. A same-id replacement, new live child, or
    // a descendant that resumed after the baseline cannot inherit that proof.
    for (const id of lineage) {
      const live = ctx.agents?.get(id)
      if (live !== liveOwners.get(id) || (live && (live.status !== 'idle'
        || (id !== parentId && live.session?.header?.parentSession !== summaries.get(id)?.parentSessionId)))) return null
    }
    signal.throwIfAborted()
    return { parentSeq, lastSeq, descendantIds: [...lineage].filter(id => id !== parentId).sort() }
  } catch { return null }
}
