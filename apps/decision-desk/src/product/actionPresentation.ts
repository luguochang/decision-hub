import type { ActionBranch } from '../api/actionPlans'

export const actionLabels = { enter_long: '满足条件后考虑买入', reduce_long: '按条件减持', exit_long: '按条件退出多仓', hold: '维持当前安排', wait: '等待更清晰的机会' }
export const actionTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚无记录'

export function probabilityText(probability: ActionBranch['probability']): string {
  if (!probability || probability.value === null) return '未校准 · 暂无可用概率'
  const number = `${(probability.value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
  return probability.kind === 'subjective' ? `${number} · 主观估计，尚未校准` : `${number} · 已校准概率`
}

type Expression = { op: string; args?: Expression[]; feature?: string; cmp?: string; threshold?: string; unit?: string }
export function conditionDescription(value: unknown): string {
  const node = value as Expression
  if (node.op === 'all' || node.op === 'any') return `${node.op === 'all' ? '同时满足' : '满足任一'}：${node.args?.map(conditionDescription).join('；') ?? ''}`
  const features: Record<string, string> = { close: '最近完整分钟收盘价', box_high_6h: '此前 6 小时最高价', box_low_6h: '此前 6 小时最低价', risk_unit_20h: '近 20 小时波动单位', volume_ratio_1h: '最近小时相对成交量', return_6h: '6 小时收益率' }
  const comparisons: Record<string, string> = { gt: '高于', gte: '达到或高于', lt: '低于', lte: '达到或低于' }
  let amount = node.threshold ?? '待确认'
  if (node.unit === 'fraction') amount = `${(Number(amount) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 6 })}%`
  else if (node.unit === 'ratio') amount += ' 倍'
  else if (node.unit?.startsWith('USDT/')) amount += ` USDT / ${node.unit.slice(5)}`
  return `${features[node.feature ?? ''] ?? '指定市场指标'}${comparisons[node.cmp ?? ''] ?? '达到指定门槛'} ${amount}`
}
