import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import type { RevisionFeedbackEvaluation } from '@decision-hub/contracts-ts'
import { checkedRevisionFeedbackReceipt, delivery, productOwner, type RevisionFeedbackRequest } from '../api/delivery'
import { acknowledgeRequest, listPendingRequests, pendingRequest } from '../research/pendingRequests'
import { assetTime, directionName, horizonName } from './assetFormat'

type SavedFeedback = { feedback_id: string; verdict: string; notes: string; created_at: string }
type FeedbackReceipt = Awaited<ReturnType<typeof delivery.feedback>>
const verdictLabels: Record<string, string> = { useful: '有帮助', not_useful: '帮助有限', incorrect: '发现错误', needs_review: '需要核查' }
export function RevisionFeedback({ revision, sourceRunId, saved }: { revision: string; sourceRunId?: string; saved?: SavedFeedback | null }) {
  return <RevisionFeedbackForm key={JSON.stringify([productOwner, revision, sourceRunId])} revision={revision} sourceRunId={sourceRunId} saved={saved} />
}

function restoredFeedback(revision: string): { pending: RevisionFeedbackRequest | null; error: string | null } {
  try {
    const records = listPendingRequests('report-feedback').map(record => {
      const value = record.payload as Partial<Omit<RevisionFeedbackRequest, 'id'>> | null
      if (!record.id || record.signature !== JSON.stringify(record.payload)
        || !value || typeof value.revision !== 'string' || !value.revision
        || typeof value.owner !== 'string' || value.owner !== productOwner
        || typeof value.notes !== 'string' || !value.notes.trim() || value.notes.length > 10000
        || !value.verdict || !Object.hasOwn(verdictLabels, value.verdict)) {
        throw new Error('反馈凭据内容或所属用户无法核对，尚未发送请求。请先恢复完整凭据。')
      }
      return { revision: value.revision, owner: value.owner, notes: value.notes, verdict: value.verdict, id: record.id } as RevisionFeedbackRequest
    }).filter(record => record.revision === revision)
    if (records.length > 1) throw new Error('这一版存在多笔未确认反馈，尚未发送请求。请先核对原请求凭据。')
    return { pending: records[0] ?? null, error: null }
  } catch (error) {
    return { pending: null, error: error instanceof Error ? error.message : '无法读取未确认反馈凭据，尚未发送请求。' }
  }
}

