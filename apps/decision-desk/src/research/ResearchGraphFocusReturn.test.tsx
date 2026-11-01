// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, StrictMode, useEffect, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { delivery } from '../api/delivery'
import { researchRunDetailViewSchema } from '../api/schemas'
import { RouteContent } from '../product/RouteContent'
import { ResearchPage, researchRouteHref } from './ResearchPage'

vi.mock('./DecisionGraphCanvas', () => ({ default: () => <button type="button">图谱操作</button> }))
vi.mock('../product/ToolAttemptsPanel', () => ({ ToolAttemptsPanel: () => null }))
vi.mock('../product/ResearchCostPanel', () => ({ ResearchCostPanel: () => null }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const now = '2026-10-02T05:00:00Z'
function savedRun(runId: string) {
  return researchRunDetailViewSchema.parse({
    schema_version: 'research-run-detail-view.v1',
    run: {
      schema_version: 'research-run-view.v2', run_id: runId, event_id: `event-${runId}`,
      event_title: '已保存研究', admission_origin: 'manual', priority: 'high',
      status: 'research_only', stage: 'done', runtime_id: 'fixture', profile_ref: 'fixture.v1',
      current_round: 1, budget: null, coverage: null, current_action: '原材料已保存',
      stop_reason: null, failure: null, latest_sequence_no: 0, artifact_id: `artifact-${runId}`,
      available_at: null, parent_run_id: null, created_at: now, updated_at: now,
    },
    trigger_snapshot: null, decision_snapshot: null, rounds: [], causal_case: null, horizons: [],
    evidence: [],
    action_summary: {
      schema_version: 'research-action-summary.v1', recommendation: 'do_not_trade',
      headline: '未授权交易', rationale: '保存原材料供核对', reliability: 'limited',
      trade_authorized: false, artifact_status: 'limited', data_status: 'partial',
      accepted_evidence_count: 0, stale_evidence_count: 0, rejected_evidence_count: 0,
      blocking_gap_ids: [], warning_codes: [], supporting_evidence_refs: [], next_review_at: null,
      research_outlook: null, market_view: null, execution_view: null,
    },
    process_steps: [{
      step_id: `step-${runId}`, order: 1, kind: 'gate', label: '执行质量门', status: 'limited',
      objective: '核对发布条件', outcome: '原材料保留，未授权交易', evidence_refs: [],
      tool_call_refs: [], warning_codes: [], occurred_at: now,
    }], trace: [], total_tool_calls: 0, total_subagents: 0,
    total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null, reviews: [],
    decision_graph: {
      schema_version: 'decision-graph.v1', graph_status: 'bounded_fallback',
      generation_mode: 'evidence_projection', stop_reason: null,
      nodes: [{ node_id: `gate-${runId}`, node_type: 'gate', label: '执行质量门', summary: '本次受限', status: 'blocked', evidence_refs: [] }],
      edges: [],
    },
  })
}
const runA = savedRun('run-a')
const runB = savedRun('run-b')
const back = '#/Inbox?before=2026-10-02T05%3A00%3A00Z&record=run-a'
const reading = { graphQuery: '执行', graphFilter: 'gate', graphView: 'list' as const }
const reportHash = researchRouteHref({ name: 'report', runId: 'run-a', back, ...reading })
const graphHash = researchRouteHref({ name: 'graph', runId: 'run-a', back: reportHash, ...reading })
const nodeHash = researchRouteHref({ name: 'graph-node', runId: 'run-a', nodeId: 'gate-run-a', back: graphHash, ...reading })
const mounted: { root: Root; container: HTMLDivElement; client: QueryClient }[] = []
let forbiddenFetch: ReturnType<typeof vi.fn>

// The production scene key really removes the old Page/summary/expand DOM.
function RouteHarness() {
  const readScene = () => window.location.hash.slice(2).split('?')[0] || 'home'
  const [scene, setScene] = useState(readScene)
  useEffect(() => {
    const sync = () => setScene(readScene())
    window.addEventListener('popstate', sync)
    window.addEventListener('hashchange', sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('hashchange', sync)
    }
  }, [])
  return <RouteContent scene={scene}>
    {scene === 'home' ? <button autoFocus type="button">新页面入口</button>
      : <ResearchPage operationsLoading={false} operationsFailed={false} />}
  </RouteContent>
}

beforeEach(() => {
  forbiddenFetch = vi.fn(() => Promise.reject(new Error('Unmocked network forbidden')))
  vi.stubGlobal('fetch', forbiddenFetch)
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(() => {
    const rect = new DOMRect(0, 0, 10, 10)
    return Object.assign([rect], { item: (index: number) => index === 0 ? rect : null })
  })
  vi.spyOn(api, 'researchRuns').mockResolvedValue([runA.run, runB.run])
  vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  vi.spyOn(api, 'researchRun').mockImplementation(async id => id === 'run-a' ? runA : runB)
  vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
  vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
  // Report feedback discovers its immutable revision through this read-only GET.
  vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
  vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
  vi.spyOn(api, 'researchForecastAudit').mockResolvedValue([])
  for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation'] as const) {
    vi.spyOn(api, method).mockRejectedValue(new Error('附加诊断未记录'))
  }
})

afterEach(async () => {
  for (const item of mounted.splice(0)) {
    await act(async () => item.root.unmount())
    item.client.clear()
    item.container.remove()
  }
  expect(forbiddenFetch).not.toHaveBeenCalled()
  window.history.replaceState(null, '', '/')
  window.dispatchEvent(new PopStateEvent('popstate'))
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
}
async function mount(hash = nodeHash, strict = false) {
  window.history.replaceState(null, '', `/?run_id=run-a${hash}`)
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  mounted.push({ root, container, client })
  const children = <QueryClientProvider client={client}><RouteHarness /></QueryClientProvider>
  await act(async () => root.render(strict ? <StrictMode>{children}</StrictMode> : children))
  await settle()
  return { container, client }
}
function summary(container: HTMLElement) {
  return container.querySelector<HTMLElement>('.research-report-evidence > summary')!
}
async function close(container: HTMLElement, how: 'Escape' | 'button') {
  const exit = container.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!
  expect(exit).not.toBeNull()
  expect(document.activeElement).toBe(exit)
  await act(async () => {
    if (how === 'Escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    else exit.click()
  })
  await settle()
}

function holdNextDetail(page: Awaited<ReturnType<typeof mount>>) {
  let resolve!: (value: typeof runA) => void
  let reject!: (error: Error) => void
  const promise = new Promise<typeof runA>((success, failure) => { resolve = success; reject = failure })
  vi.mocked(api.researchRun).mockReturnValue(promise)
  page.client.removeQueries({ queryKey: ['research-run', 'run-a'], exact: true })
  return { resolve, reject }
}
async function navigate(hash: string) {
  await act(async () => {
    window.history.pushState(null, '', hash)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await settle()
}

describe('actual keyed route fullscreen focus return', () => {
  it.each(['Escape', 'button'] as const)('returns %s to the remounted same-Run folded report summary', async how => {
    const page = await mount()
    const oldSummary = summary(page.container)
    await close(page.container, how)
    const newSummary = summary(page.container)
    expect(oldSummary.isConnected).toBe(false)
    expect(newSummary).not.toBe(oldSummary)
    expect(window.location.hash).toBe(reportHash)
    expect(window.location.search).toBe('?run_id=run-a')
    expect(page.container.querySelector('[role="dialog"]')).toBeNull()
    expect(newSummary.parentElement?.hasAttribute('open')).toBe(false)
    expect(document.activeElement).toBe(newSummary)
  })
})

describe('one-use route focus handoff guards', () => {
  it('does not focus an ordinary report navigation or a deep-link query refresh', async () => {
    const page = await mount(reportHash)
    expect(document.activeElement).not.toBe(summary(page.container))
    await navigate(researchRouteHref({ name: 'report', runId: 'run-a', back, graphQuery: '另一个查询' }))
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it('waits through loading for the exact original Run without focusing loading or opening details', async () => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'Escape')
    expect(summary(page.container)).toBeNull()
    expect(document.activeElement).toBe(document.body)
    await act(async () => held.resolve(runA))
    await settle()
    expect(document.activeElement).toBe(summary(page.container))
    expect(summary(page.container).parentElement?.hasAttribute('open')).toBe(false)
    expect(window.location.hash).toBe(reportHash)
  })

  it('abandons a waiting handoff after the user focuses a new-page control, even if that control later blurs', async () => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'button')
    const newControl = page.container.querySelector<HTMLElement>('a[href],button')!
    expect(newControl).not.toBeNull()
    newControl.focus()
    expect(document.activeElement).toBe(newControl)
    newControl.blur()
    await act(async () => held.resolve(runA))
    await settle()
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it('cannot revive a pending return after A → B → A navigation with the same final href', async () => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'Escape')
    await navigate('#/home')
    expect(document.activeElement?.textContent).toBe('新页面入口')
    await navigate(reportHash)
    await act(async () => held.resolve(runA))
    await settle()
    expect(window.location.hash).toBe(reportHash)
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it.each(['popstate', 'hashchange'] as const)('invalidates even a second same-href %s before loading resolves', async kind => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'button')
    await act(async () => window.dispatchEvent(kind === 'popstate' ? new PopStateEvent(kind) : new HashChangeEvent(kind)))
    await act(async () => held.resolve(runA))
    await settle()
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it('does not carry the original return to a different Run or retain it when coming back', async () => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'Escape')
    await navigate(researchRouteHref({ name: 'report', runId: 'run-b' }))
    await act(async () => held.resolve(runB))
    await settle()
    expect(summary(page.container)).not.toBeNull()
    expect(document.activeElement).not.toBe(summary(page.container))
    await navigate(reportHash)
    await act(async () => page.client.setQueryData(['research-run', 'run-a'], runA))
    await settle()
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it('abandons a failed report read so later cached recovery does not steal focus', async () => {
    const page = await mount()
    const held = holdNextDetail(page)
    await close(page.container, 'button')
    await act(async () => held.reject(new Error('controlled detail read failed')))
    await settle()
    expect(summary(page.container)).toBeNull()
    await act(async () => page.client.setQueryData(['research-run', 'run-a'], runA))
    await settle()
    expect(summary(page.container)).not.toBeNull()
    expect(document.activeElement).not.toBe(summary(page.container))
  })

  it.each(['technical', 'report-incomplete'] as const)('returns to its own %s summary without changing the parent URL', async name => {
    const parent = researchRouteHref({ name, runId: 'run-a', back, ...reading })
    const page = await mount(researchRouteHref({ name: 'graph', runId: 'run-a', back: parent, ...reading }))
    await close(page.container, 'Escape')
    expect(window.location.hash).toBe(parent)
    expect(document.activeElement).toBe(summary(page.container))
    expect(summary(page.container).parentElement?.hasAttribute('open')).toBe(name === 'technical')
  })

  it('consumes once under StrictMode, preserves later focus, and removes both temporary route listeners', async () => {
    const page = await mount(nodeHash, true)
    const add = vi.spyOn(window, 'addEventListener')
    const remove = vi.spyOn(window, 'removeEventListener')
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    await close(page.container, 'button')
    const target = summary(page.container)
    expect(document.activeElement).toBe(target)
    expect(focus.mock.contexts.filter(element => element === target)).toHaveLength(1)
    const other = page.container.querySelector<HTMLElement>('a[href],button')!
    other.focus()
    await act(async () => page.client.setQueryData(['research-run', 'run-a'], { ...runA }))
    await settle()
    expect(document.activeElement).toBe(other)
    const temporary = (calls: typeof add.mock.calls) => calls.filter(([kind, , capture]) => ['popstate', 'hashchange'].includes(kind) && capture === true)
    expect(temporary(add.mock.calls)).toHaveLength(2)
    expect(temporary(remove.mock.calls)).toHaveLength(2)
  })
})
