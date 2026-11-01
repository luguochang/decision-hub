// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiRequestError } from '../api/client'
import { ACTION_OWNER, actionPlansApi, actionIntentKey, readActionIntent, saveActionIntent, type ActionIntent } from '../api/actionPlans'
import { ActionPlanPanel } from './ActionPlanPanel'
import { ACTION_SCOPE, currentActionFixture, actionReceiptFixture, actionDetailFixture } from './actionPlanTestFixtures'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })

async function render(view = currentActionFixture()) {
  const current = vi.spyOn(actionPlansApi, 'current').mockResolvedValue(view)
  vi.spyOn(actionPlansApi, 'reviews').mockResolvedValue({ schema_version: 'action-review-view.v1', opportunity_id: view.decision!.opportunity_id, scope_id: ACTION_SCOPE, horizon: '24h', as_of: view.as_of, review: null })
  const command = vi.spyOn(actionPlansApi, 'command').mockImplementation(async intent => actionReceiptFixture(intent))
  const receipt = vi.spyOn(actionPlansApi, 'receipt').mockImplementation(async intent => actionReceiptFixture(intent))
  const navigate = vi.fn()
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  await act(async () => root.render(<QueryClientProvider client={client}><ActionPlanPanel scopeId={ACTION_SCOPE} onNavigate={navigate} /></QueryClientProvider>))
  await settle()
  return {
    host, current, command, receipt, navigate,
    choose: async (position: string) => { await act(async () => { const select = host.querySelector('select[aria-label="行动方案仓位"]') as HTMLSelectElement; select.value = position; select.dispatchEvent(new Event('change', { bubbles: true })) }); await settle() },
    click: async (label: string) => { const button = Array.from(host.querySelectorAll('button')).find(item => item.textContent === label); expect(button, label).toBeDefined(); await act(async () => button!.click()); await settle() },
    close: async () => { await act(async () => root.unmount()); client.clear(); host.remove() },
  }
}

it('requires a position, keeps the latest miss prominent and shows usable conditions/counterevidence', async () => {
  const page = await render()
  try {
    expect(page.host.textContent).toContain('最近一次研究未形成行动方案')
    expect(page.host.querySelector('[aria-label="所选仓位的行动方案"]')).toBeNull()
    expect(page.command).not.toHaveBeenCalled()
    await page.choose('flat')
    expect(page.host.textContent).toContain('满足条件后考虑买入')
    expect(page.host.textContent).toContain('未校准 · 暂无可用概率')
    expect(page.host.textContent).toContain('连续确认 1/2 次，尚未确认')
    expect(page.host.textContent).toContain('支持依据 1 项 · 反方依据 1 项')
    expect(page.host.textContent).toContain('需求回升可能抵消影响')
    expect(page.host.textContent).toContain('下方是此前保存的方案')
    await page.click('阅读原研究报告')
    expect(page.navigate).toHaveBeenCalledWith('report', { run: 'run-original', back: '#/home' })
    await page.choose('unknown')
    expect(page.host.textContent).toContain('等待更清晰的机会')
    expect(page.host.textContent).not.toContain('满足条件后考虑买入')
  } finally { await page.close() }
})

it('uses the selected branch probability and distinguishes subjective probability from calibration', async () => {
  const page = await render()
  try {
    await page.choose('long')
    expect(page.host.textContent).toContain('按条件减持')
    expect(page.host.textContent).toContain('61% · 主观估计，尚未校准')
    await page.choose('flat')
    expect(page.host.textContent).not.toContain('61%')
    expect(page.host.textContent).toContain('未校准 · 暂无可用概率')
  } finally { await page.close() }
})

it('opens the retained branch original report instead of the newer branch report', async () => {
  const view = currentActionFixture()
  view.decision!.id = 'decision-new'; view.decision!.run_id = 'run-new'
  view.branches[0].decision = { ...view.decision! }; view.branches[0].revision.decision_id = 'decision-new'
  const page = await render(view)
  try {
    await page.choose('long'); await page.click('阅读原研究报告')
    expect(page.navigate).toHaveBeenLastCalledWith('report', { run: 'run-original', back: '#/home' })
    await page.choose('flat'); await page.click('阅读原研究报告')
    expect(page.navigate).toHaveBeenLastCalledWith('report', { run: 'run-new', back: '#/home' })
  } finally { await page.close() }
})

it.each([true, false])('distinguishes a due review from expiry and a missing review time (due=%s)', async due => {
  const view = currentActionFixture()
  view.branches[0].next_review_at = due ? '2020-01-01T00:00:00Z' : null
  const page = await render(view)
  try {
    await page.choose('flat')
    if (due) {
      expect(page.host.textContent).toContain('已到复核时间')
      expect(page.host.textContent).toContain('方案仍在有效期内')
    } else {
      expect(page.host.textContent).not.toContain('已到复核时间')
    }
    expect(page.host.textContent).not.toContain('方案已到期')
    const pause = Array.from(page.host.querySelectorAll('button')).find(item => item.textContent === '暂停此方案监控')
    expect(pause?.disabled).toBe(false)
  } finally { await page.close() }
})

