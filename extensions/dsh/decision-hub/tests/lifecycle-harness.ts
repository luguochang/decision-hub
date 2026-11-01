import { Readable } from 'node:stream'
import { createHash } from 'node:crypto'
import { dshSessionAcceptedSchema, dshSessionCompletionSchema, dshSessionSubmitSchema, dshRunSessionLinkViewSchema, type DshSessionSubmit } from '@decision-hub/contracts-ts'
import { terminalAuthoritySchema } from '../src/host/terminal-quiescence.js'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DecisionHubHostBridge, resolveHostConfig } from '../src/host/bridge.js'
import type {
  ConnectionPublic,
  HostContextPublic,
  ModelDispatchOptions,
  SessionControllerPublic,
  SessionEventLike,
  WorkspaceRegistryPublic,
  WebServerPublic,
} from '../src/host/dsh-public.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { PLUGIN_BUILD_HASH } from '../src/generated-build-identity.js'

const HOST_KEY = 'host-secret'
const CALLBACK_KEY = 'callback-secret'
const SOURCE_COMMIT = '0a53fb55bea101816fa226bb964ae2bed71c343b'
const SOURCE_VERSION = '0.1.2-alpha.2'

// Synthetic Hub state follows the actual submitted DTO and acknowledged callback.
// It never invents a successful production Link or bypasses the real DTO readers.
export const fixtureRequestBodies = new WeakMap<IncomingMessage, unknown>()
export interface FixtureLifecycleState {
  link: Record<string, unknown> | null
  submission: DshSessionSubmit | null
  submissions: Map<string, DshSessionSubmit>
}
export function captureFixtureSubmission(state: FixtureLifecycleState, req: IncomingMessage): void {
  if (req.method !== 'PUT' || !/^\/decision-hub\/v1\/runs\/[^/]+$/.test(req.url ?? '')) return
  const parsed = dshSessionSubmitSchema.safeParse(fixtureRequestBodies.get(req))
  if (parsed.success) {
    const key = `${parsed.data.deterministic_session_id}:${parsed.data.generation}`
    if (!state.submissions.has(key)) state.submissions.set(key, parsed.data)
  }
}
export function acceptFixtureSubmission(state: FixtureLifecycleState, body: unknown): void {
  const accepted = dshSessionAcceptedSchema.parse(body)
  const submitted = state.submissions.get(`${accepted.dsh_session_id}:${accepted.generation}`)
  if (!submitted || accepted.run_id !== submitted.run_id || accepted.generation !== submitted.generation
    || accepted.dsh_session_id !== submitted.deterministic_session_id) throw new Error('fixture_acceptance_identity_mismatch')
  state.submission = structuredClone(submitted)
  const now = new Date().toISOString()
  state.link = dshRunSessionLinkViewSchema.parse({
    schema_version: 'dsh-run-session-link.v1', run_id: submitted.run_id,
    dsh_session_id: submitted.deterministic_session_id, request_hash: submitted.request_hash,
    state: 'running', generation: submitted.generation,
    upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
      package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
    deadline_at: submitted.deadline_at, max_tool_calls: submitted.max_tool_calls, tool_calls_started: 0,
    model_step_timeout_ms: submitted.model_step_timeout_ms,
    accepted_at: accepted.accepted_at, last_seen_at: null, terminal_at: null, last_seq: 0,
    trace_ref: null, result_ref: null, result_hash: null, error_code: null, created_at: now, updated_at: now,
  })
}
export function acknowledgeFixtureTerminal(state: FixtureLifecycleState, body: unknown): boolean {
  const completion = dshSessionCompletionSchema.parse(body), link = state.link
  if (!link || completion.run_id !== link.run_id || completion.dsh_session_id !== link.dsh_session_id
    || completion.generation !== link.generation) return false
  state.link = dshRunSessionLinkViewSchema.parse({ ...link, state: completion.terminal_status,
    terminal_at: completion.completed_at, last_seq: completion.last_seq,
    trace_ref: completion.trace_ref, result_ref: completion.result_ref, result_hash: completion.result_hash,
    error_code: completion.error?.code ?? null, updated_at: new Date().toISOString(),
  })
  return true
}
export function fixtureTerminalAuthority(state: FixtureLifecycleState, runId: string): Response {
  const link = state.link
  if (!link || link.run_id !== runId) return Response.json({ detail: 'not found' }, { status: 404 })
  const acknowledged = link.terminal_at !== null
  const original = state.submission ?? link
  return Response.json(terminalAuthoritySchema.parse({
    schema_version: 'dsh-terminal-queue-authority.internal.v1',
    purpose: acknowledged ? 'acknowledged_terminal' : 'before_failed_terminal',
    run_id: link.run_id, dsh_session_id: link.dsh_session_id, generation: link.generation,
    request_hash: link.request_hash, original_hash: createHash('sha256').update(JSON.stringify(original)).digest('hex'),
    terminal_hash: acknowledged ? createHash('sha256').update(JSON.stringify(link)).digest('hex') : null,
    hub_status: acknowledged ? link.state : 'running', native_state: link.state,
    lease_owner: acknowledged ? null : 'synthetic-lifecycle-worker',
    lease_expires_at: acknowledged ? null : link.deadline_at, terminal_at: link.terminal_at,
    observed_at: new Date().toISOString(),
  }))
}

