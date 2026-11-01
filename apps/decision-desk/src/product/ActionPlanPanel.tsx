import { ActionPaperPanel } from './ActionPaperPanel'
import { ActionMethodPanel } from './ActionMethodPanel'
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ACTION_OWNER, actionPlansApi, actionIntentKey, readActionIntent, saveActionIntent,
  actionErrorMessage, isDefinitivelyRejectedAction, type ActionBranch, type ActionHorizon, type ActionPosition,
  type ActionIntent, type DecisionUiCommandReceiptV1, type DecisionUiCommandV1,
  type DecisionUiCurrentViewV2,
} from '../api/actionPlans'
import { ApiRequestError } from '../api/client'
import { researchDisplayText } from './researchLanguage'
import { actionLabels as actions, actionTime as at, conditionDescription, probabilityText } from './actionPresentation'
import { ActionReviewPanel } from './ActionReviewPanel'
import { isActionHorizon, readActionHorizon, saveActionHorizon } from './actionHorizonPreference'
import './ActionPlanPanel.css'

type Navigate = (scene: string, params?: Record<string, string | undefined>) => void
type Condition = ActionBranch['revision']['entry'][number]
const horizons: Record<ActionHorizon, string> = { '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时' }
const positions: Record<ActionPosition, string> = { flat: '我目前空仓', long: '我已持有多仓', unknown: '仓位尚不确定' }
const states = { proposed: '待确认方案', armed: '等待条件', triggered: '条件已触发', completed: '方案已结束', expired: '方案已到期', invalidated: '方案已失效', superseded: '已有更新方案' }
const monitors = { active: '正在监控', paused: '已暂停', degraded: '数据受限', stopped: '已停止' }
const truth = { true: '条件成立', false: '条件未满足', unknown: '尚无法判断' }
const operations = { pause: '暂停监控', resume: '恢复监控', display: '显示回执', ack: '知悉回执' }
const terminal = new Set(['completed', 'expired', 'invalidated', 'superseded'])

export { conditionDescription } from './actionPresentation'
export const actionProbabilityText = (branch: ActionBranch) => probabilityText(branch.probability)

function Conditions({ title, items, branch }: { title: string; items: Condition[]; branch: ActionBranch }) {
  if (!items.length) return <section className="action-condition-group"><h4>{title}</h4><p>本方案未给出这类条件。</p></section>
  return <section className="action-condition-group"><h4>{title}</h4><ul>{items.map(item => {
    const result = branch.latest_evaluation?.per_condition.find(value => value.id === item.id)
    return <li key={item.id}>
      <strong>{conditionDescription(item.expression)}</strong>
      <p>{result ? result.status === 'error' ? '本次条件检查失败' : truth[result.truth] : '尚无条件检查记录'}
        {result && 'confirmation_count' in result ? ` · 连续确认 ${result.confirmation_count}/${item.min_fresh_samples} 次${'confirmed' in result && result.confirmed === true ? '，已确认' : '，尚未确认'}` : ` · 需连续 ${item.min_fresh_samples} 次新鲜观测确认`}</p>
      <small>每 {item.evaluation_seconds} 秒检查 · 数据最多可延迟 {item.max_age_seconds} 秒 · 有效至 {at(item.valid_until)}</small>
      <details><summary>原始条件与数据口径</summary><p>{researchDisplayText(item.original_text)}</p><ul>{item.input_requirements.map((input, index) => <li key={index}>{input.metric} · {input.source} · {input.unit}</li>)}</ul></details>
    </li>
  })}</ul></section>
}

function usePlanVisible(ref: React.RefObject<HTMLElement | null>) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    let intersects = false
    const update = () => setVisible(intersects && document.visibilityState === 'visible')
    const measure = () => { const box = element.getBoundingClientRect(); intersects = box.height > 0 && box.width > 0 && box.top < window.innerHeight && box.bottom > 0; update() }
    const observer = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver(entries => { intersects = entries.some(entry => entry.isIntersecting); update() }, { threshold: 0.1 }) : null
    observer?.observe(element); if (!observer) measure()
    document.addEventListener('visibilitychange', update)
    if (!observer) { window.addEventListener('scroll', measure); window.addEventListener('resize', measure) }
    return () => { observer?.disconnect(); document.removeEventListener('visibilitychange', update); window.removeEventListener('scroll', measure); window.removeEventListener('resize', measure) }
  }, [ref])
  return visible
}

