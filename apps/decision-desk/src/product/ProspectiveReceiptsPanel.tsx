import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { researchSessionResultSchema } from '@decision-hub/contracts-ts'
import { z } from 'zod'
import { assets, type HarnessComparisonView, type HarnessOwnerSession } from '../api/assets'
import { researchDisplayText } from './researchLanguage'
import { requirementNames, technicalSummaryName } from '../research/technicalPresentation'

const states: Record<string, string> = { pending: '等待执行', running: '执行中', predictions_saved: '两份预测已保存', incomplete: '执行未完整返回', excluded: '输入未通过', succeeded: '预测已保存', failed: '调用失败', unknown: '结果未知', late: '超过提交截止时间', expired: '未在截止前开始', completed: '行情已核验', retry: '行情核验待重试' }
const directions: Record<string, string> = { long: '看涨', short: '看跌', neutral: '中性', no_trade: '暂不判断方向' }
const labelStates: Record<string, string> = { pending: '等待到期采样', running: '正在核验行情', completed: '行情已核验', retry: '行情核验待重试' }
const horizons: Record<string, string> = { '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时' }
const time = (value: string) => new Date(value).toLocaleString('zh-CN')

const harnessTerminal = new Set(['completed', 'degraded', 'research_only', 'failed', 'cancelled'])
const harnessStates: Record<string, string> = { admitted: '等待研究', queued: '等待研究', running: '研究进行中', completed: '研究结果已保存', degraded: '研究已保存，存在限制', research_only: '研究已保存，存在限制', failed: '研究未完成', cancelled: '停止请求已登记' }
const intentKey = 'decision-hub:harness-comparison-intent:v1'
const intentSchema = z.object({ request_id: z.string().min(1).max(256), catalog_ref: z.string().min(1).max(256), candidate_ref: z.string().min(1).max(256), source_run_id: z.string().min(1).max(256), artifact_sha256: z.string().regex(/^[a-f0-9]{64}$/), cancel_request_id: z.string().min(1).max(256).optional() }).strict()
type HarnessIntent = z.infer<typeof intentSchema>
function readHarnessIntent(): { intent: HarnessIntent | null; blocked: boolean } {
  try { const raw = sessionStorage.getItem(intentKey); return { intent: raw ? intentSchema.parse(JSON.parse(raw)) : null, blocked: false } } catch { return { intent: null, blocked: true } }
}
function matchesIntent(view: HarnessComparisonView, intent: HarnessIntent) {
  return view.request_id === intent.request_id && view.catalog_ref === intent.catalog_ref && view.candidate_ref === intent.candidate_ref && view.source_run_id === intent.source_run_id && view.candidate.artifact_sha256 === intent.artifact_sha256
}
function comparisonReason(code: string | null) {
  const reasons: Record<string, string> = {
    harness_owner_session_required: '请先验证本机所有者身份。', local_owner_session_rejected: '所有者会话已失效，请重新验证。',
    harness_comparison_receipt_mismatch: '返回的记录与本次请求不一致，尚未确认结果。',
    harness_comparison_catalog_unavailable: '正式研究方法对照尚未开放。', harness_source_rights_unverified: '当前资料使用权限未通过核验。',
    harness_frozen_original_missing: '原研究材料不完整，暂不能作为对照输入。', harness_frozen_pit_violation: '材料超出原研究时点，暂不能用于本次对照。',
    harness_comparison_request_conflict: '本次请求已绑定另一组输入，请先核对原回执。',
    harness_intent_storage_unavailable: '无法保存原请求信息，当前未提交研究。',
    harness_cost_accounting_unavailable: '当前研究服务尚不能核验完整费用，暂不能开始新的正式方法对照；已提交组的报告和记录仍可查阅。',
    harness_frozen_cost_unverified_or_exceeded: '研究费用尚未通过核验或超过上限，本组对照未完成。',
  }
  return code ? reasons[code] ?? '当前条件尚未通过核验，具体原因保留在技术记录中。' : '当前没有可核验的原因记录。'
}
function isHarnessOwnerAuthenticationError(failure: unknown) {
  const code = failure instanceof Error ? failure.message : typeof failure === 'string' ? failure : null
  return code !== null && ['local_owner_session_rejected', 'local_owner_session_invalid', 'harness_owner_session_required'].includes(code)
}
function comparisonReportText(value: string) {
  return researchDisplayText(value).replace(/(?<![A-Za-z0-9_./:-])(crypto_spot_confirmation|derivatives_crowding)(?![A-Za-z0-9_./:-])/g, label => requirementNames[label] ?? label)
}
function reportHref(runId: string) {
  return `#/report?${new URLSearchParams({ run: runId, back: window.location.hash })}`
}
function HarnessArmReport({ arm, stale }: { arm: HarnessComparisonView['baseline']; stale: boolean }) {
  const [recordsOpen, setRecordsOpen] = useState(false)
  const [rawOpen, setRawOpen] = useState(false)
  let parsed: ReturnType<typeof researchSessionResultSchema.safeParse> | null = null
  if (arm.result_json !== null) {
    try { parsed = researchSessionResultSchema.safeParse(JSON.parse(arm.result_json)) } catch { /* Raw bytes remain available in the technical record. */ }
  }
  const result = parsed?.success ? parsed.data : null
  const terminal = harnessTerminal.has(arm.status)
  const savedCostFailure = arm.error_code === 'harness_frozen_cost_unverified_or_exceeded' && result !== null
  return <article className="product-card" aria-label={arm.arm === 'baseline' ? '基线方法研究' : '候选方法研究'}>
    <h4>{arm.arm === 'baseline' ? '基线方法' : '候选方法'} · {arm.label}</h4>
    <p>{savedCostFailure ? '报告已保存，对照未完成' : harnessStates[arm.status] ?? '研究状态待核对'}{stale ? '（上次读取的快照）' : ''}</p>
    <p>{stale ? '执行凭据待重新核对。' : arm.proof_status === 'verified' ? '根研究请求的执行凭据已核验。' : arm.proof_status === 'blocked' ? '执行凭据未通过核验。' : '执行凭据尚未完整返回。'}</p>
    {arm.error_code && <p role="status">{savedCostFailure && result.estimated_cost_usd === null ? '报告已保存，研究费用尚未核验，本组对照未完成。' : comparisonReason(arm.error_code)}</p>}
    {arm.status === 'cancelled' && <p>等待执行端停止回执；已产生费用仍需结算。任务状态已登记，不能据此确认所有执行资源均已释放。</p>}
    {!terminal && <p>本次研究尚未完成，暂不能比较结论。</p>}
    {terminal && !result && <p>{arm.result_json === null ? '尚未保存完整报告；缺失结果不计作方法成功或零分。' : '报告内容未通过格式核验，请查看原记录。'}</p>}
    {terminal && result && <>
      <p>{result.causal_case ? comparisonReportText(result.causal_case.thesis) : '暂未形成完整因果论据。'}</p>
      <p>已采纳依据 {result.evidence_candidates.filter(item => item.quality === 'accepted').length} 条 · 已采纳事实 {result.facts.filter(item => item.quality === 'accepted').length} 条</p>
      <ul>{result.horizons.map(item => <li key={item.horizon}>{horizons[item.horizon] ?? '时间窗口待核对'}：{directions[item.action] ?? '方向待核对'}。触发条件：{comparisonReportText(item.trigger)}；失效条件：{comparisonReportText(item.invalidation)}</li>)}</ul>
      <p>状态与限制：{result.stop_reason.code === 'no_actionable_path' ? technicalSummaryName(result.stop_reason.code) : comparisonReportText(result.stop_reason.detail)}</p>
    </>}
    <details onToggle={event => { if (event.target === event.currentTarget) setRecordsOpen(event.currentTarget.open) }}>
      <summary>查看这份新研究的完整记录</summary>
      {recordsOpen && <>
        {result && <>
          <h5>原报告的依据</h5>
          <p>保留全部依据及采纳状态；候选、过时或冲突材料不等于已经采纳。</p>
          <ol>{result.evidence_candidates.map(item => <li key={item.evidence_id}>
            <p>{({ accepted: '已采纳', candidate: '候选，尚未采纳', rejected: '未采纳', stale: '已过时', conflicted: '存在冲突' } as Record<string, string>)[item.quality] ?? '采纳状态待核对'}：{researchDisplayText(item.excerpt)}</p>
            {item.source_url && /^https?:\/\//i.test(item.source_url) && <a href={item.source_url} target="_blank" rel="noopener noreferrer">查看原始来源</a>}
          </li>)}</ol>
          {!!result.causal_case?.unresolved_questions.length && <><h5>尚未解决的问题</h5><ul>{result.causal_case.unresolved_questions.map(item => <li key={item}>{comparisonReportText(item)}</li>)}</ul></>}
        </>}
        <h5>本次执行记录</h5>
        {arm.traces.length === 0 ? <p>尚未保存可读取的执行记录。</p> : <ol>{arm.traces.map(item => <li key={item.trace_id}>{time(item.created_at)} · {researchDisplayText(item.summary)}</li>)}</ol>}
        <details onToggle={event => { if (event.target === event.currentTarget) setRawOpen(event.currentTarget.open) }}><summary>原始报告与执行凭据</summary>{rawOpen && <pre style={{ overflow: 'auto', maxHeight: 360 }}>{JSON.stringify(arm, null, 2)}</pre>}</details>
      </>}
    </details>
  </article>
}
function HarnessComparisonPanel() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [sourceRef, setSourceRef] = useState('')
  const [methodRef, setMethodRef] = useState('')
  const [pairingToken, setPairingToken] = useState('')
  const [ownerSession, setOwnerSession] = useState<HarnessOwnerSession | null>(null)
  const [pairing, setPairing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const [initialIntent] = useState(readHarnessIntent)
  const [intent, setIntent] = useState<HarnessIntent | null>(initialIntent.intent)
  const [submitted, setSubmitted] = useState<HarnessComparisonView | null>(null)
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const catalog = useQuery({ queryKey: ['harness-comparison-catalog'], queryFn: ({ signal }) => assets.harnessComparisonCatalog(signal), enabled: open, retry: false, refetchOnWindowFocus: false })
  const source = catalog.data?.sources.find(item => item.catalog_ref === (sourceRef || catalog.data?.sources[0]?.catalog_ref))
  const method = catalog.data?.methods.find(item => item.candidate_ref === (methodRef || catalog.data?.methods[0]?.candidate_ref))
  const canRead = catalog.data?.owner_authenticated === true || ownerSession !== null
  const restore = useQuery({ queryKey: ['harness-comparison-request', intent?.request_id], queryFn: ({ signal }) => assets.harnessComparisonByRequest(intent!.request_id, signal), enabled: open && canRead && intent !== null, retry: false, refetchOnWindowFocus: false })
  const restoredView = restore.data && intent && matchesIntent(restore.data, intent) ? restore.data : null
  const assignmentId = submitted?.assignment_id || restoredView?.assignment_id || ''
  const detail = useQuery({ queryKey: ['harness-comparison', assignmentId], queryFn: ({ signal }) => assets.harnessComparison(assignmentId, signal), enabled: open && canRead && !!assignmentId, retry: false, refetchOnWindowFocus: false,
    refetchInterval: query => open && query.state.data && ![query.state.data.baseline, query.state.data.candidate].every(arm => harnessTerminal.has(arm.status)) ? 5000 : false,
  })
  const rawView = detail.data ?? submitted ?? restore.data ?? null
  const view = rawView && intent && matchesIntent(rawView, intent) ? rawView : null
  const ownerAuthenticationRejected = [catalog.error, restore.error, detail.error, error].some(isHarnessOwnerAuthenticationError)
  const stale = detail.isError || restore.isError || catalog.isError || ownerAuthenticationRejected
  const canStart = open && !initialIntent.blocked && !intent && !submitting && catalog.data?.available === true && !!source && !!method && !!ownerSession && Date.parse(ownerSession.expires_at) > Date.now() && !catalog.isError && !ownerAuthenticationRejected
  useEffect(() => {
    if (!ownerSession) return
    const duration = Math.max(0, Date.parse(ownerSession.expires_at) - Date.now())
    const timer = setTimeout(() => setOwnerSession(null), Math.min(duration, 2_147_483_647))
    return () => clearTimeout(timer)
  }, [ownerSession])
  async function pairOwner() {
    if (!pairingToken.trim() || pairing) return
    setPairing(true); setError(null)
    try {
      setOwnerSession(await assets.harnessOwnerSession(pairingToken))
      await catalog.refetch({ cancelRefetch: false })
      // Recheck only the saved request after authentication; never resubmit it.
      if (intent) await restore.refetch({ cancelRefetch: false })
      if (assignmentId) await detail.refetch({ cancelRefetch: false })
    }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'harness_owner_session_required') }
    finally { setPairingToken(''); setPairing(false) }
  }
  async function startComparison() {
    if (!canStart || submittingRef.current || !source || !method || !ownerSession) return
    submittingRef.current = true; setSubmitting(true); setError(null)
    const next: HarnessIntent = { request_id: `harness-comparison:${crypto.randomUUID()}`, catalog_ref: source.catalog_ref, candidate_ref: method.candidate_ref, source_run_id: source.source_run_id, artifact_sha256: method.artifact_sha256 }
    try {
      // Save only the intent before POST. No cookie, token, Result or proof is stored.
      try { sessionStorage.setItem(intentKey, JSON.stringify(next)) } catch { throw new Error('harness_intent_storage_unavailable') }
      setIntent(next)
      const response = await assets.harnessComparisonStart({ request_id: next.request_id, catalog_ref: next.catalog_ref, candidate_ref: next.candidate_ref }, ownerSession.csrf_token)
      if (!matchesIntent(response, next)) throw new Error('harness_comparison_receipt_mismatch')
      setSubmitted(response)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'harness_comparison_receipt_mismatch') }
    finally { submittingRef.current = false; setSubmitting(false) }
  }
  async function refreshReceipt() {
    if (intent) await restore.refetch({ cancelRefetch: false })
    if (assignmentId) await detail.refetch({ cancelRefetch: false })
  }
  async function stopComparison() {
    if (!view || !intent || intent.cancel_request_id || ownerAuthenticationRejected || !ownerSession || Date.parse(ownerSession.expires_at) <= Date.now() || submittingRef.current || [view.baseline, view.candidate].every(arm => harnessTerminal.has(arm.status))) return
    submittingRef.current = true; setSubmitting(true); setError(null)
    const next = { ...intent, cancel_request_id: `harness-comparison-stop:${crypto.randomUUID()}` }
    try {
      try { sessionStorage.setItem(intentKey, JSON.stringify(next)) } catch { throw new Error('harness_intent_storage_unavailable') }
      setIntent(next)
      const response = await assets.harnessComparisonCancel(view.assignment_id, next.cancel_request_id, ownerSession.csrf_token)
      if (!matchesIntent(response, next) || response.baseline.run_id !== view.baseline.run_id || response.candidate.run_id !== view.candidate.run_id) throw new Error('harness_comparison_receipt_mismatch')
      setSubmitted(response)
      queryClient.setQueryData(['harness-comparison', response.assignment_id], response)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'harness_comparison_receipt_mismatch') }
    finally { submittingRef.current = false; setSubmitting(false) }
  }
  function chooseAnother() {
    if (!view || ![view.baseline, view.candidate].every(arm => harnessTerminal.has(arm.status))) return
    try { sessionStorage.removeItem(intentKey) } catch { setError('harness_comparison_receipt_mismatch'); return }
    setIntent(null); setSubmitted(null); setError(null)
  }
  return <details onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open) }}>
    <summary>正式研究方法对照</summary>
    {open && <section aria-label="正式研究方法对照">
      <h3>用同一份原时点材料，比较两种研究方法</h3>
      <p>两种方法会各自生成一份新研究。来源研究和自动复查计划保持原状；资料权限仍需核验。</p>
      <p>这里只比较研究结果与执行记录，不会自动批准或部署候选方法。</p>
      {catalog.isLoading && <p role="status">正在读取可用研究和方法…</p>}
      {catalog.isError && <p role="alert">可用输入读取失败，当前不能开始对照。保留的内容是上次快照。</p>}
      {catalog.data && !catalog.data.available && <p role="status">{comparisonReason(catalog.data.blocked_reason)}</p>}
      {catalog.data?.available && (!source || !method) && <p role="status">当前没有可用于正式对照的来源研究或候选方法。</p>}
      {initialIntent.blocked && <p role="alert">原对照请求信息无法核对，已保留原记录。当前不会另开研究，请先由本机所有者核对。</p>}
      {catalog.data && <>
        <label>来源研究 <select aria-label="方法对照来源研究" value={source?.catalog_ref ?? ''} disabled={!!intent || submitting} onChange={event => setSourceRef(event.target.value)}>
          {catalog.data.sources.map(item => <option key={item.catalog_ref} value={item.catalog_ref}>{item.event_title} · {time(item.pit_cutoff_at)}</option>)}
        </select></label>
        <label>候选方法 <select aria-label="方法对照候选方法" value={method?.candidate_ref ?? ''} disabled={!!intent || submitting} onChange={event => setMethodRef(event.target.value)}>
          {catalog.data.methods.map(item => <option key={item.candidate_ref} value={item.candidate_ref}>{item.label}</option>)}
        </select></label>
        {source && <p>材料截至 {time(source.pit_cutoff_at)}；固定依据 {source.evidence_count} 条、事实 {source.fact_count} 条。缺失材料不会用当前行情补齐。</p>}
        <p>费用上限：每份 ${catalog.data.budget.per_arm_max_cost_usd.toFixed(2)}，合计 ${catalog.data.budget.total_max_cost_usd.toFixed(2)}；每份研究开始后最长 {Math.ceil(catalog.data.budget.total_deadline_seconds / 60)} 分钟，等待执行时间另计。</p>
      </>}
      {(!ownerSession || ownerAuthenticationRejected) && <div>
        <p>{ownerAuthenticationRejected ? '所有者会话已失效或未通过验证。请重新验证身份后，读取本次原回执；不会重新提交研究。' : canRead ? '当前会话可读取回执；开始新的对照需再次验证所有者身份。' : '手动对照需要本机所有者身份。验证口令由本机可信启动配置提供。'}</p>
        <label>本机验证口令 <input aria-label="方法对照本机验证口令" type="password" autoComplete="off" maxLength={2048} value={pairingToken} onChange={event => setPairingToken(event.target.value)} /></label>
        <button className="button secondary" disabled={pairing || !pairingToken.trim()} onClick={() => void pairOwner()}>{pairing ? '正在验证…' : '验证所有者身份'}</button>
      </div>}
      {ownerSession && !ownerAuthenticationRejected && <p>所有者身份已验证，有效至 {time(ownerSession.expires_at)}。</p>}
      <button className="button primary" disabled={!canStart} onClick={() => void startComparison()}>{submitting ? '正在提交两份新研究…' : '开始两份新研究对照'}</button>
      {intent && !view && <p role="status">尚未确认原请求回执。请核对原请求；当前不会重新提交研究。</p>}
      {(error || (rawView && !view)) && <p role="alert">{comparisonReason(rawView && !view ? 'harness_comparison_receipt_mismatch' : error)}</p>}
      {intent && <button className="button secondary" disabled={!canRead || ownerAuthenticationRejected || restore.isFetching || detail.isFetching || submitting} onClick={() => void refreshReceipt()}>核对原请求回执</button>}
      {(restore.isError || detail.isError) && <p role="alert">{ownerAuthenticationRejected ? '回执读取被身份验证拒绝。请先重新验证所有者身份，再核对原请求；上次内容只是快照。' : '回执读取失败；上次内容不能代表当前执行状态，请继续核对原请求。'}</p>}
      {view && <>
        <p>共同材料截至 {time(view.pit_cutoff_at)}。<a href={reportHref(view.source_run_id)}>查看来源研究</a></p>
        <div className="product-monitor"><HarnessArmReport arm={view.baseline} stale={stale} /><HarnessArmReport arm={view.candidate} stale={stale} /></div>
        <button className="button secondary" disabled={submitting || ownerAuthenticationRejected || !ownerSession || Date.parse(ownerSession.expires_at) <= Date.now() || !!intent?.cancel_request_id || [view.baseline, view.candidate].every(arm => harnessTerminal.has(arm.status))} onClick={() => void stopComparison()}>停止这组对照</button>
        {intent?.cancel_request_id && ![view.baseline, view.candidate].every(arm => harnessTerminal.has(arm.status)) && <p role="status">停止请求已保留，尚未核对两份研究的状态。请核对原请求回执；当前不会重复发送停止请求。</p>}
        <p>候选方法尚未获得部署资格。这里的执行凭据只覆盖根研究请求。</p>
        <details onToggle={event => { if (event.target === event.currentTarget) setTechnicalOpen(event.currentTarget.open) }}><summary>技术记录：共同输入、执行凭据与原始报告</summary>{technicalOpen && <pre style={{ overflow: 'auto', maxHeight: 360 }}>{JSON.stringify(view, null, 2)}</pre>}</details>
        <button className="button secondary" disabled={![view.baseline, view.candidate].every(arm => harnessTerminal.has(arm.status))} onClick={chooseAnother}>保留记录，选择另一组对照</button>
      </>}
      {error && <details><summary>查看本次未完成原因的原始代码</summary><pre>{error}</pre></details>}
    </section>}
  </details>
}