class FakeResponse {
  statusCode = 0
  headersSent = false
  headers: Record<string, string> = {}
  body = ''
  writeHead(status: number, headers: Record<string, string>): this {
    this.statusCode = status
    this.headers = headers
    this.headersSent = true
    return this
  }
  end(body = ''): this { this.body += body; return this }
}

function request(method: string, url: string, body?: unknown, options: {
  authorized?: boolean
  contentType?: string
  idempotencyKey?: string
  raw?: string
} = {}): IncomingMessage {
  const raw = options.raw ?? (body === undefined ? '' : JSON.stringify(body))
  const stream = Readable.from(raw.length === 0 ? [] : [Buffer.from(raw)]) as IncomingMessage
  Object.assign(stream, {
    method,
    url,
    headers: {
      ...(options.authorized === false ? {} : { 'x-decision-hub-host-key': HOST_KEY }),
      ...(raw.length === 0 ? {} : { 'content-type': options.contentType ?? 'application/json' }),
      ...(options.idempotencyKey === undefined ? {} : { 'idempotency-key': options.idempotencyKey }),
    },
  })
  fixtureRequestBodies.set(stream, body)
  return stream
}

class FakeWebServer implements WebServerPublic {
  readonly routes: Array<Parameters<WebServerPublic['register']>[0]> = []
  register(route: Parameters<WebServerPublic['register']>[0]): () => void {
    this.routes.push(route)
    return () => { this.routes.splice(this.routes.indexOf(route), 1) }
  }
  route(path: string): Parameters<WebServerPublic['register']>[0] {
    const found = this.routes.find(item => item.kind === 'exact' ? item.path === path : path.startsWith(item.path))
    if (found === undefined) throw new Error(`route not found: ${path}`)
    return found
  }
}

class FakeConnection implements ConnectionPublic {
  readonly routes = new Map<string, Parameters<ConnectionPublic['fetch']['register']>[0]>()
  fetch = {
    register: (route: Parameters<ConnectionPublic['fetch']['register']>[0]) => {
      this.routes.set(route.path, route)
      return async () => { this.routes.delete(route.path) }
    },
  }
  route(path: string): Parameters<ConnectionPublic['fetch']['register']>[0] {
    const route = this.routes.get(path)
    if (route === undefined) throw new Error(`connection route not registered: ${path}`)
    return route
  }
}

class FakeWorkspaceRegistry implements WorkspaceRegistryPublic {
  readonly workspace = { id: 'workspace-crypto-macro', sessionIds: [] as string[] }
  registered = true
  resolveByPath = vi.fn(async (_path: string) => this.registered ? this.workspace : undefined)
}

