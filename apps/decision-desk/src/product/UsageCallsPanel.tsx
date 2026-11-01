import { useState } from 'react'
import type { ResearchUsageSnapshot } from '@decision-hub/contracts-ts'

type Session = ResearchUsageSnapshot['view']['sessions'][number]
const count = (value: number | null) => value === null ? '未知' : value.toLocaleString()
const time = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录'
const issueLabels: Record<string, string> = {
  invalid_or_missing_usage: '用量缺失或字段矛盾', duplicate_step_start: '步骤开始记录重复',
  retry_attempt_not_bill_reconciled: '失败尝试尚未与账单对齐', duplicate_final_message: '最终记录重复，不能直接相加',
  final_usage_missing: '缺少最终用量', final_message_missing: '缺少最终响应',
  compaction_call_identity_unverified: '压缩调用身份尚未核实', compaction_failed_usage_unknown: '压缩失败，用量可能缺失',
  model_route_unknown: '模型路由未记录', call_not_finalized: '调用未形成最终记录',
}

export function UsageCallsPanel({ sessions }: { sessions: Session[] }) {
  const [selected, setSelected] = useState(0)
  const [offset, setOffset] = useState(0)
  const session = sessions[selected]
  const calls = session?.calls
  return <details className="usage-call-panel"><summary>按调用查看输入、输出与缓存</summary>
    <p>普通输入不含缓存；推理词元包含在输出中，不重复相加。这里展示日志可识别的调用尝试，缺失项为未知，服务商账单仍待核对。</p>
    <label>选择研究会话 <select aria-label="选择用量会话" value={selected} onChange={event => { setSelected(Number(event.target.value)); setOffset(0) }}>
      {sessions.map((item, index) => <option key={item.session_id} value={index}>{item.role === 'main' ? '主研究' : `协作研究 ${index}`} · {item.calls ? `${item.calls.length} 次尝试` : '旧核验无明细'}</option>)}
    </select></label>
    {!calls ? <p>此历史核验未保存逐调用明细。可刷新最新记录，或重新核对并保存；旧核验保持原样。</p> : <>
      <p>共 {calls.length} 次可识别尝试，其中压缩 {calls.filter(item => item.kind === 'compaction').length} 次，失败或未完成 {calls.filter(item => item.state !== 'finalized').length} 次，有待核对项 {calls.filter(item => item.issues.length > 0).length} 次。</p>
      <div className="usage-call-scroll"><table className="asset-table"><thead><tr><th>调用与凭据</th><th>模型</th><th>普通输入</th><th>输出</th><th>缓存读取</th><th>缓存写入</th><th>记录总量</th></tr></thead><tbody>
        {calls.slice(offset, offset + 20).map(item => <tr key={item.ordinal}>
          <td><details><summary>{item.kind === 'compaction' ? '上下文压缩' : `第 ${item.turn ?? '?'} 轮 · 步骤 ${item.step ?? '?'}`} · 尝试 {item.attempt}</summary>
            <p>记录开始：{time(item.started_at)}<br />用量记录：{time(item.observed_at)}<br />模型服务：{item.provider ?? '未记录'}<br />{({ finalized: '已有最终响应', failed: '尝试失败', incomplete: '尚无最终响应' })[item.state]}<br />用量依据：{({ final: '最终响应', stream: '流式片段（未完整确认）', none: '没有可用记录' })[item.usage_source]}<br />其中推理词元：{count(item.reasoning_tokens)}</p>
            {item.issues.length > 0 && <ul>{item.issues.map(issue => <li key={issue}>{issueLabels[issue] ?? '需要进一步核对'}</li>)}</ul>}
          </details>{item.issues.length > 0 && <small>有待核对项</small>}</td>
          <td>{item.model ?? '未记录'}</td><td>{count(item.input_tokens)}</td><td>{count(item.output_tokens)}</td><td>{count(item.cache_read_tokens)}</td><td>{count(item.cache_write_tokens)}</td><td>{count(item.total_tokens)}</td>
        </tr>)}
      </tbody></table></div>
      {calls.length === 0 && <p>该会话尚未留下可识别的模型调用。</p>}
      <div className="design-filter-row"><button className="button secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页调用</button><span>{calls.length ? `${offset + 1}–${Math.min(offset + 20, calls.length)} / ${calls.length}` : '0 / 0'}</span><button className="button secondary" disabled={offset + 20 >= calls.length} onClick={() => setOffset(offset + 20)}>下一页调用</button></div>
    </>}
  </details>
}
