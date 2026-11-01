import { afterEach, expect, it, vi } from 'vitest'
import { API_REQUEST_TIMEOUT_MS, api, request, requestResponse } from './client'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

it('ends an unresponsive request and preserves headers without retrying a mutation', async () => {
  vi.useFakeTimers()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  }))
  const pending = request('/v1/test', { method: 'POST', headers: { 'Idempotency-Key': 'fixed-key' } })
  const outcome = expect(pending).rejects.toThrow('请求超时，尚未确认结果')
  await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
  await outcome
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const headers = new Headers(fetchMock.mock.calls[0][1]!.headers)
  expect(headers.get('Idempotency-Key')).toBe('fixed-key')
  expect(headers.get('Accept')).toBe('application/json')
  expect(vi.getTimerCount()).toBe(0)
})

it('also bounds a response body which stalls after successful headers', async () => {
  vi.useFakeTimers()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => ({
    ok: true,
    json: () => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    }),
  }) as Response)
  const outcome = expect(request('/v1/test')).rejects.toThrow('请求超时')
  await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
  await outcome
  expect(vi.getTimerCount()).toBe(0)
})

it('preserves caller cancellation and clears the request timer', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason))
  }))
  const outcome = expect(request('/v1/test', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort()
  await outcome
  expect(vi.getTimerCount()).toBe(0)
})

it('lets a slow read use its own timeout while the ordinary default remains unchanged', async () => {
  vi.useFakeTimers()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  }))
  const pending = requestResponse('/v1/slow-read', response => response.json(), undefined, undefined, { timeoutMs: 45_000 })
  const outcome = expect(pending).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
  expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(15_000)
  await outcome
  expect(API_REQUEST_TIMEOUT_MS).toBe(30_000)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('uses a longer bounded timeout for uploads', async () => {
  vi.useFakeTimers()
  let aborted = false
  vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => { aborted = true; reject(new DOMException('aborted', 'AbortError')) })
  }))
  const outcome = expect(request('/v1/upload', { method: 'POST', body: new FormData() })).rejects.toThrow('请求超时')
  await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
  expect(aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(90_000)
  await outcome
})

it('keeps an explicit submission identity across the old quarter-hour boundary', async () => {
  vi.useFakeTimers()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ schema_version: 'research-run-queued.v1', event_id: 'event-stable', run_id: 'run-stable', status: 'queued', status_url: '/v1/runs/run-stable' }), { status: 202 }))
  vi.setSystemTime(new Date('2026-09-30T06:14:59Z'))
  await api.submitResearch('BTC', 'stable-submission')
  vi.setSystemTime(new Date('2026-09-30T06:15:31Z'))
  await api.submitResearch('BTC', 'stable-submission')
  expect(fetchMock.mock.calls.map((call) => new Headers(call[1]!.headers).get('Idempotency-Key'))).toEqual(['stable-submission', 'stable-submission'])
})
