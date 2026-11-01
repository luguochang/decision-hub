import { z } from 'zod'
import { decisionSettingsPartitionV1Schema, decisionSourceRefV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { settingsApi } from './decisionSettings'
import { ApiRequestError, request } from './client'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import type { PerpetualAdviceSelection } from './perpetualAdvice'

const ref = decisionSourceRefV1Schema, text = z.string().min(1).max(128), date = z.string().datetime({ offset: true }), digest = z.string().regex(/^[a-f0-9]{64}$/)
const positive = z.string().max(80).regex(/^(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9])$/)
const scope = z.enum(['perp-book:okx:BTC-USDT-SWAP', 'perp-book:okx:ETH-USDT-SWAP'])
const ordinaryIdentity = z.object({ partition: decisionSettingsPartitionV1Schema, settings_revision: ref, settings_state_version: z.number().int().positive(),
  revision: ref, expected_plan_state_version: z.number().int().positive(), account_revision: ref, account_state_version: z.number().int().positive(), protocol_ref: ref,
  horizon: z.enum(['24h', '72h']).optional() }).strict()
const eventBinding = z.object({ event_plan_ref: ref, selection_ref: ref, branch_ref: ref, event_review_ref: ref, event_publication_ref: ref }).strict()
const eventRefs = (value: object) => Object.fromEntries(Object.keys(eventBinding.shape).map(key => [key, (value as Record<string, unknown>)[key]]))
const eventIdentity = ordinaryIdentity.extend(eventBinding.shape).strict()
const identity = z.union([eventIdentity, ordinaryIdentity])
const prepare = z.object({ schema_version: z.literal('perpetual-manual-prepare.v1'), request_id: text, partition: decisionSettingsPartitionV1Schema,
  settings_revision: ref, settings_state_version: z.number().int().positive(), revision: ref, horizon: z.enum(['4h', '24h', '72h']) }).strict()
const display = ordinaryIdentity.extend({ schema_version: z.literal('perpetual-manual-display.v1'), request_id: text }).strict()
const execution = ordinaryIdentity.extend({ schema_version: z.literal('perpetual-manual-intent-command.v1'), request_id: text, display_receipt: ref,
  max_entry_notional_usdt: positive, stop_price_usdt: positive, take_profit_price_usdt: positive, exit_at: date, simulation_acknowledged: z.literal(true) }).strict()
const eventPrepare = prepare.extend({ ...eventBinding.shape, schema_version: z.literal('perpetual-manual-prepare.v2') }).strict()
const eventDisplay = display.extend({ ...eventBinding.shape, schema_version: z.literal('perpetual-manual-display.v2') }).strict()
const eventExecution = execution.extend({ ...eventBinding.shape, schema_version: z.literal('perpetual-manual-intent-command.v2') }).strict()
const command = z.union([prepare, display, execution, eventPrepare, eventDisplay, eventExecution])
const authority = { native_reviewed: z.literal(false), financial_qualified: z.literal(false), policy_authority: z.literal('user_explicit_paper_simulation') }
const intentSchema = z.object({ schema_version: z.literal('perpetual-manual-execution-intent.v1'), id: text, owner_id: text, account_id: text, account_key: text,
  protocol_ref: ref, scope_id: scope, event_key: text, plan_id: text, revision: ref, candidate_id: z.string(), horizon: z.enum(['24h', '72h']).optional(),
  target_side: z.enum(['long', 'short']), phase: z.enum(['entry', 'reduce', 'exit']), command_request_id: text, command_hash: digest, command_json: z.string(),
  accepted_at: date, eligible_at: date, quote_deadline: date, sampling_generation: z.number().int().nonnegative(), episode_ref: ref.nullable(), trigger_ref: ref.nullable(),
  ...authority, payload_hash: digest }).strict()
