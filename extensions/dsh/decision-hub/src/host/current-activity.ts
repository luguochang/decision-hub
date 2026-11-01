import { createHash, randomUUID } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import { z } from 'zod'
import type { HostContextPublic, ModelAgentLineage, SessionSummaryPublic, SessionControlFramePublic } from './dsh-public.js'
import { deterministicIds } from './session-correlation.js'
import { commandDelegations, hasAcceptedPrompt } from './session-result.js'
import type { CancellationBinding, CancellationResourceWitness } from './command-cancellation.js'

const bindingSchema = z.object({
  run_id: z.string().min(1).max(128),
  study_id: z.string().min(1).max(128),
  dsh_session_id: z.string().min(1).max(256),
  generation: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
  request_hash: z.string().regex(/^[a-f0-9]{64}$/),
  link_fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
}).strict()

export const hostActivityRequestSchema = z.object({
  schema_version: z.literal('dsh-host-activity-request.v1'),
  records: z.array(bindingSchema).min(1).max(64),
}).strict().superRefine((request, ctx) => {
  if (new Set(request.records.map(row => row.run_id)).size !== request.records.length
    || new Set(request.records.map(row => row.dsh_session_id)).size !== request.records.length) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Duplicate activity target' })
  }
})

export type HostActivityBinding = z.infer<typeof bindingSchema>
export interface HostActivityScope {
  host_instance_id: string
  process_id: number
  /** Bridge instance creation time, not an attestation of OS process start. */
  started_at: string
  hub_base_url: string
  workspace_id: string
  workspace_cwd_hash: string
}
export interface HostActivityRecord extends HostActivityBinding {
  state: 'current_scope_inactive' | 'active' | 'unknown'
  presence: 'present' | 'absent' | 'unknown'
  running: boolean | null
  queue_count: number | null
  active_job_count: number | null
  descendant_ids: string[]
  fence_id: string | null
  reason_code: string
  terminal_state: 'unknown'
}
export interface HostActivitySnapshot {
  schema_version: 'dsh-host-activity-snapshot.v1'
  scope: HostActivityScope
  observed_at: string
  expires_at: string
  census_complete: boolean
  records: HostActivityRecord[]
}

export const hostSelectedActivityRequestSchema = z.object({
  schema_version: z.literal('dsh-host-selected-activity-request.v1'),
  run_id: z.string().min(1).max(128), dsh_session_id: z.string().min(1).max(256),
  request_hash: z.string().regex(/^[a-f0-9]{64}$/),
  generation: z.coerce.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
  plugin_build_hash: z.string().regex(/^[a-f0-9]{64}$/),
  host_instance_id: z.string().uuid(), process_id: z.coerce.number().int().min(1),
  workspace_id: z.string().min(1).max(256), workspace_cwd_hash: z.string().regex(/^[a-f0-9]{64}$/),
}).strict()
export type HostSelectedActivityRequest = z.infer<typeof hostSelectedActivityRequestSchema>
export interface HostSelectedActivityResource {
  session_id: string
  parent_session_id: string | null
  session_presence: 'present' | 'unknown'
  agent_presence: 'warm' | 'cold'
  agent_running: boolean | null
  queue_present: boolean
  jobs_present: boolean
  queue_count: number | null
  active_job_count: number | null
  job_states: { id: string | null; status: string }[] | null
  provider_stream_count: number | null
  model_invocation_count: number | null
  reason_code: string
}
export interface HostSelectedActivityObservation {
  schema_version: 'dsh-host-selected-activity-observation.v1'
  scope: HostActivityScope
  binding: Pick<HostSelectedActivityRequest, 'run_id' | 'dsh_session_id' | 'request_hash' | 'generation' | 'plugin_build_hash'>
  observed_started_at: string
  observed_at: string
  expires_at: string
  complete: boolean
  visible_session_list_complete: boolean
  visibility_scope: string
  reason_code: string
  resources: HostSelectedActivityResource[]
  cancellation_drain: { requested: boolean | null; state: 'settled' | 'unknown'; reason_code: string }
  cancellation_resource_witness: CancellationResourceWitness | null
  model_invocation_count_scope: string
  physical_release_authenticated: false
  stream_count_scope: string
}

