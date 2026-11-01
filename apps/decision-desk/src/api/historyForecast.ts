import { historyAssetRefreshReplyV2Schema, type HistoryAssetRefreshReplyV2 } from '../../../../packages/contracts_ts/src/generated/r2'
import { requestResponse } from './client'
import { HISTORY_SCOPES, type HistoryProductStatusV2 } from './historyStatus'

export type { HistoryAssetRefreshReplyV2 }
type Forecast = HistoryProductStatusV2['forecast']

export async function readHistoryForecast(forecast: Forecast, signal?: AbortSignal): Promise<HistoryAssetRefreshReplyV2> {
  if (!forecast.forecast_id || forecast.forecast_id.length > 128 || !forecast.scope_id
    || !(forecast.scope_id in HISTORY_SCOPES) || !forecast.cutoff_at) {
    throw new Error('history_forecast_identity_missing')
  }
  const value = historyAssetRefreshReplyV2Schema.parse(await requestResponse(
    `/v1/history/forecasts/${encodeURIComponent(forecast.forecast_id)}`, response => response.json(),
    { credentials: 'include', signal }, undefined, { timeoutMs: 120_000 },
  ))
  const asset = forecast.scope_id === 'crypto:spot:okx:BTC-USDT:trade:1m' ? 'BTC' : 'ETH'
  if (value.outcome !== 'committed_verified' || value.committed !== true
    || value.forecast_id !== forecast.forecast_id || value.asset !== asset
    || Date.parse(value.cutoff_at!) !== Date.parse(forecast.cutoff_at)) {
    throw new Error('history_forecast_original_identity_mismatch')
  }
  return value
}
