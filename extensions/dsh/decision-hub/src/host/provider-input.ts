import { perpetualPrepareArgumentsV1Schema, perpetualFinalizeArgumentsV1Schema } from '@decision-hub/contracts-ts'
import { createHash, randomUUID } from 'node:crypto'
import {
  researchProviderInputSubmitSchema,
  researchProviderToolEmissionSubmitSchema,
  researchProviderFinishSubmitSchema,
  type DshModelDispatchBinding,
  type ResearchProviderInputRecorded,
  type ResearchProviderToolEmissionRecorded,
  type ResearchProviderToolEmissionSubmit,
} from '@decision-hub/contracts-ts'
import type { ModelDispatchOptions } from './dsh-public.js'
import type { HostScope } from './harness-execution.js'
import { HubClientError, type HubClient } from './hub-client.js'
import { modelBindingErrorCode } from './model-errors.js'
import { normalizeSynthesisCandidate, synthesisEvidenceRefErrors, trustedSynthesisEvidence, type SynthesisReviewContext } from '../final-review-gate.js'
import type { CancellationBinding } from './command-cancellation.js'
import { parseSynthesisCandidate, synthesisProtocol } from '../synthesis-protocol.js'
import { actionSynthesisPreflightSchema, type ActionCatalogContext } from './action-preflight.js'

export type ProviderInputHub = Pick<HubClient, 'recordProviderInput' | 'recordProviderToolEmission' | 'recordProviderFinish'>
export type ProviderInputReader = Pick<HubClient, 'providerToolEmission' | 'linkBySession'> & Partial<Pick<HubClient, 'actionSynthesisPreflight'>>
const readers = new WeakMap<object, ProviderInputReader>()
const previous = new WeakMap<ProviderInputHub, Map<string, { output_id: string; payload_hash: string }>>()
export const MAX_PROVIDER_INPUT_BYTES = 8 * 1024 * 1024
const MAX_TOOL_ARGUMENT_BYTES = 1_000_000
const MAX_FINISH_BYTES = 64_000
const MAX_USAGE_CHUNKS = 16
const MAX_EMISSIONS = 128
const RECEIPT_IO_CAP_MS = 60_000
const FINISH_RECEIPT_IO_CAP_MS = 60_000
const PROVIDER_STAGE_PUBLISHER = Symbol.for('decision-hub.provider-stage-diagnostic.publisher.v1')
const PROVIDER_DISPATCH_RUNNER = Symbol.for('decision-hub.provider-dispatch-context.runner.v1')

function receiptBudget(deadlineMs: number, phase: 'input' | 'emission' | 'finish', remainingMs?: number): number {
  const cap = phase === 'finish' ? FINISH_RECEIPT_IO_CAP_MS : RECEIPT_IO_CAP_MS
  const budget = Math.floor(Math.min(cap, remainingMs ?? cap, deadlineMs - Date.now()))
  if (!Number.isSafeInteger(budget) || budget < 1) {
    throw new HubClientError(`dsh_provider_${phase}_deadline_elapsed`, false)
  }
  return budget
}

function originalReceiptBudget(deadlineMs: number, phase: 'input' | 'emission' | 'finish'): number {
  const budget = Math.floor(deadlineMs - Date.now())
  if (!Number.isSafeInteger(budget) || budget < 1 || budget > 2_147_483_647) {
    throw new HubClientError(`dsh_provider_${phase}_deadline_elapsed`, false)
  }
  return budget
}

function runDeadline(value: string | null | undefined): number {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) {
    throw new HubClientError('dsh_provider_run_deadline_unavailable', false)
  }
  return Date.parse(value)
}
const sha = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex')

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Matches the Hub's lossless canonical JSON; undefined/nonfinite are refused. */
export function providerCanonicalJson(value: unknown): string {
  return canonical(value, new Set(), 0)
}
function canonical(value: unknown, visited: Set<object>, depth: number): string {
  if (depth > 256) throw new HubClientError('dsh_provider_json_invalid', false)
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) throw new HubClientError('dsh_provider_json_invalid', false)
    return JSON.stringify(value)
  }
  if (Array.isArray(value) || (record(value) && Object.getPrototypeOf(value) === Object.prototype)) {
    if (visited.has(value)) throw new HubClientError('dsh_provider_json_invalid', false)
    visited.add(value)
    try {
      if (Array.isArray(value)) return `[${value.map(item => canonical(item, visited, depth + 1)).join(',')}]`
      return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key], visited, depth + 1)}`).join(',')}}`
    } finally { visited.delete(value) }
  }
  throw new HubClientError('dsh_provider_json_invalid', false)
}

