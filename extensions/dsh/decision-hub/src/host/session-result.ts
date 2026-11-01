import { createHash } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'
import type { DshRunSessionLinkView, DshSessionCompletion, DshSessionResult, DshSessionStatus } from '@decision-hub/contracts-ts'
import { dshSessionCompletionSchema, dshSessionResultSchema, dshSessionStatusSchema } from '@decision-hub/contracts-ts'
import { deterministicIds, type Correlation } from './session-correlation.js'
import type { SessionEventLike } from './dsh-public.js'
import { modelBindingErrorCode } from './model-errors.js'
import { deriveReviewerInstructionBindings } from './review-instruction-bindings.js'

interface InspectionLike {
  meta: { id: string; createdAt?: number }
  events: SessionEventLike[]
}

function iso(time: number | undefined): string {
  return new Date(time ?? Date.now()).toISOString()
}

interface CatalogTransportFailure {
  code: 'research_catalog_transport_timeout' | 'research_catalog_transport_unavailable' | 'research_catalog_transport_cancelled'
  phase: 'fetch' | 'response_body'
  deadlineMs: number | null
}

function catalogTransportFailure(reason: unknown): CatalogTransportFailure | null {
  const error = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).error : null
  const message = error && typeof error === 'object' ? (error as Record<string, unknown>).message : null
  const match = typeof message === 'string'
    ? /^(research_catalog_transport_(?:timeout|unavailable|cancelled)) phase=(fetch|response_body)(?: deadline_ms=([1-9][0-9]{0,4}))?$/.exec(message) : null
  if (match === null) return null
  const deadlineMs = match[3] === undefined ? null : Number(match[3])
  if (deadlineMs !== null && (!Number.isSafeInteger(deadlineMs) || deadlineMs > 30_000)) return null
  if (match[1] !== 'research_catalog_transport_timeout' && deadlineMs !== null) return null
  return {
    code: match[1] as CatalogTransportFailure['code'],
    phase: match[2] as CatalogTransportFailure['phase'],
    deadlineMs,
  }
}

function managedReadFailure(reason: unknown): string | null {
  const error = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).error : null
  const message = error && typeof error === 'object' ? (error as Record<string, unknown>).message : null
  return typeof message === 'string' && /^dsh_host_read_(?:transport_timeout|deadline_elapsed|cancelled)$/.test(message)
    ? message : null
}

function baselineFailure(reason: unknown): string | null {
  const error = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).error : null
  if (!error || typeof error !== 'object') return null
  const row = error as Record<string, unknown>
  // SDK preStep errors have UNKNOWN plus the exact local marker. Never parse
  // diagnostic cause chains or surface arbitrary upstream response text.
  const candidate = row.code === 'UNKNOWN' ? row.message : row.code
  const safe = modelBindingErrorCode(candidate)
  return safe?.startsWith('research_baseline_') ? safe : null
}

function turnFailureMessage(reason: unknown): string {
  const error = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).error : null
  const code = error && typeof error === 'object' ? (error as Record<string, unknown>).code : null
  const message = error && typeof error === 'object' ? (error as Record<string, unknown>).message : null
  const catalogTransport = catalogTransportFailure(reason)
  const managedRead = managedReadFailure(reason)
  const baseline = baselineFailure(reason)
  if (baseline) return `Research baseline acquisition failed (${baseline})`
  if (managedRead) return `Host research read failed (${managedRead})`
  if (catalogTransport) return `Research capability catalog failed (${catalogTransport.code}; phase=${catalogTransport.phase}`
    + `${catalogTransport.deadlineMs === null ? '' : `; deadline_ms=${catalogTransport.deadlineMs}`})`
  // This exact runtime-owned marker contains only an HTTP status. Preserve it
  // in the existing authenticated terminal receipt; arbitrary text stays redacted.
  const catalogStatus = typeof message === 'string'
    ? /^research_catalog_http_([45][0-9]{2})$/.exec(message)?.[1] : undefined
  if (catalogStatus !== undefined) return `Research capability catalog rejected (HTTP ${catalogStatus})`
  // Preserve a bounded provider code, never copy free-form transport messages
  // or headers into the business callback (those can contain credentials).
  return typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(code)
    ? `DSH turn failed (${code})`
    : 'DSH turn did not produce an attested result'
}

function catalogTransportRetryable(reason: unknown): boolean {
  const failure = catalogTransportFailure(reason)
  return failure !== null && failure.code !== 'research_catalog_transport_cancelled'
}

function turnErrorCode(reason: unknown): string | null {
  const error = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).error : null
  const code = error && typeof error === 'object' ? (error as Record<string, unknown>).code : null
  return typeof code === 'string' ? code : null
}

function nestedRecords(value: unknown): Record<string, unknown>[] {
  if (typeof value !== 'object' || value === null) return []
  if (Array.isArray(value)) return value.flatMap(nestedRecords)
  const row = value as Record<string, unknown>
  return [row, ...Object.values(row).flatMap(nestedRecords)]
}

