// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { EvolutionPanel, nav } from './App'
import { api } from './api/client'
import type { EvolutionOverview, RunInspector } from './api/types'

vi.mock('./api/client', () => {
  const pending = () => new Promise(() => undefined)
  return { api: {
    summary: pending,
    ledgerRunsPage: pending,
    runInspector: pending,
    health: pending,
    researchMemos: pending,
    run: pending,
    researchRun: pending,
    researchLinkedCommandReceipt: async () => null,
    evolutionOverview: pending,
    evolutionJobs: pending,
    operations: pending,
    sourcePollAttempts: pending,
    strategyCalibrations: pending,
    submitObservation: pending,
  } }
})

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const overview: EvolutionOverview = {
  datasets: [],
  candidates: [{
    candidate_id: 'candidate-1',
    execution_scope: 'directional_synthesis',
    candidate_type: 'strategy',
    content_hash: 'a'.repeat(64),
    version: 'strategy.v2',
    parent_version: 'strategy.v1',
    status: 'candidate',
    created_at: '2026-08-28T01:00:00Z',
    content_ref: null,
    source: 'owner',
  }],
  experiments: [],
  results: [],
  pointers: [],
  failures: [],
  experiences: [],
  decisions: [],
}

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
  window.history.replaceState(null, '', '#/home')
  sessionStorage.clear()
})

