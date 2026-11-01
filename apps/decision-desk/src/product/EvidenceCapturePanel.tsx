import { useEffect, useRef, useState } from 'react'
import { assets, type EvidenceCapture } from '../api/assets'
import { assetTime } from './assetFormat'
import { CaptureReadback } from './SourceHistoryPanel'

function captureHost(url: string): string {
  try { return new URL(url).hostname || '来源主机未记录' } catch { return '来源主机无法解析' }
}
export function EvidenceCapturePanel({ runId, evidenceId }: { runId: string; evidenceId: string }) {
  const scope = JSON.stringify([runId, evidenceId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [readback, setReadback] = useState<{ scope: string; captures: EvidenceCapture[] | null; loading: boolean; failed: boolean; failureDetail?: string }>({ scope, captures: null, loading: false, failed: false })
  const { captures, loading, failed, failureDetail } = readback.scope === scope ? readback : { captures: null, loading: false, failed: false, failureDetail: undefined }
  const [visibleCount, setVisibleCount] = useState(20)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => {
    pending.current?.abort()
    pending.current = null
    setReadback({ scope, captures: null, loading: false, failed: false })
    setVisibleCount(20)
    return () => { pending.current?.abort(); pending.current = null }
  }, [scope])
  async function load() {
    const requestScope = scope
    const controller = new AbortController(); pending.current?.abort(); pending.current = controller
    const isCurrent = () => !controller.signal.aborted && currentScope.current === requestScope && pending.current === controller
    setReadback(previous => ({ scope: requestScope, captures: previous.scope === requestScope ? previous.captures : null, loading: true, failed: false }))
    try {
      const result = await assets.researchEvidenceCaptures(runId, evidenceId, controller.signal)
      if (isCurrent()) { setReadback({ scope: requestScope, captures: result, loading: false, failed: false }); setVisibleCount(20) }
    } catch (error) { if (isCurrent()) setReadback(previous => ({ ...previous, loading: false, failed: true, failureDetail: error instanceof Error && error.message.startsWith('research_spot_capture_') ? '这份历史行情的原件或关联记录未能通过核验。未用当前行情替代，请保留此证据并联系维护者核对。' : undefined })) }
    finally { if (isCurrent()) setReadback(previous => ({ ...previous, loading: false })) }
  }
  return <div aria-label="采集原文档案">
    <p>查看取证时保存的原始响应；历史数据沿用原采集时间，研究读取不会改写它。</p>
    <button className="button secondary" disabled={loading} onClick={() => void load()}>{loading ? '正在查找原始响应…' : '查找已保存的原始响应'}</button>
    {failed && <p role="alert">{failureDetail ?? '采集记录暂时无法读取，请重试；不能据此判断原文缺失。'}</p>}
    {captures && captures.length > 0 && <p>这组档案保留取证关联的完整请求，可能包含其他来源。每份响应以自己的来源主机和请求端点为准。</p>}
    {captures && captures.length > 0 && <p>共 {captures.length} 份原始响应，已显示 {Math.min(visibleCount, captures.length)} 份。历史行情窗口可能对应多次采集；响应数量不代表窗口无缺口。</p>}
    {captures && (captures.length ? <ul>{captures.slice(0, visibleCount).map((capture, index) => <li key={capture.capture_id}>
      <strong>{captureHost(capture.url)}</strong>
      <p style={{ overflowWrap: 'anywhere' }}>请求端点：{capture.url}</p>
      <small style={{ overflowWrap: 'anywhere' }}>原始响应 SHA-256：{capture.body_hash ?? '未保存正文指纹'}</small>
      <span>采集于 {assetTime(capture.completed_at)} · {capture.status_code === null ? '请求未完成' : `HTTP ${capture.status_code}`}</span>
      {capture.body_ref ? <CaptureReadback captureId={capture.capture_id} index={index + 1} /> : <p>该次请求未保存响应正文。</p>}
    </li>)}</ul> : <p>这份证据没有关联的 HTTP 采集原文，可核对完整摘录与来源链接。</p>)}
    {captures && visibleCount < captures.length && <button className="button secondary" onClick={() => setVisibleCount(count => count + 20)}>再显示 {Math.min(20, captures.length - visibleCount)} 份</button>}
  </div>
}
