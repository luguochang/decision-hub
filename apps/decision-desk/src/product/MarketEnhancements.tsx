import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { enhancementErrorMessage, marketEnhancementsApi, type EnhancementView } from '../api/marketEnhancements'
import './risk.css'

type RouteView = EnhancementView['routes'][number]
const names: Record<RouteView['route'], string> = {
  MACRO_STATE_V1: '宏观环境', CROWDING_V1: '持仓拥挤度', EVENT_SURPRISE_V1: '事件预期差',
}
const states: Record<RouteView['data_status'], string> = {
  not_registered: '尚未登记', awaiting_data: '等待采集', available: '已有描述数据',
  partial: '数据尚不完整', unavailable: '缺少可核验数据',
}
function reason(code: string) {
  if (code === 'enhancement_not_registered') return 'Worker 尚未登记这条候选路线。'
  if (code === 'enhancement_inputs_not_collected') return '已登记候选，尚未保存可核验输入。'
  if (code === 'macro_observations_unavailable') return '尚无已接受、归档且带首次可得时间的宏观数据。'
  if (code === 'macro_release_history_insufficient') return '历史观察不足，无法比较宏观变化。'
  if (code.includes('vintage') || code.includes('public_time')) return '缺少历史版本或精确发布时间，目前只作描述。'
  if (code.includes('crowding')) return '缺少可比的跨市场持仓、资金费率及清算原件。'
  if (code.includes('consensus') || code.includes('event') || code.includes('surprise')) return '尚缺事件实际值、事前预期及对应可得时间。'
  if (code === 'enhancement_fred_originals_excluded') return '部分宏观原件未通过核对，已排除。'
  return '原件、时间或覆盖范围仍需补齐。'
}
function time(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}
function featureName(name: string) {
  const series = name.startsWith('DGS2') ? '美国两年期国债收益率' : name.startsWith('DGS10') ? '美国十年期国债收益率' : name.startsWith('DTWEXBGS') ? '广义美元指数' : '补充指标'
  return series + (name.endsWith('change_5_release') ? '（五次观察变化）' : name.endsWith('change_1_release') ? '（上次观察变化）' : '')
}
function featureValue(item: RouteView['features'][number]) {
  if (item.value === null) return '资料不足'
  const value = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(item.value)
  if (item.unit === 'yield_percent') return value + (item.name.includes('change_') ? ' 个百分点' : '%')
  if (item.unit === 'index') return value
  return '已有描述值（详见审计）'
}

export function MarketEnhancements({ scopeId }: { scopeId: string }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ['market-enhancements', scopeId],
    queryFn: ({ signal }) => marketEnhancementsApi.summary(scopeId, signal),
    enabled: open && Boolean(scopeId), retry: false,
  })
  return <details className="product-card" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><strong>补充研究数据</strong><span className="product-muted"> · 宏观、拥挤度与事件预期</span></summary>
    {open && <div>
      <p className="product-muted">三条路线独立收集并留存原件。当前仅为候选，没有通过预测优势验证，不提供交易胜率。</p>
      <button className="button secondary" disabled={query.isFetching} onClick={() => void query.refetch()}>{query.isFetching ? '正在核对补充数据' : '刷新补充数据'}</button>
      {query.error && <p className="product-notice danger" role="alert">{enhancementErrorMessage(query.error)}{query.data && '下方已有记录仅供参照。'}</p>}
      {query.isPending && <p className="product-muted">正在读取当前资产已保存的数据…</p>}
      {query.data?.routes.map(item => <section className="risk-monitor-row" key={item.route} aria-label={names[item.route]}>
        <div><h3>{names[item.route]}</h3><span className="risk-badge risk-unknown">{states[item.data_status]}</span>
          {[...new Set(item.missing_reasons.map(reason))].map(message => <p className="product-muted" key={message}>{message}</p>)}
          {item.observation_date && <p className="product-muted">最新观察日期：{item.observation_date}；{item.source_known_at && `本机首次可得：${time(item.source_known_at)}`}。观察日期不等于发布时间。</p>}
          {item.features.filter(feature => feature.value !== null).map(feature => <p key={feature.name}>{featureName(feature.name)}：{featureValue(feature)}</p>)}
          {item.scan_truncated && <p className="risk-warning">本轮读取已达到上限，覆盖尚不完整。</p>}
          <details className="risk-audit"><summary>原件与资格审计</summary>
            <p>当前没有晋级资格。已存原件 {item.original_count} 条；排除 {item.excluded_source_count} 条。</p>
            <p>读取时核验保存内容的哈希，未在本次页面请求中重新下载或复核全部原始数据。</p>
            <p>登记标识：{item.registration_id ?? '尚无'}；回执标识：{item.receipt_id ?? '尚无'}。</p>
            {item.saved_at && <p>保存时间：{time(item.saved_at)}</p>}
          </details>
        </div>
      </section>)}
    </div>}
  </details>
}
