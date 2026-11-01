import type { Context } from '@deepseek-ai/cordis'
import {
  errorProvenanceSchema,
  researchCapabilityQuerySchema,
  researchCapabilityInvocationSchema,
  researchCapabilityResultSchema,
} from '@decision-hub/contracts-ts'
import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import z from '@deepseek-ai/schemastery'
import { DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS, loadResearchQueryCatalog, renderResearchQueryCatalog, type ResearchQueryCatalog } from './query-catalog.js'
import { baselinePost, BaselineTransportError } from './host/baseline-transport.js'

export const name = 'decision-hub-research-tool'
export const inject = ['tools']
export const RESEARCH_TOOL_NAME = 'decision_hub_research'
export const DEFAULT_RESEARCH_TOOL_TIMEOUT_MS = 65_000
// research execution_budget.total_deadline_seconds has a canonical maximum of
// 3600. The SDK accepts only one static number on a shared registration; the
// executor below applies each authenticated absolute Task ceiling (or legacy
// transport budget), including preparation and the complete response body.
const MAX_MANAGED_RESEARCH_OBSERVATION_MS = 3_600_000
// Match the Runtime attestation reader's existing embedded-JSON bound.
export const MAX_CANONICAL_RESEARCH_RESULT_BYTES = 1_000_000

/**
 * The Gateway ledger is keyed by the root DSH session, while DSH gives every
 * subagent its own session id.  Keep this lookup at the trusted tool boundary
 * so a child can contribute evidence to its parent's audited run without
 * allowing the model to provide or override an id.
 *
 * DSH persists the lineage on `session.header.parentSession`.  The registry is
 * used to walk that lineage to the root.  A missing parent is handled by
 * returning the durable parent id as a best effort; this keeps the transport
 * honest and lets the Gateway return a typed session error instead of silently
 * attributing evidence to the child.
 */
interface AgentLineageLike {
  readonly id: string
  readonly session?: {
    readonly header?: { readonly parentSession?: string }
  }
  readonly ctx?: {
    readonly agents?: {
      get(id: string): AgentLineageLike | undefined
    }
  }
}

export function resolveResearchSessionId(agent: AgentLineageLike): string {
  let current = agent
  const visited = new Set<string>()
  while (true) {
    const currentId = String(current.id)
    if (visited.has(currentId)) return currentId
    visited.add(currentId)

    const parentId = current.session?.header?.parentSession
    if (typeof parentId !== 'string' || parentId.length === 0 || parentId === currentId) {
      return currentId
    }
    const parent = current.ctx?.agents?.get(parentId)
    if (parent === undefined) return parentId
    current = parent
  }
}

export interface ResearchToolConfig {
  serviceUrl: string
  authKey: string
  timeoutMs: number
  catalogTimeoutMs?: number
  fetchImpl?: typeof fetch
  queryCatalog?: ResearchQueryCatalog
  catalogForAgent?: (agent: AgentLineageLike, signal?: AbortSignal) => Promise<ResearchQueryCatalog>
  catalogSnapshotForAgent?: (agent: AgentLineageLike) => ResearchQueryCatalog | undefined
}

export const Config: z<Omit<ResearchToolConfig, 'fetchImpl' | 'queryCatalog'>> = z.object({
  serviceUrl: z.string(),
  authKey: z.string(),
  // Legacy/replay observation only. Managed live calls retain the original
  // Task deadline across preparation and durable admission; the Gateway's
  // individual capability deadlines remain unchanged.
  timeoutMs: z.natural().min(1).default(DEFAULT_RESEARCH_TOOL_TIMEOUT_MS),
  catalogTimeoutMs: z.natural().min(1).max(DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS).default(DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS),
})

