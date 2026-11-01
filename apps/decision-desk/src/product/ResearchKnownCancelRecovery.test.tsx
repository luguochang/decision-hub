// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, ResearchReportServingError, reportRecoveryStatusSchema, type ReportRecoveryStatus } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
const reason = 'publication_execution_compute_budget_exceeded'
const recovery = (runId: string, action: ReportRecoveryStatus['action']): ReportRecoveryStatus => ({
  schema_version: 'research-report-recovery-status.v1', run_id: runId, request_id: 'same-freeze-request',
  generation: 41, state_version: 48, state: 'cancelled', reason_code: reason,
  source_abi: action === 'owner_request_required' ? 'publication-source-snapshot-read.v2' : null,
  action, proof_scope: 'metadata_prechecks_only',
})

it.each([
  [reason, '超过本次计算时限'], ['publication_execution_total_budget_exceeded', '超过本次总执行时限'],
  ['publication_execution_stop_requested', '收到停止请求'], ['publication_bundle_preparation_cancelled', '未保存详细停止原因'],
] as const)('explains %s from actual saved reason without claiming native exit or report success', async (code, message) => {
  vi.spyOn(researchQualityApi, 'read').mockRejectedValue(new ResearchReportServingError({
    run_id: 'run', state: 'cancelled', state_version: 48, confirmed_at: null, reason_code: code,
  }))
  vi.spyOn(researchQualityApi, 'recoveryStatus').mockResolvedValue({ ...recovery('run', 'new_research_required'), reason_code: code })
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  const navigate = vi.fn()
  try {
    await act(async () => root.render(<ResearchQuality runId="run" onOpenReport={navigate} />))
    expect(researchQualityApi.recoveryStatus).not.toHaveBeenCalled()
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '核对恢复条件')!.click())
    expect(element.textContent).toContain(message)
    expect(element.textContent).toContain('不能据此确认执行线程已经退出')
    expect(element.textContent).toContain('旧版读取能力')
    expect(element.textContent).toContain('新建任务并重新调用模型')
    expect(element.textContent).not.toContain(code)
    expect(element.querySelector('a[download]')).toBeNull()
    expect(element.textContent).not.toContain('报告已确认')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看原研究及复核入口')!.click())
    expect(navigate).toHaveBeenCalledOnce()
    expect(researchQualityApi.read).toHaveBeenCalledTimes(1)
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('ignores old-run recovery result after asset changes and never enables download', async () => {
  let finish!: (value: ReportRecoveryStatus) => void
  vi.spyOn(researchQualityApi, 'read').mockImplementation(async runId => { throw new ResearchReportServingError({
    run_id: runId, state: 'cancelled', state_version: 48, confirmed_at: null, reason_code: reason,
  }) })
  vi.spyOn(researchQualityApi, 'recoveryStatus').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValueOnce(recovery('new', 'review_required'))
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="old" />))
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '核对恢复条件')!.click())
    await act(async () => root.render(<ResearchQuality runId="new" />))
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '核对恢复条件')!.click())
    await act(async () => finish(recovery('old', 'owner_request_required')))
    expect(element.textContent).not.toContain('已核对当前读取能力')
    expect(element.textContent).toContain('系统保留原现场')
    expect(element.querySelector('a[download]')).toBeNull()
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('does not turn metadata capability into a write permit or accept inconsistent ABI', () => {
  expect(reportRecoveryStatusSchema.safeParse(recovery('run', 'owner_request_required')).success).toBe(true)
  expect(reportRecoveryStatusSchema.safeParse({ ...recovery('run', 'owner_request_required'), source_abi: null }).success).toBe(false)
  expect(reportRecoveryStatusSchema.safeParse({ ...recovery('run', 'owner_request_required'), write_permit: true }).success).toBe(false)
  expect(reportRecoveryStatusSchema.safeParse({ ...recovery('run', 'new_research_required'), state: 'confirmed' }).success).toBe(false)
})
