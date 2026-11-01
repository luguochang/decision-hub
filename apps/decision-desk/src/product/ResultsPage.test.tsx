// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnalyticalCohortView, AnalyticalOutlookDetailView } from '@decision-hub/contracts-ts'
import { api } from '../api/client'
import { assets } from '../api/assets'
import { ResultsPage } from './ResultsPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const stamp = '2026-10-02T16:26:06Z'
const item: AnalyticalOutlookDetailView['outlook'] = {
  outlook_id: 'outlook-72h', run_id: 'run-results', artifact_id: 'artifact-results', study_id: 'study-results',
  market: 'crypto', instrument: 'BTC-USDT-SWAP', method_family: 'ai_synthesis', method_version: 'research.v1',
  horizon: '72h', action: 'long', subjective_probability: 0.58, probability_status: 'uncalibrated',
  data_as_of: '2026-10-02T16:22:07Z', window_start_at: stamp, window_end_at: '2026-10-05T16:26:06Z',
  result_hash: 'original-result-hash', evidence_refs: ['evidence-original'], fact_version_refs: ['fact-original'],
  trigger: '触发条件：72h long 候选成立仍需核对。',
  invalidation: '若风险延续，应把 72 小时 long 降为 neutral 或 no_trade。',
  cohort: 'prospective', eligibility: 'eligible', exclusion_reasons: [], research_quality: 'sufficient', recorded_at: stamp,
  observation: { status: 'pending', attempt: 0, next_attempt_at: '2026-10-05T16:26:06Z', error_code: null, market_return_pct: null, directional_return_pct: null, direction_correct: null, quality_status: null, benchmark: null, evaluated_at: null },
}
const detail: AnalyticalOutlookDetailView = { outlook: item, raw_payload: { thesis: '原始完整判断，条件见原文。' }, window: null, attempts: [], attempts_total: 0, attempts_limit: 10, attempts_offset: 0 }
const recipeHash = 'a'.repeat(64)
const recipeGroup: AnalyticalCohortView = {
  market: item.market, instrument: item.instrument, method_family: item.method_family,
  method_version: null, stable_recipe_hash: recipeHash, recipe_status: 'verified', saved_method_versions: ['saved.v1', 'saved.v2'],
  horizon: item.horizon, action: 'long', cohort: 'prospective', research_quality: 'sufficient', quality_status: 'observed',
  total: 36, completed: 36, pending: 0, retry: 0, excluded: 0, directional_count: 30, correct_count: 24,
  distinct_studies: 30, directional_hit_rate: 0.8, average_directional_return_pct: 1, minimum_rate_samples: 30,
  calibration_status: 'not_calibration', assessment_version: 'authenticated-recipe-originals.v2',
  record_directional_count: 36, record_correct_count: 30, fixed_sample_count: 30, repeat_count: 6,
  source_verified_count: 30, label_verified_count: 30, unverified_count: 0, method_recipe_hashes: [recipeHash],
  assessment_reasons: {}, assessment_checked_count: 36, assessment_pending_count: 0,
}
let root: Root, container: HTMLDivElement, client: QueryClient
beforeEach(() => {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [item], total: 1, limit: 12, offset: 0 })
  vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
  vi.spyOn(assets, 'outlook').mockResolvedValue(detail)
  vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
})