function RevisionFeedbackForm({ revision, sourceRunId, saved }: { revision: string; sourceRunId?: string; saved?: SavedFeedback | null }) {
  const client = useQueryClient()
  const [journal, setJournal] = useState(() => restoredFeedback(revision))
  const [open, setOpen] = useState(Boolean(journal.pending))
  const [request, setRequest] = useState(journal.pending)
  const [notes, setNotes] = useState(journal.pending?.notes ?? '')
  const [verdict, setVerdict] = useState<RevisionFeedbackRequest['verdict']>(journal.pending?.verdict ?? 'useful')
  const [confirmed, setConfirmed] = useState<FeedbackReceipt | null>(null)
  const [confirmedRequest, setConfirmedRequest] = useState<RevisionFeedbackRequest | null>(null)
  const [supplementing, setSupplementing] = useState(false)
  const inFlight = useRef(false)
  const latest = useQuery({
    queryKey: ['revision-feedback-latest', productOwner, revision, sourceRunId],
    queryFn: () => delivery.latestFeedbackEvaluation(revision, sourceRunId),
    retry: false,
  })
  const exact = useQuery({
    queryKey: ['revision-feedback-evaluation', productOwner, revision, confirmedRequest?.id, sourceRunId],
    queryFn: () => {
      if (!confirmedRequest) throw new Error('原反馈请求尚未确认。')
      return delivery.feedbackEvaluation(confirmedRequest, sourceRunId)
    },
    enabled: confirmedRequest !== null,
    retry: false,
  })
  const submit = useMutation({
    retry: false,
    mutationFn: async (value: RevisionFeedbackRequest) => {
      const receipt = checkedRevisionFeedbackReceipt(await delivery.feedback(value.revision, value.verdict, value.notes, value.id), value)
      // An original ACK can arrive after a newer opinion on the same revision.
      // Keep its exact receipt; only the owner latest GET selects the latest one.
      client.setQueryData(['revision-feedback-receipt', value.owner, value.revision, value.id], receipt)
      acknowledgeRequest('report-feedback', value.id)
      return receipt
    },
    onSuccess: (receipt, value) => {
      setConfirmed(receipt); setConfirmedRequest(value); setRequest(null); setSupplementing(false)
      void client.invalidateQueries({ queryKey: ['product-reports'] })
      void client.invalidateQueries({ queryKey: ['revision-feedback-latest', productOwner, value.revision] })
      if (sourceRunId) void client.invalidateQueries({ queryKey: ['research-value-evaluation', sourceRunId] })
    },
    onSettled: () => { inFlight.current = false },
  })
  const restore = () => {
    const next = restoredFeedback(revision); setJournal(next); setRequest(next.pending)
    if (next.pending) { setNotes(next.pending.notes); setVerdict(next.pending.verdict); setOpen(true) }
  }
  const acknowledged = supplementing ? null : confirmed ?? (!request && !journal.error ? latest.data?.feedback ?? saved : null)
  const evaluation = confirmedRequest ? exact : latest
  if (acknowledged) return <div className="revision-feedback">
    <strong>已反馈：{verdictLabels[acknowledged.verdict] ?? '已保存'}</strong><small> · {assetTime(acknowledged.created_at)}</small>
    <details><summary>查看已保存的反馈</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{acknowledged.notes}</p><p>已关联这一版报告，原报告保持不变。</p></details>
    <FeedbackEvaluationState receipt={evaluation.data} pending={evaluation.isPending} failed={evaluation.isError} retry={() => void evaluation.refetch()} />
    {confirmed && latest.data && latest.data.feedback.request_id !== confirmed.request_id && <details>
      <summary>查看这一版的最新反馈</summary><p>上方是已确认的原请求；这一版另有后来保存的反馈。</p>
      <strong>已反馈：{verdictLabels[latest.data.feedback.verdict]}</strong><p>{latest.data.feedback.notes}</p>
      <FeedbackEvaluationState receipt={latest.data} pending={latest.isPending} failed={latest.isError} retry={() => void latest.refetch()} />
    </details>}
    {latest.isSuccess && !journal.error && <button type="button" className="product-link" onClick={() => {
      const stored = restoredFeedback(revision)
      if (stored.error || stored.pending) {
        setJournal(stored); setRequest(stored.pending); setSupplementing(true); setOpen(true)
        if (stored.pending) { setNotes(stored.pending.notes); setVerdict(stored.pending.verdict) }
        return
      }
      setConfirmed(null); setConfirmedRequest(null); setSupplementing(true); setNotes(''); setVerdict('useful'); setOpen(true)
    }}>补充反馈</button>}
  </div>
  return <div className="revision-feedback">
    {supplementing && confirmed && <details><summary>查看已确认的原反馈</summary><p>{confirmed.notes}</p><p>原反馈已保存；下方单独核对新意图。</p></details>}
    {journal.error && <p role="alert">{journal.error}<button type="button" className="product-link" onClick={restore}>重新读取反馈凭据</button></p>}
    {!request && latest.isError && <p>这一版最新反馈暂时读取失败，原意见不会因此被撤销。<button type="button" className="product-link" onClick={() => void latest.refetch()}>重新读取最新反馈</button></p>}
    <button className="product-link" onClick={() => setOpen(!open)}>{open ? '收起反馈' : '反馈这一版'}</button>
    {request && <p role="status">这笔反馈保存尚未确认。原版本、评价和内容已锁定；核对原请求回执会复用同一凭据。返回此页不会自动提交。</p>}
    {request && saved && <details><summary>查看收件箱中已保存的反馈</summary><p>{saved.notes}</p><p>这条列表记录未提供当前请求身份，仍需核对原请求回执。</p></details>}
    {open && <form onSubmit={event => {
      event.preventDefault()
      if (inFlight.current || journal.error) return
      const stored = restoredFeedback(revision)
      if (stored.error) { setJournal(stored); return }
      let next = request
      if (!next && stored.pending) { setJournal(stored); setRequest(stored.pending); setNotes(stored.pending.notes); setVerdict(stored.pending.verdict); return }
      if (!next) {
        const data = new FormData(event.currentTarget), content = String(data.get('notes') ?? '').trim()
        const choice = String(data.get('verdict')) as RevisionFeedbackRequest['verdict']
        if (!content || content.length > 10000 || !Object.hasOwn(verdictLabels, choice)) return
        const payload = { revision, verdict: choice, notes: content, owner: productOwner }
        setNotes(content); setVerdict(choice)
        try { next = { ...payload, id: pendingRequest('report-feedback', payload).id } }
        catch (error) { setJournal({ pending: null, error: error instanceof Error ? error.message : '无法保存反馈凭据，尚未发送请求。' }); return }
        setRequest(next)
      }
      inFlight.current = true
      submit.mutate(next)
    }}>
      <label>这份报告是否帮助了你<select name="verdict" value={verdict} onChange={event => setVerdict(event.target.value as RevisionFeedbackRequest['verdict'])} disabled={Boolean(request) || submit.isPending || Boolean(journal.error)}><option value="useful">有帮助</option><option value="not_useful">帮助有限</option><option value="incorrect">发现错误</option><option value="needs_review">需要核查</option></select></label>
      <label>具体原因<textarea name="notes" value={notes} onChange={event => setNotes(event.target.value)} required maxLength={10000} disabled={Boolean(request) || submit.isPending || Boolean(journal.error)} placeholder="例如：哪些依据有帮助，或哪一处需要补证" /></label>
      <p className="product-muted">反馈按当前报告版本保存，供后续复核参考；不会直接更改研究方法。</p>
      <button className="button secondary" disabled={submit.isPending || Boolean(journal.error)}>{submit.isPending ? '正在保存…' : request ? '核对原请求回执' : '保存反馈'}</button>
      {submit.isError && <RevisionFeedbackError message={submit.error.message} />}
    </form>}
  </div>
}

