import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { eventLifecycleApi, type EventNotice } from '../api/eventLifecycle'

const time = (v: string | null) => v ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(v)) : '尚无记录'
const percent = (v: string | null) => v === null ? '等待完整数据' : `${(Number(v) * 100).toFixed(2)}%`
const status = { waiting: '等待公布或条件确认', triggered: '条件已确认', invalidated: '预案已失效', expired: '观察窗口已结束', missing: '等待必要数据' }
const coverage = { pending: '尚未到提醒时间', ready: '事前预案已保存', late: '预案晚于本轮提醒时点', missing: '本轮必要输入未齐', missed: '本轮未在公布前完成' }
const slots: Record<string, string> = { pre_24h: '公布前24小时', pre_2h: '公布前2小时', pre_30m: '公布前30分钟' }
const metrics: Record<string, string> = { headline_mom: '总体环比', core_mom: '核心环比', headline_yoy: '总体同比', core_yoy: '核心同比' }
const outcomeState: Record<string, string> = { waiting_horizon: '等待观察窗口结束', missing_plan: '缺少公布前的有效预案', missing_path: '认证行情不完整', scored: '已完成纸面回看' }
const outcomeGap: Record<string, string> = {
  event_24h_horizon_not_mature: '24小时观察窗口尚未结束。',
  authenticated_complete_minute_path_missing: '缺少连续且通过原件核验的分钟行情，暂无法计算。',
  valid_preevent_v2_plan_missing: '没有可核对的公布前预案，不能事后补造策略收益。',
  authenticated_event_outcome_not_recorded: '尚无通过核验的结果记录。',
  paper_rule_replay_not_observed_account_execution: '按规则进行纸面回放，没有实际账户成交。',
  held_long_value_is_one_unit_relative_to_holding_not_account_profit: '减险结果按一单位持仓相对持续持有计算。',
  spread_and_actual_account_fees_unverified: '采用约定成本，未核实实际账户价差和费用。',
  price_availability_at_close_is_simulated_not_native_observed_execution: '纸面回放假设分钟收盘后价格即可使用。',
  old_v2_profit_probability_does_not_qualify_event_anchored_protocol: '旧版策略胜率不能用于当前公告锚定规则。',
}
const scenario = (id: string | null) => {
  const match = /^cpi:headline_(cooler|near|hotter):core_(cooler|near|hotter)$/.exec(id ?? '')
  const bands: Record<string, string> = { cooler: '低于上月', near: '接近上月', hotter: '高于上月' }
  return match ? `总体环比${bands[match[1]]}、核心环比${bands[match[2]]}` : '等待实际值配对'
}

function Notice({ value, refresh }: { value: EventNotice; refresh: () => void }) {
  const element = useRef<HTMLDivElement>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorCommand, setErrorCommand] = useState<'display' | 'ack'>('display')
  useEffect(() => {
    if (value.display_at || !element.current) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        observer.disconnect()
        void eventLifecycleApi.command(value.notice_id, 'display').then(refresh).catch(() => {
          setErrorCommand('display')
          setError('提醒已呈现，显示回执尚未确认，可点击重新核对。')
        })
      }
    })
    observer.observe(element.current)
    return () => observer.disconnect()
  }, [value.notice_id, value.display_at, refresh])
  const ack = async () => {
    setPending(true); setError(null)
    try { await eventLifecycleApi.command(value.notice_id, 'ack'); refresh() }
    catch { setErrorCommand('ack'); setError('确认结果尚未核对，重试会恢复同一请求。') }
    finally { setPending(false) }
  }
  const display = async () => {
    setPending(true); setError(null)
    try { await eventLifecycleApi.command(value.notice_id, 'display'); refresh() }
    catch { setErrorCommand('display'); setError('显示回执仍未确认，请重试。') }
    finally { setPending(false) }
  }
  return <div className="product-notice" ref={element}>
    <div><strong>{value.subject}</strong><p>{value.body}</p><p className="product-muted">{time(value.created_at)} · {value.ack_at ? `已确认 ${time(value.ack_at)}` : value.display_at ? '已显示，等待确认' : '显示回执待核对'}</p>
      {!value.ack_at && <button type="button" className="button secondary" disabled={pending} onClick={() => void ack()}>{pending ? '正在确认…' : '我已了解这条提醒'}</button>}
      {error && <p role="alert">{error} <button className="button secondary" type="button" disabled={pending} onClick={() => void (errorCommand === 'ack' ? ack() : display())}>重试同一回执</button></p>}
    </div>
  </div>
}