function firstString(values: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = values[key]
    if (typeof value === 'string' && value.length > 0) return value
  }
  return null
}

function toolCallIdentity(event: SessionEventLike): { callId: string; toolName: string } | null {
  const rows = [
    event.data,
    ...nestedRecords(event.data).filter(row => row.type === 'tool-call' || row.type === 'tool_call'),
  ]
  for (const row of rows) {
    const callId = firstString(row, ['toolCallId', 'callId', 'id'])
    const toolName = firstString(row, ['toolName', 'name'])
    if (callId !== null && toolName !== null) return { callId, toolName }
  }
  return null
}

function successfulToolResultIds(event: SessionEventLike): string[] {
  if (event.type !== 'tool/result' && event.type !== 'tool/call-result') return []
  if (event.data.error !== undefined && event.data.error !== null) return []
  if (event.data.isError === true || event.data.status === 'error') return []
  const typed = nestedRecords(event.data).filter(
    row => row.type === 'tool-result' || row.type === 'tool_result',
  )
  const rows = typed.length > 0 ? typed : [event.data]
  return rows.flatMap(row => {
    if (row.error !== undefined && row.error !== null) return []
    if (row.isError === true || row.status === 'error') return []
    const callId = firstString(row, ['toolCallId', 'callId', 'id'])
    return callId === null ? [] : [callId]
  })
}

function hasAcceptedTool(events: SessionEventLike[], acceptedNames: ReadonlySet<string>): boolean {
  const calls = new Set(events.flatMap(event => {
    if (event.type !== 'tool/call') return []
    const identity = toolCallIdentity(event)
    if (identity === null || ![...acceptedNames].some(name => identity.toolName.endsWith(name))) return []
    return [identity.callId]
  }))
  if (calls.size === 0) return false
  return events.some(event => successfulToolResultIds(event).some(callId => calls.has(callId)))
}

export function hasAcceptedSynthesis(events: SessionEventLike[]): boolean {
  // A successful V2 prepare is an intermediate packet. Stopping here would
  // cancel the native turn before its independent reviewer and finalization.
  const finalCalls = events.filter(event => {
    if (event.type !== 'tool/call') return true
    const rows = [event.data, ...nestedRecords(event.data).filter(row => row.type === 'tool-call' || row.type === 'tool_call')]
    for (const row of rows) {
      if (!firstString(row, ['toolName', 'name'])?.endsWith('decision_hub_synthesis_submit')) continue
      const raw = row.arguments ?? row.args
      if (raw === undefined) return true // Legacy phase-less call vocabulary.
      try {
        const args: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw
        return typeof args === 'object' && args !== null && !Array.isArray(args)
          && (!('action' in args) || args.action === 'finalize')
      } catch { return false }
    }
    return false
  })
  return hasAcceptedTool(finalCalls, new Set(['decision_hub_synthesis_submit']))
}

export function hasAcceptedSynthesisAfterPrompt(
  inspection: InspectionLike,
  requestId: string,
): boolean {
  const at = promptIndex(inspection.events, requestId)
  return at >= 0 && hasAcceptedSynthesis(commandEvents(inspection, requestId))
}

function hasAcceptedResearch(events: SessionEventLike[]): boolean {
  return hasAcceptedTool(events, new Set(['decision_hub_research', 'research_capability_execute']))
}

function historicalAcceptedToolEvents(
  events: SessionEventLike[],
  beforeSeq: number,
): SessionEventLike[] {
  // A bounded replan is still the same durable DSH Session. If the latest
  // continuation ends without content, retain the attested calls from the
  // earlier generations so the product runtime can reconstruct the evidence
  // and synthesis that already completed. Keep only the call/result pairs for
  // product-owned tools; model prose and unrelated DSH bookkeeping are not
  // part of the recoverable product result.
  const acceptedNames = new Set([
    'decision_hub_research',
    'research_capability_execute',
    'decision_hub_synthesis_submit',
    'subagent_reviewer',
    'subagent',
    'dsh.internal.subagent',
  ])
  const calls = new Set<string>()
  for (const event of events) {
    if (event.seq >= beforeSeq || event.type !== 'tool/call') continue
    const identity = toolCallIdentity(event)
    if (identity !== null && [...acceptedNames].some(name => identity.toolName.endsWith(name))) {
      calls.add(identity.callId)
    }
  }
  return events.filter(event => {
    if (event.seq >= beforeSeq) return false
    if (event.type === 'tool/call') {
      const identity = toolCallIdentity(event)
      return identity !== null && calls.has(identity.callId)
    }
    if (event.type === 'tool/result' || event.type === 'tool/call-result') {
      return successfulToolResultIds(event).some(callId => calls.has(callId))
        || [...nestedRecords(event.data)].some(row => {
          const callId = firstString(row, ['toolCallId', 'callId', 'id'])
          return callId !== null && calls.has(callId)
        })
    }
    return false
  })
}

