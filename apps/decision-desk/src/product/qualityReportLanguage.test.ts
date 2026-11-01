import { expect, it } from 'vitest'
import { qualityEvidenceText, qualityGapText, qualityReportText, qualityStopText } from './qualityReportLanguage'

it('explains known gaps and never invents a meaning for unregistered requirements', () => {
  expect(qualityGapText('market_regime_context')).toBe('对应资产的日线趋势背景')
  expect(qualityGapText('derivatives_crowding')).toBe('对应资产的衍生品仓位与拥挤程度')
  expect(qualityGapText('unregistered_sensitive_id')).not.toContain('unregistered_sensitive_id')
  expect(qualityGapText('unregistered_sensitive_id')).toContain('数据需求未完成')
})

it('explains actual stop codes conservatively and keeps unknown codes unconfirmed', () => {
  expect(qualityStopText('critical_data_unavailable', ['crypto_spot_confirmation'])).toContain('关键市场数据仍未取得')
  expect(qualityStopText('critical_data_unavailable', ['crypto_spot_confirmation'])).toContain('多平台现货交叉确认')
  expect(qualityStopText('sufficient', [])).toContain('以实际质量核验为准')
  expect(qualityStopText('unregistered_stop', [])).not.toContain('已通过')
})

it('labels structured macro levels without inventing publication or vintage proof', () => {
  const raw = JSON.stringify({ series: 'DFII10', value: '2.91', unit: 'yield_percent', observation_date: '2026-10-06', vintage_proven: false })
  const view = qualityEvidenceText(raw)
  expect(view.structured).toBe(true)
  expect(view.text).toContain('美国十年期实际收益率；记录值约 2.91 %；观测日期 2026-10-06；历史修订版本未核验')
  expect(view.text).not.toContain('yield_percent')
  expect(JSON.parse(raw).vintage_proven).toBe(false)
})

it('keeps an actual BTC original identified as BTC without converting it into ETH evidence', () => {
  const view = qualityEvidenceText(JSON.stringify({ dataset_id: 'crypto.btc_usdt.ohlcv_1d', summary: { row_count: '90' } }))
  expect(view.text).toContain('比特币日线')
  expect(view.text).toContain('90 条行情记录')
  expect(view.text).not.toContain('以太坊')
})

it('does not expose unsupported structures, malformed JSON, or English implementation prose as user explanations', () => {
  expect(qualityEvidenceText('{"unknown_internal_field":1}').text).not.toContain('unknown_internal_field')
  expect(qualityReportText('{broken_json')).toContain('原件详情')
  expect(qualityReportText('The DSH round returned candidate Evidence for deterministic product validation.')).toContain('原件详情')
  expect(qualityReportText('仍缺 market_regime_context。')).toBe('仍缺 对应资产的日线趋势背景。')
})


it('explains the real19 macro gaps and default report fields while preserving audit originals', () => {
  const text = 'macro_transmission: 当前 10Y real yield=2.91，5d 上涨0.7787%；open_interest_delta 下行；WRESBAL 2948090 million_usd；DFII10 current series vintage_proven=false；回执为 outlook="range"，sma_60=77542.75。'
  const display = qualityReportText(text)
  expect(display).toContain('宏观因素传导')
  expect(display).toContain('十年期实际收益率=2.91')
  expect(display).toContain('过去5天 上涨0.7787%')
  expect(display).toContain('未平仓量变化 下行')
  expect(display).toContain('2948090 百万美元')
  expect(display).toContain('历史修订版本未核验')
  expect(display).toContain('日线规则倾向为区间/趋势分歧')
  expect(display).toContain('60日均价：77542.75')
  for (const key of ['macro_transmission','usd_reserve_balances','real_yield_10y','inflation_compensation_10y']) expect(qualityGapText(key)).not.toContain('原件详情')
  expect(qualityReportText('原记录：'+text)).toBe('原记录：'+text)
})
