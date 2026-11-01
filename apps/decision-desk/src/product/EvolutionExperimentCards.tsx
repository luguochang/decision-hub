import type { EvolutionOverview } from '../api/types'
import { executionScopeName } from './executionScope'

const stages: Record<string, string> = { replay: '历史回放', holdout: '独立样本验证', shadow: '前瞻观察' }
const statuses: Record<string, string> = { registered: '已登记', running: '执行中', completed: '执行已结束', failed: '执行失败' }

export function EvolutionExperimentCards({ evolution }: { evolution: EvolutionOverview }) {
  return <>{evolution.experiments.map(experiment => {
    const dataset = evolution.datasets.find(item => item.dataset_id === experiment.dataset_id)
    const results = evolution.results.filter(item => item.experiment_id === experiment.experiment_id)
    const stage = dataset?.split ?? results[0]?.stage ?? ''
    const paired = results.some(result => result.scorer_version === 'prospective.paired-direction.v1')
    return <article className="experiment-row" key={experiment.experiment_id}>
      <div><strong>{stages[stage] ?? '待核对阶段'}</strong><span>{paired && experiment.status === 'completed' ? '本次核验快照已保存' : statuses[experiment.status] ?? '状态待核对'}</span></div>
      <p>评估范围：{executionScopeName(experiment.execution_scope)}</p>
      <small>{paired ? '同一输入、同一行情窗口的两种方法比较。只统计双方均给出方向且行情已核验的样本；弃权与失败仍保留。' : '基线与候选的同批次评估 · 执行结束不代表启用条件通过'}</small>
      {paired && <p>这是方向观察记录，尚不能用于启用新方法。保存快照不代表整个观察期已结束；概率评分、独立事件数量与正式研究方法的一致性仍需核对。</p>}
      {results.some(result => result.scorer_version === 'evaluation.aggregate.v2') && <p>这些历史成绩使用了共享结果标签，未按各方法的预测重新评分。记录保留供追溯，需要重新评测后才能审核启用。</p>}
      {results.map(result => <div className="result-metrics" key={result.result_id}>
        <span>{result.candidate_id === experiment.baseline_ref ? '基线方法' : '候选方法'}</span>
        <b>{['evaluation.aggregate.v1', 'evaluation.aggregate.v2'].includes(result.scorer_version ?? '') ? `旧口径记录数 ${result.sample_count}，不能用于新审核` : `${paired ? '双方方向可比' : '有效样本'} ${result.sample_count} 个`}</b>
        <b>{paired ? '未登记概率评分' : `概率误差 ${result.brier_score === null ? '未知' : result.brier_score.toFixed(4)}`}</b>
        {paired && <b>未给方向 {result.failure_counts.non_directional ?? 0} 次</b>}
        {paired && <b>费用 {result.cost_usd === null ? '待核对' : `$${result.cost_usd.toFixed(4)}`}</b>}
        <b>95% 记录耗时 {result.p95_latency_ms === null ? '未知' : `${result.p95_latency_ms} 毫秒`}</b>
        <b className={result.safety_violations ? 'danger-text' : ''}>边界违规 {result.safety_violations} 次</b>
      </div>)}
      {!results.length && <p>尚无可审核的评估结果。</p>}
      <details><summary>实验技术记录与原始指标</summary><pre style={{ maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ experiment, dataset, results }, null, 2)}</pre></details>
    </article>
  })}</>
}