function promptIndex(events: SessionEventLike[], requestId: string): number {
  return events.findIndex(event => {
    if (event.type !== 'user/message') return false
    const source = event.data.source
    return typeof source === 'object' && source !== null
      && (source as Record<string, unknown>).kind === 'user'
      && (source as Record<string, unknown>).rpcId === requestId
  })
}

function textOfAssistant(event: SessionEventLike): string {
  const message = event.data.message
  if (typeof message !== 'object' || message === null) return ''
  const content = (message as Record<string, unknown>).content
  if (!Array.isArray(content)) return ''
  return content.flatMap(block => {
    if (typeof block !== 'object' || block === null) return []
    const row = block as Record<string, unknown>
    return row.type === 'text' && typeof row.text === 'string' ? [row.text] : []
  }).join('\n').trim()
}

const EVIDENCE_ONLY_RESPONSE =
  '本轮研究未形成可发布的结构化结论；已保留经核验的证据与缺口，请查看 Decision Hub 研究报告。'

export function hasAcceptedPrompt(inspection: InspectionLike, requestId: string): boolean {
  return promptIndex(inspection.events, requestId) >= 0
}

export interface CommandActivity {
  parentSeq: number
  parentRunning: boolean
  queueCount: number
  activeJobCount: number
}

export interface CommandDelegations {
  childIds: string[]
  jobIds: string[]
  unresolved: boolean
  lastNoticeSeq: number
}

function commandEvents(inspection: InspectionLike, requestId: string): SessionEventLike[] {
  const at = promptIndex(inspection.events, requestId)
  if (at < 0) return []
  const next = inspection.events.findIndex((event, index) => {
    if (index <= at || event.type !== 'user/message') return false
    const source = event.data.source
    return typeof source === 'object' && source !== null
      && (source as Record<string, unknown>).kind === 'user'
      && (source as Record<string, unknown>).rpcId !== requestId
  })
  return inspection.events.slice(at + 1, next < 0 ? undefined : next)
}

function toolArguments(event: SessionEventLike): Record<string, unknown> | null {
  const encoded = event.data.arguments ?? event.data.args
  if (typeof encoded === 'object' && encoded !== null && !Array.isArray(encoded)) {
    return encoded as Record<string, unknown>
  }
  if (typeof encoded !== 'string' || encoded.length > 131_072) return null
  try {
    const parsed: unknown = JSON.parse(encoded)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown> : null
  } catch { return null }
}

function resultText(event: SessionEventLike, callId: string): string | null {
  const rows = nestedRecords(event.data).filter(row =>
    (row.type === 'tool-result' || row.type === 'tool_result')
    && firstString(row, ['toolCallId', 'callId', 'id']) === callId)
  if (rows.length !== 1 || rows[0]!.isError === true) return null
  const content = rows[0]!.content
  if (!Array.isArray(content)) return null
  return content.flatMap(block => typeof block === 'object' && block !== null
    && (block as Record<string, unknown>).type === 'text'
    && typeof (block as Record<string, unknown>).text === 'string'
    ? [(block as Record<string, unknown>).text as string] : []).join('\n')
}

