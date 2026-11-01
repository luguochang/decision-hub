import { useEffect, useRef, useState } from 'react'
import { researchSessionResultSchema } from '@decision-hub/contracts-ts'
import { apiUrl } from '../api/client'
import { researchQualityApi, researchQualityReadErrorText, ResearchReportServingError, type ProviderConversationUsageCensus, type ResearchQualityView, type ResearchReportServingStatus, type ResearchQualityServingRead, type ResearchQualityFullAudit, type ReportRecoveryStatus } from '../api/researchQuality'
import { qualityEvidenceText, qualityGapText, qualityReportText, qualityStopText } from './qualityReportLanguage'
import { researchReadingExportReady, renderResearchReadingMarkdown, researchWindowExpiry, researchUnresolvedScopeText } from './researchReadingMarkdown'
import { ReportRecoveryCommand, PreparationRecoveryCommand } from './ReportRecoveryCommand'
import { SavedConfirmationRecovery } from './SavedConfirmationRecovery'
import { isSavedConfirmationReason, type SavedConfirmationReceipt } from '../api/savedConfirmation'
import { reportAuditApi, readAuditIntent, type AuditIntent, type AuditState } from '../api/reportAudit'

const statuses = { eligible: '具备研究资格', blocked: '被关键问题阻断', unknown: '尚未核验', supported: '已核验支持', complete: '完整', incomplete: '尚有缺口', untrusted: '未通过核验' }
const directions = { long: '看涨研究假说', short: '看跌研究假说', neutral: '中性研究假说', no_trade: '暂不判断方向' }
const horizons = { '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时' }
const times = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

// Schedule only the next original expiry; no interval, requests or permission renewal.
export function useResearchWindowClock(expiresAt: readonly (string | null | undefined)[], enabled = true): number {
  const [revision, setRevision] = useState(0)
  const expiryKey = JSON.stringify(expiresAt)
  const now = Date.now()
  useEffect(() => {
    if (!enabled) return
    const refresh = () => setRevision(value => value + 1)
    const currentNow = Date.now()
    const values: unknown = JSON.parse(expiryKey)
    const future = (Array.isArray(values) ? values : []).map(value => researchWindowExpiry(value, currentNow))
      .filter(item => item.status === 'not_expired' && item.expiresUtc !== null)
      .map(item => Date.parse(item.expiresUtc!))
    const remaining = future.length ? Math.max(1, Math.min(...future) - currentNow + 1) : null
    const timer = remaining !== null && remaining <= 2_147_483_647 ? window.setTimeout(refresh, remaining) : null
    window.addEventListener('focus', refresh)
    return () => { if (timer !== null) window.clearTimeout(timer); window.removeEventListener('focus', refresh) }
  }, [expiryKey, revision, enabled])
  return now
}

