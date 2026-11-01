import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiRequestError } from '../api/client'
import { readHistoryForecastIndex } from '../api/historyForecastIndex'
import type { HistoryProductStatusV2 } from '../api/historyStatus'
import { HistoryForecastRecord } from './HistoryForecastRecord'

type Forecast = HistoryProductStatusV2['forecast']
type Asset = 'BTC' | 'ETH'
const names = { BTC: '比特币 BTC', ETH: '以太坊 ETH' } as const

function failure(error: unknown) {
  if (error instanceof ApiRequestError) {
    if ([401, 403].includes(error.status ?? 0)) return '本机验证已失效或资产未获得读取授权，请重新验证并刷新状态。'
    if (error.status === 503) return '服务器尚未配置资产预测记录列表，当前无法核对已保存结果。'
    if (error.status === 409) return '资产记录的身份或时点未通过核对，当前不展示概率。'
    if (error.kind === 'timeout') return '资产记录列表读取超时，请稍后刷新列表。'
  }
  return '资产记录列表无法核对，当前不展示概率。'
}

export function HistoryForecastSelector({ fallbackForecast, statusStale, authRevision, statusAsOf }: {
  fallbackForecast: Forecast; statusStale: boolean; authRevision: number; statusAsOf: string;
}) {
  const [selected, setSelected] = useState<Asset>(fallbackForecast.scope_id?.includes(':ETH-USDT:') ? 'ETH' : 'BTC')
  const query = useQuery({ queryKey: ['history-forecast-index-v2', authRevision, statusAsOf],
    queryFn: ({ signal }) => readHistoryForecastIndex(signal), enabled: !statusStale,
    retry: false, refetchOnWindowFocus: false })
  const legacy = query.error instanceof ApiRequestError && query.error.status === 404
  const item = !query.isError ? query.data?.scopes.find(row => row.asset === selected) : undefined
  const legacyAsset: Asset = fallbackForecast.scope_id?.includes(':ETH-USDT:') ? 'ETH' : 'BTC'
  const forecast: Forecast | undefined = item ? {
    state: item.state === 'verification_required' ? 'verification_required' : 'not_generated',
    forecast_id: item.forecast_id, scope_id: item.forecast_id ? item.scope_id : null,
    cutoff_at: item.cutoff_at, threshold: null, baseline_n: null, conditional_n: null,
    probabilities: null, blocked_reason_label: item.reason_label,
  } : legacy && selected === legacyAsset ? fallbackForecast : undefined
  const readBlocked = statusStale || query.isFetching || query.isError && !legacy
  return <>
    <section className="product-card history-section" aria-label="选择资产预测记录">
      <div className="history-hourly-heading"><div><h2>选择已保存的风险记录</h2>
        <p>分别查看比特币和以太坊已保存的结果。选择资产只切换记录；点击核验后才读取该条原件。</p></div>
        <button className="button secondary" disabled={statusStale || query.isFetching} onClick={() => void query.refetch()}>刷新资产记录</button>
      </div>
      <div className="history-forecast-assets" role="group" aria-label="预测记录资产">
        {(['BTC', 'ETH'] as const).map(asset => <button className={`button ${selected === asset ? '' : 'secondary'}`}
          type="button" key={asset} aria-pressed={selected === asset}
          disabled={statusStale || legacy && asset !== legacyAsset} onClick={() => setSelected(asset)}>{names[asset]}</button>)}
      </div>
      {query.isPending && !statusStale && <p role="status">正在读取两个资产的已保存记录…</p>}
      {statusStale && <p role="status">当前身份或状态快照未确认，请先刷新状态。</p>}
      {legacy && <p role="status">当前服务仅返回一条最新记录；按资产选择尚未提供，以下保留已有记录的只读入口。</p>}
      {query.isError && !legacy && <p role="alert">{failure(query.error)}</p>}
      {item && <p>{item.reason_label}列表只核对已保存元数据，不代表原件已核验。</p>}
    </section>
    {forecast && <HistoryForecastRecord key={`${authRevision}:${selected}:${forecast.forecast_id ?? 'none'}:${forecast.cutoff_at ?? ''}`}
      forecast={forecast} authRevision={authRevision} statusStale={readBlocked} />}
  </>
}
