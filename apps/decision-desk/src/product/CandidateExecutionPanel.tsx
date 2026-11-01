import { useEffect, useRef, useState } from 'react'
import { request } from '../api/client'

type Execution = { executable: boolean; reason_code: string | null; rules: string[]; summary: string | null; changes: string[]; evidence_refs: string[] }
const reasons: Record<string, string> = {
  candidate_operational_change_unsupported: '这是运行管理建议。当前执行器不能据此调整服务商、重试、研究进程或主机设置，需先落实相应代码与验证。',
  candidate_execution_rules_missing: '此历史候选未记录可执行规则。原始建议保留，需重新规划和验证后才能用于评测或启用。',
}
export function CandidateExecutionPanel({ candidateId, active }: { candidateId: string; active: boolean }) {
  const [data, setData] = useState<Execution | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [candidateId])
  async function load() {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller
    setLoading(true); setFailed(false)
    try { const result = await request<Execution>(`/v1/evolution/candidates/${encodeURIComponent(candidateId)}/execution`, { signal: controller.signal }); if (!controller.signal.aborted) setData(result) }
    catch { if (!controller.signal.aborted) setFailed(true) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }
  if (active) return null
  return <section className="planning-call-panel planning-summary-panel" aria-label="候选实际改动">
    <button className="button secondary" disabled={loading} onClick={() => void load()}>{loading ? '正在核查…' : '查看改动与执行条件'}</button>
    {failed && <p role="alert">改动详情读取失败，请重试。</p>}
    {data && <><p>{data.executable ? '可执行范围：本次方向研究的提示规则。仍需独立评测和负责人审核；不会改变资料采集或运行管理。' : reasons[data.reason_code ?? ''] ?? '执行条件尚未核验通过，不能用于评测或启用。'}</p>
      {data.rules.length > 0 && <ul>{data.rules.map(rule => <li key={rule}>{rule}</li>)}</ul>}
      <p>关联 {data.evidence_refs.length} 项规划依据。建议说明不会直接变成执行指令。</p>
      <details><summary>原始建议与引用（可能包含英文）</summary><p>{data.summary}</p><ul>{data.changes.map((change, index) => <li key={index}>{change}</li>)}</ul><pre>{data.evidence_refs.join('\n')}</pre></details>
    </>}
  </section>
}
