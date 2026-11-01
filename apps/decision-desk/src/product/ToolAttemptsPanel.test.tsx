// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import type { ResearchUsageView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { ToolAttemptsPanel } from './ToolAttemptsPanel'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
afterEach(() => vi.restoreAllMocks())
const view: ResearchUsageView = {
  schema_version: 'research-usage-view.v1', run_id: 'run-1', status: 'partial',
  sessions: [{ session_id: 'main', role: 'main', model_steps: 1, usage_samples: 0,
    recorded_tokens: null, status: 'partial', issues: [], archive_sha256: 'a'.repeat(64),
    tool_issues: [], tool_attempts: [
      { ordinal: 1, call_id: 'call-1', kind: 'research', state: 'failed', failure_kind: 'argument_validation' },
      { ordinal: 2, call_id: 'call-2', kind: 'research', state: 'succeeded', failure_kind: null },
      { ordinal: 3, call_id: 'call-3', kind: 'other', state: 'incomplete', failure_kind: null },
    ] }],
  total_run_tokens: null, estimated_cost_usd: null, limitations: [], observed_at: '2026-10-01T01:00:00Z',
}

it('shows Host failures and unknown results without adding them to gateway totals', async () => {
  vi.spyOn(assets, 'researchUsage').mockResolvedValue(view)
  const div = document.createElement('div'); const root = createRoot(div)
  try {
    await act(async () => root.render(<ToolAttemptsPanel runId="run-1" />))
    expect(div.textContent).toContain('3 次工具尝试，其中取证 2 次；失败 1 次，结果待确认 1 次')
    expect(div.textContent).toContain('1 次参数校验失败')
    expect(div.textContent).toContain('不能相加')
    expect(div.textContent).not.toContain('call-1')
    expect(div.querySelector('details')?.open).toBe(false)
  } finally { await act(async () => root.unmount()) }
})

it('clears counts on failed refresh, retries, and treats missing archives as unknown', async () => {
  const read = vi.spyOn(assets, 'researchUsage').mockResolvedValueOnce(view)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue({ ...view, status: 'unavailable', sessions: [] })
  const div = document.createElement('div'); const root = createRoot(div)
  try {
    await act(async () => root.render(<ToolAttemptsPanel runId="run-1" />))
    await act(async () => div.querySelector('button')!.click())
    expect(div.textContent).toContain('失败次数未知')
    expect(div.textContent).not.toContain('失败 1 次')
    await act(async () => div.querySelector('button')!.click())
    expect(div.textContent).toContain('不能据此认定没有失败')
    expect(div.textContent).not.toContain('失败 0 次')
    expect(read).toHaveBeenCalledTimes(3)
  } finally { await act(async () => root.unmount()) }
})

it('ignores late responses from a previous Run', async () => {
  let finish!: (value: ResearchUsageView) => void
  vi.spyOn(assets, 'researchUsage').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue({ ...view, run_id: 'run-2', sessions: [], status: 'unavailable' })
  const div = document.createElement('div'); const root = createRoot(div)
  try {
    await act(async () => root.render(<ToolAttemptsPanel runId="run-1" />))
    await act(async () => root.render(<ToolAttemptsPanel runId="run-2" />))
    await act(async () => finish(view))
    expect(div.textContent).not.toContain('失败 1 次')
    expect(div.textContent).toContain('不能据此认定没有失败')
  } finally { await act(async () => root.unmount()) }
})