function PlanDetail({ planId, revisionId, onClose }: { planId: string; revisionId: string; onClose: () => void }) {
  const [revision, setRevision] = useState(revisionId)
  const [cursor, setCursor] = useState<string | undefined>()
  const query = useQuery({ queryKey: ['action-plan-detail', planId, revision, cursor], queryFn: ({ signal }) => actionPlansApi.plan(planId, revision, cursor, signal), retry: false, refetchOnWindowFocus: false })
  const value = query.isError ? undefined : query.data
  const historical = value && value.branch.revision.revision_id !== value.branch.head.current_revision_id
  return <section className="action-detail" aria-label="方案版本与监测记录">
    <div className="action-heading"><h3>方案版本与监测记录</h3><button type="button" className="button secondary" onClick={onClose}>关闭记录</button></div>
    {query.isPending && <p role="status">正在读取这一版方案…</p>}
    {query.isError && <p role="alert">{actionErrorMessage(query.error)}<button type="button" className="product-link" onClick={() => void query.refetch()}>重新读取这一版</button></p>}
    {value && <>
      <p>{historical ? '历史版本，仅供对照' : '当前版本'} · 第 {value.branch.revision.revision_no} 版 · 发布于 {at(value.branch.revision.published_at)}</p>
      <p>{actions[value.branch.revision.action]}：{researchDisplayText(value.branch.revision.reason)}</p>
      <p>这一版概率：{actionProbabilityText(value.branch)}</p>
      <p>监控状态：{historical ? '此处为历史版本，不使用当前版本的监控状态' : monitors[value.branch.head.monitor_state]} · 有效至 {at(value.branch.revision.valid_until)}</p>
      <Conditions title="这一版的退出条件" items={value.branch.revision.exit} branch={value.branch} />
      <Conditions title="这一版的失效条件" items={value.branch.revision.invalidation} branch={value.branch} />
      {value.branch.revision.previous_id && <button type="button" className="button secondary" onClick={() => { setCursor(undefined); setRevision(value.branch.revision.previous_id!) }}>查看上一版</button>}
      {historical && <button type="button" className="button secondary" onClick={() => { setCursor(undefined); setRevision(value.branch.head.current_revision_id) }}>返回当前版本</button>}
      <h4>条件监测记录</h4>
      {value.evaluations.length ? <ol>{value.evaluations.map(item => <li key={item.id}>{at(item.evaluated_at)} · {item.status === 'error' ? '本次检查失败' : truth[item.truth]} · {states[item.state_after]}</li>)}</ol> : <p>这一版尚无监测记录。</p>}
      {value.next_cursor && <button type="button" className="button secondary" disabled={query.isFetching} onClick={() => setCursor(value.next_cursor!)}>查看更早的监测记录</button>}
      <details><summary>原始身份与依据</summary><pre>{JSON.stringify({ revision: value.branch.revision, opportunity: value.opportunity, evaluations: value.evaluations }, null, 2)}</pre></details>
    </>}
  </section>
}

