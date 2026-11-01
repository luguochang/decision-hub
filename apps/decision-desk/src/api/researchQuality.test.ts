import { readFileSync } from 'node:fs'
import { afterEach, expect, it, vi } from 'vitest'
import { QUALITY_REQUEST_TIMEOUT_MS, researchQualityApi } from './researchQuality'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

const original = (asset: 'eth' | 'btc') => JSON.parse(readFileSync(new URL(
  `../../../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/${asset}-quality-raw.json`, import.meta.url,
), 'utf8'))

it.each(['eth', 'btc'] as const)('reads the actual saved old04 %s quality response without promoting its decision', async asset => {
  const fixture = original(asset)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(fixture)))
  const value = await researchQualityApi.read(fixture.run_id)
  expect(value).toEqual(fixture)
  expect(value.decision?.context_status).toBe('incomplete')
})

it('gives quality reads 45 seconds and reports timeout without claiming failed verification', async () => {
  vi.useFakeTimers()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  }))
  expect(QUALITY_REQUEST_TIMEOUT_MS).toBe(45_000)
  const pending = researchQualityApi.read('old')
  const outcome = expect(pending).rejects.toMatchObject({ kind: 'timeout', message: expect.stringContaining('尚未确认核验结果') })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(15_000)
  await outcome
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('supports an independent explicit quality timeout without retrying', async () => {
  vi.useFakeTimers()
  vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  }))
  const pending = researchQualityApi.read('old', undefined, { timeoutMs: 60_000 })
  const outcome = expect(pending).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(45_000)
  expect(vi.getTimerCount()).toBe(1)
  await vi.advanceTimersByTimeAsync(15_000)
  await outcome
})

it('keeps network interruption distinct from verification and hides transport details', async () => {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('secret connection detail'))
  await expect(researchQualityApi.read('old')).rejects.toMatchObject({ kind: 'network', message: '连接中断，尚未确认质量结果。请检查连接后重新读取。' })
})

it('rejects a wrong run and malformed original safely without exposing raw issues', async () => {
  const fixture = original('eth')
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(fixture)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ secret_field: 'do not display' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...fixture, decision: { ...fixture.decision, full_report_json: '{invalid' } })))
  for (const runId of ['wrong-run', 'old', fixture.run_id]) {
    await expect(researchQualityApi.read(runId)).rejects.toMatchObject({ kind: 'verification', message: '质量原件的格式或身份暂未通过核验，当前无法确认结论资格。' })
  }
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('reports an explicit server verification refusal safely and treats unavailability separately', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'internal_hash_secret' }), { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'internal_storage_secret' }), { status: 503 }))
  await expect(researchQualityApi.read('old')).rejects.toMatchObject({ kind: 'verification' })
  await expect(researchQualityApi.read('old')).rejects.toMatchObject({ kind: 'unavailable', message: expect.not.stringContaining('secret') })
})
