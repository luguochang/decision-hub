// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, StrictMode, useRef, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchRunDetailViewSchema } from '../api/schemas'
import { EvidenceDrawer, ResearchRunDetail } from '../research/ResearchPage'
import { useModalFocusContainment } from './useModalFocusContainment'

vi.mock('../research/DecisionGraphCanvas', () => ({ default: () => <div><div role="button" tabIndex={0}>真实图谱节点位置</div><button type="button">图谱操作</button></div> }))
vi.mock('./ToolAttemptsPanel', () => ({ ToolAttemptsPanel: () => null }))
vi.mock('./ResearchCostPanel', () => ({ ResearchCostPanel: () => null }))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const at = '2026-10-02T02:00:00Z'
const detail = researchRunDetailViewSchema.parse({
  schema_version: 'research-run-detail-view.v1',
  run: {
    schema_version: 'research-run-view.v2', run_id: 'focus-run', event_id: 'focus-event',
    event_title: '已保存研究', admission_origin: 'manual', priority: 'high',
    status: 'research_only', stage: 'done', runtime_id: 'fixture', profile_ref: 'fixture.v1',
    current_round: 1, budget: null, coverage: null, current_action: '原材料已保存',
    stop_reason: null, failure: null, latest_sequence_no: 0, artifact_id: 'focus-artifact',
    available_at: null, parent_run_id: null, created_at: at, updated_at: at,
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
    step_id: 'focus-step', order: 1, kind: 'gate', label: '执行质量门', status: 'limited',
    objective: '核对发布条件', outcome: '原材料保留，未授权交易', evidence_refs: [],
    tool_call_refs: [], warning_codes: [], occurred_at: at,
  }], trace: [], total_tool_calls: 0, total_subagents: 0,
  total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null, reviews: [],
  decision_graph: {
    schema_version: 'decision-graph.v1', graph_status: 'bounded_fallback',
    generation_mode: 'evidence_projection', stop_reason: null,
    nodes: [{ node_id: 'focus-gate', node_type: 'gate', label: '执行质量门', summary: '本次受限', status: 'blocked', evidence_refs: [] }],
    edges: [],
  },
})

const mounted: { root: Root; container: HTMLDivElement; client: QueryClient }[] = []
let forbiddenFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  forbiddenFetch = vi.fn(() => Promise.reject(new Error('Unmocked network forbidden')))
  vi.stubGlobal('fetch', forbiddenFetch)
  window.history.replaceState(null, '', '#/technical?run=focus-run')
  // JSDOM has no layout. Model a nonzero box; the hook still checks actual
  // hidden/disabled/closed-details ancestry and computed display/visibility.
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(() => {
    const rect = new DOMRect(0, 0, 10, 10)
    return Object.assign([rect], { item: (index: number) => index === 0 ? rect : null })
  })
})