describe('authenticated recipe grouping and progress', () => {
  it('opens one authenticated recipe across saved aliases while preserving the original return context', async () => {
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [recipeGroup], total: 1, limit: 12, offset: 0 })
    const original = new URLSearchParams({ run: item.run_id, back: '#/research', retained: 'keep' })
    const before = original.toString(), navigate = await mount(original)
    await act(async () => button('查看这组记录').click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ stable_recipe_hash: recipeHash, method_version: undefined, recipe_status: undefined, run: item.run_id, back: '#/research', retained: 'keep' }))
    expect(original.toString()).toBe(before)
    const audit = [...container.querySelectorAll('summary')].find(node => node.textContent === '方法记录')!.closest('details')!
    expect(audit.hasAttribute('open')).toBe(false)
    expect(audit.textContent).toContain('saved.v1')
    expect(audit.textContent).toContain('saved.v2')
    expect(audit.textContent).toContain(recipeHash)
    expect(container.textContent).toContain('研究配方已核对')
  })

  it('keeps unconfirmed originals separate and sends the unchecked recipe filter without forging a hash', async () => {
    const unknown = { ...recipeGroup, method_version: 'legacy.alias', stable_recipe_hash: null, recipe_status: 'unchecked' as const, saved_method_versions: ['legacy.alias'] }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [unknown], total: 1, limit: 12, offset: 0 })
    const navigate = await mount(new URLSearchParams())
    expect(container.textContent).not.toContain('80.0%')
    expect(container.textContent).toContain('认证统计尚未形成')
    expect(container.textContent).toContain('已覆盖全部记录')
    expect(container.textContent).not.toContain('等待核验')
    await act(async () => button('查看这组记录').click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ method_version: 'legacy.alias', stable_recipe_hash: undefined, recipe_status: 'unchecked' }))
  })

  it('uses actual recipe filters for list and cohorts and clears them together', async () => {
    const navigate = await mount(new URLSearchParams({ stable_recipe_hash: recipeHash, method: 'ai_synthesis', horizon: '72h', back: '#/research' }))
    expect(assets.outlooks).toHaveBeenCalledWith(expect.objectContaining({ stable_recipe_hash: recipeHash }))
    expect(assets.cohorts).toHaveBeenCalledWith(expect.objectContaining({ stable_recipe_hash: recipeHash }))
    expect(container.textContent).toContain('已按核对的研究配方')
    await act(async () => button('清除分组限定').click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ stable_recipe_hash: undefined, recipe_status: undefined, method_version: undefined, method: 'ai_synthesis', horizon: '72h', back: '#/research' }))
  })

  it('shows thirty certified first directions without counting a different unresolved Study', async () => {
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [{ ...recipeGroup, total: 37, distinct_studies: 31, fixed_sample_count: 30, assessment_checked_count: 37, unverified_count: 1, assessment_reasons: { method_identity_ambiguous: 1 } }], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    expect(container.textContent).toContain('80.0%')
    expect(container.textContent).toContain('仅统计本配方已认证的首次方向判断')
    expect(container.textContent).toContain('部分记录的首次身份尚未确认，已排除于方向样本')
  })

  it.each(['missing', 'malformed', 'mismatched'] as const)('rejects $mode recipe identity even when a response advertises a high rate', async mode => {
    const bad = { ...recipeGroup, stable_recipe_hash: mode === 'missing' ? null : mode === 'malformed' ? 'opaque' : 'b'.repeat(64) }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [bad], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    expect(container.textContent).not.toContain('80.0%')
    expect(container.textContent).toContain('认证统计尚未形成')
  })

  it('explains unsupported daily originals after background coverage rather than claiming it is still waiting', async () => {
    const daily = { ...recipeGroup, method_family: 'daily_rule', method_version: 'daily.v1', saved_method_versions: ['daily.v1'], stable_recipe_hash: null, recipe_status: 'unchecked' as const, assessment_reasons: { daily_rule_original_assessment_not_implemented: 36 } }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [daily], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    expect(container.textContent).toContain('日线规则的原件核对尚未支持，暂不生成认证统计')
    expect(container.textContent).toContain('本轮原件检查 36/36 条；已覆盖全部记录')
    expect(container.textContent).not.toContain('等待核验')
    expect(container.textContent).not.toContain('正在继续')
    expect(container.textContent).not.toContain('80.0%')
  })

  it.each(['first-count', 'study-count', 'original-count', 'rate'] as const)('hides a rate when $mode contradicts the advertised certified sample', async mode => {
    const bad = { ...recipeGroup, fixed_sample_count: mode === 'first-count' ? 1 : 30, distinct_studies: mode === 'study-count' ? 1 : 30, source_verified_count: mode === 'original-count' ? 37 : 30, directional_hit_rate: mode === 'rate' ? 0.95 : 0.8 }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [bad], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    expect(container.textContent).not.toContain('80.0%')
    expect(container.textContent).not.toContain('95.0%')
    expect(container.textContent).toContain('核验统计待确认')
  })

  it('does not promise queued background work for historical backfills', async () => {
    const historical = { ...recipeGroup, cohort: 'retrospective', recipe_status: 'unchecked' as const, stable_recipe_hash: null, assessment_checked_count: 0, assessment_pending_count: 36 }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [historical], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams({ cohort: 'retrospective' }))
    expect(container.textContent).toContain('历史档案未纳入后台认证')
    expect(container.textContent).toContain('不纳入前瞻命中率')
    expect(container.textContent).not.toContain('等待后台核验')
    expect(container.textContent).not.toContain('研究配方待确认')
    expect(container.textContent).not.toContain('80.0%')
  })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); vi.restoreAllMocks() })
