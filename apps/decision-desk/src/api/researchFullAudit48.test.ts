import { readFileSync } from 'node:fs'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi } from './researchQuality'
afterEach(() => vi.restoreAllMocks())
const original = () => JSON.parse(readFileSync(new URL('../../../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json', import.meta.url), 'utf8'))
const headers = (mode: string, comparison: string, version: string) => ({ 'X-Decision-Hub-Report-Read-Mode': mode,
  'X-Decision-Hub-Report-State-Version': version, 'X-Decision-Hub-Report-Audit-Comparison': comparison })
it('posts one explicit audit and retains the strict same-run original and exact audit scope', async () => {
  const body = original(); const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), {
    headers: headers('full_original_audit', 'saved_bundle_full_quality', '7'),
  }))
  const result = await researchQualityApi.audit(body.run_id)
  expect(result).toEqual({ view: body, comparison: 'saved_bundle_full_quality', state_version: 7, checked_at: body.as_of })
  expect(fetch).toHaveBeenCalledOnce()
  expect(fetch.mock.calls[0][0]).toBe(`/v1/research/runs/${body.run_id}/report/audit`)
  expect(fetch.mock.calls[0][1]?.method).toBe('POST'); expect(fetch.mock.calls[0][1]?.body).toBeUndefined()
})
it.each([['confirmed_bundle', 'saved_bundle_full_quality', '7'], ['full_original_audit', 'legacy_original_only', '7']])('rejects incompatible audit headers %s/%s/%s', async (mode, comparison, version) => {
  const body = original(); vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { headers: headers(mode, comparison, version) }))
  await expect(researchQualityApi.audit(body.run_id)).rejects.toMatchObject({ kind: 'verification' })
})
