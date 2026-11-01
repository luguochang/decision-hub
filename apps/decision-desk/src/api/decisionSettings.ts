import { z } from 'zod'
import {
  decisionSettingsCommandV1Schema, decisionSettingsCurrentViewV1Schema, decisionSettingsValuesV1Schema,
  decisionSettingsSaveReceiptV1Schema, decisionSourceRefV1Schema, instrumentContractV1Schema,
  decisionSettingsPartitionV1Schema, accountSnapshotV2Schema, positionContextV2Schema,
  type DecisionSettingsCommandV1, type DecisionSettingsCurrentViewV1,
  type DecisionSettingsPartitionV1, type DecisionSettingsValuesV1,
} from '../../../../packages/contracts_ts/src/generated/r2'
import { request } from './client'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'

export type { DecisionSettingsCurrentViewV1, DecisionSettingsPartitionV1 }
const base = '/v1/decision-settings'
const sessionKey = 'decision-hub:settings-session.v1'
const sessionSchema = z.object({ owner_id: z.string().min(1), csrf_token: z.string().min(1), expires_at: z.string().datetime({ offset: true }) }).strict()
const optionSchema = z.object({ instrument_id: z.enum(['okx:BTC-USDT-SWAP', 'okx:ETH-USDT-SWAP']), instrument: decisionSourceRefV1Schema, contract: instrumentContractV1Schema, execution_profile: decisionSourceRefV1Schema }).strict()
const paperOptionSchema = z.object({ partition: decisionSettingsPartitionV1Schema, account_snapshot: accountSnapshotV2Schema, position_context: positionContextV2Schema, execution_profile: decisionSourceRefV1Schema }).strict()
const optionsSchema = z.object({ schema_version: z.literal('decision-settings-options.v1'), owner_id: z.string().min(1), declared_account_id: z.string().min(1), availability: z.enum(['available', 'unavailable']), reason: z.string().nullable(), instruments: z.array(optionSchema).max(2), paper_available: z.boolean(), paper_reason: z.string().nullable(), paper_accounts: z.array(paperOptionSchema).max(40) }).strict()
export type SettingsOptions = z.infer<typeof optionsSchema>
export type InstrumentOption = SettingsOptions['instruments'][number]
export type PaperOption = SettingsOptions['paper_accounts'][number]
export type SettingsIntent = { owner_id: string; command: DecisionSettingsCommandV1 }
export type SettingsForm = { state: 'flat' | 'long' | 'short' | 'unknown'; contracts: string; entryPrice: string; equity: string; availableMargin: string; maxNotional: string; maxLoss: string; directions: 'any' | 'long' | 'short'; marginMode: 'unknown' | 'isolated' | 'cross'; positionMode: 'unknown' | 'net' | 'hedge'; leverage: string }
export const emptySettingsForm: SettingsForm = { state: 'unknown', contracts: '', entryPrice: '', equity: '', availableMargin: '', maxNotional: '', maxLoss: '', directions: 'any', marginMode: 'unknown', positionMode: 'unknown', leverage: '' }

function session() {
  const raw = sessionStorage.getItem(sessionKey)
  if (!raw) return null
  const value = sessionSchema.parse(JSON.parse(raw))
  return Date.parse(value.expires_at) > Date.now() ? value : null
}
function active() {
  const value = settingsApi.session()
  if (!value) throw new Error('decision_settings_session_required')
  return value
}
const transport = (mutation = false): RequestInit => ({ credentials: 'same-origin', headers: mutation ? { 'Content-Type': 'application/json', 'X-Settings-CSRF': active().csrf_token } : {} })
export function settingsIntentKey(owner: string) { return `decision-hub:settings-intent.v1:${owner}` }
const selectionKey = (owner: string) => `decision-hub:settings-selection.v1:${owner}`
export function readSettingsSelection(owner: string): DecisionSettingsPartitionV1 | null {
  const raw = sessionStorage.getItem(selectionKey(owner))
  try { return raw ? decisionSettingsPartitionV1Schema.parse(JSON.parse(raw)) : null } catch { return null }
}
export function saveSettingsSelection(owner: string, partition: DecisionSettingsPartitionV1) {
  sessionStorage.setItem(selectionKey(owner), JSON.stringify(decisionSettingsPartitionV1Schema.parse(partition)))
}
export function readSettingsIntent(owner: string): SettingsIntent | null {
  const raw = sessionStorage.getItem(settingsIntentKey(owner))
  if (!raw) return null
  const value = z.object({ owner_id: z.literal(owner), command: decisionSettingsCommandV1Schema }).strict().parse(JSON.parse(raw))
  return value
}
export function saveSettingsIntent(intent: SettingsIntent) {
  if (intent.owner_id !== active().owner_id) throw new Error('decision_settings_owner_mismatch')
  const key = settingsIntentKey(intent.owner_id), raw = JSON.stringify(intent)
  sessionStorage.setItem(key, raw)
  if (sessionStorage.getItem(key) !== raw) throw new Error('decision_settings_intent_storage_invalid')
  readSettingsIntent(intent.owner_id)
}
async function receipt(value: unknown, intent: SettingsIntent) {
  const result = decisionSettingsSaveReceiptV1Schema.parse(value)
  const { payload_hash: seal, ...body } = result
  if (active().owner_id !== intent.owner_id || result.owner_id !== intent.owner_id || result.request_id !== intent.command.request_id
    || result.command_hash !== await hash(intent.command) || result.settings_key !== 'dsettings_' + await hash([intent.owner_id, intent.command.partition])
    || result.state_version !== intent.command.expected_state_version + 1 || seal !== await hash(body)) throw new Error('decision_settings_receipt_identity_mismatch')
  return result
}

