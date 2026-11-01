import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, ResearchReportServingError } from './researchQuality'

afterEach(() => vi.restoreAllMocks())
const pending = (state = 'queued', runId = 'private-run') => ({
  run_id: runId, state, state_version: 2, confirmed_at: state === 'confirmed' ? '2026-10-10T00:00:00Z' : null, reason_code: null,
})
const refusal = () => new Response(JSON.stringify({ detail: 'research_quality_bundle_not_confirmed' }), { status: 409 })

it.each(['queued', 'prepared', 'unconfirmed', 'cancelled', 'revoked', 'confirmed'])('reads %s metadata after the exact pending response using only GETs', async state => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(refusal()).mockResolvedValueOnce(new Response(JSON.stringify(pending(state))))
  await expect(researchQualityApi.read('private-run')).rejects.toBeInstanceOf(ResearchReportServingError)
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/v1/research/runs/private-run/quality', '/v1/research/runs/private-run/report/status'])
  for (const [, init] of fetch.mock.calls) {
    expect(init?.method ?? 'GET').toBe('GET')
    expect(init?.body).toBeUndefined()
    expect(new Headers(init?.headers).has('X-Owner-Id')).toBe(false)
  }
})

it('rejects a different run status and preserves real verification refusal', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(refusal()).mockResolvedValueOnce(new Response(JSON.stringify(pending('queued', 'another-run'))))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'research_quality_original_verification_failed' }), { status: 409 }))
  await expect(researchQualityApi.read('private-run')).rejects.toMatchObject({ kind: 'verification' })
  await expect(researchQualityApi.read('private-run')).rejects.toMatchObject({ kind: 'verification' })
  expect(fetch).toHaveBeenCalledTimes(3)
})

it('never trusts an unknown shape or downgraded legacy state after pending', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(refusal()).mockResolvedValueOnce(new Response(JSON.stringify({ ...pending(), state: 'mystery' })))
    .mockResolvedValueOnce(refusal()).mockResolvedValueOnce(new Response(JSON.stringify({ ...pending('legacy_full_original'), state_version: 0 })))
  await expect(researchQualityApi.read('private-run')).rejects.toMatchObject({ kind: 'verification' })
  await expect(researchQualityApi.read('private-run')).rejects.toMatchObject({ kind: 'verification' })
  expect(fetch).toHaveBeenCalledTimes(4)
})
