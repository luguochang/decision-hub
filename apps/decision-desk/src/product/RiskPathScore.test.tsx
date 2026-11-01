// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { riskPathsApi } from '../api/riskPaths'
import type { PathTrainingReadiness } from '../api/riskPaths'
import { PATH_BTC, PATH_ETH, pathCurrentFixture, pathScoreFinalFixture, pathScorePendingFixture, pathPredictionsFixture, pathScoreV2FinalFixture } from '../api/riskPathsFixtures'
import { RiskPathScore } from './RiskPathScore'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
let client: QueryClient
async function mount(scopeId = PATH_BTC) {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await render(scopeId)
}
async function render(scopeId: string) {
  await act(async () => root.render(<QueryClientProvider client={client}><RiskPathScore scopeId={scopeId} /></QueryClientProvider>))
}
async function until(check: () => boolean) {
  for (let index = 0; index < 60 && !check(); index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  expect(check()).toBe(true)
}
function button() { return container.querySelector('button')! }
function defaults() {
  const current = vi.spyOn(riskPathsApi, 'current').mockResolvedValue(pathCurrentFixture)
  vi.spyOn(riskPathsApi, 'score').mockResolvedValue(pathScorePendingFixture)
  vi.spyOn(riskPathsApi, 'predictions').mockResolvedValue(pathPredictionsFixture)
  return current
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks()
})
it('shows no trial without creating one or calling a model', async () => {
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: null })
  await mount()
  await until(() => Boolean(container.textContent?.includes('尚未登记 28 日影子评价')))
  expect(riskPathsApi.score).not.toHaveBeenCalled()
  expect(riskPathsApi.predictions).not.toHaveBeenCalled()
  expect(container.textContent).toContain('不能用回填预测补成绩')
})
it('keeps future slots distinct from missing and states the qualification limit', async () => {
  defaults()
  await mount()
  await until(() => Boolean(container.textContent?.includes('4 / 112')))
  expect(container.textContent).toContain('尚待未来时点 108 条；已错过 0 条')
  expect(container.textContent).toContain('尚未成熟 56 个')
  expect(container.textContent).toContain('当前不具备晋级资格')
  expect(container.textContent).toContain('28 天不足以证明预测优势')
  expect(container.textContent).not.toContain('100%')
})
it('renders final paired Brier and full exclusions without inventing calibration or English properties', async () => {
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: pathScoreFinalFixture.trial })
  vi.mocked(riskPathsApi.score).mockResolvedValue(pathScoreFinalFixture)
  await mount()
  await until(() => Boolean(container.textContent?.includes('本轮结项：证据不足，不能晋级')))
  expect(container.textContent).toContain('配对有效样本 1 / 28')
  expect(container.textContent).toContain('基线误差：0.25')
  expect(container.textContent).toContain('候选误差：0.2')
  expect(container.textContent).toContain('误差减少：0.05')
  expect(container.textContent).toContain('没有标签 54')
  expect(container.textContent).toContain('置信区间尚未登记')
  expect(container.textContent).not.toContain('fraction')
  expect(container.textContent).not.toContain('insufficient_evidence')
})
it('survives refresh failure visibly unknown and clears BTC while loading ETH', async () => {
  const current = defaults()
  await mount()
  await until(() => Boolean(container.textContent?.includes('4 / 112')))
  await until(() => !button().disabled)
  current.mockRejectedValueOnce(new Error('offline'))
  await act(async () => button().click())
  await until(() => Boolean(container.textContent?.includes('刷新失败，当前预测成绩未确认')))
  expect(container.textContent).toContain('4 / 112')
  current.mockImplementation(async scopeId => ({ ...pathCurrentFixture, scope_id: scopeId, trial: null }))
  await render(PATH_ETH)
  await until(() => Boolean(container.textContent?.includes('尚未登记 28 日影子评价')))
  expect(container.textContent).not.toContain('4 / 112')
  expect(current).toHaveBeenLastCalledWith(PATH_ETH, expect.any(AbortSignal))
})
it('shows pause deadline and missing final receipt without extending or faking completion', async () => {
  const paused = { ...pathScoreFinalFixture.trial, state: 'paused' as const, report_available: false }
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: paused })
  vi.mocked(riskPathsApi.score).mockResolvedValue({ ...pathScorePendingFixture, trial: paused })
  await mount()
  await until(() => Boolean(container.textContent?.includes('试验已暂停')))
  expect(container.textContent).toContain('固定结项期限已到，结项回执尚未生成')
  expect(container.textContent).toContain('固定结项期限不会延长')
})
it('shows a registered interval as insufficient instead of legacy unregistered and does not claim promotion', async () => {
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: pathScoreV2FinalFixture.trial })
  vi.mocked(riskPathsApi.score).mockResolvedValue(pathScoreV2FinalFixture)
  await mount()
  await until(() => Boolean(container.textContent?.includes('固定 95% 区间方法已登记')))
  expect(container.textContent).toContain('完整配对 2 / 56')
  expect(container.textContent).toContain('独立事件区块 尚未核验 / 30')
  expect(container.textContent).toContain('不能晋级')
  expect(container.textContent).not.toContain('置信区间尚未登记')
  expect(container.textContent).not.toContain('insufficient')
})
it('renders system alarm metrics and conservative lead time separately from user benefit', async () => {
  const value = structuredClone(pathScoreV2FinalFixture)
  value.report!.score.alarm_metrics = { precision: '0.5', recall: '0.25', false_episodes_per_asset_day: '1.25',
    lead_time: { matched_count: 2, minimum_seconds: '0', median_seconds: '60', resolution_seconds: 60, conservative: true },
    status: 'partial', reason: 'matched_forecast_alarm_originals' }
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: value.trial })
  vi.mocked(riskPathsApi.score).mockResolvedValue(value)
  await mount()
  await until(() => Boolean(container.textContent?.includes('系统命中占比：50%')))
  expect(container.textContent).toContain('保守最短提前 0 秒，中位提前 60 秒')
  expect(container.textContent).toContain('不是用户实际获益或避险成功率')
  expect(container.textContent).toContain('缺失资料不能按零误报')
  expect(container.textContent).not.toContain('matched_forecast_alarm_originals')
})
it('renders a synthetic available interval as a comparison and still refuses trading qualification', async () => {
  // Synthetic view seam only; a real fixed 28-day trial cannot reach 100 day blocks.
  const value = structuredClone(pathScoreV2FinalFixture)
  const score = value.report!.score
  if (score.schema_version !== 'path-shadow-score.v2') throw new Error('fixture version')
  const ci = { lower: '0.01', upper: '0.05', point_estimate: '0.03', confidence: '0.95' as const,
    protocol: 'utc-day-paired-percentile-bootstrap.v2' as const, protocol_hash: 'a'.repeat(64),
    seed: 271828 as const, replicates: 2000 as const, independent_day_blocks: 100, event_blocks: 30,
    unit: 'UTC-day' as const, estimator: 'equal_day_mean_baseline_minus_candidate_brier' as const }
  score.ci = ci; score.ci_status = 'available'
  score.inference = { ...score.inference, ci, status: 'available', independent_day_blocks: 100,
    event_blocks: 30, expected_pairs: 200, complete_pairs: 200, reasons: [] }
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: value.trial })
  vi.mocked(riskPathsApi.score).mockResolvedValue(value)
  await mount()
  await until(() => Boolean(container.textContent?.includes('固定 95% 日区块区间：0.01 至 0.05')))
  expect(container.textContent).toContain('每个独立日权重相同')
  expect(container.textContent).toContain('不授予晋级资格')
  expect(container.textContent).not.toContain('置信区间尚未登记')
})

