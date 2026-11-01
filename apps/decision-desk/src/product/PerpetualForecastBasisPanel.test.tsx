import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { PerpetualForecastBasisPanel } from './PerpetualForecastBasisPanel'
import type { PerpetualForecastBasis } from '../api/perpetualForecastBasis'

it('keeps missing basis unknown and never labels numeric equality as baseline-only', () => {
  expect(renderToStaticMarkup(<PerpetualForecastBasisPanel basis={null} />)).toContain('本次研究未保存可用历史基准率')
  for (const relation of ['equal', 'different', 'unknown'] as const) {
    const basis = { relation, max_absolute_difference_pp: relation === 'equal' ? '0' : relation === 'different' ? '0.0001' : null,
      baseline_probabilities: [{ outcome_id: 'up', value: '0.34' }, { outcome_id: 'neutral', value: '0.33' }, { outcome_id: 'down', value: '0.33' }],
      model_probabilities: relation === 'unknown' ? null : [{ outcome_id: 'up', value: '0.34' }],
      research_materials: { direction_base_rate: { as_of: '2026-01-01T20:30:00Z', window_90d: { window_start: '2025-10-01T00:00:00Z', window_end: '2026-01-01T00:00:00Z' } } },
      horizon: '4h', research_materials_ref: { ref: 'frozen-source' } } as unknown as PerpetualForecastBasis
    const html = renderToStaticMarkup(<PerpetualForecastBasisPanel basis={basis} />)
    expect(html).not.toContain('仅基准率'); expect(html).not.toContain('无模型价值')
    expect(html).toContain(relation === 'equal' ? '与历史基准率相同' : relation === 'different' ? '0.0001 个百分点' : '模型方向概率未知')
    expect(html).toContain('历史基准率'); expect(html).toContain('frozen-source')
    if (relation === 'equal') expect(html).toContain('2026/1/2 04:30:00')
  }
})
