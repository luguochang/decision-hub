// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { PlanningContextPanel } from './PlanningContextPanel'
vi.mock('../api/client', () => ({ request: vi.fn() }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.clearAllMocks())

it('explains frozen counts and keeps recovery identity in technical details', async () => {
  vi.mocked(request).mockResolvedValue({ frozen_at: '2026-10-01T00:00:00Z', admission: 'ready', counts: { cancelled_not_method_failure: 492, included_failure_group: 82 }, summary_count: 9, input_count: 574, prompt_characters: 4663, recovery_of: 'old-failed-job', baseline_version: 'baseline.v1' })
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<PlanningContextPanel jobId="recovery" />))
    expect(request).not.toHaveBeenCalled()
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('原始引用 574 项')
    expect(host.textContent).toContain('供规划使用的摘要 9 组')
    expect(host.textContent).toContain('取消记录，保留但不作为方法失败：492 项')
    expect(host.textContent).toContain('原失败记录仍保留')
    expect(host.querySelector('details')!.open).toBe(false)
  } finally { await act(async () => root.unmount()) }
})

it('does not reconstruct missing historical snapshots', async () => {
  vi.mocked(request).mockResolvedValue(null)
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<PlanningContextPanel jobId="old" />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('不能用当前资料推断当时模型看到了什么')
  } finally { await act(async () => root.unmount()) }
})