afterEach(async () => {
  for (const item of mounted.splice(0)) {
    await act(async () => item.root.unmount())
    item.client.clear()
    item.container.remove()
  }
  expect(forbiddenFetch).not.toHaveBeenCalled()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
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
  return { container, render }
}

function graphDetail() {
  return <ResearchRunDetail detail={detail} commandReason="" setCommandReason={() => undefined}
    commandPending={false} commandError={null} onCommand={vi.fn()} initialDetailView="technical" />
}

describe('real fullscreen graph keyboard containment', () => {
  it('moves initial native focus into the actual fullscreen graph', async () => {
    const page = await mount(graphDetail())
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    const dialog = page.container.querySelector<HTMLElement>('[aria-label="全屏分析链路"]')!
    expect(dialog).not.toBeNull()
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(dialog.querySelector('[aria-label="退出全屏"]'))
  })

  it('contains native Shift+Tab at the actual exit button instead of allowing the background summary', async () => {
    const page = await mount(graphDetail())
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    const dialog = page.container.querySelector<HTMLElement>('[aria-label="全屏分析链路"]')!
    const exit = dialog.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!
    exit.focus()
    const shiftTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    await act(async () => exit.dispatchEvent(shiftTab))
    expect(shiftTab.defaultPrevented).toBe(true)
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).not.toBe(exit)
  })

  it('returns to the real same-Run expand button after close and preserves its existing callback', async () => {
    const close = vi.fn()
    const page = await mount(<ResearchRunDetail detail={detail} commandReason="" setCommandReason={() => undefined}
      commandPending={false} commandError={null} onCommand={vi.fn()} initialDetailView="technical" onCloseGraphRoute={close} />)
    const expand = page.container.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!
    expand.focus()
    await act(async () => expand.click())
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!.click())
    expect(document.activeElement).toBe(expand)
    expect(close).toHaveBeenCalledOnce()
    expect(page.container.querySelector('[aria-label="全屏分析链路"]')).toBeNull()
  })

  it('keeps the real Evidence drawer on top of an existing fullscreen graph and restores that graph', async () => {
    const close = vi.fn()
    const shell = (show: boolean) => <>{graphDetail()}{show && <EvidenceDrawer evidence={null} runId="focus-run" onClose={close} />}</>
    const page = await mount(shell(false))
    await act(async () => page.container.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    const graph = page.container.querySelector<HTMLElement>('[aria-label="全屏分析链路"]')!
    const exit = graph.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!
    exit.focus()
    await page.render(shell(true))
    const evidence = page.container.querySelector<HTMLElement>('[aria-label="证据详情"]')!
    expect(evidence.contains(document.activeElement)).toBe(true)
    const event = tab(evidence.querySelector('button')!, true)
    expect(event.defaultPrevented).toBe(true)
    expect(evidence.contains(document.activeElement)).toBe(true)
    await act(async () => evidence.querySelector<HTMLButtonElement>('[aria-label="关闭证据详情"]')!.click())
    expect(close).toHaveBeenCalledOnce()
    await page.render(shell(false))
    expect(document.activeElement).toBe(exit)
    expect(graph.contains(document.activeElement)).toBe(true)
  })
})

function Dialog({ children, active = true, scope = 'one', id = 'modal' }: { children?: ReactNode; active?: boolean; scope?: string; id?: string }) {
  const ref = useRef<HTMLElement>(null)
  useModalFocusContainment(ref, { active, scopeKey: scope })
  return <section ref={ref} tabIndex={-1} role="dialog" aria-modal="true" id={id}>{children}</section>
}

function tab(target: HTMLElement, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event
}