const protocolSchema = z.object({ schema_version: z.literal('perp-manual-paper-protocol.v1'), id: text, owner_id: text, account_id: text,
  account_revision: ref, execution_mode: z.literal('manual_after_visible'), execution_authority: z.literal(false), native_reviewed: z.literal(false),
  policy_authority: z.literal('user_explicit_paper_simulation'), reaction_delay_seconds: z.literal(1), quote_max_age_seconds: z.literal(5),
  entry_wait_seconds: z.literal(30), top_size_participation: z.literal('0.1'), fee_rate: z.literal('0.0005'), slippage_bps: z.literal('2'),
  T0: date, T1: date, payload_hash: digest }).passthrough()
const response = z.object({ schema_version: z.literal('perpetual-manual-command-result.v1'), status: z.enum(['prepared', 'recorded', 'queued']),
  command, ...authority, context: identity.optional(), protocol: protocolSchema.optional(), intent: intentSchema.optional(), server_time: date.optional() }).strict()
const receiptSchema = z.object({ schema_version: z.literal('action-command-receipt.v1'), owner_id: text, request_id: text, plan_id: text,
  operation: z.enum(['perp_prepare', 'perp_display', 'perp_manual_intent']), payload_hash: digest, response, accepted_at: date, receipt_hash: digest }).strict()
const saved = z.object({ owner_id: text, command, command_hash: digest }).strict()
export type ManualPaperIntent = z.infer<typeof saved>
export type ManualPaperReceipt = z.infer<typeof receiptSchema>
export type ManualPaperContext = z.infer<typeof identity>
export type ManualPaperEventBinding = z.infer<typeof eventBinding>
export type ManualPaperCommand = z.infer<typeof command>
const inputGuideQuote = z.discriminatedUnion('status', [
  z.object({ status: z.literal('pending') }).strict(),
  z.object({ status: z.enum(['fresh', 'stale']), sampling_receipt: ref, capture_ref: ref, bid: positive, ask: positive,
    provider_observed_at: date, observed_local_lower: date, observed_local_upper: date, received_at: date, expires_at: date }).strict(),
])
const inputGuideSchema = z.object({ schema_version: z.literal('perpetual-manual-input-guide.v1'), owner_id: text,
  prepare_request_id: text, context: identity, target_side: z.enum(['long', 'short']), tick_sz: positive,
  latest_entry_at: date.optional(), latest_exit_at: date, server_time: date, quote: inputGuideQuote }).strict()