class FakeSessions implements SessionControllerPublic {
  readonly inspections = new Map<string, { meta: { id: string; createdAt?: number }; events: SessionEventLike[] }>()
  constructor(private readonly workspaces: FakeWorkspaceRegistry) {}
  create = vi.fn(async (input: { sessionId: string; workspaceId?: string; cwd?: string; agentPreset?: string }) => {
    if (!this.inspections.has(input.sessionId)) {
      this.inspections.set(input.sessionId, { meta: { id: input.sessionId, createdAt: Date.now() }, events: [] })
    }
    if (input.workspaceId === this.workspaces.workspace.id
      && !this.workspaces.workspace.sessionIds.includes(input.sessionId)) {
      this.workspaces.workspace.sessionIds.push(input.sessionId)
    }
    return { sessionId: input.sessionId, ...(input.agentPreset === undefined ? {} : { agentPreset: input.agentPreset }) }
  })
  prompt = vi.fn(async (input: { requestId: string; sessionId: string }) => {
    const inspection = this.inspections.get(input.sessionId)
    if (inspection === undefined) throw new Error('missing session')
    inspection.events.push({
      type: 'user/message', seq: inspection.events.length + 1, time: Date.now(),
      data: { content: [{ type: 'text', text: 'redacted-test-prompt' }], source: { kind: 'user', rpcId: input.requestId } },
    })
    return { accepted: true as const }
  }) as SessionControllerPublic['prompt'] & ReturnType<typeof vi.fn>
  cancel = vi.fn(() => ({ accepted: true as const }))
  inspect = vi.fn(async (sessionId: string) => {
    const inspection = this.inspections.get(sessionId)
    if (inspection === undefined) throw new Error('session not found')
    return inspection
  })
}

interface FetchState extends FixtureLifecycleState {
  prompt: ReturnType<typeof promptPayload>
  acceptedCalls: number
  terminalBodies: unknown[]
  terminalFailuresRemaining: number
  terminalGate: Promise<void> | null
  terminalRelease: (() => void) | null
  failAccepted: boolean
  callbackHeaders: Array<{ bridge: string | null; host: string | null }>
  link: Record<string, unknown> | null
  business: Record<string, unknown> | null
  businessStatusCode: number
  intakeBodies: unknown[]
  intakeKeys: Array<string | null>
  commandBodies: unknown[]
  commandHeaders: Array<{ idempotencyKey: string | null; ownerId: string | null }>
  detail: ReturnType<typeof researchDetailPayload>
  productViewStatusCode: number
}

function submitPayload(runId = 'run-1', hash = 'a'.repeat(64), deadline = new Date(Date.now() + 60_000).toISOString(), generation = 1, modelStepTimeoutMs = 60_000) {
  const ids = deterministicIds(runId, hash, generation)
  return {
    schema_version: 'dsh-session-submit.v1', run_id: runId, request_hash: hash,
    deterministic_session_id: ids.sessionId, deterministic_request_id: ids.requestId,
    workspace_ref: 'decision-hub://workspace/default', prompt_ref: `hub://runs/${runId}/prompts/${generation}`,
    agent_preset: 'decision-research', permission_ref: 'decision-hub://permissions/research-only',
    deadline_at: deadline, model_step_timeout_ms: modelStepTimeoutMs, max_tool_calls: 12, generation,
  }
}

function promptPayload(runId = 'run-1', hash = 'a'.repeat(64), generation = 1) {
  const ids = deterministicIds(runId, hash, generation)
  return {
    schema_version: 'dsh-session-prompt.v1', run_id: runId,
    dsh_session_id: ids.sessionId, request_id: ids.requestId,
    request_hash: hash, generation, prompt: '{"request":"research"}',
  }
}

function researchDetailPayload(runId = 'run-1') {
  const now = new Date().toISOString()
  return {
    schema_version: 'research-run-detail-view.v1',
    run: {
      schema_version: 'research-run-view.v2', run_id: runId, event_id: 'event-1',
      event_title: 'Central-bank statement and BTC transmission', admission_origin: 'manual', priority: 'high',
      status: 'completed', stage: 'done', runtime_id: 'dsh', profile_ref: 'decision-research.web.v1',
      current_round: 1,
      budget: { max_evidence_rounds: 3, max_tool_calls: 12, max_subagents: 6,
        total_deadline_seconds: 180, per_tool_timeout_seconds: 20,
        per_model_step_timeout_seconds: 60, max_structured_repairs: 1,
        max_estimated_cost_usd: 1 },
      coverage: null, current_action: 'Research complete.', stop_reason: null,
      latest_sequence_no: 1, artifact_id: 'artifact-1', available_at: null,
      parent_run_id: null, created_at: now, updated_at: now,
    },
    trigger_snapshot: null, decision_snapshot: null, evidence: [], rounds: [],
    causal_case: null, horizons: [],
    action_summary: {
      schema_version: 'research-action-summary.v1', recommendation: 'manual_review_required',
      headline: '研究完成，方向情景仍需人工复核。', rationale: '报告已保存，未授权交易。',
      reliability: 'reviewable', trade_authorized: false, artifact_status: 'saved',
      data_status: 'sufficient', accepted_evidence_count: 0, stale_evidence_count: 0,
      rejected_evidence_count: 0, blocking_gap_ids: [], warning_codes: [],
      supporting_evidence_refs: [], next_review_at: null,
    },
    process_steps: [{
      step_id: 'run-intake', order: 1, kind: 'intake', label: '已接收问题',
      status: 'completed', objective: '记录用户研究请求。', outcome: '研究请求已受理。',
      evidence_refs: [], tool_call_refs: [], warning_codes: [], occurred_at: now,
    }], trace: [], total_tool_calls: 0,
    total_subagents: 0, total_tokens: null, estimated_cost_usd: null,
    scheduled_recheck_at: null,
  }
}