export function registerProviderInputReader(root: object, reader: ProviderInputReader): () => void {
  if (readers.has(root)) throw new HubClientError('dsh_provider_reader_conflict', false)
  readers.set(root, reader)
  return () => { if (readers.get(root) === reader) readers.delete(root) }
}

/** Read server-owned protocol selection; no failed lookup may guess live/Harness. */
export async function readProviderReceiptMode(
  root: object, binding: CancellationBinding, signal: AbortSignal,
): Promise<'independent-review-receipt.v1' | 'independent-review-receipt.v2'> {
  const reader = readers.get(root)
  if (!reader) throw new HubClientError('dsh_provider_reader_unavailable', false)
  signal.throwIfAborted()
  const link = await reader.linkBySession(binding.sessionId, signal)
  signal.throwIfAborted()
  if (link.run_id !== binding.runId || link.generation !== binding.generation
    || link.dsh_session_id !== binding.sessionId || link.request_hash !== binding.requestHash) {
    throw new HubClientError('dsh_provider_emission_identity_mismatch', false)
  }
  return link.harness_required === true ? 'independent-review-receipt.v1' : 'independent-review-receipt.v2'
}

export async function verifySynthesisProviderInput(
  root: object, context: SynthesisReviewContext, executionSessionId: string,
  callId: string, actualArguments: unknown, signal: AbortSignal,
): Promise<ResearchProviderToolEmissionRecorded | null> {
  const reader = readers.get(root)
  if (!reader) throw new HubClientError('dsh_provider_reader_unavailable', false)
  const binding = context.binding
  if (executionSessionId !== binding.sessionId) throw new HubClientError('dsh_provider_synthesis_root_required', false)
  // Harness uses its existing authenticated consumption protocol. A missing
  // ordinary emission is never sufficient to infer Harness authorization.
  const protocol = synthesisProtocol(context.request.output_schema_ref)
  if (await readProviderReceiptMode(root, binding, signal) === 'independent-review-receipt.v1' && protocol !== 'decision_v2') return null
  const phase=record(actualArguments) && protocol==='decision_v2' && ['prepare','finalize'].includes(String(actualArguments.action))
  if(phase) (actualArguments.action==='prepare'?perpetualPrepareArgumentsV1Schema:perpetualFinalizeArgumentsV1Schema).parse(actualArguments)
  const proof = await reader.providerToolEmission(binding.runId, binding.generation, executionSessionId, callId, signal, context.request.deadline_at)
  signal.throwIfAborted()
  if (proof.run_id !== binding.runId || proof.generation !== binding.generation
    || proof.root_session_id !== binding.sessionId || proof.execution_session_id !== executionSessionId
    || proof.call_id !== callId || proof.tool_name !== 'decision_hub_synthesis_submit'
    || proof.arguments_hash !== sha(providerCanonicalJson(actualArguments))
    // Candidate identity uses the server's real typed DTO codec, including
    // datetime/float representations. The Kernel binds it to its own witness.
    || (phase ? proof.candidate_hash !== null : proof.candidate_hash === null)) {
    throw new HubClientError('dsh_provider_emission_identity_mismatch', false)
  }
  if (synthesisProtocol(context.request.output_schema_ref) === 'decision') {
    await readSynthesisActionCatalog(root, context, callId, signal, proof, actualArguments)
  }
  return proof
}