const parameters = {
  // Compatibility only. Identity belongs to the runtime, never to model text.
  request_id: {
    type: 'string',
    description: 'Omit this field. The runtime supplies the unique tool-call identifier.',
  },
  capability_id: {
    type: 'string', required: true,
    description: 'Use an exact capability_id listed in this tool query catalog and allowed by the research plan.',
  },
  requirement_id: { type: 'string', required: true },
  query: { type: 'string', required: true },
  target_url: {
    oneOf: [{ type: 'string' }, { type: 'null' }], required: true,
    description: 'For web.fetch use the exact approved source URL. For market.* and data.historical use null; those adapters own their endpoint URLs.',
  },
  symbols: { type: 'array', items: { type: 'string' }, required: true },
  fields: {
    type: 'array', items: { type: 'string' }, required: true,
    description: 'Metric names supported together by a route in the tool query catalog. Do not request response metadata fields. For document fetch/readback use [].',
  },
  allowed_domains: {
    type: 'array', items: { type: 'string' }, required: true,
    description: 'Exact allowed API hosts from the query catalog, or [] for server-owned route selection. A parent website domain is not interchangeable with its API host.',
  },
  max_results: {
    type: 'integer', required: true,
    description: 'Number of evidence items, from 1 to 20. Historical candle count is determined by the requested date window, not this field.',
  },
  max_cost_usd: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
  event_id: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  event_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  window_start_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  window_end_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  requested_event_offsets: { type: 'array', items: { type: 'string' } },
  evidence_ref: { type: 'string', description: 'For document.readback, use the exact evidence_id already returned in this Session with the same requirement_id and retained original. Never invent a reference.' },
  content_ref: { type: 'string', description: 'Optional: exact retained content_ref of that evidence; omit if unavailable. This is not a source URL.' },
  round: { type: 'integer', required: true },
  mode: { type: 'string', enum: ['live', 'replay'], required: true },
  observed_at: { type: 'string', required: true },
  cutoff_at: { type: 'string', required: true },
} as const

