import {
  decisionHorizonV2Schema,
  decisionSettingsPartitionV1Schema,
  type DecisionHorizonV2,
  type DecisionSettingsPartitionV1,
} from '../../../../packages/contracts_ts/src/generated/r2'
import { canonicalDecisionBody as canonical } from '../api/decisionOriginals'

const schemaVersion = 'perpetual-horizon-preference.v1'
const fields = ['horizon', 'owner_id', 'partition', 'schema_version']

function validOwner(owner: string) {
  if (!owner || owner !== owner.trim() || owner.length > 128) throw new Error('perpetual_horizon_preference_owner_invalid')
  return owner
}

export function perpetualHorizonPreferenceKey(owner: string, partition: DecisionSettingsPartitionV1) {
  const savedPartition = decisionSettingsPartitionV1Schema.parse(partition)
  return `decision-hub:perpetual-horizon-preference.v1:${encodeURIComponent(validOwner(owner))}:${encodeURIComponent(canonical(savedPartition))}`
}

export function readPerpetualHorizonPreference(
  owner: string,
  partition: DecisionSettingsPartitionV1,
): DecisionHorizonV2 {
  try {
    const savedPartition = decisionSettingsPartitionV1Schema.parse(partition)
    const raw = sessionStorage.getItem(perpetualHorizonPreferenceKey(owner, savedPartition))
    if (!raw) return '4h'
    const value = JSON.parse(raw) as Record<string, unknown>
    if (!value || typeof value !== 'object' || Object.keys(value).sort().join(',') !== fields.join(',')) return '4h'
    const storedPartition = decisionSettingsPartitionV1Schema.parse(value.partition)
    const horizon = decisionHorizonV2Schema.parse(value.horizon)
    return value.schema_version === schemaVersion
      && value.owner_id === owner
      && canonical(storedPartition) === canonical(savedPartition)
      ? horizon
      : '4h'
  } catch {
    return '4h'
  }
}

export function savePerpetualHorizonPreference(
  owner: string,
  partition: DecisionSettingsPartitionV1,
  horizon: DecisionHorizonV2,
) {
  try {
    const savedPartition = decisionSettingsPartitionV1Schema.parse(partition)
    const value = {
      schema_version: schemaVersion,
      owner_id: validOwner(owner),
      partition: savedPartition,
      horizon: decisionHorizonV2Schema.parse(horizon),
    }
    const key = perpetualHorizonPreferenceKey(owner, savedPartition)
    const raw = canonical(value)
    sessionStorage.setItem(key, raw)
    return sessionStorage.getItem(key) === raw
  } catch {
    return false
  }
}
