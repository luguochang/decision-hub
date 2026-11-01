import { historyOutcomeStatusV2Schema, historyOutcomeScoreReplyV2Schema,
  historyOutcomeObservationReplyV2Schema, type HistoryOutcomeStatusV2,
  type HistoryOutcomeScoreReplyV2, type HistoryOutcomeObservationReplyV2,
  type OutcomeOriginalRefV2 } from '../../../../packages/contracts_ts/src/generated/history_outcome_v2'
import { requestResponse } from './client'

export type { HistoryOutcomeStatusV2, HistoryOutcomeScoreReplyV2, HistoryOutcomeObservationReplyV2 }
const protocolPath = (protocolId: string) => {
  if (!/^heval2_[a-f0-9]{48}$/.test(protocolId)) throw new Error('history_outcome_protocol_identity_invalid')
  return `/v1/history/evaluations/${encodeURIComponent(protocolId)}`
}

export async function readHistoryOutcomeStatus(protocolId: string, offset = 0, signal?: AbortSignal): Promise<HistoryOutcomeStatusV2> {
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 47) throw new Error('history_outcome_page_invalid')
  const value = await requestResponse(`${protocolPath(protocolId)}/status?offset=${offset}&limit=30`, async response =>
    historyOutcomeStatusV2Schema.parse(await response.json()), { method: 'GET', credentials: 'include', signal })
  if (value.protocol_id !== protocolId || value.pagination.offset !== offset || value.pagination.limit !== 30) {
    throw new Error('history_outcome_status_response_identity_mismatch')
  }
  return value
}

export async function readHistoryOutcomeScore(protocolId: string, original: OutcomeOriginalRefV2, signal?: AbortSignal): Promise<HistoryOutcomeScoreReplyV2> {
  if (!/^hscore2_[a-f0-9]{48}$/.test(original.identifier)) throw new Error('history_outcome_score_identity_invalid')
  const value = await requestResponse(`${protocolPath(protocolId)}/scores/${encodeURIComponent(original.identifier)}`, async response =>
    historyOutcomeScoreReplyV2Schema.parse(await response.json()), { method: 'GET', credentials: 'include', signal }, undefined, { timeoutMs: 120_000 })
  if (value.protocol_id !== protocolId || value.score.score_id !== original.identifier || value.score.payload_hash !== original.payload_hash) {
    throw new Error('history_outcome_score_response_identity_mismatch')
  }
  return value
}

export async function readHistoryOutcomeObservation(protocolId: string, original: OutcomeOriginalRefV2, signal?: AbortSignal): Promise<HistoryOutcomeObservationReplyV2> {
  if (!/^hobservation2_[a-f0-9]{48}$/.test(original.identifier)) throw new Error('history_outcome_observation_identity_invalid')
  const value = await requestResponse(`${protocolPath(protocolId)}/observations/${encodeURIComponent(original.identifier)}`, async response =>
    historyOutcomeObservationReplyV2Schema.parse(await response.json()), { method: 'GET', credentials: 'include', signal }, undefined, { timeoutMs: 120_000 })
  if (value.protocol_id !== protocolId || value.observation.observation_id !== original.identifier || value.observation.payload_hash !== original.payload_hash) {
    throw new Error('history_outcome_observation_response_identity_mismatch')
  }
  return value
}
