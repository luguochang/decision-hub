import { ObservationInputAuditPanel } from './ObservationInputAuditPanel'
import { useQuery } from '@tanstack/react-query'
import { assets } from '../api/assets'
import { ProspectiveReceiptsPanel } from './ProspectiveReceiptsPanel'

const issueLabels: Record<string, string> = {
  evaluation_cross_split_overlap: '不同评测阶段使用了重复材料，不能作为独立验证。',
  evaluation_duplicate_sample: '同一阶段存在重复材料，不能重复计入样本数。',
  evaluation_stage_coverage_missing: '评测阶段配置不完整。',
  evaluation_fixture_required: '部分阶段尚无样本。',
  evaluation_fixture_unreadable: '部分样本文件无法读取或格式不符合要求。',
  evaluation_fixture_split_mismatch: '样本声明的阶段与配置不一致。',
}

export function EvolutionReadinessPanel({ onObservations, onRun }: { onObservations?: () => void; onRun?: (runId: string) => void }) {
  const query = useQuery({ queryKey: ['evolution-readiness'], queryFn: assets.evolutionReadiness, retry: false, refetchInterval: 30_000 })
  const observations = useQuery({ queryKey: ['evolution-observation-inputs'], queryFn: assets.prospectiveInputs, retry: false, refetchInterval: 30_000 })
  const view = query.data
  return <section className="product-card" aria-label="反馈与验证准备">
    <div className="product-card-head"><h2>反馈到方法改进</h2><button className="button secondary" disabled={query.isFetching || observations.isFetching} onClick={() => { void query.refetch(); void observations.refetch() }}>刷新准备情况</button></div>
    <p>保存反馈 → 登记改进任务 → 生成候选方法 → 独立评测与前瞻观察 → 负责人审核。</p>
    {query.isLoading && <p role="status">正在核对已保存的反馈和评测条件…</p>}
    {query.isError && <p role="alert">准备情况暂不可用。{view ? '下方是上次读取的记录，不代表当前状态。' : '请刷新重试。'}</p>}
    {view && <>
      <div className="product-monitor">
        <div><span>已保存反馈</span><strong>{view.feedback_count}</strong></div>
        <div><span>尚未转成任务的改进反馈</span><strong>{view.unregistered_feedback}</strong></div>
        <div><span>已登记任务</span><strong>{view.jobs}</strong></div>
        <div><span>已登记方法版本（含基线）</span><strong>{view.candidates}</strong></div>
      </div>
      <p>共有 {view.actionable_feedback} 条反馈需要改进或核查。登记任务不等于候选已生成，其他实验副本的结果也不计入这里。</p>
      <h3>改进方法是否已用于正式研究</h3>
      <p>当前演进账本的版本尚未接入正式研究执行。方向比较基于冻结资料；正式研究还包含资料采集、查证与复查，不能把比较结果直接当作完整研究方法的改进。</p>
      {view.research_method && <>
        <p>{view.research_method.research_policy_status === 'verified' ? '研究准则与角色目标已从本次固定的研究包写入提交提示，并与保存的提示逐轮核对。规则实际效果仍需查看报告和评测。' : view.research_method.research_policy_status === 'prepared' ? '研究准则已准备，尚无保存的提交提示可核对。' : view.research_method.research_policy_status === 'invalid' ? '研究准则与提交提示不匹配，需要核查原始记录。' : '这份研究没有研究准则与角色内容的提交凭据，不能用当前配置证明历史执行。'}</p>
        <p>{view.research_method.request_pack_status === 'verified' ? '本次研究使用的预算、证据规则和能力范围已固定并核验；同一任务恢复时沿用原研究包。完整流程、服务商配置和正式部署仍待接入。' : view.research_method.request_pack_status === 'invalid' ? '研究包凭据缺失或校验不匹配，需要核查；不能认定该方法已完整绑定。' : '这份研究尚无已绑定的研究包内容凭据，旧任务不会事后补记。'}</p>
        <p>已保存 {view.research_method.recorded_receipts} 份执行前方法凭据。{view.research_method.latest_receipt_status === 'verified' ? '最近一份凭据完整性已核验，记录了提交前固定的提示规则和研究服务构建；服务商配置尚未完整绑定。' : view.research_method.latest_receipt_status === 'invalid' ? '最近一份凭据校验失败，需要检查原始记录。' : '旧任务没有这类执行前凭据，将从新研究开始保存，不能事后补记为已验证。'}</p>
        <p>{view.research_method.model_dispatch?.status === 'verified' ? '这次主研究实际提交的模型参数已固定；恢复时参数变化会阻止新的模型调用。服务端点与辅助模型仍未完整核验。' : view.research_method.model_dispatch?.status === 'invalid' ? '这次研究的模型提交凭据未通过核验，不能据此确认实际配置。' : '这次研究尚无模型提交绑定记录；不能用当前默认模型补记过去的实际配置。'}</p>
        {view.research_method.child_model_dispatch?.status === 'recorded' ? <details><summary>协作研究模型提交记录（{view.research_method.child_model_dispatch.bindings.length} 个会话）</summary><p>来自子研究与复核会话的执行前记录。记录数量不代表已核对全部归档；服务端点仍未核验。</p><table><thead><tr><th>协作会话</th><th>服务路由</th><th>模型</th><th>推理设置</th></tr></thead><tbody>{view.research_method.child_model_dispatch.bindings.map((binding, index) => <tr key={binding.execution_session_id}><td>协作 {index + 1}</td><td>{binding.provider}</td><td>{binding.model}</td><td>{binding.reasoning_effort === 'off' ? '关闭' : binding.reasoning_effort ?? '未指定'}</td></tr>)}</tbody></table></details> : <p>{view.research_method.child_model_dispatch?.status === 'invalid' ? '协作研究的模型记录核验失败，请检查任务原始记录。' : '这次研究尚无协作会话模型记录，不能确认子研究的实际配置。'}</p>}
        {view.research_method.model_dispatch?.binding && <details><summary>主研究模型提交记录</summary><dl><dt>服务路由</dt><dd>{view.research_method.model_dispatch.binding.provider}</dd><dt>模型</dt><dd>{view.research_method.model_dispatch.binding.model}</dd><dt>推理设置</dt><dd>{view.research_method.model_dispatch.binding.reasoning_effort === 'off' ? '关闭' : view.research_method.model_dispatch.binding.reasoning_effort ?? '未指定'}</dd></dl><p className="product-muted">来自这次任务的模型提交钩子；服务路由名称不是底层端点证明。</p></details>}
        {onRun && typeof view.research_method.latest_receipt?.run_id === 'string' && <button className="product-link" onClick={() => onRun(view.research_method!.latest_receipt!.run_id as string)}>查看这次研究</button>}
        {view.research_method.latest_receipt && <details><summary>最近研究的方法技术凭据</summary><pre style={{ maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(view.research_method.latest_receipt, null, 2)}</pre></details>}
      </>}
      <h3>当前评测输入</h3>
      <p>配置材料：历史回放 {view.configured_samples.counts.replay ?? 0} 份 · 独立验证 {view.configured_samples.counts.holdout ?? 0} 份 · 前瞻阶段 {view.configured_samples.counts.shadow ?? 0} 份。</p>
      {view.configured_samples.issues.length > 0 ? <ul>{view.configured_samples.issues.map(issue => <li key={issue}>{issueLabels[issue] ?? '样本检查未通过，请核对评测配置。'}</li>)}</ul> : <p>未发现文本重复；仍需核对事件独立性、时间切分和标签质量。</p>}
      {!view.prospective_collector_available && <p>默认演进评测仍使用离线样例，尚未接入真实前瞻样本的完整评分。独立前瞻比较需先登记方法和规则，再启动比较进程。</p>}
      {view.paired_execution && <>
        <h3>基线与候选的成对比较</h3>
        <div className="product-monitor">
          <div><span>已预登记比较</span><strong>{view.paired_execution.registered_trials}</strong></div>
          <div><span>两份预测均已保存</span><strong>{view.paired_execution.predictions_saved}</strong></div>
          <div><span>待执行或执行中</span><strong>{view.paired_execution.waiting}</strong></div>
          <div><span>执行未完整返回</span><strong>{view.paired_execution.incomplete}</strong></div>
        </div>
        <p>已留存 {view.paired_execution.pairs} 组首次课题输入，其中 {view.paired_execution.excluded} 组因输入条件或提交截止时间被排除。失败与结果未知的调用会保留，不用后来的成功替换。</p>
        {!view.paired_execution.outcome_scoring_available && <p>这里统计预测提交情况，尚未接通成对行情评分；不能据此判断候选优于基线，也不会自动启用候选。</p>}
        {view.paired_execution.outcome_scoring_available && <p>共同行情评分已保存 {view.paired_execution.labels_completed ?? 0} 组，等待到期或采样 {view.paired_execution.labels_waiting ?? 0} 组，待重试 {view.paired_execution.labels_retry ?? 0} 组。分钟收盘估算不等于交易盈亏，也不会自动启用候选。</p>}
        <ProspectiveReceiptsPanel />
      </>}
      <h3>已落库的真实研究观察</h3>
      {observations.isLoading && <p role="status">正在读取观点与到期观察…</p>}
      {observations.isError && <p role="alert">研究观察读取失败。{observations.data ? '以下保留上次记录，不代表当前状态。' : '当前样本数未知。'}</p>}
      {observations.data && <>
        <p>{observations.data.outlooks} 条前瞻观点，账本已完成 {observations.data.recorded_completed} 条观察。按同一课题、已保存的方法版本、品种和窗口固定首次记录；首次失效或弃权也不由后续更新替换。当前 {observations.data.selected} 条通过选样条件，涉及 {observations.data.distinct_studies} 个课题；这还不代表报告原件与行情标签已核验。</p>
        <div className="product-monitor">
          <div><span>所选观点已观察</span><strong>{observations.data.completed}</strong></div>
          <div><span>等待到期或采样</span><strong>{observations.data.pending}</strong></div>
          <div><span>需要重试</span><strong>{observations.data.retry}</strong></div>
          <div><span>同课题重复观察</span><strong>{observations.data.repeated}</strong></div>
        </div>
        <p>已观察观点中，方向观点 {observations.data.directional_completed} 条，中性观点 {observations.data.neutral_completed} 条；中性观点不计算方向命中率。另有 {observations.data.excluded} 条不符合输入条件，{observations.data.unknown} 条状态待核对。</p>
        {observations.data.next_due_at && <p>所选观点最早观察窗口到期：{new Date(observations.data.next_due_at).toLocaleString('zh-CN')}。</p>}
        <p>以上为落库状态；研究输入原文与行情标签需分别核查。这些数量不代表独立样本或候选方法已胜过基线。</p><ObservationInputAuditPanel />
        {onObservations && <button className="product-link" onClick={onObservations}>查看研究观点与观察记录</button>}
      </>}
      <p>晋级仍须通过独立历史样本验证，取得至少 100 个有效样本、至少 30 天前瞻观察等后台条件。登记和预测提交均不代表这些条件已满足。</p>
      <small>读取于 {new Date(view.observed_at).toLocaleString('zh-CN')} · 仅核对准备情况，本页不会触发模型调用或切换方法。</small>
    </>}
  </section>
}
