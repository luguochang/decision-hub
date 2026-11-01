import { useEffect, useRef, useState } from 'react'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from '../api/perpetualAdvice'
import { canonicalDecisionBody } from '../api/decisionOriginals'
import { ApiRequestError } from '../api/client'
import { settingsApi } from '../api/decisionSettings'
import { archiveUnacceptedManualPaperIntent, makeManualPaperIntent, manualIntentMatches, perpetualManualPaper, readManualPaperIntent, saveManualPaperIntent,
  type ManualPaperContext, type ManualPaperExecution, type ManualPaperInputGuide, type ManualPaperIntent, type ManualPaperReceipt } from '../api/perpetualManualPaper'

const reasons: Record<string, string> = {
  no_eligible_quote_before_deadline: '等待时间内没有符合条件的报价。',
  first_eligible_quote_missed_or_expired: '首个符合条件的报价已过期，本次未成交。',
  first_quote_outside_user_exit_bounds: '首个报价不在你确认的止损、止盈范围内。',
  user_amount_below_contract_minimum: '模拟金额不足最小合约张数。',
  perp_manual_planned_loss_limit_exceeded: '按当前报价、止损和交易费用估算，计划损失超过你保存的上限，本次未成交。',
  first_quote_capacity_exhausted: '首个报价的可模拟成交量不足。',
  perp_manual_settings_changed: '设置已变化，本次未成交。',
  entry_plan_changed_or_terminal: '方案已变化或结束，本次未成交。',
  entry_deadline_elapsed: '入场等待时间已结束，本次未成交。',
}
const executionStatuses = { filled: '已模拟成交', no_fill: '未成交', rejected: '未获接受', unknown: '结果待核对' }
class ManualPaperLocalInputError extends Error {}
const localDateTime = (value: number) => {
  const date = new Date(value)
  return new Date(value - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 19)
}
const localDisplay = (value: string) => new Date(value).toLocaleString()

