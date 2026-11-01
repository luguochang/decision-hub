import type {
  DshBusinessStatus,
  DshModelDispatchBinding,
  DshResearchIntake,
  DshRunSessionLinkView,
  DshSessionAccepted,
  DshSessionCompletion,
  DshSessionPrompt,
  DshSessionStatus,
  ResearchRunCommand,
  ResearchRunCommandResult,
  ResearchRunDetailView,
  ResearchRunQueued,
  ResearchInboxView,
  ResearchObservabilityView,
  ResearchValueEvaluation,
  ResearchProviderInputSubmit,
  ResearchProviderInputRecorded,
  ResearchProviderToolEmissionSubmit,
  ResearchProviderToolEmissionRecorded,
  ResearchProviderFinishSubmit,
  ResearchProviderFinishRecorded,
} from '@decision-hub/contracts-ts'
import {
  dshBusinessStatusSchema,
  dshModelDispatchBindingSchema,
  dshRunSessionLinkViewSchema,
  dshSessionPromptSchema,
  researchRunCommandResultSchema,
  researchRunDetailViewSchema,
  researchRunQueuedSchema,
  researchInboxViewSchema,
  researchObservabilityViewSchema,
  researchValueEvaluationSchema,
  researchProviderInputRecordedSchema,
  researchProviderToolEmissionRecordedSchema,
  researchProviderFinishRecordedSchema,
} from '@decision-hub/contracts-ts'
import { terminalAuthoritySchema, type TerminalAuthority } from './terminal-quiescence.js'
import { selectedSkillDeliveryDecisionSchema, type SelectedSkillDeliveryDecision,
  type SelectedSkillIdentity } from './selected-skill-delivery.js'
import { actionSynthesisPreflightSchema, type ActionSynthesisPreflight } from './action-preflight.js'
import { harnessExecutionViewSchema, witnessSummarySchema, type HostScope, type RootConsumption } from './harness-execution.js'
const harnessLinkSchema = dshRunSessionLinkViewSchema.extend({ harness_required: z.literal(true).optional() })

import { safeErrorMessage } from './redaction.js'
import { z } from 'zod'
import { MODEL_BINDING_ERRORS, modelBindingErrorCode } from './model-errors.js'
import { nativeCensusCanonical, type NativeConversationCensusV1 } from './native-conversation-census.js'
import { createHash } from 'node:crypto'
import { providerCanonicalJson } from './provider-input.js'
import { baselinePost, baselineSafeCause, BaselineTransportError } from './baseline-transport.js'
import { nativePerpetualBindingReplySchema } from './perpetual-binding.js'

const nativeConversationCensusRecordedSchema = z.object({
  schema_version: z.literal('research-native-conversation-census-recorded.v1'),
  run_id: z.string().min(1), generation: z.number().int().min(1),
  census_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  reference: z.object({ output_id: z.string().min(1), run_id: z.string().min(1), role: z.string().min(1),
    schema_version: z.string().min(1), payload_hash: z.string().regex(/^[a-f0-9]{64}$/),
    recorded_at: z.string().datetime({ offset: true }) }).strict(),
  producer_authenticated: z.literal(true),
  proof_scope: z.literal('authenticated_host_lifecycle_native_snapshots'),
  model_understanding_verified: z.literal(false), budget_qualified: z.literal(false), financial_qualified: z.literal(false),
}).strict()

/** One shared envelope across all idempotent terminal PUT attempts. */
export const TERMINAL_CALLBACK_BUDGET_MS = 15_000

async function requestLifetime<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted()
  let abort!: () => void
  const cancelled = new Promise<never>((_resolve, reject) => {
    abort = () => reject(signal.reason)
    signal.addEventListener('abort', abort, { once: true })
  })
  try { return await Promise.race([work, cancelled]) }
  finally { signal.removeEventListener('abort', abort) }
}

function researchEventHint(text: string): string | null {
  const normalized = text.normalize('NFKC').toLowerCase()
  const mentionsBtc = /\bbtc\b|比特币/u.test(normalized)
  const requestsSnapshot = /快照|snapshot/u.test(normalized)
  // Users do not need to know the internal "snapshot" label.  Current BTC
  // questions commonly name the market fields directly; treating those as a
  // snapshot keeps them out of the macro-event contract, whose hard event
  // offsets cannot be satisfied by a live quote.
  const requestsMarketState = /现货|永续|资金费|未平仓|持仓量|盘口|成交量|价格|基差|funding|open\s*interest|order\s*book|spot|perpetual|volume|price|basis/u.test(normalized)
  const requestsCurrent = /当前|实时|current|latest|live/u.test(normalized)
  return mentionsBtc && (requestsSnapshot || requestsMarketState) && requestsCurrent
    ? 'crypto_market_snapshot'
    : null
}

export class HubClientError extends Error {
  constructor(readonly code: string, readonly retryable: boolean, cause?: unknown) {
    super(code, { cause })
  }
}

/** Single read budget, independent from fast health/status and write callbacks. */
export function managedReadBudgetMs(deadlineAt?: string | null): number {
  if (deadlineAt == null) return 60_000 // Legacy callers retain their ABI.
  const remaining = Date.parse(deadlineAt) - Date.now()
  if (!Number.isFinite(remaining) || remaining < 1) throw new HubClientError('dsh_host_read_deadline_elapsed', false)
  return Math.min(60_000, remaining)
}

export function managedPreStepBudgetMs(modelStepTimeoutMs: number, deadlineAt?: string | null, legacyMs = 30_000): number {
  if (deadlineAt == null) return legacyMs
  const remaining = Date.parse(deadlineAt) - Date.now()
  if (!Number.isFinite(remaining) || remaining < 1 || !Number.isSafeInteger(modelStepTimeoutMs) || modelStepTimeoutMs < 1) {
    throw new HubClientError('dsh_host_read_deadline_elapsed', false)
  }
  return Math.min(modelStepTimeoutMs, remaining)
}

