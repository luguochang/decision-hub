import { historyForecastIndexV2Schema, type HistoryForecastIndexV2 } from '../../../../packages/contracts_ts/src/generated/history_forecast_index_v2'
import { request } from './client'

export type { HistoryForecastIndexV2 }

/** Owner-scoped saved metadata only; this never reads originals or produces a forecast. */
export async function readHistoryForecastIndex(signal?: AbortSignal): Promise<HistoryForecastIndexV2> {
  return historyForecastIndexV2Schema.parse(await request('/v1/history/forecast-index', {
    credentials: 'include', signal,
  }))
}
