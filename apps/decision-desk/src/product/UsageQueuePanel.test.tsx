// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { assets } from '../api/assets'
import { UsageQueuePanel } from './UsageQueuePanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('shows backlog separately from missing archives and retains a labelled stale result on refresh failure', async () => {
  const load = vi.spyOn(assets, 'usageQueueSummary').mockResolvedValueOnce({
    schema_version: 'usage-queue-summary.v1', observed_at: '2026-09-30T06:00:00Z',
    session_versions: 750, unique_runs: 642, statuses: { captured: 517, partial: 213, waiting_archive: 20 },
    due: 500, overdue_10m: 400, checking: 2,
    oldest_due_at: '2026-09-30T05:30:00Z', latest_check_at: '2026-09-30T05:59:59Z',
  }).mockRejectedValue(new Error('offline'))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><UsageQueuePanel /></QueryClientProvider>))
    await settle()
    expect(host.textContent).toContain('延后超过 10 分钟 400 项')
    expect(host.textContent).toContain('等待日志归档20')
    expect(host.textContent).toContain('不能据此认定费用已全部对账')
    await act(async () => host.querySelector('button')!.click()); await settle()
    expect(load).toHaveBeenCalledTimes(2)
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('不代表当前状态')
    expect(host.textContent).toContain('750 个会话版本')
    expect(host.querySelector('button')?.disabled).toBe(false)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks() }
})

it('opens filtered tasks, keeps return context, and retains stale rows on read failure', async () => {
  vi.spyOn(assets, 'usageQueueSummary').mockResolvedValue({
    schema_version: 'usage-queue-summary.v1', observed_at: '2026-09-30T06:00:00Z',
    session_versions: 12, unique_runs: 8, statuses: { waiting_archive: 12 }, due: 0,
    overdue_10m: 0, checking: 0, oldest_due_at: null, latest_check_at: null,
  })
  const load = vi.spyOn(assets, 'usageQueueJobs').mockResolvedValueOnce({
    schema_version: 'usage-queue-page.v1', observed_at: '2026-09-30T06:00:00Z',
    filter: 'waiting_archive', offset: 10, limit: 10, total: 12, has_more: false,
    items: [{ job_id: 'job-1', run_id: 'run-1', run_created_at: '2026-09-29T06:00:00Z',
      run_available: true, generation: 2, status: 'waiting_archive', checked_at: null,
      next_check_at: '2026-09-30T07:00:00Z', checking: false, attempts: 3,
      error_code: 'usage_archive_unavailable',
    }, { job_id: 'job-2', run_id: 'missing', run_created_at: null, run_available: false,
      generation: 1, status: 'waiting_archive', checked_at: null,
      next_check_at: '2026-09-30T07:00:00Z', checking: false, attempts: 1, error_code: null }],
  }).mockRejectedValue(new Error('offline'))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const navigate = vi.fn()
  const params = new URLSearchParams('usage_status=waiting_archive&usage_offset=10')
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><UsageQueuePanel params={params} onNavigate={navigate} /></QueryClientProvider>)); await settle()
    expect(load).toHaveBeenCalledWith('waiting_archive', 10)
    expect(host.textContent).toContain('当前 11–12')
    expect(host.textContent).toContain('关联研究不存在')
    const link = host.querySelector('a')!
    const target = new URLSearchParams(link.hash.split('?')[1])
    expect(target.get('run')).toBe('run-1')
    expect(target.get('back')).toBe('#/management?usage_status=waiting_archive&usage_offset=10')
    const previous = [...host.querySelectorAll('button')].find(b => b.textContent === '上一页归集任务')!
    await act(async () => previous.click())
    expect(navigate).toHaveBeenLastCalledWith('management', { usage_status: 'waiting_archive', usage_offset: '0' })
    const select = host.querySelector('select')!
    await act(async () => { select.value = 'partial'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(navigate).toHaveBeenLastCalledWith('management', { usage_status: 'partial', usage_offset: '0' })
    await act(async () => host.querySelector('button')!.click()); await settle()
    expect(host.textContent).toContain('下方保留上次读取结果，当前状态尚未确认')
    expect(host.querySelectorAll('tbody tr')).toHaveLength(2)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks() }
})
