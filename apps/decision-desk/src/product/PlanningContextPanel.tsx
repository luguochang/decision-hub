import { useEffect, useRef, useState } from 'react'
import { request } from '../api/client'

type ContextSummary = { frozen_at: string; admission: string; counts: Record<string, number>; summary_count: number; prompt_characters: number; recovery_of: string | null; input_count: number; baseline_version: string | null }
const names: Record<string, string> = { duplicate_reference: '重复引用，保留但不重复计数', included_failure_group: '纳入问题汇总的运行', cancelled_not_method_failure: '取消记录，保留但不作为方法失败', no_terminal_failure: '没有已结束的失败记录', included_feedback: '已登记反馈', expanded_pattern: '已展开核查的失败模式', missing_record: '引用记录缺失', missing_or_unsupported_reference: '引用缺失或暂不支持', unsupported_reference: '暂不支持的引用', legacy_evaluation_not_validated: '旧评分尚未核验，不作为方法成绩' }

export function PlanningContextPanel({ jobId }: { jobId: string }) {
  const [data, setData] = useState<ContextSummary | null | undefined>()
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [jobId])
  const refresh = async () => {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller
    setLoading(true); setFailed(false)
    try { const result = await request<ContextSummary | null>(`/v1/evolution/jobs/${encodeURIComponent(jobId)}/planning-context`, { signal: controller.signal }); if (!controller.signal.aborted) setData(result) }
    catch { if (!controller.signal.aborted) setFailed(true) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }
  return <section className="planning-call-panel" aria-label="规划输入核查">
    <button className="button secondary" disabled={loading} onClick={() => void refresh()}>{loading ? '正在读取输入核查…' : '读取输入核查记录'}</button>
    {failed && <p role="alert">输入核查记录读取失败，请重试。</p>}
    {data === null && <p>此任务尚未保存输入快照，不能用当前资料推断当时模型看到了什么。</p>}
    {data && <><p>原始引用 {data.input_count} 项 · 供规划使用的摘要 {data.summary_count} 组。每条来源的处置记录均保留，重复问题的运行次数不等于独立预测样本。</p>
      <ul>{Object.entries(data.counts).map(([name, count]) => <li key={name}>{names[name] ?? '其他待核查记录'}：{count} 项</li>)}</ul>
      <p>冻结于 {new Date(data.frozen_at).toLocaleString('zh-CN', { hour12: false })}；重试沿用此快照。</p>
      {data.admission !== 'ready' && <p role="alert">输入超过本次规划预算，尚未交给模型；需分批处理，未静默舍弃材料。</p>}
      {data.recovery_of && <p>这是对历史失败任务的重新核查，原失败记录仍保留。</p>}
      <details><summary>输入版本与技术凭据</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
    </>}
  </section>
}