async function mount(params: URLSearchParams, navigate = vi.fn()) {
  await act(async () => root.render(<QueryClientProvider client={client}><ResultsPage params={params} onNavigate={navigate} /></QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  return navigate
}
function button(text: string) { return [...container.querySelectorAll('button')].find(node => node.textContent === text)! }
function conditionText() { return [...container.querySelectorAll('p')].find(node => node.querySelector('strong')?.textContent === '失效条件：')?.textContent ?? '' }
function triggerText() { return [...container.querySelectorAll('p')].find(node => node.querySelector('strong')?.textContent === '后续确认：')?.textContent ?? '' }
function rawConditions() { return JSON.parse(container.querySelector('[aria-label="原始判断条件"]')!.textContent!) }
const params = () => new URLSearchParams({ outlook: item.outlook_id, run: item.run_id, horizon: item.horizon, status: 'pending', back: '#/report?run=run-results', retained: 'original-filter' })

describe('results direction and time semantics', () => {
  it('preserves repeated ledger records but displays only certified first-sample counts', async () => {
    const group = { market: item.market, instrument: item.instrument, method_family: item.method_family, method_version: item.method_version, horizon: item.horizon, action: 'long', cohort: 'prospective', research_quality: 'sufficient', quality_status: 'estimated', total: 36, completed: 36, pending: 0, retry: 0, excluded: 0, directional_count: 0, correct_count: 0, distinct_studies: 1, directional_hit_rate: null, average_directional_return_pct: null, minimum_rate_samples: 30, calibration_status: 'not_calibration' as const, assessment_version: 'fixed-study-originals.v1' as const, record_directional_count: 36, record_correct_count: 30, fixed_sample_count: 1, repeat_count: 35, source_verified_count: 0, label_verified_count: 0, unverified_count: 1, method_recipe_hashes: [], assessment_reasons: { source_unverifiable: 1 } }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [group], total: 1, limit: 12, offset: 0 })
    vi.mocked(assets.outlook).mockResolvedValue({ ...detail, assessment: { source_status: 'unverifiable', label_status: 'not_due', directional_usable: false } })
    const navigate = await mount(params())
    expect(container.textContent).toContain('固定首次记录 1 · 后续重复 35')
    expect(container.textContent).toContain('认证样本不足（0）')
    expect(container.textContent).toContain('缺少可核对的原件')
    expect(container.textContent).toContain('还未到观察时间')
    expect(container.textContent).not.toContain('83.3%')
    const audit = [...container.querySelectorAll('summary')].find(node => node.textContent === '查看原账本统计')!
    expect(audit.closest('details')?.hasAttribute('open')).toBe(false)
    expect(audit.closest('details')?.textContent).toContain('方向记录 36 条，账本记为命中 30 条')
    await act(async () => button('查看这组记录').click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ action: 'long', method_version: item.method_version }))
  })

  it('shows mismatched originals and verified direction separately without rewriting the saved outlook', async () => {
    const original = { ...detail, outlook: { ...item, observation: { ...item.observation, status: 'completed' as const, direction_correct: true } }, assessment: { source_status: 'invalid', label_status: 'invalid', directional_usable: false } }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    expect(container.textContent).toContain('原件不一致')
    expect(container.textContent).toContain('行情或计算记录不一致')
    expect(container.textContent).toContain('账本记为命中，需核对原件')
    expect(container.textContent).toContain('暂不计入认证方向样本')
    expect(JSON.stringify(original)).toBe(before)
  })

  it.each(['missing-count', 'insufficient', 'missing-recipe', 'mixed-recipes', 'ambiguous-method', 'valid'] as const)('fails closed for $mode rate metadata', async mode => {
    const group = { market: item.market, instrument: item.instrument, method_family: item.method_family, method_version: item.method_version, horizon: item.horizon, action: 'long', cohort: 'prospective', research_quality: 'sufficient', quality_status: 'estimated', total: 36, completed: 36, pending: 0, retry: 0, excluded: 0, directional_count: mode === 'insufficient' ? 1 : 30, correct_count: mode === 'insufficient' ? 1 : 24, distinct_studies: 30, directional_hit_rate: 0.8, average_directional_return_pct: 1, minimum_rate_samples: 30, calibration_status: 'not_calibration' as const, assessment_version: 'fixed-study-originals.v1' as const, record_directional_count: 36, record_correct_count: 30, fixed_sample_count: 30, repeat_count: 6, source_verified_count: mode === 'missing-count' ? undefined : 30, label_verified_count: 30, unverified_count: 0, method_recipe_hashes: mode === 'missing-recipe' ? [] : mode === 'mixed-recipes' ? ['a', 'b'] : ['a'], assessment_reasons: {} }
    const assessment_reasons: Record<string, number> = mode === 'ambiguous-method' ? { method_identity_ambiguous: 1 } : {}
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [{ ...group, assessment_reasons }], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    if (mode === 'valid') expect(container.textContent).toContain('80.0%')
    else expect(container.textContent).not.toContain('80.0%')
    if (mode === 'missing-count') {
      expect(container.textContent).toContain('尚未核验')
      expect(container.textContent).not.toContain('报告核对 0')
    }
  })

  it('keeps a verified repeat as an update instead of presenting it as a first sample', async () => {
    vi.mocked(assets.outlook).mockResolvedValue({ ...detail, assessment: { source_status: 'verified', label_status: 'verified', directional_usable: true, verified_direction_correct: true, selection_status: 'repeat' } })
    await mount(params())
    expect(container.textContent).toContain('后续更新，不替换首次样本')
    expect(container.textContent).toContain('暂不计入认证方向样本')
    expect(container.textContent).not.toContain('核对后命中')
  })

  it('explains an unresolved method identity without presenting its direction as certified', async () => {
    vi.mocked(assets.outlook).mockResolvedValue({ ...detail, assessment: { source_status: 'verified', label_status: 'verified', directional_usable: false, verified_direction_correct: true, selection_status: 'unchecked', limitations: ['method_identity_ambiguous'] } })
    await mount(params())
    expect(container.textContent).toContain('首次记录或研究配方待确认，暂不计入样本')
    expect(container.textContent).not.toContain('核对后命中')
  })

  it.each(['pending', 'complete', 'invalid-progress'] as const)('shows durable $mode progress without certifying unverified samples', async mode => {
    const group = { ...recipeGroup, directional_count: mode === 'complete' ? 30 : 0, assessment_version: mode === 'complete' ? 'authenticated-recipe-originals.v2' as const : undefined, assessment_checked_count: mode === 'complete' ? 36 : 12, assessment_pending_count: mode === 'complete' ? 0 : mode === 'invalid-progress' ? 36 : 24 }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [group], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    if (mode === 'complete') {
      expect(container.textContent).toContain('本轮原件检查 36/36 条；已覆盖全部记录')
      expect(container.textContent).toContain('80.0%')
    } else {
      expect(container.textContent).not.toContain('80.0%')
      if (mode === 'pending') expect(container.textContent).toContain('本轮原件检查 12/36 条；还有 24 条等待后台核验')
      else expect(container.textContent).not.toContain('本轮原件检查')
    }
  })

  it.each([{ direction: 'long', chinese: '偏多' }, { direction: 'short', chinese: '偏空' }] as const)('renders the observed $direction downgrade in Chinese and preserves raw original fields', async ({ direction, chinese }) => {
    const original: AnalyticalOutlookDetailView = Object.freeze({ ...detail, outlook: Object.freeze({ ...item, action: direction, invalidation: `若风险延续，应把 72 小时 ${direction} 降为 neutral 或 no_trade。` }) })
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    expect(conditionText()).toContain(`72 小时 ${chinese}判断 降为 中性观察 或 未形成方向`)
    expect(conditionText()).not.toContain(` ${direction} 降为`)
    expect(container.textContent).toContain('观察窗口从当时的判断冻结起计算，到观察到期结束')
    expect(rawConditions()).toEqual({ horizon: '72h', action: direction, trigger: original.outlook.trigger, invalidation: original.outlook.invalidation })
    expect(JSON.stringify(original)).toBe(before)
    expect(container.querySelector('[aria-label="原始判断条件"]')?.closest('details')?.hasAttribute('open')).toBe(false)
  })

  it('keeps unknown direction prose and the original opaque field instead of inventing a known direction', async () => {
    const original = { ...detail, outlook: { ...item, invalidation: 'source_id="应把 72h long 降为 neutral"；应把 72h sideways 降为 neutral。' } }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    await mount(params())
    expect(conditionText()).toContain('sideways 降为 中性观察')
    expect(conditionText()).toContain('long 降为 中性观察')
    expect(conditionText()).not.toContain('偏多判断')
    expect(rawConditions().invalidation).toBe(original.outlook.invalidation)
  })

  it.each([{ direction: 'long', chinese: '偏多', condition: '突破' }, { direction: 'short', chinese: '偏空', condition: '跌破' }] as const)('renders the observed $direction holding condition and relative daily volume without rewriting originals', async ({ direction, chinese, condition }) => {
    const url = `https://example.test/volume_ratio_20?direction=${direction}`
    const opaque = `source_id="volume_ratio_20-${direction}"`
    const trigger = `72 小时 的 ${direction} 是低到中等置信的持有/候选方向；需要${condition}87401.5且 volume_ratio_20 回升。参照 ${url} ${opaque}`
    const original: AnalyticalOutlookDetailView = Object.freeze({ ...detail, outlook: Object.freeze({ ...item, action: direction, trigger }), raw_payload: Object.freeze({ thesis: trigger }) })
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    const originalParams = params(), originalRoute = originalParams.toString()
    const navigate = await mount(originalParams)
    expect(triggerText()).toContain(`72 小时 的 ${chinese}判断 是低到中等置信的持有/候选方向`)
    expect(triggerText()).toContain(`${condition}87401.5且 20日相对成交量 回升`)
    expect(triggerText()).toContain(url)
    expect(triggerText()).toContain(opaque)
    expect(triggerText()).not.toContain(` 的 ${direction} 是`)
    expect(rawConditions().trigger).toBe(trigger)
    const explanation = [...container.querySelectorAll('summary')].find(node => node.textContent === '当时保存的完整解释')!
    expect(explanation.closest('details')?.querySelector('p')?.textContent).toBe(trigger)
    expect(JSON.stringify(original)).toBe(before)
    await act(async () => button('回到对应报告').click())
    expect(navigate).toHaveBeenLastCalledWith('report', { run: item.run_id, back: `#/results?${originalRoute}` })
    expect(originalParams.toString()).toBe(originalRoute)
  })

  it('preserves original report/fact return links, list filters and an unknown cohort direction value', async () => {
    const group = { market: item.market, instrument: item.instrument, method_family: item.method_family, method_version: item.method_version, horizon: item.horizon, action: 'sideways', cohort: item.cohort, research_quality: item.research_quality, quality_status: null, total: 1, completed: 0, pending: 1, retry: 0, excluded: 0, directional_count: 0, correct_count: 0, distinct_studies: 1, directional_hit_rate: null, average_directional_return_pct: null, minimum_rate_samples: 30, calibration_status: 'not_calibration' as const }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [group], total: 1, limit: 12, offset: 0 })
    const originalParams = params(), originalRoute = originalParams.toString(), navigate = vi.fn()
    await mount(originalParams, navigate)
    expect(assets.outlooks).toHaveBeenCalledWith(expect.objectContaining({ run_id: item.run_id, horizon: '72h', status: 'pending', limit: 12, offset: 0 }))
    expect(container.querySelector('.asset-back-link')?.getAttribute('href')).toBe('#/report?run=run-results')
    await act(async () => button('回到对应报告').click())
    expect(navigate).toHaveBeenLastCalledWith('report', { run: item.run_id, back: `#/results?${originalRoute}` })
    await act(async () => button('依据 1').click())
    expect(navigate).toHaveBeenLastCalledWith('facts', { version: 'fact-original', as_of: stamp, back: `#/results?${originalRoute}` })
    expect(container.textContent).toContain('待确认')
    await act(async () => button('查看这组记录').click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ action: 'sideways', horizon: '72h', run: item.run_id, retained: 'original-filter', back: '#/report?run=run-results' }))
    expect(originalParams.toString()).toBe(originalRoute)
    expect(group.action).toBe('sideways')
  })
})


