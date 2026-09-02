import { createHash } from 'node:crypto'
import type { DshSessionCompletion, DshSessionResult, DshSessionStatus } from '@decision-hub/contracts-ts'
import { dshSessionCompletionSchema, dshSessionResultSchema, dshSessionStatusSchema } from '@decision-hub/contracts-ts'
import type { Correlation } from './session-correlation.js'
import type { SessionEventLike } from './dsh-public.js'

interface InspectionLike {
  meta: { id: string; createdAt?: number }
  events: SessionEventLike[]
}

function iso(time: number | undefined): string {
  return new Date(time ?? Date.now()).toISOString()
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

export function hasAcceptedPrompt(inspection: InspectionLike, requestId: string): boolean {
  return promptIndex(inspection.events, requestId) >= 0
}

export function projectInspection(correlation: Correlation, inspection: InspectionLike): {
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

  if (at >= 0) {
    const tail = events.slice(at + 1)
    const turnEnd = tail.find(event => event.type === 'turn/end')
    if (turnEnd === undefined) {
      state = state === 'admitted' ? 'running' : state
    } else {
      const reason = turnEnd.data.reason
      const kind = typeof reason === 'object' && reason !== null
        ? String((reason as Record<string, unknown>).kind ?? 'unknown')
        : 'unknown'
      const assistant = tail.filter(event => event.type === 'assistant/message').at(-1)
      const finalResponse = assistant === undefined ? '' : textOfAssistant(assistant)
      const completedAt = iso(turnEnd.time)
      const traceRef = `dsh://sessions/${encodeURIComponent(correlation.sessionId)}?last_seq=${lastSeq}`
      if (correlation.modelStepTimeoutTriggered) {
        state = 'failed'
        errorCode = 'dsh_model_step_timeout'
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'failed',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: null, result_hash: null, completed_at: completedAt,
          error: {
            code: 'dsh_model_step_timeout',
            message: 'DSH model step exceeded the Host watchdog budget',
            retryable: true,
          },
        })
      } else if ((kind === 'completed' || kind === 'max-tokens') && assistant !== undefined) {
        const notifications = tail
          .filter(event => event.type !== 'assistant/chunk' && event.type !== 'request/header')
          .map(event => ({
            method: 'session.event',
            payload: { sessionId: correlation.sessionId, event },
          }))
        const eventsJson = JSON.stringify(notifications)
        const hash = createHash('sha256')
          .update(finalResponse).update('\0').update(eventsJson).digest('hex')
        result = dshSessionResultSchema.parse({
          schema_version: 'dsh-session-result.v1',
          run_id: correlation.runId,
          dsh_session_id: correlation.sessionId,
          generation: correlation.generation,
          last_seq: lastSeq,
          final_response: finalResponse,
          finish_reason: kind,
          events_json: eventsJson,
          started_at: iso(events[at]?.time ?? inspection.meta.createdAt),
          finished_at: completedAt,
          trace_ref: traceRef,
          result_hash: hash,
        })
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
          result_hash: hash,
          completed_at: completedAt,
          error: null,
        })
      } else if (kind === 'aborted') {
        state = 'cancelled'
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'cancelled',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: null, result_hash: null, completed_at: completedAt, error: null,
        })
      } else {
        state = 'failed'
        errorCode = kind === 'completed' ? 'host_terminal_result_unavailable' : `dsh_turn_${kind}`
        completion = dshSessionCompletionSchema.parse({
          schema_version: 'dsh-session-completion.v1', run_id: correlation.runId,
          dsh_session_id: correlation.sessionId, terminal_status: 'failed',
          generation: correlation.generation, last_seq: lastSeq, trace_ref: traceRef,
          result_ref: null, result_hash: null, completed_at: completedAt,
          error: { code: errorCode, message: 'DSH turn did not produce an attested result', retryable: false },
        })
      }
    }
  }

  const status = dshSessionStatusSchema.parse({
    schema_version: 'dsh-session-status.v1', run_id: correlation.runId,
    dsh_session_id: correlation.sessionId, state, generation: correlation.generation,
    last_seq: lastSeq, observed_at: new Date().toISOString(), error_code: errorCode,
  })
  return { status, completion, result }
}