const unavailableLabels: Record<string, string> = {
  source_missing: '原评估材料未齐', source_invalid: '原评估材料无法核验',
  source_identity_mismatch: '原评估材料的所属版本无法核对', source_hash_mismatch: '原评估材料完整性无法核验',
  source_conflict: '原评估材料存在冲突', source_not_research: '这版报告不适用研究评估',
  source_not_terminal: '反馈保存时研究尚未结束',
}
const outcomeReasonLabels: Record<string, string> = {
  outcome_pending: '反馈当时尚待到期观察',
  direction_not_applicable: '该发布预测记录不计方向成绩；原研究判断请查看已保存报告与后续观察',
  market_path_unavailable: '反馈当时行情路径未齐',
}

function FeedbackEvaluationState({ receipt, pending, failed, retry }: { receipt?: RevisionFeedbackEvaluation | null; pending: boolean; failed: boolean; retry: () => void }) {
  return <div className="revision-feedback-evaluation" aria-label="这笔反馈的当时评估">
    {failed && <p role="status">{receipt ? '上次核对的当时评估仍保留，本次读取尚未确认。' : '反馈已保存，当时评估暂时无法读取。'}<button type="button" className="product-link" onClick={retry}>重新读取当时评估</button></p>}
    {pending && !receipt && <p role="status">反馈已保存，正在读取当时评估…</p>}
    {!pending && !failed && !receipt && <p>这条已保存意见尚未返回可核对的当时评估记录。</p>}
    {receipt && <FeedbackEvaluation key={JSON.stringify([receipt.owner_id, receipt.revision_id, receipt.feedback.request_id, receipt.binding?.binding_hash])} receipt={receipt} />}
  </div>
}