describe('original verification recovery explanations', () => {
  it('keeps an unavailable historical verifier separate from mismatched originals and preserves the first-record barrier', async () => {
    const original: AnalyticalOutlookDetailView = {
      ...detail,
      assessment: {
        source_status: 'invalid', source_reason: 'context_selection_implementation_unavailable',
        label_status: 'verified', selection_status: 'unchecked', directional_usable: false,
        verified_direction_correct: true, limitations: ['method_identity_ambiguous'],
      },
    }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    const summary = container.querySelector('[aria-label="效果样本核对"]')!
    expect(summary.textContent).toContain('核验程序待恢复')
    expect(summary.textContent).toContain('需恢复与保存记录匹配的核验程序后重新核对')
    expect(summary.textContent).toContain('首次记录或研究配方待确认，暂不计入样本')
    expect(summary.textContent).not.toContain('原件不一致')
    expect(summary.textContent).not.toContain('核对后命中')
    expect(summary.querySelector('pre')?.textContent).toBe(JSON.stringify(original.assessment, null, 2))
    expect(summary.querySelector('details')?.hasAttribute('open')).toBe(false)
    expect(rawConditions()).toEqual({ horizon: item.horizon, action: item.action, trigger: item.trigger, invalidation: item.invalidation })
    const explanation = [...container.querySelectorAll('summary')].find(node => node.textContent === '当时保存的完整解释')!
    expect(explanation.closest('details')?.querySelector('p')?.textContent).toBe(detail.raw_payload.thesis)
    expect(JSON.stringify(original)).toBe(before)
  })

  it.each(['assessment_loaded_verifier_source_changed', 'context_selection_loaded_source_changed'] as const)('explains the controlled $reason loaded-verifier error without claiming changed originals or certification', async reason => {
    // Rendering controls only. The basis guard normally hides a stale verdict;
    // D10's separate selector guard emits its exact reason when loaded files drift.
    const original: AnalyticalOutlookDetailView = {
      ...detail,
      assessment: {
        source_status: 'invalid', source_reason: reason,
        label_status: 'not_due', selection_status: 'unchecked', directional_usable: false,
      },
    }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    const summary = container.querySelector('[aria-label="效果样本核对"]')!
    expect(summary.textContent).toContain('核验程序待恢复')
    expect(summary.textContent).toContain('需加载匹配的核验程序后重新核对')
    expect(summary.textContent).toContain('还未到观察时间')
    expect(summary.textContent).toContain('暂不计入认证方向样本')
    expect(summary.textContent).not.toContain('原件不一致')
    expect(summary.querySelector('pre')?.textContent).toBe(JSON.stringify(original.assessment, null, 2))
    expect(JSON.stringify(original)).toBe(before)
  })

  it('retains the mismatch explanation for actual replay differences without rewriting originals', async () => {
    const original: AnalyticalOutlookDetailView = {
      ...detail,
      assessment: {
        source_status: 'invalid', source_reason: 'context_selection_source_projection_changed',
        label_status: 'invalid', selection_status: 'unchecked', directional_usable: false,
      },
    }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    const summary = container.querySelector('[aria-label="效果样本核对"]')!
    expect(summary.textContent).toContain('原件不一致')
    expect(summary.textContent).toContain('行情或计算记录不一致')
    expect(summary.textContent).not.toContain('核验程序待恢复')
    expect(summary.textContent).not.toContain('需恢复与保存记录匹配')
    expect(summary.querySelector('pre')?.textContent).toBe(JSON.stringify(original.assessment, null, 2))
    expect(JSON.stringify(original)).toBe(before)
  })

  it('keeps complete inspection coverage distinct from certification when the verifier is unavailable', async () => {
    const original: AnalyticalCohortView = {
      ...recipeGroup,
      stable_recipe_hash: null, recipe_status: 'unchecked', assessment_version: undefined,
      directional_count: 0, correct_count: 0, directional_hit_rate: null,
      average_directional_return_pct: null, fixed_sample_count: 0, repeat_count: 0,
      source_verified_count: 0, label_verified_count: 0, unverified_count: 36,
      method_recipe_hashes: [], assessment_reasons: { context_selection_implementation_unavailable: 36 },
    }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [original], total: 1, limit: 12, offset: 0 })
    const before = JSON.stringify(original)
    await mount(new URLSearchParams())
    expect(container.textContent).toContain('本轮原件检查 36/36 条；已覆盖全部记录')
    expect(container.textContent).toContain('认证统计尚未形成')
    expect(container.textContent).toContain('部分记录需恢复匹配的核验程序后重新核对')
    expect(container.textContent).not.toContain('缺少可核对的方法原件')
    expect(container.textContent).not.toContain('80.0%')
    expect(container.textContent).not.toContain('核对后命中')
    const audit = [...container.querySelectorAll('summary')].find(node => node.textContent === '查看原账本统计')!.closest('details')!
    expect(audit.hasAttribute('open')).toBe(false)
    expect(audit.querySelector('pre')?.textContent).toBe(JSON.stringify(original.assessment_reasons, null, 2))
    expect(audit.textContent).toContain('方向记录 36 条，账本记为命中 30 条')
    expect(JSON.stringify(original)).toBe(before)
  })
})