/** Fetch the exact frozen scope catalog for an actual synthesis call. */
export async function readSynthesisActionCatalog(
  root: object, context: SynthesisReviewContext, callId: string, signal: AbortSignal,
  original?: ResearchProviderToolEmissionRecorded, actualArguments?: unknown,
): Promise<ActionCatalogContext> {
  const reader = readers.get(root)
  if (!reader?.actionSynthesisPreflight) throw new HubClientError('dsh_action_preflight_unavailable', false)
  const binding = context.binding
  signal.throwIfAborted()
  const proof = original ?? await reader.providerToolEmission(binding.runId, binding.generation, binding.sessionId, callId, signal, context.request.deadline_at)
  if (proof.run_id !== binding.runId || proof.generation !== binding.generation
    || proof.root_session_id !== binding.sessionId || proof.execution_session_id !== binding.sessionId
    || proof.call_id !== callId || proof.tool_name !== 'decision_hub_synthesis_submit' || proof.candidate_hash === null) {
    throw new HubClientError('dsh_provider_emission_identity_mismatch', false)
  }
  signal.throwIfAborted()
  const result = actionSynthesisPreflightSchema.parse(await reader.actionSynthesisPreflight(binding.runId, binding.generation, binding.sessionId, callId, signal, context.request.deadline_at))
  signal.throwIfAborted()
  if (providerCanonicalJson(result.emission) !== providerCanonicalJson(proof)
    || sha(providerCanonicalJson(result.catalog)) !== result.catalog_hash) {
    throw new HubClientError('dsh_action_preflight_identity_mismatch', false)
  }
  if (actualArguments !== undefined && proof.arguments_hash !== sha(providerCanonicalJson(actualArguments))) {
    throw new HubClientError('dsh_provider_emission_identity_mismatch', false)
  }
  if (result.issues.length || result.issues_truncated) {
    // Values are diagnostic JSON data. Never silently repair the candidate or
    // spend the independent review on a known unpublishable condition.
    // Surface all already known reference defects with catalog defects in the
    // same failed submission. Only authenticated actual arguments reach here;
    // the independent final-review reference gate remains authoritative.
    const candidate = record(actualArguments) && 'candidate' in actualArguments
      ? parseSynthesisCandidate(context.request.output_schema_ref, actualArguments.candidate, normalizeSynthesisCandidate) : null
    const allowedIds = candidate === null ? [] : trustedSynthesisEvidence(context).ids
    const referenceErrors = candidate === null ? [] : synthesisEvidenceRefErrors(candidate, allowedIds)
    const diagnostic = {
      instruction: 'Correct the listed fields using the exact catalog; keep six required branches, actual times and evidence. Price thresholds must be real positive levels, never zero placeholders. This check grants no review or publication authority.',
      schema_version: result.schema_version, emission_ref: result.emission.reference, catalog_hash: result.catalog_hash,
      issues: result.issues.slice(), issues_total: result.issues.length, issues_truncated: result.issues_truncated,
      evidence_ref_errors: referenceErrors.slice(0, 96),
      evidence_ref_errors_total: referenceErrors.length,
      evidence_ref_errors_truncated: referenceErrors.length > 96,
      allowed_evidence_ids: allowedIds.slice(), allowed_evidence_ids_total: allowedIds.length,
      allowed_evidence_ids_truncated: false,
      evidence_ref_instruction: 'Use exact admitted Evidence IDs only. Missing requirement/metric names belong in mechanism.unresolved, never evidence reference fields. Do not silently substitute another source or delete a causal concern. Correct every listed defect; this response grants no acceptance.',
    }
    // Keep the actual error under the official default 8192-character prune
    // threshold. The full frozen catalog already exists in the Task/Hub API;
    // do not duplicate it ahead of the defects and lose them to middle-pruning.
    let text = JSON.stringify(diagnostic)
    while (text.length > 7600) {
      if (diagnostic.allowed_evidence_ids.length) {
        diagnostic.allowed_evidence_ids.pop(); diagnostic.allowed_evidence_ids_truncated = true
      } else if (diagnostic.evidence_ref_errors.length) {
        diagnostic.evidence_ref_errors.pop(); diagnostic.evidence_ref_errors_truncated = true
      } else if (diagnostic.issues.length) {
        diagnostic.issues.pop(); diagnostic.issues_truncated = true
      } else throw new Error('decision_hub_action_catalog_diagnostics_oversized')
      text = JSON.stringify(diagnostic)
    }
    throw new Error('decision_hub_action_catalog_invalid\n' + text)
  }
  return { catalog: result.catalog, catalog_hash: result.catalog_hash }
}