function FeedbackEvaluation({ receipt }: { receipt: RevisionFeedbackEvaluation }) {
  const [expanded, setExpanded] = useState(false)
  const basis = useQuery({
    queryKey: ['revision-feedback-basis', receipt.owner_id, receipt.revision_id, receipt.feedback.request_id, receipt.binding?.binding_hash],
    queryFn: () => delivery.feedbackEvaluationBasis(receipt),
    enabled: expanded && receipt.binding !== null,
    retry: false,
  })
  const value = receipt.value
  const legacy = receipt.confirmation_basis === 'legacy_feedback_receipt_only'
  return <>
    <p>{legacy ? '未记录当时评估' : receipt.status === 'available' && value
      ? `当时评估：${{ unlabeled: '尚未标注', useful: '有帮助', partial: '部分有帮助', not_useful: '没有帮助' }[value.usefulness]}`
      : `当时评估暂不可用：${receipt.reason ? unavailableLabels[receipt.reason] ?? '原依据无法核对' : '原依据无法核对'}`}</p>
    {value && <small>评估记录于 {assetTime(value.evaluated_at)}，不会随后来反馈或到期标签改写。</small>}
    {!legacy && <details onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>查看当时评估依据</summary>
      {value && <>
        <p>用途：{{ directional: '方向研究', research_only: '仅供研究', failed: '任务失败' }[value.mode]} · 引用可追溯 {Math.round(value.citation_traceability * 100)}%</p>
        <p>必需材料覆盖：{value.hard_coverage_ratio === null ? '未核验' : `${Math.round(value.hard_coverage_ratio * 100)}%`}；补充材料覆盖：{value.soft_coverage_ratio === null ? '未核验' : `${Math.round(value.soft_coverage_ratio * 100)}%`}。覆盖率不代表结论正确率。</p>
        {value.coverage_cutoff_at && <p>当时信息截止：{assetTime(value.coverage_cutoff_at)}</p>}
        <p>原费用：{value.known_cost_usd === null ? '未知' : `$${value.known_cost_usd.toFixed(4)}`}{value.cost_status === 'known' ? '' : '（部分或未知）'}</p>
        <h4>发布预测账本</h4>
        <p>这里保留反馈当时的发布预测记录。独立研究方向和到期观察请查看已保存报告与后续观察；原账本不会重新计算或改写。</p>
        <p><a className="product-link" href={`#/report?run=${encodeURIComponent(value.run_id)}&back=${encodeURIComponent(window.location.hash || '#/research')}`}>查看已保存报告</a> · <a className="product-link" href={`#/results?run=${encodeURIComponent(value.run_id)}&back=${encodeURIComponent(window.location.hash || '#/research')}`}>查看后续观察</a></p>
        <p>{value.outcomes.length ? `当时保留 ${value.outcomes.length} 项发布预测记录；${value.outcomes.filter(item => item.direction_correct === null).length} 项未计方向成绩。` : '当时没有可复核的发布预测记录。'}缺标签不会算作未命中。</p>
        {value.outcomes.map(item => <div key={item.forecast_id}>
          <strong>{horizonName(item.horizon)} · {item.direction === 'no_trade' ? '未授权交易的发布记录' : directionName(item.direction)}</strong>
          <p>{item.direction === 'neutral' || item.direction === 'no_trade' ? '方向不计成绩' : item.direction_correct === null ? '方向尚未复核' : item.direction_correct ? '方向复核正确' : '方向复核错误'} · 净收益：{item.net_return_pct === null ? '未记录' : `${item.net_return_pct.toFixed(3)}%`} · 概率误差：{item.brier_score === null ? '未记录' : item.brier_score.toFixed(3)}</p>
          {item.unavailable_reason && <small>{outcomeReasonLabels[item.unavailable_reason] ?? '反馈当时观察依据不足'}</small>}
        </div>)}
      </>}
      {receipt.binding && <small>原记录保存于 {assetTime(receipt.binding.recorded_at)}</small>}
      {basis.isPending && expanded && <p role="status">正在读取原评估依据…</p>}
      {basis.isError && <p role="alert">原评估依据暂时无法核对。<button type="button" className="product-link" onClick={() => void basis.refetch()}>重新读取原依据</button></p>}
      {basis.data && <details><summary>核对完整原依据</summary><p>以下为这笔反馈保存时的原记录，不会重新采样或补算。</p><pre className="asset-json" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{basis.data.basis_json}</pre></details>}
    </details>}
  </>
}

function RevisionFeedbackError({ message }: { message: string }) {
  const readableChinese = /[\u3400-\u9fff]/.test(message) && !/[A-Za-z]/.test(message)
  return <div role="alert"><p>保存未确认，请核对原请求回执。</p>{readableChinese
    ? <p>{message}</p>
    : <details><summary>查看反馈错误原始记录</summary><pre className="asset-json">{message}</pre></details>}</div>
}

