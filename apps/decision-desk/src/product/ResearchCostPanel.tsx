import type { ResearchRunDetailView } from '../api/types'
import { researchDisplayText } from './researchLanguage'
import { ModelUsagePanel } from './ModelUsagePanel'
import { moneyAmount } from './numericPresentation'
export function ResearchCostPanel({ detail, visible = true }: { detail: ResearchRunDetailView; visible?: boolean }) {
  // DSH round snapshots can contain the same call again; prefer its terminal snapshot.
  const snapshots = detail.rounds.flatMap(round => round.tool_invocations)
  const byId = new Map<string, typeof snapshots[number]>()
  for (const call of snapshots) {
    const prior = byId.get(call.tool_call_id)
    if (!prior?.finished_at || call.finished_at) byId.set(call.tool_call_id, call)
  }
  const calls = [...byId.values()].sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))
  const hasDuration = (call: typeof calls[number]) => Boolean(call.finished_at && Date.parse(call.finished_at) > Date.parse(call.started_at))
  const timed = calls.filter(hasDuration)
  const start = Math.min(...timed.map(call => Date.parse(call.started_at)))
  const end = Math.max(...timed.map(call => Date.parse(call.finished_at!)))
  const span = Math.max(1, end - start)
  const unknown = calls.filter(call => call.cost_usd === null).length
  return <section className="research-section" aria-label="耗时与成本"><h3>耗时与成本</h3>
    <p>报告记录的估算费用：{moneyAmount(detail.estimated_cost_usd)}；工具费用缺失 {unknown} / {calls.length} 次。缺失费用不按零计算，工具合计也不代表完整模型账单。</p>
    <p>报告冻结时的总词元字段：{detail.total_tokens === null ? '未记录完整总量' : detail.total_tokens.toLocaleString()}。后续会话用量见下方已保存核验。执行记录包含内部编排调用，按调用身份去重；与采纳证据的工具计数口径不同。</p>
    <details><summary>查看报告费用与用量原始字段</summary><pre className="asset-json">{JSON.stringify({ estimated_cost_usd: detail.estimated_cost_usd, total_tokens: detail.total_tokens }, null, 2)}</pre></details>
    <ModelUsagePanel runId={detail.run.run_id} visible={visible} />
    <p>只有可靠的起止记录才绘制耗时，并行请求不相加为总耗时。{calls.length - timed.length} 条没有可靠起止区间；快照登记时间不当作实际执行耗时。</p>
    {calls.length ? <ol className="research-waterfall">{calls.map((call, index) => <li key={call.tool_call_id}>
      <div><strong>调用 {index + 1} · {call.status === 'succeeded' ? '完成' : call.status === 'running' ? '进行中' : '未完成'}</strong><span>{call.latency_ms === null || (call.latency_ms === 0 && !hasDuration(call)) ? '耗时未可靠记录' : call.latency_ms > 0 && call.latency_ms < 100 ? '不足 0.1 秒' : `${(call.latency_ms / 1000).toFixed(1)} 秒`} · {moneyAmount(call.cost_usd)}</span></div>
      {hasDuration(call) && <div className="research-waterfall-track"><span style={{ marginLeft: `${(Date.parse(call.started_at) - start) / span * 100}%`, width: `${Math.max(0.3, (Date.parse(call.finished_at!) - Date.parse(call.started_at)) / span * 100)}%` }} /></div>}
      <details><summary>调用内容、时间与费用原始记录</summary><p>{researchDisplayText(call.query_summary)}</p><code>{call.capability_id} · {call.started_at} → {call.finished_at ?? '未结束'}</code><pre className="asset-json">{JSON.stringify(call, null, 2)}</pre></details>
    </li>)}</ol> : <p>没有已记录的工具时间线。</p>}</section>
}