export const settingsApi = {
  session,
  pair: async (pairing_token: string) => {
    const parsed = sessionSchema.safeParse(await request(`${base}/owner-session`, { credentials: 'same-origin', method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pairing_token }) }))
    if (!parsed.success) throw new Error('decision_settings_session_response_invalid')
    const value = parsed.data
    sessionStorage.setItem(sessionKey, JSON.stringify(value))
    window.dispatchEvent(new Event('decision-hub-settings-session-changed'))
    return value
  },
  options: async () => {
    const value = optionsSchema.parse(await request(`${base}/options`, transport()))
    if (value.owner_id !== active().owner_id || new Set(value.instruments.map(item => item.instrument_id)).size !== value.instruments.length
      || (value.availability === 'available') !== (value.instruments.length > 0)
      || value.paper_available !== (value.paper_accounts.length > 0)
      || value.paper_accounts.some(item => item.partition.account_kind !== 'paper' || item.account_snapshot.owner_id !== value.owner_id || item.account_snapshot.account_kind !== 'paper' || item.account_snapshot.account_id !== item.partition.account_id || item.position_context.instrument_id !== item.partition.instrument_id || item.execution_profile.ref !== item.partition.profile_ref)
      || value.instruments.some(item => `${item.contract.venue}:${item.contract.inst_id}` !== item.instrument_id)) throw new Error('decision_settings_options_identity_mismatch')
    return value
  },
  prepare: async (instrument_id: InstrumentOption['instrument_id']) => {
    const value = optionSchema.parse(await request(`${base}/instruments/prepare`, { ...transport(true), method: 'POST', body: JSON.stringify({ instrument_id }) }))
    if (value.instrument_id !== instrument_id || `${value.contract.venue}:${value.contract.inst_id}` !== instrument_id) throw new Error('decision_settings_options_identity_mismatch')
    return value
  },
  current: async (partition: DecisionSettingsPartitionV1) => {
    const raw = await request(`${base}/current?${new URLSearchParams(partition)}`, transport())
    if (raw === null) return null
    const value = decisionSettingsCurrentViewV1Schema.parse(raw)
    const revision = value.revision, { payload_hash: seal, ...body } = revision
    if (revision.owner_id !== active().owner_id || canonical(revision.partition) !== canonical(partition)
      || revision.revision_no !== value.state_version || seal !== await hash(body)
      || revision.settings_key !== 'dsettings_' + await hash([active().owner_id, partition])) throw new Error('decision_settings_current_identity_mismatch')
    return value
  },
  command: async (intent: SettingsIntent) => {
    if (intent.owner_id !== active().owner_id) throw new Error('decision_settings_owner_mismatch')
    const command = decisionSettingsCommandV1Schema.parse(intent.command)
    return receipt(await request(`${base}/commands`, { ...transport(true), method: 'POST', headers: { ...transport(true).headers, 'Idempotency-Key': command.request_id }, body: JSON.stringify(command) }), intent)
  },
  receipt: async (intent: SettingsIntent) => {
    if (intent.owner_id !== active().owner_id) throw new Error('decision_settings_owner_mismatch')
    return receipt(await request(`${base}/commands/${encodeURIComponent(intent.command.request_id)}`, transport()), intent)
  },
}

