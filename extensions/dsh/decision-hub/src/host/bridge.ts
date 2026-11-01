import { readCanonicalResearchPrompt, readResearchBaseline, researchPreparationDeadlineAtMs } from './baseline.js'
import { TaskSkillVisibility, taskSkillCapability } from './task-skill-visibility.js'
import { TaskOutputBudgets } from './task-output-budget.js'
import { SkillHostCoordinator, type SkillHostIdentity } from './skill-host.js'
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
  type DshRunSessionLinkView,
  type DshModelDispatchBinding,
  type DshSessionStatus,
  type DshSessionSubmit,
  type DshUpstreamIdentity,
  type ResearchSessionRequest,
} from '@decision-hub/contracts-ts'
import { z, ZodError } from 'zod'
import { HOST_KEY_HEADER, secretMatches } from './auth.js'
import { BodyReadError, readJsonBody } from './body.js'
import { isSessionEventLike, type HostContextPublic, type ModelAgentLineage, type ModelDispatchOptions, type SessionEventLike } from './dsh-public.js'
import { CurrentHostActivity, hostActivityRequestSchema, hostSelectedActivityRequestSchema } from './current-activity.js'
import { HubClient, HubClientError } from './hub-client.js'
import { safeErrorMessage } from './redaction.js'
import { guardedModelStream } from './model-dispatch.js'
import { ModelInvocationWatchdogs } from './model-invocation-watchdog.js'
import { makeSelectedSkillMessage, replaceHistoricalSelectedSkillMessage, selectedSkillMessageIndex } from './selected-skill-delivery.js'
import { makeRetainedRootContextMessage, makeClaimedRootContextMessage, canReuseRetainedRootContextMessage, replaceRetainedRootContextMessage, type RetainedDispatchLink } from './retained-context.js'
import { retireClaimedRootContextPredecessor } from './retained-context-handoff.js'
import { providerCanonicalJson } from './provider-input.js'
import type { PreStepDecisionPublic } from './dsh-public.js'
import { consumeHarnessModel } from './harness-execution.js'
import { registerProviderInputReader } from './provider-input.js'
import { SessionCorrelations, type Correlation } from './session-correlation.js'
import {
  acknowledgedResultCursor,
  commandDelegations,
  type CommandActivity,
  hasAcceptedPrompt,
  hasAcceptedSynthesisAfterPrompt,
  projectInspection,
  projectAcknowledgedResult,
  projectAcknowledgedStatus,
  matchesAcknowledgedResult,
  terminalTimeMs,
} from './session-result.js'
import { CommandCancellation, freezeCommandBinding, readCommandQuiescence, sameCommandBinding } from './command-cancellation.js'
import { TerminalQuiescence, terminalAuthoritySchema } from './terminal-quiescence.js'
import { PLUGIN_BUILD_HASH } from '../generated-build-identity.js'
import type { SynthesisReviewContext } from '../final-review-gate.js'
import { perpetualSnapshotSourceCallId } from '../final-review-gate.js'
import { synthesisProtocol } from '../synthesis-protocol.js'
import { NativeConversationCensusCollector, type NativeCensusAgent } from './native-conversation-census.js'
import { readChildResearchSources, declaredChildResearchDelivery } from './child-research-context.js'

function censusAgent(agent: ModelAgentLineage | undefined): NativeCensusAgent | undefined {
  const session = agent?.session
  if (!agent || !session || session.id !== agent.id || !session.header
    || !Number.isSafeInteger(session.firstLiveSeq) || !Number.isSafeInteger(session.seq)
    || !Array.isArray(session.events) || !session.events.every(isSessionEventLike)
    || !['idle', 'running'].includes(agent.status ?? '')) return undefined
  return agent as NativeCensusAgent
}
type CensusCapture = { correlation: Correlation; collector: NativeConversationCensusCollector; attempted: boolean }
type ProviderStepOwner = {
  correlation: Correlation
  rootSessionId: string
  executionSessionId: string
  stepEventSeq: number | null
  startedAt: number
  transition: 'model_timeout' | 'task_deadline' | null
}
import { deterministicIds } from './session-correlation.js'

const SOURCE_COMMIT = '0a53fb55bea101816fa226bb964ae2bed71c343b'
const SOURCE_VERSION = '0.1.2-alpha.2'
const SHA256_PATTERN = /^[a-f0-9]{64}$/
const CLIENT_IDEMPOTENCY_PATTERN = /^[A-Za-z0-9._:-]{8,256}$/

export interface HostPluginConfig {
  researchServiceUrl?: string
  researchAuthKey?: string
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
  modelBindingTimeoutMs?: number
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
  researchServiceUrl: string
  researchAuthKey: string
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
  modelBindingTimeoutMs: number
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
    researchServiceUrl: config.researchServiceUrl ?? process.env.DECISION_HUB_RESEARCH_TOOL_URL ?? 'http://127.0.0.1:8002/decision-hub/v1/research-capabilities/execute',
    researchAuthKey: config.researchAuthKey ?? process.env.DECISION_HUB_DSH_RESEARCH_TOOL_KEY ?? '',
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
    modelBindingTimeoutMs: config.modelBindingTimeoutMs ?? 30_000,
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

function nativeSessionMissing(error: unknown, sessionId: string): boolean {
  if (!(error instanceof Error)) return false
  const code = (error as Error & { code?: unknown }).code
  if (code === 'session_not_found' || code === 'SESSION_NOT_FOUND' || code === 'session/not-found') return true
  const message = error.message.trim()
  return message.toLowerCase() === 'session not found' || message.toLowerCase() === 'missing session'
    || message === `session "${sessionId}" not found`
}

type SubmitStageCode = 'host_submit_inspect' | 'host_submit_workspace' | 'host_submit_create'
  | 'host_submit_accepted' | 'host_submit_prompt_fetch' | 'host_submit_link_fetch'
  | 'host_submit_original_prompt_fetch' | 'host_submit_prompt'
type SubmitFailureDiagnostic = {
  schema_version: 'dsh-submit-error.v1'
  stage_code: SubmitStageCode
  run_id: string
  dsh_session_id: string
  generation: number
  request_hash: string
  request_id: string
}

export class DecisionHubHostBridge {
  private readonly correlations = new SessionCorrelations()
  readonly skills?: SkillHostCoordinator
  private readonly taskSkills?: TaskSkillVisibility
  private readonly outputBudgets: TaskOutputBudgets
  private readonly hub: HubClient
  private readonly cancellations = new CommandCancellation()
  private readonly terminalDrains: TerminalQuiescence
  private readonly pendingFailures = new Map<string, NonNullable<ReturnType<typeof projectInspection>['completion']>>()
  private readonly results = new Map<string, DshSessionResult>()
  private readonly terminalSent = new Set<string>()
  private readonly terminalInFlight = new Map<string, Promise<void>>()
  private readonly cancelStops = new Map<string, Promise<void>>()
  private readonly modelStepTimers = new ModelInvocationWatchdogs()
  private readonly modelStepOwners = new Map<string, ProviderStepOwner>()
  private readonly modelStepEventSeqs = new Map<string, number>()
  private readonly modelStepRoots = new Map<string, string>()
  private readonly modelStepReleasesByRoot = new Map<string, Map<symbol, () => void>>()
  private readonly synthesisStops = new Set<string>()
  private readonly activity: CurrentHostActivity
  private hubReachable = false
  private readonly nativeCensuses = new Map<string, CensusCapture>()
  private nativeObserverInstalled = false

  constructor(private readonly ctx: HostContextPublic, private readonly config: ResolvedHostConfig) {
    this.activity = new CurrentHostActivity(ctx)
    this.hub = new HubClient({
      baseUrl: config.hubBaseUrl,
      callbackKey: config.callbackKey,
      timeoutMs: config.callbackTimeoutMs,
      modelBindingTimeoutMs: config.modelBindingTimeoutMs,
      managedReadDeadline: id => (this.correlations.getRun(id) ?? this.correlations.getSession(id))?.deadlineAt,
      ...(process.env.DECISION_HUB_DURABLE_SKILL_CALLBACKS_ENABLED === '1'
        ? { durableSkillCallbacks: { protocolVersion: 'research-skill-callback.v1' as const } } : {}),
      attempts: config.callbackAttempts,
      ownerId: config.ownerId,
      ...(config.fetchImpl === undefined ? {} : { fetchImpl: config.fetchImpl }),
    })
    this.outputBudgets = new TaskOutputBudgets(binding => {
      const correlation = this.correlations.getRun(binding.runId)
      const agent = this.ctx.agents?.get(binding.sessionId)
      return Boolean(correlation && sameCommandBinding(binding, correlation) && agent?.session?.header
        && agent.session.header.parentSession === undefined && correlation.terminalAt === null
        && !this.cancellations.requested(binding) && !this.terminalDrains.closingForSession(binding.sessionId)
        && correlation.deadlineAt && Date.parse(correlation.deadlineAt) > Date.now())
    })
    if (process.env.DECISION_HUB_RESEARCH_SKILLS_ENABLED === '1') {
      this.taskSkills = new TaskSkillVisibility((agent, binding) => {
        const correlation = this.correlations.getRun(binding.runId)
        if (!correlation || !sameCommandBinding(binding, correlation) || this.ctx.agents?.get(agent.id) !== agent
          || agent.id !== binding.sessionId || !agent.session?.header || agent.session.header.parentSession !== undefined
          || correlation.terminalAt !== null || this.cancellations.requested(binding)
          || this.terminalDrains.closingForSession(agent.id) || !correlation.deadlineAt
          || !(Date.parse(correlation.deadlineAt) > Date.now())) return false
        try { this.activity.assertNotFenced(agent.id); return true } catch { return false }
      })
      const wire = (identity: SkillHostIdentity) => ({ run_id: identity.runId, generation: identity.generation,
        request_hash: identity.requestHash, root_session_id: identity.rootSessionId, execution_session_id: identity.executionSessionId })
      this.skills = new SkillHostCoordinator({
        tool: (identity, callId, args, proof, signal) => this.hub.skillCallback('tool', {
          schema_version: 'research-skill-tool-request.v1', identity: wire(identity), call_id: callId,
          arguments: args, research_occurrence: proof,
        } as Parameters<HubClient['skillCallback']>[1], signal),
        record: (identity, record, signal) => {
          const payload = {
            schema_version: record.proof.kind === 'selected_skill_provider_input.v1'
              ? 'research-skill-record-request.v2' : 'research-skill-record-request.v1',
            identity: wire(identity), ...record,
          }
          return this.hub.skillCallback('records', payload, signal)
        },
      }, async context => {
        const agent = context.agent as ModelAgentLineage
        const correlation = this.correlations.getSession(agent.id)
        if (!correlation || this.ctx.agents?.get(agent.id) !== agent || !agent.session?.header
          || agent.session.header.parentSession !== undefined) throw new Error('skill_root_session_required')
        const link = await this.hub.linkBySession(agent.id, context.signal)
        if (link.run_id !== correlation.runId || link.dsh_session_id !== agent.id
          || link.generation !== correlation.generation || link.request_hash !== correlation.requestHash) {
          throw new Error('skill_host_binding_changed')
        }
        return { runId: link.run_id, generation: link.generation, requestHash: link.request_hash,
          rootSessionId: link.dsh_session_id, executionSessionId: agent.id }
      })
    }
    this.terminalDrains = new TerminalQuiescence(ctx,
      (runId, signal, deadlineAt) => this.hub.terminalAuthority(runId, signal, deadlineAt),
      binding => {
        const current = this.correlations.getRun(binding.runId)
        return current !== undefined && sameCommandBinding(binding, current)
          && !this.cancellations.requested(binding)
      },
      ids => this.activity.hasLineageModelStreams(ids))
  }

