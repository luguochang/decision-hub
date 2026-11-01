// Exact DEV canonical contract; shared ROOT aliases may still be older.
import { historyHourlyStatusV2Schema, type HistoryHourlyStatusV2 } from '../../../../packages/contracts_ts/src/generated/history_hourly_status_v2'
import { request } from './client'

export type { HistoryHourlyStatusV2 }

/** Fast owner-scoped metadata only. No producer or original forecast read. */
export async function readHistorySchedule(signal?: AbortSignal): Promise<HistoryHourlyStatusV2> {
  return historyHourlyStatusV2Schema.parse(await request('/v1/history/schedule', {
    credentials: 'include', signal,
  }))
}
