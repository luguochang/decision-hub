// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import type { EvolutionOverview } from '../api/types'
import { EvolutionExperimentCards } from './EvolutionExperimentCards'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('explains abstention and zero common samples without claiming the trial has ended', async () => {
  const data = { datasets: [{ dataset_id: 'd', split: 'shadow' }], experiments: [{ experiment_id: 'e', dataset_id: 'd', status: 'completed', baseline_ref: 'b' }], results: [{ result_id: 'r', experiment_id: 'e', candidate_id: 'c', scorer_version: 'prospective.paired-direction.v1', sample_count: 0, brier_score: null, cost_usd: null, p95_latency_ms: 32000, safety_violations: 0, failure_counts: { non_directional: 1 } }] } as unknown as EvolutionOverview
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<EvolutionExperimentCards evolution={data} />))
    const card = host.querySelector('article')!
    expect(card.textContent).toContain('本次核验快照已保存')
    expect(card.textContent).toContain('双方方向可比 0 个')
    expect(card.textContent).toContain('未给方向 1 次')
    expect(card.textContent).toContain('费用 待核对')
    expect(card.textContent).toContain('未登记概率评分')
    expect(card.querySelector('details')?.open).toBe(false)
    expect(card.querySelector('small')?.textContent).toContain('弃权与失败仍保留')
  } finally { await act(async () => root.unmount()); host.remove() }
})


it('keeps legacy shared-label scores readable but clearly requires rescoring', async () => {
  const data = { datasets: [], experiments: [{ experiment_id: 'e', dataset_id: 'd', status: 'completed', baseline_ref: 'b' }], results: [{ result_id: 'r', experiment_id: 'e', candidate_id: 'c', scorer_version: 'evaluation.aggregate.v2', sample_count: 100, brier_score: 0.2, cost_usd: null, p95_latency_ms: null, safety_violations: 0, failure_counts: {} }] } as unknown as EvolutionOverview
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<EvolutionExperimentCards evolution={data} />))
    expect(host.textContent).toContain('未按各方法的预测重新评分')
    expect(host.textContent).toContain('旧口径记录数 100，不能用于新审核')
    expect(host.textContent).toContain('概率误差 0.2000')
    expect(host.textContent).not.toContain('有效样本 100 个')
  } finally { await act(async () => root.unmount()); host.remove() }
})
