// Presentation only. Original model output and source titles remain in the audit view.
export function researchDisplayTitle(title: string) {
  if (/Federal Reserve.*economic projections/i.test(title)) return '美联储经济预测更新'
  if (/Barr.*Economic Conditions and Monetary Policy/i.test(title)) return '巴尔：经济状况与货币政策'
  return title
}
const reportTerms: Record<string, string> = {
  volume_ratio_20: '20 期相对成交量', uncalibrated: '尚未校准',
  market_narrative: '最新公开信息', market_regime_context: '日线趋势背景',
  usd_reserve_balances: '美元准备金余额', real_yield_10y: '美国10年期实际收益率',
  long: '偏多', accepted: '已采纳', candidate: '候选',
  stale: '已过时', fresh: '未过期', action: '研究方向',
  derivatives: '衍生品', evidence: '证据', bid: '买方', ask: '卖方',
  inflation_compensation_10y: '10年期通胀补偿',
  'orderbook imbalance': '买卖盘失衡度', imbalance: '失衡度', orderbook: '订单簿',
  'order-book': '订单簿', return: '回报', leverage: '杠杆', crowding: '拥挤程度',
  beta: '对整体市场波动的敏感性',
  'million USD': '百万美元', million_usd: '百万美元',
  macro_transmission: '宏观因素传导', open_interest_delta: '未平仓量变化',
  '10Y real yield': '十年期实际收益率', '5d': '过去5天',
  'current series': '当前序列', audited_aggregator: '已核验的聚合来源',
  actual: '实际发布值', surprise: '相对预期差', revision: '修订记录',
  'audited route': '已核验的信息获取路径', 'durable EventWatch': '已持久保存的事件跟踪记录',
  sma_20: '20日均价', sma_60: '60日均价', horizon: '研究时间窗口',
  subjective_probability: '主观概率', trigger: '触发条件', invalidation: '失效条件',
  'audited acquisition route': '已核验的信息获取路径',
  'EventWatch/event-window unavailable': '事件跟踪与事件窗口数据不可用',
  'delayed/audited aggregator': '延迟更新的已核验聚合来源',
  period_close: '期间收盘价',
  'spot trend and momentum': '现货趋势与动量',
  'leveraged positioning': '杠杆持仓结构',
  'rebound probability': '反弹可能性',
  'perpetual squeeze risk and spot upside extension': '永续合约挤压风险与现货上行动量',
  'ask-heavy': '卖盘较重', 'bid-heavy': '买盘较重',
  event_window_baseline: '事件窗口基线', liquidation_map: '强平分布',
  'event_id/window offsets': '事件编号和窗口偏移',
  probability_status: '概率状态', perpetual: '永续合约', spot: '现货',
  crypto: '加密资产',
}
const registeredTermPattern = new RegExp(`\\b(?:${Object.keys(reportTerms)
  .sort((left, right) => right.length - left.length)
  .map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '[ \\t]+'))
  .join('|')})\\b`, 'g')
