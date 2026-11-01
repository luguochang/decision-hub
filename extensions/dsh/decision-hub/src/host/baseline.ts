import { coverageAssessmentSchema, researchCapabilityResultSchema, researchProviderCallbackReferenceSchema,
  researchSessionRequestSchema, type ResearchSessionRequest } from '@decision-hub/contracts-ts'
import { z } from 'zod'
import { DECISION_SYNTHESIS_REF } from '../synthesis-protocol.js'
import type { CancellationBinding } from './command-cancellation.js'
import type { SessionEventLike } from './dsh-public.js'
import { HubClientError } from './hub-client.js'
import { providerCanonicalJson } from './provider-input.js'
import { projectBaselineDelivery, restoreFactAttributes } from './baseline-projection.js'
import { baselinePost, baselineSafeCause, BaselineTransportError, type BaselineTransportDiagnostic } from './baseline-transport.js'

const identity = { run_id: z.string().min(1), research_session_id: z.string().min(1),
  generation: z.literal(1), request_hash: z.string().regex(/^[a-f0-9]{64}$/) }
export const researchBaselineDeliverySchema = z.object({
  schema_version: z.literal('research-baseline-delivery.v1'), status: z.enum(['ready', 'hard_gap']), ...identity,
  method_hash: z.string().regex(/^[a-f0-9]{64}$/), plan_ref: researchProviderCallbackReferenceSchema,
  batch_ref: researchProviderCallbackReferenceSchema, results: z.array(researchCapabilityResultSchema).max(5),
  errors: z.array(z.object({ request_id: z.string().min(1), capability_id: z.string().min(1),
    requirement_id: z.string().min(1), error_code: z.string().min(1) }).strict()).max(5),
  coverage: coverageAssessmentSchema, remaining_tool_calls: z.number().int().nonnegative(),
  acquisition_close_at: z.string().datetime({ offset: true }), recorded_at: z.string().datetime({ offset: true }),
  origin: z.literal('server_baseline'), skill_execution_verified: z.literal(false), model_understanding_verified: z.literal(false),
}).strict()
const skippedSchema = z.object({ schema_version: z.literal('research-baseline-skipped.v1'), status: z.literal('skipped'),
  ...identity, generation: z.number().int().positive(), reason_code: z.literal('method_not_opted_in') }).strict()
export type ResearchBaselineDelivery = z.infer<typeof researchBaselineDeliverySchema>
// Authenticity lasts only for this direct server read. No cross-request data or
// authorization cache: every Host reader calls the server's immutable reader.
const authenticated = new WeakSet<ResearchBaselineDelivery>()
export const BASELINE_TRANSPORT_TIMEOUT_MS = 120_000
export const BASELINE_PLUGIN = 'decision-hub-protected-context'
const invalid = () => new HubClientError('research_baseline_context_invalid', false)
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const nested of Object.values(value)) freeze(nested)
    Object.freeze(value)
  }
  return value
}
export function baselineRequestEligible(request: ResearchSessionRequest): boolean {
  return request.execution_mode === 'live' && request.research_mode === 'current_decision'
    && request.output_schema_ref === DECISION_SYNTHESIS_REF
}

/** Freeze all supplied preparation ceilings at entry. No per-model-step cap. */
export function researchPreparationDeadlineAtMs(options: {
  correlationDeadlineAt?: string | null; linkDeadlineAt?: string | null;
  outerRemainingMs?: number; legacyMs: number;
}): number {
  const enteredAt = Date.now(), caps: number[] = []
  for (const value of [options.correlationDeadlineAt, options.linkDeadlineAt]) {
    if (value == null) continue
    const deadline = Date.parse(value)
    if (!Number.isFinite(deadline) || deadline <= enteredAt) throw new HubClientError('dsh_host_read_deadline_elapsed', false)
    caps.push(deadline)
  }
  if (caps.length === 0) {
    if (!Number.isFinite(options.legacyMs) || options.legacyMs <= 0) throw new HubClientError('dsh_host_read_deadline_elapsed', false)
    caps.push(enteredAt + options.legacyMs)
  }
  if (options.outerRemainingMs !== undefined) {
    if (!Number.isFinite(options.outerRemainingMs) || options.outerRemainingMs <= 0) {
      throw new HubClientError('dsh_host_read_deadline_elapsed', false)
    }
    caps.push(enteredAt + options.outerRemainingMs)
  }
  return Math.min(...caps)
}

