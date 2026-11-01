import { useEffect, useRef, useState } from 'react'
import { request } from '../api/client'

type Decision = { outcome: 'candidate' | 'operational_followup' | 'no_candidate'; reason: string; evidence_refs: string[] }
export function PlanningDecisionPanel({ jobId }: { jobId: string }) {
  const [data, setData] = useState<Decision | null | undefined>()
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [jobId])
  async function load() {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller
    setLoading(true); setFailed(false)
    try { const result = await request<Decision | null>(`/v1/evolution/jobs/${encodeURIComponent(jobId)}/planning-decision`, { signal: controller.signal }); if (!controller.signal.aborted) setData(result) }
    catch { if (!controller.signal.aborted) setFailed(true) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }
  return <section className="planning-call-panel planning-summary-panel" aria-label="规划处理结论">
    <button className="button secondary" disabled={loading} onClick={() => void load()}>{loading ? '正在读取结论…' : '查看规划处理结论'}</button>
    {failed && <p role="alert">处理结论读取失败，请重试。</p>}
    {data === null && <p>此任务尚未保存处理结论；历史任务不补造结论。</p>}
    {data && <><strong>{data.outcome === 'candidate' ? '已提出方向规则候选' : data.outcome === 'operational_followup' ? '需处理运行问题' : '本次没有足够依据生成候选'}</strong><p>{data.outcome === 'operational_followup' ? '本次核查未生成可执行的方向研究候选。需先核查运行条件或数据质量；运行故障不能直接证明预测方法的优劣。' : data.outcome === 'no_candidate' ? '本次依据不足以支持新的方法变更，保留原始分析供后续核查。' : '本次提出了固定规则范围内的候选，实际改动与执行条件可在候选详情查看。'}</p><details><summary>查看原始分析（可能包含英文）</summary><p>{data.reason}</p></details><p>关联 {data.evidence_refs.length} 项冻结依据；本记录不代表修复已实施或方法已启用。</p></>}
  </section>
}
