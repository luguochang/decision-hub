import { afterEach, describe, expect, it, vi } from 'vitest'
import { hasAcceptedSynthesis, hasAcceptedSynthesisAfterPrompt } from '../src/host/session-result.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'
import { harness, request, submitPayload } from './lifecycle-harness.js'

const event = (seq: number, type: string, data: Record<string, unknown>): SessionEventLike => ({ seq, type, time: Date.now(), data })
const call = (seq: number, id: string, args: unknown, nested = false) => {
  const row = { callId: id, name: 'decision_hub_synthesis_submit', arguments: args }
  return event(seq, 'tool/call', nested ? { message: { content: [{ ...row, type: 'tool-call' }] } } : row)
}
const result = (seq: number, id: string, isError = false) => event(seq, 'tool/result', {
  message: { content: [{ type: 'tool-result', toolCallId: id, isError,
    value: { schema_version: id === 'prepare' ? 'decision-hub-perpetual-preparation.v1' : 'decision-hub-synthesis-tool-result.v4' } }] },
})

afterEach(() => vi.useRealTimers())

describe('perpetual prepare is an intermediate step, not final synthesis', () => {
  it.each(['object', 'string', 'nested'] as const)('does not accept successful %s preparation as final', form => {
    const args = { action: 'prepare', request_id: 'req-1', draft: { untouched: true } }
    const rows = [call(1, 'prepare', form === 'string' ? JSON.stringify(args) : args, form === 'nested'), result(2, 'prepare')]
    const saved = structuredClone(rows)
    expect(hasAcceptedSynthesis(rows)).toBe(false)
    expect(rows).toEqual(saved)
  })

  it('accepts only a successful finalization or legacy submission in its own command', () => {
    const prompt = (seq: number, id: string) => event(seq, 'user/message', { source: { kind: 'user', rpcId: id } })
    const rows = [prompt(0, 'req-1'), call(1, 'prepare', { action: 'prepare' }), result(2, 'prepare'),
      call(3, 'failed-final', JSON.stringify({ action: 'finalize', prepared_call_id: 'prepare' })), result(4, 'failed-final', true)]
    const inspection = { meta: { id: 'root' }, events: rows }
    expect(hasAcceptedSynthesisAfterPrompt(inspection, 'req-1')).toBe(false)
    rows.push(prompt(5, 'req-2'), call(6, 'final', { action: 'finalize', prepared_call_id: 'prepare-2' }), result(7, 'final'))
    expect(hasAcceptedSynthesisAfterPrompt(inspection, 'req-1')).toBe(false)
    expect(hasAcceptedSynthesisAfterPrompt(inspection, 'req-2')).toBe(true)
    expect(hasAcceptedSynthesis([call(1, 'legacy', '{}'), result(2, 'legacy')])).toBe(true)
  })

  it('keeps the real Host alive through prepare and foreground review, then stops after finalize', async () => {
    vi.useFakeTimers()
    const app = harness()
    try {
      const payload = submitPayload()
      await app.call(request('PUT', '/decision-hub/v1/runs/run-1', payload))
      const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
      const prepared = [call(2, 'prepare', JSON.stringify({ action: 'prepare', request_id: payload.deterministic_request_id })), result(3, 'prepare')]
      inspection.events.push(...prepared)
      await app.emit('session/event', { id: payload.deterministic_session_id }, prepared[1])
      await vi.advanceTimersByTimeAsync(1)
      expect(app.sessions.cancel).not.toHaveBeenCalled()
      expect(app.state.terminalBodies).toHaveLength(0)

      const reviewed = [event(4, 'tool/call', { callId: 'review', name: 'subagent_reviewer', arguments: '{}' }), result(5, 'review')]
      inspection.events.push(...reviewed)
      await app.emit('session/event', { id: payload.deterministic_session_id }, reviewed[1])
      await vi.advanceTimersByTimeAsync(1)
      expect(app.sessions.cancel).not.toHaveBeenCalled()

      const final = [call(6, 'final', { action: 'finalize', prepared_call_id: 'prepare' }), result(7, 'final')]
      inspection.events.push(...final)
      await app.emit('session/event', { id: payload.deterministic_session_id }, final[1])
      await vi.advanceTimersByTimeAsync(1)
      expect(app.sessions.cancel).toHaveBeenCalledTimes(1)
      expect(app.sessions.cancel).toHaveBeenCalledWith({ sessionId: payload.deterministic_session_id })
    } finally { vi.clearAllTimers() }
  })
})