export function createResearchToolDefinition(options: ResearchToolConfig): ToolDefinition {
  const serviceUrl = new URL(options.serviceUrl)
  if (!['http:', 'https:'].includes(serviceUrl.protocol)) {
    throw new Error('decision_hub_research_service_url_invalid')
  }
  if (options.authKey.length === 0) throw new Error('decision_hub_research_auth_key_missing')
  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1) {
    throw new Error('decision_hub_research_timeout_invalid')
  }
  const fetchImpl = options.fetchImpl ?? fetch
  const live = options.queryCatalog?.scope?.status === 'bound'
    && options.queryCatalog.scope.execution_mode === 'live'
  const timeFields = ['mode', 'round', 'observed_at', 'cutoff_at']
  const visibleParameters = Object.fromEntries(Object.entries(parameters)
    .filter(([key]) => !live || !timeFields.includes(key))
    // DSH validates against the registered executor before invoking it. Shared
    // registration must accept both managed-live and explicit replay input.
    // The final model schema still requires these fields for replay.
    .map(([key, value]) => {
      const { required: _required, ...optional } = { required: undefined, ...value }
      return [key, options.catalogForAgent && timeFields.includes(key) ? optional : value]
    }))
  return defineTool({
    name: RESEARCH_TOOL_NAME,
    description: (
      'Execute one audited Decision Hub research capability. Session identity is supplied '
      + 'by the trusted DSH execution context; never include or infer a Session identifier.'
      + (live ? ' Online execution times, mode and round are supplied by the trusted gateway. '
        + 'Do not supply observed_at, cutoff_at, deadline_at, mode or round. '
        + 'For historical data, specify the requested historical window instead.' : '')
      + (options.queryCatalog ? renderResearchQueryCatalog(options.queryCatalog) : '')
    ),
    parameters: options.queryCatalog?.scope && options.queryCatalog.capabilities.length > 0
      ? { ...visibleParameters, capability_id: { ...parameters.capability_id,
        enum: options.queryCatalog.capabilities.map(item => item.capability_id) } }
      : visibleParameters,
    output: {
      // Canonical Zod validation below is generated from the schema source. The
      // DSH output layer uses explicit JsonValue to avoid maintaining a second schema.
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    finalizeContent(_exec, result) {
      // The registry invokes this after generic spill handling. Attestation
      // needs the complete validated value, never a head/tail preview. Failed
      // and cancelled outcomes keep their registry-owned error content.
      if (result.isError) return undefined
      try {
        const canonical = researchCapabilityResultSchema.safeParse(result.value)
        if (!canonical.success) return undefined
        const text = JSON.stringify(result.value)
        if (Buffer.byteLength(text, 'utf8') > MAX_CANONICAL_RESEARCH_RESULT_BYTES) return undefined
        return [{ type: 'text', text }]
      } catch {
        // This last-mile callback is total: preserve the original outcome.
        return undefined
      }
    },
    timeoutMs: options.catalogForAgent || live ? MAX_MANAGED_RESEARCH_OBSERVATION_MS : options.timeoutMs,
    async execute(args, exec) {
      if (exec.agent === undefined) throw new Error('decision_hub_research_agent_required')
      if (Object.prototype.hasOwnProperty.call(args, 'research_session_id')) {
        throw new Error('decision_hub_research_session_identity_forbidden')
      }
      const rawArgs = args as Record<string, unknown>
      // A model may copy the run-level request_id into every call. Using that
      // as an idempotency key silently replays spot data for unrelated daily,
      // macro and derivatives requests. Bind identity to the actual DSH call.
      const requestId = String(exec.callId)
      if (!requestId || requestId === 'undefined') {
        throw new Error('decision_hub_research_call_identity_required')
      }
      const agent = exec.agent as unknown as AgentLineageLike
      const enteredAt = Date.now()
      const cached = options.catalogSnapshotForAgent?.(agent) ?? options.queryCatalog
      let expiresAt = enteredAt + (options.catalogForAgent || live ? MAX_MANAGED_RESEARCH_OBSERVATION_MS : options.timeoutMs)
      let taskDeadline = false
      const controller = new AbortController(), signal = AbortSignal.any([exec.signal, controller.signal])
      let timer: ReturnType<typeof setTimeout> | undefined
      const failure = () => new Error(exec.signal.aborted ? 'decision_hub_research_cancelled'
        : taskDeadline ? 'decision_hub_research_task_deadline_elapsed' : 'decision_hub_research_transport_timeout')
      const arm = () => {
        if (timer !== undefined) clearTimeout(timer)
        if (exec.signal.aborted || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw failure()
        timer = setTimeout(() => controller.abort(), expiresAt - Date.now())
      }
      const narrow = (catalog: ResearchQueryCatalog) => {
        if (catalog.scope && catalog.scope.dsh_session_id !== resolveResearchSessionId(agent)) {
          throw new Error('research_catalog_scope_identity_mismatch')
        }
        if (catalog.scope?.status === 'bound' && catalog.scope.execution_mode === 'live') {
          const deadline = catalog.scope.deadline_at
          if (typeof deadline !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(deadline)
            || !Number.isFinite(Date.parse(deadline))) throw new Error('decision_hub_research_task_deadline_invalid')
          expiresAt = Math.min(expiresAt, Date.parse(deadline))
          taskDeadline = true
        } else expiresAt = Math.min(expiresAt, enteredAt + options.timeoutMs)
        arm()
      }
      // Cached authority starts the exact deadline BEFORE catalog/preparation
      // awaits. A cold cache may perform its existing bounded authenticated GET;
      // its original server timestamp then only shortens this same lifetime.
      const bounded = async <T>(work: () => Promise<T>): Promise<T> => {
        let stop!: () => void
        try {
          if (signal.aborted || Date.now() >= expiresAt) throw failure()
          const cancelled = new Promise<never>((_resolve, reject) => {
            stop = () => reject(failure())
            signal.addEventListener('abort', stop, { once: true })
          })
          const value = await Promise.race([work(), cancelled])
          if (signal.aborted || Date.now() >= expiresAt) throw failure()
          return value
        } catch (error) {
          if (signal.aborted || Date.now() >= expiresAt) throw failure()
          throw error
        } finally { if (stop) signal.removeEventListener('abort', stop) }
      }
      try {
        if (cached) narrow(cached)
        else arm()
        const catalog = options.catalogForAgent ? await bounded(() => options.catalogForAgent!(agent, signal)) : cached
        if (catalog) {
          if (cached?.scope && catalog.scope && (cached.scope.run_id !== catalog.scope.run_id
            || cached.scope.request_hash !== catalog.scope.request_hash || cached.scope.generation !== catalog.scope.generation
            || cached.scope.deadline_at !== catalog.scope.deadline_at)) throw new Error('research_catalog_scope_identity_mismatch')
          narrow(catalog)
        }
        const managed = catalog?.scope?.status === 'bound' && catalog.scope.execution_mode === 'live'
        const executionArgs = managed ? Object.fromEntries(Object.entries(rawArgs).filter(
          ([key]) => !['mode', 'round', 'observed_at', 'requested_observed_at', 'cutoff_at', 'deadline_at'].includes(key),
        )) : rawArgs
        const query = (managed ? researchCapabilityInvocationSchema : researchCapabilityQuerySchema).parse({
          ...executionArgs, schema_version: managed ? 'research-capability-invocation.v1' : 'research-capability-query.v1',
          request_id: requestId, research_session_id: resolveResearchSessionId(agent),
        })
        const executeUrl = new URL(serviceUrl)
        if (managed) executeUrl.pathname = executeUrl.pathname.replace(/\/execute$/, '/invoke-live')
        let body: string, status: number
        if (managed && !options.fetchImpl) {
          // Own real request/body closure and the original absolute ceiling;
          // global fetch would add an unrelated 300s response-headers deadline.
          status = 0
          try {
            body = await baselinePost({ url: executeUrl, authKey: options.authKey, body: JSON.stringify(query), signal,
              deadlineAtMs: expiresAt, allowErrorResponse: true, onStatus: value => { status = value } })
          } catch (error) {
            if (signal.aborted || Date.now() >= expiresAt) throw failure()
            throw new Error(error instanceof BaselineTransportError
              ? error.code.replace(/^research_baseline_/, 'decision_hub_research_') : 'decision_hub_research_transport_unavailable')
          }
        } else {
          const response = await bounded(() => fetchImpl(executeUrl, { method: 'POST',
            headers: { 'content-type': 'application/json', 'x-decision-hub-bridge-key': options.authKey },
            body: JSON.stringify(query), signal, redirect: 'error' }))
          status = response.status
          body = await bounded(() => response.text())
        }
        if (signal.aborted || Date.now() >= expiresAt) throw failure()
        if (Buffer.byteLength(body, 'utf8') > MAX_CANONICAL_RESEARCH_RESULT_BYTES) throw new Error('decision_hub_research_oversize')
        const payload: unknown = JSON.parse(body)
        if (status < 200 || status >= 300) {
          const provenance = errorProvenanceSchema.safeParse(payload)
          throw new Error(provenance.success ? `${provenance.data.error_code} provenance=${JSON.stringify(provenance.data)}`
            : `decision_hub_research_http_${status}`)
        }
        const result = researchCapabilityResultSchema.parse(payload)
        if (result.request_id !== query.request_id || result.capability_id !== query.capability_id
          || result.evidence_candidates.some(item => item.requirement_id !== query.requirement_id)) {
          throw new Error('decision_hub_research_result_identity_mismatch')
        }
        return result
      } finally { if (timer !== undefined) clearTimeout(timer) }
    },
  })
}

type ResearchAssembly = { tools: Pick<ToolDefinition, 'name' | 'description' | 'parameters'>[] }

/** A preset is a shared composition scope, not necessarily an Agent.ctx. */
export function installScopedResearchCatalog(
  ctx: {
    tools: { register(definition: ToolDefinition): () => void }
    on(event: 'system-prompt/assemble', handler: (
      assembly: ResearchAssembly, context: { agent?: AgentLineageLike; signal?: AbortSignal },
      next: () => Promise<ResearchAssembly>,
    ) => Promise<ResearchAssembly>): unknown
  },
  config: Omit<ResearchToolConfig, 'queryCatalog'>,
): void {
  // The executor stays subject to inherited tool restrictions. Only the schemas
  // in the final public assembly are narrowed, including the very first call.
  const catalogs = new WeakMap<AgentLineageLike, ResearchQueryCatalog>()
  const catalogForAgent = async (agent: AgentLineageLike, signal?: AbortSignal) => {
    signal?.throwIfAborted()
    let catalog = catalogs.get(agent)
    if (!catalog) {
      catalog = await loadResearchQueryCatalog({ ...config, signal, sessionId: resolveResearchSessionId(agent) })
      if (catalog.capabilities.length === 0) throw new Error('research_catalog_scope_empty')
      catalogs.set(agent, catalog)
    }
    return catalog
  }
  ctx.tools.register(createResearchToolDefinition({ ...config, catalogForAgent,
    catalogSnapshotForAgent: agent => catalogs.get(agent) }))
  ctx.on('system-prompt/assemble', async (_assembly, { agent, signal }, next) => {
    const assembled = await next()
    // Diagnostic assemblies have no agent. A restricted child must never gain
    // a tool which the upstream assembly filtered out.
    if (!agent || !assembled.tools.some(tool => tool.name === RESEARCH_TOOL_NAME)) return assembled
    const queryCatalog = await catalogForAgent(agent, signal)
    const { name, description, parameters } = createResearchToolDefinition({ ...config, queryCatalog })
    return { ...assembled, tools: assembled.tools.map(tool => tool.name === RESEARCH_TOOL_NAME
      ? { name, description, parameters } : tool) }
  })
}

export async function apply(ctx: Context, config: Omit<ResearchToolConfig, 'fetchImpl' | 'queryCatalog'>): Promise<void> {
  installScopedResearchCatalog(ctx as unknown as Parameters<typeof installScopedResearchCatalog>[0], config)
}
