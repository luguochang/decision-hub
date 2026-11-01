// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import { PromotionReviewPanel } from './PromotionReviewPanel'
import type { PromotionReview } from '../api/types'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('explains incomplete evidence in Chinese and keeps raw review identifiers in a disclosure', async () => {
  const review: PromotionReview = { domain_pack_ref: 'crypto_macro.v1', candidate_id: 'internal-candidate', evaluation_refs: ['internal-result'], eligible: false, deltas: [], checks: [
    { rule_id: 'cost.known:internal-result', status: 'fail', reason_code: 'promotion_cost_unknown_or_invalid', detail: 'baseline=None' },
    { rule_id: 'sample.minimum:internal-result', status: 'fail', reason_code: 'promotion_sample_count_insufficient', detail: 'candidate=1' },
  ] }
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<PromotionReviewPanel review={review} reviewing={false} />))
    const conditions = host.querySelector('.review-checks')!
    expect(conditions.textContent).toContain('费用依据完整 · 未通过')
    expect(conditions.textContent).toContain('未知费用不能当作零')
    expect(conditions.textContent).toContain('至少 100 个有效样本')
    expect(conditions.textContent).not.toContain('internal-result')
    expect(host.querySelector('details')?.open).toBe(false)
    expect(host.querySelector('pre')?.textContent).toContain('promotion_cost_unknown_or_invalid')
    await act(async () => root.render(<PromotionReviewPanel review={null} reviewing={false} />))
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('暂不能启用')
  } finally { await act(async () => root.unmount()); host.remove() }
})
