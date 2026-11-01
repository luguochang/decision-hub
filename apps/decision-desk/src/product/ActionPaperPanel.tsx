import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { actionPaperApi, paperCommandSchema, type PaperCommand, type PaperRegistration } from '../api/actionPaper'
import { ACTION_OWNER, type ActionHorizon } from '../api/actionPlans'
import { ApiRequestError } from '../api/client'
import { ledgerAmount } from './ledgerPresentation'

const at = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const states = { waiting_start: '已登记，等待开始', waiting_opening: '等待起始报价与开户', unknown: '本轮无法开始，结果未知', running: '正在验证后续行动', ended: '登记窗口已结束，未平持仓仍需查看' }
const statuses: Record<string, string> = { filled: '模拟成交', no_fill: '未成交', rejected: '执行条件不满足', unknown: '结果未知', pending: '尚未结算', settled: '已结算', unscorable: '无法评分', scorable: '等待结算', no_plan: '未形成行动方案', not_observed: '尚未处理', observed: '已有观察记录', blocked: '缺少必要原件', not_actionable: '未触发可执行动作' }
const errorText = (error: unknown) => error instanceof ApiRequestError && error.status === 409 ? '当前原件或登记条件未通过核对，请刷新后查看。' : '暂时无法核对模拟验证结果，已隐藏旧金额。请重新读取。'