  async resolveCurrentSynthesisReviewContext(agent: ModelAgentLineage, signal: AbortSignal, currentCallId?: string): Promise<SynthesisReviewContext> {
    const correlation = this.correlations.getSession(agent.id)
    if (correlation === undefined) throw new Error('decision_hub_review_context_root_unavailable')
    return this.resolveSynthesisReviewContext(agent, `research-request:${correlation.runId}`, signal, currentCallId)
  }

  /** Read a fresh product binding and official immutable log; never grant publication. */
  async resolveSynthesisReviewContext(
    agent: ModelAgentLineage,
    candidateRequestId: string,
    signal: AbortSignal,
    currentCallId?: string,
  ): Promise<SynthesisReviewContext> {
    return this.readSynthesisReviewContext(agent, candidateRequestId, signal, undefined, undefined, undefined, currentCallId)
  }

  /** Only model dispatch may supply its already freshly bound exact link. */
  private async readSynthesisReviewContext(
    agent: ModelAgentLineage, candidateRequestId: string, signal: AbortSignal,
    dispatchLink?: RetainedDispatchLink,
    outerRemainingMs?: number,
    beforeClaimedPrompt?: { requestId: string },
    currentCallId?: string,
    childDelivery?: { refresh: true } | { messages: readonly import('@deepseek-ai/dsh-llm').Message[] },
  ): Promise<SynthesisReviewContext> {
    signal.throwIfAborted()
    const correlation = this.correlations.getSession(agent.id)
    if (correlation === undefined || this.ctx.agents?.get(agent.id) !== agent
      || agent.session?.header === undefined || agent.session.header.parentSession !== undefined) {
      throw new Error('decision_hub_review_context_root_unavailable')
    }
    const binding = freezeCommandBinding(correlation)
    if (candidateRequestId !== `research-request:${binding.runId}`) {
      throw new Error('decision_hub_review_context_request_mismatch')
    }
    const ids = deterministicIds(binding.runId, binding.requestHash, binding.generation)
    if (ids.sessionId !== binding.sessionId || ids.requestId !== binding.requestId) {
      throw new Error('decision_hub_review_context_binding_invalid')
    }
    const originalDeadlineAt = researchPreparationDeadlineAtMs({ correlationDeadlineAt: correlation.deadlineAt,
      linkDeadlineAt: dispatchLink?.deadline_at, outerRemainingMs, legacyMs: this.config.operationTimeoutMs })
    // A context may include durable server-baseline acquisition, not just a
    // short GET. Per-read HubClient budgets remain local to each actual read.
    const contextReadBudget = originalDeadlineAt - Date.now()
    if (!Number.isFinite(contextReadBudget) || contextReadBudget <= 0) {
      throw new HubClientError('dsh_host_read_deadline_elapsed', false)
    }
    const contextDeadlineReason = () => correlation.deadlineAt != null
      ? new HubClientError('dsh_host_read_transport_timeout', false) : new Error('decision_hub_review_context_deadline')
    const controller = new AbortController()
    const abort = () => controller.abort(signal.reason)
    signal.addEventListener('abort', abort, { once: true })
    let expiresAt = originalDeadlineAt
    let timer = setTimeout(() => controller.abort(contextDeadlineReason()), contextReadBudget)
    const current = () => {
      controller.signal.throwIfAborted()
      if (Date.now() >= expiresAt || this.correlations.getSession(agent.id) !== correlation
        || !sameCommandBinding(binding, correlation) || this.ctx.agents?.get(agent.id) !== agent
        || agent.session?.header === undefined || agent.session.header.parentSession !== undefined
        || this.cancellations.requested(binding) || this.terminalDrains.closingForSession(agent.id)
        || correlation.terminalAt !== null || !['admitted', 'running', 'idle'].includes(correlation.state)) {
        throw new Error('decision_hub_review_context_binding_changed')
      }
      this.activity.assertNotFenced(binding.sessionId)
    }
    const bounded = async <T>(work: () => Promise<T>): Promise<T> => {
      current()
      let stop!: () => void
      const aborted = new Promise<never>((_resolve, reject) => {
        stop = () => reject(controller.signal.reason)
        controller.signal.addEventListener('abort', stop, { once: true })
      })
      try { return await Promise.race([work(), aborted]) }
      finally { controller.signal.removeEventListener('abort', stop) }
    }
    const validateLink = (link: Awaited<ReturnType<HubClient['linkBySession']>>) => {
      current()
      if (link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId
        || link.request_hash !== binding.requestHash || link.generation !== binding.generation
        || link.terminal_at !== null || !['admitted', 'running', 'idle'].includes(link.state)
        || link.upstream_identity.source_commit !== this.config.sourceCommit
        || link.upstream_identity.source_version !== this.config.sourceVersion
        || link.upstream_identity.plugin_build_hash !== this.config.pluginBuildHash
        || link.deadline_at === null || !Number.isFinite(Date.parse(link.deadline_at))) {
        throw new Error('decision_hub_review_context_link_mismatch')
      }
      expiresAt = Math.min(expiresAt, Date.parse(link.deadline_at))
      clearTimeout(timer)
      const remaining = expiresAt - Date.now()
      if (remaining <= 0) throw new Error('decision_hub_review_context_deadline')
      timer = setTimeout(() => controller.abort(contextDeadlineReason()), remaining)
    }
    try {
      current()
      validateLink(dispatchLink ?? await bounded(() => this.hub.linkBySession(binding.sessionId, controller.signal)))
      const inspected = await bounded(() => this.ctx.sessionController.inspect(binding.sessionId, controller.signal))
      current()
      if (inspected.meta.id !== binding.sessionId || inspected.meta.parentSession !== undefined) {
        throw new Error('decision_hub_review_context_lineage_mismatch')
      }
      // Detach the complete official event snapshot across subsequent awaits.
      // Tool payloads remain observations; only exact original prompt metadata
      // below provides the canonical request/budget for this product binding.
      const events: SessionEventLike[] = structuredClone(inspected.events)
      const seen = new WeakSet<object>()
      const freeze = (value: object): void => {
        if (seen.has(value)) return
        seen.add(value)
        for (const nested of Object.values(value)) {
          if (typeof nested === 'object' && nested !== null) freeze(nested)
        }
        Object.freeze(value)
      }
      freeze(events)
      if (events.length > 50_000 || events.some((event, index) => !Number.isSafeInteger(event.seq)
        || event.seq < 0 || (index > 0 && event.seq <= events[index - 1]!.seq))) {
        throw new Error('decision_hub_review_context_events_invalid')
      }
      const promptFor = (requestId: string) => {
        const matches = events.filter(event => {
          const source = event.data.source
          return event.type === 'user/message' && typeof source === 'object' && source !== null
            && 'kind' in source && source.kind === 'user' && 'rpcId' in source && source.rpcId === requestId
        })
        if (matches.length !== 1) throw new Error('decision_hub_review_context_prompt_mismatch')
        return matches[0]!
      }
      const firstIds = deterministicIds(binding.runId, binding.requestHash, 1)
      const initial = promptFor(firstIds.requestId)
      // Only retainedPreStep enters this path after authenticating the exact
      // claimed current Hub prompt. It is not yet a native SessionEvent.
      const activeRequestId = beforeClaimedPrompt === undefined ? binding.requestId
        : deterministicIds(binding.runId, binding.requestHash, binding.generation - 1).requestId
      if (beforeClaimedPrompt !== undefined && (binding.generation <= 1
        || beforeClaimedPrompt.requestId !== binding.requestId || events.some(event => {
          const source = event.data.source
          return event.type === 'user/message' && typeof source === 'object' && source !== null
            && 'kind' in source && source.kind === 'user' && 'rpcId' in source && source.rpcId === binding.requestId
        }))) throw new Error('decision_hub_review_context_prompt_mismatch')
      const active = promptFor(activeRequestId)
      if (initial.seq > active.seq || events.some(event => {
        const source = event.data.source
        return event.seq > active.seq && event.type === 'user/message' && typeof source === 'object'
          && source !== null && 'kind' in source && source.kind === 'user'
          && 'rpcId' in source && source.rpcId !== activeRequestId
      })) throw new Error('decision_hub_review_context_prompt_changed')
      const blocks = initial.data.content
      if (!Array.isArray(blocks) || blocks.length !== 1 || typeof blocks[0] !== 'object'
        || blocks[0] === null || blocks[0].type !== 'text' || typeof blocks[0].text !== 'string') {
        throw new Error('decision_hub_review_context_control_missing')
      }
      const promptText: string = blocks[0].text
      const request = readCanonicalResearchPrompt(promptText, this.config.maxBodyBytes)
      if (request.run_id !== binding.runId || request.request_id !== candidateRequestId) {
        throw new Error('decision_hub_review_context_request_mismatch')
      }
      const serverBaseline = await readResearchBaseline({ serviceUrl: this.config.researchServiceUrl,
        authKey: this.config.researchAuthKey, fetchImpl: this.config.fetchImpl, binding, request,
        onTransportFailure: diagnostic => this.ctx.logger.warn(JSON.stringify({ event: 'decision_hub_baseline_transport_failed',
          run_id: binding.runId, session_id: binding.sessionId, ...diagnostic })),
        signal: controller.signal, deadlineAt: new Date(expiresAt).toISOString() })
      current()
      const perpetual = synthesisProtocol(request.output_schema_ref) === 'decision_v2'
        ? await this.hub.perpetualResearchBinding(binding.runId, binding.requestHash, binding.generation, controller.signal, new Date(expiresAt).toISOString(), perpetualSnapshotSourceCallId(events,currentCallId)) : undefined
      current()
      if (perpetual) freeze(perpetual)
      let childContext: Pick<SynthesisReviewContext, 'childResearchSources' | 'deliveredPendingEvidence'> = {}
      if (synthesisProtocol(request.output_schema_ref) === 'decision_v2') {
        const delivered = childDelivery && 'refresh' in childDelivery ? undefined
          : declaredChildResearchDelivery(events, childDelivery && 'messages' in childDelivery
            ? childDelivery.messages : agent.session?.deriveMessages?.() ?? [])
        const capture = this.nativeCensuses.get(binding.sessionId)
        if (!capture || capture.correlation !== correlation) throw new HubClientError('research_provider_native_context_invalid', false)
        const childResearchSources = await bounded(() => readChildResearchSources({ binding,
          descriptors: capture.collector.researchSourceDescriptors(), controller: this.ctx.sessionController,
          hub: this.hub, signal: controller.signal, deadlineAt: new Date(expiresAt).toISOString(),
          ...(childDelivery && 'refresh' in childDelivery ? {} : { declared: delivered?.prefixes ?? [] }) }))
        current()
        childContext = { childResearchSources, ...(delivered ? { deliveredPendingEvidence: delivered.pending } : {}) }
      }
      const detail = await bounded(() => this.hub.researchDetail(binding.runId, controller.signal, new Date(expiresAt).toISOString()))
      current()
      if (detail.run.run_id !== binding.runId) throw new Error('decision_hub_review_context_evidence_run_mismatch')
      const admittedEvidence = structuredClone(detail.evidence)
      freeze(admittedEvidence)
      if (dispatchLink === undefined) {
        validateLink(await bounded(() => this.hub.linkBySession(binding.sessionId, controller.signal)))
        const business = await bounded(() => this.hub.businessStatus(binding.runId, controller.signal, new Date(expiresAt).toISOString()))
        current()
        if (business.status !== 'researching') throw new Error('decision_hub_review_context_run_inactive')
      } else validateLink(dispatchLink)
      return { binding, request, events, admittedEvidence, ...childContext, ...(serverBaseline ? { serverBaseline } : {}), ...(perpetual ? { perpetualBinding: perpetual.binding, perpetualReviewOriginals: perpetual.reviewOriginals } : {}) }
    } finally {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
    }
  }

