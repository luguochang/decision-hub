// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { ObservationInputAuditPanel } from './ObservationInputAuditPanel'

vi.mock('../api/client', () => ({ request: vi.fn() }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.clearAllMocks())

it('distinguishes usable inputs from valid market labels and keeps stale results explicit', async () => {
  vi.mocked(request).mockResolvedValueOnce({ checked_at: '2026-10-01T00:00:00Z', selection: { selected: 59 }, verified_inputs: 12, invalid_inputs: 47, verified_labels: 12, invalid_labels: 0, pending_labels: 47, usable_observations: 11, directional_usable: 0, neutral_usable: 11, issues: { pair_input_capture_missing: 40, pair_fact_after_cutoff_or_scope_mismatch: 7 } }).mockRejectedValueOnce(new Error('offline'))
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<ObservationInputAuditPanel />))
    expect(request).not.toHaveBeenCalled()
    await act(async () => host.querySelector('button')!.click())
    expect(request).toHaveBeenCalledWith('/v1/evolution/observation-input-audit', expect.anything())
    expect(Array.from(host.querySelectorAll('strong')).map(n => n.textContent)).toEqual(['12', '12', '11'])
    expect(host.textContent).toContain('方向观点 0 条，中性观点 11 条')
    expect(host.textContent).toContain('缺少研究原文的采集凭据：40 条')
    expect(host.textContent).not.toContain('pair_input_capture_missing')
    await act(async () => host.querySelector('button')!.click())
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('下方保留上次结果，不代表当前状态')
    expect(host.textContent).toContain('方向观点 0 条')
  } finally { await act(async () => root.unmount()) }
})
