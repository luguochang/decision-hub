// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { researchRunDetailViewSchema } from '../api/schemas'
import { ResearchPage, researchRouteHref } from './ResearchPage'

vi.mock('./DecisionGraphCanvas', () => ({ default: () => <div data-testid="graph-canvas" /> }))
vi.mock('../product/ToolAttemptsPanel', () => ({ ToolAttemptsPanel: () => null }))
vi.mock('../product/ResearchCostPanel', () => ({ ResearchCostPanel: () => null }))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const now = '2026-10-01T12:00:00Z'
function savedRun(runId: string) {
  return researchRunDetailViewSchema.parse({
    schema_version: 'research-run-detail-view.v1',
    run: {
      schema_version: 'research-run-view.v2', run_id: runId, event_id: `event-${runId}`,
      event_title: `${runId} 已保存研究`, admission_origin: 'manual', priority: 'high',
      status: 'research_only', stage: 'done', runtime_id: 'fixture', profile_ref: 'fixture.v1',
      current_round: 1, budget: null, coverage: null, current_action: '原材料已保存',
      stop_reason: null, failure: null, latest_sequence_no: 0, artifact_id: `artifact-${runId}`,
      available_at: null, parent_run_id: null, created_at: now, updated_at: now,
    },
    trigger_snapshot: null, decision_snapshot: null, rounds: [], causal_case: null, horizons: [],
    evidence: [{
      evidence_id: `evidence-${runId}`, requirement_id: 'event_identity', kind: 'official',
      authority: 'official', source_id: 'manual', source_url: null,
      published_at: now, observed_at: now, received_at: now, content_hash: 'a'.repeat(64),
      excerpt: `${runId} 的原始材料`, content_ref: null, acquisition_receipt_id: null,
      source_content_hash: null, parser_ref: null, structured_payload_ref: null,
      tool_call_id: `call-${runId}`, research_session_id: `session-${runId}`, round: 1,
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
      step_id: `step-${runId}`, order: 1, kind: 'gate', label: '执行质量门', status: 'limited',
      objective: '核对发布条件', outcome: '原材料保留，尚未授权交易',
      evidence_refs: [], tool_call_refs: [], warning_codes: [], occurred_at: now,
    }], trace: [], total_tool_calls: 0, total_subagents: 0,
    total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null, reviews: [],
    decision_graph: {
      schema_version: 'decision-graph.v1', graph_status: 'bounded_fallback',
      generation_mode: 'evidence_projection', stop_reason: null,
      nodes: [
        { node_id: `gate-${runId}`, node_type: 'gate', label: '执行质量门', summary: '本次受限', status: 'blocked', evidence_refs: [`evidence-${runId}`] },
        { node_id: `source-${runId}`, node_type: 'evidence', label: '来源证据', summary: '原材料已保存', status: 'confirmed', evidence_refs: [`evidence-${runId}`] },
      ], edges: [],
    },
  })
}

const runA = savedRun('run-a')
const runB = savedRun('run-b')
const inboxBack = '#/Inbox?status=failed&record=run-a'
let root: Root | null = null
let container: HTMLDivElement | null = null
let client: QueryClient | null = null
let forbiddenFetch: ReturnType<typeof vi.fn> | null = null

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  client?.clear()
  container?.remove()
  expect(forbiddenFetch).not.toHaveBeenCalled()
  root = null; container = null; client = null; forbiddenFetch = null
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

async function flush() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
}

async function mount(hash = researchRouteHref({ name: 'technical', runId: 'run-a', back: inboxBack })) {
  window.history.replaceState(null, '', hash)
  sessionStorage.clear()
  forbiddenFetch = vi.fn(() => Promise.reject(new Error('Unmocked network is forbidden')))
  vi.stubGlobal('fetch', forbiddenFetch)
  vi.spyOn(api, 'researchRuns').mockResolvedValue([runA.run, runB.run])
  vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  vi.spyOn(api, 'researchRun').mockImplementation(async id => id === 'run-a' ? runA : runB)
  vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
  vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
  vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
  vi.spyOn(api, 'researchForecastAudit').mockResolvedValue([])
  vi.spyOn(api, 'researchEvidenceOriginal').mockRejectedValue(new Error('原件暂不可读取'))
  for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation'] as const) {
    vi.spyOn(api, method).mockRejectedValue(new Error('附加诊断未记录'))
  }
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={client!}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
  await flush()
  await flush()
}

