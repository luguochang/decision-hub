import { createServer, type RequestListener, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, expect, it, vi } from 'vitest'
import { baselinePost, baselineSafeCause } from '../src/host/baseline-transport.js'

const servers: Server[] = []
afterEach(async () => {
  vi.useRealTimers()
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => {
    server.closeAllConnections(); server.close(() => resolve())
  })))
})
async function serve(handler: RequestListener) {
  const server = createServer(handler)
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  return new URL(`http://127.0.0.1:${(server.address() as AddressInfo).port}/baseline`)
}
function options(url: URL, controller = new AbortController()) {
  return { url, authKey: 'test-only-bridge-key', body: '{"run_id":"test-only"}',
    signal: controller.signal, deadlineAtMs: Date.now() + 600_000 }
}

it('keeps one POST alive beyond virtual 300s headers wait, under the original 600s budget', async () => {
  let release!: () => void, seen!: () => void, calls = 0
  const arrived = new Promise<void>(resolve => { seen = resolve })
  const url = await serve((request, response) => {
    calls++
    expect(request.method).toBe('POST')
    expect(request.headers['x-decision-hub-bridge-key']).toBe('test-only-bridge-key')
    release = () => response.end('{"ok":true}')
    request.resume(); request.once('end', seen)
  })
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  let settled = false
  const pending = baselinePost(options(url)).finally(() => { settled = true })
  await arrived
  await vi.advanceTimersByTimeAsync(300_001)
  expect(settled).toBe(false)
  release()
  await expect(pending).resolves.toBe('{"ok":true}')
  expect(calls).toBe(1)
})

it('uses the same absolute deadline through a stalled response body; no renewed header/body budget', async () => {
  let seen!: () => void
  const arrived = new Promise<void>(resolve => { seen = resolve })
  const url = await serve((_request, response) => { response.write('partial'); seen() })
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const pending = baselinePost({ ...options(url), deadlineAtMs: Date.now() + 600_000 })
  const rejected = expect(pending).rejects.toMatchObject({ code: 'research_baseline_transport_timeout' })
  await arrived
  await vi.advanceTimersByTimeAsync(600_000)
  await rejected
})

it.each(['headers', 'body'] as const)('parent cancellation closes the real %s request without retry', async phase => {
  let seen!: () => void, closed!: () => void, calls = 0
  const arrived = new Promise<void>(resolve => { seen = resolve })
  const socketClosed = new Promise<void>(resolve => { closed = resolve })
  const url = await serve((request, response) => {
    calls++; request.socket.once('close', closed)
    if (phase === 'body') response.write('partial')
    seen()
  })
  const controller = new AbortController()
  const pending = baselinePost(options(url, controller))
  const rejected = expect(pending).rejects.toMatchObject({ code: 'research_baseline_cancelled', cause: undefined })
  await arrived
  controller.abort(new Error('must-not-retain-private-cancellation-detail'))
  await rejected
  await socketClosed
  expect(calls).toBe(1)
})

it('rejects redirects without forwarding auth and bounds streamed bytes at 1MB', async () => {
  let targetCalls = 0
  const target = await serve((_request, response) => { targetCalls++; response.end('wrong') })
  const redirect = await serve((_request, response) => { response.writeHead(307, { location: String(target) }); response.end() })
  await expect(baselinePost(options(redirect))).rejects.toMatchObject({ code: 'research_baseline_http_refused' })
  expect(targetCalls).toBe(0)
  const huge = await serve((_request, response) => { response.end(Buffer.alloc(1_000_001, 65)) })
  await expect(baselinePost(options(huge))).rejects.toMatchObject({ code: 'research_baseline_oversize' })
})

it('preserves only allowlisted machine causes and never turns unknown network failure into timeout', async () => {
  const url = await serve(request => { request.socket.destroy() })
  const error = await baselinePost(options(url)).catch((value: unknown) => value)
  expect(error).toMatchObject({ code: 'research_baseline_transport_unavailable', cause: { code: 'ECONNRESET' } })
  expect(baselineSafeCause({ message: 'https://private-token.test', cause: { code: 'UND_ERR_HEADERS_TIMEOUT' } }))
    .toEqual({ code: 'UND_ERR_HEADERS_TIMEOUT' })
  expect(baselineSafeCause({ code: 'PRIVATE_SECRET', message: 'private-body' })).toBeUndefined()
  expect(JSON.stringify(error)).not.toContain('private')
})

it('classifies an expired original deadline as timeout even when the parent signal is already aborted', async () => {
  const controller = new AbortController()
  controller.abort(new Error('private-reason'))
  await expect(baselinePost({ ...options(new URL('http://127.0.0.1:1/baseline'), controller),
    deadlineAtMs: Date.now() - 1 })).rejects.toMatchObject({ code: 'research_baseline_transport_timeout' })
})