function servingText(status: ResearchReportServingStatus): { title: string; detail: string } {
  switch (status.state) {
    case 'queued': return { title: '报告正在排队整理', detail: '研究结果已生成，后台正在整理并核验完整报告。系统会更新整理状态，也可重试读取。' }
    case 'prepared': return { title: '报告已整理，正在确认', detail: '保存后的独立核对尚未完成，确认后才能读取和下载完整原报告。' }
    case 'confirmed': return { title: '报告已确认', detail: '最新状态已确认。请重试读取，取得本次报告后再下载。' }
    case 'cancelled': return { title: '本次报告整理已取消', detail: (
      status.reason_code === 'publication_execution_compute_budget_exceeded' ? '报告整理超过本次计算时限，系统已取消这次整理的发布资格。'
      : status.reason_code === 'publication_execution_total_budget_exceeded' ? '报告整理超过本次总执行时限，系统已取消这次整理的发布资格。'
      : status.reason_code === 'publication_execution_stop_requested' ? '收到停止请求，系统已取消这次整理的发布资格。'
      : '原取消记录未保存详细停止原因，当前无法确认具体原因。'
    ) + '原研究记录仍保留，当前没有已确认的报告读取材料。取消发布资格不能据此确认执行线程已经退出。重试读取仅核对最新状态。' }
    case 'revoked': return { title: '这份报告当前不可读取', detail: '报告读取许可已撤销，暂不提供下载。重试读取仅核对最新状态。' }
    case 'unconfirmed': return status.reason_code === 'publication_bundle_commit_unconfirmed'
      ? { title: '报告保存回执尚未确认', detail: '系统正在保留现场供核对，暂不提供下载。重试读取不会重新制作或重复保存。' }
      : status.reason_code === 'publication_execution_compute_budget_exceeded'
      ? { title: '报告整理已达到本次计算时限', detail: '已保存的材料保留，独立确认尚未完成，当前不提供完整读取或下载。重试读取只核对状态；是否可继续确认这份已保存报告，须由本人显式核对补确认条件。' }
      : status.reason_code === 'publication_full_frozen_original_changed'
      ? { title: '报告回放核验未通过', detail: '原报告回放的严格一致性检查未通过，后台已停止本次整理。原研究记录和已保存报告保留，当前不能完整读取或下载报告。重试读取只核对状态；如需继续确认，须先核对补确认条件并显式提交，不会自动重试。' }
      : status.reason_code === 'frozen_io_root_data_fallback'
      ? { title: '报告数据核验未通过', detail: '完整报告整理时的数据读取未通过核验，系统已停止本次整理。原研究记录仍保留；当前不能读取或下载已确认的完整报告。重试读取只核对状态，不会重新整理报告。' }
      : status.reason_code === 'research_graph_recipe_v3_selection_version_mismatch'
      ? { title: '报告整理已停止', detail: '这次研究使用的流程版本与完整报告整理器不匹配，后台已停止整理。原研究结论、证据和过程记录仍保留；重试读取只核对状态，不会继续整理。需要当前操作方案时，请从当前资产决策入口重新发起研究。' }
      : status.reason_code === 'publication_bundle_preparation_failed'
      ? { title: '报告整理失败', detail: '后台整理已停止，具体失败阶段尚待核对。原研究与报告记录仍保留，当前不提供完整读取或下载。重试读取只查看最新状态，不会重新整理报告。' }
      : { title: '报告整理尚未完成', detail: status.reason_code === 'research_usage_terminal_result_changed'
        ? '执行结果与调用用量记录尚未核对一致，当前不能确认完整报告。原研究记录仍保留。'
        : '当前没有已确认的完整报告，具体原因尚待核对。原研究记录仍保留；重试读取只查看最新状态，不会重新整理报告。' }
    case 'legacy_full_original': return { title: '正在读取历史报告', detail: '历史原报告仍按完整核对路径读取。' }
  }
}

function qualityIssueText(value: string) {
  if (value.startsWith('horizon_contract_invalid:')) return '研究时间窗口的输出未通过完整性核验，具体缺口保留在原报告。'
  return qualityReportText(value.replace('[legacy resolution lacks structured current-result impact proof]', '〔旧处理记录缺少对本次结论影响的结构化证明〕'))
}

function parseReport(raw?: string) {
  try {
    const parsed = researchSessionResultSchema.safeParse(raw ? JSON.parse(raw) : null)
    return parsed.success ? parsed.data : null
  } catch { return null }
}

function OriginalText({ summary, value }: { summary: string; value: string }) {
  const [open, setOpen] = useState(false)
  return <details data-quality-original onToggle={event => setOpen(event.currentTarget.open)}><summary>{summary}</summary>{open && <pre>{value}</pre>}</details>
}

function EvidenceText({ excerpt }: { excerpt: string }) {
  const presented = qualityEvidenceText(excerpt)
  return <><p>{presented.text}</p><OriginalText summary={presented.structured ? '查看结构化原文' : '查看依据原文'} value={excerpt} /></>
}

function ConversationUsage({ usage }: { usage: ProviderConversationUsageCensus | null | undefined }) {
  if (!usage) return <p>这份报告没有已保存的对话用量对账记录，实际 Token 和费用保持未知。</p>
  const known = usage.coverage_status === 'complete' && usage.token_status === 'known'
  return <>
    <p>范围仅含本次研究的对话调用，不含上下文压缩和标题生成。</p>
    <dl className="product-definition-list">
      <dt>原件覆盖</dt><dd>{usage.coverage_status === 'complete' ? '完整' : '用量尚不完整'}：已核对 {usage.covered_dispatches} / {usage.expected_dispatches ?? '未知'} 次调用</dd>
      <dt>已记录调用</dt><dd>主研究 {usage.root_dispatches} 次，协作研究 {usage.child_dispatches} 次</dd>
      <dt>对话 Token 总量</dt><dd>{known ? usage.total_tokens : '未知'}</dd>
      {known && <><dt>输入 / 输出 Token</dt><dd>{usage.input_tokens} / {usage.output_tokens}</dd></>}
      <dt>费用</dt><dd>未知：缺少可信定价凭据</dd>
    </dl>
    {usage.coverage_status === 'incomplete' && <p>仍有调用或用量原件缺失{usage.missing_sessions.length > 0 ? `，涉及 ${usage.missing_sessions.length} 个会话` : ''}。已知子集不能作为本次研究总用量。</p>}
    <p>用量记录不能证明模型已理解全部上下文，也不授予预算或交易资格。</p>
  </>
}

