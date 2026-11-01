import { z } from 'zod'
import {
  createPerpetualPaperAccountV1Schema, paperAccountCreationReceiptV1Schema, paperAccountListV1Schema,
  type CreatePerpetualPaperAccountV1, type PaperAccountCreationReceiptV1,
} from '../../../../packages/contracts_ts/src/generated/r2'
import { request } from './client'
import { settingsApi, type InstrumentOption } from './decisionSettings'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'

const base = '/v1/decision-settings/paper-accounts'
const intentSchema = z.object({ owner_id: z.string().min(1), command: createPerpetualPaperAccountV1Schema }).strict()
export type PaperAccountCreateIntent = z.infer<typeof intentSchema>
export type { PaperAccountCreationReceiptV1 }

function session() {
  const value = settingsApi.session()
  if (!value) throw new Error('decision_settings_session_required')
  return value
}
const transport = (mutation = false): RequestInit => ({ credentials: 'same-origin', headers: mutation ? { 'Content-Type': 'application/json', 'X-Settings-CSRF': session().csrf_token } : {} })
export function paperAccountIntentKey(owner: string) { return `decision-hub:perpetual-paper-create.v1:${owner}` }
export function readPaperAccountIntent(owner: string): PaperAccountCreateIntent | null {
  const raw = localStorage.getItem(paperAccountIntentKey(owner))
  if (!raw) return null
  const value = intentSchema.parse(JSON.parse(raw))
  if (value.owner_id !== owner) throw new Error('perp_paper_account_owner_rejected')
  return value
}
export function savePaperAccountIntent(intent: PaperAccountCreateIntent) {
  intentSchema.parse(intent)
  if (intent.owner_id !== session().owner_id) throw new Error('perp_paper_account_owner_rejected')
  const key = paperAccountIntentKey(intent.owner_id), raw = JSON.stringify(intent)
  localStorage.setItem(key, raw)
  if (localStorage.getItem(key) !== raw) throw new Error('perp_paper_intent_not_saved')
  readPaperAccountIntent(intent.owner_id)
}
export function clearPaperAccountIntent(intent: PaperAccountCreateIntent) {
  const value = intentSchema.parse(intent)
  if (value.owner_id !== session().owner_id) throw new Error('perp_paper_account_owner_rejected')
  const saved = readPaperAccountIntent(value.owner_id)
  if (!saved || canonical(saved) !== canonical(value)) throw new Error('perp_paper_intent_changed')
  const key = paperAccountIntentKey(value.owner_id)
  localStorage.removeItem(key)
  if (localStorage.getItem(key) !== null) throw new Error('perp_paper_intent_not_cleared')
}

async function receipt(raw: unknown, intent: PaperAccountCreateIntent): Promise<PaperAccountCreationReceiptV1> {
  const value = paperAccountCreationReceiptV1Schema.parse(raw), account = value.account
  const expected = intent.command.instrument_refs.map(item => canonical(item)).sort()
  const actual = Object.values(account.instrument_refs).map(item => canonical(item)).sort()
  if (value.owner_id !== session().owner_id || value.owner_id !== intent.owner_id
    || value.request_id !== intent.command.request_id || value.command_hash !== await hash(intent.command)
    || account.owner_id !== intent.owner_id || account.account_id !== intent.command.account_id
    || account.state_version !== 1 || canonical(account.profile) !== canonical(intent.command.profile)
    || account.execution_mode !== intent.command.execution_mode || canonical(expected) !== canonical(actual)) throw new Error('perp_paper_receipt_identity_mismatch')
  return value
}

export const perpetualPaperAccounts = {
  list: async () => {
    const value = paperAccountListV1Schema.parse(await request(base, transport()))
    if (value.owner_id !== session().owner_id || value.accounts.some(item => item.owner_id !== value.owner_id)) throw new Error('perp_paper_account_owner_rejected')
    return value
  },
  create: async (intent: PaperAccountCreateIntent) => {
    if (intent.owner_id !== session().owner_id) throw new Error('perp_paper_account_owner_rejected')
    return receipt(await request(base, { ...transport(true), method: 'POST', headers: { ...transport(true).headers, 'Idempotency-Key': intent.command.request_id }, body: JSON.stringify(createPerpetualPaperAccountV1Schema.parse(intent.command)) }), intent)
  },
  receipt: async (intent: PaperAccountCreateIntent) => {
    if (intent.owner_id !== session().owner_id) throw new Error('perp_paper_account_owner_rejected')
    return receipt(await request(`${base}/receipts/${encodeURIComponent(intent.command.request_id)}`, { ...transport(true), method: 'POST', body: JSON.stringify(createPerpetualPaperAccountV1Schema.parse(intent.command)) }), intent)
  },
}

export function makePaperAccountIntent(
  options: InstrumentOption[], initialCash: string,
  mode: CreatePerpetualPaperAccountV1['execution_mode'], acknowledged: boolean,
): PaperAccountCreateIntent {
  if (!/^(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?$/.test(initialCash) || !/[1-9]/.test(initialCash)) throw new Error('perp_paper_initial_cash_positive_required')
  const amount = initialCash.includes('.') ? initialCash.replace(/0+$/, '').replace(/\.$/, '') : initialCash
  if (!acknowledged) throw new Error('perp_paper_simulation_ack_required')
  if (!options.length || options.length > 2 || new Set(options.map(item => item.instrument_id)).size !== options.length
    || options.some(item => canonical(item.execution_profile) !== canonical(options[0].execution_profile))) throw new Error('perp_paper_instrument_selection_required')
  const command = createPerpetualPaperAccountV1Schema.parse({
    schema_version: 'perpetual-paper-account-create.v1', request_id: crypto.randomUUID(),
    account_id: `paper_${crypto.randomUUID()}`, instrument_refs: options.map(item => item.instrument),
    profile: options[0].execution_profile, initial_cash_usdt: amount,
    simulation_acknowledged: true, execution_mode: mode,
  })
  return { owner_id: session().owner_id, command }
}