type Fence = { binding: HostActivityBinding; id: string }
const TERMINAL_BUSINESS = new Set(['completed', 'degraded', 'research_only', 'rejected', 'failed', 'cancelled'])
const ACTIVE_JOB = new Set(['running', 'stopping'])
const JOB_STATES = new Set(['running', 'stopping', 'completed', 'killed', 'failed'])
const MAX_CENSUS = 4096

function sameFenceBinding(a: HostActivityBinding, b: HostActivityBinding): boolean {
  return a.run_id === b.run_id && a.study_id === b.study_id && a.dsh_session_id === b.dsh_session_id
    && a.generation === b.generation && a.request_hash === b.request_hash
    && a.link_fingerprint === b.link_fingerprint
}

function canRebindCancelledFence(
  previous: HostActivityBinding,
  current: HostActivityBinding,
  cancelled: CancellationBinding | undefined,
): boolean {
  if (!cancelled || previous.link_fingerprint === current.link_fingerprint
    || previous.run_id !== current.run_id || previous.study_id !== current.study_id
    || previous.dsh_session_id !== current.dsh_session_id || previous.generation !== current.generation
    || previous.request_hash !== current.request_hash) return false
  const expected = deterministicIds(current.run_id, current.request_hash, current.generation)
  return cancelled.runId === current.run_id && cancelled.sessionId === current.dsh_session_id
    && cancelled.requestId === expected.requestId && cancelled.requestHash === current.request_hash
    && cancelled.generation === current.generation
}

/** Current activity proof never projects or authenticates historical terminal state. */
export class CurrentHostActivity {
  private readonly instanceId = randomUUID()
  private readonly startedAt = new Date().toISOString()
  private readonly fences = new Map<string, Fence>()
  private readonly cancelledRoots = new Map<string, CancellationBinding>()
  private readonly parents = new Map<string, string>()
  private readonly modelStreams = new Map<string, number>()
  private readonly modelInvocations = new Map<string, number>()

  constructor(private readonly ctx: HostContextPublic) {}

  async scope(hubBaseUrl: string, workspaceCwd: string, workspaceId: string): Promise<HostActivityScope> {
    const canonical = (await realpath(workspaceCwd)).replace(/\\/g, '/').toLowerCase()
    return {
      host_instance_id: this.instanceId, process_id: process.pid, started_at: this.startedAt,
      hub_base_url: hubBaseUrl.replace(/\/+$/, ''), workspace_id: workspaceId,
      workspace_cwd_hash: createHash('sha256').update(canonical).digest('hex'),
    }
  }

  /** Call only after fresh original Hub Link identity validation. No receipt is minted. */
  cancelCommand(binding: CancellationBinding): void {
    const expected = deterministicIds(binding.runId, binding.requestHash, binding.generation)
    if (expected.sessionId !== binding.sessionId || expected.requestId !== binding.requestId) {
      throw new Error('host_activity_cancel_binding_mismatch')
    }
    const previous = this.cancelledRoots.get(binding.sessionId)
    if (previous && (previous.runId !== binding.runId || previous.requestHash !== binding.requestHash
      || previous.requestId !== binding.requestId || previous.generation !== binding.generation)) {
      throw new Error('host_activity_cancel_binding_changed')
    }
    if (!previous && this.cancelledRoots.size >= MAX_CENSUS) throw new Error('host_activity_fence_capacity')
    this.cancelledRoots.set(binding.sessionId, Object.freeze({ ...binding }))
  }

  hasLineageModelStreams(ids: readonly string[]): boolean {
    return ids.some(id => (this.modelStreams.get(id) ?? 0) > 0)
  }

  isFenced(sessionId: string | undefined): boolean {
    if (!sessionId) return false
    const visited = new Set<string>()
    let current: string | undefined = sessionId
    for (let depth = 0; current && depth < 128; depth++) {
      if (this.fences.has(current) || this.cancelledRoots.has(current)) return true
      if (visited.has(current)) throw new Error('host_activity_lineage_invalid')
      visited.add(current)
      const agent: ModelAgentLineage | undefined = this.ctx.agents?.get(current)
      if (agent && agent.id !== current) throw new Error('host_activity_lineage_invalid')
      current = agent?.session?.header?.parentSession ?? this.parents.get(current)
    }
    if (current) throw new Error('host_activity_lineage_invalid')
    return false
  }

