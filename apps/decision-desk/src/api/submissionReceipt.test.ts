// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'
import { acknowledgeRequest, listPendingRequests, pendingRequestWithId } from '../research/pendingRequests'

afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

const queued = {
  schema_version: 'research-run-queued.v1', event_id: 'event_queued', run_id: 'run_queued',
  status: 'queued', status_url: '/v1/runs/run_queued',
}
const libraryRequest: Parameters<typeof api.submitResearchWithLibrary>[0] = {
  schema_version: 'research-request.v1', research_family: 'crypto_macro',
  instrument_scope: ['BTC-USDT'], focus: 'receipt transport test',
  library_context_mode: 'none', allow_remote_library_content: false, selected_version_ids: [],
}
const accepted = (body: unknown) => new Response(JSON.stringify(body), { status: 202 })
const methods = [
  { name: 'latest advice', kind: 'research-submit' as const, payload: { text: 'receipt transport test' }, path: '/v1/research/observations', send: () => api.submitResearch('receipt transport test', 'submission:stable') },
  { name: 'library research', kind: 'library-research' as const, payload: libraryRequest, path: '/v1/research-library/research', send: () => api.submitResearchWithLibrary(libraryRequest, 'submission:stable') },
]

describe.each(methods)('$name admission receipt', ({ send, path, kind, payload }) => {
  it('returns only a canonical queued receipt for its own exact Run status path', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted(queued))
    expect(await send()).toEqual(queued)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe(path)
    const init = fetch.mock.calls[0][1]
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('submission:stable')
    if (path.includes('library')) {
      expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
      expect(JSON.parse(String(init?.body))).toEqual(libraryRequest)
    }
  })

  it.each([
    ['empty receipt', {}], ['null receipt', null], ['array receipt', [queued]],
    ['missing schema version', { ...queued, schema_version: undefined }],
    ['unknown schema version', { ...queued, schema_version: 'research-run-queued.v999' }],
    ['missing Run', { ...queued, run_id: undefined }],
    ['empty Run', { ...queued, run_id: '' }],
    ['missing event', { ...queued, event_id: undefined }],
    ['nonqueued status', { ...queued, status: 'completed' }],
    ['another Run status path', { ...queued, status_url: '/v1/runs/run_other' }],
    ['unrelated relative path', { ...queued, status_url: '/run_queued' }],
    ['absolute status URL', { ...queued, status_url: 'https://example.test/v1/runs/run_queued' }],
    ['query on status path', { ...queued, status_url: '/v1/runs/run_queued?run_id=run_other' }],
    ['fragment on status path', { ...queued, status_url: '/v1/runs/run_queued#run_other' }],
    ['trailing status slash', { ...queued, status_url: '/v1/runs/run_queued/' }],
    ['unexpected receipt field', { ...queued, request_id: 'unproven-key' }],
  ])('rejects 202 with %s before handing a receipt to acknowledgement/navigation', async (_name, body) => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted(body))
    const acknowledge = vi.fn()
    await expect(send().then(acknowledge)).rejects.toThrow()
    expect(acknowledge).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('preserves the original key and intent on an explicit retry after an invalid response', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(accepted({ ...queued, status_url: '/v1/runs/run_other' }))
      .mockResolvedValueOnce(accepted(queued))
    await expect(send()).rejects.toThrow()
    expect(await send()).toEqual(queued)
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[0][1]?.body).toBe(fetch.mock.calls[1][1]?.body)
    expect(fetch.mock.calls.map(call => new Headers(call[1]?.headers).get('Idempotency-Key'))).toEqual(['submission:stable', 'submission:stable'])
  })

  it.each(['run_id', 'event_id'] as const)('rejects all-whitespace %s without acknowledging the original pending intent', async field => {
    const saved = pendingRequestWithId(kind, payload, 'submission:stable')
    const body = { ...queued, [field]: ' \t', ...(field === 'run_id' ? { status_url: '/v1/runs/ \t' } : {}) }
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted(body))
    const acknowledge = vi.fn(receipt => { acknowledgeRequest(kind, saved.id); return receipt })
    await expect(send().then(acknowledge)).rejects.toThrow()
    expect(acknowledge).not.toHaveBeenCalled()
    expect(listPendingRequests(kind)).toEqual([saved])
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('preserves HTTP rejection and rejects a non-JSON successful body without another POST', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'owner_required' }), { status: 403 }))
      .mockResolvedValueOnce(new Response('<html>not a receipt</html>', { status: 202 }))
    await expect(send()).rejects.toThrow('owner_required')
    await expect(send()).rejects.toThrow()
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})

describe('legacy observation admission receipt', () => {
  const observation = { event_id: 'event_observation', run_id: 'run_observation', status_url: '/v1/runs/run_observation' }
  it('retains the existing three-field receipt and original request identity', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted(observation))
    expect(await api.submitObservation('original intent', 'observation:stable')).toEqual(observation)
    expect(fetch.mock.calls[0][0]).toBe('/v1/observations')
    expect(new Headers(fetch.mock.calls[0][1]?.headers).get('Idempotency-Key')).toBe('observation:stable')
  })
  it.each([
    ['another Run', '/v1/runs/run_other'], ['relative path', '/run_observation'],
    ['absolute path', 'https://example.test/v1/runs/run_observation'],
    ['query', '/v1/runs/run_observation?run_id=run_other'],
    ['fragment', '/v1/runs/run_observation#run_other'],
    ['trailing slash', '/v1/runs/run_observation/'],
  ])('rejects %s instead of returning an unrelated observation receipt', async (_name, status_url) => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted({ ...observation, status_url }))
    await expect(api.submitObservation('original intent', 'observation:stable')).rejects.toThrow()
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(['run_id', 'event_id'] as const)('rejects all-whitespace %s without clearing the original observation key', async field => {
    const saved = pendingRequestWithId('observation-submit', { text: 'original intent' }, 'observation:stable')
    const body = { ...observation, [field]: ' \t', ...(field === 'run_id' ? { status_url: '/v1/runs/ \t' } : {}) }
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(accepted(body))
    const acknowledge = vi.fn(receipt => { acknowledgeRequest('observation-submit', saved.id); return receipt })
    await expect(api.submitObservation('original intent', saved.id).then(acknowledge)).rejects.toThrow()
    expect(acknowledge).not.toHaveBeenCalled()
    expect(listPendingRequests('observation-submit')).toEqual([saved])
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
