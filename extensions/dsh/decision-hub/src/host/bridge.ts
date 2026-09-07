import type { IncomingMessage, ServerResponse } from 'node:http'
import { createHash } from 'node:crypto'
import {
  dshHostReadinessSchema,
  dshResearchIntakeAcceptedSchema,
  dshResearchIntakeSchema,
  dshBrowserStatusSchema,
  dshBusinessStatusSchema,
  dshSessionAcceptedSchema,
  dshSessionStatusSchema,
  dshSessionSubmitSchema,
  researchRunCommandSchema,
  type DshBusinessStatus,
  type DshHostReadiness,
  type DshResearchIntakeAccepted,
  type DshSessionResult,
  type DshSessionStatus,
  type DshSessionSubmit,
  type DshUpstreamIdentity,
} from '@decision-hub/contracts-ts'
import { ZodError } from 'zod'
import { HOST_KEY_HEADER, secretMatches } from './auth.js'
import { BodyReadError, readJsonBody } from './body.js'
import type { HostContextPublic } from './dsh-public.js'
import { HubClient, HubClientError } from './hub-client.js'
import { safeErrorMessage } from './redaction.js'
import { SessionCorrelations, type Correlation } from './session-correlation.js'
import { hasAcceptedPrompt, projectInspection } from './session-result.js'
import { PLUGIN_BUILD_HASH } from '../generated-build-identity.js'

const SOURCE_COMMIT = '0a53fb55bea101816fa226bb964ae2bed71c343b'
const SOURCE_VERSION = '0.1.2-alpha.2'
const SHA256_PATTERN = /^[a-f0-9]{64}$/
const CLIENT_IDEMPOTENCY_PATTERN = /^[A-Za-z0-9._:-]{8,256}$/

export interface HostPluginConfig {
  hubBaseUrl?: string
  decisionDeskBaseUrl?: string
  inboundKey?: string
  callbackKey?: string
  defaultWorkspaceCwd?: string
  allowedPermissionRefs?: string[]
  clientPlugin?: boolean
  requireClientPlugin?: boolean
  maxBodyBytes?: number
  operationTimeoutMs?: number
  callbackTimeoutMs?: number
  callbackAttempts?: number
  ownerId?: string
  /** Runtime transport mode exposed to the browser so replay cannot look live. */
  runtimeMode?: 'live' | 'replay'
  sourceCommit?: string
  sourceVersion?: string
  pluginBuildHash?: string
  fetchImpl?: typeof fetch
}

export interface ResolvedHostConfig {
  hubBaseUrl: string
  decisionDeskBaseUrl: string
  inboundKey: string
  callbackKey: string
  defaultWorkspaceCwd: string
  allowedPermissionRefs: ReadonlySet<string>
  clientPlugin: boolean
  requireClientPlugin: boolean
  maxBodyBytes: number
  operationTimeoutMs: number
  callbackTimeoutMs: number
  callbackAttempts: number
  ownerId: string
  runtimeMode: 'live' | 'replay'
  sourceCommit: string
  sourceVersion: string
  pluginBuildHash: string
  fetchImpl?: typeof fetch
}

export function resolveHostConfig(config: HostPluginConfig): ResolvedHostConfig {
  return {
    hubBaseUrl: config.hubBaseUrl ?? 'http://127.0.0.1:8000',
    decisionDeskBaseUrl: config.decisionDeskBaseUrl ?? 'http://127.0.0.1:8000',
    inboundKey: config.inboundKey ?? '',
    callbackKey: config.callbackKey ?? '',
    defaultWorkspaceCwd: config.defaultWorkspaceCwd ?? process.cwd(),
    allowedPermissionRefs: new Set(config.allowedPermissionRefs ?? ['decision-hub://permissions/research-only']),
    clientPlugin: config.clientPlugin ?? false,
    requireClientPlugin: config.requireClientPlugin ?? false,
    maxBodyBytes: config.maxBodyBytes ?? 1_200_000,
    operationTimeoutMs: config.operationTimeoutMs ?? 30_000,
    callbackTimeoutMs: config.callbackTimeoutMs ?? 5_000,
    callbackAttempts: config.callbackAttempts ?? 3,
    ownerId: config.ownerId ?? 'dsh-web-owner',
    runtimeMode: config.runtimeMode ?? 'live',
    sourceCommit: config.sourceCommit ?? '',
    sourceVersion: config.sourceVersion ?? '',
    pluginBuildHash: config.pluginBuildHash ?? '',
    ...(config.fetchImpl === undefined ? {} : { fetchImpl: config.fetchImpl }),
  }
}