export type ManualPaperInputGuide = z.infer<typeof inputGuideSchema>
const executionView = z.object({ schema_version: z.literal('perpetual-manual-execution-view.v1'), owner_id: text, intent: intentSchema,
  receipt: z.object({ schema_version: z.literal('perpetual-manual-execution-result.v1'), intent_id: text, status: z.enum(['filled', 'no_fill', 'rejected', 'unknown']),
    reason: z.string().nullable(), decided_at: date, quote_selection_proof: z.array(z.object({ sampling_receipt: ref, decision: z.string() }).strict()),
    fill_id: z.string().nullable(), ledger_revision: ref.nullable(), episode_id: z.string().nullable() }).strict().nullable(),
  fill: z.object({ schema_version: z.literal('perpetual-manual-fill.v1'), id: text, intent_id: text, episode_id: text, quote_capture_id: text,
    quote_capture_hash: digest, quote_capacity_key: digest, sampling_receipt: ref, protocol_ref: ref, order_side: z.enum(['buy', 'sell']),
    filled_contracts: positive, fill_price_usdt: positive, filled_notional_usdt: positive, trade_fee_usdt: z.string(), gross_pnl_usdt: z.string(),
    ledger_parent: ref, ledger_revision: ref, funding_cash_usdt: z.null() }).strict().nullable(),
  episode: z.object({ episode_id: text, version: z.number().int().positive(), owner_id: text, account_id: text, entry_intent_id: text,
    command_json: z.string(), remaining_contracts: z.string(), target_side: z.enum(['long', 'short']), last_fill_ref: ref, ledger_revision: ref,
    last_quote_capacity_key: digest, sampling_generation: z.number().int().nonnegative(), initial_trade_cash_usdt: z.string(), gross_pnl_usdt: z.string(),
    trade_fees_usdt: z.string(), outcome_id: z.string().nullable(), entry_at: date, updated_at: date, exit_alarm: z.boolean().optional() }).passthrough().nullable(),
  outcome: z.discriminatedUnion('status', [
    z.object({ schema_version: z.literal('perpetual-manual-outcome.v1'), id: text, episode_id: text, opportunity_id: text, status: z.literal('unscorable'),
      gross_pnl_usdt: z.string(), trade_fees_usdt: z.string(), trade_cash_delta_usdt: z.string(), funding_cash_usdt: z.null(), net_pnl_usdt: z.null(),
      ledger_revision: ref, gaps: z.array(z.string()), authority: z.literal('user_explicit_paper_simulation'), native_reviewed: z.literal(false), financial_qualified: z.literal(false) }).strict(),
    z.object({ schema_version: z.literal('perpetual-manual-outcome.v1'), id: text, version: z.literal(2), episode_id: text, opportunity_id: text, status: z.literal('settled'),
      gross_pnl_usdt: z.string(), trade_fees_usdt: z.string(), trade_cash_delta_usdt: z.string(), funding_cash_usdt: z.string(), net_pnl_usdt: z.string(),
      ledger_revision: ref, funding_coverage_ref: ref, gaps: z.array(z.string()), authority: z.literal('user_explicit_paper_simulation'), native_reviewed: z.literal(false), financial_qualified: z.literal(false) }).strict(),
  ]).nullable(),
}).strict()
export type ManualPaperExecution = z.infer<typeof executionView>
const base = '/v1/decision-settings/paper-execution'
export const manualPaperKey = (owner: string) => `decision-hub:manual-paper-intent.v1:${owner}`
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const manualHorizon = (value: { horizon?: string }) => value.horizon ?? '4h'
function session() { const value = settingsApi.session(); if (!value) throw new Error('perp_manual_signed_owner_required'); return value }
async function validate(value: unknown) {
  const original = saved.parse(value)
  if (original.owner_id !== session().owner_id) throw new Error('perp_manual_owner_mismatch')
  if (original.command_hash !== await hash({ owner_id: original.owner_id, command: original.command })) throw new Error('perp_manual_intent_identity_mismatch')
  return original
}
export async function makeManualPaperIntent(value: unknown): Promise<ManualPaperIntent> {
  const owner_id = session().owner_id, body = command.parse(value)
  return validate({ owner_id, command: body, command_hash: await hash({ owner_id, command: body }) })
}
export async function saveManualPaperIntent(value: ManualPaperIntent) {
  const original = await validate(value), key = manualPaperKey(original.owner_id), raw = canonical(original)
  localStorage.setItem(key, raw)
  if (localStorage.getItem(key) !== raw) throw new Error('perp_manual_intent_not_saved')
}
export async function readManualPaperIntent(owner: string): Promise<ManualPaperIntent | null> {
  if (owner !== session().owner_id) throw new Error('perp_manual_owner_mismatch')
  const raw = localStorage.getItem(manualPaperKey(owner))
  return raw ? validate(JSON.parse(raw)) : null
}
export async function archiveUnacceptedManualPaperIntent(value: ManualPaperIntent, error: unknown): Promise<boolean> {
  if (!(error instanceof ApiRequestError) || error.kind !== 'http' || error.status !== 422) return false
  const original = await validate(value), key = manualPaperKey(original.owner_id), durable = await readManualPaperIntent(original.owner_id)
  if (!same(durable, original)) throw new Error('perp_manual_intent_identity_mismatch')
  const rejectedKey = `${key}:rejected:${original.command.request_id}`
  const raw = canonical({ schema_version: 'perpetual-manual-local-rejection.v1', original, http_status: 422 })
  localStorage.setItem(rejectedKey, raw)
  if (localStorage.getItem(rejectedKey) !== raw) throw new Error('perp_manual_rejection_not_saved')
  // Preserve the entire rejected command before releasing its pending pointer.
  if (localStorage.getItem(key) !== canonical(original)) throw new Error('perp_manual_intent_identity_mismatch')
  localStorage.removeItem(key)
  return true
}
export function manualIntentMatches(value: ManualPaperIntent, selection: PerpetualAdviceSelection, revision: z.infer<typeof ref>, event?: ManualPaperEventBinding | null) {
  return value.owner_id === selection.owner_id && manualHorizon(value.command) === selection.horizon && same(value.command.partition, selection.partition)
    && same(value.command.settings_revision, selection.settings_revision) && value.command.settings_state_version === selection.settings_state_version && same(value.command.revision, revision)
    && (event ? same(eventRefs(value.command), event) : !('event_plan_ref' in value.command))
}
async function checked(raw: unknown, original: ManualPaperIntent): Promise<ManualPaperReceipt> {
  const value = receiptSchema.parse(raw), { receipt_hash, ...body } = value
  if (session().owner_id !== original.owner_id || value.owner_id !== original.owner_id || value.request_id !== original.command.request_id
    || value.payload_hash !== original.command_hash || !same(value.response.command, original.command) || receipt_hash !== await hash(body)) throw new Error('perp_manual_receipt_identity_mismatch')
  const expected = original.command.schema_version.startsWith('perpetual-manual-prepare.') ? ['perp_prepare', 'prepared'] : original.command.schema_version.startsWith('perpetual-manual-display.') ? ['perp_display', 'recorded'] : ['perp_manual_intent', 'queued']
  if (value.operation !== expected[0] || value.response.status !== expected[1]) throw new Error('perp_manual_receipt_identity_mismatch')
  if (value.response.status === 'prepared') {
    const c = value.response.context, p = value.response.protocol
    if (!c || !p || !same(c.partition, original.command.partition) || !same(c.settings_revision, original.command.settings_revision)
      || c.settings_state_version !== original.command.settings_state_version || !same(c.revision, original.command.revision)
      || manualHorizon(c) !== manualHorizon(original.command)
      || p.owner_id !== original.owner_id || p.account_id !== c.partition.account_id || !same(c.protocol_ref, { ref: p.id, hash: p.payload_hash })
      || p.payload_hash !== await hash(Object.fromEntries(Object.entries(p).filter(([key]) => key !== 'payload_hash')))) throw new Error('perp_manual_protocol_identity_mismatch')
    if ('event_plan_ref' in original.command && (!('event_plan_ref' in c) || !same(eventRefs(c), eventRefs(original.command)))) throw new Error('perp_manual_event_identity_mismatch')
  }
  if (value.response.status === 'queued') {
    const i = value.response.intent
    if (!i || manualHorizon(i) !== manualHorizon(original.command) || i.scope_id !== `perp-book:${original.command.partition.instrument_id}` || i.owner_id !== original.owner_id || i.command_hash !== original.command_hash || i.command_json !== canonical(original.command)
      || i.command_request_id !== original.command.request_id || i.plan_id !== value.plan_id || i.account_id !== original.command.partition.account_id
      || !same(i.revision, original.command.revision) || i.payload_hash !== await hash(Object.fromEntries(Object.entries(i).filter(([key]) => key !== 'payload_hash')))) throw new Error('perp_manual_intent_identity_mismatch')
  }
  if (session().owner_id !== original.owner_id) throw new Error('perp_manual_owner_mismatch')
  return value
}
export const perpetualManualPaper = {
  capabilities: async () => {
    const owner = session().owner_id
    const common = z.object({ owner_id: text, configured: z.boolean(), worker_liveness: z.literal('unknown'), horizon: z.literal('4h'), execution: z.literal('paper_only') })
    const value = z.union([common.extend({ schema_version: z.literal('perpetual-manual-capabilities.v1'), instrument_id: z.literal('okx:BTC-USDT-SWAP') }).strict(),
      common.extend({ schema_version: z.literal('perpetual-manual-capabilities.v2'), instrument_ids: z.tuple([z.literal('okx:BTC-USDT-SWAP'), z.literal('okx:ETH-USDT-SWAP')]) }).strict(),
      common.extend({ schema_version: z.literal('perpetual-manual-capabilities.v3'), horizons: z.tuple([z.literal('4h'), z.literal('24h'), z.literal('72h')]), instrument_ids: z.tuple([z.literal('okx:BTC-USDT-SWAP'), z.literal('okx:ETH-USDT-SWAP')]) }).strict()])
      .parse(await request(`${base}/capabilities`, { method: 'GET', credentials: 'include' }))
    if (value.owner_id !== owner || session().owner_id !== owner) throw new Error('perp_manual_owner_mismatch')
    return value
  },
  command: async (value: ManualPaperIntent) => {
    const original = await validate(value), durable = await readManualPaperIntent(original.owner_id)
    if (!same(durable, original)) throw new Error('perp_manual_intent_not_saved')
    const route = original.command.schema_version.startsWith('perpetual-manual-prepare.') ? 'prepare' : original.command.schema_version.startsWith('perpetual-manual-display.') ? 'display' : 'intents'
    return checked(await request(`${base}/${route}`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json',
      'X-Settings-CSRF': session().csrf_token, 'Idempotency-Key': original.command.request_id }, body: canonical(original.command) }), original)
  },
  receipt: async (value: ManualPaperIntent) => { const original = await validate(value); return checked(await request(`${base}/commands/${encodeURIComponent(original.command.request_id)}`, { method: 'GET', credentials: 'include' }), original) },
  guide: async (value: ManualPaperIntent, receipt: ManualPaperReceipt): Promise<ManualPaperInputGuide> => {
    const original = await validate(value), prepared = await checked(receipt, original)
    if (!original.command.schema_version.startsWith('perpetual-manual-prepare.') || prepared.response.status !== 'prepared'
      || !prepared.response.context || !prepared.response.protocol) throw new Error('perp_manual_input_guide_prepare_required')
    const result = inputGuideSchema.parse(await request(`${base}/input-guide/${encodeURIComponent(original.command.request_id)}`, { method: 'GET', credentials: 'include' }))
    if (result.owner_id !== original.owner_id || result.prepare_request_id !== original.command.request_id
      || !same(result.context, prepared.response.context) || result.latest_exit_at !== prepared.response.protocol.T1
      || (result.latest_entry_at !== undefined && Date.parse(result.latest_entry_at) > Date.parse(result.latest_exit_at))
      || session().owner_id !== original.owner_id) throw new Error('perp_manual_input_guide_identity_mismatch')
    return result
  },
  execution: async (original: ManualPaperIntent, intentId: string): Promise<ManualPaperExecution> => {
    await validate(original)
    const result = executionView.parse(await request(`${base}/intents/${encodeURIComponent(intentId)}`, { method: 'GET', credentials: 'include' }))
    const intent = result.intent
    if (result.schema_version !== 'perpetual-manual-execution-view.v1' || result.owner_id !== original.owner_id || intent.id !== intentId
      || manualHorizon(intent) !== manualHorizon(original.command) || intent.scope_id !== `perp-book:${original.command.partition.instrument_id}`
      || intent.command_hash !== original.command_hash || intent.command_json !== canonical(original.command)
      || intent.payload_hash !== await hash(Object.fromEntries(Object.entries(intent).filter(([key]) => key !== 'payload_hash')))
      || session().owner_id !== original.owner_id) throw new Error('perp_manual_execution_identity_mismatch')
    return result
  },
}
