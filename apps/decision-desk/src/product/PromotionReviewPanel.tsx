import { AlertTriangle, CheckCircle2, RotateCcw } from 'lucide-react'
import type { PromotionReview } from '../api/types'

const conditions: Record<string, [string, string]> = {
  'candidate.executable_changes': ['变更是否可执行', '候选须具有已核验的方向研究规则；运行管理建议与缺少执行规则的旧候选不能启用。'],
  'candidate.execution_scope': ['候选适用范围', '此入口仅审核范围明确的单次方向综合；完整研究需要独立部署确认，历史未知范围不能推定。'],
  'experiment.execution_scope': ['评估范围一致', '方向综合的审核只能使用明确登记为同一范围的评估；其他范围或历史未知记录不能混用。'],
  'candidate.status': ['候选状态', '候选须处于可审核状态。'],
  'evaluation.references': ['评估记录关联', '需要属于本候选的完整评估记录。'],
  'evaluation.stage_coverage': ['三阶段验证', '需要历史回放、独立样本验证和前瞻观察。'],
  'experiment.lineage': ['基线与数据集关联', '每份候选结果须能对应唯一的同批次基线。'],
  'experiment.completed': ['实验执行完成', '实验结束后才能审核结果。'],
  'dataset.integrity': ['数据集完整性', '登记内容、哈希、输入清单和时间必须一致；异常记录需要重新核查。'],
  'sample.counting_method': ['样本计数方式', '按材料计数，同一材料的多个窗口不能重复算样本。'],
  'sample.scorer': ['评分方法一致', '基线与候选须使用相同且受支持的评分方法。'],
  'sample.minimum': ['有效样本数量', '基线与候选均须至少 100 个有效样本。'],
  'sample.comparable_count': ['比较样本数量一致', '两种方法的已评分数量须相同，且不超过登记输入数；事件独立性仍需核查。'],
  'safety.zero': ['执行边界检查', '两种方法均不得有执行边界违规。'],
  'event_family.minimum': ['事件类型覆盖', '核对两种方法的事件类型数量及登记材料覆盖。'],
  'dataset.mode_window': ['数据来源与观察周期', '历史验证使用冻结材料；前瞻观察须真实运行至少 30 天且窗口已结束。'],
  'metric.brier_valid': ['概率评分可核对', '两种方法均需要有效的概率误差评分，缺失不能当作零。'],
  'metric.brier_noninferior': ['概率误差对比', '候选概率误差不得比基线高出 0.02。'],
  'latency.deadline': ['响应时间', '两种方法的 95% 响应时间须在 180 秒以内。'],
  'cost.known': ['费用依据完整', '先补齐两种方法的费用依据；未知费用不能当作零或直接放行。'],
  'cost.ceiling': ['费用上限', '在费用可核对的前提下，候选不得超过基线的 125%。'],
}
const stageNames: Record<string, string> = { replay: '历史回放', holdout: '独立样本验证', shadow: '前瞻观察' }
const signed = (value: number | null) => value === null ? '待核对' : `${value >= 0 ? '+' : ''}${value.toFixed(4)}`

export function PromotionReviewPanel({ review, reviewing }: { review: PromotionReview | null; reviewing: boolean }) {
  if (reviewing) return <div className="promotion-review loading"><RotateCcw size={15} className="spin" />正在核对历史回放、独立样本与前瞻观察的启用条件…</div>
  if (!review) return <div className="promotion-review failed" role="alert"><AlertTriangle size={15} />未能取得后台审核结果，暂不能启用。</div>
  const renderCheck = (item: PromotionReview['checks'][number]) => {
    const [title, help] = conditions[item.rule_id.split(':')[0]] ?? ['其他审核条件', '请展开技术详情核对后台返回的条件。']
    const stage = review.deltas.find(delta => item.rule_id.endsWith(`:${delta.result_id}`))?.stage
    return <div className={item.status} key={item.rule_id}><span>{item.status === 'pass' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}</span><div><strong>{stage ? `${stageNames[stage] ?? '待核对阶段'} · ` : ''}{title} · {item.status === 'pass' ? '通过' : '未通过'}</strong><small>{help}</small></div></div>
  }
  const passed = review.checks.filter(item => item.status === 'pass')
  return <div className={`promotion-review ${review.eligible ? 'eligible' : 'failed'}`}>
    <div className="review-heading"><div><strong>{review.eligible ? '启用条件通过' : '启用条件未通过'}</strong><span>{review.evaluation_refs.length} 份评估记录 · 仍须负责人确认</span></div><span className={`status-pill ${review.eligible ? 'success' : 'danger'}`}>{review.eligible ? '可提交审核' : '暂不可启用'}</span></div>
    <div className="stage-deltas">{review.deltas.map(item => {
      const countingFailed = review.checks.some(check => check.status === 'fail' && check.rule_id.endsWith(`:${item.result_id}`) && check.rule_id.startsWith('sample.'))
      return <div key={item.result_id}><span>{stageNames[item.stage] ?? '待核对阶段'}</span><strong>概率误差差值 {signed(item.brier_delta)}</strong><small>{countingFailed ? '记录样本数（数量或口径未通过）' : '有效样本'} {item.sample_count} 个 · 95% 记录耗时 {item.p95_latency_ms === null ? '未知' : `${item.p95_latency_ms} 毫秒`} · 边界违规 {item.safety_violations} 次</small></div>
    })}</div>
    {!review.eligible && <h4>需要补齐的条件</h4>}
    <div className="review-checks">{review.checks.filter(item => item.status !== 'pass').map(renderCheck)}</div>
    {!!passed.length && <details><summary>已通过的条件（{passed.length} 项）</summary><div className="review-checks">{passed.map(renderCheck)}</div></details>}
    <details><summary>审核技术详情与记录标识</summary><pre style={{ maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(review, null, 2)}</pre></details>
  </div>
}