type InputIdentity = Pick<DshModelDispatchBinding, 'run_id' | 'generation' | 'request_hash' | 'plugin_build_hash'> & {
  root_session_id: string; execution_session_id: string; dispatch_id: string
}

export type ProviderStreamStage = 'receipt_started' | 'receipt_completed' | 'delegate_started' | 'first_sdk_chunk'
  | 'stream_finished' | 'stream_failed'

type ProviderStageByteCounts = Partial<Record<
  'messages_bytes' | 'system_bytes' | 'tools_bytes' | 'input_bytes' | 'finish_bytes', number
>>

/**
 * The transport preload owns the only durable diagnostic writer. This call is
 * deliberately lossy: missing/invalid publishers and storage failures cannot
 * change model execution. Only hashes, opaque IDs and numeric byte counts cross
 * the global boundary; Provider input/chunks never do.
 */
function publishProviderStage(
  identity: InputIdentity,
  inputHash: string,
  stage: ProviderStreamStage,
  byteCounts: ProviderStageByteCounts = {},
): void {
  try {
    const publisher = (globalThis as unknown as Record<symbol, unknown>)[PROVIDER_STAGE_PUBLISHER]
    if (typeof publisher !== 'function') return
    publisher({
      schema_version: 'dsh-provider-stage-diagnostic.v1', at: new Date().toISOString(), stage,
      run_hash: sha(identity.run_id), dispatch_id: identity.dispatch_id,
      input_hash: inputHash, generation: identity.generation,
      execution_session_hash: sha(identity.execution_session_id), ...byteCounts,
    })
  } catch { /* A lossy diagnostic cannot alter Provider execution. */ }
}

function providerDispatchContext(prepared: { identity: InputIdentity; receipt: ResearchProviderInputRecorded }) {
  return {
    run_hash: sha(prepared.identity.run_id), dispatch_id: prepared.identity.dispatch_id,
    input_hash: prepared.receipt.input_hash, generation: prepared.identity.generation,
    execution_session_hash: sha(prepared.identity.execution_session_id),
  }
}

function withProviderDispatchContext<T>(
  prepared: { identity: InputIdentity; receipt: ResearchProviderInputRecorded }, operation: () => T,
): T {
  const runner = (globalThis as unknown as Record<symbol, unknown>)[PROVIDER_DISPATCH_RUNNER]
  if (typeof runner !== 'function') return operation()
  return runner(providerDispatchContext(prepared), operation) as T
}

/** Every SDK iterator entry point retains its own immutable dispatch context. */
function contextualProviderIterable(
  prepared: { identity: InputIdentity; receipt: ResearchProviderInputRecorded },
  create: () => AsyncIterable<unknown>,
): AsyncIterable<unknown> {
  return {
    [Symbol.asyncIterator]() {
      const iterable = withProviderDispatchContext(prepared, create)
      const iterator = withProviderDispatchContext(prepared, () => iterable[Symbol.asyncIterator]())
      const wrapped = {
        next: (...args: unknown[]) => withProviderDispatchContext(prepared,
          () => Reflect.apply(iterator.next, iterator, args)),
        return: iterator.return === undefined ? undefined : (...args: unknown[]) => withProviderDispatchContext(prepared,
          () => Reflect.apply(iterator.return!, iterator, args)),
        throw: iterator.throw === undefined ? undefined : (...args: unknown[]) => withProviderDispatchContext(prepared,
          () => Reflect.apply(iterator.throw!, iterator, args)),
        [Symbol.asyncIterator]() { return this },
      }
      return wrapped as AsyncIterator<unknown> & AsyncIterable<unknown>
    },
  }
}

