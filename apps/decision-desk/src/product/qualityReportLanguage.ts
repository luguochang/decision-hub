import { researchDisplayText } from './researchLanguage'

const gaps: Record<string, string> = {
  market_regime_context: '对应资产的日线趋势背景',
  derivatives_crowding: '对应资产的衍生品仓位与拥挤程度',
  crypto_spot_confirmation: '对应资产的多平台现货交叉确认',
  market_narrative: '近期公开信息与市场叙事',
  event_identity: '事件身份与发生时点',
  policy_or_data_delta: '政策或数据相对前值的变化',
  expectation_pricing: '事前预期及市场定价',
  macro_transmission: '宏观因素传导', cross_asset_confirmation: '跨资产确认', usd_reserve_balances: '美元准备金余额',
  real_yield_10y: '美国十年期实际收益率', inflation_compensation_10y: '十年期通胀补偿',
}
export function qualityGapText(value: string): string {
  return gaps[value] ?? (/\p{Script=Han}/u.test(value) ? qualityReportText(value) : '尚有一项数据需求未完成，具体内容保留在原件详情。')
}

export function qualityReportText(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return '原报告没有提供这项说明。'
  if (/^(?:原记录|原始记录|原文)\s*[:：]/.test(trimmed)) return value
  if (/^[{[]/.test(trimmed) || !/\p{Script=Han}/u.test(trimmed)) {
    return gaps[trimmed] ?? '原报告保存了尚未整理为中文的说明，可在原件详情核对。'
  }
  return researchDisplayText(Object.entries(gaps).reduce((text, [key, label]) => text.replaceAll(key, label), value))
}

export function qualityStopText(code: string, remaining: string[], reportStatus: 'complete' | 'incomplete' | 'untrusted' = 'incomplete'): string {
  const reasons: Record<string, string> = {
    critical_data_unavailable: '关键市场数据仍未取得，本轮已结束，取得的证据和未完成的分析记录保留。',
    sufficient: '本轮已提交分析并结束；结论可用范围以实际质量核验为准。',
    budget_exhausted: '本轮到达研究资源上限，尚未补齐的问题继续保留。',
    deadline_reached: '本轮到达处理截止时间，未完成内容继续保留。',
    tool_limit_reached: '本轮到达取证次数上限，未完成内容继续保留。',
    error: '本轮执行遇到异常，原停止记录保留供核对。',
  }
  const completeReasons: Record<string, string> = {
    critical_data_unavailable: '本轮已完成并保存报告；关键市场数据仍未取得，结论可用范围受数据缺口和质量审核限制。',
    sufficient: '本轮已完成并保存报告；结论可用范围以实际质量核验为准。',
    budget_exhausted: '本轮到达研究资源上限，报告已完成并保存，尚未补齐的问题继续保留。',
    deadline_reached: '本轮到达处理截止时间，报告已完成并保存，尚未补齐的问题继续保留。',
    tool_limit_reached: '本轮到达取证次数上限，报告已完成并保存，尚未补齐的问题继续保留。',
    error: '本轮执行遇到异常，报告已完成并保存，原停止记录保留供核对。',
  }
  const main = reportStatus === 'complete'
    ? completeReasons[code] ?? '本轮已完成并保存报告，原停止说明和结论限制保留在原件详情。'
    : reasons[code] ?? '本轮研究已结束，原停止说明保留在原件详情。'
  return remaining.length ? `${main}仍缺：${remaining.map(qualityGapText).join('、')}。` : main
}

const instruments: Record<string, string> = {
  'BTC-USDT': '比特币市场', BTC_USDT: '比特币市场', 'BTC-USDT-SWAP': '比特币永续合约',
  'ETH-USDT': '以太坊市场', ETH_USDT: '以太坊市场', 'ETH-USDT-SWAP': '以太坊永续合约',
  'crypto.btc_usdt.ohlcv_1d': '比特币日线', 'crypto.eth_usdt.ohlcv_1d': '以太坊日线',
  US10Y: '美国十年期国债收益率', DXY: '美元指数', SPX: '标普指数', VIX: '市场波动率指数',
  WRESBAL: '美元准备金余额', DFII10: '美国十年期实际收益率', T10YIE: '十年期通胀补偿',
}
const units: Record<string, string> = { percent: '%', yield_percent: '%', million_usd: '百万美元', 'million USD': '百万美元', USDT: 'USDT', index_points: '点' }
const fields: Record<string, string> = {
  funding_rate: '资金费率', basis: '期现基差', open_interest: '未平仓量', open_interest_delta: '未平仓量变化',
  bid_depth: '买方深度', ask_depth: '卖方深度', book_imbalance: '买卖盘失衡度', crowding_signal: '拥挤度代理指标',
}
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function numberText(value: unknown): string | null {
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(value))) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) : null
}
export function qualityEvidenceText(excerpt: string): { text: string; structured: boolean } {
  let parsed: unknown
  try { parsed = JSON.parse(excerpt) } catch { return { text: qualityReportText(excerpt), structured: false } }
  const data = record(parsed)
  if (!data) return { text: '这条依据为结构化资料，详细内容保留在原文。', structured: true }
  const key = [data.instrument, data.dataset_id, data.series].find(value => typeof value === 'string' && instruments[value])
  const parts = [typeof key === 'string' ? instruments[key] : '结构化资料']
  const point = numberText(data.spot_price ?? data.level ?? data.value)
  const unit = typeof data.unit === 'string' ? units[data.unit] : null
  if (point) parts.push(`记录值约 ${point}${unit ? ` ${unit}` : data.spot_price && /USDT/.test(String(data.instrument)) ? ' USDT' : '（单位见原文）'}`)
  if (typeof data.observation_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(data.observation_date)) parts.push(`观测日期 ${data.observation_date}`)
  if (typeof data.latest_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(data.latest_date)) parts.push(`资料日期 ${data.latest_date}`)
  if (typeof data.latest_close_at === 'string' && Number.isFinite(Date.parse(data.latest_close_at))) parts.push(`末条收盘 ${new Date(data.latest_close_at).toLocaleString('zh-CN', { hour12: false })}`)
  const summary = record(data.summary)
  const rows = numberText(data.observed_bars ?? summary?.row_count)
  if (rows) parts.push(`原件包含 ${rows} 条行情记录`)
  const values = record(data.values)
  const present = values ? Object.keys(fields).filter(field => Object.hasOwn(values, field)).map(field => fields[field]) : []
  if (present.length) parts.push(`包含${present.join('、')}记录，数值与单位见原文`)
  if (data.vintage_proven === false) parts.push('历史修订版本未核验')
  if (parts.length === 1) parts.push('详细字段尚未整理为中文，保留在原文')
  return { text: `${parts.join('；')}。`, structured: true }
}
