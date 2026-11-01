// Isolated transport/clock controls: no model, network, SQL or archived Run mutation.
import { afterEach, expect, it, vi } from 'vitest'
import { HubClient, managedReadBudgetMs, managedPreStepBudgetMs, managedContextReadBudgetMs } from '../src/host/hub-client.js'
import { projectInspection } from '../src/host/session-result.js'
import type { Correlation } from '../src/host/session-correlation.js'

const now = Date.parse('2026-10-09T14:30:00Z')
const deadline = (ms: number) => new Date(now + ms).toISOString()
const prompt = { schema_version: 'dsh-session-prompt.v1', run_id: 'run_control',
  dsh_session_id: 'dsh_control', request_id: 'req_control', request_hash: 'a'.repeat(64), generation: 1, prompt: 'fixture' }
const client = (fetchImpl: typeof fetch, ownedDeadline?: string) => new HubClient({ baseUrl: 'http://hub.test',
  callbackKey: 'synthetic', ownerId: 'control', timeoutMs: 5000, attempts: 3, fetchImpl,
  ...(ownedDeadline ? { managedReadDeadline: () => ownedDeadline } : {}) })
const freeze = () => { vi.useFakeTimers(); vi.setSystemTime(now) }
afterEach(() => vi.useRealTimers())

it('read phase caps at60s; total pre-step follows model600s and shorter Run remaining; legacy stays30s', () => {
  freeze()
  expect(managedReadBudgetMs(deadline(1800000))).toBe(60000)
  expect(managedReadBudgetMs(deadline(7000))).toBe(7000)
  expect(managedPreStepBudgetMs(600000, deadline(1800000))).toBe(600000)
  expect(managedPreStepBudgetMs(600000, deadline(20000))).toBe(20000)
  expect(managedPreStepBudgetMs(600000)).toBe(30000)
  expect(() => managedReadBudgetMs(deadline(0))).toThrow('dsh_host_read_deadline_elapsed')
})

it('native context phase no longer expires at30s; explicit outer and Run remaining still bound it', async () => {
  freeze()
  expect(managedContextReadBudgetMs(deadline(1800000), 600000)).toBe(60000)
  expect(managedContextReadBudgetMs(deadline(40000), 35000)).toBe(35000)
  expect(managedContextReadBudgetMs(undefined, 600000)).toBe(30000)
  expect(() => managedContextReadBudgetMs(deadline(1800000), 0)).toThrow('dsh_host_read_deadline_elapsed')
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => await new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('fixture', 'AbortError')), { once: true })
  }))
  const pending = client(fetchImpl).researchDetail('run_control', undefined, deadline(35000))
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_host_read_transport_timeout', retryable: false })
  await vi.advanceTimersByTimeAsync(30000)
  expect(fetchImpl.mock.calls[0]![1]?.signal?.aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(5000)
  await assertion
  expect(fetchImpl).toHaveBeenCalledOnce()
})

it('legacy prompt succeeds after former5s boundary with one GET and timer cleanup', async () => {
  freeze()
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(() => new Promise(resolve => {
    setTimeout(() => resolve(Response.json(prompt)), 13000)
  }))
  const pending = client(fetchImpl).prompt('run_control', 1)
  await vi.advanceTimersByTimeAsync(13000)
  await expect(pending).resolves.toEqual(prompt)
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(fetchImpl.mock.calls[0]![1]?.method).toBe('GET')
  expect(vi.getTimerCount()).toBe(0)
})

it.each(['prompt', 'link'] as const)('owned remaining budget ends %s once with no read retry', async phase => {
  freeze()
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => await new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('fixture', 'AbortError')), { once: true })
  }))
  const c = client(fetchImpl, deadline(7000))
  const pending = phase === 'prompt' ? c.prompt('run_control', 1) : c.linkBySession('dsh_control')
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_host_read_transport_timeout', retryable: false })
  await vi.advanceTimersByTimeAsync(7000)
  await assertion
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(vi.getTimerCount()).toBe(0)
})

it('expired managed Run performs zero fetch; caller cancellation remains separate', async () => {
  freeze()
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
    init?.signal?.throwIfAborted(); return Response.json(prompt)
  })
  await expect(client(fetchImpl, deadline(0)).prompt('run_control', 1)).rejects.toMatchObject({ code: 'dsh_host_read_deadline_elapsed' })
  expect(fetchImpl).not.toHaveBeenCalled()
  const controller = new AbortController(); controller.abort()
  await expect(client(fetchImpl).prompt('run_control', 1, controller.signal)).rejects.toMatchObject({ code: 'dsh_host_read_cancelled' })
  expect(fetchImpl).not.toHaveBeenCalled()
})

it('managed read deadline includes response body and maps timeout without a second GET', async () => {
  freeze()
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => ({ ok: true,
    json: async () => await new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('fixture body', 'AbortError')), { once: true })
    }),
  } as unknown as Response))
  const pending = client(fetchImpl, deadline(7000)).prompt('run_control', 1)
  const assertion = expect(pending).rejects.toMatchObject({ code: 'dsh_host_read_transport_timeout', retryable: false })
  await vi.advanceTimersByTimeAsync(7000)
  await assertion
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(vi.getTimerCount()).toBe(0)
})

it('health retains5s and one attempt; budget extension touches no write callback', async () => {
  freeze()
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => await new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('fixture', 'AbortError')), { once: true })
  }))
  const pending = client(fetchImpl).readiness()
  await vi.advanceTimersByTimeAsync(5000)
  await expect(pending).resolves.toBe(false)
  expect(fetchImpl).toHaveBeenCalledOnce()
  expect(vi.getTimerCount()).toBe(0)
})

it('future exact managed-read markers preserve error; old generic and free text retain prior terminal mapping', () => {
  const correlation: Correlation = { runId: 'run_control', requestHash: 'a'.repeat(64), sessionId: 'dsh_control',
    requestId: 'req_control', generation: 1, modelStepTimeoutMs: 600000, modelStepTimeoutSource: 'submission',
    modelStepTimeoutTriggered: false, state: 'running', lastSeq: 0, errorCode: null, acceptedAt: null, terminalAt: null }
  const project = (message: string) => projectInspection(correlation, { meta: { id: 'dsh_control' }, events: [
    { seq: 0, time: now, type: 'user/message', data: { source: { kind: 'user', rpcId: 'req_control' } } },
    { seq: 1, time: now + 1, type: 'turn/end', data: { reason: { kind: 'error', error: { code: 'UNKNOWN', message } } } },
  ] }, { commandLifecycle: true })
  expect(project('dsh_host_read_transport_timeout').completion?.error?.code).toBe('dsh_host_read_transport_timeout')
  for (const message of ['host_callback_unreachable: This operation was aborted',
    'dsh_host_read_transport_timeout token=private', 'Request timed out.']) {
    const value = project(message)
    expect(value.completion?.error?.code).toBe('dsh_turn_error')
    expect(JSON.stringify(value.completion)).not.toContain('private')
  }
})