class HostRouteError extends Error {
  constructor(readonly code: string, readonly status: number, readonly retryable = false) { super(code) }
}

export class DecisionHubHostBridge {
  private readonly correlations = new SessionCorrelations()
  private readonly hub: HubClient
  private readonly results = new Map<string, DshSessionResult>()
  private readonly terminalSent = new Set<string>()
  private readonly terminalInFlight = new Map<string, Promise<void>>()
  private readonly modelStepTimers = new Map<string, ReturnType<typeof setTimeout>>()
  private hubReachable = false

  constructor(private readonly ctx: HostContextPublic, private readonly config: ResolvedHostConfig) {
    this.hub = new HubClient({
      baseUrl: config.hubBaseUrl,
      callbackKey: config.callbackKey,
      timeoutMs: config.callbackTimeoutMs,
      attempts: config.callbackAttempts,
      ownerId: config.ownerId,
      ...(config.fetchImpl === undefined ? {} : { fetchImpl: config.fetchImpl }),
    })
  }

  start(): () => void {
    const disposers = [
      this.ctx.webServer.register({ kind: 'exact', path: '/decision-hub/v1/readiness', handler: (req, res) => this.readiness(req, res) }),
      this.ctx.webServer.register({ kind: 'prefix', path: '/decision-hub/v1/runs', handler: (req, res) => this.runs(req, res) }),
      this.ctx.webServer.register({ kind: 'exact', path: '/api/decision-hub/research', handler: (req, res) => this.researchIntake(req, res) }),
      this.ctx.webServer.register({ kind: 'exact', path: '/api/decision-hub/research/retry', handler: (req, res) => this.researchRetry(req, res) }),
      this.ctx.connection.fetch.register({
        path: '/api/decision-hub/status',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserStatus(request),
      }),
      this.ctx.connection.fetch.register({
        path: '/api/decision-hub/report',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserReport(request),
      }),
      this.ctx.connection.fetch.register({
        path: '/api/decision-hub/inbox',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserInbox(request),
      }),
      this.ctx.connection.fetch.register({
        path: '/api/decision-hub/observability',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserObservability(request),
      }),
      this.ctx.connection.fetch.register({
        path: '/api/decision-hub/value-evaluation',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserValueEvaluation(request),
      }),
      this.ctx.on('api-session/status', (sessionId, running) => { void this.onStatus(sessionId, running) }),
      this.ctx.on('api-session/error', (sessionId, message) => { this.onError(sessionId, message) }),
      this.ctx.on('session/event', (session, event) => { this.onSessionEvent(session.id, event) }),
    ]
    void this.hub.readiness().then(value => { this.hubReachable = value })
    return () => {
      for (const timer of this.modelStepTimers.values()) clearTimeout(timer)
      this.modelStepTimers.clear()
      for (const dispose of disposers.reverse()) void dispose()
    }
  }

  private identity(): DshUpstreamIdentity {
    return {
      source_commit: this.config.sourceCommit,
      source_version: this.config.sourceVersion,
      package_versions: {
        '@deepseek-ai/dsh-api-session-controller': SOURCE_VERSION,
        '@deepseek-ai/dsh-host-webserver': SOURCE_VERSION,
      },
      plugin_build_hash: PLUGIN_BUILD_HASH,
    }
  }