describe('Promotion Desk', () => {
  it.each(['progress', 'queued'])('opens the saved risk Run through #%s and preserves the home return path', async scene => {
    const read = vi.spyOn(api, 'run').mockRejectedValue(new Error('test offline'))
    const detailRead = vi.spyOn(api, 'researchRun')
    window.history.replaceState(null, '', `#/${scene}?run=spot-running&back=%23%2Fhome`)
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>))
      for (let n = 0; n < 50 && container.querySelector('h1')?.textContent !== '最新进度暂时无法确认'; n++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
      expect(read).toHaveBeenCalledWith('spot-running')
      expect(read).toHaveBeenCalledTimes(1)
      expect(detailRead).not.toHaveBeenCalled()
      expect(container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
      expect(container.querySelector('textarea')).toBeNull()
      expect(container.querySelector('a[href="#/home"]')).not.toBeNull()
      expect(window.location.hash).toContain('run=spot-running')
    } finally { await act(async () => root.unmount()); client.clear(); read.mockRestore(); detailRead.mockRestore() }
  })
  it('keeps the delivery navigation focused on the core Research workflow', () => {
    expect(nav.map((item) => item.label)).toEqual(['今日研究', '市场与事件', '研究', '资料', '管理与审计'])
  })

  it.each(['Inbox', 'Operations', 'Evolution'])('opens the legacy #%s deep link on its own page', async (scene) => {
    window.history.replaceState(null, '', `#/${scene}`)
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await act(async () => {
      root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
    })

    for (let attempt = 0; attempt < 50 && !container.querySelector('h1'); attempt += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.querySelector('h1')?.textContent).toBe(scene === 'Evolution' ? '演进与审核' : scene === 'Inbox' ? '业务账本' : scene === 'Operations' ? '运行审计' : scene)
    expect(container.querySelector('.breadcrumbs strong')?.textContent).toBe(scene === 'Evolution' ? '演进与审核' : scene === 'Inbox' ? '业务账本' : scene === 'Operations' ? '运行审计' : scene)
    await act(async () => { root.unmount() })
    client.clear()
  })

  it.each([['sources', '来源与运行状态', '管理与审计'], ['results', '研究效果', '研究']])('opens the #%s deep link on its dedicated page', async (scene, heading, workspace) => {
    window.history.replaceState(null, '', `#/${scene}`)
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    await act(async () => { root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>) })
    for (let attempt = 0; attempt < 50 && !container.querySelector('h1'); attempt += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.querySelector('h1')?.textContent).toBe(heading)
    expect(container.querySelector('.breadcrumbs strong')?.textContent).toBe(workspace)
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('restores a selected legacy execution from its URL and closes back to the same filtered page', async () => {
    const record: RunInspector = {
      run: { run_id: 'old-rule', event_id: 'event-1', status: 'completed', strategy_version: 'baseline.v1', runtime_version: 'rules', snapshot_id: null, artifact_id: null, created_at: '2026-08-27T01:00:00Z', updated_at: '2026-08-27T01:00:00Z', finished_at: null, latency_ms: 0, cost_usd: null, error_code: null, headline: '历史规则记录', gate_status: null },
      artifact: null, timeline: [], calls: [], steps: [], evaluations: [], evaluation_count: 0, snapshot_cutoff_at: null, snapshot_hash: null, evidence_lineage: [], versions: { strategy_version: 'baseline.v1', runtime_version: 'rules', pack_version: null, provider_ids: [], models: [], schema_versions: [], pricing_versions: [] }, orchestration: { mode: 'fixed_graph', supervisor_role: null, planned_capabilities: [], required_capabilities: [], specialist_coverage: [], missing_capabilities: [], replan_count: 0, experiment_refs: [] },
    }
    const fetch = vi.spyOn(api, 'runInspector').mockResolvedValue(record)
    window.history.replaceState(null, '', '#/Inbox?kind=baseline&offset=40&before=2026-09-30T03%3A00%3A00Z&record=old-rule')
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>))
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
      expect(fetch).toHaveBeenCalledWith('old-rule')
      const drawer = container.querySelector('[role="dialog"]')!
      expect(drawer.textContent).toContain('历史规则记录')
      expect(drawer.textContent).not.toContain('打开研究报告')
      await act(async () => drawer.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click())
      const params = new URLSearchParams(window.location.hash.split('?')[1])
      expect(params.get('record')).toBeNull()
      expect(params.get('kind')).toBe('baseline')
      expect(params.get('offset')).toBe('40')
      expect(params.get('before')).toBe('2026-09-30T03:00:00Z')
      expect(container.querySelector('[role="dialog"]')).toBeNull()
    } finally { await act(async () => root.unmount()); client.clear(); fetch.mockRestore() }
  })

  it('requires a reason and explicit owner confirmation before Reject', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const onDecision = vi.fn(async () => ({}))
    const onReview = vi.fn(async () => { throw new Error('review should not run for reject') })

    await act(async () => {
      root.render(<EvolutionPanel evolution={overview} deciding={false} onReview={onReview} onDecision={onDecision} />)
    })
    const reject = container.querySelector<HTMLButtonElement>('[aria-label="拒绝 strategy.v2"]')
    expect(reject).not.toBeNull()
    await act(async () => { reject!.click() })

    const submit = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('确认拒绝'))
    expect(submit?.disabled).toBe(true)
    const textarea = container.querySelector<HTMLTextAreaElement>('.reason-field textarea')
    const checkbox = container.querySelector<HTMLInputElement>('.confirm-row input')
    expect(textarea).not.toBeNull()
    expect(checkbox).not.toBeNull()

    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      valueSetter?.call(textarea, 'Evidence lineage is incomplete')
      textarea!.dispatchEvent(new Event('input', { bubbles: true }))
      checkbox!.click()
    })
    expect(submit?.disabled).toBe(false)
    await act(async () => { submit!.click() })

    expect(onReview).not.toHaveBeenCalled()
    expect(onDecision).toHaveBeenCalledWith(expect.objectContaining({
      candidate_id: 'candidate-1',
      decision: 'reject',
      evaluation_refs: [],
      expected_generation: 0,
      owner: 'owner',
      reason: 'Evidence lineage is incomplete',
    }))
    await act(async () => { root.unmount() })
  })

  it('opens the acknowledged legacy Run and keeps the original ledger filters', async () => {
    const send = vi.spyOn(api, 'submitObservation').mockResolvedValue({ event_id: 'evt-receipt', run_id: 'run-receipt', status_url: '/v1/runs/run-receipt' })
    window.history.replaceState(null, '', '#/Inbox?kind=baseline&offset=40&before=2026-09-30T03%3A00%3A00Z')
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><App /></QueryClientProvider>))
      for (let count = 0; count < 30 && !container.textContent?.includes('提交事件文本'); count++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
      const open = [...container.querySelectorAll('button')].find(button => button.textContent === '提交事件文本')!
      await act(async () => open.click())
      const field = container.querySelector('textarea')!
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, '事件正文')
        field.dispatchEvent(new Event('input', { bubbles: true }))
      })
      await act(async () => container!.querySelector<HTMLButtonElement>('.submit-modal .button.primary')!.click())
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
      const params = new URLSearchParams(window.location.hash.split('?')[1])
      expect(params.get('record')).toBe('run-receipt')
      expect(params.get('kind')).toBe('baseline')
      expect(params.get('offset')).toBe('40')
      expect(params.get('before')).toBe('2026-09-30T03:00:00Z')
      expect(container.querySelector('.submit-modal')).toBeNull()
      expect(container.querySelector('[aria-label="执行记录详情"]')).not.toBeNull()
      expect(send).toHaveBeenCalledWith('事件正文', expect.stringMatching(/^observation-submit:/))
    } finally { await act(async () => root.unmount()); client.clear(); send.mockRestore() }
  })
})


it.each([null, 'research_harness'] as const)('keeps %s candidates visible without enabling the direction pointer', async scope => {
  const div = document.createElement('div'); const root = createRoot(div)
  const onReview = vi.fn(); const onDecision = vi.fn()
  const value = { ...overview, candidates: [{ ...overview.candidates[0], execution_scope: scope }] }
  try {
    await act(async () => root.render(<EvolutionPanel evolution={value} deciding={false} onReview={onReview} onDecision={onDecision} />))
    expect(div.textContent).toContain(scope ? '完整研究流程' : '未标注适用范围')
    expect(div.querySelector<HTMLButtonElement>('[aria-label="审核并启用 strategy.v2"]')?.disabled).toBe(true)
    expect(div.querySelector<HTMLButtonElement>('[aria-label="拒绝 strategy.v2"]')?.disabled).toBe(false)
    expect(onReview).not.toHaveBeenCalled()
    expect(onDecision).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()) }
})