function SelectedPlan({ branch, stale, busy, pending, receipt, onCommand, onNavigate, runId }: {
  branch: ActionBranch; stale: boolean; busy: boolean; pending: boolean;
  receipt: DecisionUiCommandReceiptV1 | null; onCommand: (operation: DecisionUiCommandV1['operation']) => Promise<void>;
  onNavigate: Navigate; runId?: string;
}) {
  const ref = useRef<HTMLElement | null>(null)
  const visible = usePlanVisible(ref)
  const displayAttempted = useRef(false)
  const [details, setDetails] = useState(false)
  const historical = branch.head.current_revision_id !== branch.revision.revision_id
  const expired = branch.expired || Date.parse(branch.revision.valid_until) <= Date.now()
  const reviewDue = !historical && !expired && branch.next_review_at !== null && Date.parse(branch.next_review_at) <= Date.now()
  const thisReceipt = receipt?.plan_id === branch.head.plan_id && receipt.response.head.current_revision_id === branch.revision.revision_id ? receipt : null
  const displayAt = branch.first_display_at ?? (thisReceipt?.operation === 'display' ? thisReceipt.accepted_at : null)
  const ackAt = branch.first_ack_at ?? (thisReceipt?.operation === 'ack' ? thisReceipt.accepted_at : null)
  const canCommand = !stale && !expired && !historical && !busy && !pending
  const canMonitor = canCommand && !terminal.has(branch.head.state) && ['armed', 'triggered'].includes(branch.head.state)
  useEffect(() => {
    if (!visible || !canCommand || displayAt || displayAttempted.current) return
    displayAttempted.current = true
    void onCommand('display')
  }, [visible, canCommand, displayAt, onCommand])
  return <article className={`action-selected ${expired ? 'action-expired' : ''}`} ref={ref} aria-label="所选仓位的行动方案">
    <div className="action-heading"><div><span className="action-eyebrow">{positions[branch.revision.position_context]}</span><h3>{actions[branch.revision.action]}</h3></div><span className="action-state">{historical ? '历史版本（已有更新）' : expired ? '方案已到期' : states[branch.head.state]}</span></div>
    <p className="action-reason">{researchDisplayText(branch.revision.reason)}</p>
    <div className="action-metrics"><div><span>条件概率</span><strong>{actionProbabilityText(branch)}</strong></div><div><span>监控状态</span><strong>{historical ? '此版本已被替代' : monitors[branch.head.monitor_state]}</strong></div><div><span>下次复核</span><strong>{historical ? '请查看当前版本' : reviewDue ? '已到复核时间' : at(branch.next_review_at)}</strong></div></div>
    {reviewDue && <p role="status" className="action-notice">原定复核时间为 {at(branch.next_review_at)}，方案仍在有效期内。请更新研究核对依据是否变化；条件监控是否运行，以监控状态为准。</p>}
    {historical && <p className="action-notice">这份原方案可继续阅读；当前监控状态属于更新版本，此处不记录新的阅读回执，也不操作监控。</p>}
    <p className="product-muted">{branch.revision.advice_qualification === 'qualified' ? '已通过当前方案的准入要求。' : branch.revision.advice_qualification === 'blocked' ? '当前依据未满足准入要求。' : '研究试用方案，实际效果仍需持续验证。'} {branch.revision.decision_status === 'no_edge' ? '当前未发现足够优势。' : branch.revision.decision_status === 'unknown' ? '当前判断仍存在缺口。' : branch.revision.decision_status === 'blocked' ? '当前操作条件不具备。' : ''}</p>
    <p className="product-muted">分析截点 {at(branch.revision.as_of)} · 发布 {at(branch.revision.published_at)} · 有效至 {at(branch.revision.valid_until)}</p>
    {branch.revision.size_policy.amount !== null && <p>测算情景金额：{branch.revision.size_policy.amount} {branch.revision.size_policy.unit ?? ''}。以方案中明确的仓位条件为准。</p>}
    {!historical && branch.head.monitor_state === 'degraded' && <p role="status" className="action-notice">数据有缺口，条件尚未得到完整确认；恢复监控也不会补造数据。</p>}
    <div className="action-conditions"><Conditions title="开始行动的条件" items={branch.revision.entry} branch={branch} /><Conditions title="退出条件" items={branch.revision.exit} branch={branch} /><Conditions title="判断失效条件" items={branch.revision.invalidation} branch={branch} /></div>
    <section className="action-mechanisms"><h4>为什么这样判断 · 主反依据</h4>
      {branch.causal_steps.length ? <ol>{branch.causal_steps.map((step, index) => <li key={index}><strong>{researchDisplayText(step.from_description)} → {researchDisplayText(step.to_description)}</strong><p>{researchDisplayText(step.hypothesis)}</p><span>支持依据 {step.support_refs.length} 项 · 反方依据 {step.counter_refs.length} 项</span><details><summary>查看依据引用</summary><p>支持：{step.support_refs.join('、') || '尚无独立引用'}</p><p>反方：{step.counter_refs.join('、') || '尚无独立引用'}</p></details></li>)}</ol> : <p>尚无可展示的完整主反传导链。</p>}
      <p>市场是否已计入：{({ priced_in: '已有定价依据', not_priced_in: '尚未充分计入', unknown: '暂无法判断' })[branch.priced_in_status]}</p>
      {!!branch.unresolved.length && <div className="action-notice"><strong>仍需核对</strong><ul>{branch.unresolved.map((item, index) => <li key={index}>{researchDisplayText(item)}</li>)}</ul></div>}
    </section>
    {runId && <ActionMethodPanel runId={runId} revisionId={branch.revision.revision_id} />}
    <div className="action-buttons">
      <button type="button" className="button secondary" disabled={!canMonitor || !['active', 'degraded', 'paused'].includes(branch.head.monitor_state)} onClick={() => void onCommand(branch.head.monitor_state === 'paused' ? 'resume' : 'pause')}>{branch.head.monitor_state === 'paused' ? '恢复此方案监控' : '暂停此方案监控'}</button>
      <button type="button" className="button primary" disabled={!canCommand || Boolean(ackAt)} onClick={() => void onCommand('ack')}>{ackAt ? '已确认知悉' : '我已了解此方案'}</button>
      <button type="button" className="button secondary" onClick={() => setDetails(value => !value)}>{details ? '收起版本与监测记录' : '查看版本与监测记录'}</button>
      {runId && <button type="button" className="product-link" onClick={() => onNavigate('report', { run: runId, back: '#/home' })}>阅读原研究报告</button>}
    </div>
    <p className="action-receipts">已显示：{at(displayAt)} · 已知悉：{at(ackAt)}。这两项只记录阅读，不代表成交。暂停影响这份共享方案的监控。</p>
    {details && <PlanDetail key={branch.revision.revision_id} planId={branch.head.plan_id} revisionId={branch.revision.revision_id} onClose={() => setDetails(false)} />}
  </article>
}

