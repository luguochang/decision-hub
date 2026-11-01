import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { eventPrevisionsApi, type EventPrevisionDetail, type EventProbabilityEstimate, type EventMacroContext } from '../api/eventPrevisions'
import { ApiRequestError } from '../api/client'
import { EventLifecyclePanel } from './EventLifecyclePanel'

const band = { cooler: '降温', near: '接近前值', hotter: '升温' }
const state = { waiting_inputs: '等待必要数据', pending: '等待计算', running: '正在计算', published: '已保存事前预判', failed: '预判生成失败', missed: '未在公布前完成', not_scheduled: '尚未安排预判' }
const time = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '尚无记录'
const pct = (value: string) => `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value) * 100)}%`
const number = (value: string | null) => value === null ? '未知' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(Number(value))
const scenarioName = (id: string) => {
  const match = /^cpi:headline_(cooler|near|hotter):core_(cooler|near|hotter)$/.exec(id)
  return match ? `总体${band[match[1] as keyof typeof band]}、核心${band[match[2] as keyof typeof band]}` : '所选情景'
}
const methodLanguage: Record<string, string> = {
  historical_four_metric_nowcast_residuals_are_jointly_exchangeable_at_the_same_calendar_stage: '假设历史同期四项通胀预测误差的联合分布可用于当前估计。',
  official_chart_history_was_received_now_and_is_not_authenticated_historical_PIT_vintage: '历史图表是现在取得的资料，不能证明系统当年已经持有并作出预测。',
  past_regimes_are_pooled_with_equal_event_weight_and_may_not_match_current_regime: '历史事件等权合并，未充分区分当前市场环境。',
  nowcast_is_a_model_estimate_not_market_survey_consensus: '即时预测是模型估计，并非市场调查共识。',
  bands_compare_with_prior_month_not_market_surprise_or_asset_direction: '情景比较上月前值，不代表超出市场预期或资产涨跌方向。',
  dirichlet_half_count_is_explicit_joint_category_prior_not_observed_errors: '每格加入半个样本的统计先验，未把它当作实际历史事件。',
  event_probability_does_not_identify_asset_reaction_or_strategy_net_profit_probability: '事件概率不能代替资产反应概率或策略净盈利概率。',
  empirical_support_does_not_model_extremes_beyond_observed_joint_residuals: '该估计无法充分描述历史样本以外的极端结果。',
  continuous_chart_actual_and_BLS_index_derived_prior_are_not_rounded_bulletin_values: '实际值和前值采用指数计算的连续环比，并非公告四舍五入后的显示值。',
  current_HTML_nowcast_center_is_displayed_at_two_decimals_with_half_width_0_005pp: '官网当前预测仅显示两位小数，其显示精度为正负 0.005 个百分点。',
}
methodLanguage['current_HTML_nowcast_center_is_displayed_at_two_decimals_with_half_width_0.005pp'] = methodLanguage.current_HTML_nowcast_center_is_displayed_at_two_decimals_with_half_width_0_005pp
const humanText = (value: string) => (methodLanguage[value] ?? value).replace(/cpi:headline_(?:cooler|near|hotter):core_(?:cooler|near|hotter)/g, scenarioName).replace(/prior_month/g, '上月基准').replace(/reference_period/g, '参考月份').replace(/continuous MoM/g, '连续环比').replace(/snapshot/g, '预测快照').replace(/\.05pp/g, '0.05个百分点').replace(/T0/g, '公布时刻').replace(/(\d+)h\b/g, '$1小时')

function Probability({ title, value }: { title: string; value: EventProbabilityEstimate | null }) {
  return <div><strong>{title}：{value?.value !== null && value?.value !== undefined ? pct(value.value) : '尚无同类样本可估计'}</strong>
    {value && value.events > 0 && <p className="product-muted">小样本平滑估计，尚未校准；{value.events} 次历史事件中 {value.successes} 次符合目标。
      {value.lower_95 !== null && value.upper_95 !== null && <> 历史频率 95% 区间 {pct(value.lower_95)}–{pct(value.upper_95)}。</>}
      {!value.scenario_conditioned && '这是历史 CPI 事件日基准，未针对当前情景。'}</p>}
  </div>
}

