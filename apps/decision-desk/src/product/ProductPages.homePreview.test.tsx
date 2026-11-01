// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { researchHomeSchema } from '../api/researchHome'
import { ProductPages, recentRunHeading } from './ProductPages'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const original = 'BTC未来7个自然日规则偏多；SMA20、SMA60 原始摘要保持逐字可读。'
const oldSeparator = '\n\n原报告摘要（非综合判断）：\n'
const synthesis = '本次 AI 综合判断：30 分钟：中性观察；24 小时：未形成方向；72 小时：偏多。仅供研究；确认与失效条件见报告。'
const legacy = '旧版本报告摘要：综合判断来源未确认，原文仅作参考。'
const now = '2026-10-01T00:50:45Z'
type Preview = NonNullable<Awaited<ReturnType<typeof api.researchHome>>['latest_report_preview']>
const preview = (kind: Preview['kind'], text: string, overrides: Partial<Preview> = {}): Preview => ({ run_id: 'home-run', artifact_id: 'home-artifact', kind, text, ...overrides })
const cases: readonly [string, string, Preview | null | undefined, string][] = [
  ['saved synthesis', original, preview('synthesis', synthesis), synthesis],
  ['legacy old API', original, undefined, legacy],
  ['null preview', original, null, legacy],
  ['unverified result', original, preview('unverified', '已保存的研究结果暂不可核验，原始内容仅作参考。'), '已保存的研究结果暂不可核验，原始内容仅作参考。'],
  ['rejected report', original, preview('unavailable', '这份报告未通过审核，不能作为可用的综合判断。'), '这份报告未通过审核，不能作为可用的综合判断。'],
  ['legacy colliding headline', '伪综合主段' + oldSeparator + '剩余原文', undefined, legacy],
  ['legal original containing old marker', original + oldSeparator + '合法原文剩余内容', preview('synthesis', synthesis), synthesis],
  ['mismatched preview Run', original, preview('synthesis', synthesis, { run_id: 'another-run' }), legacy],
  ['mismatched unavailable preview', original, preview('unavailable', '另一任务没有形成结论', { run_id: 'another-run' }), legacy],
  ['mismatched preview Artifact', original, preview('synthesis', synthesis, { artifact_id: 'another-artifact' }), legacy],
]

it.each(cases)('keeps %s preview separate from the untouched original and opens the same report', async (_, headline, explicitPreview, expected) => {
  const home = researchHomeSchema.parse({
    latest_report: {
      schema_version: 'research-run-view.v2', run_id: 'home-run', event_id: 'home-event',
      event_title: 'BTC 与宏观快照研究', admission_origin: 'scheduled_recheck', priority: 'normal',
      status: 'research_only', stage: 'done', runtime_id: 'saved-research', profile_ref: null,
      current_round: 1, budget: null, budget_basis: 'unavailable', coverage: null,
      coverage_basis: 'unavailable', coverage_cutoff_at: null, current_action: null,
      stop_reason: null, failure: null, latest_sequence_no: 0, artifact_id: 'home-artifact',
      available_at: null, parent_run_id: 'previous-run', created_at: now, updated_at: now,
    },
    latest_report_at: now, latest_report_headline: headline, latest_report_preview: explicitPreview, latest_run: null,
    active_runs: [], active_count: 0, generated_at: now,
  })
  vi.spyOn(api, 'researchHome').mockResolvedValue(home)
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  const submit = vi.spyOn(api, 'submitObservation')
  const navigate = vi.fn()
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" onNavigate={navigate} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.querySelector('.product-report-preview'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.querySelector('.product-report-preview')?.textContent).toBe(expected)
    expect(container.querySelector('.product-report-preview')?.textContent).not.toContain('7个自然日')
    const originalDetails = container.querySelector<HTMLDetailsElement>('.product-report-original')
    expect(originalDetails?.open).toBe(false)
    expect(originalDetails?.querySelector('summary')?.textContent).toBe('查看原报告摘要（非综合判断）')
    expect(originalDetails?.querySelector('p')?.textContent).toBe(headline)
    const unavailable = explicitPreview?.kind === 'unavailable' && explicitPreview.run_id === 'home-run' && explicitPreview.artifact_id === 'home-artifact'
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes(unavailable ? '查看材料与限制' : '阅读结论与依据'))!
    await act(async () => open.click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'home-run', back: '#/home' })
    const previous = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '查看上一版研究')
    if (unavailable) {
      expect(container.querySelector('.product-home-report-title')?.textContent).toBe('本次未形成可用的综合判断')
      expect(container.querySelector('.product-status')?.textContent).toBe('未形成综合判断')
      expect(previous).toBeDefined()
      await act(async () => previous!.click())
      expect(navigate).toHaveBeenLastCalledWith('report', { run: 'previous-run', back: '#/home' })
    } else expect(previous).toBeUndefined()
    expect(submit).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})

it.each([
  { kind: 'unknown' }, { run_id: '' }, { artifact_id: '' }, { text: '' }, { extra_claim: true },
])('rejects invalid explicit preview rather than using headline content (%j)', (change) => {
  expect(() => researchHomeSchema.shape.latest_report_preview.parse({ ...preview('synthesis', synthesis), ...change })).toThrow()
})

it('shows an exact V5 publication as saved perpetual research without claiming approval or exposing the English title', async () => {
  const savedText = '已保存永续研究，请打开原报告核对结论、场景概率、独立审核与适用限制。'
  const englishTitle = 'Pre-event perpetual decision for BTC-USDT-SWAP'
  const home = researchHomeSchema.parse({
    latest_report: {
      schema_version: 'research-run-view.v2', run_id: 'home-run', event_id: 'home-event',
      event_title: englishTitle, admission_origin: 'manual', priority: 'normal',
      status: 'research_only', stage: 'done', runtime_id: 'saved-research', profile_ref: null,
      research_profile: 'perpetual_decision_v2', current_round: 1, budget: null,
      budget_basis: 'unavailable', coverage: null, coverage_basis: 'unavailable',
      coverage_cutoff_at: null, current_action: null, stop_reason: null, failure: null,
      latest_sequence_no: 0, artifact_id: 'home-artifact', available_at: null,
      parent_run_id: null, created_at: now, updated_at: now,
    },
    latest_report_at: now, latest_report_headline: original,
    latest_report_preview: preview('perpetual', savedText), latest_run: null,
    active_runs: [], active_count: 0, generated_at: now,
  })
  vi.spyOn(api, 'researchHome').mockResolvedValue(home)
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  const navigate = vi.fn()
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" onNavigate={navigate} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.querySelector('.product-report-preview'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.querySelector('.product-home-report-title')?.textContent).toBe('最近已保存的永续事前研究')
    expect(container.querySelector('.product-report-preview')?.textContent).toBe(savedText)
    expect(container.querySelector('.product-status')?.textContent).toBe('已保存，待核对原报告')
    expect(container.querySelector('.product-report-task')).toBeNull()
    expect(container.querySelector('.product-report-question')?.textContent).toContain(englishTitle)
    expect(container.textContent).not.toContain('独立审核已通过')
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('打开原报告核对'))!
    await act(async () => open.click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'home-run', back: '#/home' })
    expect(recentRunHeading({ event_title: englishTitle, research_profile: 'perpetual_decision_v2' })).toBe('永续合约事前研究')
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})
