// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, StrictMode, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { researchRunDetailViewSchema } from '../api/schemas'
import type { EvolutionOverview, MacroEventView, RunInspector } from '../api/types'
import App, { EvolutionPanel, RunDrawer } from '../App'
import { DesignStatePage } from '../DesignStatePage'
import { EvidenceDrawer, ResearchRunDetail } from '../research/ResearchPage'
import { ObservationSubmitDialog } from './ObservationSubmitDialog'
import { useModalScrollLock } from './useModalScrollLock'
import { useDetailFocus } from './useDetailFocus'

vi.mock('../research/DecisionGraphCanvas', () => ({ default: () => <div data-testid="graph-canvas" /> }))
vi.mock('./EventArchivePanel', () => ({ EventArchivePanel: () => null }))
vi.mock('./ToolAttemptsPanel', () => ({ ToolAttemptsPanel: () => null }))
vi.mock('./ResearchCostPanel', () => ({ ResearchCostPanel: () => null }))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const at = '2026-10-02T02:00:00Z'
const saved = researchRunDetailViewSchema.parse({
  schema_version: 'research-run-detail-view.v1',
  run: {
    schema_version: 'research-run-view.v2', run_id: 'modal-run', event_id: 'modal-event',
    event_title: '已保存研究', admission_origin: 'manual', priority: 'high',
    status: 'research_only', stage: 'done', runtime_id: 'fixture', profile_ref: 'fixture.v1',
    current_round: 1, budget: null, coverage: null, current_action: '原材料已保存',
    stop_reason: null, failure: null, latest_sequence_no: 0, artifact_id: 'modal-artifact',
    available_at: null, parent_run_id: null, created_at: at, updated_at: at,
  },
  trigger_snapshot: null, decision_snapshot: null, rounds: [], causal_case: null, horizons: [],
  evidence: [{
    evidence_id: 'modal-evidence', requirement_id: 'event_identity', kind: 'official',
    authority: 'official', source_id: 'federalreserve.gov', source_url: null,
    published_at: at, observed_at: at, received_at: at, content_hash: 'a'.repeat(64),
    excerpt: '原始材料', content_ref: null, acquisition_receipt_id: null,
    source_content_hash: null, parser_ref: null, structured_payload_ref: null,
    tool_call_id: 'modal-call', research_session_id: 'modal-session', round: 1,
    quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
  }],
  action_summary: {
    schema_version: 'research-action-summary.v1', recommendation: 'do_not_trade',
    headline: '未授权交易', rationale: '保存原材料供核对', reliability: 'limited',
    trade_authorized: false, artifact_status: 'limited', data_status: 'partial',
    accepted_evidence_count: 1, stale_evidence_count: 0, rejected_evidence_count: 0,
    blocking_gap_ids: [], warning_codes: [], supporting_evidence_refs: [], next_review_at: null,
    research_outlook: null, market_view: null, execution_view: null,
  },
  process_steps: [{
    step_id: 'modal-step', order: 1, kind: 'gate', label: '执行质量门', status: 'limited',
    objective: '核对发布条件', outcome: '原材料保留，未授权交易', evidence_refs: [],
    tool_call_refs: [], warning_codes: [], occurred_at: at,
  }], trace: [], total_tool_calls: 0, total_subagents: 0,
  total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null, reviews: [],
  decision_graph: {
    schema_version: 'decision-graph.v1', graph_status: 'bounded_fallback',
    generation_mode: 'evidence_projection', stop_reason: null,
    nodes: [
      { node_id: 'modal-gate', node_type: 'gate', label: '执行质量门', summary: '本次受限', status: 'blocked', evidence_refs: ['modal-evidence'] },
      { node_id: 'modal-source', node_type: 'evidence', label: '来源证据', summary: '原材料已保存', status: 'confirmed', evidence_refs: ['modal-evidence'] },
    ], edges: [],
  },
})

const event = {
  macro_event_id: 'modal-event', family: '历史发布', scheduled_at: at, updated_at: at,
  source_id: 'official', source_authority: 'official', source_url: null,
  prior_value: null, consensus_value: null, actual_value: 2, surprise: null,
  revision_flag: false, status: 'released',
} as MacroEventView

