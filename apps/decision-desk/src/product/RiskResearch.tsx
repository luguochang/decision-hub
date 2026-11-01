import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { riskResearchCommandSchema, riskResearchRequestSchema, riskResearchStatusSchema } from '@decision-hub/contracts-ts'
import { ApiRequestError, request } from '../api/client'
import { riskRequestId, clearRiskRequestId, riskErrorMessage } from '../api/risk'
import { loadFollowup, readFollowup, researchOwner, saveFollowup, type ResearchFollowup } from './researchFollowup'

type Navigate = (scene: string, params?: Record<string, string | undefined>) => void
const names = { pending_admission: '等待数据与运行环境核验', claiming: '正在核验研究条件', queued: '研究已进入执行队列', completed: '关联研究执行已结束', failed: '本次研究未完成' }
const admissionReasons: Record<string, string> = {
  research_admission_budget_exhausted: '本次核验返回研究准入次数已用尽，恢复时间尚未确认。',
  study_research_active: '当前资产已有研究正在执行，需先等待原研究结束。',
  study_host_terminal_unknown: '上一项研究的运行结束状态尚未确认，需先核对原执行记录。',
  study_host_activity_refresh_required: '上一项研究尚缺完整、未过期的运行环境核验，当前不能开始新研究。系统需要重新采集真实运行状态；旧研究的结束记录保持原样。',
  risk_research_claim_lease_expired: '第三次研究条件核验未能在有效租约内完成，本次请求已结束，尚未进入研究执行。请核对运行环境后保存新的研究请求。',
  risk_research_admission_writer_busy: '保存研究执行记录前，数据库写入暂时繁忙；本次核验尚未创建研究任务。',
  spot_current_original_path_not_fresh: '用于本次核验的分钟原路径不够新，尚不能开始研究。请核对行情来源与记录时点。',
  study_not_active: '当前持续研究尚未启用，需先恢复研究配置。',
  spot_effective_preparer_not_configured: '现货研究的正常准备入口尚未配置，需先完成运行环境安装。',
}

