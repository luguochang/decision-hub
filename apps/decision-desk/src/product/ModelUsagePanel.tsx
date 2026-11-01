import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ResearchUsageSnapshot, ResearchUsageSnapshotPage, UsageCollectionView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { UsageCallsPanel } from './UsageCallsPanel'

export function ModelUsagePanel({ runId, visible = true }: { runId: string; visible?: boolean }) {
  const [snapshot, setSnapshot] = useState<ResearchUsageSnapshot | null>(null)
  const [history, setHistory] = useState<ResearchUsageSnapshotPage | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyFailed, setHistoryFailed] = useState(false)
  const [collection, setCollection] = useState<UsageCollectionView[] | null>(null)
  const [collectionFailed, setCollectionFailed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const request = useRef(0)
  const command = useRef<string | null>(null)
  const visibleRef = useRef(visible)
  const requestedRun = useRef<string | null>(null)
  useLayoutEffect(() => { visibleRef.current = visible }, [visible])
  const view = snapshot?.view
  const loadCollection = async () => {
    if (!visibleRef.current) return
    const version = request.current
    try {
      const result = await assets.usageCollection(runId)
      if (version === request.current) { setCollection(result); setCollectionFailed(false) }
    } catch {
      if (version === request.current) setCollectionFailed(true)
    }
  }
  const loadHistory = async (offset: number, selectLatest = false) => {
    if (!visibleRef.current) return
    const version = request.current
    setHistoryLoading(true); setHistoryFailed(false)
    try {
      const result = await assets.usageHistory(runId, offset)
      if (version !== request.current) return
      setHistory(result)
      if (selectLatest) setSnapshot(result.items[0] ?? null)
    } catch {
      if (version === request.current) setHistoryFailed(true)
    } finally {
      if (version === request.current) setHistoryLoading(false)
    }
  }
  useEffect(() => {
    request.current += 1
    command.current = null
    setSnapshot(null); setHistory(null); setLoading(false); setFailed(false)
    setCollection(null); setCollectionFailed(false)
    return () => { request.current += 1 }
  }, [runId])
  useEffect(() => {
    if (!visible) return
    const firstRead = requestedRun.current !== runId
    requestedRun.current = runId
    void loadHistory(firstRead ? 0 : history?.offset ?? 0, firstRead || !snapshot)
    void loadCollection()
  }, [runId, visible])
  const read = async () => {
    const version = request.current
    setLoading(true); setFailed(false)
    command.current ??= crypto.randomUUID()
    try {
      const result = await assets.captureUsage(runId, command.current)
      if (version !== request.current) return
      command.current = null
      setSnapshot(result)
      await loadHistory(0)
    } catch {
      if (version === request.current) setFailed(true)
    } finally {
      if (version === request.current) setLoading(false)
    }
  }
  return <section aria-label="模型用量核对" className="research-section">
    <h3>模型用量核对</h3>
    <p>查看本次主研究和协作研究留下的模型用量记录。完整运行用量与费用仍待对账，缺失部分不按零计算。</p>
    <p>系统会自动核对已登记的会话版本；归档晚到或读取失败会重试，核验内容变化时保存新版本。</p>
    {collectionFailed ? <p role="status">自动归集状态暂不可用。</p> : collection && <details><summary>自动归集状态（{collection.length} 个会话版本，最多显示最近 100 个）</summary>
      <p>新任务与进行中的研究优先检查。已结束且记录未变化的任务会逐步延长检查间隔，最长 6 小时；缺失用量仍保留为未知。计划时间是最早可检查时间，排队时可能延后。</p>
      {collection.length === 0 && <p>尚未登记自动归集任务；现有核验仍可回看。</p>}
      {collection.map(item => <p key={`${item.session_id}:${item.generation}`}>会话版本 {item.generation}：{{ pending: '等待核对', waiting_archive: '等待日志归档，将自动重试', partial: '部分记录已保存，将继续核对', captured: '已保存会话核验', retry_wait: '核对失败，等待自动重试' }[item.status]}<br />最近检查：{item.checked_at ? new Date(item.checked_at).toLocaleString('zh-CN', { hour12: false }) : '尚未检查'} · 计划检查：{new Date(item.next_check_at).toLocaleString('zh-CN', { hour12: false })}{item.error_code && <><br />技术原因：<code>{item.error_code}</code></>}</p>)}
    </details>}
    <div className="design-filter-row">
    <button className="button secondary" disabled={loading || historyLoading} onClick={() => void read()}>{loading ? '正在核对并保存…' : '核对并保存用量'}</button>
    <button className="button secondary" disabled={loading || historyLoading} onClick={() => { void loadHistory(0, true); void loadCollection() }}>刷新已保存记录</button>
    </div>
    {historyLoading && <p role="status">正在读取已保存的核验记录…</p>}
    {historyFailed && <p role="alert">历史核验记录读取失败。<button className="product-link" onClick={() => void loadHistory(history?.offset ?? 0, !snapshot)}>重试读取历史</button></p>}
    {failed && <p role="alert">本次核对或保存未成功，请重试。下方若有记录，仍是上次已保存的版本。</p>}
    {history && <details><summary>历史核验记录（{history.total} 次）</summary>
      {history.total === 0 && <p>尚未保存核验记录。点击上方按钮核对日志并落库。</p>}
      {history.items.map(item => <p key={item.snapshot_id}><button className="product-link" aria-pressed={snapshot?.snapshot_id === item.snapshot_id} onClick={() => setSnapshot(item)}>{new Date(item.recorded_at).toLocaleString('zh-CN', { hour12: false })} · {item.view.sessions.length} 个会话 · {item.view.status === 'unavailable' ? '日志不可用' : '部分用量'}</button></p>)}
      <button className="button secondary" disabled={loading || historyLoading || history.offset === 0} onClick={() => void loadHistory(Math.max(0, history.offset - 20))}>上一页核验</button>
      <button className="button secondary" disabled={loading || historyLoading || history.offset + history.items.length >= history.total} onClick={() => void loadHistory(history.offset + 20)}>下一页核验</button>
    </details>}
    {snapshot && <p>当前展示已保存核验（会话版本 {snapshot.session_generation ?? '未知'}），记录时间：{new Date(snapshot.recorded_at).toLocaleString('zh-CN', { hour12: false })}。刷新页面仍可回看；它不代表当前实时状态。</p>}
    {view?.status === 'unavailable' && <p role="status">尚无可读取的关联模型日志，暂不能核对用量。</p>}
    {snapshot && view && view.sessions.length > 0 && <>
      <p role="status">已找到 {view.sessions.length} 个关联会话。以下为各会话普通步骤的最终记录；压缩与失败尝试见逐调用明细，尚不能直接相加为完整账单。</p>
      <table className="asset-table"><thead><tr><th>研究职责</th><th>模型步骤</th><th>有效用量记录</th><th>已记录词元</th><th>记录状态</th></tr></thead><tbody>{view.sessions.map((item, index) => <tr key={item.session_id}>
        <td>{item.role === 'main' ? '主研究' : `协作研究 ${index}`}</td><td>{item.model_steps}</td><td>{item.usage_samples}</td><td>{item.recorded_tokens === null ? '未知' : item.recorded_tokens.toLocaleString()}</td><td>{item.status === 'accounted' ? '已结束步骤记录齐全' : '记录不完整'}</td>
      </tr>)}</tbody></table>
      <UsageCallsPanel key={snapshot.snapshot_id} sessions={view.sessions} />
      {view.sessions.some(item => item.tool_attempts != null) ? <p>本次已保存工具日志：{view.sessions.flatMap(item => item.tool_attempts ?? []).length} 次尝试，失败 {view.sessions.flatMap(item => item.tool_attempts ?? []).filter(item => item.state === 'failed').length} 次，结果待确认 {view.sessions.flatMap(item => item.tool_attempts ?? []).filter(item => item.state === 'incomplete').length} 次。包含取证前拒绝和研究辅助工具，与网关统计有重叠，不能相加；仅代表已读取归档。</p> : <p>这份历史核验没有工具尝试明细，不能视为零失败。</p>}
      <p>核对时间：{new Date(view.observed_at).toLocaleString('zh-CN', { hour12: false })}。费用需要匹配实际服务商价格；重试、压缩调用和派生会话仍需完整对账。</p>
      {view.limitations.includes('some_archives_unreadable') && <p>部分归档无法读取，本次结果可能不完整。</p>}
      <details><summary>查看核验凭据</summary><p>核验编号：<code>{snapshot.snapshot_id}</code><br />核验内容 SHA-256：<code>{snapshot.payload_sha256}</code><br />核算版本：<code>{snapshot.accounting_version}</code> · 会话代次：{snapshot.session_generation ?? '未知'}</p>{view.sessions.map(item => <p key={item.session_id}><code>{item.session_id}</code><br />归档 SHA-256：<code>{item.archive_sha256}</code>{item.issues.length > 0 && <><br />缺失边界、重试或用量异常：{item.issues.length} 项</>}</p>)}</details>
    </>}
  </section>
}