/** Records actual GenerateOptions bytes. This is not a Provider wire/comprehension claim. */
export async function prepareProviderInput(
  hub: ProviderInputHub, binding: DshModelDispatchBinding, options: ModelDispatchOptions,
  scope: HostScope, signal: AbortSignal | undefined, timeoutMs: number,
  runDeadlineAt: string | null | undefined,
  originalStepDeadlineAt?: string,
) {
  if (!options.sessionId || !Array.isArray(options.messages)) throw new HubClientError('dsh_provider_input_unavailable', false)
  const body = { messages: options.messages, system: options.system ?? null, tools: options.tools ?? [] }
  // Ensure the real values are lossless JSON before JSON.stringify can drop one.
  providerCanonicalJson(body)
  const inputJson = JSON.stringify(body)
  if (Buffer.byteLength(inputJson, 'utf8') > MAX_PROVIDER_INPUT_BYTES) throw new HubClientError('dsh_provider_input_oversize', false)
  const identity: InputIdentity = {
    run_id: binding.run_id, generation: binding.generation, request_hash: binding.request_hash,
    plugin_build_hash: binding.plugin_build_hash, root_session_id: binding.dsh_session_id,
    execution_session_id: options.sessionId, dispatch_id: randomUUID(),
  }
  let prior = previous.get(hub)
  if (!prior) { prior = new Map(); previous.set(hub, prior) }
  const key = `${binding.run_id}:${binding.generation}:${options.sessionId}`
  const submission = researchProviderInputSubmitSchema.parse({
    schema_version: 'research-provider-input-submit.v1', ...identity,
    parent_session_id: binding.parent_session_id ?? null,
    provider: binding.provider, model: binding.model, reasoning_effort: binding.reasoning_effort ?? null,
    temperature: binding.temperature ?? null, max_tokens: binding.max_tokens ?? null,
    stop_sequences_hash: binding.stop_sequences_hash ?? null,
    input_json: inputJson, input_hash: sha(inputJson), previous_input_ref: prior.get(key) ?? null, host_scope: scope,
  })
  const runDeadlineMs = runDeadline(runDeadlineAt)
  const receiptDeadlineMs = originalStepDeadlineAt === undefined ? undefined
    : Math.min(runDeadlineMs, runDeadline(originalStepDeadlineAt))
  const receiptDeadlineAt = receiptDeadlineMs === undefined ? undefined : new Date(receiptDeadlineMs).toISOString()
  publishProviderStage(identity, submission.input_hash, 'receipt_started', {
    messages_bytes: Buffer.byteLength(JSON.stringify(body.messages), 'utf8'),
    system_bytes: Buffer.byteLength(JSON.stringify(body.system), 'utf8'),
    tools_bytes: Buffer.byteLength(JSON.stringify(body.tools), 'utf8'),
    input_bytes: Buffer.byteLength(inputJson, 'utf8'),
  })
  const receipt = await hub.recordProviderInput(submission, signal, receiptDeadlineMs === undefined
    ? receiptBudget(runDeadlineMs, 'input', timeoutMs) : originalReceiptBudget(receiptDeadlineMs, 'input'), receiptDeadlineAt)
  if (receipt.dispatch_id !== identity.dispatch_id || receipt.input_hash !== submission.input_hash) {
    throw new HubClientError('dsh_provider_input_receipt_mismatch', false)
  }
  prior.set(key, { output_id: receipt.reference.output_id, payload_hash: receipt.reference.payload_hash })
  if (prior.size > 256) prior.delete(prior.keys().next().value!)
  const prepared = { identity, receipt, inputJson, runDeadlineMs, receiptDeadlineMs, receiptDeadlineAt }
  publishProviderStage(identity, receipt.input_hash, 'receipt_completed', { input_bytes: Buffer.byteLength(inputJson, 'utf8') })
  return prepared
}