describe('shared modal focus lifecycle', () => {
  it('includes the first native summary and excludes hidden closed-details controls until opened', async () => {
    const page = await mount(<Dialog><button id="first">首项</button><details><summary id="summary">原文核对</summary><button id="closed">完整原文操作</button><summary id="secondary">非原生折叠标题</summary></details></Dialog>)
    const first = page.container.querySelector<HTMLButtonElement>('#first')!
    const summary = page.container.querySelector<HTMLElement>('#summary')!
    expect(document.activeElement).toBe(first)
    expect(tab(first, true).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(summary)
    expect(tab(summary).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
    page.container.querySelector('details')!.open = true
    first.focus()
    tab(first, true)
    expect(document.activeElement).toBe(page.container.querySelector('#closed'))
  })

  it('keeps fixed controls focusable and skips hidden, disabled and negative-tabindex controls', async () => {
    const page = await mount(<Dialog><button disabled>禁用</button><button style={{ visibility: 'hidden' }}>隐藏</button><div hidden><button>隐藏祖先</button></div><button tabIndex={-1}>只可程序聚焦</button><button id="fixed" style={{ position: 'fixed' }}>固定位置</button><a href="#proof" id="last">原证据入口</a></Dialog>)
    const fixed = page.container.querySelector<HTMLButtonElement>('#fixed')!
    expect(fixed.offsetParent).toBeNull()
    expect(document.activeElement).toBe(fixed)
    tab(fixed, true)
    expect(document.activeElement).toBe(page.container.querySelector('#last'))
  })

  it('refreshes dynamic focusable items without resetting focus on unrelated re-renders', async () => {
    const shell = (added: boolean) => <Dialog><button id="first">首项</button><button id="existing">当前项</button>{added && <button id="added">动态项</button>}</Dialog>
    const page = await mount(shell(false))
    const existing = page.container.querySelector<HTMLButtonElement>('#existing')!
    existing.focus()
    await page.render(shell(true))
    expect(document.activeElement).toBe(existing)
    const added = page.container.querySelector<HTMLButtonElement>('#added')!
    added.focus()
    expect(tab(added).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(page.container.querySelector('#first'))
    await page.render(shell(false))
    existing.focus()
    tab(existing)
    expect(document.activeElement).toBe(page.container.querySelector('#first'))
  })

  it('keeps a child on top even when the parent layout effect registers after it', async () => {
    const shell = (child: boolean, parentActive = true) => <Dialog id="parent" active={parentActive}><button id="parent-first">父层</button>{child && <Dialog id="child" scope="child"><button id="child-first">子层首项</button><button id="child-last">子层末项</button></Dialog>}</Dialog>
    const page = await mount(shell(true))
    const childFirst = page.container.querySelector<HTMLButtonElement>('#child-first')!
    expect(document.activeElement).toBe(childFirst)
    tab(childFirst, true)
    expect(document.activeElement).toBe(page.container.querySelector('#child-last'))
    await page.render(shell(true, false))
    expect(document.activeElement).toBe(page.container.querySelector('#child-last'))
    tab(page.container.querySelector<HTMLButtonElement>('#child-last')!)
    expect(document.activeElement).toBe(childFirst)
    await page.render(shell(false, true))
    expect(document.activeElement).toBe(page.container.querySelector('#parent-first'))
  })

  it('redirects background focus only while active and removes listeners across StrictMode reopens', async () => {
    const adds = vi.spyOn(document, 'addEventListener'), removes = vi.spyOn(document, 'removeEventListener')
    const shell = (active: boolean) => <StrictMode><button id="background">背景</button><Dialog active={active}><button id="inside">对话框</button></Dialog></StrictMode>
    const page = await mount(shell(true))
    const background = page.container.querySelector<HTMLButtonElement>('#background')!
    background.focus()
    expect(document.activeElement).toBe(page.container.querySelector('#inside'))
    await page.render(shell(false))
    background.focus()
    expect(document.activeElement).toBe(background)
    expect(tab(background).defaultPrevented).toBe(false)
    await page.render(shell(true))
    expect(document.activeElement).toBe(page.container.querySelector('#inside'))
    await page.render(null)
    for (const name of ['keydown', 'focusin']) {
      const added = adds.mock.calls.filter(([type, , capture]) => type === name && capture === true)
      const removed = removes.mock.calls.filter(([type, , capture]) => type === name && capture === true)
      expect(added.length).toBeGreaterThan(0)
      expect(removed.length).toBe(added.length)
    }
  })

  it('does not restore a still-connected reused opener to a different Run', async () => {
    const shell = (active: boolean, scope: string) => <><button id="opener">同DOM入口</button><Dialog active={active} scope={scope}><button id="inside">原任务详情</button></Dialog></>
    const page = await mount(shell(false, 'run-one'))
    const opener = page.container.querySelector<HTMLButtonElement>('#opener')!
    opener.focus()
    await page.render(shell(true, 'run-one'))
    const spy = vi.spyOn(opener, 'focus')
    await page.render(shell(false, 'run-two'))
    expect(opener.isConnected).toBe(true)
    expect(spy).not.toHaveBeenCalled()
  })

  it('does not steal meaningful focus assigned to a newly navigated page', async () => {
    const shell = (open: boolean) => <><button id="opener">旧入口</button><Dialog active={open}><button>原详情</button></Dialog>{!open && <button id="new-page" autoFocus>新页面动作</button>}</>
    const page = await mount(shell(true))
    await page.render(shell(false))
    expect(document.activeElement).toBe(page.container.querySelector('#new-page'))
  })

  it('uses the dialog fallback when no controls exist and always focuses without scrolling', async () => {
    const spy = vi.spyOn(HTMLElement.prototype, 'focus')
    const page = await mount(<Dialog><p>只读材料</p></Dialog>)
    const dialog = page.container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(document.activeElement).toBe(dialog)
    expect(tab(dialog, true).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(dialog)
    expect(spy.mock.calls.every(([options]) => options?.preventScroll === true)).toBe(true)
  })
})