it('shows succeeded zero-sample fits as training unavailable before any prediction exists', async () => {
  const training: PathTrainingReadiness = {
    schema_version: 'path-training-readiness.v1', scope_id: PATH_BTC, as_of: '2026-10-10T00:00:10Z',
    status: 'training_unavailable', fit_job_state: 'succeeded', frozen_fit: true,
    read_as_of: '2026-10-09T23:50:00Z', training_cutoff_at: '2026-10-10T00:00:00Z',
    requested_history_start_at: '2026-07-12T00:00:00Z', accepted_windows: 0, excluded_windows: 90,
    baseline_n: 0, candidate_training_n: 0, low_group_n: 0, high_group_n: 0,
    minimum_training_windows: 60, minimum_group_windows: 30, baseline_sample_ready: false, candidate_sample_ready: false,
    available_history_start_at: '2026-10-09T18:00:00Z', available_history_end_at: '2026-10-09T23:50:00Z', available_candle_minutes: 350,
    history_basis: 'native_ledger_metadata_at_fit_read_as_of', accepted_history_start_at: null, accepted_history_end_at: null,
    exclusion_counts: { complete_24h_label_unavailable: 90 }, reasons: ['baseline_training_windows_insufficient', 'low_volatility_group_insufficient'],
    fit_result_hash: 'a'.repeat(64), model_hash: 'b'.repeat(64), probability_qualified: false, promotion_allowed: false,
  }
  defaults().mockResolvedValue({ ...pathCurrentFixture, trial: { ...pathCurrentFixture.trial!, training_readiness: [training, { ...training, scope_id: PATH_ETH }] } })
  await mount()
  await until(() => Boolean(container.textContent?.includes('训练历史不足，当前没有可用前瞻概率')))
  expect(container.textContent).toContain('冻结拟合已保存')
  expect(container.textContent).toContain('0 / 60')
  expect(container.textContent).toContain('0 / 30')
  expect(container.textContent).toContain('已排除 90 个')
  expect(container.textContent).toContain('共 350 个分钟时点')
  expect(container.textContent).toContain('不表示连续、完整或可作为历史回测')
  expect(container.textContent).toContain('只等待 28 日结束不能补齐这次训练缺口')
  expect(container.textContent).toContain('当前不具备晋级资格')
  expect(container.textContent).not.toContain('冻结训练样本条件达到')
})