/** Only successful product-command delegate receipts establish these identities. */
export function commandDelegations(inspection: InspectionLike, requestId: string): CommandDelegations {
  const events = commandEvents(inspection, requestId)
  const children = new Map<string, number>()
  const jobs = new Map<string, number>()
  let unresolved = false
  for (const call of events) {
    if (call.type !== 'tool/call') continue
    const identity = toolCallIdentity(call)
    if (identity === null || !/(?:^|[./])subagent(?:_reviewer)?$/.test(identity.toolName)) continue
    const args = toolArguments(call)
    if (args === null || (args.run_in_background !== undefined && typeof args.run_in_background !== 'boolean')) {
      unresolved = true; continue
    }
    // The original product preset declares continuable research and a one-shot reviewer.
    const defaultBackground = !identity.toolName.endsWith('subagent_reviewer')
    if ((args.run_in_background ?? defaultBackground) === false) continue
    const result = events.find(event => event.seq > call.seq
      && successfulToolResultIds(event).includes(identity.callId))
    if (result === undefined) {
      const failed = events.some(event => event.seq > call.seq && (event.type === 'tool/result' || event.type === 'tool/call-result')
        && nestedRecords(event.data).some(row => firstString(row, ['toolCallId', 'callId', 'id']) === identity.callId
          && (row.isError === true || row.status === 'error' || (row.error !== undefined && row.error !== null))))
      if (!failed) unresolved = true
      continue
    }
    const text = resultText(result, identity.callId)
    const child = text?.match(/^started subagent ([A-Za-z0-9_-]{1,256})$/)?.[1]
    const job = text?.match(/^started background subagent job ([A-Za-z0-9_-]{1,256})$/)?.[1]
    if (child) children.set(child, result.seq)
    else if (job) jobs.set(job, result.seq)
    else unresolved = true
  }
  if (children.size + jobs.size > 64) return { childIds: [], jobIds: [], unresolved: true, lastNoticeSeq: -1 }
  let lastNoticeSeq = -1
  for (const [id, after] of children) {
    const notice = events.find(event => {
      const source = event.data.source
      return event.seq > after && event.type === 'user/message'
        && typeof source === 'object' && source !== null
        && (source as Record<string, unknown>).kind === 'subagent-settled'
        && (source as Record<string, unknown>).senderSessionId === id
    })
    if (notice === undefined) unresolved = true
    else lastNoticeSeq = Math.max(lastNoticeSeq, notice.seq)
  }
  for (const [id, after] of jobs) {
    const notice = events.find(event => {
      const source = event.data.source
      const content = event.data.content
      return event.seq > after && event.type === 'user/message'
        && typeof source === 'object' && source !== null
        && (source as Record<string, unknown>).kind === 'plugin'
        && (source as Record<string, unknown>).plugin === 'tool-jobs'
        && (source as Record<string, unknown>).form === 'notice'
        && Array.isArray(content) && content.some(block => typeof block === 'object' && block !== null
          && (block as Record<string, unknown>).type === 'text'
          && typeof (block as Record<string, unknown>).text === 'string'
          && ((block as Record<string, unknown>).text as string).startsWith(`background job ${id} `))
    })
    const collected = events.find(event => {
      if (event.seq <= after || event.type !== 'tool/call') return false
      const identity = toolCallIdentity(event)
      if (identity === null || !/(?:^|[./])job_output$/.test(identity.toolName)
        || toolArguments(event)?.job_id !== id) return false
      return events.some(result => result.seq > event.seq
        && successfulToolResultIds(result).includes(identity.callId)
        && /\n\[status: (?:completed|failed|killed)(?:, [^\n]*)?\]$/.test(resultText(result, identity.callId) ?? ''))
    })
    const collectedResult = collected === undefined ? undefined : events.find(result => result.seq > collected.seq
      && successfulToolResultIds(result).includes(toolCallIdentity(collected)!.callId))
    if (notice === undefined && collectedResult === undefined) unresolved = true
    else lastNoticeSeq = Math.max(lastNoticeSeq, notice?.seq ?? -1, collectedResult?.seq ?? -1)
  }
  return { childIds: [...children.keys()], jobIds: [...jobs.keys()], unresolved, lastNoticeSeq }
}

// Assembly can fail before the normal user/message projection exists.
// Only an exact original inbox RPC and its sole pre-model turn close this case.
function preModelTurnError(correlation: Correlation, inspection: InspectionLike): SessionEventLike | null {
  if (inspection.meta.id !== correlation.sessionId) return null
  const anchors = inspection.events.filter(event => {
    if (event.type !== 'agent/inbox/spliced' || event.data.target !== 'next-turn') return false
    const inserted = event.data.inserted
    if (!Array.isArray(inserted) || inserted.length !== 1) return false
    const message: unknown = inserted[0]
    if (typeof message !== 'object' || message === null) return false
    const row = message as Record<string, unknown>
    const source = row.source
    return row.role === 'user' && typeof row.id === 'string' && row.id.length > 0
      && typeof source === 'object' && source !== null
      && (source as Record<string, unknown>).kind === 'user'
      && (source as Record<string, unknown>).rpcId === correlation.requestId
  })
  if (anchors.length !== 1) return null
  const anchor = anchors[0]!
  const tail = inspection.events.filter(event => event.seq > anchor.seq)
  if (tail.length === 0 || tail.some((event, index) => !Number.isSafeInteger(event.seq)
    || (index > 0 && event.seq <= tail[index - 1]!.seq))) return null
  const starts = tail.filter(event => event.type === 'turn/start')
  const ends = tail.filter(event => event.type === 'turn/end')
  if (starts.length !== 1 || ends.length !== 1) return null
  const start = starts[0]!
  const end = ends[0]!
  if (start.seq >= end.seq || end !== tail.at(-1)
    || !Number.isSafeInteger(start.data.turn) || (start.data.turn as number) < 1
    || start.data.turn !== end.data.turn) return null
  for (const event of tail) {
    if (event.type === 'turn/start' || event.type === 'turn/end') continue
    // Claiming the queued message drains an empty next-turn inbox. A new
    // message, another RPC, or any model/tool event makes this scope unknown.
    if (event.type !== 'agent/inbox/spliced' || event.data.target !== 'next-turn'
      || !Array.isArray(event.data.inserted) || event.data.inserted.length !== 0) return null
  }
  const reason = end.data.reason
  if (typeof reason !== 'object' || reason === null
    || (reason as Record<string, unknown>).kind !== 'error') return null
  const error = (reason as Record<string, unknown>).error
  if (typeof error !== 'object' || error === null) return null
  return end
}

interface ProjectionOptions {
  commandLifecycle: true
  activity?: CommandActivity
  ownerCancellation?: { pending: true } | { pending: false; observedAt: string }
  abortedQuiescent?: boolean
  acknowledgedTerminal?: true
}

export function projectInspection(correlation: Correlation, inspection: InspectionLike, options?: ProjectionOptions) {
  return projectInspectionWithInstructionSource(correlation, inspection, options, inspection)
}