function researchInboxPayload() {
  return {
    schema_version: 'research-inbox-view.v1',
    items: [],
    generated_at: new Date().toISOString(),
  }
}

function researchObservabilityPayload(runId = 'run-1') {
  return {
    schema_version: 'research-observability-view.v1',
    run_id: runId,
    versions: {
      domain_pack_ref: 'crypto_macro.v1',
      domain_pack_version: '1.0.0',
      role_profile_ref: 'crypto_macro.manager.v1',
      runtime_id: 'dsh',
      runtime_version: 'test',
      capability_versions: ['market.crypto_derivatives@1'],
      gate_policy_ref: 'crypto_macro.gate.v1',
      source_registry_ref: 'crypto_macro.sources@1.0.0',
    },
    readiness: [],
    source_attempts: [],
    cost: {
      status: 'unknown',
      known_subtotal_usd: null,
      total_usd: null,
      currency: 'USD',
      unknown_components: ['subscription'],
      components: [],
    },
    trajectory_ref: null,
    telemetry_ref: null,
    ledger_ref: `decision-hub://runs/${runId}`,
    generated_at: new Date().toISOString(),
  }
}

function researchValueEvaluationPayload(runId = 'run-1') {
  return {
    schema_version: 'research-value-evaluation.v1',
    evaluation_id: `evaluation-${runId}`,
    run_id: runId,
    artifact_id: null,
    evaluation_version: 'v1',
    mode: 'research_only',
    hard_coverage_ratio: 0,
    soft_coverage_ratio: 0,
    accepted_evidence_count: 0,
    rejected_evidence_count: 0,
    typed_fact_count: 0,
    baseline_status: 'unavailable',
    latency_ms: null,
    cost_status: 'unknown',
    known_cost_usd: null,
    citation_traceability: 0,
    usefulness: 'unlabeled',
    terminal_reason: 'provider_blocked',
    outcomes: [],
    evaluated_at: new Date().toISOString(),
  }
}