export function ResearchQuality({ runId, visible = true, readRevision = 0, onOpenReport }: { runId: string; visible?: boolean; readRevision?: number; onOpenReport?: () => void }) {
  const [view, setView] = useState<ResearchQualityView | null>(null)
  const [serving, setServing] = useState<ResearchReportServingStatus | null>(null)
  const [servingRead, setServingRead] = useState<ResearchQualityServingRead | null>(null)
  const [recovery, setRecovery] = useState<ReportRecoveryStatus | null>(null)
  const [recoveryLoading, setRecoveryLoading] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [cancelled, setCancelled] = useState(false)
  const [revision, setRevision] = useState(0)
  const [audit, setAudit] = useState<ResearchQualityFullAudit | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditState, setAuditState] = useState<AuditState | null>(null)
  const [auditError, setAuditError] = useState<string | null>(null)
  const [readingExportError, setReadingExportError] = useState<string | null>(null)
  const version = useRef(0)
  const activeRead = useRef<AbortController | null>(null)
  const activeAudit = useRef<AbortController | null>(null)
  const auditVersion = useRef(0)
  const auditGate = useRef({ runId, visible }); auditGate.current = { runId, visible }
  const activeRecovery = useRef<AbortController | null>(null)
  const pendingReads = useRef(0)
  const acceptedSavedConfirmation = useRef<SavedConfirmationReceipt | null>(null)
  useEffect(() => { acceptedSavedConfirmation.current = null }, [runId])
  useEffect(() => { pendingReads.current = 0 }, [runId, visible, readRevision])
  useEffect(() => {
    // Report reads refresh independently; only Run/visibility changes end audit observation.
    auditVersion.current += 1
    activeAudit.current?.abort(); activeAudit.current = null
    setAudit(null); setAuditState(null); setAuditError(null); setAuditLoading(false)
    return () => {
      auditVersion.current += 1
      activeAudit.current?.abort(); activeAudit.current = null
    }
  }, [runId, visible])
  useEffect(() => {
    const current = ++version.current
    const controller = new AbortController()
    activeRead.current = controller
    setReadingExportError(null)
    setView(null); setServing(null); setServingRead(null); setError(null); setCancelled(false); setLoading(visible)
    if (visible) void researchQualityApi.read(runId, controller.signal, { preferSnapshot: true, onServingRead: metadata => {
      if (version.current === current && !controller.signal.aborted && metadata.run_id === runId) setServingRead(metadata)
    } }).then(result => {
      if (version.current === current) setView(result)
    }).catch(error => {
      if (version.current === current && !controller.signal.aborted) {
        if (error instanceof ResearchReportServingError) setServing(error.servingStatus)
        else setError(researchQualityReadErrorText(error))
      }
    }).finally(() => { if (version.current === current) { setLoading(false); activeRead.current = null } })
    return () => { version.current += 1; controller.abort(); activeRecovery.current?.abort(); activeRecovery.current = null; if (activeRead.current === controller) activeRead.current = null }
  }, [runId, visible, revision, readRevision])
  useEffect(() => {
    // Read-only, bounded status updates. Terminal/error/hidden views stop here;
    // no timer creates a research or publication recovery command.
    if (!visible || loading || cancelled || error || serving?.run_id !== runId
      || (!['queued', 'prepared'].includes(serving.state) && !(serving.state === 'unconfirmed'
        && isSavedConfirmationReason(serving.reason_code)
        && acceptedSavedConfirmation.current?.run_id === runId
        && serving.state_version === acceptedSavedConfirmation.current.result.authorized_state_version))
      || pendingReads.current >= 30) return
    const timer = window.setTimeout(() => {
      pendingReads.current += 1
      setRevision(value => value + 1)
    }, 10_000)
    return () => window.clearTimeout(timer)
  }, [runId, visible, loading, cancelled, error, serving])
  useEffect(() => {
    setRecovery(null)
    setRecoveryLoading(false); setRecoveryError(null)
    activeRecovery.current?.abort(); activeRecovery.current = null
  }, [runId, visible, serving])
  const checkRecovery = () => {
    if (!visible || serving?.run_id !== runId || (serving.state !== 'cancelled' && !(serving.state === 'unconfirmed' && serving.reason_code === 'frozen_io_root_data_fallback')) || activeRecovery.current) return
    const current = version.current
    const controller = new AbortController(); activeRecovery.current = controller
    setRecovery(null); setRecoveryError(null); setRecoveryLoading(true)
    void researchQualityApi.recoveryStatus(runId, serving.state_version, controller.signal).then(value => {
      if (!controller.signal.aborted && version.current === current && value.run_id === runId
        && value.state_version === serving.state_version && value.state === serving.state
        && value.reason_code === serving.reason_code) setRecovery(value)
    }).catch(() => {
      if (!controller.signal.aborted && version.current === current) setRecoveryError('恢复条件尚未核对成功，原记录保留；当前不能据此重试整理。')
    }).finally(() => {
      if (activeRecovery.current === controller) { activeRecovery.current = null; setRecoveryLoading(false) }
    })
  }
  const stopWaiting = () => {
    version.current += 1
    activeRead.current?.abort(); activeRead.current = null
    setLoading(false); setServing(null); setServingRead(null); setError(null); setCancelled(true)
  }
  const quality = view?.status === 'available' && view.run_id === runId && view.decision?.run_id === runId ? view.decision : null
  const observeAudit = async (intent: AuditIntent, controller: AbortController, current: number) => {
    const live = () => auditVersion.current === current && auditGate.current.runId === intent.expected_head.run_id
      && auditGate.current.visible && !controller.signal.aborted
    for (let read = 0; read < 30 && live(); read += 1) {
      const state = await reportAuditApi.status(intent, controller.signal)
      if (!live()) return
      if (state === null) throw new Error('尚未找到原完整核验回执，原请求键保留。可显式核对并按原请求重试，系统不会自动提交。')
      setAuditState(state)
      if (state.audit_confirmed) {
        const result = await reportAuditApi.result(intent, controller.signal)
        if (live()) setAudit(result)
        return
      }
      if (state.state === 'failed' || state.state === 'unknown') throw new Error(state.native_exited
        ? '本次完整核验未通过，原结果和时间已保存；不会自动重试。'
        : '原核验进程尚未确认退出或服务已重启，结果待核对；当前不能启动另一核验。')
      await new Promise<void>(resolve => {
        const finish = () => { window.clearTimeout(timer); controller.signal.removeEventListener('abort', finish); resolve() }
        const timer = window.setTimeout(finish, 10_000)
        controller.signal.addEventListener('abort', finish, { once: true })
        if (controller.signal.aborted) finish()
      })
    }
    if (live()) throw new Error('已停止本轮有界状态等待，原请求和核验记录保留。可继续读取原回执；不会重新运行核验。')
  }
  const auditOriginal = () => {
    if (!visible || !quality || activeAudit.current) return
    const current = auditVersion.current
    const controller = new AbortController(); activeAudit.current = controller
    setAudit(null); setAuditState(null); setAuditError(null); setAuditLoading(true)
    if (servingRead?.read_mode === 'legacy_full_original') {
      // Preserve the existing explicit legacy reader; no synthetic Saved head.
      void researchQualityApi.audit(runId, controller.signal).then(result => {
        if (auditVersion.current === current && auditGate.current.runId === runId && auditGate.current.visible && !controller.signal.aborted) setAudit(result)
      }).catch(error => {
        if (auditVersion.current === current && auditGate.current.runId === runId && auditGate.current.visible && !controller.signal.aborted) setAuditError(researchQualityReadErrorText(error))
      }).finally(() => {
        if (activeAudit.current === controller) { activeAudit.current = null; setAuditLoading(false) }
      })
      return
    }
    void reportAuditApi.begin(runId, controller.signal).then(intent => observeAudit(intent, controller, current)).catch(error => {
      if (auditVersion.current === current && auditGate.current.runId === runId && auditGate.current.visible && !controller.signal.aborted) setAuditError(error instanceof Error
        ? error.message : '完整核验尚未确认，原请求保留；受理不代表核验通过。')
    }).finally(() => {
      if (activeAudit.current === controller) { activeAudit.current = null; setAuditLoading(false) }
    })
  }
  useEffect(() => {
    // Returning to the report reads the retained key only; zero automatic POST.
    if (!visible) return
    let intent: AuditIntent | null
    try { intent = readAuditIntent(runId) } catch { setAuditError('原完整核验请求无法读取，请保留现场；未发送新请求。'); return }
    if (!intent || activeAudit.current) return
    const current = auditVersion.current
    const controller = new AbortController(); activeAudit.current = controller
    setAuditLoading(true); setAuditError(null)
    void observeAudit(intent, controller, current).catch(error => {
      if (auditVersion.current === current && auditGate.current.runId === runId && auditGate.current.visible && !controller.signal.aborted) setAuditError(error instanceof Error ? error.message : '原核验结果尚未确认。')
    }).finally(() => {
      if (activeAudit.current === controller) { activeAudit.current = null; setAuditLoading(false) }
    })
    return () => { controller.abort(); if (activeAudit.current === controller) activeAudit.current = null }
  }, [runId, visible])
  const report = parseReport(quality?.full_report_json)
  const causal = report?.causal_case
  const originalUnresolved = new Set(causal?.unresolved_questions ?? [])
  const repeatedQualityUnknowns = quality?.unknowns.filter(item => originalUnresolved.has(item)).length ?? 0
  const browserNow = useResearchWindowClock(report?.horizons.map(item => item.expires_at) ?? [], visible)
  const readingInput = { runId, view, servingRead, report }
  const downloadReading = () => {
    setReadingExportError(null)
    if (!researchReadingExportReady(readingInput)) return
    let url: string | null = null
    try {
      const markdown = renderResearchReadingMarkdown(readingInput)
      url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url; link.download = `research-${runId}-reading.md`
      document.body.appendChild(link); link.click(); link.remove()
    } catch { setReadingExportError('阅读版尚未导出成功；原报告和资格保持不变。') }
    finally { if (url) { const downloadUrl = url; window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000) } }
  }
  if (!visible) return null
  return <section className="research-section research-quality" aria-label="研究质量与限制">
    <h3>这份研究能支持什么</h3>
    {loading && <p role="status">正在读取并核对质量原件，可能需要一段时间。可以停止等待并继续阅读报告。<button className="product-link" onClick={stopWaiting}>停止等待</button></p>}
    {cancelled && <p role="status">已停止等待，质量核验结果尚未确认。已保存报告仍可阅读。<button className="product-link" onClick={() => setRevision(value => value + 1)}>重新读取</button></p>}
    {error && <p role="alert">{error}<button className="product-link" onClick={() => setRevision(value => value + 1)}>重新读取</button></p>}
    {serving && <div role="status" className="product-callout"><strong>{servingText(serving).title}</strong><p>{servingText(serving).detail}</p><button className="button secondary" onClick={() => setRevision(value => value + 1)}>重试读取</button></div>}
    {(serving?.state === 'cancelled' || serving?.state === 'unconfirmed' && serving.reason_code === 'frozen_io_root_data_fallback') && <div className="product-callout">
      <button className="button secondary" disabled={recoveryLoading} onClick={checkRecovery}>核对恢复条件</button>
      {recoveryLoading && <p role="status">正在核对原任务的读取能力；此操作不会启动研究或重新整理报告。</p>}
      {recoveryError && <p role="alert">{recoveryError}</p>}
      {recovery?.action === 'new_research_required' ? <p>这份研究绑定旧版读取能力，不能直接重试原任务的报告整理。可从原研究的复核入口发起关联的新研究；这会新建任务并重新调用模型，原记录保留。</p>
        : recovery?.action === 'owner_request_required' ? <p>{recovery.state === 'unconfirmed' ? '原失败细节不完整，可请求重新整理这次研究的报告。系统仍会核对原任务、来源、历史与保存材料；请求被接受不代表报告已保存。' : '已核对当前读取能力。原任务恢复仍需正常拥有者入口重新核对全部来源、历史和保存材料；此项状态检查不授予恢复或下载资格。'}</p>
        : recovery?.action === 'review_required' ? <p>保存材料、提交回执或来源尚待核对。系统保留原现场，当前不能重试整理或下载。</p>
        : <p>恢复条件尚未核对，系统不会自动重试。重新读取只更新状态，不会重新整理报告或调用模型。</p>}
      {onOpenReport && <button className="button secondary" onClick={onOpenReport}>查看原研究及复核入口</button>}
    </div>}
    <ReportRecoveryCommand runId={runId} status={recovery} onAccepted={() => setRevision(value => value + 1)} />
    <PreparationRecoveryCommand runId={runId} stateVersion={serving?.run_id === runId ? serving.state_version : null}
      allowRecovery={serving?.run_id === runId && serving.state === 'unconfirmed' && serving.reason_code === 'frozen_io_undeclared_authorization_resource'}
      onAccepted={() => { pendingReads.current = 0; setRevision(value => value + 1) }} />
    <SavedConfirmationRecovery runId={runId}
      stateVersion={serving?.run_id === runId ? serving.state_version : null}
      allowAuthorization={serving?.run_id === runId && serving.state === 'unconfirmed' && isSavedConfirmationReason(serving.reason_code)}
      reportConfirmed={serving?.run_id === runId && serving.state === 'confirmed'
        || servingRead?.run_id === runId && (servingRead.read_mode === 'confirmed_report_snapshot' || servingRead.read_mode === 'confirmed_bundle')}
      onAccepted={receipt => {
        if (receipt.run_id !== runId || acceptedSavedConfirmation.current?.request_id === receipt.request_id) return
        acceptedSavedConfirmation.current = receipt; pendingReads.current = 0
        setRevision(value => value + 1)
      }} />
    {view?.status === 'unavailable' && <p>{view.reason_code === 'research_quality_publication_pending' ? '研究尚未发布。进行中的质量检查不代表最终结论。' : '这份历史研究没有发布新版质量记录，暂不能补认当前资格。'}</p>}
    {quality && <>
      <p role="status">{servingRead?.run_id === runId && servingRead.read_mode === 'confirmed_report_snapshot'
        ? '已读取确认保存的报告' : servingRead?.run_id === runId && servingRead.read_mode === 'confirmed_bundle'
        ? '读取已确认保存材料' : servingRead?.run_id === runId && servingRead.read_mode === 'legacy_full_original'
          ? '完整历史原件核对' : '读取方式尚未确认，不能据此区分保存材料读取与历史原件核对。'}</p>
      {servingRead?.run_id === runId && <details><summary>本次读取核验</summary><p>核验时间：{times(servingRead.checked_at)}</p><p>状态版本：{servingRead.state_version ?? '尚未确认'}</p>{servingRead.read_mode === 'confirmed_report_snapshot' && <p>报告已在保存时完成核验并独立确认。本次读取核对当前许可和报告内容，未重新完整审计材料。</p>}</details>}
      <section aria-label="本报告窗口到期状态">
        <p>以下为本次报告的原窗口；确认保存或再次读取不会刷新证据、重置到期时间或确认当前预测资格。按浏览器参考 UTC：{new Date(browserNow).toISOString()}。</p>
        {report?.horizons.length ? <ul>{report.horizons.map(item => { const expiry = researchWindowExpiry(item.expires_at, browserNow); return <li key={item.horizon}><strong>{horizons[item.horizon]}：{expiry.label}</strong><p>{expiry.detail} 原到期 UTC：{expiry.expiresUtc ?? '无法核对'}。</p></li> })}</ul> : <p>完整窗口字段尚未确认，无法判断各窗口是否到期；不能据此视为当前有效预测。</p>}
      </section>
      <button className="button secondary" disabled={auditLoading} onClick={auditOriginal}>完整原库核对</button>
      <p>{servingRead?.read_mode === 'legacy_full_original' ? '这份历史报告沿用显式完整原件读取；没有保存报告核验任务或可取回的异步回执。' : '点击后显式受理完整原库核验，关键时间与结果持久保存。受理回执不代表核验通过；返回此页只读取原请求。'}不会重新研究、制作报告或改变研究资格。</p>
      {auditLoading && <p role="status">正在读取完整核验的持久状态，请稍候。<button className="product-link" onClick={() => {
        activeAudit.current?.abort(); activeAudit.current = null; setAuditLoading(false); setAudit(null); setAuditError('已停止界面等待，后台原核验不会据此取消；原请求和时间保留，可继续读取原结果。')
      }}>停止等待核对</button></p>}
      {auditState?.receipt.request.expected_head.run_id === runId && <p role="status">{auditState.audit_confirmed ? '本次完整核验已独立确认。' : '原核验请求已受理，完整结果尚未确认。'}
        开始：{auditState.started_at ? times(auditState.started_at) : '尚未开始'}；结束：{auditState.finished_at ? times(auditState.finished_at) : '尚未结束'}。
        原生调用退出：{auditState.native_exited ? '已确认' : '尚未确认'}。</p>}
      {auditError && <p role="alert">{auditError}<button className="product-link" disabled={auditLoading} onClick={() => {
        let intent: AuditIntent | null
        try { intent = readAuditIntent(runId) } catch { setAuditError('原完整核验请求无法读取，请保留现场；未发送新请求。'); return }
        if (!intent || activeAudit.current) return
        const current = auditVersion.current
        const controller = new AbortController(); activeAudit.current = controller
        setAuditLoading(true); setAuditError(null)
        void observeAudit(intent, controller, current).catch(error => {
          if (auditVersion.current === current && auditGate.current.runId === runId && auditGate.current.visible && !controller.signal.aborted) setAuditError(error instanceof Error ? error.message : '原核验结果尚未确认。')
        }).finally(() => { if (activeAudit.current === controller) { activeAudit.current = null; setAuditLoading(false) } })
      }}>读取原核验结果</button></p>}
      {audit?.view.run_id === runId && <details><summary>本次完整原库核对结果</summary><p>{audit.comparison === 'saved_bundle_full_quality'
        ? '完整原库核对通过；本次公开质量原件的全部字段与已确认保存材料一致，读取时间除外。'
        : '已完成完整历史原件核对；本报告没有已确认保存材料，未进行两者比较。'}</p><p>核验时间：{times(audit.checked_at)}；状态版本：{audit.state_version}。</p><p>这项核对验证内容与读取状态，不确认预测表现或交易资格。</p></details>}
      <p>{quality.report_status === 'untrusted' || view?.gate_status === 'reject' ? '本次综合研究未通过核验；目前保留的是取证材料和未完成的分析记录，不能据此判断市场方向或杠杆安全。' : quality.publication_mode === 'complete' ? '研究质量门已通过，交易执行仍需独立核验。' : '已保存本次研究记录；关键缺口仍在，不能据此确认预测有效或杠杆安全。'}</p>
      <dl className="product-definition-list">
        <dt>事实依据</dt><dd>{statuses[quality.factual_status]}</dd>
        <dt>方向判断</dt><dd>{statuses[quality.direction_status]}</dd>
        <dt>途中急跌急涨风险</dt><dd>{statuses[quality.path_risk_status]}</dd>
        <dt>执行支持</dt><dd>{statuses[quality.execution_status]}</dd>
        <dt>上下文与报告</dt><dd>{statuses[quality.context_status]} / {statuses[quality.report_status]}</dd>
      </dl>
      <a className="button secondary" href={apiUrl(`/v1/research/runs/${encodeURIComponent(runId)}/report/${servingRead?.run_id === runId && servingRead.read_mode === 'confirmed_report_snapshot' ? 'snapshot/download' : 'download'}`)} download={`research-${runId}.json`}>下载完整原报告</a>
      <p>下载该次任务实际保存的完整 JSON 原件，包含已记录的事实、引用和分析；分析未完成时，原件保留缺口与失败记录。下载时会核对当前可读状态和保存内容；浏览器保存完成后才能确认成功。下载不更改研究资格。</p>
      <button className="button secondary" disabled={!researchReadingExportReady(readingInput)} onClick={downloadReading}>下载阅读版（Markdown）</button>
      <p>阅读版使用本次已确认快照中完整的综合与三窗口分析，保留审查、缺口和研究资格限制；仅作阅读排版，不是新结论或完整原字节。尚未确认完整综合时不提供导出。</p>
      {readingExportError && <p role="alert">{readingExportError}</p>}
      <h4>关键反证和风险</h4>
      {quality.key_risks.length ? <ul>{quality.key_risks.map(item => <li key={item.risk_id}>{qualityReportText(item.statement)}{item.unknown_reason && <p>待核验：{qualityReportText(item.unknown_reason)}</p>}</li>)}</ul> : <p>尚未形成可核验的关键风险清单，此项仍有缺口。</p>}
      {report && <p>本次读取的原报告内保存 {report.reviews.length} 份审查记录；研究详情中的执行检查记录属于另一范围，不能据此补齐原报告的独立复核。</p>}
      {!!causal?.unresolved_questions.length && <><h4>原报告仍保留的未解问题（{causal.unresolved_questions.length} 项）</h4><ul>{causal.unresolved_questions.map((item, index) => <li key={index}>{qualityReportText(item)}</li>)}</ul></>}
      <h4>尚未解决的结构化挑战</h4>
      {quality.unresolved_challenge_ids.length ? <ul>{quality.challenges.filter(item => quality.unresolved_challenge_ids.includes(item.challenge_id)).map(item => <li key={item.challenge_id}>{qualityIssueText(item.reason || '反方问题尚无结构化回应。')}<p>{item.severity === 'critical' ? '关键问题' : item.severity === 'important' ? '重要问题' : '待完善问题'}；对方向与尾部风险的影响仍待核验。</p></li>)}</ul> : <p>{researchUnresolvedScopeText(causal ? causal.unresolved_questions.length : null)}</p>}
      {!!quality.unknowns.length && <details data-quality-unknowns-scope>
        <summary>质量核验缺口（{quality.unknowns.length} 条{report ? `；与上方原文相同 ${repeatedQualityUnknowns} 条` : '；重叠范围尚未核对'}）</summary>
        <p>以下保留全部质量核验记录。部分记录与上方问题相同，条数不代表独立问题数量；展开可查看全部内容。</p>
        <ul data-quality-unknowns>{quality.unknowns.map((item, index) => <li key={index}>{qualityIssueText(item)}</li>)}</ul>
        <OriginalText summary="查看全部缺口原文" value={JSON.stringify(quality.unknowns, null, 2)} />
      </details>}
      {!!quality.unmapped_inquiries.length && <details data-quality-unmapped><summary>尚无数据接入的问题（{quality.unmapped_inquiries.length} 项，含已列缺口）</summary><ul>{[...new Set(quality.unmapped_inquiries)].map((item, index) => <li key={index}>{qualityReportText(item)}</li>)}</ul><OriginalText summary="查看数据接入问题原文" value={JSON.stringify(quality.unmapped_inquiries, null, 2)} /></details>}
      <h4>研究预算与停止边界</h4>
      <p>准备上限不能作为实际消耗，也不能证明模型已读取全部上下文。</p>
      {view?.budget && <p>本次准备上限：取证 {view.budget.max_tool_calls} 次，协作研究 {view.budget.max_subagents} 个，证据轮次 {view.budget.max_evidence_rounds} 轮。截止：{times(view.budget.deadline_at)}。</p>}
      <h4>已认证对话调用用量</h4>
      <ConversationUsage usage={view?.usage} />
      {onOpenReport && <button className="button secondary" onClick={onOpenReport}>阅读已发布完整报告</button>}
      {report && <details><summary>核对质量门所依据的完整分析</summary>
        <p>以下说明当时的分析及限制，数据缺口使用中文名称，结构化原文可按需展开。主观判断仍未成为经验证的预测概率；完整原报告保持不变。</p>
        {causal ? <>
          <h4>研究主张</h4><p>{qualityReportText(causal.thesis)}</p>
          {[{ title: '主要作用机制', links: causal.main_chain }, { title: '反方作用机制', links: causal.opposite_chain }].map(({ title, links }) => <div key={title}><h4>{title}</h4><ol>{links.map(link => <li key={link.link_id}><p>{qualityReportText(link.statement)}</p><p>确认条件：{qualityReportText(link.confirmation)}</p><p>失效条件：{qualityReportText(link.invalidation)}</p></li>)}</ol></div>)}
          {!!causal.unresolved_questions.length && <><h4>原报告未解问题</h4><ul>{causal.unresolved_questions.map((item, index) => <li key={index}>{qualityReportText(item)}</li>)}</ul></>}
        </> : <p>本次尚未形成完整因果论据。</p>}
        <h4>各时间窗口的研究假说</h4><ul>{report.horizons.map(item => <li key={item.horizon}><p>{horizons[item.horizon]}：{directions[item.action]}</p><p>{researchWindowExpiry(item.expires_at, browserNow).detail} 原到期 UTC：{researchWindowExpiry(item.expires_at, browserNow).expiresUtc ?? '无法核对'}。</p><p>触发：{qualityReportText(item.trigger)}</p><p>失效：{qualityReportText(item.invalidation)}</p><p>主观判断尚不能作为路径风险概率；原报告建议复核时间（历史参考） {times(item.next_review_at)}。实际安排请查看后续复查。</p>{item.missing_facts.length > 0 && <ul>{item.missing_facts.map((fact, index) => <li key={index}>缺失：{qualityGapText(fact)}</li>)}</ul>}{item.confidence_cap_reason && <p>限制：{qualityReportText(item.confidence_cap_reason)}</p>}</li>)}</ul>
        <h4>全部取证材料</h4><ol>{report.evidence_candidates.map(item => <li key={item.evidence_id}><p>{item.quality === 'accepted' ? '已采纳' : '尚未采纳'}的依据</p><EvidenceText excerpt={item.excerpt} />{item.source_url && /^https?:\/\//i.test(item.source_url) && <a href={item.source_url} target="_blank" rel="noopener noreferrer">原始来源</a>}</li>)}</ol>
        <h4>停止原因</h4><p>{qualityStopText(report.stop_reason.code, report.stop_reason.remaining_hard_gaps, quality.report_status)}</p>
        <OriginalText summary="核对原始缺口与停止说明" value={JSON.stringify({ horizons: report.horizons, stop_reason: report.stop_reason }, null, 2)} />
      </details>}
      <details><summary>发布核验凭据</summary><p>发布于 {view?.published_at && times(view.published_at)}。质量记录与本次已发布报告绑定。</p><p>质量原件：<code>{view?.quality_sidecar_id}</code><br />报告内容：<code>{quality.full_report_hash}</code><br />发布内容：<code>{view?.publication_ref?.payload_hash}</code></p><OriginalText summary="查看完整原报告 JSON" value={quality.full_report_json} /></details>
    </>}
  </section>
}
