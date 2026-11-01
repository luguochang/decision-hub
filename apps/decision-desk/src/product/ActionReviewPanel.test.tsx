// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { actionPlansApi, type ActionPosition } from '../api/actionPlans'
import { ActionReviewPanel } from './ActionReviewPanel'
import { ACTION_SCOPE, actionReviewFixture } from './actionPlanTestFixtures'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
async function render() {
  const reviews = vi.spyOn(actionPlansApi, 'reviews').mockResolvedValue(actionReviewFixture())
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), onNavigate = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const choose = async (position: ActionPosition) => { await act(async () => root.render(<QueryClientProvider client={client}><ActionReviewPanel opportunityId="opportunity-old" scopeId={ACTION_SCOPE} horizon="24h" position={position} onNavigate={onNavigate} /></QueryClientProvider>)); await settle() }
  await choose('flat')
  return { host, reviews, onNavigate, choose, client,
    click: async (label: string) => { const button = Array.from(host.querySelectorAll('button')).find(item => item.textContent === label); expect(button).toBeDefined(); await act(async () => button!.click()); await settle() },
    close: async () => { await act(async () => root.unmount()); client.clear(); host.remove() } }
}
it('shows retained new analysis separately from the current protective conditions', async () => {
  const page = await render()
  try {
    expect(page.host.textContent).toContain('新分析已保存，原保护继续保留')
    expect(page.host.textContent).toContain('新建议尚未替换当前保护条件')
    expect(page.host.textContent).toContain('复核建议等待新的买盘确认')
    expect(page.host.textContent).toContain('暂无可用概率')
    expect(page.host.textContent).toContain('最近完整分钟收盘价达到或高于 85000 USDT / BTC')
    await page.click('阅读这次复核报告')
    expect(page.onNavigate).toHaveBeenCalledWith('report', { run: 'run-review', back: '#/home' })
    await page.choose('long')
    expect(page.host.textContent).toContain('复核已更新方案')
    expect(page.host.textContent).toContain('复核建议按条件降低多仓风险')
    expect(page.host.textContent).not.toContain('复核建议等待新的买盘确认')
    await page.choose('unknown')
    expect(page.host.textContent).toContain('未给这个仓位提供新的行动建议')
    expect(page.host.textContent).not.toContain('复核建议按条件降低多仓风险')
  } finally { await page.close() }
})
it('hides stale review on read failure and recovers through a read-only retry', async () => {
  const page = await render()
  try {
    page.reviews.mockRejectedValue(new Error('unavailable'))
    await act(async () => { await page.client.refetchQueries({ queryKey: ['action-review'] }) }); await settle()
    expect(page.host.textContent).toContain('复核结果暂时无法读取')
    expect(page.host.textContent).not.toContain('复核建议等待新的买盘确认')
    page.reviews.mockResolvedValue(actionReviewFixture())
    await page.click('重新读取复核结果')
    expect(page.host.textContent).toContain('复核建议等待新的买盘确认')
  } finally { await page.close() }
})
it.each(['expired', 'superseded'] as const)('does not present old protection as running when %s', async status => {
  const page = await render()
  try {
    const view = actionReviewFixture()
    if (status === 'expired') view.review!.branches[0].current_head.valid_until = '2020-01-01T00:00:00Z'
    else view.review!.branches[0].current_head.current_revision_id = 'newer-revision'
    page.reviews.mockResolvedValue(view)
    await act(async () => { await page.client.refetchQueries({ queryKey: ['action-review'] }) }); await settle()
    expect(page.host.textContent).toContain(status === 'expired' ? '不代表保护仍在运行' : '当前方案已有后续版本')
  } finally { await page.close() }
})
