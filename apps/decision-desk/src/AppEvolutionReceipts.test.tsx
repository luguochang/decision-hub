// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api/client'
import type { EvolutionOverview } from './api/types'
import { freezeEvolutionDecision, originalEvolutionCommand } from './product/evolutionDecisionReceipts'

vi.mock('./api/client', () => {
  const pending = () => new Promise(() => undefined)
  return { api: { summary: pending, ledgerRunsPage: pending, runInspector: pending, health: pending,
    researchMemos: pending, evolutionOverview: pending, evolutionJobs: async () => [], operations: pending,
    promotionDecision: pending, promotionDecisionReceipt: pending, promotionReview: pending } }
})
vi.mock('./product/EvolutionReadinessPanel', () => ({ EvolutionReadinessPanel: () => null }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const candidate: EvolutionOverview['candidates'][number] = {
  candidate_id: 'candidate-original', execution_scope: 'directional_synthesis', candidate_type: 'strategy',
  content_hash: 'a'.repeat(64), version: 'original.v1', parent_version: null, status: 'candidate',
  created_at: '2026-10-01T01:00:00Z', content_ref: null, source: 'owner',
}
const overview: EvolutionOverview = {
  datasets: [], candidates: [candidate, { ...candidate, candidate_id: 'candidate-other', version: 'other.v1', content_hash: 'b'.repeat(64) }],
  experiments: [], results: [], pointers: [], failures: [], experiences: [], decisions: [],
}
let root: Root | undefined, container: HTMLDivElement | undefined, client: QueryClient | undefined
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks(); sessionStorage.clear()
  window.history.replaceState(null, '', '#/home')
})

async function settle(predicate: () => boolean) {
  for (let count = 0; count < 50 && !predicate(); count++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(predicate()).toBe(true)
}
async function mount() {
  window.history.replaceState(null, '', '#/Evolution')
  if (!container) { container = document.createElement('div'); document.body.append(container) }
  root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={client!}><App /></QueryClientProvider>))
  await settle(() => Boolean(container!.querySelector('.evolution-panel-body')))
}
function button(text: string) {
  const value = [...container!.querySelectorAll('button')].find(item => item.textContent === text)
  expect(value).toBeTruthy()
  return value!
}

it('never automatically reposts a failed decision when the app client enables mutation retries', async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 2, retryDelay: 0 } } })
  client.setQueryData(['evolution-overview'], overview)
  const post = vi.spyOn(api, 'promotionDecision').mockRejectedValue(new Error('pc_probe_response_withheld_after_commit'))
  await mount()
  await act(async () => container!.querySelector<HTMLButtonElement>('[aria-label="拒绝 original.v1"]')!.click())
  await act(async () => {
    const field = container!.querySelector<HTMLTextAreaElement>('[role="dialog"] textarea')!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, '保留当前方法')
    field.dispatchEvent(new Event('input', { bubbles: true }))
    container!.querySelector<HTMLInputElement>('[role="dialog"] input')!.click()
  })
  await act(async () => button('确认拒绝').click())
  await settle(() => Boolean(container!.textContent?.includes('决定结果尚未确认')))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  expect(post).toHaveBeenCalledTimes(1)
  expect(container!.textContent).not.toContain('操作未执行')
})

it('keeps a confirmed rejected candidate closed across remount while the server refresh is still pending', async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['evolution-overview'], overview)
  const frozen = freezeEvolutionDecision({ domain_pack_ref: 'crypto_macro.v1', candidate_version: candidate.version,
    command: { candidate_id: candidate.candidate_id, owner: 'owner', decision: 'reject', reason: '保留当前方法', evaluation_refs: [], expected_generation: 0 } })
  const command = originalEvolutionCommand(frozen)
  const receipt = { decision: { decision_id: 'decision-original', request_id: command.request_id,
    domain_pack_ref: 'crypto_macro.v1', candidate_id: command.candidate_id, owner: command.owner,
    decision: 'reject' as const, reason: command.reason, evaluation_refs: [], previous_candidate_id: null,
    resulting_candidate_id: null, resulting_generation: 0, created_at: '2026-10-01T01:01:00Z' }, active_pointer: null }
  const post = vi.spyOn(api, 'promotionDecision')
  vi.spyOn(api, 'promotionDecisionReceipt').mockResolvedValue(receipt)
  await mount()
  expect(post).not.toHaveBeenCalled()
  await act(async () => button('核对回执').click())
  await settle(() => Boolean(container!.textContent?.includes('拒绝决定已确认')))
  const cached = client.getQueryData<EvolutionOverview>(['evolution-overview'])!
  expect(cached.candidates[0].status).toBe('rejected')
  expect(cached.candidates[1]).toEqual(overview.candidates[1])
  expect(cached.pointers).toEqual(overview.pointers)
  expect(cached.decisions).toEqual([receipt.decision])
  await act(async () => root!.unmount())
  root = undefined
  await mount()
  expect(container!.querySelector('[aria-label="拒绝 original.v1"]')).toBeNull()
  expect(container!.querySelector<HTMLButtonElement>('[aria-label="拒绝 other.v1"]')?.disabled).toBe(false)
  expect(post).not.toHaveBeenCalled()
})
