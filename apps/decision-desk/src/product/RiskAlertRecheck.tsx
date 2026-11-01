import { useEffect, useRef, useState } from 'react'
import { riskAlertRecheckApi, readRecheckIntent, saveRecheckIntent, replaceUnacceptedRecheckIntent, type RecheckCommand, type RecheckReceipt, type RecheckState } from '../api/riskAlertRecheck'
import { ApiRequestError } from '../api/client'

const time = (value: string | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '尚无记录'
const reasons: Record<string, string> = {
  research_admission_budget_exhausted: '当前自动研究准入容量不足，尚不能补查。',
  study_research_active: '当前资产已有研究执行中，须先核对原执行结果。',
  study_host_terminal_unknown: '当前资产的模型执行尚未证实结束，尚不能补查。',
  study_host_activity_refresh_required: '模型执行活动仍需核验，尚不能准入。',
  risk_alert_recheck_request_active: '当前资产已有研究请求等待准入或正在核验条件。',
  risk_alert_recheck_monitor_not_fresh: '监测或行情时点尚未满足本次补查条件，请在监测恢复后重新核对。',
  risk_alert_recheck_original_not_budget_ended: '原请求不满足本次预算失败补查合同。',
  risk_alert_recheck_already_accepted: '这条原告警已受理过本轮补查。',
  risk_alert_recheck_head_changed: '原告警或原请求记录已变化，本次未受理，请重新核对。',
  spot_research_disabled: '自动研究尚未启用，尚不能补查。',
}
const phase: Record<string, string> = { pending_admission: '等待正常准入', claiming: '正在核验准入条件', queued: '已关联研究执行，报告尚需核对', completed: '执行已结束，报告保存与确认须打开原记录核对', failed: '本轮补查未完成' }
const eventLabel: Record<string, string> = { claimed: '领取准入核验', budget_blocked: '准入容量不足', admission_failed: '本次准入失败', claim_expired: '本次领取已到期', queued: '已建立研究执行' }
const budgetLabel = (value: string) => value.split(',').map(v => ({ global_daily: '全部非手动研究24小时额度', ordinary_daily: '普通自动研究24小时额度', study_hour: '当前资产1小时额度' }[v] ?? '准入额度')).join('、')

export function RiskAlertRecheck({ alertId, scopeId, onNavigate }: { alertId: string; scopeId: string; onNavigate: (scene: string, params?: Record<string, string | undefined>) => void }) {
  const [state, setState] = useState<RecheckState | null>(null)
  const [receipt, setReceipt] = useState<RecheckReceipt | null>(null)
  const [intent, setIntent] = useState<RecheckCommand | null>(null)
  const [receiptMissing, setReceiptMissing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(true)
  const [polls, setPolls] = useState(0)
  const [priorUnaccepted, setPriorUnaccepted] = useState<RecheckCommand | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    setState(null); setReceipt(null); setIntent(null); setReceiptMissing(false)
    setBusy(false); setError(null); setStale(true); setPolls(0); setPriorUnaccepted(null)
    try { setIntent(readRecheckIntent(alertId, scopeId)) } catch (e) { setError((e as Error).message) }
    return () => { mounted.current = false }
  }, [alertId, scopeId])
  async function readState() {
    const view = await riskAlertRecheckApi.state(alertId, scopeId)
    if (mounted.current) { setState(view); setReceipt(view.receipt); setStale(false) }
    return view
  }
  async function load() {
    if (busy) return
    setBusy(true); setError(null); setStale(true)
    try { await readState(); setPolls(0) } catch (e) { setError(reasons[(e as Error).message] ?? '补查条件或进展尚未核对，请重新读取。') }
    finally { if (mounted.current) setBusy(false) }
  }
  async function verifyReceipt() {
    if (!intent || busy) return
    setBusy(true); setError(null); setReceiptMissing(false); setStale(true)
    try {
      const saved = await riskAlertRecheckApi.receipt(alertId, scopeId, intent)
      if (saved) { setReceipt(saved); setReceiptMissing(false); await readState() }
      else { setReceiptMissing(true); await readState() }
    } catch (e) { setError((e as Error).message.startsWith('补查') ? (e as Error).message : '原补查提交结果尚未确认，保留原请求继续核对。') }
    finally { if (mounted.current) setBusy(false) }
  }
  async function accept(useNewHead = false) {
    if (busy || stale || error || !state?.eligible || (intent && !receiptMissing)
      || (useNewHead && (!intent || intent.expected_head_hash === state.head_hash))) return
    setBusy(true); setError(null); setStale(true)
    try {
      if (useNewHead && intent) {
        const oldReceipt = await riskAlertRecheckApi.receipt(alertId, scopeId, intent)
        if (oldReceipt) { setReceipt(oldReceipt); setReceiptMissing(false); await readState(); return }
      }
      const fresh = await readState()
      if (!fresh.eligible || fresh.head_hash !== state.head_hash) throw new Error(fresh.reason_code ?? 'risk_alert_recheck_head_changed')
      if (intent && intent.expected_head_hash !== fresh.head_hash && !useNewHead) throw new Error('risk_alert_recheck_head_changed')
      if (useNewHead && intent?.expected_head_hash === fresh.head_hash) throw new Error('risk_alert_recheck_head_changed')
      const command = useNewHead || !intent ? { request_id: `risk-alert-recheck:${crypto.randomUUID()}`, expected_head_hash: fresh.head_hash } : intent
      if (useNewHead && intent) {
        replaceUnacceptedRecheckIntent(alertId, scopeId, intent, command, fresh.observed_at)
        setPriorUnaccepted(intent)
      } else saveRecheckIntent(alertId, scopeId, command)
      setIntent(command); setReceiptMissing(false)
      const saved = await riskAlertRecheckApi.accept(alertId, scopeId, command)
      if (mounted.current) { setReceipt(saved); setPolls(0) }
      await readState()
    } catch (e) {
      const message = (e as Error).message
      const rejected = e instanceof ApiRequestError && e.status === 409 && message === 'research_admission_budget_exhausted'
      setError(rejected ? '本次补查未受理，未新建研究执行。提交时自动准入容量已不足；请重新读取条件核对计数与下一容量时间。'
        : reasons[message] ?? (message.startsWith('本机') ? message : '本次提交结果尚未确认。请核对原补查回执；保留原请求，不重复新建。'))
      setStale(true)
    }
    finally { if (mounted.current) setBusy(false) }
  }
  const request = state?.research_request
  const pending = receipt && request && !['completed', 'failed'].includes(request.status)
  useEffect(() => {
    if (!pending || busy || stale || polls >= 30) return
    const timer = setTimeout(() => { void readState().then(() => setPolls(n => n + 1)).catch(() => { setStale(true); setError('最新补查进展读取失败，当前显示上次记录。') }) }, 10_000)
    return () => clearTimeout(timer)
  }, [pending, busy, stale, polls, alertId, scopeId, request?.status])
  return <section className="risk-audit" aria-label="历史事件补查">
    <h4>补查这次历史事件</h4>
    <p>原自动请求因准入容量不足结束。补查使用当前可获得的证据，原触发时点及原失败记录保留；不能作为当时的预测或事前预警。</p>
    <button type="button" className="button secondary" disabled={busy} onClick={() => { void load() }}>{busy ? '正在核对…' : receipt ? '重新读取补查进展' : '核对历史事件补查条件'}</button>
    {intent && !receipt && <button type="button" className="button secondary" disabled={busy} onClick={() => { void verifyReceipt() }}>核对原补查回执</button>}
    {state && <>
      <p>本次核对：{time(state.observed_at)} · 原触发：{time(state.original_triggered_at)}{state.original_resolved_at && ` · 原条件解除：${time(state.original_resolved_at)}`}（北京时间）。</p>
      {!receipt && <p>{state.eligible ? '本次条件允许提交一次独立补查申请；领取时仍须重新核对实时准入与数据条件。' : reasons[state.reason_code ?? ''] ?? '当前条件尚未允许补查。'}</p>}
      {!receipt && <p>滚动24小时非手动研究：{state.capacity.daily_count ?? '未知'}/{state.capacity.daily_limit ?? '未知'}；普通自动研究：{state.capacity.ordinary_daily_count ?? '未知'}/{state.capacity.ordinary_daily_limit ?? '未知'}；当前资产滚动1小时：{state.capacity.study_hour_count ?? '未知'}/{state.capacity.study_hour_limit ?? '未知'}。计数以本次核对为准。</p>}
      {!receipt && state.capacity.next_capacity_check_at && <p>容量最早可重新核对：{time(String(state.capacity.next_capacity_check_at))}。到时不保证准入，也不会自动新建本轮补查。</p>}
      {!receipt && state.eligible && <button type="button" className="button primary" disabled={busy || stale || Boolean(error) || Boolean(intent && (!receiptMissing || intent.expected_head_hash !== state.head_hash))} onClick={() => { void accept() }}>{intent ? '按原请求补查这次历史事件' : '提交一次历史事件补查'}</button>}
      {!receipt && state.eligible && intent && receiptMissing && intent.expected_head_hash !== state.head_hash && <>
        <p>已核对原请求没有受理回执，原条件已变化。可保留原未受理请求，按本次核对的新条件提交一次补查。</p>
        <button type="button" className="button primary" disabled={busy || stale || Boolean(error)} onClick={() => { void accept(true) }}>按新条件补查并保留原未受理记录</button>
      </>}
    </>}
    {receipt && <>
      <p>补查申请已记录：{time(receipt.accepted_at)}。受理不代表已建立研究执行、保存报告或确认成功。</p>
      {request && <><p>{phase[request.status]} · 已核验 {request.attempts}/3 次。最近请求更新：{time(request.updated_at)}。</p>
        {request.run_created_at && <p>研究执行建立：{time(request.run_created_at)} · 执行结束：{time(request.run_finished_at)}。</p>}
        {request.run_id && !stale && <button type="button" className="button secondary" onClick={() => onNavigate('report', { run: request.run_id!, back: '#/home' })}>查看本次补查研究记录</button>}
        {request.status === 'failed' && <p role="alert">本轮已结束，不会因刷新、等待或更换请求标识而无限创建新轮次。原失败和本次补查记录均保留。</p>}</>}
      {polls >= 30 && pending && <p>本次自动读取已结束，执行结果仍待核对；可重新读取进展。</p>}
      <details><summary>补查时间与准入记录</summary><p>受理：{time(receipt.accepted_at)}</p>{state?.timeline.map((event, index) => <div key={`${event.lease_generation}:${event.event}:${index}`}><p>{time(event.recorded_at)} · 第 {event.attempt} 次 · {eventLabel[event.event]}</p>{event.capacity.budget_blockers && <p>阻断配额：{budgetLabel(String(event.capacity.budget_blockers))}；下次可核对：{time(String(event.capacity.next_capacity_check_at ?? ''))}。</p>}<details><summary>原始准入计数与身份</summary><pre>{JSON.stringify(event, null, 2)}</pre></details></div>)}</details>
    </>}
    {stale && state && <p>当前显示上次核对记录，最新状态尚未确认。</p>}
    {priorUnaccepted && <details><summary>已保留的原未受理请求</summary><p>原请求没有受理回执，已按明确核对过的新条件另建请求；原键及原条件保留于本机。</p><pre>{JSON.stringify(priorUnaccepted, null, 2)}</pre></details>}
    {error && <p role="alert">{error}</p>}
  </section>
}