function fetchDouble(state: FetchState): typeof fetch {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : input.toString())
    const headers = new Headers(init?.headers)
    const key = headers.get('x-decision-hub-bridge-key')
    state.callbackHeaders.push({ bridge: key, host: headers.get('x-decision-hub-host-key') })
    if (key !== CALLBACK_KEY) return Response.json({ detail: 'unauthorized' }, { status: 401 })
    if (url.pathname === '/health/ready') return Response.json({ ready: true })
    if (url.pathname === '/v1/research/observations') {
      state.intakeBodies.push(JSON.parse(String(init?.body)))
      state.intakeKeys.push(headers.get('idempotency-key'))
      return Response.json({
        schema_version: 'research-run-queued.v1',
        event_id: 'event-intake-1', run_id: 'run-intake-1', status: 'queued',
        status_url: '/v1/runs/run-intake-1',
      }, { status: 202 })
    }
    if (url.pathname === '/v1/research/inbox') {
      if (state.productViewStatusCode !== 200) {
        return Response.json(
          { detail: 'product view unavailable' },
          { status: state.productViewStatusCode },
        )
      }
      return Response.json(researchInboxPayload())
    }
    if (url.pathname === '/v1/data-jobs') {
      return Response.json([{
        schema_version: 'data-job.v1', job_id: 'datajob-test', idempotency_key: 'datajob-key',
        request_hash: 'b'.repeat(64), requested_by: 'owner', dataset_ids: ['btc.usd.ohlcv.1h'],
        window_start_at: '2026-06-01T00:00:00Z', window_end_at: '2026-09-01T00:00:00Z',
        status: 'partial', attempt: 1, partition_refs: ['partition-1'], coverage_manifest_ref: 'coverage://test',
        errors: ['oi_historical_unavailable'], created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(), finished_at: new Date().toISOString(),
      }])
    }
    if (/^\/v1\/research\/runs\/[^/]+\/(observability|value-evaluation)$/.test(url.pathname)) {
      if (state.productViewStatusCode !== 200) {
        return Response.json(
          { detail: 'product view unavailable' },
          { status: state.productViewStatusCode },
        )
      }
      const runId = url.pathname.split('/')[4]!
      return url.pathname.endsWith('/observability')
        ? Response.json(researchObservabilityPayload(runId))
        : Response.json(researchValueEvaluationPayload(runId))
    }
    if (url.pathname === '/v1/research/runs/run-failed-1/commands') {
      state.commandBodies.push(JSON.parse(String(init?.body)))
      state.commandHeaders.push({
        idempotencyKey: headers.get('idempotency-key'),
        ownerId: headers.get('x-owner-id'),
      })
      return Response.json({
        schema_version: 'research-run-command-result.v1',
        request_id: 'dsh-retry:run-failed-1', command: 'retry',
        source_run_id: 'run-failed-1', target_run_id: 'run-retry-1',
        status: 'accepted', created_at: new Date().toISOString(),
      })
    }
    if (/^\/v1\/research\/runs\/[^/]+$/.test(url.pathname)) {
      return Response.json(state.detail)
    }
    if (url.pathname.endsWith('/prompt')) return Response.json(state.prompt)
    if (url.pathname.endsWith('/accepted')) {
      state.acceptedCalls += 1
      if (state.failAccepted) return Response.json({ detail: 'down' }, { status: 503 })
      acceptFixtureSubmission(state, JSON.parse(String(init?.body)))
      return Response.json({ accepted: true })
    }
    if (url.pathname.endsWith('/terminal')) {
      state.terminalBodies.push(JSON.parse(String(init?.body)))
      if (state.terminalGate !== null) await state.terminalGate
      if (state.terminalFailuresRemaining > 0) {
        state.terminalFailuresRemaining -= 1
        return Response.json({ detail: 'terminal callback unavailable' }, { status: 503 })
      }
      if (!acknowledgeFixtureTerminal(state, JSON.parse(String(init?.body)))) {
        return Response.json({ detail: 'terminal binding changed' }, { status: 409 })
      }
      return Response.json({ terminal: true })
    }
    if (url.pathname.endsWith('/terminal-quiescence-authority')) {
      return fixtureTerminalAuthority(state, decodeURIComponent(url.pathname.split('/')[4]!))
    }
    if (url.pathname.includes('/by-session/')) {
      return state.link === null
        ? Response.json({ detail: 'not found' }, { status: 404 })
        : Response.json(state.link)
    }
    if (/\/v1\/dsh\/sessions\/[^/]+$/.test(url.pathname)) {
      return state.link === null
        ? Response.json({ detail: 'not found' }, { status: 404 })
        : Response.json(state.link)
    }
    if (url.pathname.endsWith('/business-status')) {
      if (state.businessStatusCode !== 200) {
        return Response.json({ detail: 'business status unavailable' }, { status: state.businessStatusCode })
      }
      return state.business === null
        ? Response.json({ detail: 'not found' }, { status: 404 })
        : Response.json(state.business)
    }
    return Response.json({ detail: 'not found' }, { status: 404 })
  }) as typeof fetch
}