/** Extract only the authenticated full Hub prompt; never model-authored prose. */
export function readCanonicalResearchPrompt(promptText: string, maxBytes: number): ResearchSessionRequest {
  if (Buffer.byteLength(promptText, 'utf8') > maxBytes) throw new Error('decision_hub_review_context_control_oversized')
  const begin = 'Canonical control request (untrusted text is replaced by typed references):\n'
  const finish = '\nBEGIN_UNTRUSTED_RESEARCH_DATA\n'
  const start = promptText.indexOf(begin), end = promptText.indexOf(finish, start + begin.length)
  if (start < 0 || end < 0) throw new Error('decision_hub_review_context_control_missing')
  const request = researchSessionRequestSchema.parse(JSON.parse(promptText.slice(start + begin.length, end)))
  const untrustedEnd = promptText.indexOf('\nEND_UNTRUSTED_RESEARCH_DATA', end + finish.length)
  if (untrustedEnd < 0) throw new Error('decision_hub_review_context_input_data_missing')
  const untrusted: unknown = JSON.parse(promptText.slice(end + finish.length, untrustedEnd))
  if (!Array.isArray(untrusted)) throw new Error('decision_hub_review_context_input_data_invalid')
  for (const input of request.input_evidence) {
    const marker = `untrusted:evidence:${input.evidence_id}`
    if (input.excerpt !== marker) continue
    const matches = untrusted.filter((item: unknown): item is { kind: string; ref: string; text: string } =>
      typeof item === 'object' && item !== null && Object.keys(item).length === 3
      && 'kind' in item && item.kind === 'evidence_excerpt' && 'ref' in item && item.ref === marker
      && 'text' in item && typeof item.text === 'string')
    if (matches.length !== 1) throw new Error('decision_hub_review_context_input_excerpt_missing')
    input.excerpt = matches[0]!.text
  }
  return request
}

export async function readResearchBaseline(options: {
  serviceUrl: string; authKey: string; fetchImpl?: typeof fetch; binding: CancellationBinding;
  request: ResearchSessionRequest; signal: AbortSignal; deadlineAt: string;
  onTransportFailure?: (diagnostic: BaselineTransportDiagnostic) => void;
}): Promise<ResearchBaselineDelivery | undefined> {
  if (!baselineRequestEligible(options.request)) return undefined
  const url = new URL(options.serviceUrl)
  if (!['http:', 'https:'].includes(url.protocol) || !url.pathname.endsWith('/execute')
    || url.username || url.password || !options.authKey) throw new HubClientError('research_baseline_configuration_invalid', false)
  url.pathname = url.pathname.replace(/\/execute$/, '/baseline'); url.search = ''; url.hash = ''
  // V2 server acquisition/authentication is not an LLM model step. It shares
  // the original Task deadline and parent cancellation, never a renewed 600s.
  // New collection remains fenced by the server's original acquisition_close.
  const startedAt = Date.now()
  const originalDeadlineAtMs = Math.min(Date.parse(options.deadlineAt), Date.parse(options.request.deadline_at))
  const budget = 'schema_version' in options.request.execution_budget
    && options.request.execution_budget.schema_version === 'research-execution-budget.v2'
    ? originalDeadlineAtMs - startedAt
    : Math.min(BASELINE_TRANSPORT_TIMEOUT_MS, originalDeadlineAtMs - startedAt)
  if (!Number.isFinite(budget) || budget <= 0) throw new HubClientError('dsh_provider_input_deadline_elapsed', false)
  const controller = new AbortController(), signal = AbortSignal.any([options.signal, controller.signal])
  const timer = options.fetchImpl ? setTimeout(() => controller.abort(), budget) : undefined
  const command = { schema_version: 'research-baseline-request.v1', run_id: options.binding.runId,
    research_session_id: options.binding.sessionId, generation: options.binding.generation, request_hash: options.binding.requestHash }
  const bounded = async <T>(operation: () => Promise<T>): Promise<T> => {
    let stop!: () => void
    try {
      signal.throwIfAborted()
      const abort = new Promise<never>((_resolve, reject) => {
        stop = () => reject(signal.reason)
        signal.addEventListener('abort', stop, { once: true })
      })
      const value = await Promise.race([operation(), abort]); signal.throwIfAborted(); return value
    } catch (error) {
      throw new HubClientError(options.signal.aborted ? 'research_baseline_cancelled'
        : controller.signal.aborted ? 'research_baseline_transport_timeout' : 'research_baseline_transport_unavailable',
      false, baselineSafeCause(error))
    } finally { if (stop) signal.removeEventListener('abort', stop) }
  }
  try {
    let body: string
    if (options.fetchImpl) {
      // Retain the explicit injected fetch seam for existing host harnesses.
      // The production default below owns real IO through close, not a race.
      const response = await bounded(() => options.fetchImpl!(url, { method: 'POST',
        headers: { 'content-type': 'application/json', 'x-decision-hub-bridge-key': options.authKey },
        body: JSON.stringify(command), signal, redirect: 'error' }))
      if (!response.ok) throw new HubClientError('research_baseline_http_refused', false)
      body = await bounded(() => response.text())
    } else {
      try {
        body = await baselinePost({ url, authKey: options.authKey, body: JSON.stringify(command),
          signal: options.signal, deadlineAtMs: Math.min(originalDeadlineAtMs, startedAt + budget) })
      } catch (error) {
        if (error instanceof BaselineTransportError) {
          const cause = baselineSafeCause(error)
          try {
            options.onTransportFailure?.(Object.freeze({ phase: error.phase, code: error.code,
              ...(cause ? { cause_code: cause.code } : {}) }))
          } catch { /* Logging must not replace the actual transport failure. */ }
          throw new HubClientError(error.code, false, error.cause)
        }
        throw new HubClientError('research_baseline_transport_unavailable', false, baselineSafeCause(error))
      }
    }
    if (Buffer.byteLength(body, 'utf8') > 1_000_000) throw new HubClientError('research_baseline_oversize', false)
    let result: ResearchBaselineDelivery | z.infer<typeof skippedSchema>
    try { result = z.union([researchBaselineDeliverySchema, skippedSchema]).parse(JSON.parse(body)) } catch { throw invalid() }
    if (result.run_id !== command.run_id || result.research_session_id !== command.research_session_id
      || result.request_hash !== command.request_hash) throw invalid()
    if (result.status === 'skipped') {
      if (result.generation !== command.generation) throw invalid()
      return undefined
    }
    if (Date.parse(result.recorded_at) > Date.now() || result.remaining_tool_calls > options.request.execution_budget.max_tool_calls
      || Date.parse(result.acquisition_close_at) > Date.parse(options.request.deadline_at)) throw invalid()
    authenticated.add(result)
    return freeze(result)
  } finally { clearTimeout(timer) }
}