export function managedContextReadBudgetMs(deadlineAt?: string | null, outerRemainingMs?: number, legacyMs = 30_000): number {
  const budget = deadlineAt == null ? legacyMs : managedReadBudgetMs(deadlineAt)
  if (outerRemainingMs === undefined) return budget
  if (!Number.isFinite(outerRemainingMs) || outerRemainingMs < 1) throw new HubClientError('dsh_host_read_deadline_elapsed', false)
  return Math.min(budget, outerRemainingMs)
}

const skillCallbackIdentitySchema = z.object({
  run_id: z.string().min(1), generation: z.number().int().min(1),
  request_hash: z.string().regex(/^[a-f0-9]{64}$/),
  root_session_id: z.string().min(1), execution_session_id: z.string().min(1),
})
const skillCallbackJobSchema = z.object({
  schema_version: z.literal('research-skill-callback-job.v1'),
  job_id: z.string().min(1), run_id: z.string().min(1), generation: z.number().int().min(1),
  request_hash: z.string().regex(/^[a-f0-9]{64}$/), proof_request_hash: z.string().regex(/^[a-f0-9]{64}$/),
  status: z.enum(['queued', 'running', 'retry_wait', 'completed', 'rejected', 'cancelled', 'expired', 'superseded']),
  poll_after_ms: z.number().int().min(0).max(60_000),
  result: z.unknown().optional(), result_json: z.string().optional(),
  result_hash: z.string().regex(/^[a-f0-9]{64}$/).optional(), error_code: z.string().optional(),
})

/** Wait cancels only this Host observer. The durable job owns its business lifetime. */
async function skillCallbackDelay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted()
  let abort!: () => void
  let timer!: ReturnType<typeof setTimeout>
  try {
    await new Promise<void>((resolve, reject) => {
      abort = () => reject(signal.reason)
      signal.addEventListener('abort', abort, { once: true })
      timer = setTimeout(resolve, ms)
    })
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', abort)
  }
}

export interface HubClientOptions {
  baseUrl: string
  callbackKey: string
  timeoutMs: number
  attempts: number
  ownerId: string
  modelBindingTimeoutMs?: number
  managedReadDeadline?: (id: string) => string | null | undefined
  /** Explicit rollout: legacy synchronous callbacks remain the default. */
  durableSkillCallbacks?: { protocolVersion: 'research-skill-callback.v1' }
  fetchImpl?: typeof fetch
}

export class HubClient {
  private readonly fetchImpl: typeof fetch
  constructor(private readonly options: HubClientOptions) {
    this.fetchImpl = options.fetchImpl ?? fetch
  }