export function EventLifecyclePanel({ eventKey }: { eventKey: string }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['event-lifecycle', eventKey], queryFn: ({ signal }) => eventLifecycleApi.detail(eventKey, signal), retry: false, refetchInterval: 60_000 })
  const refresh = useCallback(() => { void client.invalidateQueries({ queryKey: ['event-lifecycle', eventKey] }) }, [client, eventKey])
  if (query.isLoading) return <p role="status">正在读取公布与跟踪状态…</p>
  if (!query.data) return <section aria-label="公布与后续跟踪"><h3>公布与后续跟踪</h3><p>跟踪记录暂时无法核对。</p><button type="button" className="button secondary" onClick={refresh}>重新读取跟踪记录</button></section>
  const value = query.data
  return <section aria-label="公布与后续跟踪">
    <div className="product-card-head"><h3>公布与后续跟踪</h3><button type="button" className="button secondary" disabled={query.isFetching} onClick={refresh}>刷新跟踪</button></div>
    {query.isError && <p role="alert">本次更新未能核对，下面保留上次成功读取的记录。可刷新重试。</p>}
    <p><strong>{value.realization.state === 'realized' ? '官方结果已核验，情景已激活' : value.realization.state === 'awaiting_release' ? '等待官方公布' : '已到公布时间，等待可核验实际值'}</strong></p>
    {value.realization.known_at && <p>系统确认实际值：{time(value.realization.known_at)}。此前预判仍按原版本保留。</p>}
    {value.realization.state === 'realized' && <p>已对应情景：{scenario(value.realization.scenario_id)}。比较基准为上月前值。</p>}
    {value.realization.metrics.length > 0 && <p>{value.realization.metrics.map(m => `${metrics[m.metric_id] ?? '通胀指标'}：${m.value === null ? '尚缺可比值' : `${Number(m.value).toFixed(4)}%`}`).join('；')}</p>}
    <div className="table-wrap"><table className="data-table" aria-label="事件条件跟踪"><thead><tr><th>资产</th><th>当前空仓</th><th>已有多仓</th></tr></thead><tbody>{value.assets.map(a => <tr key={a.instrument}><th scope="row">{a.instrument === 'BTC-USDT' ? 'BTC' : 'ETH'}</th>{(['flat', 'long'] as const).map(p => {
      const branch = a.positions.find(x => x.position_context === p)
      const inputs = branch?.latest_evaluation?.confirmation_inputs
      return <td key={p}>{branch ? status[branch.state] : '监测尚未登记'}
        {branch?.rule_summary && <details><summary>查看确认条件</summary><p>{branch.rule_summary}</p>
          {inputs?.event_return != null && <p>公布后价格变化 {percent(inputs.event_return)}{inputs.minute_volume_ratio != null ? `；当前分钟成交量为公告前基准的 ${Number(inputs.minute_volume_ratio).toFixed(2)} 倍` : ''}。</p>}
          {branch.strategy_probability_gap && <p className="product-muted">{branch.strategy_probability_gap}</p>}
        </details>}
      </td>
    })}</tr>)}</tbody></table></div>
    <p className="product-muted">条件确认是研究提示。账户持仓和实际成交未接入。</p>
    {value.notices.length > 0 && <><h4>需要关注的提醒</h4>{value.notices.map(n => <Notice key={n.notice_id} value={n} refresh={refresh} />)}</>}
    <details><summary>公布前覆盖与数据状态</summary>
      {value.coverage.length ? <ul>{value.coverage.map(c => <li key={c.slot_kind}>{slots[c.slot_kind] ?? '预定提醒'} · {time(c.due_at)}：{coverage[c.state]}</li>)}</ul> : <p>事前提醒尚未登记，请等待后台更新。</p>}
      <ul>{value.sources.map(s => <li key={s.source_id}>{s.label}：{s.last_success_at ? `最近成功 ${time(s.last_success_at)}` : '尚无成功采集'}{s.error_code ? '，当前有采集缺口' : ''}</li>)}</ul>
    </details>
    <h4>结果回看</h4>
    {!value.outcome || value.outcome.state === 'waiting' ? <p>完整24小时观察截至 {time(value.outcome_due_at)}，当前尚未结算。</p> : <>
      <p>{value.outcome.event_score ? '实际情景与原始事件概率已配对评分。' : '事件结果仍缺必要输入。'}</p>
      {value.outcome.event_score && <details><summary>查看事件概率评分</summary><p>实际情景：{scenario(value.outcome.event_score.label)}；概率误差评分 {Number(value.outcome.event_score.brier).toFixed(4)}；对数损失 {Number(value.outcome.event_score.log_loss).toFixed(4)}。两项评分越低越好，不能换算成操作胜率。</p></details>}
      <ul>{value.outcome.assets.map(a => <li key={a.instrument}>{a.instrument === 'BTC-USDT' ? 'BTC' : 'ETH'}：{outcomeState[a.state] ?? '结果待核对'}。24小时价格变化 {percent(a.return_24h)}；规则纸面净收益 {percent(a.strategy_net_return)}；减险相对持有变化 {percent(a.held_long_reduction_value_fraction)}
        {a.gaps.length > 0 && <details><summary>查看缺口与计算假设</summary><ul>{a.gaps.map(g => <li key={g}>{outcomeGap[g] ?? '该结果仍有待核对的输入或方法约束。'}</li>)}</ul></details>}
      </li>)}</ul>
      {value.outcome.gaps.length > 0 && <p>{value.outcome.gaps.map(g => outcomeGap[g] ?? '结果记录仍有待核对的缺口。').join(' ')}</p>}
      <p className="product-muted">上述为原规则按认证行情回放的结果，包含约定成本和延迟，不代表账户成交。</p>
    </>}
  </section>
}
