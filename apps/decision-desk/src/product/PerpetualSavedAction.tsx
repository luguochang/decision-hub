import type { SavedPerpetualAction } from '../api/perpetualSavedAction'

const actionNames = { open: '开仓', add: '加仓', reduce: '减仓', close: '平仓', conditional: '条件动作', no_change: '维持' }
const sides = { long: '多头', short: '空头', flat: '空仓' }
function percent(value: string | null) {
  if (value === null) return '未知'
  const [whole, fraction = ''] = value.split('.')
  const integer = `${whole}${fraction.padEnd(2, '0').slice(0, 2)}`.replace(/^0+(?=\d)/, '')
  const remainder = fraction.slice(2).replace(/0+$/, '')
  return `${integer}${remainder ? `.${remainder}` : ''}%`
}
function AvailableAction({ value, researchPublishedAt }: {
  value: Extract<SavedPerpetualAction, { status: 'available' }>; researchPublishedAt: string
}) {
  const selected = value.revision.selected_candidate, costs = value.cost_assumptions
  return <section aria-label="已发表方案原件"><h5>发表时入选方案</h5>
    <p>{actionNames[selected.action]}，目标{sides[selected.target_side]}。这是发表时保存的研究选择，不是当前授权。</p>
    <p>研究报告保存于 {researchPublishedAt}；方案发表受理于 {value.accepted_at}。两次发表时点分别保留。</p>
    <p>以下按每 {value.comparison.unit_notional_usdt} USDT 研究参考单位比较，条件概率以假设成交为前提；成交概率未知，账户可行性与实际执行张数未知。</p>
    <table aria-label="发表时方案比较"><thead><tr><th>方案</th><th>条件盈利</th><th>条件持平</th><th>条件亏损</th><th>假设成交单位预期净收益（USDT）</th><th>发表时选择</th></tr></thead>
      <tbody>{value.comparison.candidate_results.map(row => <tr key={row.candidate_id}>
        <td>{actionNames[row.action]}，目标{sides[row.target_side]}</td>
        <td>{percent(row.outcome.conditional_on_fill?.net_profit ?? null)}</td>
        <td>{percent(row.outcome.conditional_on_fill?.zero ?? null)}</td>
        <td>{percent(row.outcome.conditional_on_fill?.net_loss ?? null)}</td>
        <td>{row.unit_expected_net_if_assumed_fills_usdt ?? '未知'}</td>
        <td>{row.candidate_id === value.comparison.selected_action_id ? '入选' : '备选'}</td>
      </tr>)}</tbody></table>
    <details><summary>核对原保存的成本假设与限制</summary>
      <p>假定单边手续费率：{costs.assumed_taker_rate}；假定单边滑点比例：{costs.assumed_slippage_fraction_per_leg}。账户实际费率未经核验。</p>
      <p>参考单位入场成本 {costs.base_cost_policy.entry_trade_cost_usdt} USDT；退出成本 {costs.base_cost_policy.exit_trade_cost_usdt} USDT；资金费基线：{costs.funding_baseline}。未来实际资金费未知。</p>
      <p>未取得金融资格；本页不授予模拟成交或真实交易权限，历史方案不因今天的条件或到期状态而重算。</p>
      <pre>{JSON.stringify({ comparison: value.comparison, costs, gaps: value.snapshot.gaps }, null, 2)}</pre>
    </details>
  </section>
}
export function SavedPerpetualAction({ value, researchPublishedAt }: {
  value: SavedPerpetualAction | undefined; researchPublishedAt: string
}) {
  return value?.status === 'available' ? <AvailableAction value={value} researchPublishedAt={researchPublishedAt} /> : <section aria-label="已发表方案原件">
    <p>{value === undefined ? '此副本未附方案比较，不能据此判断后台是否曾发表方案。' : '该次研究尚未保存已发表的方案比较。'}</p>
    <p>条件盈利概率、单位预期净收益与首选方案保持未知。请到当前建议核对同一原任务的已保存比较。</p>
  </section>
}
