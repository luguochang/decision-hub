import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiRequestError } from '../api/client'
import { readHistoryForecast } from '../api/historyForecast'
import { HISTORY_SCOPES, type HistoryProductStatusV2 } from '../api/historyStatus'

const time = (value: string | null) => value ? `${new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
}).format(new Date(value))} UTC` : '未取得'
const percent = (value: string) => (Number(value) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
const probabilities = [
  ['baseline_long', '历史基线 · 做多逆向下跌'], ['baseline_short', '历史基线 · 做空逆向上涨'],
  ['conditional_long', '当前条件 · 做多逆向下跌'], ['conditional_short', '当前条件 · 做空逆向上涨'],
] as const

function readFailure(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (error.status === 401 || (error.status === 403 && error.message === 'history_owner_session_rejected')) return '本机验证已失效，请重新验证并刷新状态。'
    if (error.status === 403) return '这条记录没有读取授权，当前不展示风险估计。重新验证不会补齐读取授权，请检查本机历史能力配置。'
    if (error.status === 409) return '这条记录的模型或分钟原件未通过核验，当前无法展示风险估计。'
    if (error.kind === 'timeout') return '原件核验超时，当前结果尚未确认。不会发起新的预测。'
    if (error.kind === 'network') return '读取原件时连接中断，当前结果尚未确认。'
  }
  return '返回的风险记录与所选资产或观察截点无法核对，当前不展示概率。'
}

/** Explicit original read; metadata refresh never starts this expensive read or produces a forecast. */
export function HistoryForecastRecord({ forecast, statusStale, authRevision, onAuthenticationRequired }: {
  forecast: HistoryProductStatusV2['forecast']; statusStale: boolean; authRevision: number;
  onAuthenticationRequired?: () => void;
}) {
  const [requested, setRequested] = useState(false)
  const query = useQuery({ queryKey: ['history-forecast-original-v2', forecast.forecast_id, forecast.scope_id,
    forecast.cutoff_at, authRevision], queryFn: ({ signal }) => readHistoryForecast(forecast, signal),
    enabled: requested && !statusStale, retry: false, staleTime: 0, refetchOnWindowFocus: false })
  // A failed recheck removes numeric presentation, including expired or revoked authority.
  const original = !requested || query.isFetching || query.isError || statusStale ? undefined : query.data
  const canRead = Boolean(forecast.forecast_id && forecast.scope_id && forecast.cutoff_at)
  const load = () => {
    if (requested) void query.refetch()
    else setRequested(true)
  }
  return <section className="product-card history-section" aria-label="未来24小时风险估计">
    <h2>未来 24 小时逆向波动风险记录</h2>
    {canRead ? <>
      <p>最近保存记录：{HISTORY_SCOPES[forecast.scope_id as keyof typeof HISTORY_SCOPES]} · 观察截点 {time(forecast.cutoff_at)}。记录存在不代表已核验可用。</p>
      <button className="button secondary" disabled={query.isFetching || statusStale} onClick={load}>
        {query.isFetching ? '正在核验模型与分钟原件…' : original ? '重新核验本条记录' : '核验并查看风险估计'}
      </button>
      {!requested && <p>已有预测记录仍待原件核验，当前不展示概率。点击后读取这条已保存记录，不重新生产预测；核验最多等待2分钟，离开页面取消读取。</p>}
      {statusStale && <p role="status">当前状态刷新失败，请先刷新状态，再核验这条记录。</p>}
    </> : <p>{forecast.blocked_reason_label || '尚未产生经过原件核对的预测。采集记录和模型登记不能代替预测结果。'}</p>}
    {query.isError && <p role="alert">{readFailure(query.error)}</p>}
    {query.isError && query.error instanceof ApiRequestError && (query.error.status === 401
      || (query.error.status === 403 && query.error.message === 'history_owner_session_rejected'))
      && onAuthenticationRequired && <button className="button secondary" onClick={onAuthenticationRequired}>恢复本机验证后重新核验</button>}
    {original && <>
      <p className="history-proof">{original.fixture_mode ? '测试数据：不代表真实市场预测。' : '本条记录已通过模型及分钟原件核验。'}金融有效性尚未通过，历史时点可用性尚未通过。</p>
      <p>本次核验时刻：{time(new Date(query.dataUpdatedAt).toISOString())} · 截点 {time(original.cutoff_at)} · 观察范围为之后 24 小时。</p>
      {original.frequencies.map(item => <div className="history-frequency" key={item.threshold}>
        <h3>逆向价格变动超过 {item.threshold === '0.02' ? '2%' : '5%'}</h3>
        <dl className="history-risk-values">{probabilities.map(([key, label]) => <div key={key}>
          <dt>{label}</dt><dd>{percent(item.probabilities[key])}%</dd>
          <span className="history-probability-bar" aria-hidden="true"><span style={{ width: `${Number(item.probabilities[key]) * 100}%` }} /></span>
        </div>)}</dl>
        <p>历史基线 {item.baseline_n} 天；当前条件 {item.conditional_n} 天。百分比为历史样本频率估计。</p>
      </div>)}
      <p>做多逆向下跌、做空逆向上涨分别计算。这里只估计期间逆向波动超过阈值的风险，不能据此判断收盘涨跌、预计盈亏或个人清算价；未计杠杆、手续费、资金费和滑点。</p>
    </>}
  </section>
}
