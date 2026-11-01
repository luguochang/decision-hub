// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api/client'

vi.mock('./api/client', () => {
  const pending = () => new Promise(() => undefined)
  return { apiUrl: (path: string) => path, api: {
    summary: pending, ledgerRunsPage: pending, runInspector: pending, health: pending,
    researchMemos: pending, evolutionOverview: pending, evolutionJobs: async () => [], operations: pending,
    researchRun: pending, researchCommand: pending, researchCommandReceipt: pending,
    researchRuns: async () => [], macroEvents: async () => [], researchRunScope: async () => null,
    researchInbox: pending, researchHome: pending, strategyCalibrations: async () => [], researchReviewPlans: async () => [],
    researchRunComparison: pending, researchObservability: pending, researchValueEvaluation: pending, researchForecastAudit: pending,
    submitResearch: pending, submitObservation: pending,
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
  for (let count = 0; count < 80 && !predicate(); count++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(predicate()).toBe(true)
}
async function pause() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) }) }
function button(text: string) {
  const found = [...container!.querySelectorAll('button')].find(item => item.textContent === text)
  expect(found).toBeTruthy(); return found!
}
async function route(hash: string) {
  await act(async () => { window.history.replaceState(null, '', hash); window.dispatchEvent(new HashChangeEvent('hashchange')) })
}
async function mount(hash: string) {
  window.history.replaceState(null, '', hash)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 2, retryDelay: 0 } } })
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={client!}><App /></QueryClientProvider>))
}
const queued = { schema_version: 'research-run-queued.v1' as const, event_id: 'evt-old', run_id: 'run-old-reply', status: 'queued' as const, status_url: '/v1/runs/run-old-reply' }

it('keeps a departed compose page in place and saves the late receipt for an explicit open', async () => {
  let release!: (value: typeof queued) => void
  const send = vi.spyOn(api, 'submitResearch').mockImplementation(() => new Promise(resolve => { release = resolve }))
  await mount('#/compose?focus=original&back=%23%2Fstudies%3Fstudy%3Ds1')
  await settle(() => Boolean(container?.textContent?.includes('开始分析')))
  await act(async () => button('开始分析').click()); await pause()
  await route('#/management'); await settle(() => container?.querySelector('h1')?.textContent === '管理与审计')
  await act(async () => release(queued)); await pause()
  expect(window.location.hash).toBe('#/management')
  await route('#/compose'); await settle(() => Boolean(container?.textContent?.includes('收到接收回执')))
  expect(send).toHaveBeenCalledTimes(1)
  expect(window.location.hash).toBe('#/compose')
  await act(async () => button('打开已接收的研究任务').click())
  expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe(queued.run_id)
})

it('does not let a global legacy modal steal navigation after leaving its filtered Inbox', async () => {
  const receipt = { event_id: 'event-rule', run_id: 'run-rule', status_url: '/v1/runs/run-rule' }
  let release!: (value: typeof receipt) => void
  const send = vi.spyOn(api, 'submitObservation').mockImplementation(() => new Promise(resolve => { release = resolve }))
  await mount('#/Inbox?kind=baseline&offset=40')
  await settle(() => Boolean(container?.textContent?.includes('提交事件文本')))
  await act(async () => button('提交事件文本').click())
  await act(async () => {
    const field = container!.querySelector('textarea')!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, '原事件正文')
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => container!.querySelector<HTMLButtonElement>('.submit-modal .button.primary')!.click()); await pause()
  await route('#/management'); await settle(() => container?.querySelector('h1')?.textContent === '管理与审计')
  await act(async () => release(receipt)); await pause()
  expect(window.location.hash).toBe('#/management')
  expect(container?.querySelector('.submit-modal')).toBeNull()
  await route('#/Inbox?kind=baseline&offset=40'); await settle(() => Boolean(container?.textContent?.includes('提交事件文本')))
  await act(async () => button('提交事件文本').click())
  await settle(() => Boolean(container?.textContent?.includes('收到接收回执')))
  expect(send).toHaveBeenCalledTimes(1)
  await act(async () => button('打开已接收的执行记录').click())
  const params = new URLSearchParams(window.location.hash.split('?')[1])
  expect(params.get('record')).toBe(receipt.run_id)
  expect(params.get('kind')).toBe('baseline'); expect(params.get('offset')).toBe('40')
})

it('does not auto-navigate when a research composer leaves and returns to the identical hash before its response', async () => {
  let release!: (value: typeof queued) => void
  const send = vi.spyOn(api, 'submitResearch').mockImplementation(() => new Promise(resolve => { release = resolve }))
  await mount('#/report?run=original-run')
  await settle(() => Boolean(container?.textContent?.includes('获取最新操作建议')))
  await act(async () => button('获取最新操作建议').click())
  await act(async () => button('开始分析').click()); await pause()
  await route('#/technical?run=another-run'); await route('#/report?run=original-run')
  await act(async () => release(queued)); await pause()
  expect(window.location.hash).toBe('#/report?run=original-run')
  expect(send).toHaveBeenCalledTimes(1)
  expect(container?.textContent).toContain('收到接收回执')
})

it.each(['success', 'error'])('keeps a new compose focus and return context when the previous %s arrives', async outcome => {
  let release!: (value: typeof queued) => void, reject!: (error: Error) => void
  const send = vi.spyOn(api, 'submitResearch').mockImplementation(() => new Promise((resolve, fail) => { release = resolve; reject = fail }))
  await mount('#/compose?focus=original&back=%23%2Fruns')
  await settle(() => Boolean(container?.textContent?.includes('开始分析')))
  await act(async () => button('开始分析').click()); await pause()
  const next = '#/compose?focus=new-draft&back=%23%2Fstudies%3Fstudy%3Ds2'
  await route(next)
  await settle(() => container?.querySelector('textarea')?.value === 'new-draft')
  await act(async () => { if (outcome === 'success') release(queued); else reject(new Error('late_failure_503')) }); await pause()
  expect(window.location.hash).toBe(next)
  expect(container?.querySelector('textarea')?.value).toBe('new-draft')
  expect(container?.querySelector('[role="alert"]')).toBeNull()
  expect(send).toHaveBeenCalledTimes(1)
})