export async function makeSettingsIntent(owner: string, partition: DecisionSettingsPartitionV1, option: Pick<InstrumentOption, 'instrument' | 'execution_profile'> | undefined, current: DecisionSettingsCurrentViewV1 | null, form: SettingsForm, remove = false, paper?: PaperOption): Promise<SettingsIntent> {
  const request_id = crypto.randomUUID()
  let values: DecisionSettingsValuesV1 | null = null
  if (!remove) {
    if (!option) throw new Error('decision_settings_instrument_original_unavailable')
    const position = { schema_version: 'position-context.v2' as const, instrument_id: partition.instrument_id, instrument_ref: option.instrument, state: form.state,
      signed_contracts: form.state === 'flat' ? '0' : form.state === 'unknown' || !form.contracts ? null : (form.state === 'short' ? '-' : '') + form.contracts,
      entry_price: form.state === 'flat' || form.state === 'unknown' ? null : form.entryPrice || null,
      isolated_margin: null, margin_mode: form.marginMode, position_mode: form.positionMode, hedge_leg: null,
      leverage: form.leverage || null, liquidation_estimate: null, source: form.state === 'unknown' ? 'unknown' as const : 'user_declared' as const, ref: null, hash: null, account_ref: null }
    const policyBody = { schema_version: 'utility-policy.v2' as const, policy_ref: `user-risk:${request_id}`, source: 'user' as const, objective: 'mean_net' as const, risk_penalty: '0' as const,
      max_gross_notional: form.maxNotional ? { unit: 'USDT' as const, value: form.maxNotional } : null, max_planned_loss: form.maxLoss ? { unit: 'USDT' as const, value: form.maxLoss } : null,
      tail_constraints: null, position_scenario: form.state, margin_buffer_policy: null, allowed_leverage: null,
      allowed_sides: form.directions === 'any' ? null : [form.directions], account_ref: null }
    values = decisionSettingsValuesV1Schema.parse({ instrument: option.instrument, execution_profile: option.execution_profile, position_context: position,
      account_snapshot: { schema_version: 'account-snapshot.v2', owner_id: owner, account_kind: 'user_declared', account_id: partition.account_id,
        source: form.equity || form.availableMargin || form.state !== 'unknown' ? 'user_declared' : 'unknown', as_of: new Date().toISOString(), account_mode: 'unknown',
        positions: null, collateral: null, valuation_ccy: 'USDT', equity: form.equity || null, available_margin: form.availableMargin || null, fee_tier: null,
        limits: { max_gross_notional: policyBody.max_gross_notional, max_reserved_margin: null, max_fee_buffer: null, max_stress_loss: null }, original: null },
      utility_policy: { ...policyBody, hash: await hash(policyBody) } })
    if (partition.account_kind === 'paper') {
      if (!paper || paper.account_snapshot.owner_id !== owner || canonical(paper.partition) !== canonical(partition)) throw new Error('decision_settings_paper_account_unavailable')
      const policy = { ...policyBody, account_ref: paper.account_snapshot.original, position_scenario: paper.position_context.state }
      values = decisionSettingsValuesV1Schema.parse({ instrument: paper.position_context.instrument_ref, execution_profile: paper.execution_profile,
        account_snapshot: paper.account_snapshot, position_context: paper.position_context, utility_policy: { ...policy, hash: await hash(policy) } })
    }
  }
  return { owner_id: owner, command: decisionSettingsCommandV1Schema.parse({ schema_version: 'decision-settings-command.v1', request_id, expected_state_version: current?.state_version ?? 0, partition, operation: remove ? 'remove' : 'upsert', values }) }
}

export async function bootstrapSettingsSession() {
  const [route, query = ''] = window.location.hash.split('?')
  const params = new URLSearchParams(query), credential = params.get('settings_pairing')
  if (!credential) return
  params.delete('settings_pairing')
  const suffix = params.toString()
  // Remove the per-launch credential before any asynchronous work or error.
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}${route}${suffix ? '?' + suffix : ''}`)
  try {
    await settingsApi.pair(credential)
  } catch {
    window.dispatchEvent(new Event('decision-hub-settings-session-unavailable'))
  }
}

export function settingsFormFromCurrent(current: DecisionSettingsCurrentViewV1 | null): SettingsForm {
  const values = current?.revision.values
  if (!values) return { ...emptySettingsForm }
  const position = values.position_context, account = values.account_snapshot, policy = values.utility_policy
  return { state: position.state, contracts: position.signed_contracts?.replace(/^-/, '') ?? '', entryPrice: position.entry_price ?? '', equity: account.equity ?? '', availableMargin: account.available_margin ?? '',
    maxNotional: policy.max_gross_notional?.value ?? '', maxLoss: policy.max_planned_loss?.value ?? '', directions: policy.allowed_sides?.length === 1 && policy.allowed_sides[0] !== 'flat' ? policy.allowed_sides[0] : 'any',
    marginMode: position.margin_mode, positionMode: position.position_mode, leverage: position.leverage ?? '' }
}