function projectInspectionWithInstructionSource(correlation: Correlation, inspection: InspectionLike,
  options: ProjectionOptions | undefined, instructionSource: InspectionLike | null): {
  status: DshSessionStatus
  completion: DshSessionCompletion | null
  result: DshSessionResult | null
} {
  const events = inspection.events
  const lastSeq = events.reduce((value, event) => Math.max(value, event.seq), 0)
  const at = promptIndex(events, correlation.requestId)
  let state = correlation.state
  let errorCode: string | null = correlation.errorCode
  let completion: DshSessionCompletion | null = null
  let result: DshSessionResult | null = null

  const pending = (code: string) => ({
    status: dshSessionStatusSchema.parse({
      schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, state: 'idle', generation: correlation.generation,
      last_seq: lastSeq, observed_at: new Date().toISOString(), error_code: code,
      model_step_timeout_ms: correlation.modelStepTimeoutMs,
      model_step_timeout_source: correlation.modelStepTimeoutSource,
    }), completion: null, result: null,
  })
  if (options?.ownerCancellation?.pending === true) return pending('host_cancel_pending')

  const earlyError = at < 0 && options?.commandLifecycle && options.ownerCancellation === undefined
    && !correlation.modelStepTimeoutTriggered ? preModelTurnError(correlation, inspection) : null
  if (earlyError !== null) {
    state = 'failed'
    errorCode = baselineFailure(earlyError.data.reason) ?? managedReadFailure(earlyError.data.reason) ?? catalogTransportFailure(earlyError.data.reason)?.code ?? 'dsh_turn_error'
    completion = dshSessionCompletionSchema.parse({
      schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, terminal_status: 'failed',
      generation: correlation.generation, last_seq: lastSeq,
      trace_ref: `dsh://sessions/${encodeURIComponent(correlation.sessionId)}?last_seq=${lastSeq}`,
      result_ref: null, result_hash: null, completed_at: iso(earlyError.time),
      error: {
        code: errorCode,
        message: turnFailureMessage(earlyError.data.reason),
        retryable: catalogTransportRetryable(earlyError.data.reason),
      },
    })
  }

  if (at >= 0) {
    const tail = options?.commandLifecycle ? commandEvents(inspection, correlation.requestId) : events.slice(at + 1)
    const turnEnd = options?.commandLifecycle
      ? tail.filter(event => event.type === 'turn/end').at(-1)
      : tail.find(event => event.type === 'turn/end')
    if (turnEnd === undefined) {
      state = state === 'admitted' ? 'running' : state
    } else {
      const reason = turnEnd.data.reason
      const kind = typeof reason === 'object' && reason !== null
        ? String((reason as Record<string, unknown>).kind ?? 'unknown')
        : 'unknown'
      const synthesisAccepted = hasAcceptedSynthesis(tail)
      const historicalSynthesisAccepted = !synthesisAccepted && hasAcceptedSynthesis(options?.commandLifecycle ? events.slice(0, at) : events)
      const delegated = options?.commandLifecycle && !options.acknowledgedTerminal
        ? commandDelegations(inspection, correlation.requestId) : null
      const commandPending = delegated !== null && (delegated.unresolved
        || (delegated.childIds.length + delegated.jobIds.length > 0
          && (turnEnd.seq <= delegated.lastNoticeSeq || options?.activity === undefined
            || options.activity.parentSeq < lastSeq || options.activity.parentRunning
            || options.activity.queueCount > 0 || options.activity.activeJobCount > 0)))
      if (options?.commandLifecycle && options.ownerCancellation === undefined && !correlation.modelStepTimeoutTriggered
        && (kind === 'completed' || kind === 'max-tokens')) {
        if (commandPending) {
          return { status: dshSessionStatusSchema.parse({
            schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
            dsh_session_id: correlation.sessionId, state: 'running', generation: correlation.generation,
            last_seq: lastSeq, observed_at: new Date().toISOString(), error_code: null,
            model_step_timeout_ms: correlation.modelStepTimeoutMs,
            model_step_timeout_source: correlation.modelStepTimeoutSource,
          }), completion: null, result: null }
        }
      }
      const researchAccepted = hasAcceptedResearch(events)
      const effectiveKind = !commandPending && (synthesisAccepted || historicalSynthesisAccepted) && (
        kind === 'aborted'
        || (kind === 'error' && turnErrorCode(reason) === 'EMPTY_RESPONSE')
      ) ? 'completed' : kind
      if (options?.commandLifecycle && kind === 'aborted' && effectiveKind !== 'completed'
        && options.ownerCancellation === undefined && options.abortedQuiescent !== true && !options.acknowledgedTerminal
        && !correlation.modelStepTimeoutTriggered) return pending('host_command_activity_pending')
      const currentTurnStart = options?.commandLifecycle
        ? tail.filter(event => event.type === 'turn/start' && event.seq < turnEnd.seq).at(-1)?.seq ?? -1 : -1
      const assistant = tail.filter(event => event.type === 'assistant/message'
        && (!options?.commandLifecycle || (event.seq > currentTurnStart && event.seq < turnEnd.seq))).at(-1)
      const assistantText = assistant === undefined ? '' : textOfAssistant(assistant)
      // A reasoning-only assistant event is not a user-visible result. Keep a
      // bounded evidence-only message when trusted research exists so the DSH
      // surface never appears to hang on a blank response. The synthesis tool
      // remains authoritative: an accepted synthesis is allowed to end with
      // an empty trailing provider response.
      const finalResponse = assistantText.length > 0 || synthesisAccepted || historicalSynthesisAccepted || !researchAccepted
        ? assistantText
        : EVIDENCE_ONLY_RESPONSE
      const completedAt = iso(turnEnd.time)
      const traceRef = `dsh://sessions/${encodeURIComponent(correlation.sessionId)}?last_seq=${lastSeq}`
      const buildResult = (finishReason: string): DshSessionResult => {
        const historical = historicalAcceptedToolEvents(events, events[at]?.seq ?? Number.MAX_SAFE_INTEGER)
        // Preserve the current original control/RPC event for downstream scope proofs.
        const resultEvents = [...historical, ...events.slice(at, at + 1), ...tail]
          .filter((event, index, all) => all.findIndex(candidate => candidate.seq === event.seq) === index)
          .sort((left, right) => left.seq - right.seq)
        const notifications = resultEvents
          .filter(event => event.type !== 'assistant/chunk' && event.type !== 'request/header')
          .map(event => ({
            method: 'session.event',
            payload: { sessionId: correlation.sessionId, event },
          }))
        const eventsJson = JSON.stringify(notifications)
        const hash = createHash('sha256')
          .update(finalResponse).update('\0').update(eventsJson).digest('hex')
        const instructionBindings = instructionSource === null ? undefined
          : deriveReviewerInstructionBindings(correlation, instructionSource, resultEvents)
        return dshSessionResultSchema.parse({
          schema_version: 'dsh-session-result.v1',
          run_id: correlation.runId,
          dsh_session_id: correlation.sessionId,
          generation: correlation.generation,
          last_seq: lastSeq,
          final_response: finalResponse,
          finish_reason: finishReason,
          events_json: eventsJson,
          started_at: iso(events[at]?.time ?? inspection.meta.createdAt),
          finished_at: completedAt,
          trace_ref: traceRef,
          result_hash: hash,
          ...(instructionBindings === undefined ? {} : { reviewer_instruction_bindings: instructionBindings }),
        })
      }
      if (options?.ownerCancellation?.pending === false) {
        // Preserve actual native finish reason and notifications. Owner intent
        // controls the completion and never rewrites an accepted native result.
        result = researchAccepted ? buildResult(kind) : null
        state = 'cancelled'
        errorCode = null
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'cancelled',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: result === null ? null : `dsh-host://runs/${encodeURIComponent(correlation.runId)}/result`,
          result_hash: result?.result_hash ?? null, completed_at: options.ownerCancellation.observedAt, error: null,
        })
      } else if (correlation.modelStepTimeoutTriggered) {
        result = researchAccepted ? buildResult('error') : null
        state = 'failed'
        errorCode = 'dsh_model_step_timeout'
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'failed',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: result === null ? null : `dsh-host://runs/${encodeURIComponent(correlation.runId)}/result`,
          result_hash: result?.result_hash ?? null, completed_at: completedAt,
          error: {
            code: 'dsh_model_step_timeout',
            message: 'DSH model step exceeded the Host watchdog budget',
            retryable: true,
          },
        })
      } else if (
        (effectiveKind === 'completed' || effectiveKind === 'max-tokens')
        && ((assistantText.length > 0) || synthesisAccepted || historicalSynthesisAccepted)
      ) {
        result = buildResult(effectiveKind)
        state = 'completed'
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1',
          run_id: correlation.runId,
          dsh_session_id: correlation.sessionId,
          terminal_status: 'completed',
          generation: correlation.generation,
          last_seq: lastSeq,
          trace_ref: traceRef,
          result_ref: `dsh-host://runs/${encodeURIComponent(correlation.runId)}/result`,
          result_hash: result.result_hash,
          completed_at: completedAt,
          error: null,
        })
      } else if (kind === 'aborted') {
        result = researchAccepted ? buildResult('aborted') : null
        state = 'cancelled'
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'cancelled',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: result === null ? null : `dsh-host://runs/${encodeURIComponent(correlation.runId)}/result`,
          result_hash: result?.result_hash ?? null, completed_at: completedAt, error: null,
        })
      } else {
        result = researchAccepted ? buildResult(kind) : null
        state = 'failed'
        errorCode = modelBindingErrorCode(turnErrorCode(reason))
          ?? baselineFailure(reason)
          ?? managedReadFailure(reason)
          ?? catalogTransportFailure(reason)?.code
          ?? (kind === 'completed' ? 'host_terminal_result_unavailable' : `dsh_turn_${kind}`)
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'failed',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: result === null ? null : `dsh-host://runs/${encodeURIComponent(correlation.runId)}/result`,
          result_hash: result?.result_hash ?? null, completed_at: completedAt,
          error: {
            code: errorCode,
            message: turnFailureMessage(reason),
            retryable: catalogTransportRetryable(reason),
          },
        })
      }
    }
  }

  if (options?.ownerCancellation?.pending === false && completion === null) {
    // Queue cancellation before the first claimed turn has no turn/end/result.
    // It is terminal only after a fresh official quiescence proof.
    state = 'cancelled'
    errorCode = null
    completion = dshSessionCompletionSchema.parse({
      schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
      dsh_session_id: correlation.sessionId, terminal_status: 'cancelled',
      generation: correlation.generation, last_seq: lastSeq,
      trace_ref: `dsh://sessions/${encodeURIComponent(correlation.sessionId)}?last_seq=${lastSeq}`,
      result_ref: null, result_hash: null, completed_at: options.ownerCancellation.observedAt, error: null,
    })
  }
  const status = dshSessionStatusSchema.parse({
    schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
    dsh_session_id: correlation.sessionId, state, generation: correlation.generation,
    last_seq: lastSeq, observed_at: new Date().toISOString(), error_code: errorCode,
    model_step_timeout_ms: correlation.modelStepTimeoutMs,
    model_step_timeout_source: correlation.modelStepTimeoutSource,
  })
  return { status, completion, result }
}

