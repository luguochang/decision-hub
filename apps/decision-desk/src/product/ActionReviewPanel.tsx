import { useQuery } from '@tanstack/react-query'
import { ACTION_OWNER, actionErrorMessage, actionPlansApi, type ActionHorizon, type ActionPosition, type ActionReview } from '../api/actionPlans'
import { actionLabels, actionTime, conditionDescription, probabilityText } from './actionPresentation'
import { researchDisplayText } from './researchLanguage'

type Props = {
  opportunityId: string; scopeId: string; horizon: ActionHorizon; position: ActionPosition;
  onNavigate: (scene: string, params?: Record<string, string | undefined>) => void;
}

function CandidateConditions({ candidate }: { candidate: NonNullable<ActionReview['branches'][number]['candidate']> }) {
  return <details><summary>查看新分析的行动与保护条件</summary>
    {([['开始行动', candidate.entry], ['退出', candidate.exit], ['判断失效', candidate.invalidation]] as const).map(([label, items]) => <section key={label}>
      <h4>{label}</h4>{items.length ? <ul>{items.map(item => <li key={item.id}>{conditionDescription(item.expression)}；需连续 {item.min_fresh_samples} 次新鲜观测确认，有效至 {actionTime(item.valid_until)}。</li>)}</ul> : <p>新分析未提出这类条件。</p>}
    </section>)}
  </details>
}

export function ActionReviewPanel({ opportunityId, scopeId, horizon, position, onNavigate }: Props) {
  const query = useQuery({
    queryKey: ['action-review', ACTION_OWNER, opportunityId, scopeId, horizon],
    queryFn: ({ signal }) => actionPlansApi.reviews(opportunityId, scopeId, horizon, signal),
    retry: false, refetchInterval: 30_000, refetchIntervalInBackground: false, refetchOnWindowFocus: false,
  })
  if (query.isPending) return <p role="status">正在核对最新复核结果…</p>
  if (query.isError) return <section className="action-notice" aria-label="复核结果" role="alert">
    <h3>复核结果暂时无法读取</h3><p>{actionErrorMessage(query.error)} 当前方案的监控与原操作回执可分别核对。</p>
    <button type="button" className="product-link" onClick={() => void query.refetch()}>重新读取复核结果</button>
  </section>
  const review = query.data.review
  const branch = review?.branches.find(item => item.position_context === position)
  if (!review || !branch) return null
  const { candidate, mechanism, current_head: head } = branch
  const current = head?.current_revision_id === branch.revision_id
  const ended = !head || ['completed', 'expired', 'invalidated', 'superseded'].includes(head.state) || Date.parse(head.valid_until) <= Date.now()
  const title = branch.applied ? '复核已更新方案' : '新分析已保存，原保护继续保留'
  return <section className="action-review action-mechanisms" aria-label="复核结果">
    <div className="action-heading"><h3>{title}</h3><span>完成于 {actionTime(review.completed_at)}</span></div>
    {!current ? <p className="action-notice">这是已保存的复核记录，当前方案已有后续版本，请以下方当前方案为准。</p>
      : ended ? <p className="action-notice">这份方案现已结束或到期；本次复核记录不代表保护仍在运行。</p>
      : !branch.applied ? <p className="action-notice">新建议尚未替换当前保护条件。当前是否持续监控，请核对下方方案的监控状态。</p> : <p>新条件已保存为方案版本，可在下方核对监控状态。</p>}
    {candidate ? <>
      <h4>这次复核的建议：{actionLabels[candidate.action]}</h4>
      <p>{researchDisplayText(candidate.reason)}</p>
      <p>条件概率：{probabilityText(candidate.probability)} · 分析截点 {actionTime(candidate.as_of)}</p>
      <p>下次复核：{branch.next_review_at ? actionTime(branch.next_review_at) : '当前窗口内未安排下一次复核'}</p>
      <CandidateConditions candidate={candidate} />
    </> : <p>本次复核未给这个仓位提供新的行动建议；原方案按自身状态运行。</p>}
    {mechanism && <details><summary>查看这次复核的主反依据</summary><ol>{mechanism.edges.map(edge => <li key={edge.id}>
      <strong>{researchDisplayText(mechanism.nodes.find(node => node.id === edge.from_node)?.description ?? '待核实因素')} → {researchDisplayText(mechanism.nodes.find(node => node.id === edge.to_node)?.description ?? '待核实影响')}</strong>
      <p>{researchDisplayText(edge.hypothesis)}</p><p>支持依据 {edge.support_refs.length} 项 · 反方依据 {edge.counter_refs.length} 项</p>
    </li>)}</ol>{mechanism.unresolved.length > 0 && <><h4>仍需核对</h4><ul>{mechanism.unresolved.map((item, index) => <li key={index}>{researchDisplayText(item)}</li>)}</ul></>}</details>}
    <button type="button" className="product-link" onClick={() => onNavigate('report', { run: review.run_id, back: '#/home' })}>阅读这次复核报告</button>
    <details><summary>复核回执与版本依据</summary><p>本次复核记录分析与方案变更，不代表实际成交。</p><pre>{JSON.stringify({ request_id: review.request_id, receipt_hash: review.receipt_hash, sidecar_ref: review.sidecar_ref, revision_id: branch.revision_id, revision_hash: branch.revision_hash, disposition: branch.disposition, reason: branch.reason }, null, 2)}</pre></details>
  </section>
}
