import { useEffect, useRef, useState } from 'react'
import { request } from '../api/client'

type InputAudit = {
  checked_at: string; selection: { selected: number }; verified_inputs: number; invalid_inputs: number;
  verified_labels: number; invalid_labels: number; pending_labels: number; usable_observations: number;
  directional_usable: number; neutral_usable: number; issues: Record<string, number>;
}
const issues: Record<string, string> = {
  pair_input_capture_missing: '缺少研究原文的采集凭据',
  pair_input_capture_missing_or_late: '采集凭据缺失、请求失败或晚于观点冻结时间',
  pair_fact_after_cutoff_or_scope_mismatch: '事实记录晚于冻结时间或与研究范围不符',
  pair_parsed_archive_missing_or_late: '解析版本缺失或晚于冻结时间',
  prospective_raw_archive_missing: '原始文件暂不可读',
  raw_capture_archive_corrupt: '原始文件完整性校验未通过',
  archive_hash_mismatch: '归档内容与登记哈希不一致',
  fact_input_after_freeze: '研究采用了冻结时间之后的事实记录',
  source_publication_after_cutoff: '发布记录晚于观点冻结时间',
  market_return_mismatch: '保存的行情变化与原始行情不一致',
  direction_label_mismatch: '保存的方向判断与行情重算不一致',
}
export function ObservationInputAuditPanel() {
  const [data, setData] = useState<InputAudit | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  async function load() {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller
    setLoading(true); setFailed(false)
    try {
      const result = await request<InputAudit>('/v1/evolution/observation-input-audit', { signal: controller.signal })
      if (!controller.signal.aborted) setData(result)
    } catch { if (!controller.signal.aborted) setFailed(true) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }
  return <section className="planning-call-panel planning-summary-panel" aria-label="研究材料与行情核查">
    <button className="button secondary" disabled={loading} onClick={() => void load()}>{loading ? '正在核查本地原文与行情…' : '核查原文与行情'}</button>
    {failed && <p role="alert">核查未完成，请重试。{data && '下方保留上次结果，不代表当前状态。'}</p>}
    {data && <><p>本次检查所选的 {data.selection.selected} 条观点；不调用模型，也不补写旧数据。</p>
      <div className="product-monitor">
        <div><span>研究输入通过核查</span><strong>{data.verified_inputs}</strong></div>
        <div><span>行情标签通过核查</span><strong>{data.verified_labels}</strong></div>
        <div><span>两项均通过</span><strong>{data.usable_observations}</strong></div>
      </div>
      <p>两项均通过的记录中，方向观点 {data.directional_usable} 条，中性观点 {data.neutral_usable} 条。中性观点不计方向准确率；相关窗口也不能合并成独立样本。</p>
      <p>研究输入未通过 {data.invalid_inputs} 条；行情标签未通过 {data.invalid_labels} 条，待观察或核查 {data.pending_labels} 条。行情正确不代表研究材料完整。</p>
      {!!Object.keys(data.issues).length && <ul>{Object.entries(data.issues).map(([code, count]) => <li key={code}>{issues[code] ?? '其他材料核查问题'}：{count} 条</li>)}</ul>}
      <p>核查通过只确认记录关联、时间与原文完整性，不代表解析含义正确、方法配置完整或候选已胜过基线。</p>
      <small>核查于 {new Date(data.checked_at).toLocaleString('zh-CN', { hour12: false })}</small>
    </>}
  </section>
}
