import { afterEach, expect, it, vi } from 'vitest'
import { api } from './client'

afterEach(() => vi.restoreAllMocks())

const receipt = {
  decision: {
    decision_id: 'decision-original', request_id: 'evolution-decision:original',
    domain_pack_ref: 'crypto_macro', candidate_id: 'candidate-original', owner: 'owner',
    decision: 'reject', reason: '保留当前方法', evaluation_refs: [], previous_candidate_id: null,
    resulting_candidate_id: null, resulting_generation: 0, created_at: '2026-10-01T01:00:00Z',
  },
  active_pointer: null,
}

it('reads an exact historical request with owner context without posting a new decision', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(receipt)))
  expect(await api.promotionDecisionReceipt('crypto_macro', 'evolution-decision:original')).toEqual(receipt)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, init] = fetchMock.mock.calls[0]
  expect(url).toBe('/v1/evolution/crypto_macro/decisions/evolution-decision%3Aoriginal')
  expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  expect(init?.method).toBeUndefined()
  expect(init?.body).toBeUndefined()
})

it('does not turn a missing or malformed receipt into an acknowledgment', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'promotion_decision_not_found' }), { status: 404 }))
    .mockResolvedValueOnce(new Response('{}'))
  await expect(api.promotionDecisionReceipt('crypto_macro', 'missing')).rejects.toThrow('promotion_decision_not_found')
  await expect(api.promotionDecisionReceipt('crypto_macro', 'malformed')).rejects.toThrow()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
