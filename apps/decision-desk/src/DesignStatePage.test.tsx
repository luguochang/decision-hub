// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesignStatePage, designCatalog } from './DesignStatePage'
import { researchRunDetailViewSchema } from './api/schemas'
import { api } from './api/client'
import { researchRunViewSchema } from './api/schemas'
import { AssetBackLink } from './product/assetFormat'
import type { MacroEventView, ResearchDocumentVersion, ResearchRunDetailView, ResearchRunView } from './api/types'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement | null = null

function renderScene(scene: Parameters<typeof DesignStatePage>[0]['scene']) {
  container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => { root.render(<QueryClientProvider client={client}><DesignStatePage scene={scene} params={new URLSearchParams()} onNavigate={vi.fn()} /></QueryClientProvider>) })
  return root
}

afterEach(() => {
  sessionStorage.clear()
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

async function renderHistoryScene(scene: Parameters<typeof DesignStatePage>[0]['scene'], params: URLSearchParams, onNavigate = vi.fn()) {
  container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => { root.render(<QueryClientProvider client={client}><DesignStatePage scene={scene} params={params} onNavigate={onNavigate} /></QueryClientProvider>) })
  return { root, client, onNavigate }
}

async function waitForVisible(predicate: () => boolean) {
  for (let attempt = 0; attempt < 20 && !predicate(); attempt += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
  }
  expect(predicate()).toBe(true)
}

function historyCoverageRun(ratio: number, importance: ('hard' | 'soft')[], basis: ResearchRunView['coverage_basis'] = 'saved_task_request') {
  const at = '2026-10-01T12:00:00Z'
  return researchRunViewSchema.parse({
    schema_version: 'research-run-view.v2', run_id: 'coverage-run', event_id: 'coverage-event',
    event_title: '原始任务与证据覆盖', admission_origin: 'manual', priority: 'normal',
    status: 'research_only', stage: 'done', runtime_id: 'fixture', profile_ref: 'fixture.v1',
    current_round: 1, budget: null, coverage_basis: basis,
    coverage: {
      status: 'bounded_stop', covered_requirement_ids: [], conflicts: [],
      hard_coverage_ratio: ratio, soft_coverage_ratio: 0, assessed_at: at,
      gaps: importance.map((item, index) => ({
        requirement_id: `requirement-${index}`, importance: item, reason_code: 'missing',
        query_hint: '原始补充要求', attempted_capabilities: [], blocks_directional_output: true,
      })),
    },
    current_action: null, stop_reason: null, failure: null, latest_sequence_no: 0,
    artifact_id: 'saved-artifact', available_at: null, parent_run_id: null, created_at: at, updated_at: at,
  })
}

describe('Decision Hub design state routes', () => {
  it.each(['dsh', 'dsh-conversation', 'dsh-research', 'dsh-report'] as const)('keeps the same research and nested return path on %s with readable labels and unchanged raw records', async (scene) => {
    const runId = 'dsh-linked-run'
    const back = '#/technical?run=dsh-linked-run&back=%23%2Fruns%3Fstatus%3Dfailed%26offset%3D50'
    const originalHeadline = 'Original model headline, without generated translation.'
    const originalObjective = 'Original model task: examine the cross-asset reaction.'
    const trace = {
      sequence_no: 1, event_type: 'tool_completed', stage: 'acquiring_evidence',
      status: 'succeeded', occurred_at: '2026-10-01T00:00:00Z',
      summary: 'Original model summary with unchanged 17.25 value.', error_code: null,
    }
    const detail = {
      run: { run_id: runId, status: 'research_only', stage: 'done' },
      action_summary: { headline: originalHeadline, rationale: 'Original model rationale.' },
      trace: [trace], evidence: [{ quality: 'accepted' }, { quality: 'candidate' }], total_tool_calls: 1,
      rounds: [{ round: 1, plan: { objective: originalObjective, tasks: [{ task_id: 'macro-task', requirement_id: 'macro_transmission', objective: 'Original task detail.', question: 'What changed in the original model question?' }, { task_id: 'unknown-task', requirement_id: 'unfamiliar_requirement', objective: 'Arbitrary English without a supplied translation.' }] }, tool_invocations: [{ tool_name: 'dsh.internal.skill' }, { tool_name: 'market.cross_asset' }] }],
    } as unknown as ResearchRunDetailView
    vi.spyOn(api, 'researchRuns').mockResolvedValue([])
    const read = vi.spyOn(api, 'researchRun').mockResolvedValue(detail)
    const { root, client, onNavigate } = await renderHistoryScene(scene, new URLSearchParams({ run: runId, back }))
    await waitForVisible(() => Boolean(container?.querySelector('.dsh-original-record')))
    expect(read).toHaveBeenCalledWith(runId)
    expect(container!.querySelector('a')?.getAttribute('href')).toBe(back)
    expect([...container!.querySelectorAll('.design-tab-row button')].map(b => b.textContent)).toEqual(['技术轨迹', '对话摘要', '主动研究', '原生报告'])
    for (const [index, target] of ['dsh', 'dsh-conversation', 'dsh-research', 'dsh-report'].entries()) {
      await act(async () => container!.querySelectorAll<HTMLButtonElement>('.design-tab-row button')[index].click())
      expect(onNavigate).toHaveBeenLastCalledWith(target, { run: runId, back })
    }
    const rawRecord = JSON.parse(container!.querySelector('.dsh-original-record pre')!.textContent!)
    if (scene === 'dsh') {
      expect(container!.querySelector('.dsh-log-list strong')?.textContent).toBe('工具调用完成')
      expect(container!.querySelector('.dsh-log-list small')?.textContent).toContain('取得证据 · 成功')
      expect(rawRecord).toEqual(trace)
    } else if (scene === 'dsh-conversation') {
      expect(container!.querySelector('dl')?.textContent).toContain('仅研究')
      expect(container!.querySelector('dl')?.textContent).toContain('任务结束')
      expect(container!.querySelector('dl')?.textContent).toContain('保留材料2 条已采纳证据1 条')
      expect(container!.textContent).toContain(originalHeadline)
      expect(rawRecord.action_summary.headline).toBe(originalHeadline)
    } else if (scene === 'dsh-research') {
      expect(container!.querySelector('.dsh-log-list strong')?.textContent).toBe('第 1 轮研究')
      expect(container!.querySelector('.dsh-log-list ul')?.textContent).toContain('宏观传导数据')
      expect(container!.querySelector('.dsh-log-list ul')?.textContent).toContain('未知要求，请查看原始记录')
      expect(container!.querySelector('.dsh-log-list small')?.textContent).toBe('2 条工具调用记录')
      expect(container!.querySelector('.dsh-log-list small')?.textContent).not.toContain('取证')
      const preview = container!.querySelector('.dsh-log-list article')!.cloneNode(true) as HTMLElement
      preview.querySelectorAll('details').forEach(item => item.remove())
      expect(preview.textContent).not.toContain(originalObjective)
      expect(preview.textContent).not.toContain('Original task detail.')
      expect(preview.textContent).not.toContain('unfamiliar_requirement')
      expect(container!.querySelector<HTMLDetailsElement>('.dsh-original-record')!.open).toBe(false)
      expect(rawRecord.plan.objective).toBe(originalObjective)
      expect(rawRecord.plan.tasks[0].question).toBe('What changed in the original model question?')
      expect(rawRecord.plan.tasks[1].objective).toBe('Arbitrary English without a supplied translation.')
      expect(rawRecord.tool_invocations.map((item: { tool_name: string }) => item.tool_name)).toEqual(['dsh.internal.skill', 'market.cross_asset'])
    } else {
      const openReport = [...container!.querySelectorAll('button')].find(b => b.textContent?.includes('打开 Hub 研究报告'))!
      await act(async () => openReport.click())
      const expectedBack = `#/dsh-report?${new URLSearchParams({ run: runId, back })}`
      expect(onNavigate).toHaveBeenLastCalledWith('report', { run: runId, back: expectedBack })
      expect(rawRecord).toEqual(detail.action_summary)
      await act(async () => root.render(<AssetBackLink back={expectedBack} />))
      expect(container!.querySelector('a')?.textContent).toContain('返回 DSH 原生报告')
      expect(container!.querySelector('a')?.getAttribute('href')).toBe(expectedBack)
    }
    await act(async () => root.unmount()); client.clear()
  })

  it('keeps unknown DSH stage and error values in the original record without inventing labels', async () => {
    const trace = { sequence_no: 1, event_type: 'tool_failed', stage: 'custom_stage_v2', status: 'denied', occurred_at: '2026-10-01T00:00:00Z', summary: 'An unchanged model statement.', error_code: 'custom_error_v2' }
    vi.spyOn(api, 'researchRuns').mockResolvedValue([])
    vi.spyOn(api, 'researchRun').mockResolvedValue({ trace: [trace] } as unknown as ResearchRunDetailView)
    const { root, client } = await renderHistoryScene('dsh', new URLSearchParams({ run: 'run-unknown-stage' }))
    await waitForVisible(() => Boolean(container?.querySelector('.dsh-original-record')))
    expect(container!.querySelector('.dsh-log-list small')?.textContent).toContain('其他阶段 · 已拒绝执行')
    expect(container!.querySelector('.dsh-log-list small')?.textContent).not.toContain('custom_stage_v2')
    expect(container!.querySelector('.dsh-log-list small')?.textContent).not.toContain('custom_error_v2')
    expect(JSON.parse(container!.querySelector('.dsh-original-record pre')!.textContent!)).toEqual(trace)
    await act(async () => root.unmount()); client.clear()
  })

  it('retries only the linked DSH research without requiring the first Run list to be readable', async () => {
    vi.spyOn(api, 'researchRuns').mockRejectedValue(new Error('list_unavailable'))
    const read = vi.spyOn(api, 'researchRun').mockRejectedValueOnce(new Error('read_timeout')).mockResolvedValue({ run: { run_id: 'linked-run' }, action_summary: { rationale: 'Original rationale.' } } as unknown as ResearchRunDetailView)
    const back = '#/technical?run=linked-run'
    const { root, client, onNavigate } = await renderHistoryScene('dsh-report', new URLSearchParams({ run: 'linked-run', back }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('重新读取记录')))
    await act(async () => [...container!.querySelectorAll('button')].find(b => b.textContent === '重新读取记录')!.click())
    await waitForVisible(() => Boolean(container?.textContent?.includes('Original rationale.')))
    expect(read.mock.calls.map(([id]) => id)).toEqual(['linked-run', 'linked-run'])
    expect(container!.textContent).not.toContain('研究任务列表不可用')
    expect(onNavigate).not.toHaveBeenCalled()
    expect(container!.querySelector('a')?.getAttribute('href')).toBe(back)
    await act(async () => root.unmount()); client.clear()
  })

  it('keeps all ledger categories on an empty filtered page and clears filters without losing the parent', async () => {
    vi.spyOn(api, 'macroEventsPage').mockResolvedValue({ items: [], total: 0 })
    vi.spyOn(api, 'macroEventFamilies').mockResolvedValue(['fomc', 'ecb', 'boj'])
    const { root, client, onNavigate } = await renderHistoryScene('calendar-all', new URLSearchParams({ q: 'no match', family: 'fomc', offset: '50', back: '#/market' }))
    await waitForVisible(() => (container?.querySelectorAll('select option').length ?? 0) === 4)
    expect([...container!.querySelectorAll('select option')].map(o => (o as HTMLOptionElement).value)).toEqual(['all', 'fomc', 'ecb', 'boj'])
    expect(container?.textContent).toContain('没有匹配当前筛选的事件')
    await act(async () => {
      const select = container!.querySelector('select')!
      select.value = 'ecb'; select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onNavigate).toHaveBeenCalledWith('calendar-all', { q: 'no match', family: 'ecb', back: '#/market' })
    await act(async () => [...container!.querySelectorAll('button')].find(b => b.textContent === '清除筛选')!.click())
    expect(onNavigate).toHaveBeenLastCalledWith('calendar-all', { back: '#/market' })
    await act(async () => root.unmount()); client.clear()
  })

  it('explains incomplete categories and retries them while preserving the selected deep link', async () => {
    const page = vi.spyOn(api, 'macroEventsPage').mockResolvedValue({ items: [], total: 0 })
    const families = vi.spyOn(api, 'macroEventFamilies').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(['fomc', 'ecb'])
    const { root, client } = await renderHistoryScene('calendar-all', new URLSearchParams({ family: 'legacy-family' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('事件类别暂时无法完整读取')))
    expect(container!.querySelector('select')!.value).toBe('legacy-family')
    await act(async () => [...container!.querySelectorAll('button')].find(b => b.textContent === '刷新日历')!.click())
    await waitForVisible(() => (container?.querySelectorAll('select option').length ?? 0) === 4)
    expect(page).toHaveBeenCalledTimes(2)
    expect(families).toHaveBeenCalledTimes(2)
    expect(container?.textContent).not.toContain('事件类别暂时无法完整读取')
    expect(container!.querySelector('select')!.value).toBe('legacy-family')
    await act(async () => root.unmount()); client.clear()
  })

  it.each(['failed', 'cancelled'])('opens retained materials without retrying a %s task and preserves list filters', async (status) => {
    vi.spyOn(api, 'researchRun').mockResolvedValue({ run: { run_id: 'old-run', status, event_title: 'BTC研究' } } as ResearchRunDetailView)
    const retry = vi.spyOn(api, 'researchCommand')
    const back = '#/runs?q=old-run&status=failed&back=%23%2Fresearch'
    const { root, client, onNavigate } = await renderHistoryScene('failed', new URLSearchParams({ run: 'old-run', back }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('查看已保留的材料与调用记录')))
    await act(async () => [...container!.querySelectorAll('button')].find(b => b.textContent === '查看已保留的材料与调用记录')!.click())
    expect(onNavigate).toHaveBeenCalledWith('report', { run: 'old-run', back })
    expect(retry).not.toHaveBeenCalled()
    await act(async () => root.unmount()); client.clear()
  })

  it('shows final cancellation separately from an earlier tool failure and refreshes the current task', async () => {
    const detail = { run: { run_id: 'cancelled-run', status: 'cancelled', event_title: 'BTC研究', failure: { error_code: 'research_pit_violation', retryable: false }, stop_reason: { code: 'cancelled', detail: '用户主动停止了这次研究，原任务与已取得材料保留。' }, current_action: 'Earlier fetch failed.' } } as ResearchRunDetailView
    const read = vi.spyOn(api, 'researchRun').mockResolvedValue(detail)
    const { root, client } = await renderHistoryScene('failed', new URLSearchParams({ run: 'cancelled-run' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('用户主动停止了这次研究')))
    const notices = [...container!.querySelectorAll('.design-state-notice')].map(node => node.textContent)
    expect(notices.some(text => text?.includes('停止原因') && text.includes('用户主动停止'))).toBe(true)
    expect(container?.textContent).toContain('research_pit_violation')
    expect(container?.textContent).toContain('历史资料授权不自动继承')
    await act(async () => [...container!.querySelectorAll('button')].find(b => b.textContent === '刷新任务状态')!.click())
    await waitForVisible(() => read.mock.calls.length === 2)
    await act(async () => root.unmount()); client.clear()
  })

  it.each(['#/evidence?run=run-1&evidence=library%3Av1%3Apart%3A1', 'https://outside.example/'])('preserves only supported return routes while restoring a document stage: %s', async (requestedBack) => {
    const version = { version_id: 'v1', document_id: 'd1', status: 'ready', parse_receipt_ref: 'receipt', claim_count: 0, quality: { quality_status: 'ready_for_correction', issues: [] } } as unknown as ResearchDocumentVersion
    vi.spyOn(api, 'researchDocuments').mockResolvedValue([])
    vi.spyOn(api, 'researchDocumentVersion').mockResolvedValue(version)
    vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
    vi.spyOn(api, 'researchClaims').mockResolvedValue([])
    vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
    const { root, client } = await renderHistoryScene('document-review', new URLSearchParams({ version: 'v1', back: requestedBack }))
    await waitForVisible(() => window.location.hash.startsWith('#/document-ready?'))
    const expectedBack = requestedBack.startsWith('#/evidence?') ? requestedBack : '#/library'
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('back')).toBe(expectedBack)
    const returnLink = Array.from(container!.querySelectorAll('a')).find((link) => link.textContent?.includes(expectedBack === '#/library' ? '返回资料列表' : '返回证据详情'))
    expect(returnLink?.getAttribute('href')).toBe(expectedBack)
    act(() => root.unmount()); client.clear(); window.history.replaceState(null, '', '#/')
  })

  it('renders the explicit compose state instead of folding it into the home page', () => {
    const root = renderScene('compose')
    expect(container?.textContent).toContain('发起一次研究')
    expect(container?.textContent).toContain('开始分析')
    act(() => root.unmount())
  })

  it('restores unconfirmed compose text and identity after a page remount without automatic replay', async () => {
    const submit = vi.spyOn(api, 'submitResearch').mockRejectedValueOnce(new Error('请求超时')).mockResolvedValue({ schema_version: 'research-run-queued.v1', run_id: 'queued-run', event_id: 'event', status: 'queued', status_url: '/v1/runs/queued-run' })
    let { root, client, onNavigate } = await renderHistoryScene('compose', new URLSearchParams({ focus: 'BTC' }))
    const start = () => Array.from(container!.querySelectorAll('button')).find(b => b.textContent?.includes('开始分析'))!
    await act(async () => start().click())
    await waitForVisible(() => Boolean(container?.textContent?.includes('提交未确认')))
    expect(onNavigate).not.toHaveBeenCalled()
    expect(start().disabled).toBe(false)
    await act(async () => root.unmount()); client.clear(); container?.remove()
    ;({ root, client, onNavigate } = await renderHistoryScene('compose', new URLSearchParams()))
    expect(container?.querySelector('textarea')?.value).toBe('BTC')
    expect(container?.textContent).toContain('有一笔尚未确认的提交')
    expect(submit).toHaveBeenCalledTimes(1)
    await act(async () => start().click())
    await waitForVisible(() => onNavigate.mock.calls.length > 0)
    expect(submit.mock.calls).toHaveLength(2)
    expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1])
    expect(submit.mock.calls[0][1]).toMatch(/^research-submit:/)
    expect(onNavigate).toHaveBeenCalledWith('queued', { run: 'queued-run', back: '#/research' })
    await act(async () => root.unmount()); client.clear(); container?.remove()
    ;({ root, client } = await renderHistoryScene('compose', new URLSearchParams()))
    expect(container?.querySelector('textarea')?.value).toBe('')
    expect(container?.textContent).not.toContain('有一笔尚未确认的提交')
    await act(async () => root.unmount()); client.clear()
  })

  it('renders the production home dashboard shell with honest empty API placeholders', () => {
    const root = renderScene('home')
    expect(container?.textContent).toContain('Research Command Center')
    expect(container?.textContent).toContain('获取最新操作建议')
    expect(container?.textContent).toContain('所有状态、日历和进度均来自真实 API')
    act(() => root.unmount())
  })

  it('renders honest empty and degraded home states', () => {
    const emptyRoot = renderScene('home-empty')
    expect(container?.textContent).toContain('暂无研究任务')
    act(() => emptyRoot.unmount())
    const degradedRoot = renderScene('home-degraded')
    expect(container?.textContent).toContain('来源降级')
    expect(container?.textContent).toContain('交易权限')
    act(() => degradedRoot.unmount())
  })

  it('exposes the design system state board as a review route', () => {
    const root = renderScene('states')
    expect(container?.textContent).toContain('组件与状态规范')
    expect(container?.textContent).toContain('概率未校准')
    act(() => root.unmount())
  })

  it('keeps a one-to-one catalog for all 38 desktop design states', () => {
    expect(designCatalog).toHaveLength(38)
    expect(new Set(designCatalog.map((item) => item.id)).size).toBe(38)
    expect(designCatalog.map((item) => item.id)).toContain('graph-node')
    expect(designCatalog.map((item) => item.id)).toContain('dsh-report')
  })

  it('pages the filtered Run ledger on the server and preserves filters in the report return link', async () => {
    const run = { run_id: 'run-older', event_title: '较早的利率研究', status: 'failed', admission_origin: 'automatic', coverage: null, parent_run_id: null, created_at: '2025-01-01T00:00:00Z' } as ResearchRunView
    const page = vi.spyOn(api, 'researchRunsPage').mockResolvedValue({ items: [run], total: 120 })
    const { root, client, onNavigate } = await renderHistoryScene('runs', new URLSearchParams({ q: '利率', status: 'failed', origin: 'automatic', offset: '50' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('较早的利率研究')))
    expect(page).toHaveBeenCalledWith({ limit: 50, offset: 50, q: '利率', status: 'failed', origin: 'automatic' })
    expect(container?.textContent).toContain('较早的利率研究')
    expect(container?.textContent).toContain('覆盖率无法核验')
    expect(container?.textContent).toContain('缺口未核验')
    expect(container?.textContent).not.toContain('0 个缺口')
    expect(container?.textContent).toContain('第 2 页')
    const next = Array.from(container!.querySelectorAll('button')).find((button) => button.textContent === '下一页')
    await act(async () => { next?.click() })
    expect(onNavigate).toHaveBeenCalledWith('runs', { q: '利率', status: 'failed', origin: 'automatic', back: '#/research', offset: '100' })
    const row = container!.querySelector<HTMLButtonElement>('.design-table-link')!
    await act(async () => { row.click() })
    expect(onNavigate).toHaveBeenCalledWith('failed', expect.objectContaining({ run: 'run-older', back: '#/runs?q=%E5%88%A9%E7%8E%87&status=failed&origin=automatic&back=%23%2Fresearch&offset=50' }))
    await act(async () => { root.unmount() })
    client.clear()
  })

  it.each([
    [1, ['soft'], '必需缺口 0 个 · 补充缺口 1 个'],
    [0.5, ['hard', 'soft', 'soft'], '必需缺口 1 个 · 补充缺口 2 个'],
    [0, ['hard'], '必需缺口 1 个 · 补充缺口 0 个'],
  ] as const)('labels required evidence coverage %s separately from gaps by their recorded importance', async (ratio, importance, expected) => {
    const run = historyCoverageRun(ratio, [...importance])
    const original = structuredClone(run)
    vi.spyOn(api, 'researchRunsPage').mockResolvedValue({ items: [run], total: 1 })
    const { root, client, onNavigate } = await renderHistoryScene('runs', new URLSearchParams({ q: '覆盖', back: '#/management' }))
    await waitForVisible(() => Boolean(container?.querySelector('tbody tr')))
    expect([...container!.querySelectorAll('th')].map(cell => cell.textContent)).toContain('必需证据覆盖')
    const coverageCell = container!.querySelectorAll('tbody td')[3]
    expect(coverageCell.querySelector('.coverage-chip')?.textContent).toBe(`${ratio * 100}%`)
    expect(coverageCell.querySelector('small')?.textContent).toBe(expected)
    expect(run).toEqual(original)
    await act(async () => container!.querySelector<HTMLButtonElement>('.design-table-link')!.click())
    expect(onNavigate).toHaveBeenCalledWith('report', {
      run: 'coverage-run', back: '#/runs?q=%E8%A6%86%E7%9B%96&back=%23%2Fmanagement',
    })
    await act(async () => root.unmount()); client.clear()
  })

  it.each([
    [null, '覆盖率无法核验'],
    ['unavailable', '覆盖率无法核验'],
    ['round_request_only', '完整任务要求未保存'],
    ['invalid', '原始要求无法核验'],
  ] as const)('keeps historical required evidence coverage unknown when the saved basis is %s', async (basis, explanation) => {
    const run = historyCoverageRun(1, [], basis)
    vi.spyOn(api, 'researchRunsPage').mockResolvedValue({ items: [run], total: 1 })
    const { root, client } = await renderHistoryScene('runs', new URLSearchParams())
    await waitForVisible(() => Boolean(container?.querySelector('tbody tr')))
    const coverageCell = container!.querySelectorAll('tbody td')[3]
    expect(coverageCell.querySelector('.coverage-chip')?.textContent).toBe(explanation)
    expect(coverageCell.querySelector('small')?.textContent).toBe('缺口未核验')
    expect(coverageCell.textContent).not.toContain('100%')
    expect(coverageCell.textContent).not.toContain('0 个')
    await act(async () => root.unmount()); client.clear()
  })

  it('opens a historical event by ID even when that event is absent from the current page', async () => {
    const event = { macro_event_id: 'event-older', family: '历史发布', scheduled_at: '2025-01-01T12:00:00Z', updated_at: '2025-01-01T12:01:00Z', source_id: 'official', source_authority: 'official', source_url: null, prior_value: null, consensus_value: null, actual_value: 2, surprise: null, revision_flag: false, status: 'released' } as MacroEventView
    vi.spyOn(api, 'macroEventsPage').mockResolvedValue({ items: [], total: 251 })
    const detail = vi.spyOn(api, 'macroEvent').mockResolvedValue(event)
    const { root, client, onNavigate } = await renderHistoryScene('event', new URLSearchParams({ event: 'event-older', scope: 'history', offset: '200' }))
    await waitForVisible(() => Boolean(container?.querySelector('[aria-label="宏观事件详情"] h2')))
    expect(detail).toHaveBeenCalledWith('event-older')
    expect(container!.querySelector('[aria-label="宏观事件详情"] h2')?.textContent).toBe('历史发布')
    const close = container!.querySelector<HTMLButtonElement>('[aria-label="关闭事件详情"]')!
    await act(async () => { close.click() })
    expect(onNavigate).toHaveBeenCalledWith('calendar-history', { q: undefined, family: undefined, offset: '200' })
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('pages and filters the complete calendar ledger through the API before opening an event', async () => {
    const event = { macro_event_id: 'event-page-three', family: '利率决议', scheduled_at: '2025-01-01T12:00:00Z', updated_at: '2025-01-01T12:01:00Z', source_id: 'official', source_authority: 'official', source_url: null, prior_value: null, consensus_value: null, actual_value: 2, surprise: null, revision_flag: false, status: 'released' } as MacroEventView
    const page = vi.spyOn(api, 'macroEventsPage').mockResolvedValue({ items: [event], total: 151 })
    const { root, client, onNavigate } = await renderHistoryScene('calendar-history', new URLSearchParams({ q: '利率', family: '利率决议', offset: '100', back: '#/market' }))
    await waitForVisible(() => Boolean(container?.querySelector('.design-event-row')))
    expect(page).toHaveBeenCalledWith({ limit: 50, offset: 100, scope: 'history', q: '利率', family: '利率决议' })
    expect(container?.textContent).toContain('第 3 页')
    const next = Array.from(container!.querySelectorAll('button')).find((button) => button.textContent === '下一页')
    await act(async () => { next?.click() })
    expect(onNavigate).toHaveBeenCalledWith('calendar-history', { q: '利率', family: '利率决议', offset: '150', back: '#/market' })
    const row = container!.querySelector<HTMLButtonElement>('.design-event-row')!
    await act(async () => { row.click() })
    expect(onNavigate).toHaveBeenCalledWith('event', expect.objectContaining({ event: 'event-page-three', scope: 'history', offset: '100', back: '#/calendar-history?q=%E5%88%A9%E7%8E%87&family=%E5%88%A9%E7%8E%87%E5%86%B3%E8%AE%AE&back=%23%2Fmarket&offset=100' }))
    await act(async () => { root.unmount() })
    client.clear()
  })

  it.each([
    ['dsh_model_configuration_changed_during_run', '这次任务的模型配置与首次提交时不同'],
    ['dsh_model_binding_unavailable', '模型执行前的绑定校验未通过'],
    ['dsh_model_binding_writer_busy', '模型调用前取得数据库写锁时暂时繁忙'],
    ['dsh_model_binding_corrupt', '已保存的模型配置凭据校验失败'],
    ['dsh_provider_input_transport_timeout', '模型输入提交超时，本次未生成完整报告'],
    ['research_observation_unavailable', '执行原始记录尚未核验通过，本次研究未完成'],
    ['research_terminal_original_unavailable', '最终结果的原始回执暂时无法核验'],
    ['dsh_terminal_result_original_mismatch', '返回结果与原始执行记录不一致'],
    ['research_worker_failed', '后台研究执行中断，本次未生成完整报告'],
  ])('explains the model submission refusal %s without claiming a result', async (code, explanation) => {
    vi.spyOn(api, 'researchRun').mockResolvedValue({ run: {
      run_id: 'blocked-model-run', event_title: '模型配置核查', status: 'failed',
      failure: { error_code: code, retryable: false }, stop_reason: null,
    } } as unknown as ResearchRunDetailView)
    const { root, client } = await renderHistoryScene('failed', new URLSearchParams({ run: 'blocked-model-run' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes(explanation)))
    expect(container?.textContent).toContain(explanation)
    expect(container?.textContent).not.toContain('研究结果已保存')
    await act(async () => root.unmount()); client.clear()
  })

  it('preserves the failed ETH spot task scope without showing the BTC template horizons', async () => {
    const focus = 'ETH 现货未来 24 小时风险研究：核对宏观因素、分钟路径和主要反方。'
    const original = { research_focus: focus, run: {
      run_id: 'failed-eth-spot', event_title: '以太坊现货风险研究', status: 'failed',
      failure: { error_code: 'provider_timeout', retryable: false }, stop_reason: null,
    } } as unknown as ResearchRunDetailView
    const read = vi.spyOn(api, 'researchRun').mockResolvedValue(original)
    vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    const command = vi.spyOn(api, 'researchCommand')
    const submit = vi.spyOn(api, 'submitResearch')
    const { root, client } = await renderHistoryScene('failed', new URLSearchParams({ run: 'failed-eth-spot', back: '#/home' }))
    try {
      await waitForVisible(() => container?.querySelector<HTMLTextAreaElement>('textarea')?.value === focus)
      expect(read).toHaveBeenCalledWith('failed-eth-spot')
      expect(container?.textContent).toContain('原任务 · 市场研究')
      expect(container?.textContent).toContain('原任务范围核对')
      expect(container?.textContent).toContain('资产、市场和分析期限沿用原任务，具体范围以已保存记录为准。')
      expect(container?.textContent).not.toContain('BTC · 宏观研究')
      expect(container?.textContent).not.toContain('7 天日线参考')
      expect(container?.textContent).not.toContain('72 小时研究判断')
      expect(container?.querySelector<HTMLTextAreaElement>('textarea')?.readOnly).toBe(true)
      expect(original.research_focus).toBe(focus)
      expect(command).not.toHaveBeenCalled()
      expect(submit).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()); client.clear() }
  })

  it('retries the original failed Run through the audited command and opens its child Run', async () => {
    const original = { run: { run_id: 'failed-run', event_title: '原始研究任务', status: 'failed', failure: { error_code: 'provider_timeout', retryable: false }, stop_reason: { code: 'source_unavailable', detail: '来源超时' } } } as unknown as ResearchRunDetailView
    vi.spyOn(api, 'researchRun').mockResolvedValue(original)
    let receipt: Awaited<ReturnType<typeof api.researchCommandReceipt>> = null
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => receipt)
    const command = vi.spyOn(api, 'researchCommand').mockRejectedValueOnce(new Error('connection lost')).mockImplementation(async (id, payload, owner) => {
      const result = { schema_version: 'research-run-command-result.v1' as const, request_id: payload.request_id, command: payload.command, source_run_id: id, target_run_id: 'child-run', status: 'accepted' as const, created_at: '2025-01-01T00:00:00Z' }
      receipt = { schema_version: 'research-run-command-receipt.v1', request_id: payload.request_id, source_run_id: id, owner_id: owner, command: payload, result }
      return result
    })
    const submit = vi.spyOn(api, 'submitResearch').mockRejectedValue(new Error('must not submit text'))
    let { root, client, onNavigate } = await renderHistoryScene('failed', new URLSearchParams({ run: 'failed-run', back: '#/studies?study=s1&q=BTC' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('来源超时')))
    expect(container?.textContent).toContain('该错误不支持自动重试')
    expect(container?.querySelector<HTMLTextAreaElement>('textarea')?.readOnly).toBe(true)
    const retry = Array.from(container!.querySelectorAll('button')).find((button) => button.textContent?.includes('重新尝试研究'))!
    expect(retry.disabled).toBe(false)
    await act(async () => { retry.click() })
    await waitForVisible(() => Boolean(container?.textContent?.includes('尚未确认这次任务操作')))
    expect(onNavigate).not.toHaveBeenCalled()
    expect(retry.disabled).toBe(true)
    await act(async () => root.unmount()); client.clear(); container?.remove()
    ;({ root, client, onNavigate } = await renderHistoryScene('failed', new URLSearchParams({ run: 'failed-run', back: '#/studies?study=s1&q=BTC' })))
    await waitForVisible(() => Boolean(container?.textContent?.includes('来源超时')))
    expect(command).toHaveBeenCalledTimes(1)
    await act(async () => Array.from(container!.querySelectorAll('button')).find(b => b.textContent === '核对并按原操作重试')!.click())
    await waitForVisible(() => onNavigate.mock.calls.length > 0)
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1])
    expect(command).toHaveBeenCalledWith('failed-run', expect.objectContaining({ schema_version: 'research-run-command.v1', command: 'retry', reason: expect.any(String) }), expect.any(String))
    expect(submit).not.toHaveBeenCalled()
    expect(onNavigate).toHaveBeenCalledWith('queued', { run: 'child-run', back: '#/studies?study=s1&q=BTC' })
    expect(container?.querySelector('a[href="#/studies?study=s1&q=BTC"]')).not.toBeNull()
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('disables retry when the source Run is missing or no longer in a retryable status', async () => {
    const command = vi.spyOn(api, 'researchCommand')
    const missing = await renderHistoryScene('failed', new URLSearchParams())
    expect(container?.textContent).toContain('链接缺少原任务')
    expect(Array.from(container!.querySelectorAll('button')).find((button) => button.textContent?.includes('重新尝试研究'))?.disabled).toBe(true)
    await act(async () => { missing.root.unmount() })
    missing.client.clear()
    container?.remove()
    vi.spyOn(api, 'researchRun').mockResolvedValue({ run: { run_id: 'completed-run', event_title: '已完成任务', status: 'completed' } } as unknown as ResearchRunDetailView)
    const completed = await renderHistoryScene('failed', new URLSearchParams({ run: 'completed-run' }))
    await waitForVisible(() => Boolean(container?.textContent?.includes('此任务当前不能重试')))
    expect(Array.from(container!.querySelectorAll('button')).find((button) => button.textContent?.includes('重新尝试研究'))?.disabled).toBe(true)
    expect(command).not.toHaveBeenCalled()
    await act(async () => { completed.root.unmount() })
    completed.client.clear()
  })
})


it('preserves an allowed parent route through compose and successful submission', async () => {
  vi.spyOn(api, 'submitResearch').mockResolvedValue({ schema_version: 'research-run-queued.v1', run_id: 'new-run', event_id: 'event', status: 'queued', status_url: '/v1/runs/new-run' })
  const back = '#/research?q=原有筛选'
  const { root, client, onNavigate } = await renderHistoryScene('compose', new URLSearchParams({ back }))
  expect(Array.from(container!.querySelectorAll('a')).find(a => a.textContent?.includes('返回研究'))?.getAttribute('href')).toBe(back)
  await act(async () => Array.from(container!.querySelectorAll('button')).find(b => b.textContent === '开始分析')!.click())
  await waitForVisible(() => onNavigate.mock.calls.length > 0)
  expect(onNavigate).toHaveBeenCalledWith('queued', { run: 'new-run', back })
  await act(async () => root.unmount()); client.clear()
})

function preparationWaitingDetail() {
  const at = '2026-10-02T07:00:00Z', runId = 'run-pending'
  const summary = '执行准备尚未确认，原任务与材料保留；等待原租约到期后核对检查点恢复。'
  const error = { error_code: 'research_method_binding_failed', origin: 'orchestration', cause_code: 'cancellation_projection_wait_elapsed', capability_id: null, tool_call_id: null, retryable: true, deadline_ms: 1000, provider_attempts: [] }
  return researchRunDetailViewSchema.parse({
    schema_version: 'research-run-detail-view.v1',
    run: { schema_version: 'research-run-view.v2', run_id: runId, event_id: 'event-pending', event_title: '原研究关注点', admission_origin: 'manual', priority: 'normal', status: 'researching', stage: 'planning', runtime_id: 'dsh-web', profile_ref: null, current_round: 0, budget: null, coverage: null, current_action: summary, stop_reason: null, failure: error, latest_sequence_no: 4, artifact_id: null, available_at: null, parent_run_id: null, created_at: at, updated_at: at },
    research_focus: null, trigger_snapshot: null, decision_snapshot: null, evidence: [], rounds: [], causal_case: null, horizons: [],
    trace: [{ schema_version: 'research-trace-event.v1', run_id: runId, research_session_id: `product:${runId}`, sequence_no: 4, event_type: 'session_stopped', occurred_at: at, stage: 'planning', summary, reference_type: 'run', reference_id: runId, status: 'running', error_code: error.error_code, error }],
    action_summary: { schema_version: 'research-action-summary.v1', recommendation: 'unavailable', headline: '研究进行中，暂不能形成判断', rationale: '原运行记录等待核对。', reliability: 'unavailable', trade_authorized: false, artifact_status: 'pending', data_status: 'insufficient', accepted_evidence_count: 0, stale_evidence_count: 0, rejected_evidence_count: 0, blocking_gap_ids: [], warning_codes: [error.error_code], supporting_evidence_refs: [], next_review_at: null },
    process_steps: [{ step_id: 'step-plan', order: 1, kind: 'plan', label: '制定研究计划', status: 'running', objective: '核对原任务', outcome: '等待恢复核对', evidence_refs: [], tool_call_refs: [], warning_codes: [], occurred_at: at }],
    total_tool_calls: 0, total_subagents: 0, total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null,
  })
}

it('presents preparation waiting as a saved DSH trace with complete closed original and the same return path', async () => {
  const sample = preparationWaitingDetail(), back = '#/technical?run=run-pending&back=%23%2Fruns'
  vi.spyOn(api, 'researchRuns').mockResolvedValue([])
  const read = vi.spyOn(api, 'researchRun').mockResolvedValue(sample), submit = vi.spyOn(api, 'submitResearch')
  const { root, client, onNavigate } = await renderHistoryScene('dsh', new URLSearchParams({ run: sample.run.run_id, back }))
  try {
    await waitForVisible(() => Boolean(container?.querySelector('.dsh-original-record')))
    expect(container!.querySelector('.dsh-log-list strong')?.textContent).toBe('执行准备尚未确认')
    const audit = container!.querySelector<HTMLDetailsElement>('.dsh-original-record')!
    expect(audit.open).toBe(false)
    await act(async () => audit.querySelector<HTMLElement>('summary')!.click())
    expect(JSON.parse(audit.querySelector('pre')!.textContent!)).toEqual(sample.trace[0])
    expect(container!.querySelector('a')?.getAttribute('href')).toBe(back)
    expect(read).toHaveBeenCalledWith('run-pending'); expect(submit).not.toHaveBeenCalled(); expect(onNavigate).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear() }
})