function Receipt({ id }: { id: string }) {
  const [open, setOpen] = useState(false)
  const receipt = useQuery({ queryKey: ['prospective-receipt', id], queryFn: () => assets.prospectivePairReceipt(id), enabled: open, retry: false })
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>技术凭据：输入、预测与原始行情</summary>
    {receipt.isLoading && <p role="status">正在核对凭据…</p>}
    {receipt.isError && <p role="alert">凭据读取或校验失败，不能使用上次内容作为本次核验结果。</p>}
    {receipt.data && !receipt.isError && <pre style={{ overflow: 'auto', maxHeight: 360 }}>{JSON.stringify(receipt.data, null, 2)}</pre>}
  </details>
}

export function ProspectiveReceiptsPanel() {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState('')
  const [offset, setOffset] = useState(0)
  const trials = useQuery({ queryKey: ['prospective-trials'], queryFn: assets.prospectiveTrials, enabled: open, retry: false })
  const trialId = selected || trials.data?.[0]?.trial_id || ''
  const trial = trials.data?.find(item => item.trial_id === trialId)
  const pairs = useQuery({ queryKey: ['prospective-pairs', trialId, offset], queryFn: () => assets.prospectivePairs(trialId, offset), enabled: open && !!trialId, retry: false })
  return <><details onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open) }}>
    <summary>查看逐条比较与行情凭据</summary>
    {open && <section aria-label="比较样本与行情核验">
      <div className="product-card-head"><h3>比较样本与行情核验</h3><button className="button secondary" disabled={trials.isFetching || pairs.isFetching} onClick={() => { void trials.refetch(); if (trialId) void pairs.refetch(); void client.invalidateQueries({ queryKey: ['prospective-receipt'] }) }}>刷新样本记录</button></div>
      {trials.isLoading && <p role="status">正在读取登记记录…</p>}
      {(trials.isError || pairs.isError) && <p role="alert">当前记录读取失败。下方保留的数据是上次快照，不能代表当前状态。</p>}
      {trials.data?.length === 0 && <p>尚未登记基线与候选比较。已有研究观察仍可从“研究效果”查看。</p>}
      {!!trials.data?.length && <label>比较批次 <select aria-label="比较批次" value={trialId} onChange={event => { setSelected(event.target.value); setOffset(0) }}>
        {trials.data.map(item => <option key={item.trial_id} value={item.trial_id}>{item.instrument} · {horizons[item.horizon] ?? item.horizon} · {time(item.registered_at)}</option>)}
      </select></label>}
      {trial && <p>预登记取样窗口：{time(trial.starts_at)} 至 {time(trial.ends_at)}。预测和行情分别核验，单条结果不能证明方法有效。</p>}
      {pairs.isLoading && !!trialId && <p role="status">正在读取比较样本…</p>}
      {pairs.data?.items.length === 0 && <p>这一批次尚未接收到可记录的新课题输入。</p>}
      {pairs.data?.items.map(item => <article className="product-card" key={item.pair_id}>
        <h4>{time(item.admitted_at)} · {states[item.status] ?? '状态待核对'}</h4>
        <p>共同行情窗口：{time(item.window_start_at)} 至 {time(item.window_end_at)}</p>
        {item.exclusion_reason && <p>这组输入未进入有效比较，已保留排除依据；后来的成功不会替换它。</p>}
        <div className="product-monitor">{item.arms.map(arm => <div key={arm.arm}><span>{arm.arm === 'baseline' ? '基线方法' : '候选方法'}</span><strong>{arm.direction ? directions[arm.direction] ?? '方向待核对' : states[arm.status] ?? '状态待核对'}</strong><small>{states[arm.status] ?? '状态待核对'}</small></div>)}</div>
        <p>行情状态：{item.label ? labelStates[item.label.status] ?? '状态待核对' : '尚未安排行情核验'}</p>
        {item.label && item.label.status !== 'completed' && <p>最早下次检查：{time(item.label.next_attempt_at)}。未到期或失败不计作零收益。</p>}
        {item.label?.result && <>
          <p>共同市场变化：{item.label.result.market_return_pct.toFixed(4)}%；采用分钟收盘估算，不是实际交易盈亏。</p>
          <ul>{Object.entries(item.label.result.scores).map(([arm, score]) => <li key={arm}>{arm === 'baseline' ? '基线' : '候选'}：{score.direction_correct === null ? '未计算方向命中（中性、弃权或调用未完整）' : score.direction_correct ? '本窗口方向与市场变化一致' : '本窗口未命中方向'}。</li>)}</ul>
        </>}
        <Receipt id={item.pair_id} />
      </article>)}
      {!!trialId && <div className="product-card-head"><button className="button secondary" disabled={!offset || pairs.isFetching} onClick={() => setOffset(value => Math.max(0, value - 10))}>上一页</button><span>第 {offset / 10 + 1} 页</span><button className="button secondary" disabled={!pairs.data?.has_more || pairs.isFetching} onClick={() => setOffset(value => value + 10)}>下一页</button></div>}
    </section>}
  </details><HarnessComparisonPanel /></>
}