export function providerToolEmission(
  identity: InputIdentity, receipt: ResearchProviderInputRecorded, chunk: unknown,
): ResearchProviderToolEmissionSubmit | null {
  if (!record(chunk) || chunk.type !== 'block-end') return null
  const block = chunk.block
  if (!record(block) || block.type !== 'tool-call') return null
  if (typeof block.id !== 'string' || !block.id || typeof block.name !== 'string' || !block.name
    || typeof block.arguments !== 'string' || !Number.isSafeInteger(chunk.index) || Number(chunk.index) < 0) {
    throw new HubClientError('dsh_provider_tool_block_invalid', false)
  }
  if (Buffer.byteLength(block.arguments, 'utf8') > MAX_TOOL_ARGUMENT_BYTES) throw new HubClientError('dsh_provider_tool_block_invalid', false)
  let parsed: unknown
  try { parsed = JSON.parse(block.arguments) } catch { throw new HubClientError('dsh_provider_json_invalid', false) }
  return researchProviderToolEmissionSubmitSchema.parse({
    schema_version: 'research-provider-tool-emission-submit.v1', ...identity,
    input_ref: { output_id: receipt.reference.output_id, payload_hash: receipt.reference.payload_hash },
    call_id: block.id, tool_name: block.name, raw_arguments_json: block.arguments,
    raw_arguments_hash: sha(block.arguments), arguments_hash: sha(providerCanonicalJson(parsed)), block_index: chunk.index,
  })
}

