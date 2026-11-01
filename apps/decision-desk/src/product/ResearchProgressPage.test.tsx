// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchRunDetailViewSchema, runViewSchema } from '../api/schemas'
import { api } from '../api/client'
import type { ResearchRunCommandReceipt, ResearchRunDetailView, RunView } from '../api/types'
import { commandReceipt } from '../research/commandTestFixtures'
import { readPendingCommand } from '../research/commandRequests'
import { ResearchProgressPage, researchTaskScene, researchTitle } from './ResearchProgressPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const at = '2026-09-29T03:00:00Z'
const roots: Root[] = [], clients: QueryClient[] = [], containers: HTMLElement[] = []
beforeEach(() => {
  vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null)
  vi.spyOn(api, 'run').mockRejectedValue(new Error('The basic RunView must be explicitly mocked'))
  vi.spyOn(api, 'researchRun').mockRejectedValue(new Error('Details must be explicitly requested and mocked'))
})
afterEach(async () => {
  await act(async () => roots.splice(0).forEach(root => root.unmount()))
  clients.splice(0).forEach(client => client.clear()); containers.splice(0).forEach(container => container.remove())
  sessionStorage.clear(); vi.useRealTimers(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/')
})
const basic = (status: RunView['status'] = 'running', overrides: Partial<RunView> = {}) => runViewSchema.parse({
  run_id: 'run-live', event_id: 'event-live', status, strategy_version: 'research.v1', runtime_version: 'engineering',
  snapshot_id: null, artifact_id: null, created_at: at, updated_at: at, finished_at: ['running', 'admitted'].includes(status) ? null : at,
  latency_ms: null, cost_usd: null, error_code: null, headline: null, gate_status: null, ...overrides,
})
const detail = (status: ResearchRunDetailView['run']['status'], artifact: string | null = null) => {
  const saved = preparationWaitingDetail()
  return researchRunDetailViewSchema.parse({ ...saved, run: { ...saved.run, run_id: 'run-live', event_id: 'event-live',
    event_title: 'BTC 最新操作建议 · 美元与资金流', status, stage: status === 'researching' ? 'acquiring_evidence' : 'done',
    artifact_id: artifact, created_at: at, updated_at: at, failure: null, current_action: null }, trace: [],
    process_steps: [{ ...saved.process_steps[0], status: status === 'researching' ? 'running' : 'completed', outcome: '工程UI样本步骤', occurred_at: at }], total_tool_calls: 4 })
}
async function settle() {
  if (vi.isFakeTimers()) await act(async () => { await vi.advanceTimersByTimeAsync(30) })
  else await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
}
function button(container: HTMLElement, text: string) { return [...container.querySelectorAll('button')].find(item => item.textContent === text)! }
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
async function renderPage(runId = 'run-live', back?: string) {
  const container = document.createElement('div'); document.body.append(container); containers.push(container)
  const root = createRoot(container); roots.push(root)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  const navigate = vi.fn()
  const rerender = async (nextRun = runId, nextBack = back) => {
    await act(async () => root.render(<QueryClientProvider client={client}><ResearchProgressPage runId={nextRun} back={nextBack} onNavigate={navigate} /></QueryClientProvider>))
    await settle()
  }
  await rerender()
  return { container, root, client, navigate, rerender }
}
async function unmount(root: Root) { await act(async () => root.unmount()); roots.splice(roots.indexOf(root), 1) }

describe('live research progress', () => {
  it.each([
    ['running', false, 'queued'], ['researching', false, 'queued'], ['admitted', false, 'queued'],
    ['cancelled', false, 'failed'], ['failed', false, 'failed'], ['completed', false, 'queued'],
    ['research_only', true, 'report'], ['rejected', true, 'report'],
  ])('opens the appropriate task destination for %s with report %s', (status, report, scene) => {
    expect(researchTaskScene(String(status), Boolean(report))).toBe(scene)
  })

  it('does not present cached active state as current after a basic refresh fails, stops polling and can reconnect', async () => {
    vi.useFakeTimers()
    const read = vi.mocked(api.run).mockResolvedValueOnce(basic()).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(basic('completed', { artifact_id: 'saved' }))
    const page = await renderPage('run-live', '#/studies?study=parent')
    expect(page.container.textContent).toContain('研究正在执行')
    await act(async () => button(page.container, '刷新进度').click()); await settle()
    expect(page.container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
    expect(page.container.textContent).toContain('最近一次读取的任务记录')
    expect(button(page.container, '停止本次研究').disabled).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(9_100) })
    expect(read).toHaveBeenCalledTimes(2)
    expect(page.container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
    await act(async () => button(page.container, '重新连接').click()); await settle()
    expect(page.container.textContent).toContain('研究已结束，有报告记录待核对')
    await act(async () => button(page.container, '阅读研究报告').click())
    expect(page.navigate).toHaveBeenCalledWith('report', { run: 'run-live', back: '#/studies?study=parent' })
    expect(read).toHaveBeenCalledTimes(3)
    expect(api.researchRun).not.toHaveBeenCalled()
  })

  it('polls only basic saved state and exposes its report without reading details or resubmitting', async () => {
    vi.useFakeTimers()
    const read = vi.mocked(api.run).mockResolvedValueOnce(basic()).mockResolvedValue(basic('completed', { artifact_id: 'art-live' }))
    const submit = vi.spyOn(api, 'submitResearch')
    const page = await renderPage()
    expect(page.container.textContent).toContain('研究正在执行')
    expect(page.container.textContent).toContain('材料条目：未读取；工具调用：未读取')
    expect(page.container.textContent).not.toContain('已保留证据0')
    expect(api.researchRun).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) }); await settle()
    expect(read).toHaveBeenCalledTimes(2)
    expect(page.container.textContent).toContain('研究已结束，有报告记录待核对')
    await act(async () => { await vi.advanceTimersByTimeAsync(6_100) })
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => button(page.container, '阅读研究报告').click())
    expect(page.navigate).toHaveBeenCalledWith('report', { run: 'run-live' })
    expect(api.researchRun).not.toHaveBeenCalled(); expect(submit).not.toHaveBeenCalled()
  })

  it('keeps a failed basic task visibly failed and routes to its recovery record', async () => {
    vi.mocked(api.run).mockResolvedValue(basic('failed', { error_code: 'context_selection_deadline_elapsed' }))
    const page = await renderPage()
    expect(page.container.textContent).toContain('本次研究未完成')
    expect(page.container.textContent).toContain('context_selection_deadline_elapsed')
    await act(async () => button(page.container, '查看原因与恢复').click())
    expect(page.navigate).toHaveBeenCalledWith('failed', { run: 'run-live' })
    expect(api.researchRun).not.toHaveBeenCalled()
  })

  it('preserves the actual topic instead of assigning every task the same label', () => {
    expect(researchTitle(detail('completed').run, '美元与资金流')).toBe('美元与资金流')
    expect(researchTitle({ event_title: 'BTC 最新操作建议' }, '')).toBe('BTC 与宏观快照研究')
    const custom = 'BTC 最新操作建议 · 用户自定义标题'
    expect(researchTitle({ event_title: custom }, null)).toBe(custom)
  })
})

