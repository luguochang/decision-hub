import { useDetailFocus } from './useDetailFocus'
import { formattedNumeric, numericFactDisplay, numericPresentation, temporalPresentation } from './factPresentation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FactVersionView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { CaptureReadback } from './SourceHistoryPanel'
import { AssetBackLink, AssetError, AssetPagination, assetTime, instrumentName, readOffset, routeChange, type AssetNavigate } from './assetFormat'

export const factNames: Record<string, string> = {
  level: '指标水平', price: '现货价格', volume: '成交量', basis: '期现基差', funding_rate: '资金费率',
  index_price: '指数价格', mark_price: '标记价格', open_interest: '未平仓量', return_5d: '近 5 日收益',
  change_5d: '近 5 日变化', open_interest_delta: '未平仓量变化', period_close: '区间收盘价', row_count: '有效数据行',
  ask_depth: '卖方深度', bid_depth: '买方深度', book_imbalance: '买卖盘失衡', crowding_signal: '拥挤程度',
  realized_vol_20_pct: '20 日实现波动率', return_20_pct: '20 日收益', rolling_high_20: '20 日最高价',
  rolling_low_20: '20 日最低价', sma_20: '20 日均价', sma_60: '60 日均价', volume_ratio_20: '20 日量比',
  period_open: '区间开盘价', return_pct: '区间收益', volume_sum: '区间总成交量', event_actor: '事件主体',
  event_time: '事件时间', revision_status: '修订状态',
  path: '区间价格路径', return_30m: '近30分钟涨跌幅', return_24h: '近24小时涨跌幅', realized_vol_30m: '30分钟已实现波动率',
}
const factSources: Record<string, string> = {
  'binance-public': '币安公开数据', 'fed.monetary_policy': '美联储货币政策',
  'fred.current_macro': 'FRED 宏观数据库（圣路易斯联储）', 'fred-public': 'FRED 宏观数据库', 'gateio-derivatives-public': 'Gate 衍生品公开数据',
  'gateio-orderbook-public': 'Gate 盘口公开数据', 'gateio-public': 'Gate 公开数据',
  'htx-derivatives-public': 'HTX 衍生品公开数据', 'htx-orderbook-public': 'HTX 盘口公开数据',
  'huobi-public': 'Huobi 公开数据', 'okx-orderbook-public': 'OKX 盘口公开数据',
  'okx-public': 'OKX 公开数据', 'yahoo-finance-public': '雅虎财经公开数据',
}
const archiveName = (status: string) => ({ captured: '原始采集已保存', reference_only: '保留原资料引用', legacy_unarchived: '历史原文未归档', unarchived: '原文未归档', missing: '归档信息缺失' }[status] ?? '归档状态未知')
export const factName = (field: string) => factNames[field] ?? '其他研究指标'
export const factValue = (value: number | string | null) => {
  if (value === null) return '未提供数值'
  const numeric = typeof value === 'number' ? value : /^[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?$/i.test(value.trim()) ? Number(value) : NaN
  return Number.isFinite(numeric) ? numeric.toLocaleString('zh-CN', numeric !== 0 && Math.abs(numeric) < 0.001 ? { maximumSignificantDigits: 8 } : { maximumFractionDigits: 6 }) : String(value).length > 150 ? `${String(value).slice(0, 150)}…` : String(value)
}
export const factUnit = (unit: string) => ({ contracts: '张合约', yield_percent: '收益率（%）', text: '文本', datetime: '日期时间', index: '指数点', percent: '%', pct: '%', bps: '基点', ratio: '比值', rate: '比率', count: '条', usdt: 'USDT', million_usd: '百万美元', usd: '美元', btc: 'BTC' }[unit.toLowerCase()] ?? unit)

function displayedFactValue(fact: FactVersionView['fact']) {
  const numeric = numericPresentation(fact)
  if (numeric !== null) return numeric.value
  if (fact.field === 'revision_status' && typeof fact.value === 'string') {
    return ({ original: '原始发布', revised: '已修订' } as Record<string, string>)[fact.value] ?? factValue(fact.value)
  }
  if (fact.field === 'event_time' && fact.unit.toLowerCase() === 'datetime' && typeof fact.value === 'string'
    && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/i.test(fact.value)
    && Number.isFinite(Date.parse(fact.value))
    && new Date(`${fact.value.slice(0, 10)}T00:00:00Z`).toISOString().slice(0, 10) === fact.value.slice(0, 10)) {
    return assetTime(fact.value)
  }
  return factValue(fact.value)
}

const displayedFactUnit = (fact: FactVersionView['fact']) => numericPresentation(fact)?.unit ?? factUnit(fact.unit)
const displayedFactTime = (fact: FactVersionView['fact']) => {
  const time = temporalPresentation(fact)
  return time.period !== null || time.primaryValue === '来源观测期未记录' ? time.primaryValue : assetTime(time.primaryValue)
}

const factInstrumentLabel = (instrument: string, family: string) => instrument === 'GOLD' && family === 'cross_asset.commodity' ? '黄金相关指标' : instrumentName(instrument, family)

export function FactVersionChart({ versions }: { versions: FactVersionView[] }) {
  const points = versions.flatMap(item => {
    const numeric = numericFactDisplay(item.fact), time = Date.parse(item.known_at)
    return numeric !== null && Number.isFinite(time) ? [{ item, ...numeric, time }] : []
  }).sort((a, b) => a.time - b.time)
  if (points.length < 2) return <p className="product-muted">这个指标当前不足两个可比较的数值版本，暂不绘制曲线。</p>
  const unit = points[0].unit
  if (points.some(point => point.unit !== unit)) return <p className="product-muted">这些版本的单位口径不一致，暂不绘制曲线。</p>
  const values = points.map(p => p.value), times = points.map(p => p.time)
  const min = Math.min(...values), max = Math.max(...values), from = times[0], to = times.at(-1)!
  const coordinates = points.map(p => `${55 + (p.time - from) / (to - from || 1) * 650},${145 - (p.value - min) / (max - min || 1) * 105}`).join(' ')
  return <figure className="asset-chart"><figcaption>同口径指标的已知版本变化 · {points.length} 个数值版本 · {factUnit(unit)}</figcaption><svg viewBox="0 0 760 190" role="img" aria-label={`已知版本变化，最小 ${formattedNumeric(min, unit)}，最大 ${formattedNumeric(max, unit)} ${factUnit(unit)}；非连续行情曲线`}><line x1="55" y1="145" x2="710" y2="145" stroke="#d8e0e5" /><polyline points={coordinates} fill="none" stroke="#277b83" strokeWidth="2.5" /><text x="4" y="40">{formattedNumeric(max, unit)}</text><text x="4" y="145">{formattedNumeric(min, unit)}</text><text x="55" y="176">{new Date(from).toLocaleDateString('zh-CN')}</text><text x="630" y="176">{new Date(to).toLocaleDateString('zh-CN')}</text></svg><p>横轴为系统当时已知时间。仅比较相同指标、来源、品种、单位及窗口；修订和重复观察均保留，不补齐缺失行情。</p></figure>
}

const factInstrument = (item: FactVersionView) => item.fact.instrument ? factInstrumentLabel(item.fact.instrument, item.fact.metric_family) : item.fact.metric_family === 'event.identity' ? '事件信息' : '未限定品种'
const factVenue = (item: FactVersionView) => item.fact.venue ? ({ okx: 'OKX', htx: 'HTX', huobi: 'Huobi', gateio: 'Gate', binance: 'Binance', coinex: 'CoinEx' }[item.fact.venue] ?? item.fact.venue) : null

function FactDetail({ item, asOf, params, onNavigate }: { item: FactVersionView; asOf: string; params: URLSearchParams; onNavigate: AssetNavigate }) {
  const time = temporalPresentation(item.fact)
  const historyOffset = readOffset(params, 'history_offset')
  const refsOffset = item.run_inputs.length ? Math.min(readOffset(params, 'refs_offset'), Math.floor((item.run_inputs.length - 1) / 20) * 20) : 0
  const page = (key: string, value: number) => onNavigate('facts', routeChange(params, { as_of: asOf, [key]: String(value) }))
  const [showInput, setShowInput] = useState(false)
  const history = useQuery({ queryKey: ['asset-fact-history', item.semantic_key, asOf, historyOffset], queryFn: () => assets.facts({ semantic_key: item.semantic_key, as_of: asOf, limit: 100, offset: historyOffset }), retry: false })
  const sourceRegistryLocator = item.structured_payload_ref?.startsWith('source-registry://') ?? false
  const registryDocument = /^source-registry:\/\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(item.structured_payload_ref ?? '') && /^document:\/\/sha256\/[a-f0-9]{64}$/.test(item.content_ref ?? '')
  const inputRef = item.structured_payload_ref || item.content_ref
  const canReadInput = sourceRegistryLocator ? registryDocument : Boolean(inputRef)
  const originalTextInput = sourceRegistryLocator ? registryDocument : /^document:\/\/sha256\/[a-f0-9]{64}$/.test(inputRef ?? '')
  const input = useQuery({ queryKey: ['asset-fact-input', item.fact_version_id], queryFn: () => assets.factInput(item.fact_version_id), enabled: showInput && canReadInput, retry: false })
  const compatible = history.data?.items.filter(value => value.semantic_key === item.semantic_key && Date.parse(value.known_at) <= Date.parse(asOf) && Date.parse(value.fact.observed_at) <= Date.parse(asOf)) ?? []
  return <><div className="asset-detail-title"><strong>{factName(item.fact.field)}：{displayedFactValue(item.fact)} {displayedFactUnit(item.fact)}</strong><span>{factInstrument(item)}{factVenue(item) && ` · ${factVenue(item)}`}</span></div>
    <dl className="asset-facts"><div><dt>{time.primaryLabel}</dt><dd>{displayedFactTime(item.fact)}</dd></div>{time.sourceUpdatedAt !== null && <div><dt>{time.sourceUpdatedLabel}</dt><dd>{assetTime(time.sourceUpdatedAt)}</dd></div>}{time.period !== null || item.fact.metric_family.startsWith('macro.') ? <div><dt>{time.observedLabel}</dt><dd>{assetTime(item.fact.observed_at)}</dd></div> : null}<div><dt>系统当时已知</dt><dd>{assetTime(item.known_at)}</dd></div><div><dt>版本登记时间</dt><dd>{assetTime(item.recorded_at)}</dd></div><div><dt>原始资料</dt><dd>{archiveName(item.archive_status)}</dd></div></dl>
    {time.note && <p className="product-muted">{time.note}</p>}
    {item.fact.field === 'realized_vol_30m' && <p className="product-muted">根据30个一分钟对数收益计算的区间已实现波动率，未年化；不能直接当作预测涨跌幅。</p>}
    {item.fact.field === 'return_24h' && <p className="product-muted">比较当前与24小时前的已核对收盘端点；不表示中间每根K线均完整。</p>}
    {item.fact.field === 'path' && <p className="product-muted">摘要来自留存路径记录，原始数值和完整结构可在“字段与版本审计”查看。摘要不授予模型或交易资格。</p>}
    {history.isPending ? <p>读取同口径指标版本…</p> : history.isError ? <AssetError retry={() => void history.refetch()} /> : <FactVersionChart versions={compatible} />}
    {(history.data?.total ?? 0) > 100 && <section aria-label="指标版本分页"><p className="product-muted">曲线仅显示当前页数值版本；翻页查看更早记录，不连接跨页区间。</p><AssetPagination total={history.data!.total} offset={history.data?.offset ?? historyOffset} limit={100} onChange={value => page('history_offset', value)} /></section>}
    <h3>回到原始材料</h3><p>以下文件来自同一次能力调用，保留采集时间及内容校验；不代表每个文件都单独支持本指标。</p>
    {item.raw_captures.length ? <ul className="asset-captures">{item.raw_captures.map((capture, index) => <li key={capture.capture_id}><CaptureReadback captureId={capture.capture_id} index={index + 1} /><span>{assetTime(capture.completed_at)} · {capture.status_code === null ? '请求未完成' : `HTTP ${capture.status_code}`}</span></li>)}</ul> : <p className="product-muted">这条记录没有可回读的 HTTP 原始响应。历史缺失不会补造成新采集。</p>}
    {sourceRegistryLocator && !canReadInput && <p className="product-muted">{item.content_ref ? '该版本的来源登记或原文归档引用格式不受支持，暂不能回读。' : '该版本保留了来源登记引用，未保存可回读的原文归档。'}不会重新抓取当前网页补作旧原文。</p>}
    {canReadInput && <><button className="button secondary" onClick={() => setShowInput(!showInput)}>{showInput ? originalTextInput ? '收起留存原文' : '收起归档输入' : originalTextInput ? '查看留存原文' : '核验归档输入'}</button>{showInput && (input.isPending ? <p>核对归档文件与哈希…</p> : input.isError ? <AssetError retry={() => void input.refetch()} /> : <details open><summary>{input.data.verified ? input.data.kind === 'original_text' ? '原文内容哈希已核验' : '归档哈希已核验 · 查看输入清单' : '留存材料 · 尚未完成哈希核验'}</summary>{input.data.kind === 'original_text' && typeof input.data.payload.text === 'string' ? <><pre className="asset-json">{input.data.payload.text}</pre><details><summary>原文归档字段与哈希</summary><pre className="asset-json">{JSON.stringify(input.data.payload, null, 2)}</pre></details></> : <pre className="asset-json">{JSON.stringify(input.data.payload, null, 2)}</pre>}</details>)}</>}
    <h3>在哪些研究中使用</h3>{item.run_inputs.length ? <div className="asset-ref-list">{item.run_inputs.slice(refsOffset, refsOffset + 20).map((ref, index) => <button className="product-link" key={`${ref.run_id}:${ref.fact_id}`} onClick={() => onNavigate('report', { run: ref.run_id, back: `#/facts?${new URLSearchParams({ ...Object.fromEntries(params), as_of: asOf })}` })}>研究 {refsOffset + index + 1} · {ref.use_mode === 'reused' ? '采用已有版本' : ref.use_mode === 'legacy' ? '历史引用' : '首次采集'}<ArrowRight size={13} /></button>)}</div> : <p>尚无关联研究引用。</p>}
    {item.run_inputs.length > 20 && <section aria-label="关联研究分页"><AssetPagination total={item.run_inputs.length} offset={refsOffset} limit={20} onChange={value => page('refs_offset', value)} /></section>}
    <details><summary>字段与版本审计</summary><p>来源：{item.fact.source_id} · 原字段：{item.fact.field} · 本指标共 {item.run_inputs.length} 次引用</p><code>{item.fact_version_id}</code><p>解析版本：{item.parser_version}</p><pre className="asset-json">{JSON.stringify(item.fact, null, 2)}</pre></details>
  </>
}

export function FactsPage({ params, onNavigate }: { params: URLSearchParams; onNavigate: AssetNavigate }) {
  const queryClient = useQueryClient()
  const [initialAsOf] = useState(() => new Date().toISOString())
  const asOf = params.get('as_of') || initialAsOf
  const validAsOf = Number.isFinite(Date.parse(asOf)) && /(?:Z|[+-]\d{2}:\d{2})$/i.test(asOf)
  const offset = readOffset(params), field = params.get('field') || '', version = params.get('version')
  const source = params.get('source_id') || '', factInstrumentFilter = params.get('fact_instrument') || '', metricFamily = params.get('metric_family') || ''
  const list = useQuery({ queryKey: ['asset-facts', asOf, offset, field, source, factInstrumentFilter, metricFamily], queryFn: () => assets.facts({ as_of: asOf, field: field || undefined, source_id: source || undefined, fact_instrument: factInstrumentFilter || undefined, metric_family: metricFamily || undefined, limit: 20, offset }), enabled: validAsOf, retry: false })
  const options = useQuery({ queryKey: ['asset-fact-options', asOf], queryFn: () => assets.factOptions(asOf), enabled: validAsOf, retry: false })
  const detail = useQuery({ queryKey: ['asset-fact', version], queryFn: () => assets.fact(version!), enabled: Boolean(version) && validAsOf, retry: false })
  const detailRef = useDetailFocus(version, !detail.isPending)
  const change = (values: Record<string, string | undefined>) => onNavigate('facts', routeChange(params, { as_of: asOf, ...(Object.hasOwn(values, 'version') ? { history_offset: undefined, refs_offset: undefined } : {}), ...values }))
  useEffect(() => {
    if (list.isSuccess && list.data.offset !== offset) change({ offset: String(list.data.offset) })
  }, [list.isSuccess, list.data?.offset, offset])
  const resetFilters = () => change({ field: undefined, source_id: undefined, fact_instrument: undefined, metric_family: undefined, offset: undefined, version: undefined })
  const selectedInstrument = factInstrumentFilter ? JSON.stringify([factInstrumentFilter, metricFamily]) : ''
  const instrumentOptions = options.data?.instruments ?? []
  const instrumentOptionLabel = (instrument: string, family: string) => `${factInstrumentLabel(instrument, family)} · ${({ 'crypto.spot': '现货', 'crypto.spot.history': '现货历史', 'crypto.derivatives': '衍生品', 'macro.cross_asset': '宏观与跨资产', 'macro.rates': '国债利率', 'macro.usd': '美元指标', 'macro.real_yield': '实际收益率', 'macro.inflation_compensation': '市场通胀补偿', 'macro.reserve_balances': '银行准备金余额', 'cross_asset.equity': '股票指数', 'cross_asset.volatility': '波动率', 'cross_asset.commodity': '商品指标（口径待核验）', 'event.identity': '事件信息' } as Record<string, string>)[family] ?? family}${instrumentOptions.filter(item => item.metric_family === family && factInstrumentLabel(item.instrument, family) === factInstrumentLabel(instrument, family)).length > 1 ? ` · ${instrument}` : ''}`
  const fields = [...new Set([...(options.data?.fields ?? Object.keys(factNames)), ...(field ? [field] : [])])]
  const sources = [...new Set([...(options.data?.sources ?? []), ...(source ? [source] : [])])]
  const filtered = Boolean(field || source || factInstrumentFilter || metricFamily)
  const refresh = () => {
    void list.refetch(); void options.refetch()
    if (version) { void detail.refetch(); void queryClient.invalidateQueries({ queryKey: ['asset-fact-history'] }) }
  }
  return <div className="content-wrap product-page facts-page"><AssetBackLink back={params.get('back')} /><div className="product-heading"><div><span className="product-kicker">已经积累的数据</span><h1>指标与依据</h1><p>查到一个数值，看到它来自哪里，以及当时哪些研究使用了它。</p></div><div className="asset-filter-actions"><button className="button secondary" disabled={!validAsOf || list.isFetching} onClick={refresh}><RefreshCw size={15} />{list.isFetching ? '正在读取…' : '刷新当前筛选'}</button><button className="button secondary" onClick={() => change({ as_of: new Date().toISOString(), offset: undefined, version: undefined })}>回到最新数据</button></div></div>
    <form key={asOf} className="asset-filters" onSubmit={event => { event.preventDefault(); const raw = new FormData(event.currentTarget).get('asOf'); const parsed = new Date(String(raw)); if (!Number.isNaN(parsed.getTime())) change({ as_of: parsed.toISOString(), offset: undefined, version: undefined }) }}>
      <label>品种与指标类别<select aria-label="品种与指标类别" value={selectedInstrument} onChange={e => { const [instrument, family] = e.target.value ? JSON.parse(e.target.value) as string[] : []; change({ fact_instrument: instrument, metric_family: family, offset: undefined, version: undefined }) }}><option value="">全部品种与类别</option>{selectedInstrument && !instrumentOptions.some(item => item.instrument === factInstrumentFilter && item.metric_family === metricFamily) && <option value={selectedInstrument}>{instrumentOptionLabel(factInstrumentFilter, metricFamily)}（当前条件）</option>}{instrumentOptions.map(item => <option key={JSON.stringify([item.instrument, item.metric_family])} value={JSON.stringify([item.instrument, item.metric_family])}>{instrumentOptionLabel(item.instrument, item.metric_family)}</option>)}</select></label>
      <label>指标<select value={field} onChange={e => change({ field: e.target.value || undefined, offset: undefined, version: undefined })}><option value="">全部指标</option>{fields.map(key => <option key={key} value={key}>{factNames[key] ?? '其他研究指标'}</option>)}</select></label>
      <label>数据来源<select value={source} onChange={e => change({ source_id: e.target.value || undefined, offset: undefined, version: undefined })}><option value="">全部来源</option>{sources.map(key => <option key={key} value={key}>{factSources[key] ?? `其他来源（${key}）`}</option>)}</select></label>
      <label>当时已知截止时间<input type="datetime-local" name="asOf" required step="1" defaultValue={validAsOf ? new Date(Date.parse(asOf) - new Date(asOf).getTimezoneOffset() * 60000).toISOString().slice(0, 19) : ''} /></label><button className="button secondary" type="submit">查看当时的数据</button>{filtered && <button type="button" className="button secondary" onClick={resetFilters}>清除筛选</button>}
    </form>
    {!validAsOf && <div className="product-notice danger" role="alert"><strong>截止时间无效</strong><p>请选择有效时间并点击“查看当时的数据”，或回到最新数据。没有自动替换成其他时间的记录。</p></div>}
    {validAsOf && <><p className="product-muted">截止：{assetTime(asOf)}。选项来自这个时点已知的实际记录；品种按原始报价口径区分，不合并现货与衍生品。</p>{options.isError && <div className="product-notice warning" role="alert"><p>筛选选项暂时无法读取，已选条件仍保留。</p><button className="button secondary" onClick={() => void options.refetch()}>重新读取筛选选项</button></div>}
    {version && <section ref={detailRef} tabIndex={-1} className="product-card asset-detail"><div className="product-card-head"><h2>指标详情与来源</h2><button className="button secondary" onClick={() => change({ version: undefined })}>收起详情</button></div>{detail.isPending ? <p>读取这个版本…</p> : detail.isError ? <AssetError retry={() => void detail.refetch()} /> : <><p className="product-muted">详情是指定版本；列表和曲线按上方截止时间过滤。</p><FactDetail key={version} item={detail.data} asOf={asOf} params={params} onNavigate={onNavigate} /></>}</section>}
    <section className="product-card"><div className="product-card-head"><h2>已保存的指标版本</h2><span>{list.isError ? '读取失败' : list.data ? `${list.data.total.toLocaleString()} 项` : '读取中'}</span></div><p className="product-muted">同一指标可能多次观察或修订。旧版本保留，晚到的修订不会出现在更早的“当时已知”列表。{list.dataUpdatedAt > 0 && ` 本次读取于 ${assetTime(new Date(list.dataUpdatedAt).toISOString())}。`}</p>{list.isPending ? <p>读取指标记录…</p> : list.isError ? <AssetError retry={() => void list.refetch()} /> : <>{list.data.items.length === 0 ? <p className="product-muted" role="status">当前筛选与截止时间下没有已保存的指标版本。可清除筛选或调整时间；没有记录不代表指标为零。</p> : <div className="table-wrap"><table className="asset-table"><thead><tr><th>指标 / 品种</th><th>数值 / 单位</th><th>指标时间</th><th>系统当时已知</th><th>资料留存</th><th>依据</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.fact_version_id}><td data-label="指标 / 品种">{factName(item.fact.field)}<small>{factInstrument(item)}{factVenue(item) && ` · ${factVenue(item)}`}</small></td><td data-label="数值 / 单位">{displayedFactValue(item.fact)}<small>{displayedFactUnit(item.fact)}</small></td><td data-label="指标时间">{displayedFactTime(item.fact)}<small>{temporalPresentation(item.fact).primaryLabel}</small></td><td data-label="系统当时已知">{assetTime(item.known_at)}</td><td data-label="资料留存">{archiveName(item.archive_status)}</td><td data-label="依据"><button className="product-link" onClick={() => change({ version: item.fact_version_id })}>查看来源</button></td></tr>)}</tbody></table></div>}<AssetPagination total={list.data.total} offset={list.data.offset} limit={20} onChange={value => change({ offset: String(value), version: undefined })} /></>}</section></>}
  </div>
}