const ledgerRecord: RunInspector = {
  run: { run_id: 'ledger-modal', event_id: 'old-event', status: 'completed', strategy_version: 'baseline.v1', runtime_version: 'rules', snapshot_id: null, artifact_id: null, created_at: at, updated_at: at, finished_at: null, latency_ms: 0, cost_usd: null, error_code: null, headline: '历史规则记录', gate_status: null },
  artifact: null, timeline: [], calls: [], steps: [], evaluations: [], evaluation_count: 0,
  snapshot_cutoff_at: null, snapshot_hash: null, evidence_lineage: [],
  versions: { strategy_version: 'baseline.v1', runtime_version: 'rules', pack_version: null, provider_ids: [], models: [], schema_versions: [], pricing_versions: [] },
  orchestration: { mode: 'fixed_graph', supervisor_role: null, planned_capabilities: [], required_capabilities: [], specialist_coverage: [], missing_capabilities: [], replan_count: 0, experiment_refs: [] },
}
const overview: EvolutionOverview = {
  datasets: [], candidates: [{ candidate_id: 'modal-candidate', execution_scope: 'directional_synthesis', candidate_type: 'strategy', content_hash: 'b'.repeat(64), version: 'modal.v2', parent_version: 'modal.v1', status: 'candidate', created_at: at, content_ref: null, source: 'owner' }],
  experiments: [], results: [], pointers: [], failures: [], experiences: [], decisions: [],
}

const mounted: { root: Root; container: HTMLDivElement; client: QueryClient }[] = []
let originalHtml = ''
let originalBody = ''
let originalScrollTop = 0
let forbiddenFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  sessionStorage.clear()
  originalHtml = document.documentElement.style.cssText
  originalBody = document.body.style.cssText
  originalScrollTop = document.documentElement.scrollTop
  forbiddenFetch = vi.fn(() => Promise.reject(new Error('Unmocked network forbidden')))
  vi.stubGlobal('fetch', forbiddenFetch)
})

afterEach(async () => {
  for (const item of mounted.splice(0)) {
    await act(async () => item.root.unmount())
    item.client.clear()
    item.container.remove()
  }
  expect(forbiddenFetch).not.toHaveBeenCalled()
  document.documentElement.style.cssText = originalHtml
  document.body.style.cssText = originalBody
  document.documentElement.scrollTop = originalScrollTop
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
  sessionStorage.clear()
})

