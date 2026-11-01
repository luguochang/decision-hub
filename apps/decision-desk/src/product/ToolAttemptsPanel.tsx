import { useEffect, useState } from 'react'
import type { ResearchUsageView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'

export function ToolAttemptsPanel({ runId, active = false }: { runId: string; active?: boolean }) {
  const [view, setView] = useState<ResearchUsageView | null>(null)
  const [failed, setFailed] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let current = true
    setView(null); setFailed(false)
    const read = async () => {
      try {
        const value = await assets.researchUsage(runId)
        if (current) { setView(value); setFailed(false) }
      } catch { if (current) { setFailed(true); setView(null) } }
    }
    void read()
    const timer = active ? window.setInterval(() => void read(), 30000) : undefined
    return () => { current = false; window.clearInterval(timer) }
  }, [runId, active, revision])
  const sessions = view?.sessions.filter(item => item.tool_attempts != null) ?? []
  const calls = sessions.flatMap(item => item.tool_attempts ?? [])
  const errors = calls.filter(item => item.state === 'failed')
  const incomplete = calls.filter(item => item.state === 'incomplete')
  const validation = errors.filter(item => item.failure_kind === 'argument_validation')
  return <div className="research-tool-attempts" aria-label="工具尝试核对">
    {failed ? <p role="status">工具尝试记录暂时无法读取，失败次数未知。</p>
      : !view ? <p role="status">正在核对工具尝试记录…</p>
      : sessions.length === 0 ? <p role="status">工具尝试日志尚不可用，不能据此认定没有失败。</p>
      : <>
        <p className={errors.length ? 'research-notice warning' : 'research-trust-note'} role="status">已读取日志中有 {calls.length} 次工具尝试，其中取证 {calls.filter(item => item.kind === 'research').length} 次；失败 {errors.length} 次，结果待确认 {incomplete.length} 次。{validation.length > 0 && <>其中 {validation.length} 次参数校验失败。</>}</p>
        <details className="research-trust-sources"><summary>核对工具执行范围</summary>
          <p>上方取证执行数来自网关；这里还包含执行前拒绝，以及委派、复核和报告等工具。两组数量有重叠，不能相加。仅覆盖当前读取的 {sessions.length} 个关联会话，日志可能晚到或缺失；它不代表全部历史尝试已核齐。</p>
          <table className="asset-table"><thead><tr><th>研究职责</th><th>工具尝试</th><th>其中取证</th><th>失败</th><th>结果待确认</th></tr></thead><tbody>{sessions.map((session, index) => <tr key={session.session_id}>
            <td>{session.role === 'main' ? '主研究' : `协作研究 ${index}`}</td><td>{session.tool_attempts!.length}</td><td>{session.tool_attempts!.filter(item => item.kind === 'research').length}</td><td>{session.tool_attempts!.filter(item => item.state === 'failed').length}</td><td>{session.tool_attempts!.filter(item => item.state === 'incomplete').length}</td>
          </tr>)}</tbody></table>
          {sessions.some(item => item.tool_issues?.length) && <p>日志存在缺失、重复或结果冲突，以上仅为可识别记录。</p>}
          {view?.limitations.includes('some_archives_unreadable') && <p>部分归档无法读取，统计不完整。</p>}
          <p>读取时间：{new Date(view!.observed_at).toLocaleString('zh-CN', { hour12: false })}。可在模型用量核对中保存并回看同一口径的核验凭据。</p>
        </details>
      </>}
    <button className="product-link" onClick={() => setRevision(value => value + 1)}>刷新工具记录</button>
  </div>
}
