import type { Correlation } from '../../src/host/session-correlation.js'
import { deterministicIds } from '../../src/host/session-correlation.js'
import type { SessionEventLike } from '../../src/host/dsh-public.js'
import type { DshRunSessionLinkView, DshSessionResult } from '@decision-hub/contracts-ts'

const epoch = Date.parse('2026-10-29T00:00:00Z')
export const event = (seq: number, type: string, data: Record<string, unknown>): SessionEventLike =>
  ({ seq, type, time: epoch + seq, data,
    ...(['user/message', 'assistant/message', 'tool/result'].includes(type) ? { surfaceOp: 'append' } : {}) })
export const baseline = (seq: number): SessionEventLike => ({ ...event(seq, 'user/message', {
  role: 'user', id: `baseline-${seq}`, source: { kind: 'agent-instructions', form: 'instructions',
    baseline: true, baselineIdentity: 'synthetic-config', changes: [
      { action: 'set', scope: '.', path: 'AGENTS.md', digest: 'a'.repeat(40) },
    ] }, content: [{ type: 'text', text: 'Synthetic instructions.' }],
}), surfaceOp: 'append' } as SessionEventLike)

export function fixture() {
  const runId = 'run-host-binding', requestHash = 'b'.repeat(64), ids = deterministicIds(runId, requestHash)
  const correlation: Correlation = { runId, requestHash, sessionId: ids.sessionId, requestId: ids.requestId,
    generation: 1, modelStepTimeoutMs: 150000, modelStepTimeoutSource: 'submission',
    modelStepTimeoutTriggered: false, state: 'running', lastSeq: 0, errorCode: null,
    acceptedAt: null, terminalAt: null }
  const events = [event(0, 'turn/start', {}), baseline(1),
    event(2, 'user/message', { source: { kind: 'user', rpcId: ids.requestId }, content: [{ type: 'text', text: 'research' }] }),
    event(3, 'tool/call', { name: 'subagent_reviewer', callId: 'review-negative' }),
    event(4, 'tool/result', { callId: 'review-negative' }), event(5, 'compaction/start', {}),
    { ...event(6, 'user/message', { role: 'user', id: 'summary', source: { kind: 'compact' }, content: [{ type: 'text', text: 'summary' }] }),
      surfaceOp: { op: 'replace', start: 1, end: 4 }, sourceEventSeqs: [1, 2, 3, 4, 5] },
    baseline(7), event(8, 'tool/call', { name: 'subagent_reviewer', callId: 'review-approved' }),
    event(9, 'tool/result', { callId: 'review-approved' }), baseline(10),
    event(11, 'assistant/message', { message: { content: [{ type: 'text', text: 'final answer' }] } }),
    event(12, 'turn/end', { reason: { kind: 'completed' } })]
  return { correlation, inspection: { meta: { id: ids.sessionId }, events } }
}

export function acknowledgedLink(f: ReturnType<typeof fixture>, result: DshSessionResult): DshRunSessionLinkView {
  const finished = result.finished_at
  return { schema_version: 'dsh-run-session-link.v1', run_id: f.correlation.runId,
    dsh_session_id: f.correlation.sessionId, request_hash: f.correlation.requestHash, state: 'completed', generation: 1,
    upstream_identity: { source_commit: 'a'.repeat(40), source_version: 'synthetic',
      package_versions: {}, plugin_build_hash: 'b'.repeat(64) }, deadline_at: finished,
    max_tool_calls: 12, tool_calls_started: 2, model_step_timeout_ms: 150000,
    accepted_at: null, last_seen_at: null, terminal_at: finished, last_seq: result.last_seq,
    trace_ref: result.trace_ref, result_ref: `dsh-host://runs/${f.correlation.runId}/result`,
    result_hash: result.result_hash, error_code: null, created_at: finished, updated_at: finished }
}