  private async readiness(req: IncomingMessage, res: ServerResponse): Promise<void> {
    if (req.method !== 'GET') return this.error(res, new HostRouteError('host_method_not_allowed', 405))
    this.hubReachable = await this.hub.readiness()
    const versionCompatible = this.versionCompatible()
    const baseReady = versionCompatible && this.hubReachable
      && (!this.config.requireClientPlugin || this.config.clientPlugin)
      && this.config.inboundKey.length > 0 && this.config.callbackKey.length > 0
    let workspaceError: string | null = null
    if (baseReady) {
      try {
        await this.resolveWorkspace()
      } catch (error) {
        workspaceError = error instanceof HostRouteError
          ? error.code
          : 'host_workspace_resolution_failed'
      }
    }
    const ready = baseReady && workspaceError === null
    const body: DshHostReadiness = dshHostReadinessSchema.parse({
      schema_version: 'dsh-host-readiness.v1', ready, version_compatible: versionCompatible,
      session_controller: true, client_plugin: this.config.clientPlugin,
      hub_reachable: this.hubReachable, upstream_identity: this.identity(),
      checked_at: new Date().toISOString(),
      error_code: ready ? null : workspaceError ?? this.readinessError(),
    })
    this.json(res, ready ? 200 : 503, body)
  }

  private readinessError(): string {
    if (this.config.inboundKey.length === 0 || this.config.callbackKey.length === 0) return 'host_secret_missing'
    if (!this.versionCompatible()) return 'host_version_incompatible'
    if (!this.hubReachable) return 'host_hub_unreachable'
    if (this.config.requireClientPlugin && !this.config.clientPlugin) return 'host_client_plugin_missing'
    return 'host_not_ready'
  }

  private versionCompatible(): boolean {
    return this.config.sourceCommit === SOURCE_COMMIT
      && this.config.sourceVersion === SOURCE_VERSION
      && this.config.pluginBuildHash === PLUGIN_BUILD_HASH
      && SHA256_PATTERN.test(PLUGIN_BUILD_HASH)
  }

  private async browserStatus(request: Request): Promise<Response> {
    this.hubReachable = await this.hub.readiness()
    const url = new URL(request.url)
    const sessionId = url.searchParams.get('session_id')
    const runId = url.searchParams.get('run_id')
    let link: Awaited<ReturnType<HubClient['linkBySession']>> | null = null
    let business: DshBusinessStatus | null = null
    if (sessionId !== null && sessionId.length > 0) {
      try {
        link = await this.hub.linkBySession(sessionId)
      } catch (error) {
        if (!(error instanceof HubClientError && error.code === 'host_hub_http_404')) {
          return this.browserJson(503, {
            schema_version: 'dsh-browser-status.v1', ready: false, run_id: null,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === 'replay' ? 'read_only' : 'interactive',
            dsh_session_id: sessionId, state: null, error_code: 'host_hub_unreachable',
            decision_desk_url: this.decisionDeskUrl(null), business: null,
          }, request.method)
        }
      }
    } else if (runId !== null && runId.length > 0) {
      try {
        link = await this.hub.link(runId)
      } catch (error) {
        if (!(error instanceof HubClientError && error.code === 'host_hub_http_404')) {
          return this.browserJson(503, {
            schema_version: 'dsh-browser-status.v1', ready: false, run_id: runId,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === 'replay' ? 'read_only' : 'interactive',
            dsh_session_id: null, state: null, error_code: 'host_hub_unreachable',
            decision_desk_url: this.decisionDeskUrl(runId), business: null,
          }, request.method)
        }
      }
    }
    if (link?.run_id !== undefined) {
      try {
        business = await this.businessStatus(link.run_id)
      } catch (error) {
        if (!(error instanceof HubClientError && error.code === 'host_hub_http_404')) {
          return this.browserJson(503, {
            schema_version: 'dsh-browser-status.v1', ready: false,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === 'replay' ? 'read_only' : 'interactive',
            run_id: link.run_id, dsh_session_id: link.dsh_session_id,
            state: link.state, error_code: 'host_business_summary_unavailable',
            decision_desk_url: this.decisionDeskUrl(link.run_id), business: null,
          }, request.method)
        }
      }
    }
    const ready = this.versionCompatible() && this.hubReachable
    return this.browserJson(ready ? 200 : 503, {
      schema_version: 'dsh-browser-status.v1', ready,
      runtime_mode: this.config.runtimeMode,
      interaction_mode: this.config.runtimeMode === 'replay' ? 'read_only' : 'interactive',
      run_id: link?.run_id ?? null, dsh_session_id: link?.dsh_session_id ?? sessionId,
      state: link?.state ?? null, error_code: ready ? link?.error_code ?? null : this.readinessError(),
      decision_desk_url: this.decisionDeskUrl(link?.run_id ?? null), business,
    }, request.method)
  }

