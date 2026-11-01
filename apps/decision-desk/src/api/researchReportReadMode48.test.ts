import { readFileSync } from 'node:fs'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi } from './researchQuality'

afterEach(() => vi.restoreAllMocks())
const original = () => JSON.parse(readFileSync(new URL(
  '../../../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json', import.meta.url,
), 'utf8'))
const headers = (mode: string, version: string) => ({
  'X-Decision-Hub-Report-Read-Mode': mode, 'X-Decision-Hub-Report-State-Version': version,
})

it.each([['confirmed_bundle', '7'], ['legacy_full_original', '0']] as const)('binds successful %s response metadata to the strict same-run original', async (mode, version) => {
  const body = original(); const metadata = vi.fn()
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { headers: headers(mode, version) }))
  expect(await researchQualityApi.read(body.run_id, undefined, { onServingRead: metadata })).toEqual(body)
  expect(metadata).toHaveBeenCalledOnce()
  expect(metadata).toHaveBeenCalledWith({ run_id: body.run_id, read_mode: mode, state_version: Number(version), checked_at: body.as_of })
  expect(fetch).toHaveBeenCalledOnce()
})

it.each([['mystery', '7'], ['confirmed_bundle', '0'], ['legacy_full_original', '7']])('rejects an incompatible successful header %s/%s without claiming verified metadata', async (mode, version) => {
  const body = original(); const metadata = vi.fn()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { headers: headers(mode, version) }))
  await expect(researchQualityApi.read(body.run_id, undefined, { onServingRead: metadata })).rejects.toMatchObject({ kind: 'verification' })
  expect(metadata).not.toHaveBeenCalled()
})

it('marks absent read headers unknown while preserving the old strict DTO', async () => {
  const body = original(); const metadata = vi.fn()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)))
  expect(await researchQualityApi.read(body.run_id, undefined, { onServingRead: metadata })).toEqual(body)
  expect(metadata).toHaveBeenCalledWith({ run_id: body.run_id, read_mode: 'unknown', state_version: null, checked_at: body.as_of })
})
