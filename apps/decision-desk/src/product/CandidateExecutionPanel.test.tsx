// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { CandidateExecutionPanel } from './CandidateExecutionPanel'
import { PlanningDecisionPanel } from './PlanningDecisionPanel'
vi.mock('../api/client', () => ({ request: vi.fn() }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.clearAllMocks())

it('shows operational limitations while leaving original prose collapsed', async () => {
  vi.mocked(request).mockResolvedValue({ executable: false, reason_code: 'candidate_operational_change_unsupported', rules: [], summary: 'Host preflight', changes: ['Pause Worker'], evidence_refs: ['run:1'] })
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<CandidateExecutionPanel candidateId="old" active={false} />))
    expect(request).not.toHaveBeenCalled()
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('当前执行器不能据此调整')
    expect(host.textContent).toContain('关联 1 项规划依据')
    expect(host.querySelector('details')!.open).toBe(false)
  } finally { await act(async () => root.unmount()) }
})

it('describes compiled rules without claiming evaluation or activation', async () => {
  vi.mocked(request).mockResolvedValue({ executable: true, rules: ['结论前检查反向证据'], summary: 'test', changes: [], evidence_refs: [] })
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<CandidateExecutionPanel candidateId="new" active={false} />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('仍需独立评测和负责人审核')
    expect(host.textContent).toContain('结论前检查反向证据')
  } finally { await act(async () => root.unmount()) }
})

it('keeps operational followup distinct from implemented fixes', async () => {
  vi.mocked(request).mockResolvedValue({ outcome: 'operational_followup', reason: '需核对服务连接', candidate: null, evidence_refs: ['run:1'] })
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<PlanningDecisionPanel jobId="job" />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('需处理运行问题')
    expect(host.querySelector('details')!.open).toBe(false)
    expect(host.textContent).toContain('不代表修复已实施或方法已启用')
  } finally { await act(async () => root.unmount()) }
})
