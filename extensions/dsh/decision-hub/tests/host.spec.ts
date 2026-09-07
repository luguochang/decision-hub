import { Readable } from 'node:stream'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DecisionHubHostBridge, resolveHostConfig } from '../src/host/bridge.js'
import type {
  ConnectionPublic,
  HostContextPublic,
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

interface FetchState {
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
    causal_case: null, horizons: [], trace: [], total_tool_calls: 0,
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
      return Response.json({ accepted: true })
    }
    if (url.pathname.endsWith('/terminal')) {
      state.terminalBodies.push(JSON.parse(String(init?.body)))
      if (state.terminalGate !== null) await state.terminalGate
      if (state.terminalFailuresRemaining > 0) {
        state.terminalFailuresRemaining -= 1
        return Response.json({ detail: 'terminal callback unavailable' }, { status: 503 })
      }
      return Response.json({ terminal: true })
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
    prompt: promptPayload(), acceptedCalls: 0, terminalBodies: [], failAccepted: false,
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
    call, browserStatus, browserRunStatus, browserReport, browserInbox, browserObservability,
    browserValueEvaluation, sessions, workspaceRegistry, state, logs, emit,
  }
}

describe('Decision Hub DSH Host bridge', () => {
  beforeEach(() => { vi.useRealTimers() })

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

  it('rejects invalid Inbox limits and missing product-view Run ids', async () => {
    const app = harness()

    const invalid = await app.browserInbox(0)
    expect(invalid.status).toBe(400)
    expect(await invalid.json()).toEqual({
      error: { code: 'host_inbox_limit_invalid', retryable: false },
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

  it('returns the inspected cancellation state and only commits an aborted terminal event', async () => {
    const app = harness()
    const payload = submitPayload()
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const accepted = await app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { reason: 'deadline' }))
    expect(accepted.status).toBe(202)
    expect(accepted.json.state).toBe('running')
    expect(app.state.terminalBodies).toHaveLength(0)

    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
    inspection.events.push({
      type: 'turn/end', seq: 2, time: Date.now(),
      data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
    })
    const cancelled = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(cancelled.json.state).toBe('cancelled')
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({ terminal_status: 'cancelled' })
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

  it('keeps a DSH turn error visible and refuses to manufacture a terminal result', async () => {
    const app = harness()
    const payload = submitPayload()
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
      error: { code: 'dsh_turn_error' },
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
    for (let i = 0; i < 8 && app.state.terminalBodies.length === 0; i += 1) await Promise.resolve()
    expect(app.state.terminalBodies).toHaveLength(1)

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

  it('cancels a model step through the public DSH seam when its watchdog expires', async () => {
    vi.useFakeTimers()
    const app = harness()
    const payload = submitPayload('run-1', 'a'.repeat(64), new Date(Date.now() + 60_000).toISOString(), 1, 1_000)
    await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
    const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!

    await app.emit('session/event', { id: payload.deterministic_session_id }, {
      type: 'step/start', seq: 2, time: Date.now(), data: {},
    })
    await vi.advanceTimersByTimeAsync(1_001)
    expect(app.sessions.cancel).toHaveBeenCalledWith({ sessionId: payload.deterministic_session_id })

    inspection.events.push({
      type: 'turn/end', seq: 3, time: Date.now(),
      data: { reason: { kind: 'aborted', reason: { kind: 'user' } } },
    })
    const status = await app.call(request('GET', '/decision-hub/v1/runs/run-1'))
    expect(status.json).toMatchObject({ state: 'failed', error_code: 'dsh_model_step_timeout' })
    expect(app.state.terminalBodies).toHaveLength(1)
    expect(app.state.terminalBodies[0]).toMatchObject({
      terminal_status: 'failed', error: { code: 'dsh_model_step_timeout', retryable: true },
    })
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
    await app.call(request('POST', '/decision-hub/v1/runs/run-1/cancel', { reason: 'owner' }))
    await vi.advanceTimersByTimeAsync(1_001)
    expect(app.sessions.cancel).toHaveBeenCalledTimes(1)
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
