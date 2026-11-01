import { z } from 'zod'
import { decisionHorizonV2Schema, decisionSettingsPartitionV1Schema, decisionSourceRefV1Schema, perpetualResearchPaperPartitionV3Schema, type DecisionHorizonV2, type DecisionSettingsPartitionV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import { request } from './client'
import { settingsApi } from './decisionSettings'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'

const base = '/v1/decision-settings/research/schedules'
const commandV1Schema = z.object({ schema_version: z.literal('perpetual-research-schedule-command.v1'), request_id: z.string().min(1).max(128),
  partition: decisionSettingsPartitionV1Schema, horizon: decisionHorizonV2Schema, expected_state_version: z.number().int().min(0),
  operation: z.enum(['enable', 'pause']), interval_seconds: z.literal(3600), settings_binding_policy: z.literal('follow_current_saved_head') }).strict()
const commandV2Schema = commandV1Schema.extend({ schema_version: z.literal('perpetual-research-schedule-command.v2'), partition: perpetualResearchPaperPartitionV3Schema,
  input_policy: z.literal('saved_preferences_current_paper_ledger.v1') }).strict()
const commandSchema = z.discriminatedUnion('schema_version', [commandV1Schema, commandV2Schema])
const receiptV1Schema = z.object({ schema_version: z.literal('perpetual-research-schedule-receipt.v1'), owner_id: z.string().min(1), request_id: z.string().min(1),
  command: commandV1Schema, command_hash: z.string(), schedule_id: z.string(), state_version: z.number().int().min(1), state: z.enum(['active', 'paused']),
  accepted_at: z.string().datetime({ offset: true }), revision: decisionSourceRefV1Schema, payload_hash: z.string() }).strict()
const receiptV2Schema = receiptV1Schema.extend({ schema_version: z.literal('perpetual-research-schedule-receipt.v2'), command: commandV2Schema }).strict()
const receiptSchema = z.discriminatedUnion('schema_version', [receiptV1Schema, receiptV2Schema])
const intentSchema = z.object({ owner_id: z.string().min(1), command: commandSchema, command_hash: z.string() }).strict()
export type ScheduleIntent = z.infer<typeof intentSchema>
export type ScheduleReceipt = z.infer<typeof receiptSchema>
const viewSchema = z.object({ schema_version: z.literal('perpetual-research-schedule-view.v1'), owner_id: z.string(), partition: decisionSettingsPartitionV1Schema,
  horizon: decisionHorizonV2Schema, state: z.enum(['not_enabled', 'active', 'paused']), state_version: z.number().int().min(0), revision: decisionSourceRefV1Schema.nullable(),
  next_due_at: z.string().datetime({ offset: true }).nullable(), last_slot: z.object({ request_id: z.string(), status: z.string(), run_id: z.string().nullable(),
    run_status: z.string().nullable(), error_code: z.string().nullable(), protocol_version: z.string() }).strict().nullable(), missed_slots: z.number().int().min(0), receipt: receiptSchema.nullable() }).strict()
export type ScheduleView = z.infer<typeof viewSchema>
function session() { const value = settingsApi.session(); if (!value) throw new Error('decision_settings_session_required'); return value }
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const key = (owner: string, partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2) => `decision-hub:periodic-research-intent.v1:${owner}:${encodeURIComponent(canonical(partition))}:${horizon}`
async function intent(value: unknown, owner: string) {
  const result = intentSchema.parse(value)
  if (result.owner_id !== owner || result.command_hash !== await hash(result.command)) throw new Error('perpetual_schedule_intent_mismatch')
  return result
}
async function receipt(raw: unknown, original?: ScheduleIntent) {
  const value = receiptSchema.parse(raw), { revision, payload_hash, ...body } = value
  if (value.owner_id !== session().owner_id || value.request_id !== value.command.request_id || value.command_hash !== await hash(value.command)
    || value.payload_hash !== await hash(body) || revision.hash !== payload_hash
    || revision.ref !== 'perpschedulecmd_' + await hash([value.owner_id, value.request_id])
    || value.schedule_id !== 'perpschedule_' + await hash([value.owner_id, value.command.partition, value.command.horizon])
    || value.state_version !== value.command.expected_state_version + 1 || value.state !== (value.command.operation === 'enable' ? 'active' : 'paused')
    || original && (!same(value.command, original.command) || value.command_hash !== original.command_hash || value.owner_id !== original.owner_id)) throw new Error('perpetual_schedule_receipt_mismatch')
  if (value.owner_id !== session().owner_id) throw new Error('perpetual_schedule_owner_changed')
  return value
}
export async function readScheduleIntent(partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2) {
  const owner = session().owner_id, raw = localStorage.getItem(key(owner, partition, horizon))
  if (!raw) return null
  const original = await intent(JSON.parse(raw), owner)
  if (!same(original.command.partition, partition) || original.command.horizon !== horizon) throw new Error('perpetual_schedule_intent_mismatch')
  return original
}
export async function makeScheduleIntent(partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2, version: number, operation: 'enable' | 'pause', previous?: ScheduleReceipt | null) {
  const owner = session().owner_id
  if (await readScheduleIntent(partition, horizon)) throw new Error('perpetual_schedule_pending_intent')
  const legacyPause = operation === 'pause' && previous?.command.schema_version === 'perpetual-research-schedule-command.v1'
  const command = commandSchema.parse({ schema_version: legacyPause ? 'perpetual-research-schedule-command.v1' : 'perpetual-research-schedule-command.v2', request_id: crypto.randomUUID(), partition, horizon,
    expected_state_version: version, operation, interval_seconds: 3600, settings_binding_policy: 'follow_current_saved_head',
    ...(!legacyPause ? { input_policy: 'saved_preferences_current_paper_ledger.v1' } : {}) })
  const original = { owner_id: owner, command, command_hash: await hash(command) }
  if (session().owner_id !== owner) throw new Error('perpetual_schedule_owner_changed')
  const storageKey = key(owner, partition, horizon), raw = canonical(original)
  localStorage.setItem(storageKey, raw)
  if (localStorage.getItem(storageKey) !== raw) throw new Error('perpetual_schedule_intent_not_saved')
  return original
}
export function acknowledgeScheduleIntent(original: ScheduleIntent) {
  if (original.owner_id !== session().owner_id) throw new Error('perpetual_schedule_owner_changed')
  localStorage.removeItem(key(original.owner_id, original.command.partition, original.command.horizon))
}
export const perpetualResearchSchedules = {
  command: async (value: ScheduleIntent) => {
    const original = await intent(value, session().owner_id)
    const stored = await readScheduleIntent(original.command.partition, original.command.horizon)
    if (!same(original, stored)) throw new Error('perpetual_schedule_intent_not_saved')
    return receipt(await request(base + '/commands', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json',
      'X-Settings-CSRF': session().csrf_token, 'Idempotency-Key': original.command.request_id }, body: canonical(original.command) }), original)
  },
  receipt: async (value: ScheduleIntent) => {
    const original = await intent(value, session().owner_id)
    return receipt(await request(base + '/commands/' + encodeURIComponent(original.command.request_id), { credentials: 'include' }), original)
  },
  current: async (partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2) => {
    const owner = session().owner_id
    const value = viewSchema.parse(await request(base + '/current?' + new URLSearchParams({ ...partition, horizon }), { credentials: 'include' }))
    if (value.owner_id !== owner || !same(value.partition, partition) || value.horizon !== horizon) throw new Error('perpetual_schedule_view_mismatch')
    if (value.state === 'not_enabled') {
      if (value.state_version !== 0 || value.revision || value.receipt || value.next_due_at || value.last_slot) throw new Error('perpetual_schedule_view_mismatch')
    } else {
      if (!value.receipt) throw new Error('perpetual_schedule_view_mismatch')
      const original = await receipt(value.receipt)
      if (!same(value.revision, original.revision) || value.state_version !== original.state_version || value.state !== original.state
        || !same(partition, original.command.partition) || horizon !== original.command.horizon) throw new Error('perpetual_schedule_view_mismatch')
    }
    if (session().owner_id !== owner) throw new Error('perpetual_schedule_owner_changed')
    return value
  },
}