/** Compare native millisecond instants without erasing nonzero sub-ms time. */
export function terminalTimeMs(value: string | null): number {
  if (value === null) return Number.NaN
  const match = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (match === null || /[1-9]/.test((match[1] ?? '').slice(3))) return Number.NaN
  return Date.parse(value)
}

function assertAcknowledgedLink(correlation: Correlation, link: DshRunSessionLinkView): void {
  const ids = deterministicIds(correlation.runId, correlation.requestHash, correlation.generation)
  const terminal = terminalTimeMs(link.terminal_at)
  if (link.run_id !== correlation.runId || link.dsh_session_id !== correlation.sessionId
    || ids.sessionId !== correlation.sessionId || ids.requestId !== correlation.requestId
    || link.request_hash !== correlation.requestHash || link.generation !== correlation.generation
    || link.terminal_at === null || !Number.isFinite(terminal)
    || terminal !== terminalTimeMs(correlation.terminalAt)
    || !Number.isSafeInteger(link.last_seq) || link.last_seq < 0
    || !['completed', 'failed', 'cancelled'].includes(link.state)) {
    throw new Error('host_terminal_result_binding_mismatch')
  }
}

/** Only the fresh authenticated Hub link can describe an acknowledged terminal. */
export function projectAcknowledgedStatus(correlation: Correlation, link: DshRunSessionLinkView): DshSessionStatus {
  assertAcknowledgedLink(correlation, link)
  return dshSessionStatusSchema.parse({
    schema_version: 'dsh-session-status.v1', run_id: link.run_id,
    dsh_session_id: link.dsh_session_id, state: link.state, generation: link.generation,
    last_seq: link.last_seq, observed_at: new Date().toISOString(), error_code: link.error_code,
    model_step_timeout_ms: correlation.modelStepTimeoutMs,
    model_step_timeout_source: correlation.modelStepTimeoutSource,
  })
}

