// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import App from '../App'
import * as clientModule from '../api/client'
import type { EvolutionOverview, PromotionDecisionCommand } from '../api/types'
import { listPendingRequests } from '../research/pendingRequests'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('does not POST automatically with mutation retry defaults and preserves a verified reject across immediate route return', async () => {
  const { api } = clientModule
  const overview: EvolutionOverview = {
    datasets: [], experiments: [], results: [], failures: [], experiences: [], decisions: [], pointers: [],
    candidates: [{ candidate_id: 'one', execution_scope: 'directional_synthesis',
      candidate_type: 'strategy', content_hash: 'a'.repeat(64), version: 'one.v1',
      parent_version: null, status: 'candidate', created_at: '2026-10-01T00:00:00Z', content_ref: null, source: 'owner' }],
  }
  const reads = vi.spyOn(api, 'evolutionOverview').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'evolutionJobs').mockResolvedValue([])
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'researchMemos').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(clientModule, 'request').mockImplementation(() => new Promise<never>(() => undefined))
  let committed: PromotionDecisionCommand | undefined
  const send = vi.spyOn(api, 'promotionDecision').mockImplementation(async command => {
    committed = command
    throw new Error('API 503')
  })
  const getReceipt = vi.spyOn(api, 'promotionDecisionReceipt').mockImplementation(async () => {
    if (!committed) throw new Error('promotion_decision_not_found')
    return { decision: { decision_id: 'promotion-one', domain_pack_ref: 'crypto_macro.v1',
      request_id: committed.request_id, candidate_id: committed.candidate_id, owner: committed.owner,
      decision: committed.decision, reason: committed.reason, evaluation_refs: committed.evaluation_refs,
      previous_candidate_id: null, resulting_candidate_id: null, resulting_generation: 0,
      created_at: '2026-10-01T00:00:00Z' }, active_pointer: null }
  })
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 2, retryDelay: 1 } } })
  cache.setQueryData(['evolution-overview'], overview)
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const settle = async (predicate: () => boolean) => {
    for (let i = 0; i < 80 && !predicate(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(predicate()).toBe(true)
  }
  const click = async (label: string) => {
    const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === label)!
    expect(button).toBeTruthy()
    await act(async () => button.click())
  }
  const route = async (path: string) => {
    await act(async () => { window.history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')) })
  }
  sessionStorage.clear(); window.history.replaceState(null, '', '#/Evolution')
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><App /></QueryClientProvider>))
    await settle(() => Boolean(container.querySelector('[aria-label="拒绝 one.v1"]')))
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="拒绝 one.v1"]')!.click())
    await act(async () => {
      const textarea = container.querySelector('textarea')!
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '核对后拒绝')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
      container.querySelector<HTMLInputElement>('.confirm-row input')!.click()
    })
    await click('确认拒绝')
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(send).toHaveBeenCalledOnce()
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    await click('关闭并保留原决定'); await click('核对回执')
    expect(getReceipt).toHaveBeenCalledOnce()
    expect(listPendingRequests('evolution-decision')).toHaveLength(0)
    const saved = cache.getQueryData<EvolutionOverview>(['evolution-overview'])!
    expect(saved.candidates[0].status).toBe('rejected')
    expect(saved.decisions[0].request_id).toBe(committed!.request_id)
    expect(saved.pointers).toEqual([])
    await route('#/management')
    await settle(() => !container.textContent?.includes('候选、评测与晋级'))
    await route('#/Evolution')
    await settle(() => Boolean(container.textContent?.includes('候选、评测与晋级')))
    expect(container.querySelector('[aria-label="拒绝 one.v1"]')).toBeNull()
    expect(send).toHaveBeenCalledOnce()
    expect(reads).toHaveBeenCalled()
  } finally {
    await act(async () => root.unmount()); cache.clear(); container.remove(); vi.restoreAllMocks()
    sessionStorage.clear(); window.history.replaceState(null, '', '#/home')
  }
})