const macroState = { unknown: '数据不足，暂不能判断背景方向', headwind: '利率与美元形成压力', tailwind: '利率与美元形成支撑', mixed: '背景信号分化' }
const macroNames = { DGS10: '美国10年期国债名义收益率', DFII10: '美国10年期通胀保值国债实际收益率', DTWEXBGS: '广义贸易加权美元指数（非DXY）' }
const macroGap = (value: string) => {
  if (value === 'daily_background_not_intraday_or_historical_vintage') return '使用日频背景与当前可见数据版本；不是盘中行情，也未认证为当时的历史版本。'
  if (value === 'survey_consensus_and_event_pricing_not_bound') return '尚未接入市场调查共识及事件前市场定价，不能仅凭这些指标判断“超预期”。'
  const [series, reason] = value.split(':')
  const label = macroNames[series as keyof typeof macroNames]
  if (!label) return '仍有待核对的宏观数据缺口。'
  const description: Record<string, string> = { source_not_admitted: '尚无通过核验的来源数据', observation_stale: '观察值已超过新鲜度要求', original_invalid: '来源原件暂时无法核对' }
  return `${label}：${description[reason] ?? '来源状态需要进一步核对'}。`
}

export function EventMacroContextSection({ value }: { value: EventMacroContext | null | undefined }) {
  if (!value) return <section aria-label="已使用的宏观背景"><h4>已使用的宏观背景</h4><p className="product-muted">这份旧版预案没有保存宏观背景，不能据此确认当时已使用利率和美元数据。</p></section>
  const missing = (Object.keys(macroNames) as Array<keyof typeof macroNames>).filter(id => !value.series.some(s => s.series_id === id))
  return <section aria-label="已使用的宏观背景">
    <h4>已使用的宏观背景</h4><p><strong>{macroState[value.state]}</strong>。背景截至 {time(value.as_of)}（北京时间），反映利率与美元的日频环境。</p>
    {value.series.length > 0 && <div className="table-wrap"><table className="data-table" aria-label="事件预案采用的利率与美元数据"><thead><tr><th scope="col">指标</th><th scope="col">观察值</th><th scope="col">最近5个观察间隔变化</th><th scope="col">观察日期</th><th scope="col">入库确认时间（系统可用时间）</th><th scope="col">来源</th></tr></thead><tbody>{value.series.map(s => <tr key={s.series_id}>
      <th scope="row">{macroNames[s.series_id]}</th><td>{number(s.value)}{s.unit === 'percent' ? '%' : '（2006年1月=100）'}</td><td>{number(s.change_5_observations)} {s.unit === 'percent' ? '个百分点' : '指数点'}</td><td>{s.observation_date}</td><td>{time(s.known_at)}</td><td><a href={s.source_url} target="_blank" rel="noreferrer">FRED 官方数据（CSV）</a></td>
    </tr>)}</tbody></table></div>}
    {missing.length > 0 && <p className="product-muted">尚未使用的指标：{missing.map(id => macroNames[id]).join('、')}。</p>}
    <p className="product-muted">入库确认时间是系统能够使用这项数据的时间，不等于精确采集或对外发布时间。</p>
    {value.gaps.length > 0 && <details><summary>宏观数据缺口与使用范围</summary><ul>{value.gaps.map((gap, i) => <li key={i}>{macroGap(gap)}</li>)}</ul></details>}
  </section>
}