  private async readRetainedContext(
    dispatch: DshModelDispatchBinding, link: RetainedDispatchLink, signal: AbortSignal | undefined,
    messages?: readonly import('@deepseek-ai/dsh-llm').Message[],
  ): Promise<SynthesisReviewContext> {
    const agent = this.ctx.agents?.get(dispatch.dsh_session_id)
    const correlation = this.correlations.getSession(dispatch.dsh_session_id)
    if (this.config.runtimeMode !== 'live'
      || dispatch.execution_session_id !== null || dispatch.parent_session_id !== null
      || agent === undefined || correlation === undefined || dispatch.run_id !== correlation.runId
      || dispatch.request_hash !== correlation.requestHash || dispatch.generation !== correlation.generation
      || dispatch.plugin_build_hash !== this.config.pluginBuildHash) {
      throw new HubClientError('research_provider_native_context_invalid', false)
    }
    try {
      return await this.readSynthesisReviewContext(agent, `research-request:${dispatch.run_id}`,
        signal ?? new AbortController().signal, link, undefined, undefined, undefined,
        messages === undefined ? undefined : { messages })
    } catch (error) {
      if (error instanceof HubClientError && (/^dsh_host_read_(?:transport_timeout|deadline_elapsed|cancelled)$/.test(error.code)
        || error.code.startsWith('research_baseline_'))) throw error
      throw new HubClientError('research_provider_native_context_invalid', false)
    }
  }