function Results({ registration }: { registration: PaperRegistration }) {
  const queryClient = useQueryClient()
  const [showDenominator, setShowDenominator] = useState(false)
  const [cursor, setCursor] = useState<string | undefined>()
  const [showExecutions, setShowExecutions] = useState(false)
  const [executionCursor, setExecutionCursor] = useState<string | undefined>()
  const status = useQuery({ queryKey: ['action-paper-status', registration.payload_hash], queryFn: () => actionPaperApi.status(registration), retry: false, refetchInterval: 15_000 })
  const executions = useQuery({ queryKey: ['action-paper-outcomes', registration.payload_hash, executionCursor], queryFn: () => actionPaperApi.outcomes(registration, executionCursor), retry: false, enabled: showExecutions })
  const denominator = useQuery({ queryKey: ['action-paper-opportunities', registration.payload_hash, cursor], queryFn: () => actionPaperApi.opportunities(registration, cursor), retry: false, enabled: showDenominator && Boolean(status.data?.account) })
  const value = status.isError ? undefined : status.data
  const openingUnknown = value?.bootstrap?.status === 'unknown'
  useEffect(() => {
    if (openingUnknown) void queryClient.invalidateQueries({ queryKey: ['action-paper-options', registration.source.scope_id, registration.source.horizon] })
  }, [queryClient, openingUnknown, registration.source.scope_id, registration.source.horizon])
  const asset = registration.source.scope_id.split(':')[3].split('-')[0]
  return <div className="action-opportunity" aria-label="模拟验证结果">
    <h3>{registration.scenario === 'cash' ? '空仓开始' : '已有持仓情景'} · 模拟验证</h3>
    <p role="status">登记已保存 · {at(registration.accepted_at)}</p>
    <p>{at(registration.protocol.T0)} 开始 · {at(registration.protocol.T1)} 结束登记</p>
    {status.isPending && <p role="status">正在核对账户与原始执行记录…</p>}
    {status.isError && <p role="alert">{errorText(status.error)}</p>}
    <button className="button secondary" disabled={status.isFetching} onClick={() => void status.refetch()}>查看跟踪结果</button>
    {value && <>
      <strong>{states[value.state]}</strong>
      {value.unresolved_inventory && (value.state === 'ended'
        ? <p role="alert">登记窗口已结束，仍有模拟持仓待退出或继续保护。请查看后续处理记录。</p>
        : <p>正在跟踪已有模拟持仓，后续减仓、退出及损益会单独记录。</p>)}
      {value.bootstrap?.status === 'unknown' && <p>本轮未建立模拟持仓，不计算收益。原因：{value.bootstrap.reason === 'execution_opening_T0_quote_missing' ? '起始报价缺失' : '错过登记窗口'}。系统会重新核对登记条件，条件恢复后可开始新轮次；本轮记录会保留。</p>}
      {value.account && <>
        {registration.scenario === 'existing_long' && <p>初始仓位按开始时的真实报价换算，作为已有持仓情景；不计作系统买入成交。后续操作从此开始验证。</p>}
        <dl><dt>现金（USDT）</dt><dd>{ledgerAmount(value.account.account_after.cash)}</dd><dt>模拟持仓（{asset}）</dt><dd>{ledgerAmount(value.account.account_after.quantity, 6)}</dd><dt>已实现净损益（USDT）</dt><dd>{ledgerAmount(value.account.account_after.realized_net)}</dd></dl>
        <details><summary>查看账户完整精度与记录编号</summary><dl><dt>现金（USDT）</dt><dd>{value.account.account_after.cash}</dd><dt>持仓（{asset}）</dt><dd>{value.account.account_after.quantity}</dd><dt>已实现净损益（USDT）</dt><dd>{value.account.account_after.realized_net}</dd><dt>登记编号</dt><dd>{registration.request_id}</dd><dt>模拟账户</dt><dd>{registration.portfolio_id}</dd></dl></details>
      </>}
      <p>待执行 {value.pending_intents} 项 · 执行结果 {value.outcomes_count} 条 · 登记范围内全部机会 {value.opportunities_count} 次</p>
      {value.outcomes.length === 0 && <p>尚无执行结果。登记、条件触发、提交意图均不代表已经成交。</p>}
      {value.outcomes.map(item => <article key={item.id}><strong>{statuses[item.execution_status] || '结果待核对'} · {statuses[item.status] || '状态待核对'}</strong><p>{item.net_pnl === null ? '净损益：未知或尚未结算' : `净损益：${item.net_pnl} USDT`}</p><details><summary>查看这条结果的原件引用</summary><p>计划：{item.plan_id}</p><p>执行：{item.execution_ref}</p><p>结果：{item.id}</p><p>执行原因：{value.execution_reasons[item.id] || '无附加原因'}</p><p>核对时间：{at(value.observed_at)}</p></details></article>)}
      {value.outcomes_count > value.outcomes.length && <p>以上为最近 {value.outcomes.length} 条执行结果。</p>}
      {value.outcomes_count > 0 && <button onClick={() => setShowExecutions(current => !current)}>{showExecutions ? '收起全部执行记录' : '查看全部执行记录'}</button>}
      {showExecutions && executions.isError && <p role="alert">执行记录暂时无法核对，未显示旧金额。</p>}
      {showExecutions && !executions.isError && executions.data && <div>{executions.data.items.map(item => <article key={item.outcome.id}><strong>{statuses[item.receipt.status]}</strong><p>{item.outcome.net_pnl === null ? '净损益未知或未结算' : `净损益：${item.outcome.net_pnl} USDT`}</p><details><summary>查看执行依据</summary><p>{item.receipt.reason || '执行条件已核对'}</p><p>{item.outcome.execution_ref}</p><p>{item.outcome.id}</p></details></article>)}{executionCursor && <button onClick={() => setExecutionCursor(undefined)}>回到首批执行记录</button>}{executions.data.next_cursor && <button onClick={() => setExecutionCursor(executions.data.next_cursor!)}>下一批执行记录</button>}</div>}
      {value.account && <button className="button secondary" onClick={() => setShowDenominator(current => !current)}>{showDenominator ? '收起全部机会记录' : '查看全部机会记录'}</button>}
      {showDenominator && denominator.isError && <p role="alert">机会记录暂时无法核对，未显示旧结果。</p>}
      {showDenominator && !denominator.isError && denominator.data && <div><p>未形成方案、未知、未成交也保留在机会记录中，不计作胜负。</p>{denominator.data.items.length === 0 && <p>这个登记窗口尚无新机会。</p>}{denominator.data.items.map(item => <article key={item.opportunity.id}><strong>{at(item.opportunity.registered_at)} · {statuses[item.category] || '待核对'}</strong>{item.observations.map((observation, index) => <div key={index}>{statuses[observation.status] || '观察已记录'} <details><summary>查看原始原因</summary>{observation.reason}</details></div>)}</article>)}{cursor && <button onClick={() => setCursor(undefined)}>回到首批机会</button>}{denominator.data.next_cursor && <button onClick={() => setCursor(denominator.data.next_cursor!)}>下一批机会</button>}</div>}
    </>}
  </div>
}