  async selectedSkillDelivery(identity: SelectedSkillIdentity, signal: AbortSignal, originalDeadlineAt: string): Promise<SelectedSkillDeliveryDecision> {
    if (identity.rootSessionId !== identity.executionSessionId) throw new HubClientError('skill_root_session_required', false)
    const query = new URLSearchParams({ generation: String(identity.generation),
      execution_session_id: identity.executionSessionId, request_hash: identity.requestHash })
    return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(identity.runId)}/skills/selected-delivery?${query}`,
      identity.runId, selectedSkillDeliveryDecisionSchema, signal, originalDeadlineAt, 'research-proof')
  }

  async skillCallback(kind: 'tool' | 'records', payload: { identity: { run_id: string } }, signal: AbortSignal): Promise<unknown> {
    if (this.options.durableSkillCallbacks !== undefined) return this.durableSkillCallback(kind, payload, signal)
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.identity.run_id)}/skills/${kind}`, {
      method: 'POST', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }, 1, [], { signal, timeoutMs: 60_000, timeoutCode: 'skill_callback_transport_timeout',
      cancelledCode: 'skill_callback_cancelled', safeSkillErrors: true })
    return this.json(response)
  }

  private async durableSkillCallback(kind: 'tool' | 'records', payload: { identity: { run_id: string } }, signal: AbortSignal): Promise<unknown> {
    if (this.options.durableSkillCallbacks?.protocolVersion !== 'research-skill-callback.v1') {
      throw new HubClientError('skill_callback_protocol_invalid', false)
    }
    const identity = skillCallbackIdentitySchema.safeParse(payload.identity)
    if (!identity.success) throw new HubClientError('skill_callback_identity_invalid', false)
    const deadlineAt = this.options.managedReadDeadline?.(identity.data.run_id)
    if (typeof deadlineAt !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(deadlineAt)
      || !Number.isFinite(Date.parse(deadlineAt))) throw new HubClientError('skill_callback_deadline_unavailable', false)
    // Freeze the existing owned deadline. Polling and retries cannot extend it.
    const deadlineMs = Date.parse(deadlineAt)
    const encoded = providerCanonicalJson({ protocol_version: 'research-skill-callback.v1', kind, body: payload })
    const proofHash = createHash('sha256').update(encoded, 'utf8').digest('hex')
    const basePath = `/v1/dsh/sessions/${encodeURIComponent(identity.data.run_id)}/skills/callbacks`
    const query = new URLSearchParams({ generation: String(identity.data.generation),
      execution_session_id: identity.data.execution_session_id, request_hash: identity.data.request_hash })
    let jobId: string | undefined
    let submitted = false
    let pauseMs = 0
    const remaining = (): number => {
      if (signal.aborted) throw new HubClientError('skill_callback_cancelled', false)
      const value = deadlineMs - Date.now()
      if (value < 1) throw new HubClientError(submitted ? 'skill_callback_commit_unconfirmed' : 'skill_callback_deadline_elapsed', false)
      return value
    }
    for (;;) {
      if (pauseMs > 0) {
        try { await skillCallbackDelay(Math.min(pauseMs, remaining()), signal) }
        catch (error) {
          if (signal.aborted) throw new HubClientError('skill_callback_cancelled', false)
          throw error
        }
      }
      const budget = Math.min(60_000, remaining())
      const path = jobId === undefined ? basePath : `${basePath}/${encodeURIComponent(jobId)}?${query}`
      const init: RequestInit = jobId === undefined
        ? { method: 'POST', body: encoded, headers: { 'content-type': 'application/json' } }
        : { method: 'GET' }
      // A lost POST reply is commit-unknown. Replay these identical bytes only;
      // the server deduplicates by the full authenticated callback identity.
      if (jobId === undefined) submitted = true
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), budget)
      const lifetime = AbortSignal.any([signal, controller.signal])
      const work = (async () => {
        const response = await this.request(path, init, 1, [], { signal: lifetime, timeoutMs: budget,
          timeoutCode: 'skill_callback_transport_timeout', cancelledCode: 'skill_callback_cancelled', safeSkillErrors: true })
        const body = await this.json(response)
        lifetime.throwIfAborted()
        return { response, body }
      })()
      void work.catch(() => undefined)
      let reply: Awaited<typeof work>
      try { reply = await requestLifetime(work, lifetime) }
      catch (error) {
        if (signal.aborted) throw new HubClientError('skill_callback_cancelled', false)
        remaining()
        // A transport failure is never a rejected job or a successful tool result.
        if (controller.signal.aborted || error instanceof HubClientError
          && (error.code === 'skill_callback_transport_timeout' || error.retryable
            || error.code === 'host_hub_response_invalid' && error.cause instanceof TypeError)) {
          pauseMs = 1_000
          continue
        }
        throw error
      } finally { clearTimeout(timer); controller.abort() }
      const parsed = skillCallbackJobSchema.safeParse(reply.body)
      if (!parsed.success) throw new HubClientError('skill_callback_receipt_invalid', false)
      const job = parsed.data
      if (job.run_id !== identity.data.run_id || job.generation !== identity.data.generation
        || job.request_hash !== identity.data.request_hash || job.proof_request_hash !== proofHash
        || jobId !== undefined && job.job_id !== jobId) throw new HubClientError('skill_callback_receipt_mismatch', false)
      jobId = job.job_id
      if (job.status === 'queued' || job.status === 'running' || job.status === 'retry_wait') {
        if (reply.response.status !== 202 || job.poll_after_ms < 1 || Object.hasOwn(reply.body as object, 'result')) {
          throw new HubClientError('skill_callback_receipt_invalid', false)
        }
        pauseMs = job.poll_after_ms
        continue
      }
      if (job.status !== 'completed') {
        const code = typeof job.error_code === 'string' && job.error_code.length <= 160
          && /^skill_[a-z_]{1,100}(?::[a-zA-Z0-9_.:]*)?$/.test(job.error_code)
          ? job.error_code : `skill_callback_job_${job.status}`
        throw new HubClientError(code, false)
      }
      if (reply.response.status !== 200) throw new HubClientError('skill_callback_receipt_invalid', false)
      if (!Object.hasOwn(reply.body as object, 'result') || typeof job.result_json !== 'string'
        || typeof job.result_hash !== 'string'
        || createHash('sha256').update(job.result_json, 'utf8').digest('hex') !== job.result_hash) {
        throw new HubClientError('skill_callback_result_mismatch', false)
      }
      let result: unknown
      try {
        result = JSON.parse(job.result_json)
        if (providerCanonicalJson(result) !== providerCanonicalJson(job.result)) throw new Error('result mismatch')
      } catch { throw new HubClientError('skill_callback_result_mismatch', false) }
      remaining()
      return result
    }
  }

  async readiness(): Promise<boolean> {
    try {
      const response = await this.request('/health/ready', { method: 'GET' }, 1)
      return response.ok
    } catch {
      return false
    }
  }

  async prompt(runId: string, generation: number, signal?: AbortSignal, deadlineAt?: string | null): Promise<DshSessionPrompt> {
    if (deadlineAt != null) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(runId)}/prompt?generation=${generation}`,
      runId, dshSessionPromptSchema, signal, deadlineAt, 'research-proof')
    const response = await this.managedRead(`/v1/dsh/sessions/${encodeURIComponent(runId)}/prompt?generation=${generation}`, signal,
      this.options.managedReadDeadline?.(runId))
    return dshSessionPromptSchema.parse(response)
  }

  async link(runId: string): Promise<DshRunSessionLinkView> {
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}`, { method: 'GET' })
    return dshRunSessionLinkViewSchema.parse(await this.json(response))
  }

  async linkBySession(sessionId: string, signal?: AbortSignal, deadlineAt?: string | null): Promise<DshRunSessionLinkView & { harness_required?: true }> {
    const response = await this.managedRead(`/v1/dsh/sessions/by-session/${encodeURIComponent(sessionId)}`, signal,
      deadlineAt ?? this.options.managedReadDeadline?.(sessionId))
    return harnessLinkSchema.parse(response)
  }

  private async managedRead(path: string, signal?: AbortSignal, deadlineAt?: string | null): Promise<unknown> {
    const budget = managedReadBudgetMs(deadlineAt)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), budget)
    try {
      const response = await this.request(path, { method: 'GET' }, 1, [], {
        signal: signal ? AbortSignal.any([controller.signal, signal]) : controller.signal,
        timeoutMs: budget, timeoutCode: 'dsh_host_read_transport_timeout', cancelledCode: 'dsh_host_read_cancelled' })
      const value = await this.json(response)
      if (signal?.aborted) throw new HubClientError('dsh_host_read_cancelled', false)
      if (controller.signal.aborted) throw new HubClientError('dsh_host_read_transport_timeout', false)
      return value
    } catch (error) {
      if (signal?.aborted) throw new HubClientError('dsh_host_read_cancelled', false)
      if (controller.signal.aborted) throw new HubClientError('dsh_host_read_transport_timeout', false)
      throw error
    } finally { clearTimeout(timer) }
  }

  modelBindingBudgetMs(): number {
    const value = this.options.modelBindingTimeoutMs ?? 30_000
    if (!Number.isSafeInteger(value) || value < 1_000 || value > 30_000) {
      throw new HubClientError('dsh_model_binding_budget_invalid', false)
    }
    return value
  }

  async harnessExecution(runId: string, generation: number, scope: HostScope, signal: AbortSignal, originalDeadlineAt?: string) {
    if (originalDeadlineAt !== undefined) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(runId)}/harness-execution?generation=${generation}`,
      runId, harnessExecutionViewSchema, signal, originalDeadlineAt, 'research-proof', scope, { method: 'POST' })
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/harness-execution?generation=${generation}`, {
      method: 'POST', body: JSON.stringify(scope), headers: { 'content-type': 'application/json' },
    }, 1, [], { signal, timeoutMs: this.modelBindingBudgetMs() })
    return harnessExecutionViewSchema.parse(await this.json(response))
  }

  async recordHarnessRoot(payload: RootConsumption, signal: AbortSignal, originalDeadlineAt?: string) {
    if (originalDeadlineAt !== undefined) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/harness-root-consumption`,
      payload.run_id, witnessSummarySchema, signal, originalDeadlineAt, 'research-proof', payload)
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/harness-root-consumption`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }, 1, [], { signal, timeoutMs: this.modelBindingBudgetMs() })
    return witnessSummarySchema.parse(await this.json(response))
  }

  private async providerCallback<T>(path: string, payload: unknown, schema: z.ZodType<T>, signal?: AbortSignal, budgetMs?: number, phase: 'input' | 'emission' | 'finish' = 'input', receiptDeadlineAt?: string): Promise<T> {
    if (receiptDeadlineAt !== undefined) return this.originalProviderCallback(path, payload, schema, signal, budgetMs, phase, receiptDeadlineAt)
    const timeoutMs = budgetMs ?? this.modelBindingBudgetMs()
    const capMs = 60_000
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > capMs) {
      throw new HubClientError('dsh_provider_input_budget_elapsed', false)
    }
    let response: Response
    try {
      response = await this.request(path, {
        method: 'PUT', body: JSON.stringify(payload), headers: {
          'content-type': 'application/json', 'x-decision-hub-provider-input-budget-ms': String(timeoutMs),
        },
      }, 1, MODEL_BINDING_ERRORS, { signal, timeoutMs })
    } catch (error) {
      // request() keeps its model-binding ABI; receipt IO has distinct causes.
      if (error instanceof HubClientError && error.code === 'dsh_model_binding_budget_elapsed') {
        throw new HubClientError(`dsh_provider_${phase}_transport_timeout`, false)
      }
      if (error instanceof HubClientError && error.code === 'dsh_model_binding_cancelled') {
        throw new HubClientError(`dsh_provider_${phase}_transport_cancelled`, false)
      }
      if (error instanceof HubClientError && modelBindingErrorCode(error.code)) throw error
      throw new HubClientError('dsh_provider_callback_refused', false)
    }
    signal?.throwIfAborted()
    try { return schema.parse(await this.json(response)) }
    catch {
      throw new HubClientError('dsh_provider_input_receipt_mismatch', false)
    }
  }

  /** Same physical callback, bounded by an existing Task/step absolute deadline. */
  private async originalProviderCallback<T>(path: string, payload: unknown, schema: z.ZodType<T>, parent: AbortSignal | undefined,
    budgetMs: number | undefined, phase: 'input' | 'emission' | 'finish', receiptDeadlineAt: string): Promise<T> {
    const now = Date.now()
    const runId = (payload as { run_id?: string }).run_id
    const taskDeadline = runId ? this.options.managedReadDeadline?.(runId) : undefined
    const limits = [Date.parse(receiptDeadlineAt), now + (budgetMs ?? NaN),
      ...(taskDeadline == null ? [] : [Date.parse(taskDeadline)])]
    if (limits.some(value => !Number.isFinite(value)) || !Number.isSafeInteger(budgetMs)
      || budgetMs! < 1 || budgetMs! > 2_147_483_647) throw new HubClientError('dsh_provider_input_budget_elapsed', false)
    const deadline = Math.min(...limits)
    if (deadline <= now) throw new HubClientError(`dsh_provider_${phase}_deadline_elapsed`, false)
    const controller = new AbortController(), signal = parent ? AbortSignal.any([parent, controller.signal]) : controller.signal
    const timer = setTimeout(() => controller.abort(), deadline - now)
    const actualDeadline = new Date(deadline).toISOString()
    let text: string, status = 0
    try {
      if (this.options.fetchImpl) {
        const response = await this.request(path, { method: 'PUT', body: JSON.stringify(payload), headers: {
          'content-type': 'application/json', 'x-decision-hub-provider-input-budget-ms': String(Math.floor(deadline - Date.now())),
          'x-decision-hub-provider-input-deadline-at': actualDeadline,
        }, redirect: 'error' }, 1, MODEL_BINDING_ERRORS, { signal, timeoutMs: Math.floor(deadline - Date.now()) })
        status = response.status
        text = await response.text()
      } else {
        text = await baselinePost({ url: new URL(path, this.options.baseUrl), authKey: this.options.callbackKey,
          body: JSON.stringify(payload), signal, deadlineAtMs: deadline, method: 'PUT', receiptDeadlineAt: actualDeadline,
          allowErrorResponse: true, onStatus: value => { status = value } })
      }
      if (Date.now() >= deadline) throw new HubClientError(`dsh_provider_${phase}_transport_timeout`, false)
      if (parent?.aborted) throw new HubClientError(`dsh_provider_${phase}_transport_cancelled`, false)
      if (Buffer.byteLength(text, 'utf8') > 1_000_000) throw new HubClientError('dsh_provider_input_receipt_mismatch', false)
      let value: unknown
      try { value = JSON.parse(text) } catch { throw new HubClientError('dsh_provider_input_receipt_mismatch', false) }
      if (status >= 400) {
        const detail = typeof value === 'object' && value !== null && 'detail' in value ? value.detail : null
        if (status === 409 && typeof detail === 'string' && modelBindingErrorCode(detail)) throw new HubClientError(detail, false)
        throw new HubClientError('dsh_provider_callback_refused', false)
      }
      try { return schema.parse(value) } catch { throw new HubClientError('dsh_provider_input_receipt_mismatch', false) }
    } catch (error) {
      // The owned Node path has closed request/body before reaching this catch.
      if (Date.now() >= deadline || controller.signal.aborted) throw new HubClientError(`dsh_provider_${phase}_transport_timeout`, false)
      if (parent?.aborted) throw new HubClientError(`dsh_provider_${phase}_transport_cancelled`, false)
      if (error instanceof HubClientError && modelBindingErrorCode(error.code)) throw error
      if (error instanceof BaselineTransportError) throw new HubClientError('dsh_provider_callback_refused', false, error.cause)
      throw new HubClientError('dsh_provider_callback_refused', false)
    } finally { clearTimeout(timer); controller.abort() }
  }

  async recordProviderInput(payload: ResearchProviderInputSubmit, signal?: AbortSignal, budgetMs?: number, receiptDeadlineAt?: string): Promise<ResearchProviderInputRecorded> {
    return this.providerCallback(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/provider-inputs/${encodeURIComponent(payload.dispatch_id)}`,
      payload, researchProviderInputRecordedSchema, signal, budgetMs, 'input', receiptDeadlineAt)
  }

  async recordProviderToolEmission(payload: ResearchProviderToolEmissionSubmit, signal?: AbortSignal, budgetMs?: number, receiptDeadlineAt?: string): Promise<ResearchProviderToolEmissionRecorded> {
    return this.providerCallback(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/provider-inputs/${encodeURIComponent(payload.dispatch_id)}/tool-emissions/${encodeURIComponent(payload.call_id)}`,
      payload, researchProviderToolEmissionRecordedSchema, signal, budgetMs, 'emission', receiptDeadlineAt)
  }

  async recordProviderFinish(payload: ResearchProviderFinishSubmit, signal?: AbortSignal, budgetMs?: number, receiptDeadlineAt?: string): Promise<ResearchProviderFinishRecorded> {
    return this.providerCallback(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/provider-inputs/${encodeURIComponent(payload.dispatch_id)}/finish`,
      payload, researchProviderFinishRecordedSchema, signal, budgetMs, 'finish', receiptDeadlineAt)
  }

  async providerToolEmission(runId: string, generation: number, executionSessionId: string, callId: string, signal?: AbortSignal, originalDeadlineAt?: string): Promise<ResearchProviderToolEmissionRecorded> {
    const query = new URLSearchParams({ generation: String(generation), execution_session_id: executionSessionId })
    if (originalDeadlineAt !== undefined) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(runId)}/provider-tool-emissions/${encodeURIComponent(callId)}?${query}`,
      runId, researchProviderToolEmissionRecordedSchema, signal, originalDeadlineAt, 'research-proof')
    let response: Response
    try {
      response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/provider-tool-emissions/${encodeURIComponent(callId)}?${query}`,
        { method: 'GET' }, 1, MODEL_BINDING_ERRORS, { signal, timeoutMs: this.modelBindingBudgetMs() })
    } catch (error) {
      if (error instanceof HubClientError && modelBindingErrorCode(error.code)) throw error
      throw new HubClientError('dsh_provider_emission_unavailable', false)
    }
    signal?.throwIfAborted()
    try { return researchProviderToolEmissionRecordedSchema.parse(await this.json(response)) }
    catch {
      throw new HubClientError('dsh_provider_emission_receipt_mismatch', false)
    }
  }

  async actionSynthesisPreflight(runId: string, generation: number, executionSessionId: string, callId: string, signal?: AbortSignal, originalDeadlineAt?: string): Promise<ActionSynthesisPreflight> {
    const query = new URLSearchParams({ generation: String(generation), execution_session_id: executionSessionId })
    if (originalDeadlineAt !== undefined) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(runId)}/action-preflight/${encodeURIComponent(callId)}?${query}`,
      runId, actionSynthesisPreflightSchema, signal, originalDeadlineAt, 'research-proof')
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/action-preflight/${encodeURIComponent(callId)}?${query}`,
      { method: 'GET' }, 1, MODEL_BINDING_ERRORS, { signal, timeoutMs: this.modelBindingBudgetMs() })
    signal?.throwIfAborted()
    const parsed = actionSynthesisPreflightSchema.safeParse(await this.json(response))
    if (!parsed.success) throw new HubClientError('dsh_action_preflight_response_invalid', false)
    return parsed.data
  }

  /** Separate immutable observation, one transport attempt, never part of result_hash. */
  async recordNativeConversationCensus(payload: NativeConversationCensusV1, signal: AbortSignal, budgetMs: number) {
    if (!Number.isSafeInteger(budgetMs) || budgetMs < 1 || budgetMs > this.modelBindingBudgetMs()) {
      throw new HubClientError('dsh_native_census_budget_invalid', false)
    }
    const { census_sha256, ...body } = payload
    const encoded = nativeCensusCanonical(payload)
    if (Buffer.byteLength(encoded, 'utf8') > 8_388_608
      || createHash('sha256').update(nativeCensusCanonical(body), 'utf8').digest('hex') !== census_sha256) {
      throw new HubClientError('dsh_native_census_original_invalid', false)
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), budgetMs)
    const bounded = AbortSignal.any([signal, controller.signal])
    try {
      bounded.throwIfAborted()
      const request = this.request(
        `/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/native-conversation-censuses/${payload.generation}`,
        { method: 'POST', body: encoded, headers: { 'content-type': 'application/json',
          'x-decision-hub-provider-input-budget-ms': String(budgetMs) } }, 1, [], { signal: bounded, timeoutMs: budgetMs })
      // The transport can synchronously abort its supplied signal. Attach a
      // rejection handler before the lifetime helper checks the new state.
      void request.catch(() => undefined)
      const response = await requestLifetime(request, bounded)
      const result = nativeConversationCensusRecordedSchema.parse(await requestLifetime(this.json(response), bounded))
      bounded.throwIfAborted()
      if (result.run_id !== payload.run_id || result.generation !== payload.generation || result.census_sha256 !== census_sha256
        || result.reference.run_id !== payload.run_id || result.reference.role !== `native_conversation_census:${payload.generation}`
        || result.reference.schema_version !== 'research-native-conversation-census.v1') {
        throw new HubClientError('dsh_native_census_receipt_mismatch', false)
      }
      return result
    } catch (error) {
      if (signal.aborted) throw new HubClientError('dsh_native_census_transport_cancelled', false)
      if (controller.signal.aborted) throw new HubClientError('dsh_native_census_transport_timeout', false)
      if (error instanceof HubClientError && error.code.startsWith('dsh_native_census_')) throw error
      throw new HubClientError('dsh_native_census_recording_refused', false)
    } finally { clearTimeout(timer); controller.abort() }
  }

  async terminalAuthority(runId: string, signal?: AbortSignal, originalDeadlineAt?: string): Promise<TerminalAuthority> {
    if (originalDeadlineAt !== undefined) return this.originalResearchProof(
      `/v1/dsh/sessions/${encodeURIComponent(runId)}/terminal-quiescence-authority`,
      runId, terminalAuthoritySchema, signal, originalDeadlineAt, 'research-proof')
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/terminal-quiescence-authority`, { method: 'GET' }, 1, [], { signal })
    return terminalAuthoritySchema.parse(await this.json(response))
  }

  async businessStatus(runId: string, signal?: AbortSignal, deadlineAt?: string, projection: 'full' | 'lifecycle' = 'full'): Promise<DshBusinessStatus> {
    const path = `/v1/dsh/sessions/${encodeURIComponent(runId)}/business-status${projection === 'lifecycle' ? '?projection=lifecycle' : ''}`
    if (deadlineAt !== undefined && projection === 'full') return this.originalResearchProof(
      path, runId, dshBusinessStatusSchema, signal, deadlineAt, 'research-proof')
    if (deadlineAt !== undefined) return dshBusinessStatusSchema.parse(await this.managedRead(path, signal, deadlineAt))
    const response = await this.request(path, { method: 'GET' })
    return dshBusinessStatusSchema.parse(await this.json(response))
  }

  async researchDetail(runId: string, signal?: AbortSignal, deadlineAt?: string): Promise<ResearchRunDetailView> {
    if (deadlineAt !== undefined) return this.originalResearchProof(
      `/v1/research/runs/${encodeURIComponent(runId)}`, runId, researchRunDetailViewSchema,
      signal, deadlineAt, 'research-proof')
    const response = await this.request(`/v1/research/runs/${encodeURIComponent(runId)}`, { method: 'GET' }, this.options.attempts, [], { signal })
    return researchRunDetailViewSchema.parse(await this.json(response))
  }

  async perpetualResearchBinding(runId: string, requestHash: string, generation: number, signal: AbortSignal, deadlineAt: string, sourceCallId?: string) {
    const value = await this.originalResearchProof(`/v1/research/runs/${encodeURIComponent(runId)}/decision-binding?request_hash=${encodeURIComponent(requestHash)}&generation=${generation}${sourceCallId === undefined ? '' : `&source_call_id=${encodeURIComponent(sourceCallId)}`}`,
      runId, nativePerpetualBindingReplySchema, signal, deadlineAt, 'research-proof')
    if (value.run_id !== runId || value.request_hash !== requestHash || value.generation !== generation) throw new HubClientError('decision_hub_perpetual_admission_identity_mismatch', false)
    return { binding: { opportunity_id: value.opportunity_id, context: value.context, candidate_target_ref: value.candidate_target_ref,
      anchor_rule_ref: value.anchor_rule_ref, method_ref: value.method_ref, method_code_hash: value.method_code_hash }, reviewOriginals: value.review_originals }
  }

  async researchInbox(limit = 100): Promise<ResearchInboxView> {
    const bounded = Math.max(1, Math.min(500, Math.trunc(limit)))
    const response = await this.request(`/v1/research/inbox?limit=${bounded}`, { method: 'GET' })
    return researchInboxViewSchema.parse(await this.json(response))
  }

  async dataJobs(limit = 20): Promise<unknown> {
    const bounded = Math.max(1, Math.min(100, Math.trunc(limit)))
    const response = await this.request(`/v1/data-jobs?limit=${bounded}`, { method: 'GET' })
    return this.json(response)
  }

  async researchObservability(runId: string): Promise<ResearchObservabilityView> {
    const response = await this.request(
      `/v1/research/runs/${encodeURIComponent(runId)}/observability`,
      { method: 'GET' },
    )
    return researchObservabilityViewSchema.parse(await this.json(response))
  }

  async researchValueEvaluation(runId: string): Promise<ResearchValueEvaluation> {
    const response = await this.request(
      `/v1/research/runs/${encodeURIComponent(runId)}/value-evaluation`,
      { method: 'GET' },
    )
    return researchValueEvaluationSchema.parse(await this.json(response))
  }

  async accepted(payload: DshSessionAccepted): Promise<void> {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/accepted`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }))
  }

  /** One original research proof request; no arbitrary 30s cap or IO replay. */
  private async originalResearchProof<S extends z.ZodTypeAny>(path: string, runId: string, schema: S,
    parent: AbortSignal | undefined, originalDeadlineAt: string,
    kind: 'model-binding' | 'research-proof', payload?: unknown,
    proof: { method?: 'POST' | 'PUT'; harnessWitnessHash?: string } = {}): Promise<z.output<S>> {
    const now = Date.now()
    const task = this.options.managedReadDeadline?.(runId)
    const limits = [Date.parse(originalDeadlineAt), ...(task == null ? [] : [Date.parse(task)])]
    const invalid = kind === 'model-binding' ? 'dsh_model_binding_budget_invalid' : 'dsh_host_read_deadline_elapsed'
    const timeout = kind === 'model-binding' ? 'dsh_model_binding_budget_elapsed' : 'dsh_host_read_transport_timeout'
    const cancelled = kind === 'model-binding' ? 'dsh_model_binding_cancelled' : 'dsh_host_read_cancelled'
    if (limits.some(value => !Number.isFinite(value))) throw new HubClientError(invalid, false)
    const deadline = Math.min(...limits)
    if (deadline <= now || deadline - now > 2_147_483_647) throw new HubClientError(timeout, false)
    if (proof.harnessWitnessHash !== undefined && (kind !== 'model-binding'
      || !/^[a-f0-9]{64}$/.test(proof.harnessWitnessHash))) throw new HubClientError(invalid, false)
    if (proof.method !== undefined && payload === undefined) throw new HubClientError(invalid, false)
    const controller = new AbortController()
    const signal = parent ? AbortSignal.any([parent, controller.signal]) : controller.signal
    const timer = setTimeout(() => controller.abort(), deadline - now)
    const method = proof.method ?? (payload === undefined ? 'GET' : 'PUT')
    const body = payload === undefined ? '' : JSON.stringify(payload)
    const actualDeadline = new Date(deadline).toISOString()
    let status = 0, text: string
    try {
      if (this.options.fetchImpl) {
        const prefix = kind === 'model-binding' ? 'x-decision-hub-model-binding' : 'x-decision-hub-research-proof'
        const response = await this.request(path, { method, ...(payload === undefined ? {} : { body }), headers: {
          'content-type': 'application/json', [`${prefix}-deadline-at`]: actualDeadline,
          ...(proof.harnessWitnessHash === undefined ? {} : { 'x-decision-hub-harness-witness': proof.harnessWitnessHash }),
          [`${prefix}-budget-ms`]: String(Math.floor(deadline - Date.now())),
        }, redirect: 'error' }, 1, MODEL_BINDING_ERRORS,
        { signal, timeoutMs: Math.floor(deadline - Date.now()), timeoutCode: timeout, cancelledCode: cancelled })
        status = response.status
        text = await response.text()
      } else {
        text = await baselinePost({ url: new URL(path, this.options.baseUrl), authKey: this.options.callbackKey,
          body, signal, deadlineAtMs: deadline, method, receiptDeadlineAt: actualDeadline, proofKind: kind,
          harnessWitnessHash: proof.harnessWitnessHash,
          allowErrorResponse: true, onStatus: value => { status = value } })
      }
      if (Date.now() >= deadline) throw new HubClientError(timeout, false)
      if (parent?.aborted) throw new HubClientError(cancelled, false)
      if (Buffer.byteLength(text, 'utf8') > 1_000_000) throw new HubClientError('dsh_model_binding_receipt_mismatch', false)
      let value: unknown
      try { value = JSON.parse(text) } catch { throw new HubClientError('dsh_model_binding_receipt_mismatch', false) }
      if (status >= 400) {
        const detail = typeof value === 'object' && value !== null && 'detail' in value ? value.detail : null
        if (status === 409 && typeof detail === 'string'
          && (modelBindingErrorCode(detail) || /^action_preflight_[a-z_]{1,100}$/.test(detail))) {
          throw new HubClientError(detail, detail === 'dsh_model_binding_writer_busy')
        }
        throw new HubClientError('dsh_model_binding_unavailable', false)
      }
      try { return schema.parse(value) } catch { throw new HubClientError('dsh_model_binding_receipt_mismatch', false) }
    } catch (error) {
      if (error instanceof BaselineTransportError) process.stderr.write(JSON.stringify({
        event: 'decision_hub_research_proof_transport_failed', proof_kind: kind,
        run_id: /^run_[a-f0-9]{32}$/.test(runId) ? runId : 'unrecognized',
        phase: error.phase, code: error.code, cause_code: baselineSafeCause(error)?.code,
      }) + '\n')
      if (Date.now() >= deadline || controller.signal.aborted) throw new HubClientError(timeout, false)
      if (parent?.aborted) throw new HubClientError(cancelled, false)
      if (error instanceof HubClientError) throw error
      if (error instanceof BaselineTransportError) throw new HubClientError('dsh_model_binding_unavailable', false,
        { phase: error.phase, ...baselineSafeCause(error) })
      throw new HubClientError('dsh_model_binding_unavailable', false, baselineSafeCause(error))
    } finally { clearTimeout(timer) }
  }

  async bindModel(payload: DshModelDispatchBinding, signal?: AbortSignal, harness?: { witnessHash: string; budgetMs: number }, originalDeadlineAt?: string): Promise<void> {
    if (harness !== undefined && !/^[a-f0-9]{64}$/.test(harness.witnessHash)) throw new HubClientError('dsh_model_binding_budget_invalid', false)
    if (originalDeadlineAt !== undefined) {
      const receipt = await this.originalResearchProof(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/model-binding`,
        payload.run_id, dshModelDispatchBindingSchema, signal, originalDeadlineAt, 'model-binding', payload,
        { harnessWitnessHash: harness?.witnessHash })
      if (Object.keys(payload).some(key => receipt[key as keyof DshModelDispatchBinding] !== payload[key as keyof DshModelDispatchBinding])) {
        throw new HubClientError('dsh_model_binding_receipt_mismatch', false)
      }
      return
    }

    const timeoutMs = harness?.budgetMs ?? this.options.modelBindingTimeoutMs ?? 30_000
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1_000 || timeoutMs > 30_000) {
      throw new HubClientError('dsh_model_binding_budget_invalid', false)
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const lifetime = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal
    const operation = async () => {
      lifetime.throwIfAborted()
      const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/model-binding`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json', 'x-decision-hub-model-binding-budget-ms': String(timeoutMs),
        ...(harness ? { 'x-decision-hub-harness-witness': harness.witnessHash } : {}), },
      }, 1, MODEL_BINDING_ERRORS, { signal: lifetime, timeoutMs })
      const receipt = dshModelDispatchBindingSchema.parse(await this.json(response))
      lifetime.throwIfAborted()
      if (Object.keys(payload).some(key => receipt[key as keyof DshModelDispatchBinding] !== payload[key as keyof DshModelDispatchBinding])) {
        throw new HubClientError('dsh_model_binding_receipt_mismatch', false)
      }
    }
    try { await requestLifetime(operation(), lifetime) }
    catch (error) {
      if (signal?.aborted) throw new HubClientError('dsh_model_binding_cancelled', false)
      if (controller.signal.aborted) throw new HubClientError('dsh_model_binding_budget_elapsed', false)
      throw error
    } finally { clearTimeout(timer) }
  }

  async status(payload: DshSessionStatus): Promise<void> {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/status`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }))
  }

  async terminal(payload: DshSessionCompletion): Promise<void> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), TERMINAL_CALLBACK_BUDGET_MS)
    try {
      const request = this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/terminal`, {
        method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
      }, this.options.attempts, [], {
        signal: controller.signal,
        cancelledCode: 'host_terminal_callback_deadline_elapsed',
      })
      // Some controlled transports do not settle their fetch promise when the
      // signal aborts. Bound both response and body reads by the same envelope.
      void request.catch(() => undefined)
      const response = await requestLifetime(request, controller.signal)
      await requestLifetime(this.json(response), controller.signal)
    } catch (error) {
      if (controller.signal.aborted) {
        throw new HubClientError('host_terminal_callback_deadline_elapsed', true)
      }
      throw error
    } finally {
      clearTimeout(timer)
      controller.abort()
    }
  }

  async researchIntake(payload: DshResearchIntake, idempotencyKey: string): Promise<ResearchRunQueued> {
    const eventHint = researchEventHint(payload.text)
    const response = await this.request('/v1/research/observations', {
      method: 'POST',
      body: JSON.stringify({
        text: payload.text,
        source_id: payload.source_id,
        source_type: 'manual',
        language: payload.language,
        ...(eventHint === null ? {} : { event_hint: eventHint }),
      }),
      headers: {
        'content-type': 'application/json',
        'idempotency-key': idempotencyKey,
      },
    })
    return researchRunQueuedSchema.parse(await this.json(response))
  }

  async researchCommand(runId: string, payload: ResearchRunCommand): Promise<ResearchRunCommandResult> {
    const response = await this.request(`/v1/research/runs/${encodeURIComponent(runId)}/commands`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: {
        'content-type': 'application/json',
        'idempotency-key': payload.request_id,
        'x-owner-id': this.options.ownerId,
      },
    })
    return researchRunCommandResultSchema.parse(await this.json(response))
  }

  private async request(path: string, init: RequestInit, attempts = this.options.attempts, knownErrors: readonly string[] = [], control: { signal?: AbortSignal; timeoutMs?: number; timeoutCode?: string; cancelledCode?: string; safeSkillErrors?: boolean } = {}): Promise<Response> {
    let last: unknown
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), control.timeoutMs ?? this.options.timeoutMs)
      const signal = control.signal ? AbortSignal.any([controller.signal, control.signal]) : controller.signal
      try {
        signal.throwIfAborted()
        const response = await this.fetchImpl(new URL(path, this.options.baseUrl), {
          ...init,
          signal,
          headers: {
            'x-decision-hub-bridge-key': this.options.callbackKey,
            ...init.headers,
          },
        })
        if (control.signal?.aborted) throw new HubClientError(control.cancelledCode ?? 'dsh_model_binding_cancelled', false)
        if (control.timeoutMs !== undefined && controller.signal.aborted) throw new HubClientError(control.timeoutCode ?? 'dsh_model_binding_budget_elapsed', false)
        if (response.ok) return response
        if (response.status < 500 || attempt === attempts) {
          if ((knownErrors.length > 0 || control.safeSkillErrors) && response.status === 409) {
            let body: unknown
            try { body = await response.json() } catch { /* Generic HTTP error for a non-JSON reply. */ }
            const detail = typeof body === 'object' && body !== null && 'detail' in body ? body.detail : null
            if (typeof detail === 'string' && knownErrors.includes(detail)) {
              throw new HubClientError(detail, detail === 'dsh_model_binding_writer_busy')
            }
            if (control.safeSkillErrors && typeof detail === 'string' && detail.length <= 160
              && detail.match(/^skill_[a-z_]{1,100}(?::[a-zA-Z0-9_.:]*)?$/)?.[0] === detail) {
              throw new HubClientError(detail, false)
            }
          }
          throw new HubClientError(`host_hub_http_${response.status}`, response.status >= 500)
        }
        last = new Error(`Hub returned ${response.status}`)
      } catch (error) {
        if (control.signal?.aborted) throw new HubClientError(control.cancelledCode ?? 'dsh_model_binding_cancelled', false)
        if (control.timeoutMs !== undefined && controller.signal.aborted) throw new HubClientError(control.timeoutCode ?? 'dsh_model_binding_budget_elapsed', false)
        // A pre-BEGIN busy cause is recoverable after service recovery, but this
        // callback never replays it or replaces its typed cause with transport failure.
        if (error instanceof HubClientError && (!error.retryable || error.code === 'dsh_model_binding_writer_busy')) throw error
        last = error
        if (attempt === attempts) break
      } finally {
        clearTimeout(timer)
      }
    }
    throw new HubClientError('host_callback_unreachable', true, safeErrorMessage(last))
  }

  private async json(response: Response): Promise<unknown> {
    try { return await response.json() } catch (error) {
      throw new HubClientError('host_hub_response_invalid', false, error)
    }
  }
}