export function PerpetualManualPaperPanel({ value, selection }: { value: PerpetualAdviceView | null; selection: PerpetualAdviceSelection }) {
  const element = useRef<HTMLElement>(null), [visible, setVisible] = useState(false)
  const [configured, setConfigured] = useState(false)
  const [tickSize, setTickSize] = useState<string | null>(null), [tickChecked, setTickChecked] = useState(false)
  const [context, setContext] = useState<ManualPaperContext | null>(null), [pending, setPending] = useState<ManualPaperIntent | null>(null)
  const [guide, setGuide] = useState<ManualPaperInputGuide | null>(null), [guideState, setGuideState] = useState<'idle' | 'loading' | 'unavailable'>('idle')
  const [protocolEnd, setProtocolEnd] = useState<string | null>(null), guideGeneration = useRef(0)
  const [guideSource, setGuideSource] = useState<{ original: ManualPaperIntent; receipt: ManualPaperReceipt } | null>(null)
  const [accepted, setAccepted] = useState<{ original: ManualPaperIntent; id: string } | null>(null), [execution, setExecution] = useState<ManualPaperExecution | null>(null)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [amount, setAmount] = useState(''), [stop, setStop] = useState(''), [take, setTake] = useState(''), [exit, setExit] = useState(''), [ack, setAck] = useState(false)
  const event = value?.event_scenario_plan
  const activeEvent = event?.current_state === 'active' && event.selection?.status === 'selected_preapproved_branch'
    && ['open_long', 'open_short'].includes(event.selection.selected_action_id ?? '') && event.selection_original && event.selection_ref && event.selection.branch_ref ? event : null
  const eventBinding = activeEvent ? { event_plan_ref: activeEvent.plan_ref, selection_ref: activeEvent.selection_ref!, branch_ref: activeEvent.selection!.branch_ref!,
    event_review_ref: activeEvent.review_authorization_ref, event_publication_ref: activeEvent.publication_ref } : null
  const eventTarget = activeEvent?.plan.branches.find(branch => canonicalDecisionBody(branch.branch_ref) === canonicalDecisionBody(eventBinding?.branch_ref))?.target
  const revision = activeEvent?.selection_original?.plan_revision_ref ?? (value?.revision ? { ref: value.revision.revision_id, hash: value.revision.payload_hash } : null)
  const key = `decision-hub:manual-paper-preparation.v1:${selection.owner_id}`
  const selectionIdentity = canonicalDecisionBody({ owner_id: selection.owner_id, partition: selection.partition,
    settings_revision: selection.settings_revision, settings_state_version: selection.settings_state_version, horizon: selection.horizon, revision, eventBinding })
  const selectionIdentityRef = useRef(selectionIdentity)
  selectionIdentityRef.current = selectionIdentity
  const allowed = ['4h', '24h', '72h'].includes(selection.horizon) && selection.partition.account_kind === 'paper' && (event ? Boolean(activeEvent)
    : (value?.status === 'available' && value.context?.account_snapshot?.account_kind === 'paper' && value.context.account_snapshot.available_margin !== null
    && value.context.position_context?.state === 'flat' && value.revision?.selected_candidate.action === 'open'))
  useEffect(() => {
    guideGeneration.current += 1
    setGuide(null); setGuideState('idle'); setGuideSource(null); setProtocolEnd(null); setContext(null); setPending(null)
  }, [selectionIdentity])
  useEffect(() => {
    void perpetualManualPaper.capabilities().then(result => setConfigured(result.configured &&
      (result.schema_version === 'perpetual-manual-capabilities.v1' ? result.instrument_id === selection.partition.instrument_id : result.instrument_ids.includes(selection.partition.instrument_id)) &&
      (result.schema_version === 'perpetual-manual-capabilities.v3' ? result.horizons.includes(selection.horizon) : selection.horizon === '4h'))).catch(() => setConfigured(false))
    if (typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)))
    if (element.current) observer.observe(element.current)
    return () => observer.disconnect()
  }, [selection.horizon, selection.partition.instrument_id])
  useEffect(() => {
    let live = true
    setTickSize(null); setTickChecked(false)
    void settingsApi.options().then(options => {
      if (!live) return
      const selected = options.instruments.find(item => item.instrument_id === selection.partition.instrument_id)
      setTickSize(selected?.contract.tick_sz ?? null); setTickChecked(true)
    }).catch(() => { if (live) setTickChecked(true) })
    return () => { live = false }
  }, [selection.partition.instrument_id])
  function readGuide(original: ManualPaperIntent, receipt: ManualPaperReceipt, expectedIdentity = selectionIdentityRef.current) {
    const generation = ++guideGeneration.current
    setGuide(null); setGuideState('loading')
    void perpetualManualPaper.guide(original, receipt).then(result => {
      if (guideGeneration.current === generation && selectionIdentityRef.current === expectedIdentity) { setGuide(result); setGuideState('idle') }
    }).catch(() => {
      if (guideGeneration.current === generation && selectionIdentityRef.current === expectedIdentity) { setGuide(null); setGuideState('unavailable') }
    })
  }
  async function receive(original: ManualPaperIntent, receipt: ManualPaperReceipt, expectedIdentity = selectionIdentity) {
    if (original.owner_id !== selection.owner_id || canonicalDecisionBody(original.command.partition) !== canonicalDecisionBody(selection.partition)) throw new Error('原回执账户与当前账户不符。')
    setPending(null)
    if (receipt.response.status === 'prepared') {
      if (selectionIdentityRef.current !== expectedIdentity || !revision || !manualIntentMatches(original, selection, revision, eventBinding)) return
      const completed = !accepted || execution?.episode?.remaining_contracts === '0'
        || execution?.receipt?.status === 'no_fill' || execution?.receipt?.status === 'rejected'
      if (accepted && completed && allowed) {
        const historyKey = `decision-hub:manual-paper-completed.v1:${selection.owner_id}:${accepted.id}`
        const history = canonicalDecisionBody({ original: accepted.original, execution })
        localStorage.setItem(historyKey, history)
        if (localStorage.getItem(historyKey) !== history) throw new Error('perp_manual_history_not_saved')
        setAccepted(null); setExecution(null)
      }
      localStorage.setItem(key, JSON.stringify(original)); setContext(receipt.response.context!); setProtocolEnd(receipt.response.protocol!.T1)
      setGuideSource({ original, receipt }); readGuide(original, receipt, expectedIdentity)
      setMessage('协议已保存。请明确输入本笔模拟金额与保护条件。')
    } else if (receipt.response.status === 'queued') {
      const id = receipt.response.intent!.id
      setAccepted({ original, id }); setMessage('模拟意图已排队，尚不表示成交。')
      setExecution(await perpetualManualPaper.execution(original, id))
    }
  }
  useEffect(() => {
    let live = true
    const expectedIdentity = selectionIdentity
    void (async () => {
      try {
        const original = await readManualPaperIntent(selection.owner_id)
        if (!live || selectionIdentityRef.current !== expectedIdentity || !original) return
        if (original.owner_id !== selection.owner_id || canonicalDecisionBody(original.command.partition) !== canonicalDecisionBody(selection.partition)) return
        if (original.command.schema_version.startsWith('perpetual-manual-prepare.') && (!revision || !manualIntentMatches(original, selection, revision, eventBinding))) {
          setPending(null); setMessage('已保留旧准备回执，但它不适用于当前建议；请重新准备。'); return
        }
        setPending(original)
        const recovered = await perpetualManualPaper.receipt(original)
        if (!live || selectionIdentityRef.current !== expectedIdentity) return
        await receive(original, recovered, expectedIdentity)
        if (!revision || !manualIntentMatches(original, selection, revision, eventBinding)) setMessage('这是原模拟意图的回执与保护状态；当前建议已变化，不作为新开仓授权。')
        const prepared = localStorage.getItem(key)
        if (live && prepared && !original.command.schema_version.startsWith('perpetual-manual-prepare.')) {
          const previous = JSON.parse(prepared) as ManualPaperIntent
          if (revision && manualIntentMatches(previous, selection, revision, eventBinding)) {
            const receipt = await perpetualManualPaper.receipt(previous)
            if (live && selectionIdentityRef.current === expectedIdentity) await receive(previous, receipt, expectedIdentity)
          }
        }
      } catch { if (live) setMessage('结果尚不确定，请先读取原回执；不会自动重新提交。') }
    })()
    return () => { live = false }
  // Each saved advice identity gets a separate recovery operation.
  }, [selectionIdentity])
  async function post(command: unknown) {
    const expectedIdentity = selectionIdentity
    const original = await makeManualPaperIntent(command)
    await saveManualPaperIntent(original); setPending(original)
    let receipt: ManualPaperReceipt
    try { receipt = await perpetualManualPaper.command(original) }
    catch (error) {
      if (await archiveUnacceptedManualPaperIntent(original, error)) setPending(null)
      throw error
    }
    await receive(original, receipt, expectedIdentity)
    return receipt
  }
  async function run(operation: () => Promise<unknown>) {
    setBusy(true); setMessage('')
    try { await operation() } catch (error) {
      setMessage(error instanceof ManualPaperLocalInputError ? error.message
        : error instanceof ApiRequestError && error.kind === 'http' && error.status === 422
        ? '请求未获接受，原输入已保存在本机拒绝记录。请修正后再次确认。'
        : '结果尚不确定，请先读取原回执；不会自动重新提交。')
    } finally { setBusy(false) }
  }
  function exitLimit() {
    const protocol = Date.parse(protocolEnd ?? ''), target = Date.parse(eventTarget?.window_end ?? value?.target?.window_end ?? '')
    if (!Number.isFinite(protocol) || !Number.isFinite(target)) throw new ManualPaperLocalInputError('协议最晚退出时间尚未读取，请保留原回执并稍后重试。')
    return Math.min(protocol, target)
  }
  function applyLatestExit() {
    try {
      const limit = exitLimit()
      if (limit <= Date.now()) throw new ManualPaperLocalInputError('本次模拟协议已过期，请重新准备。')
      setExit(localDateTime(limit)); setMessage('')
    } catch (error) { setMessage(error instanceof ManualPaperLocalInputError ? error.message : '无法读取允许的最晚退出时间。') }
  }
  async function confirm() {
    if (!allowed || !revision || !context || !ack || !visible || document.visibilityState !== 'visible') throw new ManualPaperLocalInputError('请在可见建议下确认模拟。')
    if (guide?.latest_entry_at && Date.now() >= Date.parse(guide.latest_entry_at)) throw new ManualPaperLocalInputError('新入场许可已到期，请重新读取建议；已有仓位和原回执仍可查看。')
    const positive = /^(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9])$/
    if (![amount, stop, take].every(item => positive.test(item))) throw new ManualPaperLocalInputError('请输入明确的正数金额与价格。')
    const exitAt = new Date(exit)
    if (!Number.isFinite(exitAt.valueOf()) || exitAt.valueOf() <= Date.now()) throw new ManualPaperLocalInputError('最晚退出时间必须晚于当前时间。')
    if (exitAt.valueOf() > exitLimit()) throw new ManualPaperLocalInputError('最晚退出时间不得晚于协议 T1 或当前预测窗。')
    const policy = { max_entry_notional_usdt: amount, stop_price_usdt: stop, take_profit_price_usdt: take, exit_at: exitAt.toISOString() }
    const displayed = await post({ ...context, schema_version: 'event_plan_ref' in context ? 'perpetual-manual-display.v2' : 'perpetual-manual-display.v1', request_id: crypto.randomUUID() })
    await post({ ...context, ...policy, schema_version: 'event_plan_ref' in context ? 'perpetual-manual-intent-command.v2' : 'perpetual-manual-intent-command.v1', request_id: crypto.randomUUID(),
      display_receipt: { ref: displayed.request_id, hash: displayed.receipt_hash }, simulation_acknowledged: true })
  }
  return <section ref={element} aria-label="手动模拟交易"><h4>手动确认一笔模拟</h4>
    <p>使用模拟资金，按当前建议手动确认金额、止损、止盈和退出时间。当前支持 BTC/ETH 4、24、72小时空仓开仓。100 USDT 比较单位不是投入金额，不发送真实订单。</p>
    {!allowed ? <p>请先保存真实模拟账户并读取可用的 BTC/ETH 4、24、72小时空仓开仓建议；其他情形暂不支持手动执行。</p> : <>
      <p>已保存的计划损失上限会在成交前按止损触发价和两边交易费核对。该估算的资金费仍未知，跳空后的实际损失可能超过计划。</p>
      <p>{configured ? '服务已配置模拟执行；工作进程是否在线仍未知，以执行原回执为准。' : '服务尚未配置模拟执行，暂不能提交；已有原回执仍可读取。'}</p>
      <p id="perpetual-manual-price-format">{!tickChecked && !guide ? '正在读取当前合约的交易所价格格式…' : (guide?.tick_sz ?? tickSize)
        ? `当前已核验的交易所合约元数据：最小价格变动为 ${guide?.tick_sz ?? tickSize} USDT。止损和止盈必须是 ${guide?.tick_sz ?? tickSize} 的整数倍；请自行输入，不会自动生成价格。`
        : '当前合约的交易所价格格式尚未读取；止损和止盈仍由服务端按已保存合约原件核验，不会自动生成价格。'}</p>
      {activeEvent && <p>按已激活事件分支准备：{activeEvent.selection?.selected_action_id === 'open_short' ? '做空' : '做多'}。金额和保护条件由你确认，服务端重新核对当前账户与分支许可。</p>}
      {!context && <button disabled={!configured || busy || Boolean(pending)} onClick={() => void run(() => post({ schema_version: eventBinding ? 'perpetual-manual-prepare.v2' : 'perpetual-manual-prepare.v1', request_id: crypto.randomUUID(),
        partition: selection.partition, settings_revision: selection.settings_revision, settings_state_version: selection.settings_state_version, revision, horizon: selection.horizon, ...(eventBinding ?? {}) }))}>准备手动模拟</button>}
      {context && !accepted && <fieldset disabled={!configured || busy || Boolean(pending)}><legend>本笔独立模拟意图</legend>
        {guideState === 'loading' && <p>正在读取这份准备回执已有的参考报价原件…</p>}
        {guideState === 'unavailable' && <p>参考报价暂未读取；已保留原准备回执，提交仍须通过当前入场许可核验，不会自动重新提交。</p>}
        {guide?.quote.status === 'pending' && <p>暂未捕获该合约的可用参考报价。保护价仍需由你明确输入。</p>}
        {guide?.quote.status !== 'pending' && guide?.quote && <p>参考买一 {guide.quote.bid} USDT，卖一 {guide.quote.ask} USDT；
          {guide.quote.status === 'fresh' ? `报价有效至 ${localDisplay(guide.quote.expires_at)}。` : '该报价已过期，仅用于追溯，请等待新原件。'}
          成交时会另取确认后的新报价。</p>}
        {guide && <p>{guide.target_side === 'long' ? '做多：止损价应低于拟入场价，止盈价应高于拟入场价。' : '做空：止损价应高于拟入场价，止盈价应低于拟入场价。'}参考报价不会自动填入保护价。</p>}
        {guideSource && guideState !== 'loading' && <button type="button" onClick={() => readGuide(guideSource.original, guideSource.receipt)}>重新读取参考报价</button>}
        {guide?.latest_entry_at
          ? <p>新入场许可截止（本地）：<time aria-label="新入场许可截止" dateTime={guide.latest_entry_at}>{localDisplay(guide.latest_entry_at)}</time>。确认和实际成交仍分别由服务端按当前许可核验。</p>
          : <p>本准备未单列新入场截止；入场仍按原建议和服务端当前许可核验。</p>}
        {protocolEnd && <p>持仓最晚退出时间（原协议 T1，本地）：<time aria-label="持仓最晚退出" dateTime={protocolEnd}>{localDisplay(protocolEnd)}</time>。你可选择更早退出；已有仓位仍按原保护条件管理。</p>}
        <p>手动金额、止损、止盈和提前退出构成本笔独立模拟计划；本笔模拟计划的盈利概率尚未单独计算。</p>
        <label>最多入场名义金额（USDT）<input name="amount" inputMode="decimal" value={amount} onInput={event => setAmount(event.currentTarget.value)} /></label>
        <label>止损触发价（USDT）<input name="stop" inputMode="decimal" aria-describedby="perpetual-manual-price-format" value={stop} onInput={event => setStop(event.currentTarget.value)} /></label>
        <label>止盈触发价（USDT）<input name="take" inputMode="decimal" aria-describedby="perpetual-manual-price-format" value={take} onInput={event => setTake(event.currentTarget.value)} /></label>
        <label>最晚退出时间（本地时间）<input name="exit" type="datetime-local" step="1" value={exit} onInput={event => setExit(event.currentTarget.value)} /></label>
        <button type="button" disabled={!protocolEnd} onClick={applyLatestExit}>使用允许的最晚时间</button>
        <label><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />我确认这是模拟，并独立授权以上金额和退出条件。</label>
        <button disabled={!ack || !visible} onClick={() => void run(confirm)}>确认这笔手动模拟</button>
      </fieldset>}
    </>}
    {message && <p role="status">{message}</p>}
    {pending && <button disabled={busy} onClick={() => void run(async () => receive(pending, await perpetualManualPaper.receipt(pending)))}>读取原回执</button>}
    {accepted && <button disabled={busy} onClick={() => void run(async () => setExecution(await perpetualManualPaper.execution(accepted.original, accepted.id)))}>读取模拟执行状态</button>}
    {execution && <><p>入场状态：{execution.receipt ? executionStatuses[execution.receipt.status] : '排队等待报价'}。{execution.receipt?.reason && (reasons[execution.receipt.reason] ?? '本次未满足成交条件，详情见原回执。')}</p>
      {execution.fill && <p>成交 {execution.fill.filled_contracts} 张，价格 {execution.fill.fill_price_usdt} USDT，交易费 {execution.fill.trade_fee_usdt} USDT。</p>}
      {execution.episode && <p>当前剩余 {execution.episode.remaining_contracts} 张。{execution.episode.remaining_contracts === '0'
        ? execution.outcome?.status === 'settled' ? '已完成资金费覆盖核对。' : '已退出，正在核对实际资金费覆盖。'
        : execution.episode.exit_alarm ? '退出超时，保护意图仍待执行。' : '按原保护条件等待退出。'}</p>}
      {execution.outcome && (execution.outcome.status === 'settled'
        ? <><p>已退出；交易现金变化 {execution.outcome.trade_cash_delta_usdt} USDT，交易费 {execution.outcome.trade_fees_usdt} USDT，实际资金费 {execution.outcome.funding_cash_usdt} USDT，净收益 {execution.outcome.net_pnl_usdt} USDT。本次模拟结果为{Number(execution.outcome.net_pnl_usdt) > 0 ? '盈利' : Number(execution.outcome.net_pnl_usdt) < 0 ? '亏损' : '持平'}。</p>
          <p>继续使用同一模拟账户前，请<a href="#/settings">返回账户设置</a>，重新读取模拟账户并保存设置，再发起新研究；不会沿用旧建议授权。</p></>
        : <p>已退出；交易现金变化 {execution.outcome.trade_cash_delta_usdt} USDT，交易费 {execution.outcome.trade_fees_usdt} USDT。实际资金费未完整核对，净收益与胜负保持未知。</p>)}
      <details><summary>模拟原回执与账本追溯</summary><pre>{JSON.stringify(execution, null, 2)}</pre></details></>}
  </section>
}
