import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { ProbabilityEstimateV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import { perpetualAdviceApi, type PerpetualAdviceSelection, type PerpetualAdviceView } from '../api/perpetualAdvice'
import { settingsApi } from '../api/decisionSettings'
import { ApiRequestError } from '../api/client'
import { perpetualForecastResultApi, type PerpetualForecastResultView } from '../api/perpetualForecastResult'
import './PerpetualAdvicePanel.css'
import { PerpetualPlanWatchPanel } from './PerpetualPlanWatchPanel'
import { PerpetualManualPaperPanel } from './PerpetualManualPaperPanel'
import { PerpetualForecastBasisPanel } from './PerpetualForecastBasisPanel'

const statuses: Record<PerpetualAdviceView['status'], string> = { available: '已发布的四小时研究方案', expired: '这版四小时研究方案已到期',
  invalidated: '这版建议的条件已失效',
  not_started: '当前保存设置尚未发起研究', researching: '研究进行中，尚未形成建议', failed: '本次研究未完成', awaiting_publication: '等待建议发布', settings_changed: '设置已变化，请读取当前保存版本' }
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const pct = (value: string) => `${(Number(value) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`
const outcomeNames: Record<string, string> = { up: '上涨', down: '下跌', neutral: '横盘' }
function Estimate({ value }: { value: ProbabilityEstimateV1 }) {
  return <>{value.probabilities === null ? <p>未估计，保持未知。</p> : <ul>{value.probabilities.map((item: { outcome_id: string; value: string }) => <li key={item.outcome_id}>{outcomeNames[item.outcome_id] ?? item.outcome_id}：{pct(item.value)}</li>)}</ul>}
    <small>{value.kind === 'calibrated' && value.qualification === 'qualified' ? '原预测记录为已校准；本方案仍仅供研究试行。' : '原研究估计；未获得可用于交易的校准资格。'}</small></>
}
function EventScenarioPlans({ value }: { value: PerpetualAdviceView }) {
  const event = value.event_scenario_plan
  if (!event) return null
  const plan = event.plan, selected = event.selection
  const statusNames = { selected_preapproved_branch: '已匹配事前复核方案', pending_evidence: '等待必要证据',
    unsupported_fill_distribution: '条件成交分布尚未支持', invalidated: '这份条件方案已失效',
    uncovered_requires_research: '实际情况超出已复核范围，需要重新研究' }
  const actionNames: Record<string, string> = { open_long: '开多', open_short: '开空', no_change: '维持当前状态', reduce_half: '减半', close: '平仓' }
  const reasonNames: Record<string, string> = {
    authenticated_realization_unavailable: '还没有与这份事件定义一致的已认证结果。',
    required_realization_metrics_unavailable: '事件结果仍缺少必要指标。',
    required_condition_unknown_or_conflicting: '必要条件的证据尚未确认或存在冲突。',
    required_quote_unavailable: '还没有覆盖目标起点和当前时点的有效行情原件。',
    current_account_original_unknown: '当前账户或仓位原件尚未完成核对。',
    conditional_entry_fill_distribution_unavailable: '限价或触发入场缺少自己的成交分布。',
    elapsed_time_or_price_bridge_outside_reviewed_domain: '观察时间或价格变化已超出事前复核范围。',
    material_economic_domain_changed: '成本、账户、仓位或其他必要经济输入已经变化。',
    scenario_branch_missing_or_conflicting: '这次结果没有唯一对应的已复核情景方案。',
    event_actual_adapter_unsupported: '这类结果尚无适用的认证解析规则。',
    event_selection_target_start_or_current_close_missing: '缺少目标起点或当前时点的已确认收盘价。',
    review_rejected_or_expired: '原复核未通过或适用期已经结束。',
    confirmed_condition_invalidation: '已认证证据触发了事前声明的失效条件。',
    branch_expired: '该情景方案的适用期已经结束。',
    reviewed_target_not_started: '这份条件方案的固定目标窗口尚未开始。',
  }
  const active = event.current_state === 'active'
  const chosen = selected?.branch_ref ? plan.branches.find(branch => branch.branch_ref.hash === selected.branch_ref!.hash) : undefined
  return <section aria-label="事件事前情景方案"><h4>事件事前情景方案</h4>
    <p>事件：{plan.event.family} · {plan.event.stage_id}；计划时间：{time(plan.event.scheduled_at)}。事前冻结：{time(plan.as_of)}；条件方案复核有效至 {time(event.review_scope.valid_until)}。</p>
    <p>{event.current_state === 'expired' ? '这份情景方案的适用期已结束。' : event.current_state === 'invalidated' ? '这份情景方案已有失效记录。' : selected ? statusNames[selected.status] : '事前方案已保存，等待已认证结果和必要证据。'}</p>
    {chosen && selected?.selected_action_id && <p>{active ? '当前匹配' : '历史匹配'}情景：{chosen.scenario_id}；事前复核条件首选：{actionNames[selected.selected_action_id] ?? selected.selected_action_id}；假设单位预期净收益 {selected.unit_expected_net_if_assumed_fills_usdt} USDT。</p>}
    <h5>事件发生概率</h5><Estimate value={plan.occurrence} />
    <h5>事件发生后的结果概率 p</h5><Estimate value={plan.outcome_given_occurrence} />
    <p>下方每个 q 是对应事件结果下的市场方向概率。它与上方事件结果概率分别冻结；方向概率不提供成交率或盈亏幅度。</p>
    {plan.branches.map(branch => <details key={branch.branch_id} open={branch.branch_ref.hash === chosen?.branch_ref.hash}>
      <summary>情景 {branch.scenario_id}{branch.branch_ref.hash === chosen?.branch_ref.hash ? ' · 已匹配' : ''}</summary>
      <h5>该情景下的市场方向概率 q</h5><Estimate value={branch.market_distribution.direction_distribution} />
      <p>固定窗口：{time(branch.target.window_start)} 至 {time(branch.target.window_end)}；条件适用至 {time(branch.valid_until)}。</p>
      {(['flat', 'long', 'short'] as const).map(position => {
        const comparison = branch.comparisons_by_position[position].comparison
        return <section key={position}><h5>{position === 'flat' ? '假设空仓' : position === 'long' ? '假设已有多仓' : '假设已有空头仓位'}</h5>
          <table><thead><tr><th>方案</th><th>假设单位预期净收益（USDT）</th></tr></thead><tbody>{comparison.candidate_results.map(row =>
            <tr key={row.candidate_id}><th scope="row">{actionNames[row.candidate_id] ?? row.candidate_id}{row.candidate_id === comparison.selected_action_id ? ' · 条件首选' : ''}</th><td>{row.unit_expected_net_if_assumed_fills_usdt}</td></tr>)}</tbody></table></section>
      })}
    </details>)}
    {selected && selected.unknown_reasons.length > 0 && <p>{Array.from(new Set(selected.unknown_reasons.map(reason => reasonNames[reason]).filter(Boolean))).join(' ') || '仍有输入或假设需要核对，详见限制记录。'}</p>}
    <p>这些是原假设下的研究单位比较。真实成交率、账户可行性与可执行张数保持未知；匹配到条件首选不会授权模拟或真实下单。</p>
    {plan.unknown_reasons.length > 0 && <p>部分情景或边际结果未覆盖，已经保留原有概率与可支持的条件方案。</p>}
    <details><summary>情景方案原件与限制记录</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
  </section>
}
function Advice({ value }: { value: PerpetualAdviceView }) {
  const c = value.comparison!, t = value.target!, cost = value.cost_assumptions!, account = value.context!.account_snapshot!, position = value.context!.position_context!
  const candidates = [...c.candidate_results].sort((a, b) => ['long', 'short', 'flat'].indexOf(a.target_side) - ['long', 'short', 'flat'].indexOf(b.target_side))
  const selected = candidates.find(row => row.candidate_id === c.selected_action_id)
  const actionName = (row: (typeof candidates)[number]) => row.action === 'no_change' ? '不变' : row.action === 'open' ? row.target_side === 'long' ? '开多' : '开空' : row.action === 'close' ? '平仓' : '减仓'
  return <>
    {selected && <section className="perpetual-advice-choice" aria-label={value.status === 'invalidated' ? '原研究入选方案' : '当前首选方案'}>
      <h4>{value.status === 'invalidated' ? '原研究首选' : '当前首选'}：{selected.action === 'no_change' ? '维持当前状态' : actionName(selected)}</h4>
      <p>这是本次成本、路径假设和风险设置下入选的方案；每 {c.unit_notional_usdt} USDT 比较单位的预期净收益为 {selected.unit_expected_net_if_assumed_fills_usdt} USDT。</p>
      <p>先看下方多空比较，再核对<a href={`#/report?run=${encodeURIComponent(value.run_id!)}`}>判断依据、反方观点和失效条件</a>。比较单位不代表应投入的金额。</p>
    </section>}
    <p>时间窗：<time dateTime={t.window_start}>{time(t.window_start)}</time> 至 <time dateTime={t.window_end}>{time(t.window_end)}</time>（{t.horizon.replace('h', '')} 小时预测）。</p>
    <p>原预测时点：{time(value.frozen_forecast!.forecast_as_of)}；发布时间：{time(value.revision!.published_at)}。交易价一分钟收盘口径，方向阈值 ±{pct(t.threshold_fraction)}。</p>
    <p className="perpetual-advice-limit">研究试行 · 未通过金融资格核验 · 仅限模拟协议参考。账户可行性、可执行张数、真实成交概率与强平概率仍未知。</p>
    {position.state === 'unknown' && <p>仓位未知：此处仅按假设空仓作比较，不代表你的真实仓位或账户操作；持仓分支未覆盖。</p>}
    <p>本次研究冻结的账户原件（不表示当前账本余额）：账户权益：{account.equity === null ? '未知' : `${account.equity} USDT`}；可用保证金：{account.available_margin === null ? '未知' : `${account.available_margin} USDT`}（{account.source === 'unknown' ? '来源未知' : account.account_kind === 'paper' ? '模拟账本' : account.source === 'user_declared' ? '用户自报' : '账户原件'}）。</p>
    <h4>三个方案的假设单位比较</h4>
    <p>{c.unit_notional_usdt} USDT 仅是比较单位，并非账户余额、投入金额或下单大小。净值为假设路径与假设成交下的预期净收益。</p>
    <div className="perpetual-advice-table"><table><thead><tr><th>方案</th><th>单位预期净收益（USDT）</th><th>净盈利概率</th><th>净持平概率</th><th>净亏损概率</th></tr></thead><tbody>{candidates.map(row => {
      const label = actionName(row)
      const probabilities = row.outcome.conditional_on_fill
      return <tr key={row.candidate_id}><th scope="row">{label}{row.candidate_id === c.selected_action_id && <small>研究比较入选</small>}</th><td>{row.unit_expected_net_if_assumed_fills_usdt}</td>
        <td>{probabilities ? pct(probabilities.net_profit) : '未知'}</td><td>{probabilities ? pct(probabilities.zero) : '未知'}</td><td>{probabilities ? pct(probabilities.net_loss) : '未知'}</td></tr>
    })}</tbody></table></div>
    <p>表中概率对应原{t.horizon.replace('h', '')}小时预测窗口终点，并以假设成交和原固定成本为前提。手动金额、止损、止盈或提前退出未另行计算盈利概率。不成交比例和机会级预期净收益未估计。</p>
    <div className="perpetual-advice-probabilities"><section><h4>事件概率</h4><h5>事件发生</h5><Estimate value={value.event_probability!.occurrence_distribution} /><h5>事件发生后的结果</h5><Estimate value={value.event_probability!.outcome_given_occurrence} /></section>
      <section><h4>市场方向概率</h4><Estimate value={value.market_distribution!.direction_distribution} /><p>保留原预测的事件条件；方向概率不会自动换算成净收益概率。</p></section></div>
    <PerpetualForecastBasisPanel basis={value.forecast_basis} />
    <details><summary>成本假设与限制</summary><p>假设 taker 费率：{pct(cost.assumed_taker_rate)}；每边额外滑点：{pct(cost.assumed_slippage_fraction_per_leg)}。当前账户真实费率未核验。</p>
      <p>每 {cost.unit_notional_usdt} USDT 参考单位：入场固定成本 {cost.base_cost_policy.entry_trade_cost_usdt} USDT，退出固定成本 {cost.base_cost_policy.exit_trade_cost_usdt} USDT。退出同按固定参考金额估算，未按未来成交价改变名义金额。</p>
      <p>BASE 资金费现金假设：{cost.base_cost_policy.long_funding_cash_usdt} USDT；这是显式零资金费假设，实际未来资金费未知，不表示已有结算覆盖。</p>
      <p>成本原件另声明双倍交易成本与正负资金费敏感性；此卡尚未展示敏感性重算结果。</p></details>
    <p><a href={`#/report?run=${encodeURIComponent(value.run_id!)}`}>追溯原研究报告</a></p>
    <details><summary>原件与已记录限制（{value.gaps.length} 项）</summary><pre>{JSON.stringify({ run_id: value.run_id, report_id: value.report_id, snapshot: value.snapshot!.id, revision: value.revision!.revision_id,
      gaps: value.gaps, comparison_unknowns: c.unknown_reasons, cost_ref: value.cost_assumptions_ref, forecast: value.frozen_forecast }, null, 2)}</pre></details>
  </>
}
function resultReadError(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (error.status === 401) return '本机身份已失效，请重新连接后读取到期结果。'
    if (error.status === 403) return '当前身份无权读取这份结果。'
    if (error.status === 409) return '到期结果原件无法核对；没有改用其他记录。'
    if (error.status === 404) return '这次研究没有可读取的永续到期结果。'
    if (error.kind === 'timeout') return '到期结果读取超时；尚未确认结果，可稍后手动重读。'
    if (error.kind === 'network') return '网络中断，未能读取到期结果。'
  }
  return '到期结果原件无法核对，请重新核对身份与原研究。'
}
function ForecastResult({ advice }: { advice: PerpetualAdviceView }) {
  const [value, setValue] = useState<PerpetualForecastResultView | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const current = useRef<AbortController | null>(null), epoch = useRef(0)
  useEffect(() => () => { epoch.current++; current.current?.abort() }, [])
  async function read() {
    const generation = ++epoch.current
    current.current?.abort()
    const controller = new AbortController(); current.current = controller
    setValue(null); setError(''); setBusy(true)
    try {
      const result = await perpetualForecastResultApi.read(advice, controller.signal)
      if (generation === epoch.current && !controller.signal.aborted) setValue(result)
    } catch (failure) {
      if (generation === epoch.current && !controller.signal.aborted) setError(resultReadError(failure))
    } finally {
      if (generation === epoch.current && !controller.signal.aborted) setBusy(false)
    }
  }
  return <section aria-label="预测到期结果"><h4>预测到期结果</h4>
    <p>这是预测到期评估，不是模拟或真实交易盈亏，也不会改变金融资格。</p>
    <button type="button" disabled={busy} onClick={() => void read()}>读取到期结果</button>
    {busy && <p role="status">正在核对到期结果原件…</p>}
    {error && <p role="alert">{error}</p>}
    {value && <>
      {value.status === 'not_due' && <p role="status">预测窗口尚未结束。目标结束时间：{time(value.target.window_end)}。</p>}
      {value.status === 'pending' && <p role="status">到期结果尚未形成，后台仍可能在处理；可稍后手动重读。此次读取不会生成结果。</p>}
      {value.status === 'unsupported' && <p role="status">当前期限暂不支持到期评分；这不表示结果正在等待生成。</p>}
      {value.status === 'observed' && <>
        <h5>事前市场方向概率</h5><Estimate value={value.result.direction_estimate} />
        <p>原预测时点：{time(value.result.direction_estimate.as_of)}。</p>
        <p>实际分类：{outcomeNames[value.result.actual_category]}；到期实际收益率：{pct(value.result.actual_return_fraction)}（目标交易价一分钟收盘口径）。</p>
        <p>Brier 评分：{value.result.multiclass_brier ?? '未评分（见记录限制）'}。越低越好；单个样本不是胜率，也不能证明预测已校准。</p>
        {value.schema_version === 'perpetual-forecast-result-view.v2' && <>
          <p>Log loss 评分：{value.result.log_loss.status === 'finite' ? value.result.log_loss.value
            : value.result.log_loss.status === 'infinite' ? '无穷（原预测给实际结果的概率为 0）'
              : '未评分（方向概率未知）'}。越低越好。</p>
          <h5>逐样本可靠性记录</h5>
          {value.result.reliability_observations.length > 0
            ? <ul>{value.result.reliability_observations.map(row => <li key={row.outcome_id}>
              {outcomeNames[row.outcome_id]}：原预测概率 {row.probability}；实际发生：{row.observed === 1 ? '是' : '否'}。
            </li>)}</ul> : <p>未形成可靠性记录（方向概率未知）。</p>}
          {value.result.base_rate_comparison && <p>与历史基准率对照：{value.result.base_rate_comparison.brier_skill !== null
            ? `基准率 Brier ${value.result.base_rate_comparison.base_rate_brier}；相对技能 ${value.result.base_rate_comparison.brier_skill}（正数表示优于基准率，单样本不代表稳定优势）`
            : value.result.base_rate_comparison.reason === 'base_rate_unavailable' ? '本次研究没有可用的历史基准率' : '方向概率未知，无法对照'}。</p>}
          <p>分层：{value.result.strata.asset} · {value.result.strata.horizon}；事件族：{value.result.strata.event_family ?? '未知'}；市场状态：{value.result.strata.market_regime ?? '未知'}。</p>
          <p>这些是单个到期样本的预测与实际记录，不能证明预测已校准；累计成熟样本后才能评估可靠性。</p>
        </>}
        <p>结果已知时间：{time(value.result.known_at)}；记录时间：{time(value.result.recorded_at)}。</p>
        {value.result.gaps.length > 0 && <p>该样本存在记录限制，原因见下方折叠原件。</p>}
        <small>研究试行 · 未通过金融资格核验 · 未作为交易结果。</small>
      </>}
      <p>本次读取时间：{time(value.read_at)}。</p>
      <details><summary>原件与记录限制</summary><p>此处展示结果投影与原件引用；完整经验原件和行情已由服务端核验。</p><pre>{JSON.stringify(value, null, 2)}</pre></details>
    </>}
  </section>
}
function signedOwner() { try { return settingsApi.session()?.owner_id ?? null } catch { return null } }
export function PerpetualAdvicePanel({ selection }: { selection: PerpetualAdviceSelection | null }) {
  const [owner, setOwner] = useState(signedOwner)
  const [sessionEpoch, setSessionEpoch] = useState(0)
  useEffect(() => { const changed = () => { setOwner(signedOwner()); setSessionEpoch(value => value + 1) }; window.addEventListener('decision-hub-settings-session-changed', changed); return () => window.removeEventListener('decision-hub-settings-session-changed', changed) }, [])
  const permitted = Boolean(selection && selection.owner_id === owner)
  const query = useQuery({ queryKey: ['perpetual-advice', owner, selection], queryFn: ({ signal }) => perpetualAdviceApi.current(selection!, signal), enabled: permitted,
    retry: false, refetchOnWindowFocus: false })
  const value = permitted ? query.data : undefined
  const hours = (selection?.horizon ?? '4h').replace('h', '')
  return <section className="perpetual-advice-panel" aria-label={`已发布永续${hours}小时建议`}><h3>BTC/ETH 永续事前建议 · {hours} 小时</h3>
    {!permitted ? <p>请连接本机身份并读取已保存的账户设置。</p> : query.isPending ? <p role="status">正在读取这份设置的建议原件…</p> : query.isError ? <p role="alert">建议原件暂不可用或身份未通过核对。没有使用其他账户、旧协议或演示建议。</p> : value && <>
      <h4 role="status">{statuses[value.status]}</h4>
      {value.status === 'invalidated' && <p>服务端已拒绝这版旧建议的新增模拟开仓或加仓；已有模拟仓位的保护继续有效。原概率与路径保持冻结，新建议须等待经审核后正式发布的后续方案。</p>}
      {(value.status === 'available' || value.status === 'expired' || value.status === 'invalidated') ? <><Advice value={value} /><PerpetualPlanWatchPanel key={value.revision!.revision_id} selection={selection!} value={value} /></> : <p>此处尚无可展示的已发布建议；可在研究入口核对原请求与进度。</p>}
      <EventScenarioPlans value={value} />
      {value.status === 'expired' && value.run_id && <ForecastResult key={JSON.stringify([owner, sessionEpoch, selection, value.run_id, value.snapshot?.bindings.final_target])} advice={value} />}
      <small>读取时间：{time(value.updated_at)}</small>
    </>}
    {permitted && <PerpetualManualPaperPanel key={JSON.stringify(selection!.partition)} value={value ?? null} selection={selection!} />}
    {permitted && <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取建议原件</button>}
  </section>
}