export function RiskResearch({ scopeId, onNavigate, title = '当前资产研究请求', actionLabel = '更新当前资产研究', primaryAction = false }: {
  scopeId: string; onNavigate: Navigate; title?: string; actionLabel?: string; primaryAction?: boolean
}) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<{ scopeId: string; requestId: string; message: string } | null>(null)
  const [followup, setFollowup] = useState<ResearchFollowup | null>(() => loadFollowup(scopeId))
  const activeScope = useRef(scopeId)
  activeScope.current = scopeId
  useEffect(() => { setReceipt(null); setError(null); setFollowup(loadFollowup(scopeId)) }, [scopeId])
  const query = useQuery({ queryKey: ['risk-research', scopeId], enabled: Boolean(scopeId), retry: false, refetchInterval: 30_000, queryFn: async ({ signal }) => {
    const view = riskResearchStatusSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/research`, { signal }))
    if (view.scope_id !== scopeId || (view.request && view.request.scope_id !== scopeId)) throw new Error('研究返回的资产范围无法核对。')
    return view
  } })
  const followed = followup?.scopeId === scopeId && followup.ownerId === researchOwner ? followup : null
  const own = useQuery({ queryKey: ['risk-research-owner-command', researchOwner, scopeId, followed?.commandId],
    enabled: Boolean(followed), retry: false, refetchInterval: 30_000,
    queryFn: ({ signal }) => readFollowup(followed!, signal) })
  useEffect(() => {
    if (!followed || followed.canonicalRequestId !== null || !own.data) return
    const verified = { ...followed, canonicalRequestId: own.data.request.request_id }
    try { saveFollowup(verified); setFollowup(verified) }
    catch { setError('本次回执已核对，浏览器保存失败；刷新后需再次读取同一原回执。') }
  }, [followed, own.data])
  const state = followed ? own.data?.request : query.data?.request
  const automatic = followed ? own.data?.next_automatic_request : null
  const statusError = query.isError || Boolean(followed && own.isError)
  const missingPendingIntent = Boolean(followed && followed.canonicalRequestId === null && own.error instanceof ApiRequestError && own.error.kind === 'http' && own.error.status === 404)
  const availableAt = state ? Date.parse(state.available_at) : NaN
  const checkedAt = own.data && followed ? Date.parse(own.data.as_of) : query.data ? Date.parse(query.data.as_of) : NaN
  const waitingUntil = !statusError && state?.status === 'pending_admission' && Number.isFinite(availableAt) && Number.isFinite(checkedAt) && availableAt > checkedAt
  const earliestTime = waitingUntil ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(new Date(availableAt)) : null
  const hasRun = Boolean(state?.run_id)
  const observationUnavailable = hasRun && state?.error_code === 'research_observation_unavailable'
  const runFailed = hasRun && state?.run_status === 'failed'
  const runCancelled = hasRun && state?.run_status === 'cancelled'
  const requestUnsettled = state?.status === 'pending_admission' || state?.status === 'claiming'
    || (state?.status === 'queued' && !runFailed && !runCancelled)
  const workerFailed = hasRun && state?.error_code === 'research_worker_failed'
  const executionIssue = hasRun && (observationUnavailable || runFailed || runCancelled || state?.status === 'failed')
  const admissionBlocked = !hasRun && state && (state.status === 'pending_admission' || state.status === 'failed') && state.error_code
  const reason = admissionBlocked ? admissionReasons[state.error_code!] : undefined
  const budgetBlocked = admissionBlocked && state.error_code === 'research_admission_budget_exhausted'
  const stateTitle = observationUnavailable ? '研究已进入执行，结果状态暂不可读取'
    : runCancelled ? '研究已进入执行，当前执行已取消'
    : runFailed || workerFailed ? '研究已进入执行，本次执行失败'
    : hasRun && state?.status === 'failed' ? '研究已进入执行，本次结果尚未确认'
    : hasRun && state?.status === 'queued' && state.run_status === 'running' ? '研究正在执行'
    : state?.status === 'failed'
    ? budgetBlocked && !state.run_id ? '研究准入次数已用尽，本次请求未进入研究' : names.failed
    : state?.status === 'pending_admission' && budgetBlocked
      ? '研究请求已保存，准入次数暂不足，尚未开始研究'
      : waitingUntil ? '研究请求已保存，等待计划处理' : state ? names[state.status] : ''
  async function refresh(retryCommandId?: string) {
    if (retryCommandId !== undefined) {
      if (!missingPendingIntent || followed?.commandId !== retryCommandId || !query.data?.enabled || query.isError || busy) return
    } else if (!query.data?.enabled || statusError || busy || requestUnsettled || (followed && !own.data)) return
    setBusy(true); setError(null); setReceipt(null)
    const key = `research:${scopeId}`
    const requestId = retryCommandId ?? riskRequestId(key)
    try {
      const intent: ResearchFollowup = { version: 1, ownerId: researchOwner, scopeId, commandId: requestId, canonicalRequestId: null }
      saveFollowup(intent)
      if (activeScope.current === scopeId) setFollowup(intent)
      const view = riskResearchRequestSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/research`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId, 'X-Owner-Id': import.meta.env.VITE_OWNER_ID?.trim() || 'owner' }, body: JSON.stringify(riskResearchCommandSchema.parse({ request_id: requestId })) }))
      if (view.scope_id !== scopeId) throw new Error('研究请求的资产范围无法核对。')
      const accepted = { ...intent, canonicalRequestId: view.request_id }
      saveFollowup(accepted)
      if (activeScope.current === scopeId) setFollowup(accepted)
      clearRiskRequestId(key)
      const message = view.status === 'pending_admission'
        ? `已收到本次更新请求，提交时已${query.data?.request?.request_id === view.request_id ? '合并' : '加入'}到等待队列。提交已保存；下方持续核对这次原请求。`
        : view.status === 'claiming' ? '已收到本次更新请求，提交时正在核验研究条件；当前进度请以下方请求记录为准。'
        : view.status === 'queued' && view.run_id ? '已收到本次更新请求，提交时研究已进入执行队列；当前进度请以下方请求记录为准。'
        : view.status === 'completed' && view.run_id ? '已收到本次更新请求，关联研究已执行结束；结果是否可用请核对原报告。'
        : '已收到本次更新请求，请以下方请求记录核对处理状态与限制。'
      if (activeScope.current === scopeId) setReceipt({ scopeId: view.scope_id, requestId: view.request_id, message })
      await client.invalidateQueries({ queryKey: ['risk-research', scopeId] })
      await client.invalidateQueries({ queryKey: ['risk-research-owner-command', researchOwner, scopeId, requestId] })
    } catch (e) { if (activeScope.current === scopeId) setError(riskErrorMessage(e)) }
    finally { setBusy(false) }
  }
  return <section className="product-card" aria-label="当前资产研究请求">
    <div className="product-card-head"><h2>{followed ? '我本次的研究' : title}</h2><span className="risk-badge">{statusError ? '状态未确认' : query.data?.enabled ? '自动调度已启用' : query.isLoading ? '读取中' : '自动调度尚未启用'}</span></div>
    <p>{followed ? '这里持续核对您这次操作保存的同一项研究；较新的自动队列单独显示。' : '这里显示当前资产最新的研究请求，可能由自动计划、告警或手动更新产生。'}自动调度启用状态独立显示；启用不代表当前请求已经执行或生成报告。</p>
    {receipt?.scopeId === scopeId && <p role="status" data-request-id={receipt.requestId}>{receipt.message}</p>}
    {state && <p><strong>{stateTitle}</strong>{state.status === 'completed' && '；执行结束不代表报告已保存或确认，可用性仍需核对原报告。'}</p>}
    {requestUnsettled && <p className="product-muted">当前请求尚未结束，无需重复提交；进度会自动更新。</p>}
    {executionIssue && <p role="alert">{observationUnavailable ? '本次结果状态读取失败，不能据此确认已完成报告。' : runCancelled ? '原执行记录已取消，当前没有已确认的本次结果。' : runFailed || workerFailed ? '执行记录显示本次研究失败，当前尚未确认可用结果。' : '本次请求记录为未完成，具体执行或读取原因需核对原记录。'}{observationUnavailable && runFailed && '已读取的执行状态记录为失败；结果原件仍需独立核对。'}请点击“查看研究执行记录”，核对这次研究的原因与可用恢复操作；读取或重试能否成功以实际回执为准。</p>}
    {reason && <p role={state?.status === 'failed' ? 'alert' : 'status'}>{reason}{state?.status === 'failed' ? '本次请求已结束；核对原因与恢复条件后，可更新当前资产研究。计划时间到达不代表此请求会自动恢复。' : '请求仍已保存，系统将按计划再次核验；无需重复点击。'}</p>}
    {admissionBlocked && ['study_host_terminal_unknown', 'study_host_activity_refresh_required', 'risk_research_claim_lease_expired', 'risk_research_admission_writer_busy', 'spot_current_original_path_not_fresh'].includes(state.error_code!) && state.status === 'failed' && <p className="product-muted">这次请求的三次核验已结束，系统不会自动发起第四次。点击“更新当前资产研究”会保存一项新的请求，并重新核验真实运行条件；保存请求不代表研究已经开始或报告已经生成。</p>}
    {admissionBlocked && !reason && <p>准入核验尚未通过，具体原因保留在“触发与失败记录”。{state?.status === 'failed' ? '本次请求已结束，请先核对原记录与恢复条件。' : '请求仍已保存，尚未确认可以开始研究。'}</p>}
    {waitingUntil && <p className="product-muted">最早再次检查时间：北京时间 {earliestTime}；按本次状态检查约还需 {Math.ceil((availableAt - checkedAt) / 60_000)} 分钟。此时间是计划检查时间，不保证准入次数恢复或研究开始；到时仍需核验数据与运行条件。</p>}
    {!state && followed && <p role="status">正在核对本次研究的原操作回执；读取不能代替重新提交。</p>}
    {!state && !followed && !query.isLoading && <p>{query.data?.enabled ? '尚无已保存的研究请求，自动调度会核验数据后启动。' : '行情监测可以独立运行；自动研究待正常安装后开放。'}</p>}
    {statusError && <p role="alert">{followed ? '本次研究回执暂时无法核对；保留原操作身份，不用最新自动队列替换。' : '自动研究状态暂时无法读取，当前是否已执行未确认。'}{missingPendingIntent && '服务端当前未查到本次操作，您可以沿原操作标识重试保存；不会新建另一项操作。'}</p>}
    <div className="risk-actions"><button type="button" className={`button ${primaryAction ? 'primary' : 'secondary'}`} disabled={busy || requestUnsettled || !query.data?.enabled || statusError || Boolean(followed && !own.data)} onClick={() => { void refresh() }}>{busy ? '正在保存研究请求…' : actionLabel}</button>
      {state?.run_id && <button className="button secondary" type="button" onClick={() => onNavigate(state.status === 'completed' || runFailed || runCancelled ? 'report' : 'progress', { run: state.run_id!, back: '#/home' })}>{state.status === 'completed' ? '阅读研究结果与限制' : '查看研究执行记录'}</button>}
      {statusError && <button type="button" className="button secondary" onClick={() => { void query.refetch(); if (followed) void own.refetch() }}>重新读取研究状态</button>}
      {missingPendingIntent && <button type="button" className="button secondary" disabled={busy || !query.data?.enabled || query.isError} onClick={() => { void refresh(followed!.commandId) }}>重试保存本次请求</button>}</div>
    {error && <p role="alert">{error}</p>}
    {state && <details className="risk-audit"><summary>触发与失败记录</summary><p>请求：{state.request_id} · 核验次数：{state.attempts}/3</p><p>本次触发依据：{state.trigger_refs.join('、') || '无已保存依据'}</p>{state.error_code && <p>失败记录：<code>{state.error_code}</code></p>}</details>}
    {automatic && <section aria-label="下次自动研究计划"><h3>下次自动研究计划</h3><p>这是另一个自动请求，不是上方本次点击的结果。</p><p>{names[automatic.status]}{automatic.run_status === 'running' ? '，正在执行' : ''}</p><p>最早再次检查时间：北京时间 {new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(automatic.available_at))}；到时仍需核验条件，不保证开始研究。</p><details><summary>自动计划记录</summary><p>{automatic.request_id}</p></details></section>}
  </section>
}
