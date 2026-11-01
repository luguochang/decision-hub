import { useEffect, useRef, useState } from 'react'
import type { ReviewScheduleView } from '../api/types'
import { inspectFollowupFailure, type FollowupFailureInspection } from '../api/followupInspection52'

export function FollowupFailureDetail52({ plan }: { plan: ReviewScheduleView }) {
  const [savedResult, setResult] = useState<{ identity: string; value: FollowupFailureInspection } | null>(null)
  const [loadingIdentity, setLoading] = useState<string | null>(null)
  const [failedIdentity, setFailed] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const revision = useRef(0)
  const identity = JSON.stringify(plan)
  const result = savedResult?.identity === identity ? savedResult.value : null
  const loading = loadingIdentity === identity
  const failed = failedIdentity === identity
  useEffect(() => {
    revision.current += 1
    active.current?.abort()
    active.current = null
    setResult(null); setFailed(null); setLoading(null)
    return () => { revision.current += 1; active.current?.abort(); active.current = null }
  }, [identity])
  async function inspect() {
    const current = ++revision.current
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    setResult(null); setFailed(null); setLoading(identity)
    try {
      const inspection = await inspectFollowupFailure(plan, controller.signal)
      if (!controller.signal.aborted && current === revision.current) setResult({ identity, value: inspection })
    } catch {
      if (!controller.signal.aborted && current === revision.current) setFailed(identity)
    } finally {
      if (!controller.signal.aborted && current === revision.current) { setLoading(null); active.current = null }
      controller.abort()
    }
  }
  return <div className="followup-failure-detail">
    <button className="button secondary" disabled={loading} onClick={() => void inspect()}>{loading ? '正在核对复查记录…' : result ? '重新核对失败与恢复条件' : '核对失败阶段与恢复条件'}</button>
    {loading && <p role="status">正在读取这次复查的原任务、失败阶段与执行关联记录…</p>}
    {failed && <p role="alert">复查记录未能核对，或读取期间记录已变化。请刷新复查状态后重试；尚未确认具体原因或安全恢复条件。</p>}
    {result && <div aria-label="复查失败核对结果">
      <p><strong>记录阶段：</strong>{result.stage}</p>
      <p><strong>具体原因：</strong>{result.cause}</p>
      {result.occurredAt && <p><strong>失败记录时间：</strong><time dateTime={result.occurredAt}>{new Date(result.occurredAt).toLocaleString('zh-CN', { hour12: false })}</time></p>}
      <p><strong>旧执行记录：</strong>{result.execution}</p>
      <p><strong>恢复条件：</strong>{result.recovery}</p>
      <small>以上是本次读取的任务记录。未发起新研究；后续恢复仍以原任务的最新核验回执为准。</small>
    </div>}
  </div>
}