/** The authenticated trace points to original result bytes, before failure drain. */
export function acknowledgedResultCursor(link: DshRunSessionLinkView): number {
  const prefix = `dsh://sessions/${encodeURIComponent(link.dsh_session_id)}?last_seq=`
  const digits = link.trace_ref?.startsWith(prefix) ? link.trace_ref.slice(prefix.length) : ''
  const cursor = /^(0|[1-9][0-9]*)$/.test(digits) ? Number(digits) : Number.NaN
  if (!Number.isSafeInteger(cursor) || cursor < 0 || !Number.isSafeInteger(link.last_seq)
    || cursor > link.last_seq || link.result_ref !== `dsh-host://runs/${encodeURIComponent(link.run_id)}/result`
    || link.result_hash === null || !/^[a-f0-9]{64}$/.test(link.result_hash)) {
    throw new Error('host_terminal_result_binding_mismatch')
  }
  return cursor
}

/** Cache and restart use identical byte, cursor, trace and terminal-time gates. */
function matchesOriginalResultMetadata(result: DshSessionResult, link: DshRunSessionLinkView,
  nativeInspection?: InspectionLike): boolean {
  // A sparse result cannot prove its own parent prefix. Bound results always
  // take the existing official native reread route, including on cache hits.
  if (result.reviewer_instruction_bindings !== undefined && nativeInspection === undefined) return false
  const rows: unknown = JSON.parse(result.events_json)
  if (!Array.isArray(rows)) return false
  const events: SessionEventLike[] = []
  for (const row of rows) {
    if (typeof row !== 'object' || row === null || row.method !== 'session.event'
      || typeof row.payload !== 'object' || row.payload === null || row.payload.sessionId !== link.dsh_session_id) return false
    const event = row.payload.event
    if (typeof event !== 'object' || event === null || !Number.isSafeInteger(event.seq) || event.seq < 0
      || typeof event.time !== 'number' || !Number.isFinite(event.time) || typeof event.type !== 'string'
      || typeof event.data !== 'object' || event.data === null || Array.isArray(event.data)) return false
    events.push(event as SessionEventLike)
  }
  const ids = deterministicIds(link.run_id, link.request_hash, link.generation)
  const projected = projectInspectionWithInstructionSource({
    runId: link.run_id, requestHash: link.request_hash, sessionId: link.dsh_session_id,
    requestId: ids.requestId, generation: link.generation, modelStepTimeoutMs: link.model_step_timeout_ms ?? 150000,
    modelStepTimeoutSource: 'durable_link', modelStepTimeoutTriggered: link.error_code === 'dsh_model_step_timeout',
    state: link.state, lastSeq: link.last_seq, errorCode: link.error_code,
    acceptedAt: link.accepted_at, terminalAt: link.terminal_at,
  }, nativeInspection === undefined ? { meta: { id: link.dsh_session_id }, events } : {
    ...nativeInspection, events: nativeInspection.events.filter(event => event.seq <= acknowledgedResultCursor(link)),
  }, {
    commandLifecycle: true, acknowledgedTerminal: true,
    ...(link.state === 'cancelled' && link.terminal_at !== null
      ? { ownerCancellation: { pending: false as const, observedAt: link.terminal_at } } : {}),
  }, nativeInspection ?? null)
  const original = projected.result
  return original !== null && projected.status.state === link.state && projected.status.error_code === link.error_code
    && original.started_at === result.started_at && original.finished_at === result.finished_at
    && original.finish_reason === result.finish_reason && original.final_response === result.final_response
    && original.events_json === result.events_json && original.result_hash === link.result_hash
    && isDeepStrictEqual(original.reviewer_instruction_bindings, result.reviewer_instruction_bindings)
}