it('persists an uncertain pause and recovers its original key without a second POST', async () => {
  const page = await render()
  try {
    page.command.mockRejectedValueOnce(new ApiRequestError('lost response', 'network'))
    await page.choose('flat'); await page.click('暂停此方案监控')
    const original = page.command.mock.calls[0][0]
    expect(readActionIntent(actionIntentKey(ACTION_SCOPE, '24h'))).toEqual(original)
    expect(page.host.textContent).toContain('暂停监控结果待确认')
    await page.click('恢复原操作回执')
    expect(page.receipt).toHaveBeenCalledWith(original)
    expect(page.command).toHaveBeenCalledTimes(1)
    expect(readActionIntent(actionIntentKey(ACTION_SCOPE, '24h'))).toBeNull()
    expect(page.host.textContent).toContain('暂停监控已保存')
  } finally { await page.close() }
})

it('GET404 recovery retries the identical body and key, never changes preconditions', async () => {
  const page = await render()
  try {
    page.command.mockRejectedValueOnce(new ApiRequestError('timeout', 'timeout'))
    page.receipt.mockRejectedValue(new ApiRequestError('missing', 'http', 404))
    await page.choose('flat'); await page.click('我已了解此方案')
    const original: ActionIntent = page.command.mock.calls[0][0]
    await page.click('恢复原操作回执'); await page.click('重试原操作')
    expect(page.command).toHaveBeenCalledTimes(2)
    expect(page.command.mock.calls[1][0]).toEqual(original)
    expect(page.host.textContent).toContain('知悉回执已保存')
    expect(page.host.textContent).toContain('不代表成交')
  } finally { await page.close() }
})

it('records display only after a selected plan is actually visible and foreground', async () => {
  let callback: IntersectionObserverCallback | undefined
  class Observer {
    constructor(cb: IntersectionObserverCallback) { callback = cb }
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal('IntersectionObserver', Observer)
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  const page = await render()
  try {
    expect(callback).toBeUndefined()
    await page.choose('flat')
    await act(async () => callback!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)); await settle()
    expect(page.command).not.toHaveBeenCalled()
    visibility.mockReturnValue('visible')
    await act(async () => document.dispatchEvent(new Event('visibilitychange'))); await settle()
    expect(page.command).toHaveBeenCalledTimes(1)
    expect(page.command.mock.calls[0][0].command.operation).toBe('display')
    expect(page.host.textContent).toContain('显示回执已保存')
    expect(page.host.textContent).toContain('已知悉：尚无记录')
  } finally { await page.close() }
})

it('expired or failed current reads cannot enable pause, resume or acknowledge', async () => {
  const view = currentActionFixture(); view.branches[0].expired = true
  const page = await render(view)
  try {
    await page.choose('flat')
    for (const label of ['暂停此方案监控', '我已了解此方案']) {
      expect(Array.from(page.host.querySelectorAll('button')).find(item => item.textContent === label)?.disabled).toBe(true)
    }
    page.current.mockRejectedValue(new Error('storage unavailable'))
    await page.click('刷新行动方案')
    expect(page.host.querySelector('[aria-label="所选仓位的行动方案"]')).toBeNull()
    expect(page.command).not.toHaveBeenCalled()
  } finally { await page.close() }
})

it('reads the selected exact revision and paginates monitoring without changing it', async () => {
  const view = actionDetailFixture(); view.next_cursor = 'earlier'
  const detail = vi.spyOn(actionPlansApi, 'plan').mockResolvedValue(view)
  const page = await render()
  try {
    await page.choose('flat'); await page.click('查看版本与监测记录')
    expect(detail).toHaveBeenCalledWith('plan-flat', 'revision-flat', undefined, expect.any(AbortSignal))
    await page.click('查看更早的监测记录')
    expect(detail).toHaveBeenLastCalledWith('plan-flat', 'revision-flat', 'earlier', expect.any(AbortSignal))
  } finally { await page.close() }
})

it('recovers a saved command after remount without automatically resubmitting it', async () => {
  const original: ActionIntent = { owner_id: ACTION_OWNER, plan_id: 'plan-flat', command: {
    request_id: 'saved-before-reload', expected_revision_id: 'revision-flat', expected_state_version: 1,
    operation: 'ack', payload: {},
  } }
  saveActionIntent(actionIntentKey(ACTION_SCOPE, '24h'), original)
  const page = await render()
  try {
    expect(page.receipt).toHaveBeenCalledWith(original)
    expect(page.command).not.toHaveBeenCalled()
    expect(page.host.textContent).toContain('知悉回执已保存')
  } finally { await page.close() }
})

