// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { assets } from '../api/assets'
import { EvolutionReadinessPanel } from './EvolutionReadinessPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it.each(['verified', 'invalid', 'not_recorded'] as const)('shows validation blockers and %s request Pack status without claiming deployment', async (packStatus) => {
  const load = vi.spyOn(assets, 'evolutionReadiness').mockResolvedValueOnce({
    schema_version: 'evolution-readiness.v1', observed_at: '2026-09-30T06:00:00Z',
    feedback_count: 2, actionable_feedback: 1, unregistered_feedback: 1, jobs: 0, candidates: 0,
    configured_samples: { counts: { replay: 1, holdout: 1, shadow: 1 }, cross_split_groups: 1, within_split_groups: 0, issues: ['evaluation_cross_split_overlap'] }, prospective_collector_available: false,
    paired_execution: { registered_trials: 1, pairs: 4, predictions_saved: 1, waiting: 1, incomplete: 1, excluded: 1, outcome_scoring_available: false },
    research_method: { child_model_dispatch: { status: packStatus === 'verified' ? 'recorded' : packStatus, bindings: packStatus === 'verified' ? [{ schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'method-run', dsh_session_id: 'dsh-main', execution_session_id: 'child-one', parent_session_id: 'dsh-main', request_hash: 'a'.repeat(64), plugin_build_hash: 'b'.repeat(64), generation: 1, purpose: 'conversation', provider: 'child-route', model: 'child-model', reasoning_effort: 'high', temperature: null, max_tokens: 8192, stop_sequences_hash: null }] : [] }, model_dispatch: { status: packStatus, binding: packStatus === 'verified' ? {
      schema_version: 'dsh-model-dispatch-binding.v1', run_id: 'method-run', dsh_session_id: 'dsh-main', execution_session_id: null, parent_session_id: null, request_hash: 'a'.repeat(64), plugin_build_hash: 'b'.repeat(64), generation: 1, purpose: 'conversation', provider: 'live-route', model: 'test-model', reasoning_effort: 'off', temperature: null, max_tokens: 8192, stop_sequences_hash: null,
    } : null }, research_policy_status: packStatus, request_pack_status: packStatus, recorded_receipts: 1, deployment_connected: false, latest_receipt_status: 'verified', latest_receipt: { method_hash: 'technical-hash', run_id: 'method-run' } },
  }).mockRejectedValue(new Error('offline'))
  vi.spyOn(assets, 'prospectiveInputs').mockResolvedValueOnce({
    observed_at: '2026-09-30T06:00:00Z', selection_version: 'first-eligible-study-method-horizon.v1',
    outlooks: 60, eligible: 49, selected: 4, repeated: 45, excluded: 11, distinct_studies: 1,
    recorded_completed: 4, completed: 1, pending: 3, retry: 0, unknown: 0,
    directional_completed: 0, neutral_completed: 1, next_due_at: '2026-10-01T00:14:40Z',
  }).mockRejectedValue(new Error('offline'))
  const onObservations = vi.fn()
  const onRun = vi.fn()
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><EvolutionReadinessPanel onObservations={onObservations} onRun={onRun} /></QueryClientProvider>))
    await settle()
    expect(host.textContent).toContain('已保存反馈2')
    expect(host.textContent).toContain('当前演进账本的版本尚未接入正式研究执行')
    expect(host.textContent).toContain('已保存 1 份执行前方法凭据')
    expect(host.textContent).toContain({ verified: '这次主研究实际提交的模型参数已固定', invalid: '模型提交凭据未通过核验', not_recorded: '不能用当前默认模型补记过去的实际配置' }[packStatus])
    expect(host.textContent).toContain({ verified: '协作研究模型提交记录（1 个会话）', invalid: '协作研究的模型记录核验失败', not_recorded: '这次研究尚无协作会话模型记录' }[packStatus])
    if (packStatus === 'verified') {
      expect(host.textContent).toContain('服务端点与辅助模型仍未完整核验')
      expect(host.textContent).toContain('推理设置关闭')
    }

    expect(host.textContent).toContain({ verified: '预算、证据规则和能力范围已固定并核验', invalid: '研究包凭据缺失或校验不匹配', not_recorded: '旧任务不会事后补记' }[packStatus])
    expect(host.textContent).toContain({ verified: '研究准则与角色目标已从本次固定的研究包写入提交提示', invalid: '研究准则与提交提示不匹配', not_recorded: '不能用当前配置证明历史执行' }[packStatus])
    expect(host.querySelector('details')?.open).toBe(false)
    await act(async () => Array.from(host.querySelectorAll('button')).find(b => b.textContent === '查看这次研究')!.click())
    expect(onRun).toHaveBeenCalledWith('method-run')
    expect(host.textContent).toContain('尚未转成任务的改进反馈1')
    expect(host.textContent).toContain('不同评测阶段使用了重复材料')
    expect(host.textContent).toContain('尚未接入真实前瞻样本')
    expect(host.textContent).not.toContain('evaluation_cross_split_overlap')
    expect(host.textContent).toContain('两份预测均已保存1')
    expect(host.textContent).toContain('执行未完整返回1')
    expect(host.textContent).toContain('尚未接通成对行情评分')
    expect(host.textContent).toContain('账本已完成 4 条观察')
    expect(host.textContent).toContain('方向观点 0 条，中性观点 1 条')
    expect(host.textContent).toContain('这些数量不代表独立样本')
    const detail = Array.from(host.querySelectorAll('button')).find(b => b.textContent === '查看研究观点与观察记录')!
    await act(async () => detail.click())
    expect(onObservations).toHaveBeenCalledTimes(1)
    await act(async () => host.querySelector('button')!.click()); await settle()
    expect(load).toHaveBeenCalledTimes(2)
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('不代表当前状态')
    expect(host.textContent).toContain('已保存反馈2')
    expect(host.querySelector('button')?.disabled).toBe(false)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks() }
})
