import { Readable } from 'node:stream'
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
import { fixtureRequestBodies, captureFixtureSubmission, acceptFixtureSubmission, acknowledgeFixtureTerminal, fixtureTerminalAuthority, type FixtureLifecycleState } from './lifecycle-harness.js'

const HOST_KEY = 'host-secret'
const CALLBACK_KEY = 'callback-secret'
const SOURCE_COMMIT = '0a53fb55bea101816fa226bb964ae2bed71c343b'
const SOURCE_VERSION = '0.1.2-alpha.2'

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
    if (url.pathname.endsWith('/prompt')) {
      if (init?.signal?.aborted) throw new DOMException('fixture request aborted', 'AbortError')
      return Response.json(state.prompt)
    }
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
    ctx, call, browserStatus, browserRunStatus, browserReport, browserInbox, browserObservability,
    browserValueEvaluation, browserDataJobs, sessions, workspaceRegistry, state, logs, emit,
    modelStream: (options: ModelDispatchOptions, next: () => AsyncIterable<unknown>) =>
      listeners.get('llm/stream')![0]!(options, next) as AsyncIterable<unknown>,
  }
}


// Strict original Link DTO and fresh public activity are explicit test fixtures;
// they neither represent Provider cleanup nor bypass the actual Hub parser.
function runningLink(payload: ReturnType<typeof submitPayload>) {
  const now = new Date().toISOString()
  return {
    schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
    dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
    state: 'running', generation: payload.generation,
    upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
      package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
    deadline_at: payload.deadline_at, max_tool_calls: 12, tool_calls_started: 0,
    model_step_timeout_ms: payload.model_step_timeout_ms,
    accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 0,
    trace_ref: null, result_ref: null, result_hash: null, error_code: null, created_at: now, updated_at: now,
  }
}

function installQuietActivity(app: ReturnType<typeof harness>, sessionId: string) {
  const parent = { id: sessionId, status: 'idle' as const, cancel: vi.fn(), whenIdle: async () => undefined }
  Object.assign(app.ctx, { agents: { get: (id: string) => id === sessionId ? parent : undefined },
    get: (name: string) => name === 'subagents' ? { drainContinuableDescendants: async () => undefined }
      : { list: () => [], kill: () => 'already-finished', wait: async () => { throw new Error('no fixture jobs') } },
  })
  const list: NonNullable<SessionControllerPublic['list']> = async () => ({ items: [{ sessionId, running: false }] })
  const control: NonNullable<SessionControllerPublic['control']> = async function* () {
    const lastSeq = app.sessions.inspections.get(sessionId)!.events.reduce((seq, event) => Math.max(seq, event.seq), 0)
    yield { type: 'baseline', value: { queues: { [sessionId]: [] }, jobs: { [sessionId]: [] },
      projections: { [sessionId]: { asOfSeq: lastSeq } } } }
  }
  Object.assign(app.sessions, { list, control })
}