/** No output prose/reasoning or private replay state is archived by this wrapper. */
export async function* recordedProviderStream(
  options: ModelDispatchOptions, next: () => AsyncIterable<unknown>, hub: ProviderInputHub,
  prepared: Awaited<ReturnType<typeof prepareProviderInput>>,
  beforeProviderDelegate?: () => void,
  onProviderStage?: (stage: ProviderStreamStage) => void,
): AsyncIterable<unknown> {
  const usage: unknown[] = []
  let finish: Record<string, unknown> | null = null
  let status: 'completed' | 'error' | 'cancelled' | 'interrupted' = 'interrupted'
  let errorCode: string | null = 'dsh_provider_finish_missing'
  let emissions = 0
  let failure: unknown
  let finishRecorded = false
  let finishAttempted = false
  let firstChunkObserved = false
  let terminalStageObserved = false
  let finishBytes: number | undefined
  const observe = (stage: ProviderStreamStage, byteCounts: ProviderStageByteCounts = {}) => {
    publishProviderStage(prepared.identity, prepared.receipt.input_hash, stage, byteCounts)
    try { onProviderStage?.(stage) } catch { /* Diagnostics never change Provider execution. */ }
  }
  async function saveFinish() {
    const raw = JSON.stringify({ finish, usage_chunks: usage })
    finishBytes = Buffer.byteLength(raw, 'utf8')
    if (finishBytes > MAX_FINISH_BYTES) throw new HubClientError('dsh_provider_finish_oversize', false)
    const submission = researchProviderFinishSubmitSchema.parse({
      schema_version: 'research-provider-finish-submit.v1', ...prepared.identity,
      input_ref: { output_id: prepared.receipt.reference.output_id, payload_hash: prepared.receipt.reference.payload_hash },
      status, finish_json: raw, finish_hash: sha(raw), error_code: errorCode,
    })
    // Independent bounded callback also works after the caller is cancelled.
    finishAttempted = true
    const saved = await hub.recordProviderFinish(submission, undefined, prepared.receiptDeadlineMs === undefined
      ? receiptBudget(prepared.runDeadlineMs, 'finish') : originalReceiptBudget(prepared.receiptDeadlineMs, 'finish'), prepared.receiptDeadlineAt)
    if (saved.dispatch_id !== submission.dispatch_id || saved.finish_hash !== submission.finish_hash
      || saved.status !== submission.status || saved.input_ref.output_id !== submission.input_ref.output_id
      || saved.input_ref.payload_hash !== submission.input_ref.payload_hash) {
      throw new HubClientError('dsh_provider_finish_receipt_mismatch', false)
    }
    finishRecorded = true
  }
  try {
    if (prepared.receiptDeadlineMs !== undefined) originalReceiptBudget(prepared.receiptDeadlineMs, 'input')
    // The input receipt is durable before this hook runs, and no Provider byte
    // can be requested before it returns. The Bridge uses this single boundary
    // to start the actual Provider watchdog without charging preparation time.
    beforeProviderDelegate?.()
    observe('delegate_started')
    for await (const chunk of contextualProviderIterable(prepared, next)) {
      if (!firstChunkObserved) {
        firstChunkObserved = true
        observe('first_sdk_chunk')
      }
      options.signal?.throwIfAborted()
      if (prepared.receiptDeadlineMs !== undefined) originalReceiptBudget(prepared.receiptDeadlineMs, 'emission')
      if (finish !== null) throw new HubClientError('dsh_provider_stream_after_finish', false)
      const emission = providerToolEmission(prepared.identity, prepared.receipt, chunk)
      if (emission) {
        if (++emissions > MAX_EMISSIONS) throw new HubClientError('dsh_provider_emission_limit', false)
        const saved = await hub.recordProviderToolEmission(emission, options.signal, prepared.receiptDeadlineMs === undefined
          ? receiptBudget(prepared.runDeadlineMs, 'emission') : originalReceiptBudget(prepared.receiptDeadlineMs, 'emission'), prepared.receiptDeadlineAt)
        if (saved.run_id !== emission.run_id || saved.generation !== emission.generation
          || saved.root_session_id !== emission.root_session_id || saved.execution_session_id !== emission.execution_session_id
          || saved.tool_name !== emission.tool_name || saved.dispatch_id !== emission.dispatch_id || saved.call_id !== emission.call_id
          || saved.arguments_hash !== emission.arguments_hash || saved.raw_arguments_hash !== emission.raw_arguments_hash
          || saved.input_ref.output_id !== emission.input_ref.output_id || saved.input_ref.payload_hash !== emission.input_ref.payload_hash) {
          throw new HubClientError('dsh_provider_emission_receipt_mismatch', false)
        }
      }
      if (record(chunk) && chunk.type === 'usage') {
        if (usage.length >= MAX_USAGE_CHUNKS) throw new HubClientError('dsh_provider_usage_limit', false)
        usage.push({ type: 'usage', usage: chunk.usage })
      }
      if (record(chunk) && chunk.type === 'finish') {
        finish = { type: 'finish', reason: chunk.reason }
        const kind = record(chunk.reason) ? chunk.reason.kind : null
        if (!['stop', 'tool-calls', 'max-tokens', 'aborted', 'error'].includes(String(kind))) {
          throw new HubClientError('dsh_provider_finish_invalid', false)
        }
        status = kind === 'aborted' ? 'cancelled' : kind === 'error' ? 'error' : 'completed'
        errorCode = status === 'completed' ? null : status === 'cancelled' ? 'dsh_provider_cancelled' : 'dsh_provider_failed'
        await saveFinish()
      }
      yield chunk
    }
    if (!finish) throw new HubClientError('dsh_provider_finish_missing', false)
  } catch (error) {
    status = options.signal?.aborted ? 'cancelled'
      : error instanceof HubClientError && error.code === 'dsh_provider_finish_missing' ? 'interrupted' : 'error'
    failure = status === 'cancelled' ? new HubClientError('dsh_provider_cancelled', false) : error
    errorCode = error instanceof HubClientError && modelBindingErrorCode(error.code) ? error.code
      : status === 'cancelled' ? 'dsh_provider_cancelled' : 'dsh_provider_stream_failed'
  } finally {
    // An aborted caller signal cannot erase already delegated work. Independent
    // finite callback lifetime records cancellation without starting a new model.
    try {
      if (!finishRecorded && !finishAttempted) await saveFinish()
    } catch (error) { failure ??= error instanceof HubClientError ? error : new HubClientError('dsh_provider_finish_unavailable', false) }
    if (!terminalStageObserved) {
      terminalStageObserved = true
      observe(failure === undefined && finish !== null ? 'stream_finished' : 'stream_failed',
        finishBytes === undefined ? {} : { finish_bytes: finishBytes })
    }
  }
  if (failure !== undefined) throw failure
}