function StillTracking({ view }: { view: DecisionUiCurrentViewV2 }) {
  const [cursor, setCursor] = useState<string | undefined>()
  const [selected, setSelected] = useState<{ plan_id: string; revision_id: string } | null>(null)
  const query = useQuery({ queryKey: ['action-tracking', view.scope_id, view.horizon, cursor], queryFn: ({ signal }) => actionPlansApi.tracking(view.scope_id, view.horizon, cursor, signal), enabled: Boolean(cursor), retry: false, refetchOnWindowFocus: false })
  const page = cursor ? query.isError ? undefined : query.data : { count: view.still_tracking_count, items: view.still_tracking, next_cursor: view.tracking_next_cursor }
  if (!view.still_tracking_count && !cursor) return null
  return <section className="action-tracking" aria-label="仍在跟踪的方案"><h3>仍在跟踪的方案{page ? `（${page.count}）` : ''}</h3><p>这些方案曾触发条件，持续跟踪独立于新研究；触发不代表已经成交。</p>
    {cursor && query.isPending && <p role="status">正在读取跟踪方案…</p>}
    {cursor && query.isError && <p role="alert">跟踪记录读取失败，当前页已隐藏。<button type="button" className="product-link" onClick={() => void query.refetch()}>重新读取跟踪记录</button></p>}
    {page && <><ul>{page.items.map(item => <li key={item.plan_id}><div><strong>{actions[item.action]}</strong><span>{({ flat: '适用于空仓', long: '适用于已有多仓', unknown: '仓位条件尚不明确' })[item.position_context]} · {monitors[item.monitor_state]}</span><small>最近检查 {at(item.last_evaluated_at)} · {Date.parse(item.valid_until) <= Date.now() ? '已到期，等待监控收尾' : `有效至 ${at(item.valid_until)}`}</small></div><button type="button" className="button secondary" onClick={() => setSelected(item)}>查看跟踪方案</button></li>)}</ul>
      {page.next_cursor && <button type="button" className="button secondary" disabled={query.isFetching} onClick={() => setCursor(page.next_cursor!)}>下一页跟踪方案</button>}
      {cursor && <button type="button" className="button secondary" onClick={() => setCursor(undefined)}>返回第一页</button>}
    </>}
    {selected && <PlanDetail key={`${selected.plan_id}:${selected.revision_id}`} planId={selected.plan_id} revisionId={selected.revision_id} onClose={() => setSelected(null)} />}
  </section>
}