it('does not POST if original intent storage is unavailable', async () => {
  const page = await render()
  try {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage disabled') })
    await page.choose('flat'); await page.click('暂停此方案监控')
    expect(page.command).not.toHaveBeenCalled()
    expect(page.host.querySelector('[role="alert"]')).not.toBeNull()
  } finally { await page.close() }
})

it('keeps a superseded original readable without applying the new head state or sending commands', async () => {
  const view = currentActionFixture()
  view.branches[0].head.current_revision_id = 'newer-revision'
  view.branches[0].head.state = 'triggered'
  const page = await render(view)
  try {
    await page.choose('flat')
    expect(page.host.textContent).toContain('历史版本（已有更新）')
    expect(page.host.textContent).toContain('此版本已被替代')
    expect(page.host.textContent).not.toContain('条件已触发')
    expect(page.host.querySelector('[aria-label="所选仓位的行动方案"]')).not.toBeNull()
    for (const label of ['暂停此方案监控', '我已了解此方案']) {
      expect(Array.from(page.host.querySelectorAll('button')).find(item => item.textContent === label)?.disabled).toBe(true)
    }
    expect(page.command).not.toHaveBeenCalled()
  } finally { await page.close() }
})

it('retains a definitive rejection, refreshes, and allows an explicit new command with new preconditions', async () => {
  const page = await render()
  try {
    page.command.mockRejectedValueOnce(new ApiRequestError('action_state_version_conflict', 'http', 409))
    page.receipt.mockRejectedValueOnce(new ApiRequestError('missing', 'http', 404))
    const refreshed = currentActionFixture(); refreshed.branches[0].head.state_version = 2
    page.current.mockResolvedValue(refreshed)
    await page.choose('flat'); await page.click('暂停此方案监控')
    const first = page.command.mock.calls[0][0]
    expect(page.receipt).toHaveBeenCalledWith(first)
    expect(page.host.textContent).toContain('上次操作未提交，原拒绝记录已保留')
    expect(readActionIntent(actionIntentKey(ACTION_SCOPE, '24h'))).toBeNull()
    expect(sessionStorage.getItem(`${actionIntentKey(ACTION_SCOPE, '24h')}:rejected:${first.command.request_id}`)).toContain('action_state_version_conflict')
    await page.click('暂停此方案监控')
    const second = page.command.mock.calls[1][0]
    expect(second.command.request_id).not.toBe(first.command.request_id)
    expect(second.command.expected_state_version).toBe(2)
  } finally { await page.close() }
})

it('does not abandon a conflicting original command key', async () => {
  const page = await render()
  try {
    page.command.mockRejectedValueOnce(new ApiRequestError('action_command_key_conflict', 'http', 409))
    await page.choose('flat'); await page.click('暂停此方案监控')
    const first = page.command.mock.calls[0][0]
    expect(readActionIntent(actionIntentKey(ACTION_SCOPE, '24h'))).toEqual(first)
    expect(page.host.textContent).toContain('暂停监控结果待确认')
  } finally { await page.close() }
})

it('keeps a previously triggered plan reachable across new research and exact tracking pages', async () => {
  const view = currentActionFixture()
  const tracked = { plan_id: 'independent-tracked-plan', revision_id: 'independent-tracked-revision',
    opportunity_id: 'earlier-opportunity', action: 'enter_long' as const, position_context: 'flat' as const,
    state: 'triggered' as const, monitor_state: 'active' as const,
    valid_until: '2026-10-01T00:00:00Z', last_evaluated_at: '2026-10-01T00:00:00Z' }
  view.still_tracking_count = 11; view.still_tracking = [tracked]; view.tracking_next_cursor = 'tracking-page-2'
  const tracking = vi.spyOn(actionPlansApi, 'tracking').mockResolvedValue({
    schema_version: 'decision-tracking-view.v1', scope_id: ACTION_SCOPE, horizon: '24h', as_of: view.as_of,
    count: 11, items: [{ ...tracked, plan_id: 'next-plan', revision_id: 'next-revision' }], next_cursor: null,
  })
  const detail = vi.spyOn(actionPlansApi, 'plan').mockResolvedValue(actionDetailFixture())
  const page = await render(view)
  try {
    expect(page.host.textContent).toContain('最近一次研究未形成行动方案')
    expect(page.host.textContent).toContain('仍在跟踪的方案（11）')
    expect(page.host.textContent).toContain('已到期，等待监控收尾')
    expect(page.command).not.toHaveBeenCalled()
    await page.click('查看跟踪方案')
    expect(detail).toHaveBeenCalledWith('independent-tracked-plan', 'independent-tracked-revision', undefined, expect.any(AbortSignal))
    await page.click('下一页跟踪方案')
    expect(tracking).toHaveBeenCalledWith(ACTION_SCOPE, '24h', 'tracking-page-2', expect.any(AbortSignal))
    expect(page.host.textContent).toContain('仍在跟踪的方案（11）')
  } finally { await page.close() }
})
