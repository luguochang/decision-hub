import { useEffect, useRef, useState } from 'react'
import { ApiRequestError } from '../api/client'
import { readHistoryOutcomeEvaluations, type HistoryOutcomeIndexV2 } from '../api/historyOutcomeEvaluations'
import { HistoryOutcomeResults } from './HistoryOutcomeResults'

const clock = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
}).format(new Date(value)) + ' UTC'
const failure = (error: unknown) => error instanceof ApiRequestError && [401, 403].includes(error.status ?? 0)
  ? '本机身份已失效或未获验证记录的读取权限，请重新完成历史能力身份验证。'
  : error instanceof ApiRequestError && [404, 503].includes(error.status ?? 0)
    ? '前瞻验证服务尚未配置或部署，当前没有可验收的验证记录。'
    : '验证列表读取未完成，尚未确认可读取的批次。'

/** Users select a server-granted batch; no protocol/hash/T input or automatic mutation. */
export function HistoryOutcomeEntry({ authRevision = 0, ownerStatusStale = false }: {
  authRevision?: number; ownerStatusStale?: boolean;
}) {
  const [index, setIndex] = useState<HistoryOutcomeIndexV2 | null>(null)
  const [indexContext, setIndexContext] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const context = `${authRevision}:${ownerStatusStale}:${revision}`
  const generation = useRef(0)
  useEffect(() => {
    const controller = new AbortController()
    const current = ++generation.current
    setIndex(null); setSelectedId(null); setError('')
    setLoading(!ownerStatusStale)
    if (!ownerStatusStale) {
      void readHistoryOutcomeEvaluations(controller.signal).then(value => {
        if (controller.signal.aborted || generation.current !== current) return
        setIndex(value)
        setIndexContext(context)
        setSelectedId(value.evaluations[0]?.protocol_id ?? null)
      }).catch(cause => {
        if (controller.signal.aborted || generation.current !== current) return
        setError(failure(cause)); setIndex(null); setSelectedId(null)
      }).finally(() => {
        if (!controller.signal.aborted && generation.current === current) setLoading(false)
      })
    }
    return () => { controller.abort(); generation.current++ }
  }, [authRevision, ownerStatusStale, revision, context])
  const visibleIndex = !ownerStatusStale && indexContext === context ? index : null
  const selected = visibleIndex?.evaluations.find(item => item.protocol_id === selectedId)
  return <section className="product-card history-section" aria-label="前瞻验证批次">
    <div className="history-hourly-heading"><div><h2>前瞻验证与结果验收</h2>
      <p>查看服务器已授权的验证记录，核对按时发布、缺失与迟到，以及预测相对基线的实际改善。</p></div>
      <button className="button secondary" disabled={loading || ownerStatusStale}
        onClick={() => setRevision(value => value + 1)}>刷新验证列表</button></div>
    {ownerStatusStale && <p role="status">历史身份状态尚未确认，已停止读取评估原件。请先刷新历史能力状态。</p>}
    {!ownerStatusStale && loading && <p role="status">正在读取已授权的验证批次…</p>}
    {error && !ownerStatusStale && <p role="alert">{error}</p>}
    {visibleIndex && visibleIndex.evaluations.length === 0 && <p role="status">
      尚未开始正式前瞻验证。需要后台先登记、独立确认并授权验证批次；本页不会自动登记或生产预测。
    </p>}
    {visibleIndex && visibleIndex.evaluations.length > 0 && <>
      <div className="history-pair"><label>选择验证批次<select className="button secondary" aria-label="选择验证批次"
        value={selectedId ?? ''} onChange={event => setSelectedId(event.target.value)}>
        {visibleIndex.evaluations.map(item => <option key={item.protocol_id} value={item.protocol_id}>
          {clock(item.first_hour)} 至 {clock(item.last_hour)} · {item.state === 'confirmed' ? '登记已确认' : '登记待确认'}
        </option>)}
      </select></label></div>
      <p>授权列表截至 {clock(visibleIndex.actual_as_of)}；本次列表读取未重新核验完整原件。</p>
      {selected && <p>{selected.state === 'confirmed'
        ? '登记已取得独立确认；是否按时发布、路径是否完整以及评分是否改善，请继续核对下方结果。'
        : '这轮登记尚未取得独立确认，不能当作已经开始的正式前瞻验证。'}
        固定比特币、以太坊共 {selected.expected_total} 个小时机会，受阻和失败仍保留在分母中。</p>}
    </>}
    {selected && <HistoryOutcomeResults key={`${selected.protocol_id}:${authRevision}:${revision}`}
      protocolId={selected.protocol_id} authRevision={authRevision} />}
  </section>
}