function HorizonPanel({ scopeId, horizon, onNavigate }: { scopeId: string; horizon: ActionHorizon; onNavigate: Navigate }) {
  const query = useQuery({ queryKey: ['action-current', ACTION_OWNER, scopeId, horizon], queryFn: ({ signal }) => actionPlansApi.current(scopeId, horizon, signal), retry: false, refetchInterval: 30_000, refetchIntervalInBackground: false, refetchOnWindowFocus: false })
  const [position, setPosition] = useState<ActionPosition | ''>('')
  const key = actionIntentKey(scopeId, horizon)
  const [restored] = useState(() => { try { return { intent: readActionIntent(key), error: false } } catch { return { intent: null, error: true } } })
  const [intent, setIntent] = useState<ActionIntent | null>(restored.intent)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [receipt, setReceipt] = useState<DecisionUiCommandReceiptV1 | null>(null)
  const [error, setError] = useState<string | null>(restored.error ? '无法核对本机原操作记录，尚未提交新操作。' : null)
  const [missingReceipt, setMissingReceipt] = useState(false)
  const [rejection, setRejection] = useState<{ request_id: string; reason: string } | null>(() => {
    try { const raw = sessionStorage.getItem(`${key}:last-rejected`); const value = raw ? JSON.parse(raw) : null; return value && typeof value.request_id === 'string' && typeof value.reason === 'string' ? value : null } catch { return null }
  })
  const value = query.isError ? undefined : query.data
  const branch = value?.branches.find(item => item.revision.position_context === position)
  const latest = value?.latest_opportunity
  const older = Boolean(latest && value?.decision && latest.id !== value.decision.opportunity_id)

  async function accepted(result: DecisionUiCommandReceiptV1) {
    // Receipt has already passed owner/request/plan/revision identity checks.
    sessionStorage.removeItem(key)
    setReceipt(result); setIntent(null); setError(null); setMissingReceipt(false)
    await query.refetch()
  }
  async function postOriginal(original: ActionIntent) {
    try { await accepted(await actionPlansApi.command(original)) }
    catch (failure) {
      if (!isDefinitivelyRejectedAction(failure)) throw failure
      // A committed receipt takes precedence even if a later retry was rejected.
      try { await accepted(await actionPlansApi.receipt(original)); return }
      catch (readFailure) {
        if (!(readFailure instanceof ApiRequestError && readFailure.status === 404)) throw readFailure
      }
      const record = { request_id: original.command.request_id, reason: failure.message, original }
      sessionStorage.setItem(`${key}:rejected:${record.request_id}`, JSON.stringify(record))
      sessionStorage.setItem(`${key}:last-rejected`, JSON.stringify(record))
      sessionStorage.removeItem(key)
      setRejection(record); setIntent(null); setMissingReceipt(false)
      setError(null)
      await query.refetch()
    }
  }
  async function restoreOriginal(retryPost = false) {
    if (!intent || busyRef.current) return
    busyRef.current = true; setBusy(true); setError(null)
    try {
      try { await accepted(await actionPlansApi.receipt(intent)) }
      catch (failure) {
        if (!(failure instanceof ApiRequestError && failure.status === 404)) throw failure
        setMissingReceipt(true)
        if (retryPost) await postOriginal(intent)
        else setError(actionErrorMessage(failure))
      }
    } catch (failure) { setError(actionErrorMessage(failure)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  useEffect(() => { if (restored.intent) void restoreOriginal() }, []) // Reads only the saved original key.

  async function command(operation: DecisionUiCommandV1['operation']) {
    if (!branch || !value || query.isError || intent || restored.error || busyRef.current || branch.expired
      || branch.head.current_revision_id !== branch.revision.revision_id) return
    const next: ActionIntent = { owner_id: ACTION_OWNER, plan_id: branch.head.plan_id, command: {
      request_id: `action-${crypto.randomUUID()}`, expected_revision_id: branch.revision.revision_id,
      expected_state_version: branch.head.state_version, operation, payload: {},
    } }
    busyRef.current = true; setBusy(true); setError(null)
    try {
      saveActionIntent(key, next); setIntent(next)
      await postOriginal(next)
    } catch (failure) { setError(actionErrorMessage(failure)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  return <>
    {query.isPending && <p role="status">正在读取当前行动方案…</p>}
    {query.isError && <div className="action-notice" role="alert"><p>{actionErrorMessage(query.error)}</p><p>已隐藏上次方案的操作入口，请重新读取后核对。</p></div>}
    <div className="action-toolbar"><label>我的仓位<select aria-label="行动方案仓位" value={position} onChange={event => setPosition(event.target.value as ActionPosition | '')}><option value="">请选择当前仓位</option>{Object.entries(positions).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button type="button" className="button secondary" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新行动方案</button></div>
    {value && <>
      {value.availability === 'disabled' ? <p className="action-notice">行动方案尚未启用，原有研究报告仍可查阅。</p> : <div className={`action-opportunity ${older || latest?.status === 'missed' || latest?.status === 'expired' ? 'action-notice' : ''}`} aria-label="最近一次研究机会">
        <strong>{!latest ? '尚无已登记的研究机会' : latest.status === 'open' ? '最新机会已登记，尚未形成行动方案' : latest.status === 'missed' ? '最近一次研究未形成行动方案' : latest.status === 'expired' ? '最近一次机会已到期，未形成新方案' : '最近一次行动方案已保存'}</strong>
        {latest && <p>登记于 {at(latest.registered_at)} · 机会窗口至 {at(latest.time_end)}</p>}
        {older && <p>下方是此前保存的方案，不能替代这次研究的结果。</p>}
        {(latest?.reason || value.gap_codes.length > 0) && <details><summary>查看未完成原因与缺口</summary><p>{latest?.reason ? researchDisplayText(latest.reason) : '当前材料尚不满足完整判断要求。'}</p><ul>{value.gap_codes.map(code => <li key={code}>{researchDisplayText(code)}</li>)}</ul></details>}
      </div>}
      {!position && <p className="action-position-prompt">选择仓位后查看适用方案。空仓与已有持仓的处理方式不同，系统不会推断你的持仓。</p>}
      {position && !branch && <p>当前没有适用于“{positions[position]}”的已保存方案。</p>}
      {position === 'unknown' && <p className="action-notice">仓位尚不确定时只展示对应的等待或观察方案。</p>}
      {position && value.decision && <ActionReviewPanel key={`${value.decision.opportunity_id}:${position}`} opportunityId={value.decision.opportunity_id} scopeId={scopeId} horizon={horizon} position={position} onNavigate={onNavigate} />}
      <StillTracking view={value} />
    </>}
    {intent && <div className="action-notice" role="status"><strong>{operations[intent.command.operation]}结果待确认</strong><p>原操作已保留，请先核对回执。</p><div className="action-buttons"><button type="button" className="button secondary" disabled={busy} onClick={() => void restoreOriginal()}>恢复原操作回执</button>{missingReceipt && <button type="button" className="button secondary" disabled={busy} onClick={() => void restoreOriginal(true)}>重试原操作</button>}</div></div>}
    {error && <p className="action-error" role="alert">{error}</p>}
    {rejection && <div className="action-notice" role="status"><strong>上次操作未提交，原拒绝记录已保留</strong><p>{actionErrorMessage(new ApiRequestError(rejection.reason, 'http', 409))} 已重新读取方案；核对后可明确发起新操作。</p><details><summary>查看原拒绝记录</summary><p>{rejection.request_id} · {rejection.reason}</p></details></div>}
    {receipt && <p role="status">{operations[receipt.operation]}已保存 · {at(receipt.accepted_at)}</p>}
    {branch && <SelectedPlan key={branch.revision.revision_id} branch={branch} stale={query.isError || value?.availability !== 'available'} busy={busy || restored.error} pending={Boolean(intent)} receipt={receipt} onCommand={command} onNavigate={onNavigate} runId={branch.decision.run_id} />}
  </>
}

function ScopedActionPlanPanel({ scopeId, onNavigate }: { scopeId: string; onNavigate: Navigate }) {
  const [restored] = useState(() => readActionHorizon(ACTION_OWNER, scopeId))
  const [horizon, setHorizon] = useState<ActionHorizon>(restored.horizon)
  const [notice, setNotice] = useState(restored.notice)
  function choose(value: string) {
    if (!isActionHorizon(value)) return
    setHorizon(value)
    setNotice(saveActionHorizon(ACTION_OWNER, scopeId, value))
  }
  return <section className="product-card action-plan-panel" aria-label="当前行动方案">
    <div className="action-heading"><div><span className="action-eyebrow">事前条件 · 持续监测</span><h2>现在可以怎么做</h2></div><label>观察窗口<select aria-label="行动方案观察窗口" value={horizon} onChange={event => choose(event.target.value)}>{Object.entries(horizons).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
    {notice && <p className="product-muted" role="status">{notice}</p>}
    <HorizonPanel key={`${scopeId}:${horizon}`} scopeId={scopeId} horizon={horizon} onNavigate={onNavigate} />
    <ActionPaperPanel key={`paper:${scopeId}:${horizon}`} scopeId={scopeId} horizon={horizon} />
  </section>
}

export function ActionPlanPanel({ scopeId, onNavigate }: { scopeId: string; onNavigate: Navigate }) {
  return <ScopedActionPlanPanel key={`${ACTION_OWNER}:${scopeId}`} scopeId={scopeId} onNavigate={onNavigate} />
}