function ForecastBody({ value }: { value: EventPrevisionDetail }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [instrument, setInstrument] = useState<'BTC-USDT' | 'ETH-USDT'>('BTC-USDT')
  const [position, setPosition] = useState<'flat' | 'long'>('flat')
  const first = [...value.scenarios].sort((a, b) => Number(b.probability) - Number(a.probability))[0]
  const scenario = value.scenarios.find(s => s.scenario_id === selected) ?? first
  const asset = value.assets.find(a => a.instrument === instrument)
  const plan = asset?.plans.find(p => p.scenario_id === scenario?.scenario_id)
  if (value.probability_status === 'missing') return <div className="product-notice danger"><div><strong>{state[value.event.state]}</strong><p>本事件尚无通过原件核验的事前概率和机会。系统不能把事后研究报告代替这项结果。</p>{value.event.next_attempt_at && <p>下次尝试：{time(value.event.next_attempt_at)}</p>}</div></div>
  return <>
    {new Date(value.as_of).getTime() >= new Date(value.event.scheduled_at).getTime() && <p className="product-notice">事件已到公布时间。以下保留公布前的原始预判，未用事后结果重写。</p>}
    <p>估计截至 {time(value.estimate_as_of)}，于 {time(value.published_at)} 保存。以下比较<strong>上月前值</strong>，并非市场共识或“超预期”。</p>
    <p className="product-muted">总体前值 {number(value.headline_baseline)}%、核心前值 {number(value.core_baseline)}%；接近前值范围为 ±{number(value.tolerance_pp)} 个百分点。使用指数计算的连续环比，未先四舍五入成公告显示值。</p>
    <h3>可能公布什么结果</h3>
    <p className="product-muted">{value.sample_count} 组历史同期误差比较形成的未校准估计。点击情景查看传导与机会；九格合计 100%，显示值可能有舍入差异。</p>
    <table className="data-table" aria-label="CPI 事前联合情景概率"><caption>行：总体通胀；列：核心通胀（均相对上月）</caption><thead><tr><th scope="col">总体 / 核心</th>{(['cooler', 'near', 'hotter'] as const).map(b => <th scope="col" key={b}>{band[b]}</th>)}</tr></thead><tbody>{(['cooler', 'near', 'hotter'] as const).map(h => <tr key={h}><th scope="row">{band[h]}</th>{(['cooler', 'near', 'hotter'] as const).map(c => {
      const cell = value.scenarios.find(s => s.headline_band === h && s.core_band === c)
      return <td key={c}>{cell ? <button type="button" className="button secondary" aria-pressed={scenario?.scenario_id === cell.scenario_id} aria-label={`${scenarioName(cell.scenario_id)} ${pct(cell.probability)}`} onClick={() => setSelected(cell.scenario_id)}>{pct(cell.probability)}</button> : '缺失'}</td>
    })}</tr>)}</tbody></table>
    <p><strong>事件结果概率不能直接当作资产上涨概率或操作胜率。</strong></p>
    <div className="product-card-head"><h3>{scenario ? scenarioName(scenario.scenario_id) : '情景'}：传导与机会</h3><label>分析资产 <select aria-label="事件分析资产" value={instrument} onChange={e => setInstrument(e.target.value as typeof instrument)}><option value="BTC-USDT">BTC</option><option value="ETH-USDT">ETH</option></select></label></div>
    {!asset || asset.state === 'missing' || asset.state === 'outdated' ? <p className="product-muted">{asset?.state === 'outdated' ? '上一版机会尚未随当前事件预测更新，当前机会仍未完成。' : '该资产尚未形成可核验的事件传导和机会预案。这部分仍未完成。'}</p> : <>
      {asset.state === 'expired' && <div className="product-notice danger"><div><strong>市场模型已到复估时间</strong><p>以下保留上一版预案，不能作为当前有效机会。应在 {time(asset.refresh_required_at)} 前更新。</p></div></div>}
      <EventMacroContextSection value={asset.macro_context} />
      <details><summary>历史 CPI 事件日基准（不等于当前情景概率）</summary><Probability title="事件后 24 小时价格上涨" value={asset.asset_estimate} /><Probability title="正向价量确认策略扣费后盈利" value={asset.net_profit_estimate} /><Probability title="已触发入场条件后的净盈利" value={asset.net_profit_given_entry_estimate} /></details>
      {plan && <>
        <h4>根因链与反向可能</h4><ol>{plan.main_chain.map((text, i) => <li key={i}>{humanText(text)}</li>)}</ol><p>反向机制：{humanText(plan.counter_mechanism)}</p>
        <Probability title="在该情景下资产上涨" value={plan.asset_estimate} />
        {plan.current_confirmation ? <><strong>当前公告锚定策略的扣费后胜率：尚未校准</strong><p className="product-muted">{plan.current_confirmation.probability_gap}</p></> : <Probability title="在该情景下原版策略扣费后盈利" value={plan.net_profit_estimate} />}
        <label>仓位情景 <select aria-label="仓位情景" value={position} onChange={e => setPosition(e.target.value as typeof position)}><option value="flat">当前空仓</option><option value="long">已持多仓</option></select></label>
        {plan.current_confirmation ? <>
          <h4>{position === 'flat' ? '当前自动确认与入场条件' : '当前持仓减险条件'}</h4>
          <p>{position === 'flat' ? plan.current_confirmation.flat_entry : plan.current_confirmation.long_entry}</p>
          {position === 'flat' && <><h4>退出条件</h4><p>{plan.current_confirmation.flat_exit}</p></>}
          <h4>失效与结束条件</h4><p>{position === 'flat' ? plan.current_confirmation.flat_invalidation : plan.current_confirmation.long_invalidation}</p>
        </> : <p>这份历史预案尚未绑定当前自动确认规则，请核对下方跟踪状态。</p>}
        <details><summary>保存的原版规则（历史对照）</summary>
          <p>以下是原快照中的滚动指标规则；它与当前公告锚定监测分别记账。</p>
          <Probability title="原版策略扣费后盈利" value={plan.net_profit_estimate} />
          <h4>{position === 'flat' ? '原版条件入场预案' : '原版减仓预案'}</h4>
          {(position === 'flat' ? plan.flat_entry : plan.held_long_entry).map((text, i) => <p key={i}>{humanText(text)}</p>)}
          {position === 'flat' && <><h4>原版退出条件</h4>{plan.flat_exit.map((text, i) => <p key={i}>{humanText(text)}</p>)}</>}
          <h4>原版失效条件</h4>{(position === 'flat' ? plan.flat_invalidation : plan.held_long_invalidation).map((text, i) => <p key={i}>{humanText(text)}</p>)}
        </details>
        <p>入场观察：{time(asset.entry_valid_from)} 至 {time(asset.entry_valid_until)}；最晚退出：{time(asset.exit_valid_until)}。</p>
        <p className="product-muted">这是公布前保存的条件预案。实际情景、条件确认与提醒见下方“公布与后续跟踪”。</p>
        <details><summary>激活前提和数据缺口</summary><ul>{[...plan.activation_required, ...asset.gaps].map((text, i) => <li key={i}>{humanText(text)}</li>)}</ul></details>
      </>}
    </>}
    <details><summary>估计方法与快照记录</summary><ul>{value.assumptions.map((text, i) => <li key={i}>{humanText(text)}</li>)}</ul><p>快照：{value.snapshot_ref}</p><p>内容指纹：{value.snapshot_hash}</p></details>
  </>
}