function harness(
  overrides: Partial<Parameters<typeof resolveHostConfig>[0]> = {},
  options: { workspaceRegistered?: boolean } = {},
) {
  const webServer = new FakeWebServer()
  const workspaceRegistry = new FakeWorkspaceRegistry()
  workspaceRegistry.registered = options.workspaceRegistered ?? true
  const sessions = new FakeSessions(workspaceRegistry)
  const connection = new FakeConnection()
  const listeners = new Map<string, Function[]>()
  const logs: string[] = []
  const state: FetchState = {
    submission: null, submissions: new Map(), prompt: promptPayload(), acceptedCalls: 0, terminalBodies: [], failAccepted: false,
    terminalFailuresRemaining: 0, terminalGate: null, terminalRelease: null,
    callbackHeaders: [], link: null, business: null, businessStatusCode: 200,
    intakeBodies: [], intakeKeys: [], commandBodies: [], commandHeaders: [],
    detail: researchDetailPayload(), productViewStatusCode: 200,
  }
  const ctx: HostContextPublic = {
    webServer, connection, sessionController: sessions, workspaceRegistry,
    on: ((event: string, listener: Function) => {
      const values = listeners.get(event) ?? []
      values.push(listener)
      listeners.set(event, values)
      return () => { values.splice(values.indexOf(listener), 1) }
    }) as HostContextPublic['on'],
    logger: {
      info: message => logs.push(message),
      warn: message => logs.push(String(message)),
      error: message => logs.push(String(message)),
    },
  }
  const bridge = new DecisionHubHostBridge(ctx, resolveHostConfig({
    inboundKey: HOST_KEY, callbackKey: CALLBACK_KEY,
    defaultWorkspaceCwd: '/safe/workspace', callbackAttempts: 2,
    sourceCommit: SOURCE_COMMIT, sourceVersion: SOURCE_VERSION, pluginBuildHash: PLUGIN_BUILD_HASH,
    fetchImpl: fetchDouble(state), ...overrides,
  }))
  bridge.start()
  const call = async (req: IncomingMessage) => {
    captureFixtureSubmission(state, req)
    const res = new FakeResponse()
    await webServer.route(new URL(req.url ?? '/', 'http://x').pathname).handler(req, res as unknown as ServerResponse)
    return { status: res.statusCode, json: JSON.parse(res.body), raw: res.body }
  }
  const emit = async (event: string, ...args: unknown[]) => {
    for (const listener of listeners.get(event) ?? []) await listener(...args)
  }
  const browserStatus = async (sessionId?: string, method = 'GET') => {
    const suffix = sessionId === undefined ? '' : `?session_id=${encodeURIComponent(sessionId)}`
    return connection.route('/api/decision-hub/status').fetch(new Request(`http://dsh.local/api/decision-hub/status${suffix}`, { method }))
  }
  const browserRunStatus = async (runId: string, method = 'GET') => {
    return connection.route('/api/decision-hub/status').fetch(new Request(`http://dsh.local/api/decision-hub/status?run_id=${encodeURIComponent(runId)}`, { method }))
  }
  const browserReport = async (runId?: string, method = 'GET') => {
    const suffix = runId === undefined ? '' : `?run_id=${encodeURIComponent(runId)}`
    return connection.route('/api/decision-hub/report').fetch(new Request(`http://dsh.local/api/decision-hub/report${suffix}`, { method }))
  }
  const browserInbox = async (limit?: number, method = 'GET') => {
    const suffix = limit === undefined ? '' : `?limit=${encodeURIComponent(limit)}`
    return connection.route('/api/decision-hub/inbox').fetch(
      new Request(`http://dsh.local/api/decision-hub/inbox${suffix}`, { method }),
    )
  }
  const browserDataJobs = async (limit?: number, method = 'GET') => {
    const suffix = limit === undefined ? '' : `?limit=${encodeURIComponent(limit)}`
    return connection.route('/api/decision-hub/data-jobs').fetch(
      new Request(`http://dsh.local/api/decision-hub/data-jobs${suffix}`, { method }),
    )
  }
  const browserObservability = async (runId?: string, method = 'GET') => {
    const suffix = runId === undefined ? '' : `?run_id=${encodeURIComponent(runId)}`
    return connection.route('/api/decision-hub/observability').fetch(
      new Request(`http://dsh.local/api/decision-hub/observability${suffix}`, { method }),
    )
  }
  const browserValueEvaluation = async (runId?: string, method = 'GET') => {
    const suffix = runId === undefined ? '' : `?run_id=${encodeURIComponent(runId)}`
    return connection.route('/api/decision-hub/value-evaluation').fetch(
      new Request(`http://dsh.local/api/decision-hub/value-evaluation${suffix}`, { method }),
    )
  }
  return {
    ctx, bridge, call, browserStatus, browserRunStatus, browserReport, browserInbox, browserObservability,
    browserValueEvaluation, browserDataJobs, sessions, workspaceRegistry, state, logs, emit,
    modelStream: (options: ModelDispatchOptions, next: () => AsyncIterable<unknown>) =>
      listeners.get('llm/stream')![0]!(options, next) as AsyncIterable<unknown>,
  }
}


export { harness, request, submitPayload, promptPayload }
