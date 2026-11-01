// @vitest-environment jsdom
// Candidate control only: not executed and not an authentication/report acceptance proof.
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { researchQualityApi, researchQualitySchema } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'
import { researchDisplayText } from './researchLanguage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('renders actual31 Chinese prose and bounded price precision while retaining every original gap', async () => {
  const artifact = resolve(process.cwd(), '../../artifacts/market-risk-implementation-20261007/B2/report-language31')
  const fragments = JSON.parse(readFileSync(resolve(artifact, 'fragments31.json'), 'utf8')) as {
    price: string; probability: string; qualification: string; gaps: string[]
  }
  const originalFragments = JSON.stringify(fragments)
  const price = researchDisplayText(fragments.price)
  expect(price).toContain('20日均价 84247.41')
  expect(price).toContain('60日均价 约 77823.69')
  expect(price).toContain('20日区间为 80130.8 至 87401.5')
  expect(researchDisplayText(fragments.probability)).toContain('研究时间窗口 的 主观概率')
  const qualification = researchDisplayText(fragments.qualification)
  expect(qualification).toContain('金融效果资格未通过')
  expect(qualification).toContain('研究预算资格未通过')
  expect(qualification).toContain('触发条件 和 失效条件')
  const boundaries = '说明：主观概率 0.000000123456789；样本 77823.68833333333333333333333；2026-10-09；收盘价 0.000000123456789；raw_id=sma_60；`sma_60 77823.68833333333333333333333`；"financial_qualified=false"；financial_qualified=true；https://example.test/sma_60/77823.68833333333333333333333'
  const displayed = researchDisplayText(boundaries)
  for (const exact of ['0.000000123456789', '样本 77823.68833333333333333333333', '2026-10-09', 'raw_id=sma_60', '`sma_60 77823.68833333333333333333333`', '"financial_qualified=false"', 'financial_qualified=true', 'https://example.test/sma_60/77823.68833333333333333333333']) expect(displayed).toContain(exact)
  expect(researchDisplayText('原文：' + fragments.price)).toBe('原文：' + fragments.price)
  const fixture = researchQualitySchema.parse(JSON.parse(readFileSync(resolve(process.cwd(),
    '../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json'), 'utf8')))
  const decision = fixture.decision!
  decision.unknowns = [...fragments.gaps, fragments.gaps[0]]
  decision.unmapped_inquiries = [...decision.unknowns]
  const originalDecision = JSON.stringify(decision)
  const rawReport = decision.full_report_json
  const fullReportHash = decision.full_report_hash
  vi.spyOn(researchQualityApi, 'read').mockResolvedValue(fixture)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={fixture.run_id} />))
    expect(element.querySelector('[data-quality-unknowns]')?.children).toHaveLength(new Set(fragments.gaps).size)
    const unmapped = element.querySelector<HTMLDetailsElement>('[data-quality-unmapped]')!
    expect(unmapped.open).toBe(false)
    expect(unmapped.querySelectorAll('li')).toHaveLength(new Set(fragments.gaps).size)
    const originals = Array.from(element.querySelectorAll<HTMLDetailsElement>('[data-quality-original]'))
    for (const title of ['查看全部缺口原文', '查看数据接入问题原文']) {
      const detail = originals.find(item => item.firstElementChild?.textContent === title)!
      await act(async () => { detail.open = true; detail.dispatchEvent(new Event('toggle')) })
      expect(detail.querySelector('pre')?.textContent).toBe(JSON.stringify(decision.unknowns, null, 2))
    }
    expect(JSON.stringify(decision)).toBe(originalDecision)
    expect(decision.full_report_json).toBe(rawReport)
    expect(decision.full_report_hash).toBe(fullReportHash)
    expect(JSON.stringify(fragments)).toBe(originalFragments)
  } finally {
    await act(async () => root.unmount()); element.remove(); vi.restoreAllMocks()
  }
})
