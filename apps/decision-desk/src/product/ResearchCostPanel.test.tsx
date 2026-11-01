// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import type { ResearchRunDetailView } from '../api/types'
import { ResearchCostPanel } from './ResearchCostPanel'

vi.mock('./ModelUsagePanel', () => ({ ModelUsagePanel: ({ runId }: { runId: string }) => <div data-usage-run={runId} /> }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Call = ResearchRunDetailView['rounds'][number]['tool_invocations'][number]
const call = (id: string, cost: number | null, finished = true): Call => ({
  tool_call_id: id, capability_id: 'future-capability', tool_name: 'future-tool',
  query_summary: 'Original tool request', started_at: '2026-10-01T00:00:00Z',
  finished_at: finished ? '2026-10-01T00:00:01Z' : null, status: finished ? 'succeeded' : 'running',
  attempt: 1, latency_ms: finished ? 1000 : null, cost_usd: cost,
  error_code: 'future-error', error: null,
})
const detail = (calls: Call[], cost: number | null, tokens: number | null) => ({
  run: { run_id: 'run-cost' }, rounds: [{ tool_invocations: calls }],
  estimated_cost_usd: cost, total_tokens: tokens,
}) as ResearchRunDetailView

it('shows tiny fees without zero and retains exact fees and call errors in closed audits', async () => {
  const div = document.createElement('div'); document.body.append(div)
  const root = createRoot(div)
  try {
    await act(async () => root.render(<ResearchCostPanel detail={detail([call('call-tiny', 0.000000012345)], 0.000000098765, 0)} />))
    expect(div.querySelector('section > p')?.textContent).toContain('$9.8765e-8')
    expect(div.querySelector('li > div:first-child > span')?.textContent).toContain('$1.2345e-8')
    expect(div.querySelector('section > p')?.textContent).not.toContain('$0.0000')
    expect(div.textContent).toContain('总词元字段：0')
    const audits = [...div.querySelectorAll('details')]
    expect(audits).toHaveLength(2)
    expect(audits.every(item => !item.open)).toBe(true)
    expect(JSON.parse(audits[0].querySelector('pre')!.textContent!)).toEqual({ estimated_cost_usd: 0.000000098765, total_tokens: 0 })
    expect(JSON.parse(audits[1].querySelector('pre')!.textContent!)).toMatchObject({ cost_usd: 0.000000012345, error_code: 'future-error', error: null, tool_call_id: 'call-tiny' })
    await act(async () => { audits[1].querySelector('summary')!.click() })
    expect(audits[1].open).toBe(true)
    expect(div.querySelector('[data-usage-run]')?.getAttribute('data-usage-run')).toBe('run-cost')
  } finally { await act(async () => root.unmount()); div.remove() }
})

it('keeps missing costs distinct from recorded zero and deduplicates repeated snapshots', async () => {
  const div = document.createElement('div'); document.body.append(div)
  const root = createRoot(div)
  const data = detail([call('call-zero', 0, false), call('call-zero', 0), call('call-missing', null)], null, null)
  try {
    await act(async () => root.render(<ResearchCostPanel detail={data} />))
    expect(div.querySelector('section > p')?.textContent).toContain('估算费用：未记录；工具费用缺失 1 / 2 次')
    expect(div.textContent).toContain('未记录完整总量')
    expect(div.querySelectorAll('li')).toHaveLength(2)
    expect(div.querySelectorAll('li > div:first-child > span')[0].textContent).toContain('$0.0000')
    expect(div.querySelectorAll('li > div:first-child > span')[1].textContent).toContain('未记录')
    expect(JSON.parse(div.querySelectorAll('li pre')[0].textContent!)).toMatchObject({ status: 'succeeded', cost_usd: 0 })
  } finally { await act(async () => root.unmount()); div.remove() }
})
