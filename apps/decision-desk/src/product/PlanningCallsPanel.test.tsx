// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { PlanningCallsPanel } from './PlanningCallsPanel'

vi.mock('../api/client', () => ({ request: vi.fn() }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.clearAllMocks())

it('loads only on request and does not treat missing history as zero calls', async () => {
  vi.mocked(request).mockResolvedValue([])
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<PlanningCallsPanel jobId="old-job" />))
    expect(request).not.toHaveBeenCalled()
    await act(async () => host.querySelector('button')!.click())
    expect(request).toHaveBeenCalledWith('/v1/evolution/jobs/old-job/planning-calls', expect.anything())
    expect(host.textContent).toContain('旧任务未记录的失败原因无法追溯')
  } finally { await act(async () => root.unmount()) }
})

it('keeps role failures readable and reports refresh failure without replacing old rows', async () => {
  vi.mocked(request).mockResolvedValueOnce([{ call_id: 'c', job_attempt: 2, role: 'counter_thesis', status: 'failed', started_at: '2026-10-01T00:00:00Z', finished_at: '2026-10-01T00:00:01Z', details: { error_code: 'candidate_evidence_out_of_scope', retryable: false, text_characters: 200, evidence_count: 4 } }]).mockRejectedValueOnce(new Error('unavailable'))
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<PlanningCallsPanel jobId="job" />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('反向复核 · 第 2 次任务尝试 · 调用失败')
    expect(host.textContent).toContain('引用超出本任务材料范围')
    expect(host.querySelector('details')!.open).toBe(false)
    await act(async () => host.querySelector('button')!.click())
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('读取失败')
    expect(host.textContent).toContain('反向复核')
  } finally { await act(async () => root.unmount()) }
})