describe('background detail verification pending', () => {
  it.each(['assessment_background_pending', 'assessment_recheck_pending'] as const)('preserves originals and waits for background verification for %s', async reason => {
    const original: AnalyticalOutlookDetailView = { ...detail, assessment: {
      source_status: 'unverifiable', method_status: 'unverifiable', source_reason: reason,
      label_status: 'unverifiable', label_reason: reason, selection_status: 'unchecked', directional_usable: false,
    } }
    vi.mocked(assets.outlook).mockResolvedValue(original)
    const before = JSON.stringify(original)
    await mount(params())
    const summary = container.querySelector('[aria-label="效果样本核对"]')!
    expect(summary.textContent).toContain('等待后台核验')
    expect(summary.textContent).toContain('完成核验前不计入认证效果样本')
    expect(summary.textContent).not.toContain('缺少可核对的原件')
    expect(summary.textContent).not.toContain('缺少行情原件')
    expect(summary.textContent).not.toContain('核对后命中')
    expect(summary.querySelector('pre')?.textContent).toBe(JSON.stringify(original.assessment, null, 2))
    expect(rawConditions()).toEqual({ horizon: item.horizon, action: item.action, trigger: item.trigger, invalidation: item.invalidation })
    expect(container.textContent).toContain(detail.raw_payload.thesis)
    expect(JSON.stringify(original)).toBe(before)
    expect(summary.textContent).toContain('可点“刷新观察结果”查看核验进度')
    await act(async () => button('刷新观察结果').click())
    expect(assets.outlook).toHaveBeenCalledTimes(2)
    expect(assets.outlook).toHaveBeenLastCalledWith(item.outlook_id, { limit: 10, offset: 0 })
  })
  it('does not call pending recipe verification missing originals', async () => {
    const pending = { ...recipeGroup, recipe_status: 'unchecked' as const, stable_recipe_hash: null,
      method_recipe_hashes: [], directional_count: 0, fixed_sample_count: 0, source_verified_count: 0,
      label_verified_count: 0, unverified_count: 36, assessment_checked_count: 0, assessment_pending_count: 36 }
    vi.mocked(assets.cohorts).mockResolvedValue({ items: [pending], total: 1, limit: 12, offset: 0 })
    await mount(new URLSearchParams())
    expect(container.textContent).toContain('等待后台核验研究配方')
    expect(container.textContent).not.toContain('缺少可核对的方法原件')
    expect(container.textContent).not.toContain('80.0%')
  })
})