export function ActionPaperPanel({ scopeId, horizon }: { scopeId: string; horizon: ActionHorizon }) {
  const key = `action-paper:${ACTION_OWNER}:${scopeId}:${horizon}`
  const [restored] = useState(() => {
    try { const raw = sessionStorage.getItem(key); return { pending: raw ? paperCommandSchema.parse(JSON.parse(raw)) : null, error: false } }
    catch { return { pending: null, error: true } }
  })
  const [pending, setPending] = useState<PaperCommand | null>(restored.pending)
  const [scenario, setScenario] = useState<PaperCommand['scenario']>('cash')
  const [selected, setSelected] = useState<PaperRegistration | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const options = useQuery({ queryKey: ['action-paper-options', scopeId, horizon], queryFn: () => actionPaperApi.options(scopeId, horizon), retry: false })
  const value = options.isError ? undefined : options.data
  const registration = selected || value?.registrations[0]
  const active = value?.registrations.find(item => item.scenario === scenario && item.source.method_hash === value.source?.method_hash && value.active_request_ids.includes(item.request_id))
  const previousMethod = Boolean(registration && value?.source && registration.source.method_hash !== value.source.method_hash)
  async function accept(result: PaperRegistration) { sessionStorage.removeItem(key); setPending(null); setMissing(false); setSelected(result); setError(null); await options.refetch() }
  async function post(command: PaperCommand) {
    try { await accept(await actionPaperApi.register(command)) }
    catch (failure) {
      if (!(failure instanceof ApiRequestError && [403, 409, 422].includes(failure.status || 0))) throw failure
      try { await accept(await actionPaperApi.receipt(command)); return }
      catch (readFailure) { if (!(readFailure instanceof ApiRequestError && readFailure.status === 404)) throw readFailure }
      sessionStorage.setItem(`${key}:rejected:${command.request_id}`, JSON.stringify({ command, reason: failure.message }))
      sessionStorage.removeItem(key); setPending(null); setMissing(false)
      setError(failure.message === 'paper_active_registration_exists' ? '已有这一情景的跟踪轮次，请查看原记录；没有重复开户。' : '本次登记未提交，拒绝记录已保留。请刷新条件后核对。')
      await options.refetch()
    }
  }
  async function restore(retry = false) {
    if (!pending || busyRef.current) return
    busyRef.current = true; setBusy(true); setError(null)
    try { try { await accept(await actionPaperApi.receipt(pending)) } catch (failure) { if (!(failure instanceof ApiRequestError && failure.status === 404)) throw failure; setMissing(true); if (retry) await post(pending) } }
    catch (failure) { setError(errorText(failure)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  useEffect(() => { if (pending) void restore() }, [])
  async function start() {
    if (!value?.enabled || !value.source || pending || busyRef.current || restored.error || active) return
    const command: PaperCommand = { request_id: `paper-${crypto.randomUUID()}`, source_opportunity_id: value.source.opportunity_id, scenario }
    busyRef.current = true; setBusy(true); setError(null)
    try { sessionStorage.setItem(key, JSON.stringify(command)); setPending(command); await post(command) }
    catch (failure) { setError(errorText(failure)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  return <section aria-label="行动模拟验证" className="action-opportunity">
    <h3>用后续行情验证行动</h3>
    <p>固定 10,000 USDT 情景本金，60 秒执行延迟；每侧费用假设 0.1%、滑点假设 0.05%。这是模拟测算金额，不是购买建议，不连接交易账户。</p>
    <p>只跟踪登记时的分析方法与当前观察窗口；其他资产或窗口单独验证，不合并本金和胜率。当前是诊断验证，尚不能宣称实盘胜率。</p>
    {options.isError && <p role="alert">{errorText(options.error)}</p>}
    <button className="button secondary" disabled={options.isFetching} onClick={() => void options.refetch()}>刷新登记条件</button>
    {value && <>
      {!value.enabled && <p>模拟执行尚未启用，可查看已有记录。</p>}
      {!value.source && <p>还没有当前资产的已登记分析方法。等待正常研究形成机会后再开始。</p>}
      <label>起始情景<select aria-label="模拟起始情景" value={scenario} onChange={event => setScenario(event.target.value as PaperCommand['scenario'])}><option value="cash">空仓开始</option><option value="existing_long">已有持仓情景</option></select></label>
      <button className="button primary" disabled={!value.enabled || !value.source || busy || Boolean(pending) || restored.error || Boolean(active)} onClick={() => void start()}>开始模拟跟踪</button>
      {active && <p>当前方法的这一情景已登记，继续查看原轮次的跟踪结果，不会重复开户。</p>}
      {previousMethod && <p>下方是此前分析方法的独立验证轮次。方法变更后使用独立登记，各轮本金、持仓与损益单独保留，不合并计算。</p>}
      {value.registrations.length > 0 && <label>已登记轮次<select aria-label="模拟验证轮次" value={registration?.request_id || ''} onChange={event => setSelected(value.registrations.find(item => item.request_id === event.target.value) || null)}>{value.registrations.map(item => <option key={item.request_id} value={item.request_id}>{at(item.accepted_at)} · {item.scenario === 'cash' ? '空仓' : '已有持仓'} · {value.source ? (item.source.method_hash === value.source.method_hash ? '当前方法' : '此前方法') : '已冻结方法'}</option>)}</select></label>}
      {value.registrations_more && <p>这里展示最近 20 轮。原登记回执仍可按请求编号读取。</p>}
    </>}
    {restored.error && <p role="alert">本机原登记恢复记录无法解析，已保留原文并停止新登记，请先核对原请求。</p>}
    {pending && <div role="status"><p>登记结果待确认，原请求已保存；请先恢复原回执。</p><button disabled={busy} onClick={() => void restore()}>恢复模拟登记回执</button>{missing && <button disabled={busy} onClick={() => void restore(true)}>重试原模拟登记</button>}</div>}
    {error && <p role="alert">{error}</p>}
    {!options.isError && registration && <Results key={registration.payload_hash} registration={registration} />}
  </section>
}