describe('Decision Hub DSH Host bridge', () => {
  beforeEach(() => { vi.useRealTimers() })

  it.each([undefined, 'compaction', 'session-title'] as const)('current activity fences submit and every model purpose: %s', async purpose => {
    const app = harness({ defaultWorkspaceCwd: process.cwd() })
    const payload = submitPayload()
    const now = new Date().toISOString()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'unknown', generation: payload.generation,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: null, max_tool_calls: 12, tool_calls_started: 0,
      accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: now, updated_at: now,
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'failed', gate_status: null,
      coverage_status: null, hard_coverage_ratio: null, stop_reason_code: null,
      stop_reason_detail: null, failures: [],
    }
    const frozenLink = JSON.stringify(app.state.link)
    const controller = app.sessions as SessionControllerPublic
    controller.list = vi.fn(async () => ({ items: [] }))
    controller.control = async function* () { yield { type: 'baseline', value: { queues: {}, jobs: {} } } }
    const initialScope = await app.call(request('GET', '/decision-hub/v1/current-activity'))
    const snapshot = await app.call(request('POST', '/decision-hub/v1/current-activity', {
      schema_version: 'dsh-host-activity-request.v1', records: [{
        run_id: payload.run_id, study_id: 'study-one', dsh_session_id: payload.deterministic_session_id,
        request_hash: payload.request_hash, generation: payload.generation, link_fingerprint: 'b'.repeat(64),
      }],
    }))
    expect(snapshot.status).toBe(200)
    expect(snapshot.json.scope).toEqual(initialScope.json.scope)
    expect(snapshot.json.records[0]).toMatchObject({ state: 'current_scope_inactive', presence: 'absent', terminal_state: 'unknown' })
    const submission = await app.call(request('PUT', `/decision-hub/v1/runs/${payload.run_id}`, payload))
    expect(submission).toMatchObject({ status: 409, json: { error: { code: 'host_activity_lineage_fenced' } } })
    const next = vi.fn(async function* () { yield 'provider' })
    const chunks = []
    for await (const chunk of app.modelStream({
      sessionId: payload.deterministic_session_id, provider: 'test', model: 'test',
      ...(purpose === undefined ? {} : { purpose }),
    }, next)) chunks.push(chunk)
    expect(chunks).toMatchObject([{ type: 'finish', reason: { failure: { code: 'HOST_ACTIVITY_LINEAGE_FENCED' } } }])
    expect(next).not.toHaveBeenCalled()
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
    expect(app.sessions.cancel).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
    expect(app.state.acceptedCalls).toBe(0)
    expect(JSON.stringify(app.state.link)).toBe(frozenLink)
  })

  it.each([
    { config: {}, authorized: false, status: 401, code: 'host_unauthorized' },
    { config: { runtimeMode: 'replay' as const }, authorized: true, status: 409, code: 'host_replay_read_only' },
    { config: { pluginBuildHash: '0'.repeat(64) }, authorized: true, status: 503, code: 'host_version_incompatible' },
  ])('current activity rejects $code before reading scope or installing a fence', async scenario => {
    const app = harness(scenario.config)
    const response = await app.call(request('GET', '/decision-hub/v1/current-activity', undefined, { authorized: scenario.authorized }))
    expect(response).toMatchObject({ status: scenario.status, json: { error: { code: scenario.code } } })
    expect(app.workspaceRegistry.resolveByPath).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })

  it.each(['DSH_MODEL_CONFIGURATION_CHANGED_DURING_RUN', 'DSH_MODEL_BINDING_UNAVAILABLE'])('preserves the structured model binding refusal: %s', async code => {
    const app = harness()
    const payload = submitPayload()
    installQuietActivity(app, payload.deterministic_session_id)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    app.sessions.inspections.get(payload.deterministic_session_id)!.events.push({
      type: 'turn/end', seq: 2, time: Date.now(),
      data: { reason: { kind: 'error', error: { code, message: 'bounded failure' } } },
    })
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(result.json).toMatchObject({ state: 'failed', error_code: code.toLowerCase() })
    expect(app.state.terminalBodies[0]).toMatchObject({ error: { code: code.toLowerCase(), retryable: false } })
  })

  it('reports readiness only when secrets and Hub are available', async () => {
    const ready = harness()
    const response = await ready.call(request('GET', '/decision-hub/v1/readiness', undefined, { authorized: false }))
    expect(response.status).toBe(200)
    expect(response.json).toMatchObject({ ready: true, hub_reachable: true, session_controller: true })

    const missing = harness({ inboundKey: '' })
    const failed = await missing.call(request('GET', '/decision-hub/v1/readiness', undefined, { authorized: false }))
    expect(failed.status).toBe(503)
    expect(failed.json.error_code).toBe('host_secret_missing')

    const mismatched = harness({ sourceCommit: 'f'.repeat(40) })
    const incompatible = await mismatched.call(request('GET', '/decision-hub/v1/readiness', undefined, { authorized: false }))
    expect(incompatible.status).toBe(503)
    expect(incompatible.json).toMatchObject({ version_compatible: false, error_code: 'host_version_incompatible' })
  })

  it('reports not ready until the configured product workspace is registered', async () => {
    const app = harness({}, { workspaceRegistered: false })

    const response = await app.call(request('GET', '/decision-hub/v1/readiness', undefined, { authorized: false }))

    expect(response.status).toBe(503)
    expect(response.json).toMatchObject({
      ready: false,
      hub_reachable: true,
      error_code: 'host_workspace_not_registered',
    })
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('rejects Run routes before Session creation when the upstream identity is incompatible', async () => {
    const app = harness({ sourceVersion: '0.0.0-incompatible' })

    const response = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', submitPayload()))

    expect(response.status).toBe(503)
    expect(response.json.error.code).toBe('host_version_incompatible')
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('keeps completed Session state separate from a rejected business Gate', async () => {
    const app = harness({ decisionDeskBaseUrl: 'http://127.0.0.1:8000/' })
    const payload = submitPayload()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'completed', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: null, max_tool_calls: 12, tool_calls_started: 6,
      accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 2,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'rejected', gate_status: 'reject',
      coverage_status: 'insufficient', hard_coverage_ratio: 1 / 6,
      stop_reason_code: 'critical_data_unavailable',
      stop_reason_detail: 'One or more hard facts remain unavailable.',
      failures: [{ capability_id: 'market.cross_asset', error_code: 'research_capability_timeout',
        origin: 'transport', cause_code: 'deadline', retryable: true }],
    }
    const response = await app.browserStatus(payload.deterministic_session_id)
    const body = await response.json()
    expect(response.status).toBe(200)
    expect(body).toEqual({
      schema_version: 'dsh-browser-status.v1', ready: true, run_id: payload.run_id,
      runtime_mode: 'live', interaction_mode: 'interactive',
      dsh_session_id: payload.deterministic_session_id, state: 'completed', error_code: null,
      decision_desk_url: `http://127.0.0.1:8000/?run_id=${payload.run_id}`,
      business: app.state.business,
    })
    expect(body).not.toHaveProperty('request_hash')
    expect(body).not.toHaveProperty('upstream_identity')
    expect(app.state.callbackHeaders.every(item => item.bridge === CALLBACK_KEY && item.host === null)).toBe(true)
  })

  it('projects replay mode as read-only for the browser without changing Hub state', async () => {
    const app = harness({ runtimeMode: 'replay' })
    const response = await app.browserStatus()
    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({
      runtime_mode: 'replay', interaction_mode: 'read_only', run_id: null, dsh_session_id: null,
    })
  })

  it('proxies one contract-validated research report to the official DSH client', async () => {
    const app = harness()

    const response = await app.browserReport('run-1')
    const body = await response.json()

    expect(response.status).toBe(200)
    expect(body).toMatchObject({
      schema_version: 'research-run-detail-view.v1',
      run: { run_id: 'run-1', event_title: 'Central-bank statement and BTC transmission' },
    })
    const head = await app.browserReport('run-1', 'HEAD')
    expect(head.status).toBe(200)
    expect(await head.text()).toBe('')
  })

  it('rejects a report request without a Run id before calling Hub', async () => {
    const app = harness()

    const response = await app.browserReport()

    expect(response.status).toBe(400)
    expect(await response.json()).toEqual({
      error: { code: 'host_run_id_required', retryable: false },
    })
  })

  it('proxies canonical Inbox, observability and value evaluation views', async () => {
    const app = harness()

    const inbox = await app.browserInbox()
    expect(inbox.status).toBe(200)
    expect(await inbox.json()).toMatchObject({
      schema_version: 'research-inbox-view.v1', items: [],
    })

    const observability = await app.browserObservability('run-1')
    expect(observability.status).toBe(200)
    expect(await observability.json()).toMatchObject({
      schema_version: 'research-observability-view.v1',
      run_id: 'run-1',
      cost: { status: 'unknown', total_usd: null },
    })

    const evaluation = await app.browserValueEvaluation('run-1')
    expect(evaluation.status).toBe(200)
    expect(await evaluation.json()).toMatchObject({
      schema_version: 'research-value-evaluation.v1',
      run_id: 'run-1',
      mode: 'research_only',
    })

    const head = await app.browserInbox(undefined, 'HEAD')
    expect(head.status).toBe(200)
    expect(await head.text()).toBe('')
  })

  it('proxies DataJob coverage with explicit partial status and errors', async () => {
    const app = harness()

    const response = await app.browserDataJobs()
    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject([{
      job_id: 'datajob-test', status: 'partial', partition_refs: ['partition-1'],
      coverage_manifest_ref: 'coverage://test', errors: ['oi_historical_unavailable'],
    }])

    const head = await app.browserDataJobs(undefined, 'HEAD')
    expect(head.status).toBe(200)
    expect(await head.text()).toBe('')
  })

  it('rejects invalid Inbox limits and missing product-view Run ids', async () => {
    const app = harness()

    const invalid = await app.browserInbox(0)
    expect(invalid.status).toBe(400)
    expect(await invalid.json()).toEqual({
      error: { code: 'host_inbox_limit_invalid', retryable: false },
    })

    const invalidDataJobs = await app.browserDataJobs(101)
    expect(invalidDataJobs.status).toBe(400)
    expect(await invalidDataJobs.json()).toEqual({
      error: { code: 'host_data_jobs_limit_invalid', retryable: false },
    })

    const observability = await app.browserObservability()
    expect(observability.status).toBe(400)
    expect(await observability.json()).toEqual({
      error: { code: 'host_run_id_required', retryable: false },
    })

    const evaluation = await app.browserValueEvaluation()
    expect(evaluation.status).toBe(400)
    expect(await evaluation.json()).toEqual({
      error: { code: 'host_run_id_required', retryable: false },
    })
  })

  it('keeps product-view upstream failures explicit and retryable', async () => {
    const app = harness()
    app.state.productViewStatusCode = 503

    const inbox = await app.browserInbox()
    expect(inbox.status).toBe(503)
    expect(await inbox.json()).toEqual({
      error: { code: 'host_research_inbox_unavailable', retryable: true },
    })

    const observability = await app.browserObservability('run-1')
    expect(observability.status).toBe(503)
    expect(await observability.json()).toEqual({
      error: { code: 'host_research_observability_unavailable', retryable: true },
    })

    const evaluation = await app.browserValueEvaluation('run-1')
    expect(evaluation.status).toBe(503)
    expect(await evaluation.json()).toEqual({
      error: { code: 'host_research_value_evaluation_unavailable', retryable: true },
    })
  })

  it('admits typed research text from the DSH client into one durable Hub Run', async () => {
    const app = harness({ decisionDeskBaseUrl: 'http://127.0.0.1:8000/' })
    const intake = {
      schema_version: 'dsh-research-intake.v1',
      text: 'Evaluate the market impact of the latest Federal Reserve speech.',
      source_id: 'dsh-web', language: 'en', client_session_id: 'session-user-1',
    }

    const first = await app.call(request('POST', '/api/decision-hub/research', intake, {
      authorized: false, idempotencyKey: 'client-intake-0001',
    }))
    const duplicate = await app.call(request('POST', '/api/decision-hub/research', intake, {
      authorized: false, idempotencyKey: 'client-intake-0001',
    }))
    await app.call(request('POST', '/api/decision-hub/research', intake, {
      authorized: false, idempotencyKey: 'client-intake-0002',
    }))

    expect(first.status).toBe(202)
    expect(first.json).toEqual({
      schema_version: 'dsh-research-intake-accepted.v1',
      event_id: 'event-intake-1', run_id: 'run-intake-1', status: 'queued',
      status_url: '/v1/runs/run-intake-1',
      decision_desk_url: 'http://127.0.0.1:8000/?run_id=run-intake-1',
    })
    expect(duplicate.json.run_id).toBe('run-intake-1')
    expect(app.state.intakeBodies).toHaveLength(3)
    expect(app.state.intakeBodies[0]).toEqual({
      text: intake.text, source_id: 'dsh-web', source_type: 'manual', language: 'en',
    })
    expect(app.state.intakeKeys[0]).toMatch(/^dsh-intake-[a-f0-9]{64}$/)
    expect(app.state.intakeKeys[1]).toBe(app.state.intakeKeys[0])
    expect(app.state.intakeKeys[2]).not.toBe(app.state.intakeKeys[0])
  })

  it('routes an explicit current BTC snapshot to the snapshot evidence contract', async () => {
    const app = harness()
    const intake = {
      schema_version: 'dsh-research-intake.v1',
      text: '研究当前 BTC 快照，获取实时现货和衍生品数据。',
      source_id: 'dsh-web', language: 'zh', client_session_id: 'session-user-1',
    }

    const response = await app.call(request('POST', '/api/decision-hub/research', intake, {
      authorized: false, idempotencyKey: 'client-intake-snapshot',
    }))

    expect(response.status).toBe(202)
    expect(app.state.intakeBodies[0]).toEqual({
      text: intake.text,
      source_id: 'dsh-web',
      source_type: 'manual',
      language: 'zh',
      event_hint: 'crypto_market_snapshot',
    })
  })

  it('routes a current BTC market question without requiring the snapshot label', async () => {
    const app = harness()
    const intake = {
      schema_version: 'dsh-research-intake.v1',
      text: '请基于当前可验证的 BTC 现货与永续市场数据判断短中长期是否存在方向机会。',
      source_id: 'dsh-web', language: 'zh', client_session_id: 'session-user-2',
    }

    const response = await app.call(request('POST', '/api/decision-hub/research', intake, {
      authorized: false, idempotencyKey: 'client-intake-current-market',
    }))

    expect(response.status).toBe(202)
    expect(app.state.intakeBodies[0]).toMatchObject({ event_hint: 'crypto_market_snapshot' })
  })

  it('rejects research intake without a client idempotency identity', async () => {
    const app = harness()
    const response = await app.call(request('POST', '/api/decision-hub/research', {
      schema_version: 'dsh-research-intake.v1', text: 'A new blank DSH session.',
      source_id: 'dsh-web', language: 'en', client_session_id: null,
    }, { authorized: false }))

    expect(response.status).toBe(400)
    expect(response.json.error.code).toBe('host_intake_idempotency_required')
    expect(app.state.intakeBodies).toHaveLength(0)
  })

  it('keeps the DSH research intake disabled in replay mode', async () => {
    const app = harness({ runtimeMode: 'replay' })
    const response = await app.call(request('POST', '/api/decision-hub/research', {
      schema_version: 'dsh-research-intake.v1', text: 'Do not execute replay as live.',
      source_id: 'dsh-web', language: 'en', client_session_id: null,
    }, { authorized: false, idempotencyKey: 'client-intake-replay' }))

    expect(response.status).toBe(409)
    expect(response.json.error.code).toBe('host_replay_read_only')
    expect(app.state.intakeBodies).toHaveLength(0)
  })

  it('forwards a typed retry command to the existing Hub command boundary', async () => {
    const app = harness({ ownerId: 'dsh-web-owner' })
    const command = {
      schema_version: 'research-run-command.v1',
      request_id: 'dsh-retry:run-failed-1',
      command: 'retry',
      reason: 'Retry failed research from the official DSH Web.',
    }

    const response = await app.call(request(
      'POST', '/api/decision-hub/research/retry', command, { authorized: false },
    ))

    expect(response.status).toBe(200)
    expect(response.json).toMatchObject({
      source_run_id: 'run-failed-1', target_run_id: 'run-retry-1',
      command: 'retry', status: 'accepted',
    })
    expect(app.state.commandBodies).toEqual([command])
    expect(app.state.commandHeaders).toEqual([{
      idempotencyKey: 'dsh-retry:run-failed-1', ownerId: 'dsh-web-owner',
    }])
  })

  it('projects research business status by Run id before the DSH session is known', async () => {
    const app = harness({ decisionDeskBaseUrl: 'http://127.0.0.1:8000/' })
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: 'run-intake-1',
      dsh_session_id: 'dsh-managed-1', request_hash: 'a'.repeat(64), state: 'running', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: null, max_tool_calls: 12, tool_calls_started: 3,
      accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 1,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'researching', gate_status: null,
      coverage_status: 'insufficient', hard_coverage_ratio: 0,
      stop_reason_code: null, stop_reason_detail: null, failures: [],
    }
    const response = await app.browserRunStatus('run-intake-1')
    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({ run_id: 'run-intake-1', dsh_session_id: 'dsh-managed-1', business: app.state.business })
  })

  it('fails closed when the Hub business summary is unavailable', async () => {
    const app = harness({ decisionDeskBaseUrl: 'http://127.0.0.1:8000/' })
    const payload = submitPayload()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'completed', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: null, max_tool_calls: 12, tool_calls_started: 6,
      accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 2,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.businessStatusCode = 503

    const response = await app.browserStatus(payload.deterministic_session_id)
    const body = await response.json()

    expect(response.status).toBe(503)
    expect(body).toMatchObject({
      ready: false, state: 'completed', error_code: 'host_business_summary_unavailable',
      business: null, decision_desk_url: `http://127.0.0.1:8000/?run_id=${payload.run_id}`,
    })
  })

  it('fails closed for auth, content type, malformed JSON, body limit and deadline', async () => {
    const app = harness({ maxBodyBytes: 32 })
    const route = '/decision-hub/v1/runs/run-1'
    expect((await app.call(request('PUT', route, submitPayload(), { authorized: false }))).json.error.code).toBe('host_unauthorized')
    expect((await app.call(request('PUT', route, {}, { contentType: 'text/plain' }))).json.error.code).toBe('host_content_type_invalid')
    expect((await app.call(request('PUT', route, undefined, { raw: '{bad', contentType: 'application/json' }))).json.error.code).toBe('host_json_invalid')
    expect((await app.call(request('PUT', route, submitPayload()))).json.error.code).toBe('host_body_too_large')

    const deadlineApp = harness()
    const expired = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() - 1_000).toISOString())
    expect((await deadlineApp.call(request('PUT', route, expired))).json.error.code).toBe('host_deadline_elapsed')
  })

  it('creates and prompts once across identical submit retries', async () => {
    const app = harness()
    const payload = submitPayload()
    const first = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const second = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    expect(first.status).toBe(202)
    expect(first.json.schema_version).toBe('dsh-session-accepted.v1')
    expect(second.status).toBe(202)
    expect(app.sessions.create).toHaveBeenCalledTimes(1)
    expect(app.sessions.create).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: payload.deterministic_session_id,
      workspaceId: app.workspaceRegistry.workspace.id,
    }))
    expect(app.sessions.create.mock.calls[0]![0]).not.toHaveProperty('cwd')
    expect(app.sessions.prompt).toHaveBeenCalledTimes(1)
    expect(app.state.acceptedCalls).toBe(2)
  })

  it('keeps accepted prompt admission on the Run deadline after the short operation signal expires', async () => {
    const app = harness({ operationTimeoutMs: 10 })
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 5_000).toISOString())
    const create = app.sessions.create.getMockImplementation()!
    app.sessions.create.mockImplementationOnce(async input => {
      const created = await create(input)
      await new Promise(resolve => setTimeout(resolve, 30))
      return created
    })

    const response = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))

    expect(response.status).toBe(202)
    expect(response.json.schema_version).toBe('dsh-session-accepted.v1')
    expect(app.state.acceptedCalls).toBe(1)
    expect(app.sessions.prompt).toHaveBeenCalledOnce()
  })

  it('fails closed before Session creation when the configured workspace is not registered', async () => {
    const app = harness({}, { workspaceRegistered: false })

    const response = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', submitPayload()))

    expect(response.status).toBe(503)
    expect(response.json.error.code).toBe('host_workspace_not_registered')
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('attaches an existing managed Session that is missing from the registered workspace', async () => {
    const app = harness()
    const payload = submitPayload()
    app.sessions.inspections.set(payload.deterministic_session_id, {
      meta: { id: payload.deterministic_session_id, createdAt: Date.now() },
      events: [],
    })

    const response = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))

    expect(response.status).toBe(202)
    expect(app.sessions.create).toHaveBeenCalledOnce()
    expect(app.workspaceRegistry.workspace.sessionIds).toContain(payload.deterministic_session_id)
    expect(app.sessions.prompt).toHaveBeenCalledOnce()
  })

  it('continues a completed Run with a distinct turn in the same DSH Session', async () => {
    const app = harness()
    const first = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', first))
    const inspection = app.sessions.inspections.get(first.deterministic_session_id)!
    inspection.events.push(
      { type: 'assistant/message', seq: 2, time: Date.now(), data: { message: { content: [{ type: 'text', text: '{"request_id":"req-domain","schema_version":"research-synthesis-candidate.v1","causal_case":null,"horizons":[]}' }] } } },
      { type: 'turn/end', seq: 3, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))).status).toBe(200)

    const second = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 2)
    app.state.prompt = promptPayload('run-1', 'a'.repeat(64), 2)
    const continued = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', second))

    expect(continued.status).toBe(202)
    expect(first.deterministic_session_id).toBe(second.deterministic_session_id)
    expect(first.deterministic_request_id).not.toBe(second.deterministic_request_id)
    expect(app.sessions.create).toHaveBeenCalledTimes(1)
    expect(app.sessions.prompt).toHaveBeenCalledTimes(2)
    const promptEvents = inspection.events.filter(event => event.type === 'user/message')
    expect(promptEvents).toHaveLength(2)
    expect((promptEvents[1]!.data.source as { rpcId: string }).rpcId).toBe(second.deterministic_request_id)
  })

  it('recovers an attested result when a later continuation ends without content', async () => {
    const app = harness()
    const first = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', first))
    const inspection = app.sessions.inspections.get(first.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: { callId: 'research-1', name: 'decision_hub_research', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          callId: 'research-1',
          message: { content: [{ type: 'tool-result', toolCallId: 'research-1', isError: false }] },
        },
      },
      {
        type: 'tool/call', seq: 4, time: Date.now(),
        data: { callId: 'synthesis-1', name: 'decision_hub_synthesis_submit', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 5, time: Date.now(),
        data: {
          callId: 'synthesis-1',
          message: { content: [{ type: 'tool-result', toolCallId: 'synthesis-1', isError: false }] },
        },
      },
      { type: 'turn/end', seq: 6, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))).status).toBe(200)

    const second = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 2)
    app.state.prompt = promptPayload('run-1', 'a'.repeat(64), 2)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', second))
    inspection.events.push(
      { type: 'assistant/message', seq: 8, time: Date.now(), data: { message: { content: [{ type: 'reasoning', text: 'continuing' }] } } },
      { type: 'turn/end', seq: 9, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(status.json).toMatchObject({ state: 'completed', generation: 2 })
    expect(result.status).toBe(200)
    expect(result.json).toMatchObject({ generation: 2, finish_reason: 'completed', final_response: '' })
    const events = JSON.parse(result.json.events_json)
    expect(events.some((item: { payload: { event: { type: string; seq: number } } }) => item.payload.event.seq === 3)).toBe(true)
    expect(events.some((item: { payload: { event: { type: string; seq: number } } }) => item.payload.event.seq === 5)).toBe(true)
    expect(app.state.terminalBodies.at(-1)).toMatchObject({ terminal_status: 'completed', result_hash: result.json.result_hash })
  })

  it('does not overwrite a terminal result with a late session error', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      { type: 'assistant/message', seq: 2, time: Date.now(), data: { message: { content: [{ type: 'text', text: 'done' }] } } },
      { type: 'turn/end', seq: 3, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))).status).toBe(200)
    await app.emit('api-session/error', payload.deterministic_session_id, 'late provider close')
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1'))).json).toMatchObject({
      state: 'completed', error_code: null,
    })
  })

  it('rejects conflicting duplicate submit and unsupported refs', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const conflict = { ...submitPayload('run-1', 'b'.repeat(64)) }
    expect((await app.call(request('PUT', '/decision-hub/v1/runs/run-1', conflict))).json.error.code).toBe('host_session_conflict')
    const unsupported = { ...submitPayload('run-2', 'c'.repeat(64)), prompt_ref: 'https://example.com/prompt' }
    expect((await app.call(request('PUT', '/decision-hub/v1/runs/run-2', unsupported))).json.error.code).toBe('host_prompt_ref_unsupported')
  })

  it('does not treat idle as completed without an attested turn result', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    await app.emit('api-session/status', payload.deterministic_session_id, false)
    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(status.json.state).toBe('idle')
    expect(app.state.terminalBodies).toHaveLength(0)
  })

  it('retains cancellation pending when scoped SDK cleanup is unavailable, even after a raw abort', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    app.state.link = runningLink(payload)
    const accepted = await app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'deadline' }))
    expect(accepted.status).toBe(503)
    expect(accepted.json.error.code).toBe('host_cancel_capability_unavailable')
    expect(app.state.terminalBodies).toHaveLength(0)

    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push({
      type: 'turn/end', seq: 2, time: Date.now(),
      data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
    })
    const cancelled = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(cancelled.json).toMatchObject({ state: 'idle', error_code: 'host_cancel_pending' })
    expect(app.state.terminalBodies).toHaveLength(0)
    expect(app.sessions.cancel).not.toHaveBeenCalled()
  })

  it('retains successful research results when the product deadline aborts the turn', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: { callId: 'research-before-timeout', name: 'decision_hub_research', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          message: { content: [{
            type: 'tool-result', toolCallId: 'research-before-timeout', isError: false,
            structuredContent: { schema_version: 'research-capability-result.v1' },
          }] },
        },
      },
      {
        type: 'turn/end', seq: 4, time: Date.now(),
        data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
      },
    )

    installQuietActivity(app, payload.deterministic_session_id)

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json.state).toBe('cancelled')
    expect(result.status).toBe(200)
    expect(result.json.finish_reason).toBe('aborted')
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'cancelled',
      result_ref: 'dsh-host://runs/run-1/result',
      result_hash: result.json.result_hash,
    })
  })

  it('projects a completed deterministic turn into canonical result and one terminal callback', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      { type: 'tool/call', seq: 2, time: Date.now(), data: { callId: 'call-1', name: 'research_capability_execute', arguments: '{}' } },
      { type: 'tool/result', seq: 3, time: Date.now(), data: { callId: 'call-1', message: { content: [{ type: 'text', text: '{"schema_version":"research-capability-result.v1"}' }] } } },
      { type: 'assistant/message', seq: 4, time: Date.now(), data: { message: { content: [{ type: 'text', text: '{"request_id":"req-domain","schema_version":"research-synthesis-candidate.v1","causal_case":null,"horizons":[]}' }] } } },
      { type: 'turn/end', seq: 5, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    await app.emit('session/event', { id: payload.deterministic_session_id }, inspection.events.at(-1))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(result.status).toBe(200)
    expect(result.json.finish_reason).toBe('completed')
    const events = JSON.parse(result.json.events_json)
    expect(events.every((item: { method: string }) => item.method === 'session.event')).toBe(true)
    expect(events.some((item: { payload: { event: { type: string } } }) => item.payload.event.type === 'tool/result')).toBe(true)
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'completed', result_hash: result.json.result_hash })
  })

  it('fails closed with an evidence-only response when the model ends on reasoning only', async () => {
    const app = harness()
    const payload = submitPayload()
    installQuietActivity(app, payload.deterministic_session_id)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: { callId: 'research-1', name: 'decision_hub_research', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          callId: 'research-1',
          message: { content: [{ type: 'tool-result', toolCallId: 'research-1', isError: false }] },
        },
      },
      {
        type: 'assistant/message', seq: 4, time: Date.now(),
        data: { message: { content: [{ type: 'reasoning', text: 'ending turn' }] } },
      },
      { type: 'turn/end', seq: 5, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json).toMatchObject({ state: 'failed', error_code: 'host_terminal_result_unavailable' })
    expect(result.status).toBe(200)
    expect(result.json).toMatchObject({
      finish_reason: 'completed',
      final_response: '本轮研究未形成可发布的结构化结论；已保留经核验的证据与缺口，请查看 Decision Hub 研究报告。',
    })
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'failed', result_ref: 'dsh-host://runs/run-1/result', result_hash: result.json.result_hash,
      error: { code: 'host_terminal_result_unavailable' },
    })
  })

  it('keeps an accepted synthesis when the provider emits an empty trailing response', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: { callId: 'synthesis-1', name: 'decision_hub_synthesis_submit', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          message: {
            content: [{ type: 'tool-result', toolCallId: 'synthesis-1', content: [], isError: false }],
          },
        },
      },
      {
        type: 'turn/end', seq: 4, time: Date.now(),
        data: { reason: { kind: 'error', error: { code: 'EMPTY_RESPONSE' } } },
      },
    )

    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(result.status).toBe(200)
    expect(result.json).toMatchObject({ finish_reason: 'completed', final_response: '' })
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'completed' })
  })

  it('stops after a durable synthesis result and treats that stop as completed', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    const synthesisCall: SessionEventLike = {
      type: 'tool/call', seq: 2, time: Date.now(),
      data: { callId: 'synthesis-1', name: 'decision_hub_synthesis_submit', arguments: '{}' },
    }
    const synthesisResult: SessionEventLike = {
      type: 'tool/result', seq: 3, time: Date.now(),
      data: {
        message: {
          content: [{ type: 'tool-result', toolCallId: 'synthesis-1', content: [], isError: false }],
        },
      },
    }
    inspection.events.push(synthesisCall, synthesisResult)

    await app.emit('session/event', { id: payload.deterministic_session_id }, synthesisResult)
    await vi.waitFor(() => {
      expect(app.sessions.cancel).toHaveBeenCalledWith({
        sessionId: payload.deterministic_session_id,
      })
    })

    inspection.events.push({
      type: 'turn/end', seq: 4, time: Date.now(),
      data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
    })
    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json.state).toBe('completed')
    expect(result.json).toMatchObject({ finish_reason: 'completed' })
    expect(app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'completed' })
  })

  it('scopes synthesis stopping to the active continuation generation', async () => {
    const app = harness()
    const first = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', first))
    const inspection = app.sessions.inspections.get(first.deterministic_session_id)!
    const firstSynthesis: SessionEventLike[] = [
      { type: 'tool/call', seq: 2, time: Date.now(), data: { callId: 'synthesis-1', name: 'decision_hub_synthesis_submit' } },
      { type: 'tool/result', seq: 3, time: Date.now(), data: { message: { content: [{ type: 'tool-result', toolCallId: 'synthesis-1', isError: false }] } } },
    ]
    inspection.events.push(...firstSynthesis)
    await app.emit('session/event', { id: first.deterministic_session_id }, firstSynthesis[1])
    await vi.waitFor(() => expect(app.sessions.cancel).toHaveBeenCalledTimes(1))
    inspection.events.push({
      type: 'turn/end', seq: 4, time: Date.now(),
      data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
    })
    expect((await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))).status).toBe(200)

    const second = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 2)
    app.state.prompt = promptPayload('run-1', 'a'.repeat(64), 2)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', second))
    const research: SessionEventLike[] = [
      { type: 'tool/call', seq: 6, time: Date.now(), data: { callId: 'research-2', name: 'decision_hub_research' } },
      { type: 'tool/result', seq: 7, time: Date.now(), data: { message: { content: [{ type: 'tool-result', toolCallId: 'research-2', isError: false }] } } },
    ]
    inspection.events.push(...research)
    await app.emit('session/event', { id: second.deterministic_session_id }, research[1])
    expect(app.sessions.cancel).toHaveBeenCalledTimes(1)

    const secondSynthesis: SessionEventLike[] = [
      { type: 'tool/call', seq: 8, time: Date.now(), data: { callId: 'synthesis-2', name: 'decision_hub_synthesis_submit' } },
      { type: 'tool/result', seq: 9, time: Date.now(), data: { message: { content: [{ type: 'tool-result', toolCallId: 'synthesis-2', isError: false }] } } },
    ]
    inspection.events.push(...secondSynthesis)
    await app.emit('session/event', { id: second.deterministic_session_id }, secondSynthesis[1])
    await vi.waitFor(() => expect(app.sessions.cancel).toHaveBeenCalledTimes(2))
  })

  it('keeps a DSH turn error visible and refuses to manufacture a terminal result', async () => {
    const app = harness()
    const payload = submitPayload()
    installQuietActivity(app, payload.deterministic_session_id)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push({
      type: 'turn/end', seq: 2, time: Date.now(),
      data: { reason: { kind: 'error', error: { code: 'MODEL_SCRIPT_EXHAUSTED' } } },
    })

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json).toMatchObject({ state: 'failed', error_code: 'dsh_turn_error' })
    expect(result.status).toBe(409)
    expect(result.json.error.code).toBe('host_terminal_result_unavailable')
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'failed',
      result_ref: null,
      result_hash: null,
      error: { code: 'dsh_turn_error', message: 'DSH turn failed (MODEL_SCRIPT_EXHAUSTED)' },
    })
  })

  it('returns trusted research events when a later provider step fails', async () => {
    const app = harness()
    const payload = submitPayload()
    installQuietActivity(app, payload.deterministic_session_id)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: {
          message: {
            content: [{
              type: 'tool-call', toolCallId: 'research-1',
              toolName: 'mcp__decision_research__decision_hub_research', arguments: '{}',
            }],
          },
        },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          message: {
            content: [{
              type: 'tool-result', toolCallId: 'research-1', isError: false,
              structuredContent: { schema_version: 'research-capability-result.v1' },
            }],
          },
        },
      },
      {
        type: 'assistant/message', seq: 4, time: Date.now(),
        data: { message: { content: [{ type: 'text', text: 'unfinished model narrative' }] } },
      },
      {
        type: 'turn/end', seq: 5, time: Date.now(),
        data: { reason: { kind: 'error', error: { code: 'PI_AI_ERROR' } } },
      },
    )

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json).toMatchObject({ state: 'failed', error_code: 'dsh_turn_error' })
    expect(result.status).toBe(200)
    expect(result.json).toMatchObject({
      finish_reason: 'error', final_response: 'unfinished model narrative', last_seq: 5,
    })
    const events = JSON.parse(result.json.events_json)
    expect(events.some((item: { payload: { event: { type: string } } }) => (
      item.payload.event.type === 'tool/result'
    ))).toBe(true)
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'failed',
      result_ref: 'dsh-host://runs/run-1/result',
      result_hash: result.json.result_hash,
      error: { code: 'dsh_turn_error', message: 'DSH turn failed (PI_AI_ERROR)' },
    })
  })

  it('does not expose a partial result when the research tool itself failed', async () => {
    const app = harness()
    const payload = submitPayload()
    installQuietActivity(app, payload.deterministic_session_id)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      {
        type: 'tool/call', seq: 2, time: Date.now(),
        data: { callId: 'research-failed', name: 'decision_hub_research', arguments: '{}' },
      },
      {
        type: 'tool/result', seq: 3, time: Date.now(),
        data: {
          callId: 'research-failed',
          message: {
            content: [{
              type: 'tool-result', toolCallId: 'research-failed', isError: true,
              content: [{ type: 'text', text: 'capability failed' }],
            }],
          },
        },
      },
      {
        type: 'turn/end', seq: 4, time: Date.now(),
        data: { reason: { kind: 'error', error: { code: 'PI_AI_ERROR' } } },
      },
    )

    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    const result = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))

    expect(status.json).toMatchObject({ state: 'failed', error_code: 'dsh_turn_error' })
    expect(result.status).toBe(409)
    expect(result.json.error.code).toBe('host_terminal_result_unavailable')
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'failed', result_ref: null, result_hash: null,
    })
  })

  it('coalesces concurrent terminal reconciliation into one callback', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      { type: 'assistant/message', seq: 2, time: Date.now(), data: { message: { content: [{ type: 'text', text: '{"request_id":"req-domain","schema_version":"research-synthesis-candidate.v1","causal_case":null,"horizons":[]}' }] } } },
      { type: 'turn/end', seq: 3, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    await Promise.all([
      app.call(request('GET', '/decision-hub/v1/runs/run-1/result')),
      app.call(request('GET', '/decision-hub/v1/runs/run-1/result')),
    ])
    expect(app.state.terminalBodies).toHaveLength(1)
  })

  it('coalesces event, status, and result races even when DSH appends trailing events', async () => {
    const app = harness({ callbackAttempts: 1 })
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      { type: 'assistant/message', seq: 2, time: Date.now(), data: { message: { content: [{ type: 'text', text: '{"request_id":"req-domain","schema_version":"research-synthesis-candidate.v1","causal_case":null,"horizons":[]}' }] } } },
      { type: 'turn/end', seq: 3, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )

    let release!: () => void
    app.state.terminalGate = new Promise<void>(resolve => { release = resolve })
    const turnEnd = inspection.events.at(-1)!
    await app.emit('session/event', { id: payload.deterministic_session_id }, turnEnd)
    await vi.waitFor(() => expect(app.state.terminalBodies).toHaveLength(1))

    // DSH may append bookkeeping after turn/end. That must not create a new
    // terminal identity or a second callback while status/result are polled.
    inspection.events.push({ type: 'session/status', seq: 4, time: Date.now(), data: {} })
    const requests = Promise.all([
      app.call(request('GET', '/decision-hub/v1/runs/run-1')),
      app.call(request('GET', '/decision-hub/v1/runs/run-1/result')),
    ])
    for (let i = 0; i < 8; i += 1) await Promise.resolve()
    expect(app.state.terminalBodies).toHaveLength(1)
    release()
    const responses = await requests
    expect(responses.every(response => response.status === 200)).toBe(true)
    expect(app.state.terminalBodies).toHaveLength(1)
  })

  it('clears the terminal mutex after the first callback failure so a later poll retries', async () => {
    const app = harness({ callbackAttempts: 1 })
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push(
      { type: 'assistant/message', seq: 2, time: Date.now(), data: { message: { content: [{ type: 'text', text: '{"request_id":"req-domain","schema_version":"research-synthesis-candidate.v1","causal_case":null,"horizons":[]}' }] } } },
      { type: 'turn/end', seq: 3, time: Date.now(), data: { reason: { kind: 'completed' } } },
    )
    app.state.terminalFailuresRemaining = 1

    const first = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(first.status).toBe(503)
    expect(first.json.error.code).toBe('host_callback_unreachable')
    expect(app.state.terminalBodies).toHaveLength(1)

    const second = await app.call(request('GET', '/decision-hub/v1/runs/run-1/result'))
    expect(second.status).toBe(200)
    expect(app.state.terminalBodies).toHaveLength(2)
  })

  it('recovers an accepted durable link through GET when its native Session still exists', async () => {
    const app = harness()
    const payload = submitPayload()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'running', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: payload.deadline_at, max_tool_calls: 12, tool_calls_started: 0,
      model_step_timeout_ms: payload.model_step_timeout_ms,
      accepted_at: new Date().toISOString(), last_seen_at: null, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'researching', gate_status: null,
      coverage_status: null, hard_coverage_ratio: null, stop_reason_code: null,
      stop_reason_detail: null, failures: [],
    }
    app.sessions.inspections.set(payload.deterministic_session_id, {
      meta: { id: payload.deterministic_session_id }, events: [],
    })

    const response = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))

    expect(response.status).toBe(200)
    expect(response.json).toMatchObject({
      run_id: payload.run_id, dsh_session_id: payload.deterministic_session_id,
      generation: payload.generation,
    })
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('reports an accepted durable link whose native Session was lost without recreating it', async () => {
    const app = harness()
    const payload = submitPayload()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'running', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: payload.deadline_at, max_tool_calls: 12, tool_calls_started: 0,
      model_step_timeout_ms: payload.model_step_timeout_ms,
      accepted_at: new Date().toISOString(), last_seen_at: null, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'researching', gate_status: null,
      coverage_status: null, hard_coverage_ratio: null, stop_reason_code: null, stop_reason_detail: null, failures: [],
    }
    class ApiSessionNotFound extends Error {}
    app.sessions.inspect.mockRejectedValueOnce(
      new ApiSessionNotFound(`session "${payload.deterministic_session_id}" not found`),
    )

    const response = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))

    expect(response.status).toBe(410)
    expect(response.json.error).toEqual({
      code: 'dsh_native_session_lost', message: 'dsh_native_session_lost', retryable: false,
    })
    expect(app.sessions.create).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it.each([1_000, 600_000, null])('does not start the recovered Provider watchdog from step/start preparation alone: %s', async budget => {
    vi.useFakeTimers()
    const app = harness()
    const payload = submitPayload()
    app.state.link = {
      schema_version: 'dsh-run-session-link.v1', run_id: payload.run_id,
      dsh_session_id: payload.deterministic_session_id, request_hash: payload.request_hash,
      state: 'running', generation: 1,
      upstream_identity: { source_commit: SOURCE_COMMIT, source_version: SOURCE_VERSION,
        package_versions: { '@deepseek-ai/dsh': SOURCE_VERSION }, plugin_build_hash: PLUGIN_BUILD_HASH },
      deadline_at: payload.deadline_at, max_tool_calls: 12, tool_calls_started: 0,
      model_step_timeout_ms: budget,
      accepted_at: null, last_seen_at: null, terminal_at: null, last_seq: 0,
      trace_ref: null, result_ref: null, result_hash: null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }
    app.state.business = {
      schema_version: 'dsh-business-status.v1', status: 'researching', gate_status: null,
      coverage_status: null, hard_coverage_ratio: null, stop_reason_code: null, stop_reason_detail: null, failures: [],
    }
    app.sessions.inspections.set(payload.deterministic_session_id, { meta: { id: payload.deterministic_session_id }, events: [] })
    const recovered = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(recovered.status).toBe(200)
    expect(recovered.json).toMatchObject({
      model_step_timeout_ms: budget ?? 150_000,
      model_step_timeout_source: budget == null ? 'legacy_default' : 'durable_link',
    })
    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/start', seq: 1, time: Date.now(), data: {},
    })
    await vi.advanceTimersByTimeAsync((budget ?? 150_000) + 1)
    expect(app.sessions.cancel).not.toHaveBeenCalled()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
  })

  it('does not charge native step/start preparation to the Provider watchdog', async () => {
    vi.useFakeTimers()
    const app = harness()
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 1, 1_000)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/start', seq: 2, time: Date.now(), data: {},
    })
    await vi.advanceTimersByTimeAsync(1_001)
    expect(app.sessions.cancel).not.toHaveBeenCalled()
  })

  it('clears a model step watchdog when the step ends', async () => {
    vi.useFakeTimers()
    const app = harness()
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 1, 1_000)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/start', seq: 2, time: Date.now(), data: {},
    })
    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/end', seq: 3, time: Date.now(), data: {},
    })
    await vi.advanceTimersByTimeAsync(1_001)
    expect(app.sessions.cancel).not.toHaveBeenCalled()
  })

  it('does not relabel an owner cancellation after a step watchdog is armed', async () => {
    vi.useFakeTimers()
    const app = harness()
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 1, 1_000)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/start', seq: 2, time: Date.now(), data: {},
    })
    app.state.link = runningLink(payload)
    const pending = await app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { generation: 1, reason: 'owner' }))
    expect(pending.status).toBe(503)
    await vi.advanceTimersByTimeAsync(1_001)
    expect(app.sessions.cancel).not.toHaveBeenCalled()
    expect(app.state.terminalBodies).toEqual([])
  })

  it('surfaces callback failure without leaking keys or prompt body', async () => {
    const app = harness()
    app.state.failAccepted = true
    const response = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', submitPayload()))
    expect(response.status).toBe(503)
    expect(response.json.error.code).toBe('host_callback_unreachable')
    expect(app.sessions.create).toHaveBeenCalledOnce()
    expect(app.sessions.prompt).not.toHaveBeenCalled()
    const combined = `${response.raw}\n${app.logs.join('\n')}`
    expect(combined).not.toContain(HOST_KEY)
    expect(combined).not.toContain(CALLBACK_KEY)
    expect(combined).not.toContain('{"request":"research"}')
  })

  it('queues the prompt only after a failed durable acceptance succeeds on retry', async () => {
    const app = harness()
    const payload = submitPayload()
    app.state.failAccepted = true

    const failed = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    expect(failed.status).toBe(503)
    expect(app.sessions.prompt).not.toHaveBeenCalled()

    app.state.failAccepted = false
    const retried = await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    expect(retried.status).toBe(202)
    expect(app.sessions.create).toHaveBeenCalledTimes(1)
    expect(app.sessions.prompt).toHaveBeenCalledTimes(1)
  })
})