  private async businessStatus(runId: string): Promise<DshBusinessStatus | null> {
    return this.hub.businessStatus(runId)
  }

  private async browserReport(request: Request): Promise<Response> {
    const runId = new URL(request.url).searchParams.get('run_id')
    if (runId === null || runId.length === 0) {
      return this.rawBrowserJson(400, {
        error: { code: 'host_run_id_required', retryable: false },
      }, request.method)
    }
    try {
      const report = await this.hub.researchDetail(runId)
      return this.rawBrowserJson(200, report, request.method)
    } catch (error) {
      const notFound = error instanceof HubClientError && error.code === 'host_hub_http_404'
      return this.rawBrowserJson(notFound ? 404 : 503, {
        error: {
          code: notFound ? 'host_research_report_not_found' : 'host_research_report_unavailable',
          retryable: !notFound,
        },
      }, request.method)
    }
  }

  private async browserInbox(request: Request): Promise<Response> {
    try {
      const rawLimit = new URL(request.url).searchParams.get('limit')
      const limit = rawLimit === null || rawLimit.length === 0 ? 100 : Number(rawLimit)
      if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
        return this.rawBrowserJson(400, {
          error: { code: 'host_inbox_limit_invalid', retryable: false },
        }, request.method)
      }
      const view = await this.hub.researchInbox(limit)
      return this.rawBrowserJson(200, view, request.method)
    } catch (error) {
      const notFound = error instanceof HubClientError && error.code === 'host_hub_http_404'
      return this.rawBrowserJson(notFound ? 404 : 503, {
        error: {
          code: notFound ? 'host_research_inbox_not_found' : 'host_research_inbox_unavailable',
          retryable: !notFound,
        },
      }, request.method)
    }
  }

  private async browserObservability(request: Request): Promise<Response> {
    return this.browserRunProductView(request, 'observability')
  }

  private async browserValueEvaluation(request: Request): Promise<Response> {
    return this.browserRunProductView(request, 'value-evaluation')
  }

  private async browserRunProductView(
    request: Request,
    view: 'observability' | 'value-evaluation',
  ): Promise<Response> {
    const runId = new URL(request.url).searchParams.get('run_id')
    if (runId === null || runId.length === 0) {
      return this.rawBrowserJson(400, {
        error: { code: 'host_run_id_required', retryable: false },
      }, request.method)
    }
    try {
      const payload = view === 'observability'
        ? await this.hub.researchObservability(runId)
        : await this.hub.researchValueEvaluation(runId)
      return this.rawBrowserJson(200, payload, request.method)
    } catch (error) {
      const notFound = error instanceof HubClientError && error.code === 'host_hub_http_404'
      const label = view === 'observability' ? 'observability' : 'value_evaluation'
      return this.rawBrowserJson(notFound ? 404 : 503, {
        error: {
          code: notFound ? `host_research_${label}_not_found` : `host_research_${label}_unavailable`,
          retryable: !notFound,
        },
      }, request.method)
    }
  }

  private decisionDeskUrl(runId: string | null): string {
    const url = new URL(this.config.decisionDeskBaseUrl)
    if (runId !== null) url.searchParams.set('run_id', runId)
    return url.toString()
  }

  private browserJson(status: number, value: unknown, method: string): Response {
    const body = dshBrowserStatusSchema.parse(value)
    return this.rawBrowserJson(status, body, method)
  }

  private rawBrowserJson(status: number, value: unknown, method: string): Response {
    return new Response(method === 'HEAD' ? null : JSON.stringify(value), {
      status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
    })
  }

  private async runs(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      this.authorize(req)
      if (!this.versionCompatible()) throw new HostRouteError('host_version_incompatible', 503)
      const url = new URL(req.url ?? '/', 'http://localhost')
      const match = /^\/decision-hub\/v1\/runs\/([^/]+)(?:\/(cancel|result))?$/.exec(url.pathname)
      if (match === null) throw new HostRouteError('host_route_not_found', 404)
      const runId = decodeURIComponent(match[1]!)
      const action = match[2]
      if (action === 'cancel') {
        if (req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
        return await this.cancel(runId, res)
      }
      if (action === 'result') {
        if (req.method !== 'GET') throw new HostRouteError('host_method_not_allowed', 405)
        return await this.result(runId, res)
      }
      if (req.method === 'PUT') return await this.submit(runId, req, res)
      if (req.method === 'GET') return await this.status(runId, res)
      throw new HostRouteError('host_method_not_allowed', 405)
    } catch (error) {
      this.error(res, this.normalizeError(error))
    }
  }

  private async researchIntake(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      if (req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
      if (this.config.runtimeMode === 'replay') throw new HostRouteError('host_replay_read_only', 409)
      if (!this.versionCompatible()) throw new HostRouteError('host_version_incompatible', 503)
      const payload = dshResearchIntakeSchema.parse(await readJsonBody(req, this.config.maxBodyBytes))
      const clientKey = req.headers['idempotency-key']
      if (typeof clientKey !== 'string' || !CLIENT_IDEMPOTENCY_PATTERN.test(clientKey)) {
        throw new HostRouteError('host_intake_idempotency_required', 400)
      }
      const keySource = `dsh-intake.v2\0${clientKey}`
      const idempotencyKey = `dsh-intake-${createHash('sha256').update(keySource).digest('hex')}`
      const queued = await this.hub.researchIntake(payload, idempotencyKey)
      const accepted: DshResearchIntakeAccepted = dshResearchIntakeAcceptedSchema.parse({
        schema_version: 'dsh-research-intake-accepted.v1',
        event_id: queued.event_id,
        run_id: queued.run_id,
        status: queued.status,
        status_url: queued.status_url,
        decision_desk_url: this.decisionDeskUrl(queued.run_id),
      })
      this.json(res, 202, accepted)
    } catch (error) {
      this.error(res, this.normalizeError(error))
    }
  }

  private async researchRetry(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      if (req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
      if (this.config.runtimeMode === 'replay') throw new HostRouteError('host_replay_read_only', 409)
      if (!this.versionCompatible()) throw new HostRouteError('host_version_incompatible', 503)
      const command = researchRunCommandSchema.parse(
        await readJsonBody(req, this.config.maxBodyBytes),
      )
      if (command.command !== 'retry') throw new HostRouteError('host_command_unsupported', 422)
      const prefix = 'dsh-retry:'
      if (!command.request_id.startsWith(prefix) || command.request_id.length <= prefix.length) {
        throw new HostRouteError('host_retry_request_id_invalid', 422)
      }
      const runId = command.request_id.slice(prefix.length)
      const result = await this.hub.researchCommand(runId, command)
      this.json(res, 200, result)
    } catch (error) {
      this.error(res, this.normalizeError(error))
    }
  }

  private authorize(req: IncomingMessage): void {
    if (!secretMatches(req.headers[HOST_KEY_HEADER], this.config.inboundKey)) {
      throw new HostRouteError('host_unauthorized', 401)
    }
  }

  private async submit(runId: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const submit = dshSessionSubmitSchema.parse(await readJsonBody(req, this.config.maxBodyBytes))
    if (submit.run_id !== runId) throw new HostRouteError('host_run_id_mismatch', 400)
    if (Date.parse(submit.deadline_at) <= Date.now()) throw new HostRouteError('host_deadline_elapsed', 408)
    this.validateRefs(submit)
    const { correlation } = this.correlations.admit(submit)
    const signal = this.deadlineSignal(submit.deadline_at)
    let inspection: Awaited<ReturnType<typeof this.ctx.sessionController.inspect>> | null = null
    try { inspection = await this.ctx.sessionController.inspect(correlation.sessionId, signal) } catch { /* absent is create */ }
    const workspace = await this.resolveWorkspace()
    if (inspection === null || !workspace.sessionIds.includes(correlation.sessionId)) {
      const created = await this.ctx.sessionController.create({
        sessionId: correlation.sessionId,
        workspaceId: workspace.id,
        ...(submit.agent_preset === null ? {} : { agentPreset: submit.agent_preset }),
      })
      if (created.sessionId !== correlation.sessionId) throw new HostRouteError('host_session_identity_changed', 502)
    }
    const accepted = dshSessionAcceptedSchema.parse({
      schema_version: 'dsh-session-accepted.v1', run_id: runId,
      dsh_session_id: correlation.sessionId, accepted_at: new Date().toISOString(),
      generation: correlation.generation,
    })
    await this.hub.accepted(accepted)
    correlation.acceptedAt = accepted.accepted_at
    if (inspection === null || !hasAcceptedPrompt(inspection, correlation.requestId)) {
      const prompt = await this.hub.prompt(runId, correlation.generation)
      if (prompt.run_id !== runId || prompt.dsh_session_id !== correlation.sessionId
        || prompt.request_id !== correlation.requestId || prompt.request_hash !== correlation.requestHash
        || prompt.generation !== correlation.generation) {
        throw new HostRouteError('host_prompt_conflict', 409)
      }
      await this.ctx.sessionController.prompt({
        requestId: correlation.requestId, sessionId: correlation.sessionId,
        mode: 'queue', content: [{ type: 'text', text: prompt.prompt }],
      }, signal)
    }
    this.json(res, 202, accepted)
  }

  private validateRefs(submit: DshSessionSubmit): void {
    if (submit.workspace_ref !== 'decision-hub://workspace/default') {
      throw new HostRouteError('host_workspace_ref_unsupported', 422)
    }
    if (submit.prompt_ref !== `hub://runs/${submit.run_id}/prompts/${submit.generation}`) {
      throw new HostRouteError('host_prompt_ref_unsupported', 422)
    }
    if (!this.config.allowedPermissionRefs.has(submit.permission_ref)) {
      throw new HostRouteError('host_permission_ref_unsupported', 422)
    }
  }

  private async resolveWorkspace(): Promise<{
    id: string
    readonly sessionIds: readonly string[]
  }> {
    let workspace: Awaited<ReturnType<HostContextPublic['workspaceRegistry']['resolveByPath']>>
    try {
      workspace = await this.ctx.workspaceRegistry.resolveByPath(this.config.defaultWorkspaceCwd)
    } catch {
      throw new HostRouteError('host_workspace_resolution_failed', 503, true)
    }
    if (workspace === undefined) {
      throw new HostRouteError('host_workspace_not_registered', 503)
    }
    return workspace
  }

  private async status(runId: string, res: ServerResponse): Promise<void> {
    const correlation = await this.correlation(runId)
    const projected = await this.inspect(correlation)
    await this.maybeTerminal(correlation, projected.completion, projected.result)
    this.json(res, 200, projected.status)
  }

  private async result(runId: string, res: ServerResponse): Promise<void> {
    const correlation = await this.correlation(runId)
    const projected = await this.inspect(correlation)
    if (projected.result === null) throw new HostRouteError('host_terminal_result_unavailable', 409)
    await this.maybeTerminal(correlation, projected.completion, projected.result)
    this.json(res, 200, projected.result)
  }

  private async cancel(runId: string, res: ServerResponse): Promise<void> {
    const correlation = await this.correlation(runId)
    // An owner cancellation must win over a pending model-step watchdog. If
    // the timer were left armed it could relabel an explicit owner action as
    // a model timeout after the cancel request has already been accepted.
    this.clearModelStepTimer(correlation.sessionId)
    if (!['completed', 'failed', 'cancelled'].includes(correlation.state)) {
      await this.ctx.sessionController.cancel({ sessionId: correlation.sessionId })
    }
    const projected = await this.inspect(correlation)
    await this.maybeTerminal(correlation, projected.completion, projected.result)
    this.json(res, 202, projected.status)
  }

  private async correlation(runId: string): Promise<Correlation> {
    const local = this.correlations.getRun(runId)
    if (local !== undefined) return local
    try { return this.correlations.recover(await this.hub.link(runId)) } catch (error) {
      if (error instanceof HubClientError && error.code === 'host_hub_http_404') {
        throw new HostRouteError('host_run_not_found', 404)
      }
      throw error
    }
  }

  private async inspect(correlation: Correlation): Promise<ReturnType<typeof projectInspection>> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.config.operationTimeoutMs)
    try {
      const projected = projectInspection(correlation, await this.ctx.sessionController.inspect(correlation.sessionId, controller.signal))
      correlation.state = projected.status.state
      correlation.lastSeq = projected.status.last_seq
      correlation.errorCode = projected.status.error_code
      if (['completed', 'failed', 'cancelled'].includes(projected.status.state)) {
        this.clearModelStepTimer(correlation.sessionId)
      }
      return projected
    } finally { clearTimeout(timer) }
  }

  private async reconcileSession(sessionId: string): Promise<void> {
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    try {
      const projected = await this.inspect(correlation)
      await this.maybeTerminal(correlation, projected.completion, projected.result)
    } catch (error) {
      this.ctx.logger.warn(`decision-hub terminal reconciliation failed: ${safeErrorMessage(error)}`)
    }
  }

  /**
   * Enforce the canonical per-model-step budget using only public DSH seams.
   * The timer is Host-owned; DSH remains the owner of the Agent Loop and
   * emits the eventual terminal event after the cancel request is accepted.
   */
  private onSessionEvent(sessionId: string, event: { type: string }): void {
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    if (event.type === 'step/start') {
      this.clearModelStepTimer(sessionId)
      if (correlation.modelStepTimeoutTriggered) return
      const timeoutMs = correlation.modelStepTimeoutMs
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return
      const timer = setTimeout(() => {
        this.modelStepTimers.delete(sessionId)
        // Preserve this reason before cancellation causes DSH to emit an
        // aborted/user-shaped turn event. session-result.ts maps it to a
        // failed dsh_model_step_timeout completion.
        correlation.modelStepTimeoutTriggered = true
        correlation.errorCode = 'dsh_model_step_timeout'
        try {
          void Promise.resolve(this.ctx.sessionController.cancel({ sessionId }))
            .catch(error => this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${safeErrorMessage(error)}`))
        } catch (error) {
          this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${safeErrorMessage(error)}`)
        }
      }, timeoutMs)
      this.modelStepTimers.set(sessionId, timer)
      return
    }
    if (event.type === 'step/end' || event.type === 'turn/end') {
      this.clearModelStepTimer(sessionId)
      if (event.type === 'turn/end') void this.reconcileSession(sessionId)
    }
  }

  private clearModelStepTimer(sessionId: string): void {
    const timer = this.modelStepTimers.get(sessionId)
    if (timer !== undefined) {
      clearTimeout(timer)
      this.modelStepTimers.delete(sessionId)
    }
  }

  private async maybeTerminal(correlation: Correlation, completion: ReturnType<typeof projectInspection>['completion'], result: DshSessionResult | null): Promise<void> {
    if (completion === null) return
    if (result !== null) this.results.set(correlation.runId, result)
    // last_seq can advance when DSH appends bookkeeping events after
    // turn/end. It is not terminal identity. A Run generation has exactly one
    // terminal callback; keeping the key stable prevents event/status/result
    // races from issuing a second callback for the same terminal state.
    const key = `${correlation.runId}:${completion.generation}:${completion.terminal_status}`
    if (this.terminalSent.has(key)) return
    const existing = this.terminalInFlight.get(key)
    if (existing !== undefined) return await existing
    const task = this.hub.terminal(completion).then(() => {
      this.terminalSent.add(key)
      correlation.terminalAt = completion.completed_at
    })
    this.terminalInFlight.set(key, task)
    try {
      await task
    } finally {
      if (this.terminalInFlight.get(key) === task) this.terminalInFlight.delete(key)
    }
  }

  private async onStatus(sessionId: string, running: boolean): Promise<void> {
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    if (!running) this.clearModelStepTimer(sessionId)
    correlation.state = running ? 'running' : 'idle'
    const status = dshSessionStatusSchema.parse({
      schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, state: correlation.state,
      generation: correlation.generation, last_seq: correlation.lastSeq,
      observed_at: new Date().toISOString(), error_code: correlation.errorCode,
    })
    try {
      await this.hub.status(status)
    } catch (error) {
      this.ctx.logger.warn(`decision-hub status callback failed: ${safeErrorMessage(error)}`)
    }
    if (!running) await this.reconcileSession(sessionId)
  }

  private onError(sessionId: string, _message: string): void {
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    correlation.state = 'unknown'
    correlation.errorCode = 'dsh_session_error'
    void this.reconcileSession(sessionId)
  }

  private deadlineSignal(deadlineAt: string): AbortSignal {
    const remaining = Math.min(this.config.operationTimeoutMs, Date.parse(deadlineAt) - Date.now())
    if (remaining <= 0) throw new HostRouteError('host_deadline_elapsed', 408)
    return AbortSignal.timeout(remaining)
  }

  private normalizeError(error: unknown): HostRouteError {
    if (error instanceof HostRouteError) return error
    if (error instanceof BodyReadError) {
      const status = error.code === 'host_body_too_large' ? 413 : error.code === 'host_content_type_invalid' ? 415 : 400
      return new HostRouteError(error.code, status)
    }
    if (error instanceof ZodError) return new HostRouteError('host_contract_invalid', 422)
    if (error instanceof HubClientError) return new HostRouteError(error.code, error.retryable ? 503 : 502, error.retryable)
    if (error instanceof Error && error.message.startsWith('host_')) return new HostRouteError(error.message, 409)
    if (error instanceof DOMException && error.name === 'TimeoutError') return new HostRouteError('host_deadline_elapsed', 408, true)
    this.ctx.logger.warn(`decision-hub route failed: ${safeErrorMessage(error)}`)
    return new HostRouteError('host_internal_error', 500)
  }

  private error(res: ServerResponse, error: HostRouteError): void {
    this.json(res, error.status, { error: { code: error.code, message: error.code, retryable: error.retryable } })
  }

  private json(res: ServerResponse, status: number, body: unknown): void {
    if (res.headersSent) return
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
    res.end(JSON.stringify(body))
  }
}