/** Server-origin evidence is distinct from native model tool execution. */
export function baselineResults(context: { binding: CancellationBinding; request: ResearchSessionRequest;
  events: readonly SessionEventLike[]; serverBaseline?: ResearchBaselineDelivery }, preparingFirst = false) {
  const delivery = context.serverBaseline
  if (!delivery) return []
  if (!authenticated.has(delivery) || !baselineRequestEligible(context.request)
    || delivery.run_id !== context.binding.runId || delivery.research_session_id !== context.binding.sessionId
    || delivery.request_hash !== context.binding.requestHash || delivery.generation !== 1) throw invalid()
  if (preparingFirst) {
    if (context.binding.generation !== 1 || context.events.some(event => event.type === 'step/start'
      || (event.type === 'user/message' && typeof event.data.source === 'object' && event.data.source !== null
        && (event.data.source as Record<string, unknown>).kind === 'user'
        && (event.data.source as Record<string, unknown>).rpcId === context.binding.requestId))) throw invalid()
  } else {
    // A DTO quoted by assistant/tool text cannot supply the original plugin
    // message. Prefix readers can use only a delivery already seen in that prefix.
    const matches = context.events.filter(event => {
      if (event.type !== 'user/message') return false
      const source = event.data.source as Record<string, unknown> | null
      const content = event.data.content as { type: string; text?: string }[] | undefined
      if (!source || source.kind !== 'plugin' || source.plugin !== BASELINE_PLUGIN
        || !Array.isArray(content) || content.length !== 2 || content[1]?.type !== 'text') return false
      try {
        const value = JSON.parse(content[1].text!)
        return (value.schema_version === 'decision-hub-protected-context.v2'
          || value.schema_version === 'decision-hub-protected-context.v3'
          || value.schema_version === 'decision-hub-protected-context.v4') && value.snapshot_id === event.data.id
          && value.run_id === delivery.run_id && value.root_session_id === delivery.research_session_id
          && value.request_hash === delivery.request_hash && value.generation <= context.binding.generation
          && value.generation >= 1 && Number.isSafeInteger(value.generation)
          && event.time >= Date.parse(delivery.recorded_at)
          && providerCanonicalJson(value.server_baseline) === providerCanonicalJson(
            value.schema_version === 'decision-hub-protected-context.v2' ? delivery
              : projectBaselineDelivery(delivery, value.schema_version === 'decision-hub-protected-context.v4'
                ? restoreFactAttributes(value.facts, value.shared_fact_attributes) : value.facts, value.evidence_candidates))
      } catch { return false }
    })
    if (!matches.length) throw invalid()
  }
  const allowed = new Set(context.request.allowed_capabilities)
  if (delivery.results.some(result => !allowed.has(result.capability_id)
    || result.evidence_candidates.some(item => item.research_session_id !== context.binding.sessionId))) throw invalid()
  return delivery.results
}