function inlineGraph() {
  return container!.querySelector<HTMLElement>('section[aria-label="分析链路图"]')!
}
function button(scope: ParentNode, text: string) {
  const found = [...scope.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === text)
  expect(found, `Missing button: ${text}`).toBeDefined()
  return found!
}
async function setQuery(scope: ParentNode, query: string) {
  const input = scope.querySelector<HTMLInputElement>('[aria-label="搜索分析链路节点"]')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, query)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function setFilter(scope: ParentNode, kind: string) {
  const select = scope.querySelector<HTMLSelectElement>('[aria-label="筛选图谱节点类型"]')!
  await act(async () => { select.value = kind; select.dispatchEvent(new Event('change', { bubbles: true })) })
}
function expectGateList(scope: ParentNode, query = '执行') {
  expect(scope.querySelector<HTMLInputElement>('input')?.value).toBe(query)
  expect(scope.querySelector<HTMLSelectElement>('select')?.value).toBe('gate')
  expect(scope.querySelectorAll('.graph-node-list > button')).toHaveLength(1)
  expect(scope.textContent).toContain('1 / 2 节点')
  expect(button(scope, '返回图谱')).toBeDefined()
}

async function routeTo(hash: string) {
  await act(async () => {
    window.history.pushState(null, '', hash)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await flush()
}
async function remountPage() {
  await act(async () => root!.unmount())
  client!.clear()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  root = createRoot(container!)
  await act(async () => root!.render(<QueryClientProvider client={client!}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
  await flush()
  await flush()
}
function fullscreenGraph() {
  const dialog = container!.querySelector<HTMLElement>('[aria-label="全屏分析链路"]')
  expect(dialog).not.toBeNull()
  return dialog!
}
async function chooseGateList() {
  await setQuery(inlineGraph(), '执行')
  await setFilter(inlineGraph(), 'gate')
  await act(async () => button(inlineGraph(), '列表查看').click())
}

describe('graph route return context', () => {
  it('keeps inline query, gate filter and list through node fullscreen and exit', async () => {
    await mount()
    await setQuery(inlineGraph(), '执行')
    await setFilter(inlineGraph(), 'gate')
    await act(async () => button(inlineGraph(), '列表查看').click())
    expectGateList(inlineGraph())
    await act(async () => inlineGraph().querySelector<HTMLButtonElement>('.graph-node-list > button')!.click())
    const dialog = container!.querySelector<HTMLElement>('[aria-label="全屏分析链路"]')!
    expectGateList(dialog)
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe('run-a')
    await act(async () => button(dialog, '退出全屏').click())
    expect(window.location.hash).toContain('#/technical?')
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('back')).toBe(inboxBack)
    expect(container!.querySelector('[aria-label="全屏分析链路"]')).toBeNull()
    expectGateList(inlineGraph())
  })

  it('restores the latest fullscreen edits on Escape and restores inline context after remount', async () => {
    await mount()
    await act(async () => inlineGraph().querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    await setQuery(fullscreenGraph(), '执行')
    await setFilter(fullscreenGraph(), 'gate')
    await act(async () => button(fullscreenGraph(), '列表查看').click())
    expectGateList(fullscreenGraph())
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(container!.querySelector('[aria-label="全屏分析链路"]')).toBeNull()
    expectGateList(inlineGraph())
    const params = new URLSearchParams(window.location.hash.split('?')[1])
    expect(params.get('back')).toBe(inboxBack)
    expect(params.get('q')).toBe('执行')
    expect(params.get('kind')).toBe('gate')
    expect(params.get('view')).toBe('list')
    await remountPage()
    expectGateList(inlineGraph())
  })

  it('keeps the same Run and reading context through node, evidence, refresh and both close controls', async () => {
    await mount()
    await chooseGateList()
    await act(async () => inlineGraph().querySelector<HTMLButtonElement>('.graph-node-list > button')!.click())
    const nodeHash = window.location.hash
    const evidenceLink = fullscreenGraph().querySelector<HTMLAnchorElement>('.graph-node-evidence-list a')!
    expect(new URLSearchParams(evidenceLink.hash.split('?')[1]).get('run')).toBe('run-a')
    await act(async () => evidenceLink.click())
    const evidenceHash = window.location.hash
    const evidenceParams = new URLSearchParams(evidenceHash.split('?')[1])
    expect(evidenceParams.get('run')).toBe('run-a')
    expect(evidenceParams.get('evidence')).toBe('evidence-run-a')
    expect(evidenceParams.get('back')).toBe(nodeHash)
    expect(container!.querySelector('[aria-label="证据详情"]')?.textContent).toContain('run-a 的原始材料')
    expect(container!.querySelector('[aria-label="证据详情"]')?.textContent).not.toContain('run-b 的原始材料')
    await remountPage()
    expect(window.location.hash).toBe(evidenceHash)
    await act(async () => container!.querySelector<HTMLButtonElement>('[aria-label="关闭证据详情"]')!.click())
    expect(window.location.hash).toBe(nodeHash)
    expectGateList(fullscreenGraph())
    expect(fullscreenGraph().querySelector('[aria-label="图谱节点检查器"]')).not.toBeNull()
    await act(async () => fullscreenGraph().querySelector<HTMLButtonElement>('[aria-label="关闭节点检查器"]')!.click())
    expect(window.location.hash).toContain('#/graph?')
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe('run-a')
    expect(fullscreenGraph().querySelector('[aria-label="图谱节点检查器"]')).toBeNull()
    expectGateList(fullscreenGraph())
    await act(async () => button(fullscreenGraph(), '退出全屏').click())
    expectGateList(inlineGraph())
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('back')).toBe(inboxBack)
  })

  it('clears the previous Run context on popstate and restores an explicit node deep link on refresh', async () => {
    await mount()
    await chooseGateList()
    await act(async () => inlineGraph().querySelector<HTMLButtonElement>('.graph-node-list > button')!.click())
    const nodeHash = window.location.hash
    await routeTo(researchRouteHref({ name: 'technical', runId: 'run-b' }))
    expect(inlineGraph().querySelector<HTMLInputElement>('input')?.value).toBe('')
    expect(inlineGraph().querySelector<HTMLSelectElement>('select')?.value).toBe('all')
    expect(button(inlineGraph(), '列表查看')).toBeDefined()
    expect(inlineGraph().textContent).toContain('2 / 2 节点')
    expect(container!.querySelector('[aria-label="图谱节点检查器"]')).toBeNull()
    expect(container!.querySelector('[aria-label="全屏分析链路"]')).toBeNull()
    expect(api.researchRun).toHaveBeenCalledWith('run-b')
    await routeTo(nodeHash)
    await remountPage()
    expectGateList(fullscreenGraph())
    expect(fullscreenGraph().querySelector('[aria-label="图谱节点检查器"]')?.textContent).toContain('gate-run-a')
    const params = new URLSearchParams(window.location.hash.split('?')[1])
    expect(params.get('run')).toBe('run-a')
    expect(params.get('node')).toBe('gate-run-a')
    await act(async () => button(fullscreenGraph(), '退出全屏').click())
    expectGateList(inlineGraph())
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe('run-a')
  })

  it('does not follow a fullscreen parent belonging to another Run', async () => {
    const wrongParent = researchRouteHref({ name: 'technical', runId: 'run-b', graphQuery: '另一任务' })
    await mount(researchRouteHref({ name: 'graph', runId: 'run-a', back: wrongParent, graphQuery: '执行', graphFilter: 'gate', graphView: 'list' }))
    expectGateList(fullscreenGraph())
    await act(async () => button(fullscreenGraph(), '退出全屏').click())
    const params = new URLSearchParams(window.location.hash.split('?')[1])
    expect(window.location.hash).toContain('#/report?')
    expect(params.get('run')).toBe('run-a')
    expect(params.get('back')).toBeNull()
    expect(params.get('q')).toBe('执行')
    expect(params.get('view')).toBe('list')
    expect(api.researchRun).not.toHaveBeenCalledWith('run-b')
  })
})