export function matchesAcknowledgedResult(result: DshSessionResult, link: DshRunSessionLinkView,
  nativeInspection?: InspectionLike): boolean {
  try {
    const cursor = acknowledgedResultCursor(link)
    const finished = terminalTimeMs(result.finished_at)
    const terminal = terminalTimeMs(link.terminal_at)
    const hash = createHash('sha256').update(result.final_response).update('\0').update(result.events_json).digest('hex')
    return result.run_id === link.run_id && result.dsh_session_id === link.dsh_session_id
      && result.generation === link.generation && result.last_seq === cursor && result.trace_ref === link.trace_ref
      && result.result_hash === link.result_hash && hash === link.result_hash
      && Number.isFinite(finished) && Number.isFinite(terminal)
      && ['completed', 'failed', 'cancelled'].includes(link.state)
      && (link.state !== 'completed' || result.finish_reason === 'completed' || result.finish_reason === 'max-tokens')
      && (link.state === 'cancelled' ? finished <= terminal : finished === terminal)
      && matchesOriginalResultMetadata(result, link, nativeInspection)
  } catch { return false }
}

/** Recover a previously acknowledged result; this cannot create terminal authority. */
export function projectAcknowledgedResult(
  correlation: Correlation,
  inspection: InspectionLike,
  link: DshRunSessionLinkView,
): DshSessionResult {
  assertAcknowledgedLink(correlation, link)
  if (inspection.meta.id !== correlation.sessionId || link.terminal_at === null) {
    throw new Error('host_terminal_result_binding_mismatch')
  }
  const cursor = acknowledgedResultCursor(link)
  const bounded = { ...inspection, events: inspection.events.filter(event => event.seq <= cursor) }
  if (!bounded.events.some(event => event.seq === cursor)) {
    throw new Error('host_terminal_result_native_closure_mismatch')
  }
  const at = promptIndex(bounded.events, correlation.requestId)
  const tail = commandEvents(bounded, correlation.requestId)
  const nativeEnd = tail.filter(event => event.type === 'turn/end').at(-1)
  const terminal = terminalTimeMs(link.terminal_at)
  if (at < 0 || nativeEnd === undefined || !Number.isFinite(bounded.events[at]?.time)
    || !Number.isFinite(nativeEnd.time) || !Number.isFinite(terminal)
    || (link.state === 'cancelled' ? nativeEnd.time > terminal : nativeEnd.time !== terminal)) {
    throw new Error('host_terminal_result_native_closure_mismatch')
  }
  const projected = projectInspection(correlation, bounded, {
    commandLifecycle: true, acknowledgedTerminal: true,
    ...(link.state === 'cancelled' ? {
      ownerCancellation: { pending: false as const, observedAt: link.terminal_at },
    } : {}),
  })
  if (projected.result === null || !matchesAcknowledgedResult(projected.result, link, bounded)
    || projected.status.state !== link.state || projected.status.error_code !== link.error_code) {
    throw new Error('host_terminal_result_hash_mismatch')
  }
  return projected.result
}
