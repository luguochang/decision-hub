import { PlanningDecisionPanel } from './PlanningDecisionPanel'
import { useEffect, useRef, useState } from 'react'
import { request } from '../api/client'
import { PlanningContextPanel } from './PlanningContextPanel'

type PlanningCall = { call_id: string; job_attempt: number; role: string; status: string; started_at: string; finished_at: string | null; details: { text_characters?: number; evidence_count?: number; error_code?: string; retryable?: boolean } }
const roles: Record<string, string> = { research_supervisor: '安排研究角色', counter_thesis: '反向复核', data_quality: '资料质量核查', market_transmission: '市场传导核查', decision_synthesis: '汇总改进方案' }
const states: Record<string, string> = { running: '尚无完整返回记录', succeeded: '调用已返回', failed: '调用失败', cancelled: '调用已取消' }
const errors: Record<string, string> = { provider_timeout: '模型请求超时', provider_rate_limited: '模型服务限流', provider_unavailable: '模型服务暂不可用', candidate_evidence_out_of_scope: '引用超出本任务材料范围', structured_output_invalid: '模型返回格式不合格' }

export function PlanningCallsPanel({ jobId, showDecision = true }: { jobId: string; showDecision?: boolean }) {
  const [rows, setRows] = useState<PlanningCall[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [jobId])
  const refresh = async () => {
    pending.current?.abort()
    const controller = new AbortController(); pending.current = controller
    setLoading(true); setFailed(false)
    try { const data = await request<PlanningCall[]>(`/v1/evolution/jobs/${encodeURIComponent(jobId)}/planning-calls`, { signal: controller.signal }); if (!controller.signal.aborted) setRows(data) }
    catch { if (!controller.signal.aborted) setFailed(true) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }
  return <section className="planning-call-panel" aria-label="本任务规划调用记录">
    <button className="button secondary" disabled={loading} onClick={() => void refresh()}>{loading ? '正在读取规划记录…' : rows === null ? '读取规划调用记录' : '刷新规划调用记录'}</button>
    {failed && <p role="alert">读取失败，请重试。已有记录不代表当前状态。</p>}
    <PlanningContextPanel jobId={jobId} />{showDecision && <PlanningDecisionPanel jobId={jobId} />}
    {rows?.length === 0 && <p>此任务尚无逐调用记录。旧任务未记录的失败原因无法追溯，不能据此判定没有调用或调用成功。</p>}
    {!!rows?.length && <><p>以下为最近最多 100 条调用。已返回不代表候选生成或评测通过；未完整返回可能是仍在执行或曾被中断。</p>
      {rows.map(row => <article key={row.call_id}><strong>{roles[row.role] ?? '其他规划角色'} · 第 {row.job_attempt} 次任务尝试 · {states[row.status] ?? '状态待核对'}</strong>
        <p>开始于 {new Date(row.started_at).toLocaleString('zh-CN', { hour12: false })} · 输入 {row.details.text_characters ?? '未知'} 字符 · 引用 {row.details.evidence_count ?? '未知'} 项</p>
        {row.details.error_code && <p>{errors[row.details.error_code] ?? '调用异常，需核对技术记录'}；{row.details.retryable ? '调用可重试，任务是否重试还取决于次数和其他角色结果' : '未标记为可自动重试'}</p>}
        <details><summary>查看调用技术凭据</summary><pre>{JSON.stringify(row, null, 2)}</pre></details>
      </article>)}</>}
  </section>
}
