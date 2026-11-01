import { expect, it, vi } from 'vitest'
import { api } from './client'

it('requests the bounded recent preview separately from priority/cursor history', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }))
  try {
    expect(await api.researchRecentRuns()).toEqual([])
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/research/recent-runs?limit=8')
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
  } finally { fetchMock.mockRestore() }
})

it('rejects malformed recent rows through the original run-view schema', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[{"run_id":"incomplete"}]', { status: 200 }))
  try { await expect(api.researchRecentRuns()).rejects.toThrow() }
  finally { fetchMock.mockRestore() }
})
