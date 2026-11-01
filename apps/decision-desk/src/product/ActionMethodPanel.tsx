import { useQuery } from '@tanstack/react-query'
import type { ResearchMethodTrialProvenance } from '@decision-hub/contracts-ts'
import { actionMethodsApi } from '../api/actionMethods'
import { ApiRequestError } from '../api/client'
import { ledgerAmount } from './ledgerPresentation'

const METHOD_PROJECTION_RETRY_MS = 2_000

function isMethodProjectionPending(error: unknown): boolean {
  return error instanceof ApiRequestError &&
    error.kind === 'http' &&
    error.status === 503 &&
    error.message === 'method_projection_pending'
}

const sourceStatus = {
  filled: '已成交', no_fill: '未成交', rejected: '未执行', unknown: '执行结果待核实',
}
const outcomeReason: Record<string, string> = {
  quote_window_exhausted: '等待期限内没有取得合格报价',
  action_quote_legacy_clock_incompatible: '历史报价的时间校验规则不适用',
}
function methodTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function TrialMethodSummary({ trial }: { trial: ResearchMethodTrialProvenance }) {
  const source = trial.source
  return <div aria-label="方法学习来源与试用范围">
    <p>来自 {source.instrument.replace('-USDT', '')} 的模拟{source.terminal_kind === 'closed_episode' ? '已结束持仓' : '执行记录'}：
      {sourceStatus[source.execution_status]}；{source.outcome_status === 'settled' ? '已结算' : '暂不能评价收益'}。</p>
    {source.execution_status !== 'filled' && <p>{outcomeReason[source.reason] ?? '具体原因保存在下方原始依据中。'}</p>}
    <p>净收益：{ledgerAmount(source.net_pnl)}{source.net_pnl !== null ? ' USDT' : ''}；
      执行成本：{ledgerAmount(source.cost)}{source.cost !== null ? ' USDT' : ''}。</p>
    <p>参照预置方法形成候选。实际调整了{trial.changes.map(change =>
      change.path === 'SKILL.md' ? '研究说明' : '执行步骤').join('、')}。</p>
    <p>行为对照：{trial.behavior.parent_passed ? '原方法与候选方法均通过' : '候选方法通过，原方法未全部通过'}。
      共 {trial.behavior.case_count} 项，适用场景 {trial.behavior.applies_case_count} 项、排除场景 {trial.behavior.excludes_case_count} 项；
      历史时点材料 {trial.behavior.historical_pit_case_count} 项、构造控制材料 {trial.behavior.synthetic_control_case_count} 项。
      这些检查验证方法能否按规则执行，金融效果尚待后续实际结果检验。</p>
    <p>试用范围：{source.instrument.replace('-USDT', '')} · 24 小时分析；
      {methodTime(trial.start_at)} 至 {methodTime(trial.end_at)}（本地时间），
      本次为登记试用的第 {trial.run_ordinal} / {trial.max_runs} 次。</p>
  </div>
}

export function ActionMethodPanel({ runId, revisionId }: { runId: string; revisionId: string }) {
  const query = useQuery({
    queryKey: ['action-methods', runId, revisionId],
    queryFn: ({ signal }) => actionMethodsApi.read(runId, revisionId, signal),
    retry: (_failureCount, error) => isMethodProjectionPending(error),
    retryDelay: METHOD_PROJECTION_RETRY_MS,
    refetchOnWindowFocus: false,
  })
  const value = query.isError ? undefined : query.data
  return <section className="action-mechanisms" aria-label="本次采用的方法">
    <h4>本次采用的方法</h4>
    {query.isPending && <p role="status">正在核对方法使用记录…</p>}
    {query.isError && <p role="alert">暂时无法核对方法原始记录，当前使用阶段已隐藏。
      <button type="button" className="product-link" onClick={() => void query.refetch()}>重新读取方法记录</button>
    </p>}
    {value?.status === 'not_selected' && <p>本次研究尚无可核对的方法选择记录。</p>}
    {value?.methods.map(item => <div key={`${item.skill_version_id}:${item.generation}`}>
      <strong>{item.name}</strong><p>{item.purpose}</p>
      <p className="product-muted">{item.origin === 'trial_registered' ? '结果复盘后的试用方法' : '预置研究方法'} · 第 {item.generation} 轮研究。
        金融效果尚未验证。方法完成研究步骤不代表盈利或胜率达标。</p>
      {'trial' in item && item.trial && <TrialMethodSummary trial={item.trial} />}
      <ol aria-label="方法使用阶段">
        <li>选择：{item.selected ? '已核实' : '尚无回执'}</li>
        <li>读取：{item.loaded ? '已核实模型收到完整方法' : '尚无完整读取回执'}</li>
        <li>执行：{item.executed ? '全部步骤已有原始回执' : `已记录 ${item.executed_nodes.length} / ${item.required_nodes.length} 步`}</li>
        <li>采用：{value.adoption === 'adopted' && item.consumed && item.generation === value.producer_generation
          ? '当前方案已引用通过核验的采用回执'
          : item.consumed ? '本轮研究有采用回执；当前方案采用尚未核实' : '尚无采用回执'}</li>
      </ol>
      {(value.gaps.includes('current_generation_adoption_unverified') || value.adoption === 'generation_unverified') &&
        <p role="status" className="action-notice">此处展示已核实的历史研究轮次。后续续接轮次采用情况尚未认证，不能沿用历史回执。</p>}
      {value.adoption === 'not_adopted' && <p>当前行动方案没有匹配的已核验采用引用。</p>}
      <details><summary>查看方法版本与原始依据</summary><pre>{JSON.stringify({
        run_id: value.run_id, revision_id: value.revision_id, selected_generation: item.generation,
        current_generation: value.current_generation, producer_generation: value.producer_generation,
        skill_version_id: item.skill_version_id, version_hash: item.version_hash,
        receipts: item.receipts, gaps: value.gaps,
        ...('trial' in item ? { trial: item.trial,
          generation_binding_ref: item.generation_binding_ref,
          original_selection_ref: item.original_selection_ref } : {}),
      }, null, 2)}</pre></details>
    </div>)}
  </section>
}
