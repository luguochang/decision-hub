// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ResearchUsageSnapshot } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { ModelUsagePanel } from './ModelUsagePanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
beforeEach(() => { vi.spyOn(assets, 'usageCollection').mockResolvedValue([]) })
const snapshot: ResearchUsageSnapshot = {
  schema_version: 'research-usage-snapshot.v1', snapshot_id: 'usage_1', run_id: 'run-1',
  recorded_at: '2026-09-30T02:01:00Z', accounting_version: 'dsh-final-messages.v1', session_generation: 1, payload_sha256: 'b'.repeat(64),
  view: { schema_version: 'research-usage-view.v1', run_id: 'run-1', status: 'partial',
    sessions: [{ session_id: 'main', role: 'main', model_steps: 2, usage_samples: 2, recorded_tokens: 300, status: 'accounted', issues: [], archive_sha256: 'a'.repeat(64) }],
    total_run_tokens: null, estimated_cost_usd: null, limitations: [], observed_at: '2026-09-30T02:00:00Z' },
}
const empty = { items: [], total: 0, offset: 0, limit: 20 }

it('restores a saved receipt without rescanning archives and clears it on a different run', async () => {
  vi.mocked(assets.usageCollection).mockResolvedValueOnce([{
    run_id: 'run-1', session_id: 'main', generation: 1, status: 'captured',
    checked_at: '2026-09-30T02:01:00Z', next_check_at: '2026-09-30T02:11:00Z',
    attempts: 1, last_snapshot_id: 'usage_1', error_code: null,
  }])
  const history = vi.spyOn(assets, 'usageHistory').mockResolvedValueOnce({ ...empty, items: [snapshot], total: 1 }).mockResolvedValue(empty)
  const capture = vi.spyOn(assets, 'captureUsage')
  const div = document.createElement('div'); document.body.append(div)
  const root = createRoot(div)
  try {
    await act(async () => root.render(<ModelUsagePanel runId="run-1" />))
    expect(history).toHaveBeenCalledWith('run-1', 0)
    expect(capture).not.toHaveBeenCalled()
    expect(div.textContent).toContain('自动归集状态（1 个会话版本')
    expect(div.textContent).toContain('当前展示已保存核验')
    expect(div.textContent).toContain('已找到 1 个关联会话')
    expect(div.textContent).toContain('尚不能直接相加为完整账单')
    expect(div.textContent).not.toContain('$0')
    await act(async () => root.render(<ModelUsagePanel runId="run-2" />))
    expect(div.querySelector('table')).toBeNull()
  } finally { await act(async () => root.unmount()); div.remove() }
})

it('retries the same capture command after connection loss and only then shows the saved record', async () => {
  vi.spyOn(assets, 'usageHistory').mockResolvedValue(empty)
  const capture = vi.spyOn(assets, 'captureUsage').mockRejectedValueOnce(new Error('lost response')).mockResolvedValue(snapshot)
  const div = document.createElement('div'); document.body.append(div)
  const root = createRoot(div)
  try {
    await act(async () => root.render(<ModelUsagePanel runId="run-1" />))
    await act(async () => div.querySelector('button')!.click())
    expect(div.textContent).toContain('本次核对或保存未成功')
    expect(div.querySelector('table')).toBeNull()
    await act(async () => div.querySelector('button')!.click())
    expect(capture.mock.calls[0]).toEqual(capture.mock.calls[1])
    expect(div.textContent).toContain('当前展示已保存核验')
    expect(div.textContent).toContain('300')
  } finally { await act(async () => root.unmount()); div.remove() }
})

it('ignores a late receipt from the previously selected run', async () => {
  vi.spyOn(assets, 'usageHistory').mockResolvedValue(empty)
  let resolve!: (value: ResearchUsageSnapshot) => void
  vi.spyOn(assets, 'captureUsage').mockImplementation(() => new Promise(done => { resolve = done }))
  const div = document.createElement('div'); document.body.append(div)
  const root = createRoot(div)
  try {
    await act(async () => root.render(<ModelUsagePanel runId="run-1" />))
    await act(async () => div.querySelector('button')!.click())
    await act(async () => root.render(<ModelUsagePanel runId="run-2" />))
    await act(async () => resolve(snapshot))
    expect(div.querySelector('table')).toBeNull()
    expect(div.textContent).not.toContain('当前展示已保存核验')
  } finally { await act(async () => root.unmount()); div.remove() }
})