  start(): () => void {
    const disposers = [
      this.ctx.on('agent/pre-step', (payload, next) => this.retainedPreStep(payload, next)),
      this.ctx.on('agent/request', async ({ agent }, next) => this.outputBudgets.select(agent.id, await next())),
      this.ctx.on('agent/created', ({ agent }) => { this.taskSkills?.observe(agent); this.observeCensusAgent(agent, false) }),
      this.ctx.on('agent/disposed', ({ agent }) => { this.taskSkills?.disposed(agent); this.outputBudgets.forget(agent.id); this.observeCensusAgent(agent, true) }),
      this.ctx.on('subagent/start', info => {
        // The global official edge has no parent carrier. Do not invent remote
        // ownership; a remote source makes open census proofs conservative.
        if (info.local === false) for (const capture of this.nativeCensuses.values()) {
          capture.collector.unknown('native_remote_registry_unscoped')
        }
      }),
      this.ctx.webServer.register({ kind: 'exact', path: '/decision-hub/v1/readiness', handler: (req, res) => this.readiness(req, res) }),
      this.ctx.webServer.register({ kind: 'exact', path: '/decision-hub/v1/current-activity', handler: (req, res) => this.currentActivity(req, res) }),
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
        path: '/api/decision-hub/data-jobs',
        methods: ['GET', 'HEAD'],
        fetch: request => this.browserDataJobs(request),
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
      this.ctx.on('session/event', (session, event) => {
        for (const capture of this.nativeCensuses.values()) {
          try {
            if (session.id && session.header && Number.isSafeInteger(session.firstLiveSeq)
              && Number.isSafeInteger(session.seq) && Array.isArray(session.events)) {
              capture.collector.event(session as NativeCensusAgent['session'], event)
            }
          } catch { capture.collector.unknown('native_lifecycle_observation_failed') }
        }
        if (session.id) this.onSessionEvent(session.id, event)
      }),
      this.ctx.on('llm/stream', (options, next) => this.modelStream(options, next)),
      // The real Cordis Context supplies root. Legacy standalone public-service
      // fixtures have no root and cannot authorize synthesis through this reader.
      ...((this.ctx as HostContextPublic & { root?: object }).root
        ? [registerProviderInputReader((this.ctx as HostContextPublic & { root: object }).root, this.hub)] : []),
    ]
    this.nativeObserverInstalled = true
    void this.hub.readiness().then(value => { this.hubReachable = value })
    return () => {
      this.modelStepTimers.clearAll()
      this.modelStepOwners.clear()
      this.modelStepEventSeqs.clear()
      this.modelStepRoots.clear()
      this.modelStepReleasesByRoot.clear()
      this.synthesisStops.clear()
      this.nativeObserverInstalled = false
      this.nativeCensuses.clear()
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

  private async browserDataJobs(request: Request): Promise<Response> {
    try {
      const rawLimit = new URL(request.url).searchParams.get('limit')
      const limit = rawLimit === null || rawLimit.length === 0 ? 20 : Number(rawLimit)
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
        return this.rawBrowserJson(400, {
          error: { code: 'host_data_jobs_limit_invalid', retryable: false },
        }, request.method)
      }
      const jobs = await this.hub.dataJobs(limit)
      return this.rawBrowserJson(200, jobs, request.method)
    } catch (error) {
      const notFound = error instanceof HubClientError && error.code === 'host_hub_http_404'
      return this.rawBrowserJson(notFound ? 404 : 503, {
        error: {
          code: notFound ? 'host_data_jobs_not_found' : 'host_data_jobs_unavailable',
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
      const match = /^\/decision-hub\/v1\/runs\/([^/]+)(?:\/(cancel|result|quiesce))?$/.exec(url.pathname)
      if (match === null) throw new HostRouteError('host_route_not_found', 404)
      const runId = decodeURIComponent(match[1]!)
      const action = match[2]
      if (action === 'quiesce') {
        if (req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
        return await this.quiesce(runId, req, res)
      }
      if (action === 'cancel') {
        if (req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
        return await this.cancel(runId, req, res)
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

  private async currentActivity(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      this.authorize(req)
      if (this.config.runtimeMode !== 'live') throw new HostRouteError('host_replay_read_only', 409)
      if (!this.versionCompatible()) throw new HostRouteError('host_version_incompatible', 503)
      if (req.method !== 'GET' && req.method !== 'POST') throw new HostRouteError('host_method_not_allowed', 405)
      const workspace = await this.resolveWorkspace()
      const scope = await this.activity.scope(this.config.hubBaseUrl, this.config.defaultWorkspaceCwd, workspace.id)
      if (req.method === 'GET') {
        const parameters = new URL(req.url ?? '/', 'http://localhost').searchParams
        if (parameters.size === 0) {
          return this.json(res, 200, { schema_version: 'dsh-host-activity-scope.v1', scope, observed_at: new Date().toISOString() })
        }
        if (new Set(parameters.keys()).size !== parameters.size) throw new HostRouteError('host_activity_selected_duplicate_parameter', 400)
        const selected = hostSelectedActivityRequestSchema.parse(Object.fromEntries(parameters))
        if (selected.plugin_build_hash !== PLUGIN_BUILD_HASH) throw new HostRouteError('host_activity_selected_plugin_mismatch', 409)
        const expected = deterministicIds(selected.run_id, selected.request_hash, selected.generation)
        const binding = freezeCommandBinding({ runId: selected.run_id, sessionId: selected.dsh_session_id,
          requestHash: selected.request_hash, generation: selected.generation, requestId: expected.requestId })
        const observe = await this.activity.observeSelected(scope, selected, async () => {
          const link = await this.hub.link(selected.run_id)
          if (link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId
            || link.request_hash !== binding.requestHash || link.generation !== binding.generation
            || link.upstream_identity.plugin_build_hash !== PLUGIN_BUILD_HASH) {
            throw new Error('host_activity_selected_original_binding_changed')
          }
        }, () => {
          const requested = this.cancellations.requested(binding)
          const settled = requested && this.cancellations.readyForFreshQuiescence(binding, this.ctx)
          return { requested, state: settled ? 'settled' : 'unknown', reason_code: settled
            ? 'host_activity_scoped_drain_completed' : this.cancellations.errorCode(binding) ?? 'host_activity_drain_unobserved',
            resource_witness: this.cancellations.resourceWitness(binding, this.ctx) }
        }, this.config.operationTimeoutMs)
        return this.json(res, 200, observe)
      }
      const request = hostActivityRequestSchema.parse(await readJsonBody(req, this.config.maxBodyBytes))
      const snapshot = await this.activity.snapshot(scope, request.records, async binding => {
        const link = await this.hub.link(binding.run_id)
        if (link.run_id !== binding.run_id || link.dsh_session_id !== binding.dsh_session_id
          || link.generation !== binding.generation || link.request_hash !== binding.request_hash) {
          throw new Error('host_activity_binding_mismatch')
        }
        const business = await this.hub.businessStatus(binding.run_id, undefined, undefined, 'lifecycle')
        return { businessStatus: business.status }
      }, this.config.operationTimeoutMs)
      this.json(res, 200, snapshot)
    } catch (error) { this.error(res, this.normalizeError(error)) }
  }

  private async *modelStream(options: ModelDispatchOptions, next: () => AsyncIterable<unknown>): AsyncIterable<unknown> {
    const releaseInvocation = this.activity.beginModelInvocation(options.sessionId)
    const releaseWatchdogs: (() => void)[] = []
    let preparedCorrelation: Correlation | undefined
    const requireActiveCorrelation = (binding: DshModelDispatchBinding, executionSessionId: string): Correlation => {
      const correlation = this.correlations.getRun(binding.run_id)
      if (correlation === undefined || correlation.runId !== binding.run_id
        || correlation.sessionId !== binding.dsh_session_id || correlation.requestHash !== binding.request_hash
        || correlation.generation !== binding.generation) {
        throw new HubClientError('dsh_model_dispatch_identity_mismatch', false)
      }
      const expectedExecution = binding.execution_session_id ?? binding.dsh_session_id
      if (executionSessionId !== expectedExecution) {
        throw new HubClientError('dsh_model_dispatch_identity_mismatch', false)
      }
      if (executionSessionId !== correlation.sessionId) {
        let current = this.ctx.agents?.get(executionSessionId)
        if (!current || current.id !== executionSessionId
          || current.session?.header?.parentSession !== binding.parent_session_id) {
          throw new HubClientError('dsh_model_dispatch_lineage_invalid', false)
        }
        const visited = new Set<string>([executionSessionId])
        let reachesRoot = false
        for (let depth = 0; depth < 128; depth++) {
          const parent: string | undefined = current.session?.header?.parentSession
          if (!parent || visited.has(parent)) break
          if (parent === correlation.sessionId) { reachesRoot = true; break }
          visited.add(parent)
          current = this.ctx.agents?.get(parent)
          if (!current || current.id !== parent) break
        }
        if (!reachesRoot) throw new HubClientError('dsh_model_dispatch_lineage_invalid', false)
      }
      const command = freezeCommandBinding(correlation)
      if (correlation.terminalAt !== null || !['admitted', 'running', 'idle'].includes(correlation.state)) {
        throw new HubClientError('dsh_model_dispatch_run_terminal', false)
      }
      if (this.cancellations.requested(command)) throw new HubClientError('dsh_provider_cancelled', false)
      if (this.terminalDrains.closingForSession(correlation.sessionId)
        || this.terminalDrains.closingForSession(executionSessionId)) {
        throw new HubClientError('dsh_provider_cancelled', false)
      }
      try { this.activity.assertNotFenced(executionSessionId) } catch {
        throw new HubClientError('dsh_provider_cancelled', false)
      }
      return correlation
    }
    const preparationDeadline = (binding: DshModelDispatchBinding, executionSessionId?: string): string => {
      const correlation = requireActiveCorrelation(binding,
        executionSessionId ?? binding.execution_session_id ?? binding.dsh_session_id)
      if (preparedCorrelation === undefined) preparedCorrelation = correlation
      else if (preparedCorrelation !== correlation) {
        throw new HubClientError('dsh_model_dispatch_identity_mismatch', false)
      }
      const task = correlation?.deadlineAt ? Date.parse(correlation.deadlineAt) : NaN
      if (!Number.isFinite(task) || task <= Date.now()) {
        throw new HubClientError('dsh_provider_input_deadline_elapsed', false)
      }
      // Binding, retained context and the input receipt are preparation. They
      // remain bounded by the immutable Task deadline, but do not spend or
      // mislabel the actual Provider model window.
      return new Date(task).toISOString()
    }
    const beginProviderDelegate = (binding: DshModelDispatchBinding, executionSessionId: string): void => {
      const correlation = requireActiveCorrelation(binding, executionSessionId)
      // A field-identical replacement is still a different command lifetime.
      // The receipt and the Provider delegate must belong to one correlation
      // object, so a delete/reinsert race cannot release Provider bytes.
      if (preparedCorrelation === undefined || preparedCorrelation !== correlation) {
        throw new HubClientError('dsh_model_dispatch_identity_mismatch', false)
      }
      const task = correlation?.deadlineAt ? Date.parse(correlation.deadlineAt) : NaN
      if (!Number.isFinite(task) || task <= Date.now()) {
        throw new HubClientError('dsh_provider_input_deadline_elapsed', false)
      }
      // The first real delegate in one native step owns its absolute model
      // start. Root and child executions use the same authenticated command,
      // while each execution has a stable step owner and cannot refill it.
      const stepEventSeq = this.modelStepEventSeqs.get(executionSessionId) ?? null
      let owner = this.modelStepOwners.get(executionSessionId)
      if (owner === undefined || owner.correlation !== correlation || owner.stepEventSeq !== stepEventSeq) {
        owner = { correlation, rootSessionId: correlation.sessionId, executionSessionId,
          stepEventSeq, startedAt: Date.now(), transition: null }
        this.modelStepOwners.set(executionSessionId, owner)
      }
      this.modelStepRoots.set(executionSessionId, correlation.sessionId)
      const modelDeadline = owner.startedAt + correlation.modelStepTimeoutMs
      const deadline = Math.min(task, modelDeadline)
      const transition = modelDeadline < task ? 'model_timeout' as const : 'task_deadline' as const
      const command = freezeCommandBinding(correlation)
      const elapsed = () => {
        if (this.correlations.getRun(binding.run_id) !== correlation
          || !sameCommandBinding(command, correlation)
          || this.modelStepOwners.get(executionSessionId) !== owner
          || owner.transition !== null) return
        owner.transition = transition
        if (transition === 'model_timeout') {
          // The correlation fields are global Run status, while cancellation
          // belongs to each execution owner. A sibling root/child expiration
          // must still cancel its own session after the first status write.
          if (!correlation.modelStepTimeoutTriggered) {
            correlation.modelStepTimeoutTriggered = true
            correlation.errorCode = 'dsh_model_step_timeout'
          }
        }
        try {
          void Promise.resolve(this.ctx.sessionController.cancel({ sessionId: executionSessionId }))
            .catch(error => this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${safeErrorMessage(error)}`))
        } catch (error) {
          this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${safeErrorMessage(error)}`)
        }
      }
      if (!Number.isFinite(deadline) || deadline <= Date.now()) {
        if (modelDeadline < task) elapsed()
        throw new HubClientError(modelDeadline < task
          ? 'dsh_provider_cancelled' : 'dsh_provider_input_deadline_elapsed', false)
      }
      let rawRelease: () => void
      try {
        rawRelease = this.modelStepTimers.acquire(executionSessionId, deadline, elapsed)
      } catch (error) {
        // Date.now can cross the absolute boundary between the check above and
        // timer acquisition. Preserve the authenticated Task/model
        // classification instead of leaking the watchdog's internal error.
        if (!(error instanceof Error && error.message === 'model_watchdog_deadline_invalid')) throw error
        if (modelDeadline < task) elapsed()
        throw new HubClientError(modelDeadline < task
          ? 'dsh_provider_cancelled' : 'dsh_provider_input_deadline_elapsed', false)
      }
      const releaseId = Symbol('provider-watchdog-release')
      let rootReleases = this.modelStepReleasesByRoot.get(correlation.sessionId)
      if (rootReleases === undefined) {
        rootReleases = new Map()
        this.modelStepReleasesByRoot.set(correlation.sessionId, rootReleases)
      }
      let released = false
      const release = () => {
        if (released) return
        released = true
        rawRelease()
        const current = this.modelStepReleasesByRoot.get(correlation.sessionId)
        current?.delete(releaseId)
        if (current?.size === 0) this.modelStepReleasesByRoot.delete(correlation.sessionId)
      }
      rootReleases.set(releaseId, release)
      releaseWatchdogs.push(release)
    }
    try {
      await this.activity.assertModelNotFenced(options.sessionId)
      this.terminalDrains.assertModelAdmission(options.sessionId)
      yield* guardedModelStream(options, () => this.activity.modelStream(options.sessionId, next), this.hub, PLUGIN_BUILD_HASH, this.ctx.agents, async (payload, actual, originalDeadlineAt) => {
        const taskDeadline = originalDeadlineAt ?? preparationDeadline(payload)
        const workspace = await this.resolveWorkspace()
        const scope = await this.activity.scope(this.config.hubBaseUrl, this.config.defaultWorkspaceCwd, workspace.id)
        await consumeHarnessModel(this.hub, payload, actual, scope, PLUGIN_BUILD_HASH, taskDeadline)
      }, async () => {
        const workspace = await this.resolveWorkspace()
        return this.activity.scope(this.config.hubBaseUrl, this.config.defaultWorkspaceCwd, workspace.id)
      }, this.config.runtimeMode === 'live'
        ? (payload, link, signal, messages) => this.readRetainedContext(payload, link, signal, messages) : undefined,
      this.skills ? (input, signal) => this.skills!.afterRecordedProviderInput(input, signal) : undefined,
      preparationDeadline,
      beginProviderDelegate,
      (stage, input) => {
        try {
          this.ctx.logger.info(`decision_hub_provider_stage ${JSON.stringify({
            schema_version: 'dsh-provider-stage-diagnostic.v1', at: new Date().toISOString(), stage,
            run_id_hash: createHash('sha256').update(input.identity.run_id).digest('hex'),
            generation: input.identity.generation, dispatch_id: input.identity.dispatch_id,
            input_hash: input.receipt.input_hash,
          })}`)
        } catch { /* Diagnostics never change Provider execution. */ }
      })
    } catch (error) {
      if (!(error instanceof Error) || !error.message.startsWith('host_activity_')) throw error
      yield { type: 'finish', reason: { kind: 'error', failure: {
        code: error.message.toUpperCase(), message: error.message,
      } } }
    } finally {
      // yield* only exits after its delegated iterator and all owned proof /
      // receipt cleanup. A finish chunk is not lifecycle closure.
      for (const release of releaseWatchdogs) release()
      releaseInvocation()
    }
  }

  private async submit(runId: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const submit = dshSessionSubmitSchema.parse(await readJsonBody(req, this.config.maxBodyBytes))
    if (submit.run_id !== runId) throw new HostRouteError('host_run_id_mismatch', 400)
    this.activity.assertNotFenced(submit.deterministic_session_id)
    if (Date.parse(submit.deadline_at) <= Date.now()) throw new HostRouteError('host_deadline_elapsed', 408)
    this.validateRefs(submit)
    if (this.cancellations.requestedForSession(submit.deterministic_session_id)
      || this.terminalDrains.closingForSession(submit.deterministic_session_id)) {
      throw new HostRouteError('host_cancel_pending', 409)
    }
    const { correlation, duplicate } = this.correlations.admit(submit)
    if (!duplicate) {
      this.synthesisStops.delete(correlation.sessionId)
      if (this.config.runtimeMode === 'live') {
        if (this.nativeCensuses.size >= 64 && !this.nativeCensuses.has(correlation.sessionId)) {
          throw new HostRouteError('host_native_census_capacity', 503)
        }
        this.nativeCensuses.set(correlation.sessionId, { correlation, attempted: false,
          collector: new NativeConversationCensusCollector({ run_id: correlation.runId, generation: correlation.generation,
            root_session_id: correlation.sessionId, request_hash: correlation.requestHash,
            plugin_build_hash: this.config.pluginBuildHash }, this.nativeObserverInstalled) })
      }
    }
    let stage: SubmitStageCode = 'host_submit_inspect'
    try {
      const inspectSignal = this.deadlineSignal(submit.deadline_at)
      let inspection: Awaited<ReturnType<typeof this.ctx.sessionController.inspect>> | null = null
      try { inspection = await this.ctx.sessionController.inspect(correlation.sessionId, inspectSignal) } catch { /* absent is create */ }
      stage = 'host_submit_workspace'
      const workspace = await this.resolveWorkspace()
      this.activity.assertNotFenced(correlation.sessionId)
      if (inspection === null || !workspace.sessionIds.includes(correlation.sessionId)) {
        stage = 'host_submit_create'
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
      stage = 'host_submit_accepted'
      await this.hub.accepted(accepted)
      correlation.acceptedAt = accepted.accepted_at
      const needsPrompt = inspection === null || !hasAcceptedPrompt(inspection, correlation.requestId)
      let prompt: Awaited<ReturnType<HubClient['prompt']>> | undefined
      if (needsPrompt) {
        stage = 'host_submit_prompt_fetch'
        // Canonical proof owns the immutable Run deadline. The short SDK
        // inspect signal must not cancel a valid long read after acceptance.
        prompt = await this.hub.prompt(runId, correlation.generation, undefined, correlation.deadlineAt)
        if (prompt.run_id !== runId || prompt.dsh_session_id !== correlation.sessionId
          || prompt.request_id !== correlation.requestId || prompt.request_hash !== correlation.requestHash
          || prompt.generation !== correlation.generation) throw new HostRouteError('host_prompt_conflict', 409)
      }
      if (this.taskSkills) {
        // SDK assembly precedes agent/pre-step. Freeze visibility here, before
        // queue wakes the driver; duplicate submits also authenticate the scope.
        stage = 'host_submit_link_fetch'
        const binding = freezeCommandBinding(correlation)
        const link = await this.hub.linkBySession(binding.sessionId, undefined, correlation.deadlineAt)
        if (link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId || link.request_hash !== binding.requestHash
          || link.generation !== binding.generation || link.terminal_at !== null || !['admitted', 'running', 'idle'].includes(link.state)
          || link.upstream_identity.source_commit !== this.config.sourceCommit || link.upstream_identity.source_version !== this.config.sourceVersion
          || link.upstream_identity.plugin_build_hash !== this.config.pluginBuildHash || link.deadline_at === null
          || !(Date.parse(link.deadline_at) > Date.now())) throw new HostRouteError('host_prompt_conflict', 409)
        if (!this.taskSkills.has(binding)) {
          stage = 'host_submit_original_prompt_fetch'
          const original = binding.generation === 1 && prompt ? prompt
            : await this.hub.prompt(binding.runId, 1, undefined, link.deadline_at)
          const ids = deterministicIds(binding.runId, binding.requestHash, 1)
          if (original.run_id !== binding.runId || original.dsh_session_id !== binding.sessionId
            || original.request_id !== ids.requestId || original.request_hash !== binding.requestHash || original.generation !== 1) {
            throw new HostRouteError('host_prompt_conflict', 409)
          }
          const originalTask = readCanonicalResearchPrompt(original.prompt, this.config.maxBodyBytes)
          this.taskSkills.remember(binding, originalTask)
          this.outputBudgets.remember(binding, originalTask)
        }
        const agent = this.ctx.agents?.get(binding.sessionId)
        if (!agent) throw new HostRouteError('host_prompt_conflict', 409)
        this.taskSkills.apply(agent, binding)
        this.outputBudgets.activate(binding)
      }
      if (needsPrompt) {
        if (!prompt) throw new HostRouteError('host_prompt_conflict', 409)
        this.activity.assertNotFenced(correlation.sessionId)
        stage = 'host_submit_prompt'
        await this.ctx.sessionController.prompt({
          requestId: correlation.requestId, sessionId: correlation.sessionId,
          mode: 'queue', content: [{ type: 'text', text: prompt.prompt }],
        }, this.deadlineSignal(submit.deadline_at))
      }
      this.json(res, 202, accepted)
    } catch (error) {
      // Unknown exceptions are never logged or returned as arbitrary messages.
      // The closed stage and exact original tuple diagnose partial submission;
      // neither of them acknowledges creation, acceptance, cancellation or result.
      const normalized = error instanceof HostRouteError || error instanceof HubClientError
        || error instanceof BodyReadError || error instanceof ZodError
        || (error instanceof DOMException && error.name === 'TimeoutError')
        || (error instanceof Error && ['host_activity_lineage_fenced', 'host_activity_lineage_invalid'].includes(error.message))
        ? this.normalizeError(error) : new HostRouteError('host_internal_error', 500)
      const diagnostic: SubmitFailureDiagnostic = {
        schema_version: 'dsh-submit-error.v1', stage_code: stage,
        run_id: correlation.runId, dsh_session_id: correlation.sessionId,
        generation: correlation.generation, request_hash: correlation.requestHash,
        request_id: correlation.requestId,
      }
      this.ctx.logger.warn(`decision_hub_submit_failure ${JSON.stringify({
        ...diagnostic, http_status: normalized.status,
      })}`)
      this.error(res, normalized, diagnostic)
    }
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
    const status = correlation.terminalAt === null ? projected.status : (await this.inspect(correlation)).status
    this.json(res, 200, status)
  }

  private async result(runId: string, res: ServerResponse): Promise<void> {
    const correlation = await this.correlation(runId)
    const binding = freezeCommandBinding(correlation)
    if (correlation.terminalAt === null) {
      const projected = await this.inspect(correlation)
      await this.maybeTerminal(correlation, projected.completion, projected.result)
    }
    // Every result answer first reads a fresh authenticated Link. This includes
    // the active/no-result path, so a stale local projection cannot return a
    // specific absence for a replaced command or newly acknowledged terminal.
    const link = await this.hub.link(runId)
    // Authenticate command identity before interpreting absent result pointers.
    // A genuine failed/cancelled terminal may intentionally have no result bytes; a
    // foreign/changed terminal must never gain that more specific response.
    if (this.correlations.getRun(binding.runId) !== correlation || !sameCommandBinding(binding, correlation)
      || link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId
      || link.request_hash !== binding.requestHash || link.generation !== binding.generation) {
      throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
    }
    if (correlation.terminalAt === null) {
      if (link.terminal_at !== null || ['completed', 'failed', 'cancelled'].includes(link.state)) {
        throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
      }
      throw new HostRouteError('host_terminal_result_unavailable', 409)
    }
    if (link.terminal_at === null || terminalTimeMs(link.terminal_at) !== terminalTimeMs(correlation.terminalAt)
      || !['completed', 'failed', 'cancelled'].includes(link.state)
      || link.state !== correlation.state || link.last_seq !== correlation.lastSeq
      || link.error_code !== correlation.errorCode) {
      throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
    }
    const noResultRef = link.result_ref === null
    const noResultHash = link.result_hash === null
    if (noResultRef && noResultHash) {
      if (link.state === 'failed' || link.state === 'cancelled') {
        throw new HostRouteError('host_terminal_result_unavailable', 409)
      }
      throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
    }
    if (noResultRef !== noResultHash) throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
    try { acknowledgedResultCursor(link) }
    catch { throw new HostRouteError('host_terminal_result_binding_mismatch', 409) }
    const cached = this.results.get(runId)
    if (cached !== undefined && this.matchesAcknowledgedResult(cached, link)) {
      this.json(res, 200, cached)
      return
    }
    // After restart, recover only the exact acknowledged native event snapshot.
    // Appended bookkeeping, another RPC, and an old generation cannot extend it.
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.config.operationTimeoutMs)
    try {
      const inspection = await this.ctx.sessionController.inspect(binding.sessionId, controller.signal)
      if (this.correlations.getRun(binding.runId) !== correlation || !sameCommandBinding(binding, correlation)) {
        throw new HostRouteError('host_command_binding_changed', 409)
      }
      const result = projectAcknowledgedResult(correlation, inspection, link)
      if (!matchesAcknowledgedResult(result, link, inspection)) {
        throw new HostRouteError('host_terminal_result_binding_mismatch', 409)
      }
      this.results.set(runId, result)
      this.json(res, 200, result)
    } catch (error) {
      if (error instanceof HostRouteError) throw error
      throw new HostRouteError('host_terminal_result_reconstruction_failed', 409)
    } finally { clearTimeout(timer) }
  }

  private matchesAcknowledgedResult(result: DshSessionResult, link: DshRunSessionLinkView): boolean {
    return matchesAcknowledgedResult(result, link)
  }

  private async quiesce(runId: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const command = z.object({
      generation: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
      purpose: z.enum(['acknowledged_terminal', 'before_failed_terminal']).optional(),
      authority: terminalAuthoritySchema.optional(),
    }).strict().parse(await readJsonBody(req, this.config.maxBodyBytes))
    const purpose = command.purpose ?? 'acknowledged_terminal'
    if ((purpose === 'before_failed_terminal') !== (command.authority !== undefined)
      || (command.authority !== undefined && command.authority.purpose !== purpose)) {
      throw new HostRouteError('host_terminal_drain_authority_changed', 409)
    }
    const correlation = await this.correlation(runId, false)
    const binding = freezeCommandBinding(correlation)
    if (command.generation !== binding.generation) throw new HostRouteError('host_terminal_drain_generation_mismatch', 409)
    const proof = await this.terminalDrains.drain(binding, purpose, this.config.operationTimeoutMs, command.authority)
    // Native-only cleanup: no terminal callback, status rewrite or new submission.
    this.json(res, 200, proof)
  }

  private async cancel(runId: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const responseDeadline = Date.now() + 4_000
    const command = z.object({
      generation: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
      reason: z.string().min(1).max(256),
    }).strict().parse(await readJsonBody(req, this.config.maxBodyBytes))
    const known = this.correlations.getRun(runId)
    if (known !== undefined && command.generation !== known.generation) {
      throw new HostRouteError('host_cancel_generation_mismatch', 409)
    }
    const correlation = known ?? await this.cancelResponseWait(this.correlation(runId, false), responseDeadline, 'host_cancel_binding_wait_elapsed')
    if (command.generation !== correlation.generation) throw new HostRouteError('host_cancel_generation_mismatch', 409)
    const binding = freezeCommandBinding(correlation)
    // For a known command install intent before freshLink's await. Unknown
    // commands are recovered without starting any SDK cancellation.
    if (correlation.terminalAt === null) {
      this.cancellations.begin(binding)
      this.clearModelStepTimer(binding.sessionId)
      this.terminalDrains.requestCancel(binding)
    }
    const replyToStopping = async (stopping: Promise<void>): Promise<void> => {
      try {
      await this.cancelResponseWait(stopping, responseDeadline)
      if (this.correlations.getRun(runId) !== correlation || !sameCommandBinding(binding, correlation)) {
        throw new HostRouteError('host_cancel_binding_mismatch', 409)
      }
      const projected = await this.cancelResponseWait(this.inspect(correlation, Math.max(1, responseDeadline - Date.now()), true), responseDeadline)
      // Physical confirmation precedes HTTP reply; Hub callback is independent.
      this.json(res, 202, projected.status)
    } catch (error) {
      if (this.correlations.getRun(runId) !== correlation || !sameCommandBinding(binding, correlation)) {
        throw new HostRouteError('host_cancel_binding_mismatch', 409)
      }
      if (!(error instanceof HostRouteError && error.code === 'host_cancel_response_pending')) {
        throw new HostRouteError(this.cancellations.errorCode(binding) ?? 'host_cancel_pending', 503)
      }
      // Only this already-authenticated immutable command gets a pending reply.
      this.json(res, 202, this.pendingCancelStatus(correlation))
    }
    }
    const terminalWork = [...this.terminalInFlight.entries()]
      .filter(([key]) => key.startsWith(`${runId}:${binding.generation}:`)).map(([, work]) => work)
    if (terminalWork.length > 0 && correlation.terminalAt === null) {
      // Intent and cancellation notification precede this await. Retain the
      // original terminal operation; the one cancelStops promise joins it.
      this.activity.cancelCommand(binding)
      return await replyToStopping(this.stopCancelledCommand(correlation, terminalWork))
    }
    const link = await this.cancelResponseWait(this.hub.link(runId), responseDeadline, 'host_cancel_binding_wait_elapsed')
    if (this.correlations.getRun(runId) !== correlation || !sameCommandBinding(binding, correlation)
      || command.generation !== correlation.generation
      || link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId
      || link.generation !== binding.generation || link.request_hash !== binding.requestHash) {
      throw new HostRouteError('host_cancel_binding_mismatch', 409)
    }
    if (correlation.terminalAt !== null || link.terminal_at !== null) {
      this.cancellations.discardUnstarted(binding)
      correlation.terminalAt = link.terminal_at ?? correlation.terminalAt
      try {
        await this.cancelResponseWait(this.terminalDrains.drain(binding, 'acknowledged_terminal', this.config.operationTimeoutMs), responseDeadline)
        const projected = await this.cancelResponseWait(this.inspect(correlation, Math.max(1, responseDeadline - Date.now())), responseDeadline)
        return this.json(res, 202, projected.status)
      } catch (error) {
        if (!(error instanceof HostRouteError && error.code === 'host_cancel_response_pending')) throw error
        if (this.correlations.getRun(runId) !== correlation || !sameCommandBinding(binding, correlation)) {
          throw new HostRouteError('host_cancel_binding_mismatch', 409)
        }
        return this.json(res, 202, this.pendingCancelStatus(correlation))
      }
    }
    this.activity.cancelCommand(binding)
    return await replyToStopping(this.stopCancelledCommand(correlation))
  }

  private async cancelResponseWait<T>(work: Promise<T>, deadline: number, code = 'host_cancel_response_pending'): Promise<T> {
    const remaining = deadline - Date.now()
    if (remaining <= 0) {
      void work.catch(() => undefined)
      throw new HostRouteError(code, 503, true)
    }
    let timer: ReturnType<typeof setTimeout> | undefined
    const elapsed = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new HostRouteError(code, 503, true)), remaining)
    })
    try {
      const result = await Promise.race([work, elapsed])
      if (Date.now() > deadline) throw new HostRouteError(code, 503, true)
      return result
    } finally { clearTimeout(timer) }
  }

  private pendingCancelStatus(correlation: Correlation): DshSessionStatus {
    return dshSessionStatusSchema.parse({
      schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, state: 'idle', generation: correlation.generation,
      last_seq: correlation.lastSeq, observed_at: new Date().toISOString(), error_code: 'host_cancel_pending',
      model_step_timeout_ms: correlation.modelStepTimeoutMs,
      model_step_timeout_source: correlation.modelStepTimeoutSource,
    })
  }

  private stopCancelledCommand(correlation: Correlation, terminalWork: readonly Promise<void>[] = []): Promise<void> {
    const binding = freezeCommandBinding(correlation)
    const key = `${binding.runId}:${binding.sessionId}:${binding.generation}:${binding.requestHash}`
    const existing = this.cancelStops.get(key)
    if (existing !== undefined) return existing
    const current = () => this.correlations.getRun(binding.runId) === correlation
      && sameCommandBinding(binding, correlation) && this.cancellations.requested(binding)
    const stopping = Promise.resolve().then(async () => {
      if (!current()) throw new HostRouteError('host_cancel_binding_mismatch', 409)
      if (terminalWork.length > 0) {
        // Rejection (including the requested abort) does not mean rollback or
        // quiescence. Await every original promise through its actual finally.
        await Promise.allSettled(terminalWork)
        if (!current()) throw new HostRouteError('host_cancel_binding_mismatch', 409)
        const link = await this.hub.link(binding.runId)
        if (this.correlations.getRun(binding.runId) !== correlation
          || !sameCommandBinding(binding, correlation)
          || link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId
          || link.generation !== binding.generation || link.request_hash !== binding.requestHash) {
          throw new HostRouteError('host_cancel_binding_mismatch', 409)
        }
        if (link.terminal_at !== null) {
          if (!['completed', 'failed', 'cancelled'].includes(link.state)) {
            throw new HostRouteError('host_cancel_binding_mismatch', 409)
          }
          this.cancellations.discardUnstarted(binding)
          correlation.terminalAt = link.terminal_at
          correlation.state = link.state
          correlation.lastSeq = link.last_seq
          correlation.errorCode = link.error_code
          return
        }
      }
      await this.cancellations.stop(this.ctx, binding, this.config.operationTimeoutMs)
      if (!current()) throw new HostRouteError('host_cancel_binding_mismatch', 409)
    })
    this.cancelStops.set(key, stopping)
    // The same resource operation completes after a pending HTTP reply. Every
    // projection/callback re-reads native quiet state and retains original CAS.
    void stopping.then(async () => {
      if (current()) await this.reconcileSession(binding.sessionId)
    }).catch(error => {
      this.ctx.logger.warn(`decision-hub cancellation remains unconfirmed: ${safeErrorMessage(error)}`)
    }).finally(() => {
      if (this.cancelStops.get(key) === stopping) this.cancelStops.delete(key)
    })
    return stopping
  }

  private async correlation(runId: string, restoreCancellation = true): Promise<Correlation> {
    const local = this.correlations.getRun(runId)
    if (local !== undefined) return local
    try {
      const link = await this.hub.link(runId)
      const concurrent = this.correlations.getRun(runId)
      if (concurrent !== undefined) return concurrent
      const recovered = this.correlations.recover(link)
      if (restoreCancellation && recovered.terminalAt === null) {
        const binding = freezeCommandBinding(recovered)
        const business = await this.hub.businessStatus(runId)
        if (this.correlations.getRun(runId) !== recovered || !sameCommandBinding(binding, recovered)) {
          throw new HostRouteError('host_command_binding_changed', 409)
        }
        if (business.status === 'cancelled') this.restoreCancelledIntent(recovered)
      }
      return recovered
    } catch (error) {
      if (error instanceof HubClientError && error.code === 'host_hub_http_404') {
        throw new HostRouteError('host_run_not_found', 404)
      }
      throw error
    }
  }

  private restoreCancelledIntent(correlation: Correlation): void {
    const binding = freezeCommandBinding(correlation)
    this.cancellations.begin(binding)
    this.activity.cancelCommand(binding)
    this.clearModelStepTimer(correlation.sessionId)
    // Restore only authoritative business intent; perform a fresh actual SDK
    // drain. A previous boot's quiet/aborted projection is never reused.
    void this.cancellations.stop(this.ctx, binding, this.config.operationTimeoutMs).catch(error => {
      this.ctx.logger.warn(`decision-hub cancellation remains unconfirmed: ${safeErrorMessage(error)}`)
    })
  }

  private async inspect(correlation: Correlation, timeoutMs = this.config.operationTimeoutMs, cancellationReply = false): Promise<ReturnType<typeof projectInspection>> {
    const binding = freezeCommandBinding(correlation)
    if (correlation.terminalAt !== null) {
      const link = await this.hub.link(binding.runId)
      if (this.correlations.getRun(binding.runId) !== correlation || !sameCommandBinding(binding, correlation)) {
        throw new HostRouteError('host_command_binding_changed', 409)
      }
      const status = projectAcknowledgedStatus(correlation, link)
      correlation.state = status.state
      correlation.lastSeq = status.last_seq
      correlation.errorCode = status.error_code
      return { status, completion: null, result: null }
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      let inspection = await this.ctx.sessionController.inspect(correlation.sessionId, controller.signal)
      // Existing acknowledged terminal generations retain their original turn semantics.
      const commandLifecycle = correlation.terminalAt === null || cancellationReply
      const delegated = commandLifecycle ? commandDelegations(inspection, correlation.requestId) : null
      let activity: CommandActivity | undefined
      if (delegated !== null && !delegated.unresolved && delegated.childIds.length + delegated.jobIds.length > 0) {
        activity = await this.commandActivity(correlation, delegated.childIds, controller.signal)
        // The fresh event cursor must be covered by the official baseline before it can settle.
        inspection = await this.ctx.sessionController.inspect(correlation.sessionId, controller.signal)
      }
      let ownerCancellation = commandLifecycle && this.cancellations.requested(binding)
      const hasAbortedEnd = inspection.events.some(event => event.type === 'turn/end'
        && typeof event.data.reason === 'object' && event.data.reason !== null
        && (event.data.reason as { kind?: unknown }).kind === 'aborted')
      const quiet = commandLifecycle && (ownerCancellation || hasAbortedEnd)
        && (!ownerCancellation || this.cancellations.readyForFreshQuiescence(correlation, this.ctx))
        ? await readCommandQuiescence(this.ctx, this.ctx.sessionController, correlation.sessionId, controller.signal) : null
      if (this.correlations.getRun(binding.runId) !== correlation || !sameCommandBinding(binding, correlation)) {
        throw new HostRouteError('host_command_binding_changed', 409)
      }
      if (correlation.terminalAt !== null) return await this.inspect(correlation, timeoutMs, cancellationReply)
      ownerCancellation = commandLifecycle && this.cancellations.requested(binding)
      const currentSeq = inspection.events.reduce((seq, event) => Math.max(seq, event.seq), 0)
      const projected = projectInspection(correlation, inspection, commandLifecycle ? {
        commandLifecycle: true, ...(activity === undefined ? {} : { activity }),
        ...(ownerCancellation ? { ownerCancellation: quiet !== null && quiet.lastSeq === currentSeq
          && this.cancellations.readyForFreshQuiescence(binding, this.ctx)
          && !this.activity.hasLineageModelStreams([binding.sessionId, ...quiet.descendantIds]) ? { pending: false as const, observedAt: new Date().toISOString() } : { pending: true as const } } : {}),
        abortedQuiescent: quiet !== null && quiet.lastSeq === currentSeq
          && !this.activity.hasLineageModelStreams([binding.sessionId, ...quiet.descendantIds]),
      } : undefined)
      correlation.state = projected.status.state
      correlation.lastSeq = projected.status.last_seq
      correlation.errorCode = projected.status.error_code
      return projected
    } catch (error) {
      // A durable correlation can survive a Host restart while its native DSH
      // Session does not.  This is terminal for the original Run: recreating it
      // through PUT would dispatch the frozen prompt/model/tools a second time.
      if (nativeSessionMissing(error, correlation.sessionId)) {
        throw new HostRouteError('dsh_native_session_lost', 410)
      }
      throw error
    } finally { clearTimeout(timer) }
  }

  private async commandActivity(correlation: Correlation, childIds: readonly string[], signal: AbortSignal): Promise<CommandActivity | undefined> {
    const control = this.ctx.sessionController.control
    const parent = this.ctx.agents?.get(correlation.sessionId)
    if (control === undefined || parent?.id !== correlation.sessionId
      || (parent.status !== 'idle' && parent.status !== 'running') || childIds.length > 64) return undefined
    // Only declared child ids are inspected; no Host-wide session census or activation.
    for (const id of childIds) {
      let child: { meta: { id: string; parentSession?: string }; events: SessionEventLike[] }
      try { child = await this.ctx.sessionController.inspect(id, signal) } catch { return undefined }
      if (child.meta.id !== id || child.meta.parentSession !== correlation.sessionId) return undefined
      const live = this.ctx.agents?.get(id)
      if (live !== undefined && (live.id !== id || live.status !== 'idle')) return undefined
    }
    const iterator = control.call(this.ctx.sessionController, signal)[Symbol.asyncIterator]()
    try {
      const first = await iterator.next()
      if (first.done || first.value.type !== 'baseline' || !first.value.value) return undefined
      const { queues, jobs, projections } = first.value.value
      const parentSeq = projections?.[correlation.sessionId]?.asOfSeq
      if (!Number.isSafeInteger(parentSeq) || (parentSeq ?? -1) < 0) return undefined
      let queueCount = 0
      let activeJobCount = 0
      for (const id of [correlation.sessionId, ...childIds]) {
        const queued = queues[id]
        const ownedJobs = jobs[id]
        // Settled child Agents may already have left the live control census.
        if (id === correlation.sessionId && (!Array.isArray(queued) || !Array.isArray(ownedJobs))) return undefined
        if (queued !== undefined && !Array.isArray(queued)) return undefined
        if (ownedJobs !== undefined && (!Array.isArray(ownedJobs) || ownedJobs.some(job =>
          !['running', 'stopping', 'completed', 'killed', 'failed'].includes(job.status)))) return undefined
        queueCount += queued?.length ?? 0
        activeJobCount += ownedJobs?.filter(job => job.status === 'running' || job.status === 'stopping').length ?? 0
      }
      const current = this.ctx.agents?.get(correlation.sessionId)
      if (current?.id !== correlation.sessionId || (current.status !== 'idle' && current.status !== 'running')) return undefined
      return { parentSeq: parentSeq!, parentRunning: current.status === 'running', queueCount, activeJobCount }
    } catch { return undefined } finally { await iterator.return?.() }
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
  private onSessionEvent(sessionId: string, event: SessionEventLike): void {
    // Native step/start is preparation, not proof that Provider delegation has
    // begun. It supplies only a stable step identity. Duplicate observation of
    // that step cannot refill the Provider window; a later sequence can own a
    // new window. step/end does not revoke an iterator still closing receipts.
    if (event.type === 'step/start' && Number.isSafeInteger(event.seq)) {
      const observed = this.modelStepEventSeqs.get(sessionId)
      if (observed === undefined || event.seq > observed) {
        this.modelStepEventSeqs.set(sessionId, event.seq)
        this.modelStepOwners.delete(sessionId)
      }
    }
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    if (event.type === 'tool/result' || event.type === 'tool/call-result') {
      void this.stopAfterAcceptedSynthesis(sessionId)
      return
    }
    if (event.type === 'step/end' || event.type === 'turn/end') {
      if (event.type === 'turn/end') void this.reconcileSession(sessionId)
    }
  }

  /**
   * End the model turn once the validated synthesis Tool result is durable.
   * The synthesis object is the product result; asking the provider for a
   * trailing prose response adds latency and can turn a successful run into a
   * deadline failure when a compatible endpoint returns an empty final step.
   */
  private async stopAfterAcceptedSynthesis(sessionId: string): Promise<void> {
    if (this.synthesisStops.has(sessionId)) return
    const correlation = this.correlations.getSession(sessionId)
    if (correlation === undefined) return
    try {
      const inspection = await this.ctx.sessionController.inspect(sessionId)
      if (!hasAcceptedSynthesisAfterPrompt(inspection, correlation.requestId)) return
      const delegated = commandDelegations(inspection, correlation.requestId)
      if (delegated.unresolved) return
      if (delegated.childIds.length + delegated.jobIds.length > 0) {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), this.config.operationTimeoutMs)
        try {
          const activity = await this.commandActivity(correlation, delegated.childIds, controller.signal)
          const latest = await this.ctx.sessionController.inspect(sessionId, controller.signal)
          const lastSeq = latest.events.reduce((value, event) => Math.max(value, event.seq), 0)
          if (activity === undefined || activity.queueCount > 0 || activity.activeJobCount > 0
            || activity.parentSeq < lastSeq || commandDelegations(latest, correlation.requestId).unresolved
            || !hasAcceptedSynthesisAfterPrompt(latest, correlation.requestId)) return
        } finally { clearTimeout(timer) }
      }
      this.synthesisStops.add(sessionId)
      this.clearModelStepTimer(sessionId)
      await Promise.resolve(this.ctx.sessionController.cancel({ sessionId }))
    } catch (error) {
      this.synthesisStops.delete(sessionId)
      this.ctx.logger.warn(`decision-hub synthesis stop failed: ${safeErrorMessage(error)}`)
    }
  }

  private clearModelStepTimer(sessionId: string): void {
    // Explicit owner cancellation / accepted synthesis are scope revocation,
    // unlike an ordinary SDK idle or step/end observation.
    const rootSessionId = this.modelStepRoots.get(sessionId) ?? sessionId
    const releases = this.modelStepReleasesByRoot.get(rootSessionId)
    if (releases !== undefined) {
      for (const release of [...releases.values()]) release()
      this.modelStepReleasesByRoot.delete(rootSessionId)
    }
    for (const [executionSessionId, root] of [...this.modelStepRoots]) {
      if (root !== rootSessionId) continue
      this.modelStepTimers.clearSession(executionSessionId)
      this.modelStepOwners.delete(executionSessionId)
      this.modelStepEventSeqs.delete(executionSessionId)
      this.modelStepRoots.delete(executionSessionId)
    }
    // A root may be revoked before its first delegate registered ownership.
    this.modelStepTimers.clearSession(rootSessionId)
    this.modelStepOwners.delete(rootSessionId)
    this.modelStepEventSeqs.delete(rootSessionId)
  }

  private async selectedSkillPreStep(decision: PreStepDecisionPublic, request: ResearchSessionRequest,
    agent: ModelAgentLineage, binding: import('./command-cancellation.js').CancellationBinding,
    signal: AbortSignal, originalDeadlineAt: string): Promise<PreStepDecisionPublic> {
    if (decision.kind === 'reject' || !this.skills || taskSkillCapability(request) !== 'decision') return decision
    signal.throwIfAborted()
    const identity = { runId: binding.runId, generation: binding.generation, requestHash: binding.requestHash,
      rootSessionId: binding.sessionId, executionSessionId: agent.id }
    // This decision is pinned content from an authenticated immutable selection,
    // not permission. Actual input/loaded/execution readers still authenticate
    // current Task/source/owner and their exact original occurrence.
    const prior = this.skills.deliveryDecision(identity)
    const delivery = prior ?? this.skills.rememberDeliveryDecision(identity,
      await this.hub.selectedSkillDelivery(identity, signal, originalDeadlineAt))
    signal.throwIfAborted()
    if (delivery.delivery_mode === 'legacy-tool') return decision
    replaceHistoricalSelectedSkillMessage(delivery, agent.session, this.ctx.get?.('tokenMeter'))
    const existing = agent.session?.deriveMessages?.() ?? []
    if (selectedSkillMessageIndex([...existing, ...decision.messages], delivery) !== null) return decision
    return { ...decision, messages: [...decision.messages, makeSelectedSkillMessage(delivery)] }
  }

  private async retainedPreStep(payload: { agent: ModelAgentLineage; messages: import('@deepseek-ai/dsh-session').UserMessage[];
    turn: number; step: number; signal: AbortSignal }, next: () => Promise<PreStepDecisionPublic>): Promise<PreStepDecisionPublic> {
    const decision = await next()
    if (decision.kind === 'reject' || this.config.runtimeMode !== 'live') return decision
    const agent = payload.agent, correlation = this.correlations.getSession(agent.id)
    if (this.taskSkills && !this.taskSkills.observed(agent)) {
      // An agent already alive before plugin registration has an old assembly.
      // Deny this step rather than sending that stale catalog to the model.
      this.taskSkills.deny(agent)
      return { kind: 'reject' }
    }
    if (!correlation || agent.session?.header?.parentSession !== undefined) {
      this.taskSkills?.deny(agent)
      return decision
    }
    payload.signal.throwIfAborted()
    const binding = freezeCommandBinding(correlation)
    // Pre-step evidence IO uses only the immutable Task deadline. The model
    // window is minted later at the real Provider delegate boundary.
    const originalDeadlineAt = researchPreparationDeadlineAtMs({ correlationDeadlineAt: correlation.deadlineAt,
      legacyMs: this.config.operationTimeoutMs })
    const preStepBudget = originalDeadlineAt - Date.now()
    if (!Number.isFinite(preStepBudget) || preStepBudget <= 0) {
      throw new HubClientError('dsh_host_read_deadline_elapsed', false)
    }
    const controller = new AbortController()
    const stop = () => controller.abort(payload.signal.reason)
    payload.signal.addEventListener('abort', stop, { once: true })
    let expiresAt = originalDeadlineAt
    let timer = setTimeout(() => controller.abort(), preStepBudget)
    const current = () => {
      controller.signal.throwIfAborted()
      if (Date.now() >= expiresAt || this.correlations.getRun(binding.runId) !== correlation || !sameCommandBinding(binding, correlation)
        || this.ctx.agents?.get(agent.id) !== agent || agent.session?.header === undefined
        || agent.session.header.parentSession !== undefined || this.cancellations.requested(binding)
        || this.terminalDrains.closingForSession(agent.id) || correlation.terminalAt !== null) {
        throw new HubClientError('research_provider_native_context_invalid', false)
      }
      this.activity.assertNotFenced(binding.sessionId)
    }
    const finish = async (accepted: PreStepDecisionPublic, request: ResearchSessionRequest) => {
      current()
      const result = await this.selectedSkillPreStep(accepted, request, agent, binding,
        controller.signal, new Date(expiresAt).toISOString())
      current()
      return result
    }
    const bounded = async <T>(work: () => Promise<T>): Promise<T> => {
      current()
      let abort!: () => void
      const cancelled = new Promise<never>((_resolve, reject) => {
        abort = () => reject(new HubClientError('research_provider_native_context_invalid', false))
        controller.signal.addEventListener('abort', abort, { once: true })
      })
      try { return await Promise.race([work(), cancelled]) }
      finally { controller.signal.removeEventListener('abort', abort) }
    }
    try {
      current()
      const link = await bounded(() => this.hub.linkBySession(binding.sessionId, controller.signal, correlation.deadlineAt))
      current()
      if (link.run_id !== binding.runId || link.dsh_session_id !== binding.sessionId || link.request_hash !== binding.requestHash
        || link.generation !== binding.generation || link.terminal_at !== null || !['admitted', 'running', 'idle'].includes(link.state)
        || link.upstream_identity.source_commit !== this.config.sourceCommit || link.upstream_identity.source_version !== this.config.sourceVersion
        || link.upstream_identity.plugin_build_hash !== this.config.pluginBuildHash || link.deadline_at === null) {
        throw new HubClientError('research_provider_native_context_invalid', false)
      }
      const remaining = Date.parse(link.deadline_at) - Date.now()
      if (!Number.isFinite(remaining) || remaining <= 0) throw new HubClientError('dsh_provider_input_deadline_elapsed', false)
      expiresAt = Math.min(expiresAt, Date.parse(link.deadline_at))
      clearTimeout(timer); timer = setTimeout(() => controller.abort(), Math.max(1, expiresAt - Date.now()))
      // Admission already fixed visibility before queue/assembly; here the
      // current Link and exact binding are rechecked, never harness flags.
      this.taskSkills?.apply(agent, binding)
      const original = censusAgent(agent)
      if (!original) throw new HubClientError('research_provider_native_context_invalid', false)
      const actual = original.session.events
      const nativePrompt = actual.some(event => event.type === 'user/message'
        && event.data.source !== null && typeof event.data.source === 'object'
        && (event.data.source as Record<string, unknown>).kind === 'user'
        && (event.data.source as Record<string, unknown>).rpcId === binding.requestId)
      if (nativePrompt && decision.messages.some(message => message.source.kind === 'user'
        && 'rpcId' in message.source)) throw new HubClientError('research_provider_native_context_invalid', false)
      if (!nativePrompt) {
        // The first pre-step owns the real claimed RPC message, but the SDK has
        // not appended it yet. Authenticate its full bytes without inventing seq.
        if (binding.generation === 1 && actual.some(event => event.type === 'step/start')) {
          throw new HubClientError('research_provider_native_context_invalid', false)
        }
        const prompt = await bounded(() => this.hub.prompt(binding.runId, binding.generation, controller.signal, link.deadline_at))
        current()
        const claimed = decision.messages.filter(message => message.source.kind === 'user'
          && 'rpcId' in message.source && message.source.rpcId === binding.requestId)
        if (prompt.run_id !== binding.runId || prompt.dsh_session_id !== binding.sessionId || prompt.request_id !== binding.requestId
          || prompt.request_hash !== binding.requestHash || prompt.generation !== binding.generation || claimed.length !== 1
          || decision.messages.some(message => message.source.kind === 'user'
            && 'rpcId' in message.source && message.source.rpcId !== binding.requestId)
          || providerCanonicalJson(claimed[0]!.content) !== providerCanonicalJson([{ type: 'text', text: prompt.prompt }])) {
          throw new HubClientError('research_provider_native_context_invalid', false)
        }
        if (binding.generation > 1) {
          let context = await this.readSynthesisReviewContext(agent, `research-request:${binding.runId}`,
            controller.signal, link, expiresAt - Date.now(), { requestId: binding.requestId }, undefined, { refresh: true })
          current()
          if (retireClaimedRootContextPredecessor(context, this.config.pluginBuildHash, agent.session!, this.ctx.get?.('tokenMeter'))) {
            current()
            context = await this.readSynthesisReviewContext(agent, `research-request:${binding.runId}`,
              controller.signal, link, expiresAt - Date.now(), { requestId: binding.requestId }, undefined, { refresh: true })
            current()
            if (providerCanonicalJson(context.events) !== providerCanonicalJson(agent.session!.events)) {
              throw new HubClientError('research_provider_native_context_invalid', false)
            }
          }
          const message = makeClaimedRootContextMessage(context, this.config.pluginBuildHash)
          current()
          return await finish({ ...decision, messages: [...decision.messages, message] }, context.request)
        }
        const request = readCanonicalResearchPrompt(prompt.prompt, this.config.maxBodyBytes)
        if (request.run_id !== binding.runId || request.request_id !== `research-request:${binding.runId}`) {
          throw new HubClientError('research_baseline_context_invalid', false)
        }
        const serverBaseline = await readResearchBaseline({ serviceUrl: this.config.researchServiceUrl,
          authKey: this.config.researchAuthKey, fetchImpl: this.config.fetchImpl, binding, request,
          onTransportFailure: diagnostic => this.ctx.logger.warn(JSON.stringify({ event: 'decision_hub_baseline_transport_failed',
            run_id: binding.runId, session_id: binding.sessionId, ...diagnostic })),
          signal: controller.signal, deadlineAt: new Date(expiresAt).toISOString() })
        current()
        if (!serverBaseline) return await finish(decision, request)
        const detail = await bounded(() => this.hub.researchDetail(binding.runId, controller.signal, new Date(expiresAt).toISOString()))
        current()
        if (detail.run.run_id !== binding.runId) throw new HubClientError('research_baseline_context_invalid', false)
        const message = makeClaimedRootContextMessage({ binding, request, events: structuredClone(actual),
          admittedEvidence: structuredClone(detail.evidence), serverBaseline }, this.config.pluginBuildHash)
        current()
        return await finish({ ...decision, messages: [...decision.messages, message] }, request)
      }
      let context = await this.readSynthesisReviewContext(agent, `research-request:${binding.runId}`,
        controller.signal, link, expiresAt - Date.now(), undefined, undefined, { refresh: true })
      for (;;) {
        current()
        const live = agent.session?.events
        const snapshot = providerCanonicalJson(context.events)
        if (!live || providerCanonicalJson(live.slice(0, context.events.length)) !== snapshot) {
          throw new HubClientError('research_provider_native_context_invalid', false)
        }
        if (providerCanonicalJson(live) === snapshot) break
        // Child reports may append to the official inbox while proof IO awaits.
        // Re-read the complete context under the original deadline; never treat
        // a shortened or changed prefix as an authenticated newer snapshot.
        const previous = context.events
        context = await this.readSynthesisReviewContext(agent, `research-request:${binding.runId}`,
          controller.signal, link, expiresAt - Date.now(), undefined, undefined, { refresh: true })
        current()
        if (providerCanonicalJson(context.events.slice(0, previous.length)) !== providerCanonicalJson(previous)) {
          throw new HubClientError('research_provider_native_context_invalid', false)
        }
      }
      const message = makeRetainedRootContextMessage(context, this.config.pluginBuildHash)
      if (typeof agent.session?.deriveMessages === 'function'
        && canReuseRetainedRootContextMessage(context, message, agent.session.deriveMessages())) {
        current()
        return await finish(decision, context.request)
      }
      current()
      if (agent.session && replaceRetainedRootContextMessage(context, message, agent.session, this.ctx.get?.('tokenMeter'))) {
        current()
        return await finish(decision, context.request)
      }
      return await finish({ ...decision, messages: [...decision.messages, message] }, context.request)
    } finally { clearTimeout(timer); controller.abort(); payload.signal.removeEventListener('abort', stop) }
  }

  private observeCensusAgent(agent: ModelAgentLineage, disposed: boolean): void {
    for (const capture of this.nativeCensuses.values()) {
      try {
        const original = censusAgent(agent)
        if (original) {
          if (disposed) capture.collector.disposed(original)
          else capture.collector.created(original)
        } else if (agent.id === capture.correlation.sessionId) capture.collector.unknown('native_registry_observer_unavailable')
      } catch { capture.collector.unknown('native_lifecycle_observation_failed') }
    }
  }

  /** Observation failure never rewrites or blocks the original terminal. */
  private async retainNativeCensus(correlation: Correlation): Promise<void> {
    const capture = this.nativeCensuses.get(correlation.sessionId)
    if (!capture || capture.correlation !== correlation || capture.attempted || this.config.runtimeMode !== 'live') return
    capture.attempted = true
    let configured: number
    try { configured = this.hub.modelBindingBudgetMs() }
    catch { this.ctx.logger.warn('decision-hub native census unavailable: dsh_native_census_budget_invalid'); return }
    const started = Date.now()
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), configured)
    const cancelWatch = setInterval(() => {
      if (this.correlations.getRun(correlation.runId) !== correlation || this.cancellations.requested(correlation)) controller.abort()
    }, 25)
    try {
      const link = await this.hub.linkBySession(correlation.sessionId, controller.signal)
      if (link.harness_required === true) return
      if (this.correlations.getRun(correlation.runId) !== correlation || link.run_id !== correlation.runId
        || link.dsh_session_id !== correlation.sessionId || link.generation !== correlation.generation
        || link.request_hash !== correlation.requestHash
        || link.upstream_identity.source_commit !== this.config.sourceCommit
        || link.upstream_identity.source_version !== this.config.sourceVersion
        || link.upstream_identity.plugin_build_hash !== this.config.pluginBuildHash
        || link.terminal_at !== null || link.deadline_at === null || this.cancellations.requested(correlation)) {
        throw new HubClientError('dsh_native_census_binding_invalid', false)
      }
      const deadline = Date.parse(link.deadline_at)
      const remaining = deadline - Date.now()
      if (!Number.isFinite(deadline) || remaining < 1) throw new HubClientError('dsh_native_census_deadline_elapsed', false)
      const root = censusAgent(this.ctx.agents?.get(correlation.sessionId))
      if (root) capture.collector.finalizeRoot(root)
      else capture.collector.unknown('native_root_terminal_unavailable')
      const payload = capture.collector.snapshot()
      await this.hub.recordNativeConversationCensus(payload,
        AbortSignal.any([controller.signal, this.deadlineSignal(link.deadline_at)]),
        Math.min(configured - (Date.now() - started), deadline - Date.now()))
      if (this.correlations.getRun(correlation.runId) !== correlation || this.cancellations.requested(correlation)) {
        throw new HubClientError('dsh_native_census_binding_changed', false)
      }
    } catch (error) {
      const code = error instanceof HubClientError && /^dsh_native_census_[a-z_]+$/.test(error.code)
        ? error.code : 'dsh_native_census_recording_refused'
      this.ctx.logger.warn(`decision-hub native census unavailable: ${code}`)
    } finally { clearTimeout(timer); clearInterval(cancelWatch); controller.abort() }
  }

  private async maybeTerminal(correlation: Correlation, completion: ReturnType<typeof projectInspection>['completion'], result: DshSessionResult | null): Promise<void> {
    if (correlation.terminalAt !== null) return
    const binding = freezeCommandBinding(correlation)
    const failureKey = `${binding.runId}:${binding.generation}:${binding.requestHash}`
    const pendingFailure = this.pendingFailures.get(failureKey)
    if (pendingFailure !== undefined && !this.cancellations.requested(binding)) completion = structuredClone(pendingFailure)
    if (completion === null) return
    if (completion.run_id !== binding.runId || completion.dsh_session_id !== binding.sessionId
      || completion.generation !== binding.generation || this.correlations.getRun(binding.runId) !== correlation) return
    const ownerCancellation = this.cancellations.requested(correlation)
    if (ownerCancellation && (completion.terminal_status !== 'cancelled'
      || !this.cancellations.readyForFreshQuiescence(correlation, this.ctx))) return
    if (completion.terminal_status === 'cancelled') {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), this.config.operationTimeoutMs)
      try {
        const quiet = await readCommandQuiescence(this.ctx, this.ctx.sessionController, correlation.sessionId, controller.signal)
        if (quiet === null || quiet.lastSeq !== completion.last_seq
          || this.activity.hasLineageModelStreams([binding.sessionId, ...quiet.descendantIds])) return
        if (this.cancellations.requested(correlation)
          && !this.cancellations.readyForFreshQuiescence(correlation, this.ctx)) return
      } finally { clearTimeout(timer); controller.abort() }
    }
    if (correlation.terminalAt !== null || !sameCommandBinding(binding, correlation)
      || this.correlations.getRun(binding.runId) !== correlation
      || (this.cancellations.requested(binding) && completion.terminal_status !== 'cancelled')) return
    if (result !== null) this.results.set(correlation.runId, result)
    // last_seq can advance when DSH appends bookkeeping events after
    // turn/end. It is not terminal identity. A Run generation has exactly one
    // terminal callback; keeping the key stable prevents event/status/result
    // races from issuing a second callback for the same terminal state.
    const key = `${correlation.runId}:${completion.generation}:${completion.terminal_status}`
    if (this.terminalSent.has(key)) return
    const existing = this.terminalInFlight.get(key)
    if (existing !== undefined) return await existing
    // Coalesce one authenticated callback for this immutable native closure.
    const frozen = structuredClone(completion)
    const immediateNativeFailure = frozen.terminal_status === 'failed'
      && frozen.result_ref === null && frozen.result_hash === null
      && /^research_catalog_transport_(?:timeout|unavailable|cancelled)$/.test(frozen.error?.code ?? '')
    const observe = (stage: string, error?: unknown): void => {
      try {
        const message = error === undefined ? undefined : safeErrorMessage(error)
        const code = message !== undefined && /^(?:host_|dsh_|research_|terminal_)[a-z_]{1,120}$/.test(message)
          ? message : error === undefined ? null : 'terminal_finalization_unconfirmed'
        this.ctx.logger.warn(`decision_hub_terminal_finalization ${JSON.stringify({
          run_id: binding.runId, dsh_session_id: binding.sessionId, generation: binding.generation,
          request_hash: binding.requestHash, operation_key: key, terminal_status: frozen.terminal_status,
          stage, observed_at: new Date().toISOString(), error_code: code,
        })}`)
      } catch { /* Safe observation never changes the original terminal outcome. */ }
    }
    if (frozen.terminal_status === 'failed' && !this.pendingFailures.has(failureKey)) {
      if (this.pendingFailures.size >= 256) throw new HostRouteError('host_terminal_drain_capacity', 503)
      this.pendingFailures.set(failureKey, structuredClone(frozen))
    }
    const task = Promise.resolve().then(async () => {
      let phase = immediateNativeFailure ? 'callback' : 'drain'
      try {
        if (immediateNativeFailure) {
          // Catalog assembly failed before the first provider/tool call. The
          // native turn/end is already the complete execution boundary, so
          // persist its authenticated original immediately. Waiting for the
          // pre-terminal queue drain here lets an unrelated 35s read timeout
          // overwrite this exact cause in the product worker. Acknowledged
          // terminal maintenance retains the existing post-terminal cleanup.
          // This no-result failure is read through dsh_authenticated_terminal;
          // it never enters result quality/provider-usage projection, whose
          // native census intentionally requires a completed conversation.
          observe('native_callback_started')
          await this.hub.terminal(frozen)
          observe('native_callback_ack')
          return
        }
        if (frozen.terminal_status === 'failed') {
          observe('failed_frozen')
          observe('drain_started')
          const quiet = await this.terminalDrains.drain(binding, 'before_failed_terminal', this.config.operationTimeoutMs, undefined, correlation.deadlineAt ?? undefined)
          if (this.cancellations.requested(binding) || this.correlations.getRun(binding.runId) !== correlation
            || !sameCommandBinding(binding, correlation)) throw new HostRouteError('host_terminal_drain_binding_changed', 409)
          frozen.last_seq = Math.max(frozen.last_seq, quiet.last_seq)
          observe('drain_completed')
        }
        phase = 'census'
        observe('census_started')
        await this.retainNativeCensus(correlation)
        observe('census_completed')
        phase = 'callback'
        observe('callback_started')
        await this.hub.terminal(frozen)
        observe('callback_ack')
      } catch (error) {
        observe(`${phase}_rejected`, error)
        throw error
      }
    }).then(() => {
      this.pendingFailures.delete(failureKey)
      this.terminalSent.add(key)
      this.clearModelStepTimer(binding.sessionId)
      this.skills?.forgetSession(correlation.runId)
      this.taskSkills?.forgetRun(correlation.runId)
      if (this.correlations.getRun(binding.runId) === correlation && sameCommandBinding(binding, correlation)) {
        correlation.terminalAt = frozen.completed_at
        correlation.state = frozen.terminal_status
        correlation.lastSeq = frozen.last_seq
        correlation.errorCode = frozen.error?.code ?? null
        if (this.nativeCensuses.get(correlation.sessionId)?.correlation === correlation) this.nativeCensuses.delete(correlation.sessionId)
      }
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
    if (correlation === undefined || correlation.terminalAt !== null) return
    correlation.state = running ? 'running' : 'idle'
    const status = dshSessionStatusSchema.parse({
      schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, state: correlation.state,
      generation: correlation.generation, last_seq: correlation.lastSeq,
      observed_at: new Date().toISOString(), error_code: correlation.errorCode,
      model_step_timeout_ms: correlation.modelStepTimeoutMs,
      model_step_timeout_source: correlation.modelStepTimeoutSource,
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
    // DSH may emit a late transport/session error after it has already
    // appended a terminal turn event (for example when a provider closes an
    // empty trailing response). Preserve the authoritative projected terminal
    // state instead of relabelling a completed result as an unknown failure.
    if (['completed', 'failed', 'cancelled'].includes(correlation.state)) return
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

  private error(res: ServerResponse, error: HostRouteError, submit?: SubmitFailureDiagnostic): void {
    this.json(res, error.status, { error: { code: error.code, message: error.code, retryable: error.retryable,
      ...(submit === undefined ? {} : { submit }),
    } })
  }

  private json(res: ServerResponse, status: number, body: unknown): void {
    if (res.headersSent) return
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
    res.end(JSON.stringify(body))
  }
}
