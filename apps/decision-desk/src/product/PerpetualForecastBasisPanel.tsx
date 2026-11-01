import type { PerpetualForecastBasis } from '../api/perpetualForecastBasis'

const names = { up: '上涨', neutral: '横盘', down: '下跌' }
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
export function PerpetualForecastBasisPanel({ basis }: { basis?: PerpetualForecastBasis | null }) {
  if (!basis) return <section aria-label="概率来源对照"><h4>概率来源对照</h4><p>本次研究未保存可用历史基准率；对照未知。</p></section>
  const original = basis.research_materials.direction_base_rate, window = original.window_90d
  return <section aria-label="概率来源对照"><h4>历史基准率与模型概率</h4>
    <p>同一 {basis.horizon} 方向定义；历史样本窗口（北京时间）：<time dateTime={window.window_start}>{time(window.window_start)}</time> 至 <time dateTime={window.window_end}>{time(window.window_end)}</time>，资料截止 <time dateTime={original.as_of}>{time(original.as_of)}</time>。</p>
    <ul>{basis.baseline_probabilities.map(row => <li key={row.outcome_id}>{names[row.outcome_id]}：历史基准率 {row.value}；模型概率 {basis.model_probabilities?.find(model => model.outcome_id === row.outcome_id)?.value ?? '未知'}（原始概率，1 表示 100%）</li>)}</ul>
    <p>{basis.relation === 'equal' ? '与历史基准率相同' : basis.relation === 'unknown' ? '模型方向概率未知，无法计算差值。' : `最大绝对差：${basis.max_absolute_difference_pp} 个百分点。`}</p>
    <p>历史频率不含本次事件条件；模型保留原预测条件。数值差异不证明预测增益，也不能换算成可交易胜率。调整理由及证据请追溯原研究报告。</p>
    <small>同次研究冻结来源：{basis.research_materials_ref.ref} · {basis.research_materials_ref.hash}</small>
  </section>
}