  assertNotFenced(sessionId: string | undefined): void {
    if (this.isFenced(sessionId)) throw new Error('host_activity_lineage_fenced')
  }

  async assertModelNotFenced(sessionId: string | undefined): Promise<void> {
    this.assertNotFenced(sessionId)
    if (!sessionId || (this.fences.size === 0 && this.cancelledRoots.size === 0)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const elapsed = new Promise<never>((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(new Error('host_activity_lineage_unavailable')) }, 2000)
    })
    const inspectLineage = async () => {
      const visited = new Set<string>()
      let id: string | undefined = sessionId
      while (id) {
        if (this.fences.has(id) || this.cancelledRoots.has(id)) throw new Error('host_activity_lineage_fenced')
        if (visited.has(id) || visited.size >= 128) throw new Error('host_activity_lineage_invalid')
        visited.add(id)
        // Every managed root is independently bound by guardedModelStream.
        if (/^dsh_[a-f0-9]{64}$/.test(id)) return
        const agent: ModelAgentLineage | undefined = this.ctx.agents?.get(id)
        if (agent && agent.id !== id) throw new Error('host_activity_lineage_invalid')
        let parent: string | undefined = agent?.session?.header?.parentSession ?? this.parents.get(id)
        if (!agent && !parent) {
          // A new/cold descendant must not evade a fence when no live Agent is exposed.
          const inspection = await this.ctx.sessionController.inspect(id, controller.signal)
          if (inspection.meta.id !== id) throw new Error('host_activity_lineage_invalid')
          parent = inspection.meta.parentSession
        }
        if (parent) this.parents.set(id, parent)
        id = parent
      }
    }
    try { await Promise.race([inspectLineage(), elapsed]) } catch (error) {
      throw new Error(activityReason(error) === 'host_activity_read_failed' ? 'host_activity_lineage_unavailable' : activityReason(error))
    } finally {
      clearTimeout(timer)
      controller.abort()
    }
  }

  /** Whole formal Host hook lifetime, before its first await, through iterator finally. */
  beginModelInvocation(sessionId: string | undefined): () => void {
    if (!sessionId) return () => undefined // An untagged invocation cannot be assigned to a Run.
    this.modelInvocations.set(sessionId, (this.modelInvocations.get(sessionId) ?? 0) + 1)
    let released = false
    return () => {
      if (released) return
      released = true
      const remaining = (this.modelInvocations.get(sessionId) ?? 1) - 1
      if (remaining) this.modelInvocations.set(sessionId, remaining)
      else this.modelInvocations.delete(sessionId)
    }
  }

  /** Rechecked at provider delegation, including compaction and title purposes. */
  async *modelStream(sessionId: string | undefined, next: () => AsyncIterable<unknown>): AsyncIterable<unknown> {
    await this.assertModelNotFenced(sessionId)
    this.assertNotFenced(sessionId)
    if (sessionId) this.modelStreams.set(sessionId, (this.modelStreams.get(sessionId) ?? 0) + 1)
    try { yield* next() } finally {
      if (sessionId) {
        const remaining = (this.modelStreams.get(sessionId) ?? 1) - 1
        if (remaining) this.modelStreams.set(sessionId, remaining)
        else this.modelStreams.delete(sessionId)
      }
    }
  }

  /** Observe existing resources only; no admission fence, teardown, or historical authority. */
  async observeSelected(
    scope: HostActivityScope,
    request: HostSelectedActivityRequest,
    verify: () => Promise<void>,
    drain: () => { requested: boolean; state: 'settled' | 'unknown'; reason_code: string; resource_witness?: CancellationResourceWitness },
    timeoutMs: number,
  ): Promise<HostSelectedActivityObservation> {
    const expected = deterministicIds(request.run_id, request.request_hash, request.generation)
    if (expected.sessionId !== request.dsh_session_id
      || request.host_instance_id !== scope.host_instance_id || request.process_id !== scope.process_id
      || request.workspace_id !== scope.workspace_id || request.workspace_cwd_hash !== scope.workspace_cwd_hash) {
      throw new Error('host_activity_selected_binding_or_scope_mismatch')
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const bounded = <T>(work: Promise<T>): Promise<T> => new Promise((resolve, reject) => {
      const aborted = () => reject(new Error('host_activity_deadline_elapsed'))
      if (controller.signal.aborted) return aborted()
      controller.signal.addEventListener('abort', aborted, { once: true })
      work.then(resolve, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    const observedStarted = new Date().toISOString()
    const result: HostSelectedActivityObservation = {
      schema_version: 'dsh-host-selected-activity-observation.v1', scope,
      binding: { run_id: request.run_id, dsh_session_id: request.dsh_session_id,
        request_hash: request.request_hash, generation: request.generation, plugin_build_hash: request.plugin_build_hash },
      observed_started_at: observedStarted, observed_at: observedStarted, expires_at: observedStarted,
      complete: false, visible_session_list_complete: false,
      visibility_scope: 'Official visible Session rows; cold absence does not authenticate execution teardown',
      reason_code: 'host_activity_read_failed', resources: [],
      cancellation_drain: { requested: null, state: 'unknown', reason_code: 'host_activity_drain_unobserved' },
      cancellation_resource_witness: null,
      model_invocation_count_scope: 'Session-tagged llm/stream hooks held by this Host, including pre-provider fence and binding waits; untagged calls and other resources are unassigned',
      physical_release_authenticated: false,
      stream_count_scope: 'Delegated provider iterators in this Host; excludes pre-provider model-binding waits',
    }
    try {
      // The route verifier checks the original current Hub Link and plugin before any SDK read.
      await bounded(verify())
      const list = this.ctx.sessionController.list, control = this.ctx.sessionController.control
      if (!list || !control || !this.ctx.agents) throw new Error('host_activity_official_capability_unavailable')
      const listed = await bounded(list.call(this.ctx.sessionController, {}, controller.signal))
      if (!Array.isArray(listed.items) || listed.items.length > MAX_CENSUS) throw new Error('host_activity_census_invalid')
      const summaries = new Map<string, SessionSummaryPublic>()
      for (const row of listed.items) {
        if (!row || typeof row.sessionId !== 'string' || !row.sessionId || typeof row.running !== 'boolean'
          || summaries.has(row.sessionId) || (row.parentSessionId !== undefined && !row.parentSessionId)) {
          throw new Error('host_activity_census_invalid')
        }
        summaries.set(row.sessionId, row)
      }
      // Pinned SessionController.list and SessionListState.list have no pagination/limit.
      result.visible_session_list_complete = true
      const lineage = new Set([request.dsh_session_id])
      for (let changed = true; changed;) {
        changed = false
        for (const row of summaries.values()) {
          if (row.parentSessionId && lineage.has(row.parentSessionId) && !lineage.has(row.sessionId)) {
            if (lineage.size >= 65) throw new Error('host_activity_lineage_invalid')
            lineage.add(row.sessionId); changed = true
          }
        }
      }
      const owners = new Map<string, ModelAgentLineage | undefined>()
      for (const id of lineage) {
        const agent = this.ctx.agents.get(id)
        owners.set(id, agent)
        if (agent && (agent.id !== id || (id !== request.dsh_session_id
          && agent.session?.header?.parentSession !== summaries.get(id)?.parentSessionId))) {
          throw new Error('host_activity_lineage_invalid')
        }
      }
      const iterator = control.call(this.ctx.sessionController, controller.signal)[Symbol.asyncIterator]()
      let queues: NonNullable<SessionControlFramePublic['value']>['queues']
      let jobs: NonNullable<SessionControlFramePublic['value']>['jobs']
      let cursor: number
      try {
        const first = await bounded(iterator.next())
        if (first.done || first.value.type !== 'baseline' || !first.value.value) throw new Error('host_activity_control_incomplete')
        const baseline = first.value.value
        queues = baseline.queues; jobs = baseline.jobs
        const seq = baseline.projections?.[request.dsh_session_id]?.asOfSeq
        if (!queues || !jobs || typeof queues !== 'object' || typeof jobs !== 'object'
          || typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 0) throw new Error('host_activity_control_incomplete')
        cursor = seq
      } finally {
        await bounded(Promise.resolve(iterator.return?.()))
      }
      // inspect attaches only the saved archive. It never querySession/activates an Agent.
      const inspected = await bounded(this.ctx.sessionController.inspect(request.dsh_session_id, controller.signal))
      const lastSeq = inspected.events.reduce((seq, event) => Math.max(seq, event.seq), 0)
      if (inspected.meta.id !== request.dsh_session_id || inspected.meta.parentSession !== undefined || lastSeq > cursor
        || !hasAcceptedPrompt(inspected, expected.requestId)) throw new Error('host_activity_selected_cursor_or_prompt_mismatch')
      const declared = commandDelegations(inspected, expected.requestId)
      for (const id of declared.childIds) {
        if (!lineage.has(id)) {
          if (lineage.size >= 65) throw new Error('host_activity_lineage_invalid')
          lineage.add(id); owners.set(id, this.ctx.agents.get(id))
        }
      }
      const resources: HostSelectedActivityResource[] = []
      for (const id of lineage) {
        const row = summaries.get(id), agent = this.ctx.agents.get(id)
        if (agent !== owners.get(id) || (agent && agent.id !== id)) throw new Error('host_activity_registry_changed')
        const warm = agent !== undefined
        let parentSessionId = row?.parentSessionId ?? null
        if (id !== request.dsh_session_id) {
          if (row?.parentSessionId && !lineage.has(row.parentSessionId)) throw new Error('host_activity_lineage_invalid')
          if (warm && (!row?.parentSessionId || agent.session?.header?.parentSession !== row.parentSessionId)) {
            throw new Error('host_activity_lineage_invalid')
          }
          if (!warm) {
            const cold = await bounded(this.ctx.sessionController.inspect(id, controller.signal))
            if (cold.meta.id !== id || !cold.meta.parentSession || !lineage.has(cold.meta.parentSession)) {
              throw new Error('host_activity_lineage_invalid')
            }
            parentSessionId = cold.meta.parentSession
          }
        }
        const queuePresent = Object.hasOwn(queues, id), jobsPresent = Object.hasOwn(jobs, id)
        const queue = queuePresent ? queues[id] : undefined, ownedJobs = jobsPresent ? jobs[id] : undefined
        if (warm && (!queuePresent || !jobsPresent)) throw new Error('host_activity_control_incomplete')
        if ((queue !== undefined && !Array.isArray(queue)) || (ownedJobs !== undefined && (!Array.isArray(ownedJobs)
          || ownedJobs.length > 64 || ownedJobs.some(job => !job || !JOB_STATES.has(job.status))))) {
          throw new Error('host_activity_control_incomplete')
        }
        const streams = this.modelStreams.get(id) ?? 0
        resources.push({ session_id: id, parent_session_id: parentSessionId,
          session_presence: row ? 'present' : 'unknown', agent_presence: warm ? 'warm' : 'cold',
          agent_running: warm && (agent.status === 'running' || agent.status === 'idle') ? agent.status === 'running' : null,
          queue_present: queuePresent, jobs_present: jobsPresent,
          // Official baseline has synthesized [] for cold Agents: keep those counters UNKNOWN.
          queue_count: warm && queue !== undefined ? queue.length : null,
          active_job_count: warm && ownedJobs !== undefined ? ownedJobs.filter(job => ACTIVE_JOB.has(job.status)).length : null,
          job_states: warm && ownedJobs !== undefined ? ownedJobs.map(job => ({ id: typeof job.id === 'string' ? job.id : null, status: job.status })) : null,
          // Both counters are our own lifecycle maps, not synthesized SDK cold arrays.
          provider_stream_count: streams, model_invocation_count: this.modelInvocations.get(id) ?? 0,
          reason_code: warm ? 'host_activity_warm_observed' : 'host_activity_cold_resources_unknown' })
      }
      // No borrowed output grants an execution permission. Recheck actual identity after every await.
      await bounded(verify())
      for (const resource of resources) {
        const owner = owners.get(resource.session_id), current = this.ctx.agents.get(resource.session_id)
        if (current !== owner || (current && (current.id !== resource.session_id
          || (resource.agent_running !== null && (current.status === 'running') !== resource.agent_running)
          || (resource.session_id !== request.dsh_session_id && current.session?.header?.parentSession !== resource.parent_session_id)))) {
          throw new Error('host_activity_registry_changed')
        }
        const count = this.modelStreams.get(resource.session_id) ?? 0
        if (resource.provider_stream_count !== count
          || resource.model_invocation_count !== (this.modelInvocations.get(resource.session_id) ?? 0)) {
          throw new Error('host_activity_streams_changed')
        }
      }
      result.resources = resources
      const cancellation = drain()
      result.cancellation_drain = { requested: cancellation.requested, state: cancellation.state, reason_code: cancellation.reason_code }
      result.cancellation_resource_witness = cancellation.resource_witness ?? null
      result.complete = !declared.unresolved
      result.reason_code = declared.unresolved ? 'host_activity_declared_settlement_unknown' : 'host_activity_selected_observed'
    } catch (error) {
      result.complete = false
      result.reason_code = activityReason(error)
      result.resources = []
      result.cancellation_drain = { requested: null, state: 'unknown', reason_code: 'host_activity_drain_unobserved' }
    } finally {
      clearTimeout(timer); controller.abort()
      result.observed_at = new Date().toISOString()
      result.expires_at = new Date(Date.parse(result.observed_at) + 10_000).toISOString()
    }
    return result
  }

  async snapshot(
    scope: HostActivityScope,
    bindings: HostActivityBinding[],
    verify: (binding: HostActivityBinding) => Promise<{ businessStatus: string }>,
    timeoutMs: number,
  ): Promise<HostActivitySnapshot> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const reasons = new Map<string, string>()
    const bounded = <T>(work: Promise<T>): Promise<T> => new Promise((resolve, reject) => {
      const aborted = () => reject(new Error('host_activity_deadline_elapsed'))
      if (controller.signal.aborted) return aborted()
      controller.signal.addEventListener('abort', aborted, { once: true })
      work.then(resolve, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    let complete = false
    let summaries = new Map<string, SessionSummaryPublic>()
    let queues: Readonly<Record<string, readonly unknown[]>> = {}
    let jobs: Readonly<Record<string, readonly { status: string }[]>> = {}
    let censusReason = 'host_activity_census_unavailable'
    try {
      const verifyOne = async (binding: HostActivityBinding): Promise<void> => {
        try {
          if (deterministicIds(binding.run_id, binding.request_hash, binding.generation).sessionId !== binding.dsh_session_id) {
            throw new Error('host_activity_binding_mismatch')
          }
          const proof = await bounded(verify(binding))
          if (!TERMINAL_BUSINESS.has(proof.businessStatus)) throw new Error('host_activity_run_not_terminal')
          controller.signal.throwIfAborted()
          const previous = this.fences.get(binding.dsh_session_id)
          if (previous && !sameFenceBinding(previous.binding, binding)) {
            if (!canRebindCancelledFence(previous.binding, binding, this.cancelledRoots.get(binding.dsh_session_id))) {
              throw new Error('host_activity_fence_binding_changed')
            }
            this.fences.set(binding.dsh_session_id, { binding: { ...binding }, id: previous.id })
          }
          if (!previous) {
            if (this.fences.size >= MAX_CENSUS) throw new Error('host_activity_fence_capacity')
            this.fences.set(binding.dsh_session_id, { binding: { ...binding }, id: randomUUID() })
          }
        } catch (error) {
          reasons.set(binding.dsh_session_id, activityReason(error))
        }
      }
      let nextBinding = 0
      // Bounded read concurrency keeps a full batch inside the collector deadline.
      // These requests inspect existing Hub records; they never schedule work.
      await Promise.all(Array.from({ length: Math.min(4, bindings.length) }, async () => {
        while (nextBinding < bindings.length) {
          const binding = bindings[nextBinding++]!
          await verifyOne(binding)
        }
      }))
      const list = this.ctx.sessionController.list
      const control = this.ctx.sessionController.control
      if (!list || !control) throw new Error('host_activity_official_capability_unavailable')
      const listed = await bounded(list.call(this.ctx.sessionController, {}, controller.signal))
      if (!Array.isArray(listed.items) || listed.items.length > MAX_CENSUS) throw new Error('host_activity_census_invalid')
      summaries = new Map()
      for (const row of listed.items) {
        if (!row || typeof row.sessionId !== 'string' || !row.sessionId || typeof row.running !== 'boolean'
          || (row.parentSessionId !== undefined && (typeof row.parentSessionId !== 'string' || !row.parentSessionId))
          || summaries.has(row.sessionId)) throw new Error('host_activity_census_invalid')
        summaries.set(row.sessionId, row)
      }
      const iterator = control.call(this.ctx.sessionController, controller.signal)[Symbol.asyncIterator]()
      try {
        const first = await bounded(iterator.next())
        if (first.done || first.value.type !== 'baseline' || !first.value.value) throw new Error('host_activity_control_incomplete')
        queues = first.value.value.queues
        jobs = first.value.value.jobs
        if (!queues || !jobs || typeof queues !== 'object' || typeof jobs !== 'object'
          || Array.isArray(queues) || Array.isArray(jobs)) throw new Error('host_activity_control_incomplete')
        const attached = new Set([...Object.keys(queues), ...Object.keys(jobs)])
        if (attached.size > MAX_CENSUS) throw new Error('host_activity_census_invalid')
        for (const id of attached) {
          if (!summaries.has(id) || !Object.hasOwn(queues, id) || !Object.hasOwn(jobs, id)
            || !Array.isArray(queues[id]) || !Array.isArray(jobs[id])
            || jobs[id]!.some(job => !job || !JOB_STATES.has(job.status))) throw new Error('host_activity_control_incomplete')
        }
      } finally {
        // The official first frame is a complete baseline; never retain a live stream.
        await bounded(Promise.resolve(iterator.return?.()))
      }
      controller.signal.throwIfAborted()
      for (const row of summaries.values()) {
        if (row.parentSessionId) this.parents.set(row.sessionId, row.parentSessionId)
      }
      for (const row of summaries.values()) {
        const seen = new Set<string>()
        let id: string | undefined = row.sessionId
        while (id) {
          if (seen.has(id) || seen.size >= 128) throw new Error('host_activity_lineage_invalid')
          seen.add(id)
          const parent: string | undefined = summaries.get(id)?.parentSessionId
          if (parent && !summaries.has(parent)) throw new Error('host_activity_lineage_incomplete')
          id = parent
        }
      }
      complete = true
    } catch (error) { censusReason = activityReason(error) } finally {
      clearTimeout(timer)
      controller.abort()
    }
    const observed = new Date()
    const records: HostActivityRecord[] = bindings.map(binding => {
      const fence = this.fences.get(binding.dsh_session_id)
      const reason = reasons.get(binding.dsh_session_id)
      const base: HostActivityRecord = {
        ...binding, state: 'unknown', presence: 'unknown', running: null, queue_count: null,
        active_job_count: null, descendant_ids: [], fence_id: fence?.id ?? null,
        reason_code: reason ?? (complete ? 'host_activity_fence_unavailable' : censusReason), terminal_state: 'unknown',
      }
      if (!complete || reason || !fence) return base
      const lineage = new Set([binding.dsh_session_id])
      for (let changed = true; changed;) {
        changed = false
        for (const row of summaries.values()) {
          if (row.parentSessionId && lineage.has(row.parentSessionId) && !lineage.has(row.sessionId)) {
            lineage.add(row.sessionId); changed = true
          }
        }
      }
      const row = summaries.get(binding.dsh_session_id)
      const running = [...lineage].some(id => summaries.get(id)?.running || this.modelStreams.has(id))
      const queueCount = [...lineage].reduce((total, id) => total + (queues[id]?.length ?? 0), 0)
      const activeJobCount = [...lineage].reduce((total, id) => total + (jobs[id]?.filter(job => ACTIVE_JOB.has(job.status)).length ?? 0), 0)
      return {
        ...base, state: running || queueCount || activeJobCount ? 'active' : 'current_scope_inactive',
        presence: row ? 'present' : 'absent',
        running, queue_count: queueCount, active_job_count: activeJobCount,
        descendant_ids: [...lineage].filter(id => id !== binding.dsh_session_id).sort(),
        reason_code: running || queueCount || activeJobCount ? 'host_activity_lineage_active' : 'host_activity_lineage_quiescent',
      }
    })
    return {
      schema_version: 'dsh-host-activity-snapshot.v1', scope, observed_at: observed.toISOString(),
      expires_at: new Date(observed.getTime() + 30_000).toISOString(), census_complete: complete, records,
    }
  }
}

function activityReason(error: unknown): string {
  return error instanceof Error && /^host_activity_[a-z_]+$/.test(error.message)
    ? error.message : 'host_activity_read_failed'
}
