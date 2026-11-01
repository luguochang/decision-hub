import { historyOutcomeIndexV2Schema, type HistoryOutcomeIndexV2 } from '../../../../packages/contracts_ts/src/generated/history_outcome_index_v2'
import { requestResponse } from './client'

export type { HistoryOutcomeIndexV2 }

/** Signed-owner server grants only. Listing never starts an evaluation or reads originals. */
export async function readHistoryOutcomeEvaluations(signal?: AbortSignal): Promise<HistoryOutcomeIndexV2> {
  return requestResponse('/v1/history/evaluations', async response =>
    historyOutcomeIndexV2Schema.parse(await response.json()), {
    method: 'GET', credentials: 'include', signal,
  })
}
