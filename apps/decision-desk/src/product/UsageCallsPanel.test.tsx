// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import type { ResearchUsageSnapshot } from '@decision-hub/contracts-ts'
import { UsageCallsPanel } from './UsageCallsPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
type Session = ResearchUsageSnapshot['view']['sessions'][number]
const session: Session = {
  session_id: 'main', role: 'main', model_steps: 21, usage_samples: 21, recorded_tokens: 420,
  status: 'accounted', issues: [], archive_sha256: 'a'.repeat(64),
  calls: Array.from({ length: 21 }, (_, index) => ({
    ordinal: index + 1, kind: 'model', turn: 1, step: index + 1, attempt: 1,
    provider: 'route-a', model: 'model-a', state: 'finalized', usage_source: 'final',
    started_at: '2026-09-30T02:00:00Z', observed_at: '2026-09-30T02:01:00Z',
    input_tokens: 12, output_tokens: 3, cache_read_tokens: 5, cache_write_tokens: null,
    reasoning_tokens: null, total_tokens: 20, issues: [],
  })),
}

it('paginates call detail and resets to page one when selecting another session', async () => {
  const div = document.createElement('div'); const root = createRoot(div)
  try {
    await act(async () => root.render(<UsageCallsPanel sessions={[session, { ...session, session_id: 'child', role: 'delegated', calls: session.calls!.slice(0, 1) }]} />))
    expect(div.querySelectorAll('tbody tr')).toHaveLength(20)
    await act(async () => [...div.querySelectorAll('button')].find(x => x.textContent === '下一页调用')!.click())
    expect(div.textContent).toContain('21–21 / 21')
    expect(div.querySelectorAll('tbody tr')).toHaveLength(1)
    await act(async () => { const select = div.querySelector('select')!; select.value = '1'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(div.textContent).toContain('1–1 / 1')
    expect(div.textContent).toContain('缓存写入')
    expect(div.textContent).toContain('未知')
    expect(div.textContent).not.toContain('$0')
  } finally { await act(async () => root.unmount()) }
})

it('shows legacy receipts as missing detail and keeps failure gaps visible', async () => {
  const div = document.createElement('div'); const root = createRoot(div)
  try {
    await act(async () => root.render(<UsageCallsPanel sessions={[{ ...session, calls: undefined }]} />))
    expect(div.textContent).toContain('此历史核验未保存逐调用明细')
    expect(div.querySelector('table')).toBeNull()
    await act(async () => root.render(<UsageCallsPanel sessions={[{ ...session, calls: [{ ...session.calls![0], state: 'failed', usage_source: 'stream', issues: ['retry_attempt_not_bill_reconciled', 'final_usage_missing'] }] }]} />))
    expect(div.textContent).toContain('失败或未完成 1 次')
    expect(div.textContent).toContain('失败尝试尚未与账单对齐')
    expect(div.textContent).toContain('流式片段（未完整确认）')
  } finally { await act(async () => root.unmount()) }
})