async function mount(children: ReactNode) {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  mounted.push({ root, container, client })
  const render = async (value: ReactNode) => {
    await act(async () => root.render(<QueryClientProvider client={client}>{value}</QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
  await render(children)
  return { root, container, render }
}

function expectRootLocked() {
  for (const element of [document.documentElement, document.body]) {
    expect(element.style.getPropertyValue('overflow-x')).toBe('hidden')
    expect(element.style.getPropertyValue('overflow-y')).toBe('hidden')
  }
}

function graphDetail(open = false) {
  return <ResearchRunDetail detail={saved} commandReason="" setCommandReason={() => undefined}
    commandPending={false} commandError={null} onCommand={vi.fn()}
    initialDetailView="technical" graphRouteOpen={open} />
}

describe('actual modal scroll locking', () => {
  it('locks html and body for the real event drawer and keeps its original return callback', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    vi.spyOn(api, 'macroEventsPage').mockResolvedValue({ items: [], total: 0 })
    vi.spyOn(api, 'macroEventFamilies').mockResolvedValue([])
    vi.spyOn(api, 'macroEvent').mockResolvedValue(event)
    const navigate = vi.fn()
    const params = new URLSearchParams({ event: 'modal-event', scope: 'history', offset: '200' })
    const page = await mount(<DesignStatePage scene="event" params={params} onNavigate={navigate} />)
    expect(page.container.querySelector('[aria-label="宏观事件详情"]')).not.toBeNull()
    expectRootLocked()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="关闭事件详情"]')!.click())
    expect(navigate).toHaveBeenCalledWith('calendar-history', { q: undefined, family: undefined, offset: '200' })
    await page.render(null)
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    expect(document.body.style.getPropertyValue('overflow-y')).toBe('')
    expect(focus.mock.calls.length).toBeGreaterThan(0)
    expect(focus.mock.calls.every(([options]) => options?.preventScroll === true)).toBe(true)
  })

  it('locks the real evidence drawer while retaining its source identity and close action', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    const close = vi.fn()
    const page = await mount(<EvidenceDrawer evidence={saved.evidence[0]} onClose={close} />)
    expectRootLocked()
    expect(page.container.querySelector('h2')?.textContent).toBe('美联储官网')
    expect(page.container.querySelector('details[aria-label="来源标识核对"]')?.textContent).toContain('federalreserve.gov')
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="关闭证据详情"]')!.click())
    expect(close).toHaveBeenCalledOnce()
    await page.render(null)
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    expect(focus.mock.calls.length).toBeGreaterThan(0)
    expect(focus.mock.calls.every(([options]) => options?.preventScroll === true)).toBe(true)
  })

  it('locks the actual fullscreen graph on both roots and leaves its inline state unlocked', async () => {
    const page = await mount(graphDetail())
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    expect(page.container.querySelector('[aria-label="全屏分析链路"]')).not.toBeNull()
    expectRootLocked()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!.click())
    expect(page.container.querySelector('[aria-label="全屏分析链路"]')).toBeNull()
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    expect(document.body.style.getPropertyValue('overflow-y')).toBe('')
  })

  it('locks the real App loading ledger modal through its loaded drawer and preserves filtered return', async () => {
    const pending = () => new Promise<never>(() => undefined)
    for (const name of ['summary', 'health', 'ledgerRunsPage', 'operations'] as const) vi.spyOn(api, name).mockImplementation(pending)
    let resolve!: (value: RunInspector) => void
    vi.spyOn(api, 'runInspector').mockImplementation(() => new Promise<RunInspector>(value => { resolve = value }))
    window.history.replaceState(null, '', '#/Inbox?kind=baseline&offset=40&before=2026-10-01T00%3A00%3A00Z&record=ledger-modal')
    const page = await mount(<App />)
    expect(page.container.querySelector('[aria-label="执行记录详情"]')?.textContent).toContain('正在读取所选记录')
    expectRootLocked()
    await act(async () => resolve(ledgerRecord))
    for (let attempt = 0; attempt < 20 && !page.container.querySelector('[aria-label="执行记录详情"] h2'); attempt++) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
    }
    expect(page.container.querySelector('[aria-label="执行记录详情"] h2')?.textContent).toBe('执行记录详情')
    const titleAudit = [...page.container.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '核对原始任务与报告标题')!
    expect(titleAudit.open).toBe(false)
    expect(JSON.parse(titleAudit.querySelector('pre')!.textContent!)).toEqual({ task_headline: ledgerRecord.run.headline, report_headline: ledgerRecord.artifact?.headline ?? null })
    expectRootLocked()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="执行记录详情"] [aria-label="关闭"]')!.click())
    const params = new URLSearchParams(window.location.hash.split('?')[1])
    expect(params.get('record')).toBeNull()
    expect(params.get('kind')).toBe('baseline')
    expect(params.get('offset')).toBe('40')
    expect(params.get('before')).toBe('2026-10-01T00:00:00Z')
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
  })

  it('locks the exported real ledger drawer without changing its close or report callbacks', async () => {
    const close = vi.fn(), report = vi.fn()
    const page = await mount(<RunDrawer selected={ledgerRecord.run} inspector={ledgerRecord} loading={false} failed={false} onClose={close} onReport={report} />)
    expectRootLocked()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click())
    expect(close).toHaveBeenCalledOnce()
    expect(report).not.toHaveBeenCalled()
  })

  it('locks the real promotion modal only while a draft is open without sending a decision', async () => {
    const review = vi.fn(async () => { throw new Error('reject needs no review') })
    const decision = vi.fn(async () => ({}))
    const page = await mount(<EvolutionPanel evolution={overview} deciding={false} onReview={review} onDecision={decision} />)
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="拒绝 modal.v2"]')!.click())
    expect(page.container.querySelector('[aria-labelledby="promotion-title"]')).not.toBeNull()
    expectRootLocked()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-labelledby="promotion-title"] [aria-label="关闭"]')!.click())
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    expect(review).not.toHaveBeenCalled()
    expect(decision).not.toHaveBeenCalled()
  })

  it('locks the real observation composer and closes without submitting or claiming a receipt', async () => {
    const post = vi.spyOn(api, 'submitObservation'), close = vi.fn(), receipt = vi.fn()
    const page = await mount(<ObservationSubmitDialog onClose={close} onReceipt={receipt} originHash="#/Inbox?offset=40" />)
    expectRootLocked()
    expect(page.container.querySelector('[aria-label="事件文本"]')).not.toBeNull()
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click())
    expect(close).toHaveBeenCalledOnce()
    expect(post).not.toHaveBeenCalled()
    expect(receipt).not.toHaveBeenCalled()
  })
})

function Lock({ active = true }: { active?: boolean }) {
  useModalScrollLock(active)
  return <section>受控弹窗生命周期</section>
}

function managedStyles() {
  return [document.documentElement, document.body].map(element =>
    ['overflow', 'overflow-x', 'overflow-y'].map(name => [name, element.style.getPropertyValue(name), element.style.getPropertyPriority(name)]))
}