export function EventPrevisionPanel({ eventKey: requestedEventKey = null }: { eventKey?: string | null }) {
  const client = useQueryClient()
  const [chosen, setChosen] = useState<string | null>(null)
  const listing = useQuery({ queryKey: ['event-previsions'], queryFn: ({ signal }) => eventPrevisionsApi.list(signal), retry: false, refetchInterval: 60_000 })
  const eventKey = chosen ?? requestedEventKey ?? listing.data?.events[0]?.event_key ?? null
  const detail = useQuery({ queryKey: ['event-prevision-detail', eventKey], queryFn: ({ signal }) => eventPrevisionsApi.detail(eventKey!, signal), enabled: Boolean(eventKey), retry: false, refetchInterval: 60_000 })
  const events = [...(listing.data?.events ?? [])]
  if (detail.data && !events.some(e => e.event_key === detail.data.event.event_key)) events.unshift(detail.data.event)
  const refresh = () => { void client.invalidateQueries({ queryKey: ['event-previsions'] }); void client.invalidateQueries({ queryKey: ['event-prevision-detail'] }); void client.invalidateQueries({ queryKey: ['event-lifecycle'] }) }
  return <section className="product-card" aria-label="核心事件事前预判">
    <div className="product-card-head"><div><h2>核心事件事前预判</h2><p className="product-muted">公布前看可能结果、市场传导与条件机会；公布后核对结果。</p></div><button type="button" className="button secondary" disabled={listing.isFetching || detail.isFetching} onClick={refresh}>刷新预判</button></div>
    {listing.isLoading && <p role="status">正在读取事件列表…</p>}
    {listing.isError && <div className="product-notice danger"><div><strong>事件列表暂时无法核对</strong><p>请刷新重试；指定事件的详情会单独核对，当前列表覆盖尚未确认。</p></div></div>}
    <>
      {eventKey ? <>
        <label>事件 <select aria-label="选择核心事件" value={eventKey} onChange={e => setChosen(e.target.value)}>{!events.some(e => e.event_key === eventKey) && <option value={eventKey}>正在核对指定事件</option>}{events.map(e => <option key={e.event_key} value={e.event_key}>{e.title} · {e.reference_period ?? '参考月份待确认'} · {time(e.scheduled_at)}</option>)}</select></label>
        <p>计划公布：{time(events.find(e => e.event_key === eventKey)?.scheduled_at ?? null)}（北京时间）</p>
        <a className="product-link" href={`#/market?event_key=${encodeURIComponent(eventKey)}`}>该事件的固定回看链接</a>
        {detail.isError && <div className="product-notice danger"><div><strong>{detail.error instanceof ApiRequestError && detail.error.kind === 'timeout' ? '预判读取超过等待时间' : '这项事件的预判暂时无法核对'}</strong><p>{detail.data ? '下方保留上次已读取的版本，本次更新尚未确认。' : '请点击“刷新预判”重新核对。'}{detail.error instanceof ApiRequestError && detail.error.kind === 'network' && '当前网络连接中断。'}</p></div></div>}
        {detail.isLoading && !detail.data ? <p role="status">正在核对完整原件…</p> : detail.data && <ForecastBody key={`forecast:${detail.data.event.event_key}`} value={detail.data} />}
        <EventLifecyclePanel key={`lifecycle:${eventKey}`} eventKey={eventKey} />
      </> : !listing.isLoading && !listing.isError && <p className="product-muted">尚无已登记的未来事件预判。核心事件的自动事前覆盖仍有缺口，已保存报告不能证明这项能力已完成。</p>}
      {listing.data?.uncovered_families.includes('fomc') && <p className="product-muted">美联储议息会议的利率概率、指引情景与机会尚未接入；日历中的会议记录不等于事前预测。</p>}
    </>
  </section>
}