it('keeps the full saved focus accessible in explicitly requested details', async () => {
  const value = detail('completed', 'saved')
  value.research_focus = '关注第一段\n' + '公开来源核查、'.repeat(30) + '最后的失效条件'
  value.run.event_title = 'BTC 最新操作建议 · ' + value.research_focus.slice(0, 60) + '...'
  vi.mocked(api.run).mockResolvedValue(basic('completed', { artifact_id: 'saved', headline: value.run.event_title }))
  vi.mocked(api.researchRun).mockResolvedValue(value)
  const page = await renderPage()
  expect(Array.from(page.container.querySelector('h2')!.textContent!).length).toBeLessThanOrEqual(36)
  expect(api.researchRun).not.toHaveBeenCalled()
  await act(async () => button(page.container, '展开后台工作与材料记录').click()); await settle()
  const focus = page.container.querySelector<HTMLDetailsElement>('.research-focus')!
  expect(focus.open).toBe(false)
  expect(focus.querySelector('p')?.textContent).toBe(value.research_focus)
  expect(focus.querySelector('summary')?.textContent).toBe('查看研究关注点')
  expect(api.researchRun).toHaveBeenCalledTimes(1)
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

it('presents preparation waiting only in requested records without promising report generation or resubmitting', async () => {
  const sample = preparationWaitingDetail()
  vi.mocked(api.run).mockResolvedValue(basic('running', { run_id: 'run-pending', updated_at: sample.run.updated_at }))
  vi.mocked(api.researchRun).mockResolvedValue(sample)
  const submit = vi.spyOn(api, 'submitResearch'), page = await renderPage('run-pending', '#/runs?status=researching')
  expect(page.container.querySelector('h1')?.textContent).toBe('研究正在执行')
  expect(page.container.textContent).not.toContain('等待原租约到期后核对检查点恢复')
  expect(api.researchRun).not.toHaveBeenCalled()
  await act(async () => button(page.container, '展开后台工作与材料记录').click()); await settle()
  expect(page.container.querySelector('[aria-label="读取后台工作与材料记录"]')?.textContent).toContain('等待原租约到期后核对检查点恢复')
  expect(page.container.querySelector('.product-hero')?.textContent).not.toContain('生成报告')
  await act(async () => button(page.container, '查看已收集的证据').click())
  expect(page.navigate).toHaveBeenCalledWith('report', { run: 'run-pending', focus: 'sources', back: '#/runs?status=researching' })
  expect(api.researchRun).toHaveBeenCalledTimes(1); expect(submit).not.toHaveBeenCalled()
})

it('does not claim current preparation waiting from a cached detail snapshot after a basic refresh error', async () => {
  const sample = preparationWaitingDetail()
  const read = vi.mocked(api.run).mockResolvedValueOnce(basic('running', { run_id: 'run-pending', updated_at: sample.run.updated_at })).mockRejectedValue(new Error('controlled offline'))
  vi.mocked(api.researchRun).mockResolvedValue(sample)
  const page = await renderPage('run-pending')
  await act(async () => button(page.container, '展开后台工作与材料记录').click()); await settle()
  await act(async () => button(page.container, '刷新进度').click()); await settle()
  expect(page.container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
  expect(page.container.querySelector('.product-hero')?.textContent).toContain('最近一次读取的任务记录')
  expect(page.container.querySelector('.product-hero')?.textContent).not.toContain('等待原租约到期')
  expect(page.container.textContent).toContain('尚未确认与最新基础状态一致')
  expect(read).toHaveBeenCalledTimes(2); expect(api.researchRun).toHaveBeenCalledTimes(1)
})

it('keeps basic updates and report navigation while one explicitly requested detail read is slow or fails', async () => {
  vi.useFakeTimers()
  const read = vi.mocked(api.run).mockResolvedValueOnce(basic()).mockResolvedValue(basic('completed', { artifact_id: 'saved', updated_at: '2026-09-29T03:01:00Z' }))
  const slow = deferred<ResearchRunDetailView>()
  vi.mocked(api.researchRun).mockReturnValue(slow.promise)
  const submit = vi.spyOn(api, 'submitResearch'), page = await renderPage('run-live', '#/home')
  expect(api.researchRun).not.toHaveBeenCalled()
  await act(async () => button(page.container, '展开后台工作与材料记录').click()); await settle()
  expect(page.container.textContent).toContain('正在核验完整后台记录')
  await act(async () => { await vi.advanceTimersByTimeAsync(3_100) }); await settle()
  expect(read).toHaveBeenCalledTimes(2)
  expect(page.container.querySelector('h1')?.textContent).toBe('研究已结束，有报告记录待核对')
  expect(button(page.container, '刷新进度').disabled).toBe(false)
  await act(async () => slow.reject(new Error('controlled detail timeout'))); await settle()
  expect(page.container.textContent).toContain('后台工作与材料记录读取失败')
  expect(page.container.querySelector('h1')?.textContent).toBe('研究已结束，有报告记录待核对')
  await act(async () => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')); await vi.advanceTimersByTimeAsync(6_100) })
  expect(api.researchRun).toHaveBeenCalledTimes(1)
  await act(async () => button(page.container, '阅读研究报告').click())
  expect(page.navigate).toHaveBeenCalledWith('report', { run: 'run-live', back: '#/home' })
  expect(submit).not.toHaveBeenCalled()
})

it.each([
  ['non-research ledger', { strategy_version: 'evolution.v1' }],
  ['different Run', { run_id: 'run-other' }],
])('fails closed on %s instead of exposing a research stop or report', async (_name, overrides) => {
  vi.mocked(api.run).mockResolvedValue(basic('running', { ...overrides, artifact_id: 'untrusted-artifact' }))
  const post = vi.spyOn(api, 'researchCommand'), page = await renderPage()
  expect(page.container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
  expect(page.container.querySelector('.product-hero')).toBeNull()
  expect(button(page.container, '停止本次研究')).toBeUndefined()
  expect(button(page.container, '阅读研究报告')).toBeUndefined()
  expect(post).not.toHaveBeenCalled(); expect(api.researchRun).not.toHaveBeenCalled()
})

it('keeps a missing Run route readable without requesting progress or exposing stop', async () => {
  const container = document.createElement('div'); document.body.append(container); containers.push(container)
  const root = createRoot(container); roots.push(root)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchProgressPage onNavigate={vi.fn()} /></QueryClientProvider>))
  await settle()
  expect(container.querySelector('h1')?.textContent).toBe('链接缺少研究任务')
  expect(api.run).not.toHaveBeenCalled(); expect(api.researchRun).not.toHaveBeenCalled()
  expect(button(container, '停止本次研究')).toBeUndefined()
})

it('keeps a late detail for another route from replacing the current Run and its recovery target', async () => {
  vi.mocked(api.run).mockImplementation(async id => basic(id === 'run-b' ? 'failed' : 'running', { run_id: id }))
  const slow = deferred<ResearchRunDetailView>(); vi.mocked(api.researchRun).mockReturnValue(slow.promise)
  const submit = vi.spyOn(api, 'submitResearch'), page = await renderPage('run-live', '#/home')
  await act(async () => button(page.container, '展开后台工作与材料记录').click()); await settle()
  await page.rerender('run-b')
  const a = detail('researching'); a.research_focus = 'only-A-focus'
  await act(async () => slow.resolve(a)); await settle()
  expect(page.container.querySelector('h1')?.textContent).toBe('本次研究未完成')
  expect(page.container.textContent).not.toContain('only-A-focus')
  await act(async () => button(page.container, '查看原因与恢复').click())
  expect(page.navigate).toHaveBeenCalledWith('failed', { run: 'run-b', back: '#/home' })
  expect(api.researchRun).toHaveBeenCalledTimes(1); expect(submit).not.toHaveBeenCalled()
})

it('preserves the original owner-bound stop lock and recovers a lost response from the same receipt without another POST', async () => {
  let stopped = false, committed: ResearchRunCommandReceipt | null = null
  vi.mocked(api.run).mockImplementation(async () => basic(stopped ? 'cancelled' : 'running'))
  vi.mocked(api.researchLinkedCommandReceipt).mockImplementation(async () => committed)
  const lookup = deferred<ResearchRunCommandReceipt | null>()
  const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed ?? lookup.promise)
  const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, commandOwner) => {
    committed = commandReceipt(id, command, commandOwner); stopped = true
    throw new Error('engineering response withheld after commit')
  })
  const first = await renderPage()
  expect(button(first.container, '停止本次研究').disabled).toBe(false)
  await act(async () => button(first.container, '停止本次研究').click()); await settle()
  expect(button(first.container, '正在核对操作…').disabled).toBe(true)
  expect(post).not.toHaveBeenCalled()
  await act(async () => lookup.resolve(null)); await settle()
  const pending = readPendingCommand('run-live', owner)!
  expect(pending.intent.command.command).toBe('cancel')
  expect(pending.intent.command.reason).toBe('用户在研究进度页停止本次任务。')
  expect(post).toHaveBeenCalledTimes(1)
  expect(post).toHaveBeenCalledWith('run-live', pending.intent.command, owner)
  expect(button(first.container, '请先核对原操作').disabled).toBe(true)
  await unmount(first.root)
  const second = await renderPage(); await settle()
  expect(second.container.querySelector('[aria-label="任务操作回执"]')?.textContent).toContain('停止请求已确认')
  expect(second.container.querySelector('h1')?.textContent).toBe('本次研究已停止')
  expect(get).toHaveBeenLastCalledWith('run-live', pending.record.id, owner)
  expect(readPendingCommand('run-live', owner)).toBeNull()
  expect(post).toHaveBeenCalledTimes(1); expect(second.navigate).not.toHaveBeenCalled()
  expect(api.researchRun).not.toHaveBeenCalled()
})
