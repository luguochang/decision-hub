import { expect, it, vi } from 'vitest'
import { projectInspection } from '../src/host/session-result.js'
import type { Correlation } from '../src/host/session-correlation.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'
import { harness, request, submitPayload } from './lifecycle-harness.js'
import { HubClient, TERMINAL_CALLBACK_BUDGET_MS } from '../src/host/hub-client.js'

const correlation: Correlation = {
  runId: 'isolated-catalog-run', requestHash: 'a'.repeat(64), sessionId: 'isolated-dsh', requestId: 'isolated-req', generation: 1,
  modelStepTimeoutMs: 600000, modelStepTimeoutSource: 'submission', modelStepTimeoutTriggered: false,
  state: 'running', lastSeq: 0, errorCode: null, acceptedAt: null, terminalAt: null,
}
const now = Date.now()
function project(message: string) {
  const events: SessionEventLike[] = [
    { seq: 0, time: now, type: 'user/message', data: { source: { kind: 'user', rpcId: 'isolated-req' } } },
    { seq: 1, time: now + 1, type: 'turn/start', data: { turn: 1 } },
    { seq: 2, time: now + 2, type: 'turn/end', data: { turn: 1, reason: { kind: 'error', error: { code: 'UNKNOWN', message } } } },
  ]
  return projectInspection(correlation, { meta: { id: 'isolated-dsh', createdAt: now }, events }, { commandLifecycle: true })
}

it('retains exact safe catalog status in authenticated terminal completion with original trace identity', () => {
  const value = project('research_catalog_http_409')
  expect(value.status.state).toBe('failed')
  expect(value.status.error_code).toBe('dsh_turn_error')
  expect(value.completion).toMatchObject({
    run_id: correlation.runId, dsh_session_id: correlation.sessionId, generation: 1, last_seq: 2,
    terminal_status: 'failed', result_ref: null, result_hash: null,
    error: { code: 'dsh_turn_error', message: 'Research capability catalog rejected (HTTP 409)', retryable: false },
  })
  expect(value.result).toBeNull()
})

it('retains catalog fetch timeout phase and actual bounded deadline', () => {
  const value = project('research_catalog_transport_timeout phase=fetch deadline_ms=30000')
  expect(value.status).toMatchObject({ state: 'failed', error_code: 'research_catalog_transport_timeout' })
  expect(value.completion).toMatchObject({
    terminal_status: 'failed', result_ref: null, result_hash: null,
    error: {
      code: 'research_catalog_transport_timeout',
      message: 'Research capability catalog failed (research_catalog_transport_timeout; phase=fetch; deadline_ms=30000)',
      retryable: true,
    },
  })
  expect(value.result).toBeNull()
})

it('sends the authenticated catalog terminal directly from turn/end exactly once', async () => {
  const app = harness({ callbackAttempts: 1 })
  const payload = submitPayload()
  await app.call(request('PUT', `/decision-hub/v1/runs/${payload.run_id}`, payload))
  const inspection = app.sessions.inspections.get(payload.deterministic_session_id)!
  const turnEnd: SessionEventLike = {
    type: 'turn/end', seq: 2, time: Date.now(),
    data: { reason: { kind: 'error', error: {
      code: 'UNKNOWN',
      message: 'research_catalog_transport_timeout phase=fetch deadline_ms=30000',
    } } },
  }
  inspection.events.push(turnEnd)

  await app.emit('session/event', { id: payload.deterministic_session_id }, turnEnd)
  await vi.waitFor(() => expect(app.state.terminalBodies).toHaveLength(1))

  expect(app.state.terminalBodies[0]).toMatchObject({
    run_id: payload.run_id, dsh_session_id: payload.deterministic_session_id,
    generation: 1, last_seq: 2, terminal_status: 'failed',
    result_ref: null, result_hash: null,
    error: {
      code: 'research_catalog_transport_timeout',
      message: 'Research capability catalog failed (research_catalog_transport_timeout; phase=fetch; deadline_ms=30000)',
      retryable: true,
    },
  })
  expect(inspection.events.filter(event => event.type.startsWith('tool/'))).toHaveLength(0)
  expect(inspection.events.filter(event => event.type.startsWith('step/'))).toHaveLength(0)
  expect(app.sessions.prompt).toHaveBeenCalledTimes(1)

  await app.emit('session/event', { id: payload.deterministic_session_id }, turnEnd)
  await Promise.resolve()
  expect(app.state.terminalBodies).toHaveLength(1)
})

it('shares one fifteen-second deadline across all terminal transport attempts', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(now)
  try {
    const fetchImpl = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      return await new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
      })
    }) as unknown as typeof fetch
    const client = new HubClient({ baseUrl: 'http://isolated.invalid', callbackKey: 'private-only',
      timeoutMs: 5_000, attempts: 3, ownerId: 'test', fetchImpl })
    const completion = project('research_catalog_transport_timeout phase=fetch deadline_ms=30000').completion!
    const pending = client.terminal(completion).then(() => 'unexpected success', error => error.code)

    await vi.advanceTimersByTimeAsync(TERMINAL_CALLBACK_BUDGET_MS - 1)
    expect(fetchImpl).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(1)

    expect(await pending).toBe('host_terminal_callback_deadline_elapsed')
    expect(fetchImpl).toHaveBeenCalledTimes(3)
  } finally {
    vi.useRealTimers()
  }
})

it('does not copy free text, URL, headers or marker suffix into terminal business receipt', () => {
  for (const text of ['research_catalog_http_409 token=fixture-secret',
    'research_catalog_http_409\nAuthorization: fixture-secret',
    'research_catalog_transport_timeout phase=fetch deadline_ms=30001 fixture-secret',
    'https://example.test/?token=fixture-secret', 'research_catalog_http_200']) {
    const value = project(text)
    expect(value.completion?.error?.message).toBe('DSH turn failed (UNKNOWN)')
    expect(JSON.stringify(value.completion)).not.toContain('fixture-secret')
  }
})