function displayedPricePrecision(text: string) {
  // Only explicit price/average labels. Tiny values and unrelated numbers stay exact.
  return text.replace(/(?<![a-zA-Z0-9_])((?:sma_(?:20|60)|(?:20|60)\s*日均价|日线收盘(?:价)?|收盘价|标记价格|指数价格)(?:\s*[:=：]\s*|\s+))(-?\d+\.\d{7,})(?![a-zA-Z0-9_.])/g,
    (original, label: string, raw: string) => {
      const value = Number(raw)
      return Number.isFinite(value) && Math.abs(value) >= 1 && Number.isSafeInteger(Math.trunc(value))
        ? `${label}约 ${value.toFixed(2)}` : original
    })
}
function registeredReportText(text: string) {
  // Known prose terms only. Keep quoted/code spans and explicit configuration values.
  const prose = text.split(/(`[^`]*`|"[^"\n]*"|'[^'\n]*')/g)
    .map((part, index) => index % 2 ? part : displayedPricePrecision(part).replace(/\b10Y[ \t]+real[ \t]+yield(?=\s*=)/g, '十年期实际收益率')).join('')
  return prose.split(/(`[^`]*`|"[^"\n]*"|'[^'\n]*'|\b[a-zA-Z][a-zA-Z0-9_]*\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s;,，；]+))/g)
    .map((part, index) => index % 2 ? part
      .replace(/^outlook\s*=\s*"range"$/, '日线规则倾向为区间/趋势分歧')
      .replace(/^vintage_proven\s*=\s*false$/, '历史修订版本未核验')
      .replace(/^financial_qualified\s*=\s*false(?=$|[^a-zA-Z0-9_])/, '金融效果资格未通过')
      .replace(/^budget_qualified\s*=\s*false(?=$|[^a-zA-Z0-9_])/, '研究预算资格未通过')
      .replace(/^sma_(20|60)\s*=\s*/, (_match, days: string) => `${days}日均价：`)
      : part.replace(registeredTermPattern, term => reportTerms[term.replace(/[ \t]+/g, ' ')])).join('')
}
const terms: Record<string, string> = {
  event_identity: '事件身份', policy_or_data_delta: '政策或数据变化', expectation_pricing: '预期定价',
  no_trade: '未形成方向', neutral: '中性观察', funding: '资金费率', basis: '期现基差',
  book_imbalance: '买卖盘失衡度', orderbook_imbalance: '买卖盘失衡度', venue: '交易平台',
  mark_price: '标记价格', index_price: '指数价格', mark: '标记价格', index: '指数价格', close: '收盘价', proxy: '代理指标', Greed: '贪婪区间',
  funding_rate: '资金费率', open_interest: '未平仓量',
  proxy_kind: '代理指标类型', crowding_signal: '拥挤度指标', liquidation: '强平', contracts: '张合约',
  ask_depth: '卖方深度', bid_depth: '买方深度', 'ask-heavy': '卖盘较重', 'bid-heavy': '买盘较重',
  OI: '未平仓量', US10Y: '美国十年期国债收益率', DXY: '美元指数', SPX: '标普指数', VIX: '波动率指数',
  SMA20: '20 日均价', SMA60: '60 日均价', bp: '基点', bps: '基点', Fed: '美联储', '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时',
}
export function researchDisplayText(text: string) {
  // Explicit original-record prose is an audit value, not a translated summary.
  if (/^\s*(?:原记录|原始记录|原文)\s*[:：]/.test(text)) return text
  // Apply only the newly registered phrases to prose, keeping opaque URL/ID spans.
  // The historical display pipeline below remains unchanged, including its audit-ID folding.
  const prose = /[\u3400-\u9fff]/.test(text) ? text.split(/(https?:\/\/[^\s<>"']+|\b(?:[a-zA-Z][a-zA-Z0-9_]*_(?:id|ref)|id)"?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s;,，；]+))/g).map((part, index) => index % 2 ? part : registeredReportText(part)
    .replace(/\borderbook[ \t]+imbalance\b/g, terms.orderbook_imbalance)
    .replace(/(回执为)(bullish|bearish)(?![a-zA-Z0-9_])/g, (_match, field: string, direction: string) => `${field}${direction === 'bullish' ? '偏多' : '偏空'}`)
    .replace(/(标记为|声明为)stale(?![a-zA-Z0-9_])/g, '$1已过时')
    .replace(/\baccepted(?=事实)/g, '已采纳')
    .replace(/\bdelta\b/g, '变化量')
    .replace(/\brisk-off\b/g, '避险情绪')
  ).join('') : text
  return prose.split(/(https?:\/\/[^\s<>"']+|\b(?:[a-zA-Z][a-zA-Z0-9_]*_(?:id|ref)|id)"?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s;,，；]+))/g)
    .map((part, index) => index % 2 ? part : part.replace(/\bmark\s*低于\s*index\b/g, '标记价格低于指数价格').replace(/\bFear and Greed\b/g, '恐惧与贪婪指数').replace(/\b(long|short)(?=\s*(?:[\/，。、；：）)]|研究候选|候选|方向|观点|判断|持有|倾向))/g, term => term === 'long' ? '偏多' : '偏空').replace(/\bev_[a-f0-9]{16,}\b/g, '〔依据见原文〕').replace(/\b(?:orderbook_imbalance|venue|mark_price|index_price|mark|index|close|book_imbalance|proxy_kind|crowding_signal|liquidation|contracts|proxy|Greed|funding_rate|open_interest|event_identity|policy_or_data_delta|expectation_pricing|no_trade|neutral|funding|basis|ask_depth|bid_depth|ask-heavy|bid-heavy|OI|US10Y|DXY|SPX|VIX|SMA20|SMA60|bps|bp|Fed|30m|24h|72h)\b/g, term => terms[term])).join('')
}