describe('shared lock lifecycle and navigation boundaries', () => {
  it.each([true, false])('restores exact priorities only after both StrictMode locks close (first closes: %s)', async first => {
    document.documentElement.style.setProperty('overflow', 'auto', 'important')
    document.documentElement.style.setProperty('overflow-x', 'clip')
    document.body.style.setProperty('overflow-y', 'scroll', 'important')
    document.body.style.setProperty('color', 'red')
    const before = managedStyles()
    const children = (a: boolean, b: boolean) => <StrictMode><Lock active={a} /><Lock active={b} /></StrictMode>
    const page = await mount(children(true, true))
    expectRootLocked()
    document.body.style.setProperty('color', 'blue')
    await page.render(children(!first, first))
    expectRootLocked()
    expect(document.documentElement.style.getPropertyPriority('overflow-y')).toBe('important')
    await page.render(children(false, false))
    expect(managedStyles()).toEqual(before)
    expect(document.body.style.color).toBe('blue')
    await page.render(children(true, false))
    expectRootLocked()
    await page.render(null)
    expect(managedStyles()).toEqual(before)
  })

  it.each(['graph', 'evidence'] as const)('keeps a real graph/evidence stack locked when %s closes first, including graph edits', async first => {
    document.documentElement.style.setProperty('overflow-y', 'auto', 'important')
    document.body.style.setProperty('overflow-x', 'clip')
    const before = managedStyles()
    const setters = vi.spyOn(CSSStyleDeclaration.prototype, 'setProperty')
    const graph = await mount(graphDetail(true))
    expectRootLocked()
    const writes = () => setters.mock.calls.filter(([name]) => name === 'overflow-x' || name === 'overflow-y').length
    const firstWrites = writes()
    const evidence = await mount(<EvidenceDrawer evidence={saved.evidence[0]} onClose={vi.fn()} />)
    expect(writes()).toBe(firstWrites)
    const dialog = graph.container.querySelector('[aria-label="全屏分析链路"]')!
    const input = dialog.querySelector<HTMLInputElement>('[aria-label="搜索分析链路节点"]')!
    const filter = dialog.querySelector<HTMLSelectElement>('[aria-label="筛选图谱节点类型"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '执行')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      filter.value = 'gate'; filter.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => [...dialog.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === '列表查看')!.click())
    expect(dialog.querySelectorAll('.graph-node-list > button')).toHaveLength(1)
    expect(writes()).toBe(firstWrites)
    if (first === 'graph') await act(async () => graph.container.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!.click())
    else await evidence.render(null)
    expectRootLocked()
    if (first === 'graph') await evidence.render(null)
    else await act(async () => graph.container.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!.click())
    expect(managedStyles()).toEqual(before)
    expect(graph.container.querySelector<HTMLInputElement>('[aria-label="搜索分析链路节点"]')?.value).toBe('执行')
    expect(graph.container.querySelector<HTMLSelectElement>('[aria-label="筛选图谱节点类型"]')?.value).toBe('gate')
    expect(graph.container.querySelectorAll('.graph-node-list > button')).toHaveLength(1)
  })

  it('does not scroll to an obsolete coordinate on real navigation before modal unmount', async () => {
    window.history.replaceState(null, '', '#/Inbox?record=ledger-modal')
    document.documentElement.scrollTop = 251
    const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    const page = await mount(<ObservationSubmitDialog onClose={vi.fn()} onReceipt={vi.fn()} />)
    expectRootLocked()
    await act(async () => {
      window.history.pushState(null, '', '#/market')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    document.documentElement.scrollTop = 35
    await page.render(null)
    expect(window.location.hash).toBe('#/market')
    expect(document.documentElement.scrollTop).toBe(35)
    expect(scroll).not.toHaveBeenCalled()
  })

  it('preserves a same-route natural position and never locks inline useDetailFocus', async () => {
    document.documentElement.scrollTop = 251
    const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    const page = await mount(<EvidenceDrawer evidence={saved.evidence[0]} onClose={vi.fn()} />)
    await page.render(null)
    expect(document.documentElement.scrollTop).toBe(251)
    expect(scroll).not.toHaveBeenCalled()
    function Inline() {
      const ref = useDetailFocus('inline-detail')
      return <section ref={ref} tabIndex={-1}>行内详情</section>
    }
    await page.render(<Inline />)
    expect(document.documentElement.style.getPropertyValue('overflow-y')).toBe('')
    expect(document.body.style.getPropertyValue('overflow-y')).toBe('')
  })
})
