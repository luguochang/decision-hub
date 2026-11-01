// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api/client'
import { createPendingCommand } from './research/commandRequests'
import { listPendingRequests } from './research/pendingRequests'
import type { ResearchRunCommandReceipt } from './api/types'

vi.mock('./api/client', () => {
  const pending = () => new Promise(() => undefined)
  return { apiUrl: (path: string) => path, api: {
    summary: pending, ledgerRunsPage: pending, runInspector: pending, health: pending,
    researchMemos: pending, evolutionOverview: pending, evolutionJobs: async () => [], operations: pending,
    researchRun: pending, researchCommand: pending, researchCommandReceipt: pending,
    researchRuns: async () => [], researchHome: pending, macroEvents: async () => [], researchRunScope: async () => null,
    researchInbox: pending, strategyCalibrations: async () => [], researchReviewPlans: async () => [],
    researchRunComparison: pending, researchObservability: pending, researchValueEvaluation: pending, researchForecastAudit: pending,
  } }
})
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined, container: HTMLDivElement | undefined, client: QueryClient | undefined
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks(); sessionStorage.clear()
  root = undefined; container = undefined; client = undefined
  window.history.replaceState(null, '', '/')
})
async function settle(predicate: () => boolean) {
  for (let count = 0; count < 80 && !predicate(); count++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  }
  expect(predicate()).toBe(true)
}
function button(text: string) {
  const found = [...container!.querySelectorAll('button')].find(item => item.textContent === text)
  expect(found).toBeTruthy()
  return found!
}

it('recovers a failed-page retry on the technical page without a second POST or automatic child navigation', async () => {
  const saved = createPendingCommand('original-run', 'owner', 'retry', '跨入口仍保留的原理由')
  const receipt: ResearchRunCommandReceipt = {
    schema_version: 'research-run-command-receipt.v1', request_id: saved.record.id,
    owner_id: 'owner', source_run_id: 'original-run', command: saved.intent.command,
    result: { schema_version: 'research-run-command-result.v1', request_id: saved.record.id,
      command: 'retry', source_run_id: 'original-run', target_run_id: 'child-run', status: 'accepted',
      created_at: '2026-10-01T02:00:00Z' },
  }
  const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
  const post = vi.spyOn(api, 'researchCommand').mockRejectedValue(new Error('controlled_response_lost_after_commit'))
  window.history.replaceState(null, '', '#/failed?run=original-run')
  client = new QueryClient({ defaultOptions: {
    queries: { retry: false, staleTime: Infinity }, mutations: { retry: 2, retryDelay: 0 },
  } })
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={client!}><App /></QueryClientProvider>))
  await settle(() => Boolean(container!.textContent?.includes('有一笔尚未确认的任务操作')))
  await settle(() => !button('核对并按原操作重试').disabled)
  expect(post).not.toHaveBeenCalled()
  await act(async () => button('核对并按原操作重试').click())
  await settle(() => Boolean(container!.querySelector('[role="alert"]')))
  expect(post).toHaveBeenCalledTimes(1)
  expect(post.mock.calls[0]).toEqual(['original-run', saved.intent.command, 'owner'])
  expect(listPendingRequests('research-command')).toHaveLength(1)
  get.mockResolvedValue(receipt)
  await act(async () => {
    window.history.replaceState(null, '', '#/technical?run=original-run')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  await settle(() => Boolean(container!.textContent?.includes('原操作已确认创建后续研究')))
  expect(window.location.hash).toBe('#/technical?run=original-run')
  expect(post).toHaveBeenCalledTimes(1)
  expect(get.mock.calls.every(args => JSON.stringify(args) === JSON.stringify(['original-run', saved.record.id, 'owner']))).toBe(true)
  expect(listPendingRequests('research-command')).toEqual([])
  const original = container!.querySelector<HTMLDetailsElement>('[aria-label="任务操作回执"] details')!
  expect(original.open).toBe(false)
})


it('restores a late successful command after leaving the actual App and returning without another POST or route takeover', async () => {
  const saved = createPendingCommand('original-run', 'owner', 'retry', '保持原任务和原请求键')
  const receipt: ResearchRunCommandReceipt = {
    schema_version: 'research-run-command-receipt.v1', request_id: saved.record.id,
    owner_id: 'owner', source_run_id: 'original-run', command: saved.intent.command,
    result: { schema_version: 'research-run-command-result.v1', request_id: saved.record.id,
      command: 'retry', source_run_id: 'original-run', target_run_id: 'unique-child', status: 'accepted',
      created_at: '2026-10-05T06:00:00Z' },
  }
  let committed: ResearchRunCommandReceipt | null = null
  let resolvePost!: (value: ResearchRunCommandReceipt['result']) => void
  const delayed = new Promise<ResearchRunCommandReceipt['result']>(resolve => { resolvePost = resolve })
  const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
  const post = vi.spyOn(api, 'researchCommand').mockImplementation(() => { committed = receipt; return delayed })
  window.history.replaceState(null, '', '#/failed?run=original-run')
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={client!}><App /></QueryClientProvider>))
  await settle(() => Boolean(container!.textContent?.includes('有一笔尚未确认的任务操作')))
  await settle(() => !button('核对并按原操作重试').disabled)
  await act(async () => button('核对并按原操作重试').click())
  await settle(() => post.mock.calls.length === 1)
  await act(async () => container!.querySelector<HTMLButtonElement>('nav button[aria-label="管理与审计"]')!.click())
  await settle(() => container!.querySelector('.breadcrumbs strong')?.textContent === '管理与审计')
  const managementHash = window.location.hash
  await act(async () => resolvePost(receipt.result))
  await settle(() => get.mock.calls.length === 3)
  expect(window.location.hash).toBe(managementHash)
  expect(listPendingRequests('research-command')).toEqual([saved.record])
  await act(async () => {
    window.history.replaceState(null, '', '#/failed?run=original-run')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  await settle(() => Boolean(container!.textContent?.includes('打开已确认的新研究')))
  expect(window.location.hash).toBe('#/failed?run=original-run')
  expect(get).toHaveBeenCalledTimes(4)
  expect(post).toHaveBeenCalledTimes(1)
  expect(get.mock.calls.every(args => JSON.stringify(args) === JSON.stringify(['original-run', saved.record.id, 'owner']))).toBe(true)
  expect(listPendingRequests('research-command')).toEqual([])
  await act(async () => button('打开已确认的新研究').click())
  await settle(() => new URLSearchParams(window.location.hash.split('?')[1]).get('run') === 'unique-child')
  expect(get).toHaveBeenCalledTimes(5)
  expect(post).toHaveBeenCalledTimes(1)
})
