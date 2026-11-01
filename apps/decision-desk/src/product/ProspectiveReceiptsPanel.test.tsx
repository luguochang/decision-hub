// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { assets } from '../api/assets'
import { ProspectiveReceiptsPanel } from './ProspectiveReceiptsPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('loads on demand, separates abstention from a hit, paginates and hides raw fields until requested', async () => {
  const trials = vi.spyOn(assets, 'prospectiveTrials').mockResolvedValue([{ trial_id: 'trial_one', registered_at: '2026-09-30T08:00:00Z', starts_at: '2026-09-30T09:00:00Z', ends_at: '2026-11-01T09:00:00Z', instrument: 'BTC-USDT-SWAP', horizon: '30m', baseline_version: 'baseline.v1', candidate_version: 'candidate.v1' }])
  const page = vi.spyOn(assets, 'prospectivePairs').mockResolvedValue({ offset: 0, has_more: true, items: [{ pair_id: 'pair_one', admitted_at: '2026-09-30T10:00:00Z', window_start_at: '2026-09-30T10:03:00Z', window_end_at: '2026-09-30T10:33:00Z', status: 'predictions_saved', exclusion_reason: null, arms: [{ arm: 'baseline', direction: 'long', status: 'succeeded', error_code: null }, { arm: 'candidate', direction: 'no_trade', status: 'succeeded', error_code: null }], label: { status: 'completed', attempt: 1, error_code: null, next_attempt_at: '2026-09-30T10:34:00Z', result_hash: 'hash', result: { market_return_pct: 2, benchmark: 'close_1m', quality_status: 'estimated', paired_correctness_delta: null, scores: { baseline: { status: 'succeeded', direction: 'long', direction_correct: true, directional_return_pct: 2 }, candidate: { status: 'succeeded', direction: 'no_trade', direction_correct: null, directional_return_pct: null } } } } }] })
  const detail = vi.spyOn(assets, 'prospectivePairReceipt').mockResolvedValue({ pair: { input_hash: 'technical-hash', input: { facts: [{ raw_field: 'technical-only' }] } } })
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProspectiveReceiptsPanel /></QueryClientProvider>))
    expect(trials).not.toHaveBeenCalled()
    await act(async () => { const outer = host.querySelector('details')!; outer.open = true; outer.dispatchEvent(new Event('toggle')) }); await settle(); await settle()
    expect(host.textContent).toContain('暂不判断方向')
    expect(host.textContent).toContain('共同市场变化：2.0000%')
    expect(host.textContent).toContain('未计算方向命中')
    expect(host.textContent).not.toContain('technical-only')
    expect(detail).not.toHaveBeenCalled()
    await act(async () => { const inner = host.querySelector('article details') as HTMLDetailsElement; inner.open = true; inner.dispatchEvent(new Event('toggle')) }); await settle()
    expect(detail).toHaveBeenCalledWith('pair_one')
    expect(host.textContent).toContain('technical-only')
    const refresh = Array.from(host.querySelectorAll('button')).find(item => item.textContent === '刷新样本记录')!
    const callsBeforeRefresh = detail.mock.calls.length
    await act(async () => refresh.click()); await settle()
    expect(detail.mock.calls.length).toBeGreaterThan(callsBeforeRefresh)
    const next = Array.from(host.querySelectorAll('button')).find(item => item.textContent === '下一页')!
    await act(async () => next.click()); await settle()
    expect(page).toHaveBeenLastCalledWith('trial_one', 10)
    trials.mockRejectedValue(new Error('offline')); page.mockRejectedValue(new Error('offline'))
    await act(async () => refresh.click()); await settle()
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('上次快照')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks() }
})
