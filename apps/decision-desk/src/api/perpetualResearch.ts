import { z } from 'zod'
import {
  decisionSettingsCurrentViewV1Schema, perpetualResearchCommandV1Schema, perpetualResearchReceiptV1Schema,
  perpetualResearchCommandV2Schema, perpetualResearchReceiptV2Schema,
  perpetualResearchCommandV3Schema, perpetualResearchReceiptV3Schema,
  type DecisionSettingsCurrentViewV1, type PerpetualResearchReceiptV1, type PerpetualResearchReceiptV2,
  type DecisionHorizonV2, type DecisionSettingsPartitionV1,
  type PerpetualResearchReceiptV3,
  perpetualResearchClosureReceiptV1Schema, type PerpetualResearchClosureReceiptV1,
} from '../../../../packages/contracts_ts/src/generated/r2'
import { request, ApiRequestError } from './client'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { settingsApi } from './decisionSettings'

const base = '/v1/decision-settings/research'
const commandSchema = z.union([perpetualResearchCommandV1Schema, perpetualResearchCommandV2Schema, perpetualResearchCommandV3Schema])
const intentSchema = z.object({ owner_id: z.string().min(1), command: commandSchema, command_hash: z.string().regex(/^[a-f0-9]{64}$/) }).strict()
export type PerpetualResearchIntent = z.infer<typeof intentSchema>
export type { PerpetualResearchReceiptV1 }
export type PerpetualResearchReceipt = PerpetualResearchReceiptV1 | PerpetualResearchReceiptV2 | PerpetualResearchReceiptV3
export type PerpetualResearchCommandResult = PerpetualResearchReceipt | PerpetualResearchClosureReceiptV1
export type { PerpetualResearchClosureReceiptV1 }
export type { DecisionHorizonV2 }
export const perpetualResearchIntentKey = (owner: string) => `decision-hub:perpetual-research-intent.v1:${owner}`
const scopedKey = (owner: string, partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2) => `decision-hub:perpetual-research-intent.v2:${owner}:${encodeURIComponent(canonical(partition))}:${horizon}`

export function receiptRecoveryDisposition(error: unknown): 'retry' | 'reconnect' | 'stop' {
  if (error instanceof ApiRequestError) {
    if (error.kind === 'http' && (error.status === 401 || error.status === 403)) return 'reconnect'
    if (error.kind === 'timeout' || error.kind === 'network'
      || error.kind === 'http' && (error.status === 404 && error.message === 'perpetual_research_receipt_missing'
        || [502, 503, 504].includes(error.status ?? 0))) return 'retry'
  }
  if (error instanceof Error && (error.message.includes('session') || error.message.includes('owner_mismatch'))) return 'reconnect'
  return 'stop'
}

function session() {
  const value = settingsApi.session()
  if (!value) throw new Error('decision_settings_session_required')
  return value
}
async function validateIntent(value: unknown, owner: string): Promise<PerpetualResearchIntent> {
  const intent = intentSchema.parse(value)
  if (intent.owner_id !== owner || intent.command_hash !== await hash(intent.command)) throw new Error('perpetual_research_intent_identity_mismatch')
  return intent
}
export async function readPerpetualResearchIntent(owner: string, partition?: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2 = '4h'): Promise<PerpetualResearchIntent | null> {
  const raw = partition ? localStorage.getItem(scopedKey(owner, partition, horizon)) : null
  if (raw) {
    const original = await validateIntent(JSON.parse(raw), owner)
    if (original.command.horizon !== horizon || canonical(original.command.partition) !== canonical(partition)) throw new Error('perpetual_research_intent_scope_mismatch')
    return original
  }
  // Preserve previously saved V1 browser intents and their exact original bytes.
  const legacy = horizon === '4h' ? localStorage.getItem(perpetualResearchIntentKey(owner)) : null
  if (!legacy) return null
  const original = await validateIntent(JSON.parse(legacy), owner)
  if (original.command.horizon !== '4h') throw new Error('perpetual_research_intent_scope_mismatch')
  return !partition || canonical(original.command.partition) === canonical(partition) ? original : null
}
export async function readPerpetualResearchHistory(owner: string, partition: DecisionSettingsPartitionV1, horizon: DecisionHorizonV2) {
  if (session().owner_id !== owner) throw new Error('perpetual_research_owner_mismatch')
  const raw = localStorage.getItem(scopedKey(owner, partition, horizon) + ':history')
  const originals = raw ? z.array(z.string()).parse(JSON.parse(raw)) : []
  const values = await Promise.all(originals.map(value => validateIntent(JSON.parse(value), owner)))
  if (session().owner_id !== owner || values.some(value => canonical(value.command.partition) !== canonical(partition) || value.command.horizon !== horizon)) throw new Error('perpetual_research_owner_mismatch')
  return values
}

export async function savePerpetualResearchIntent(value: PerpetualResearchIntent) {
  const original = await validateIntent(value, session().owner_id)
  if (original.owner_id !== session().owner_id) throw new Error('perpetual_research_owner_mismatch')
  const legacyRaw = localStorage.getItem(perpetualResearchIntentKey(original.owner_id))
  if (legacyRaw) {
    const legacy = await validateIntent(JSON.parse(legacyRaw), original.owner_id)
    if (legacy.command.horizon !== '4h') throw new Error('perpetual_research_intent_scope_mismatch')
    const legacyKey = scopedKey(legacy.owner_id, legacy.command.partition, '4h')
    if (!localStorage.getItem(legacyKey)) {
      localStorage.setItem(legacyKey, legacyRaw)
      if (localStorage.getItem(legacyKey) !== legacyRaw) throw new Error('perpetual_research_intent_not_saved')
    }
  }
  const key = scopedKey(original.owner_id, original.command.partition, original.command.horizon), raw = canonical(original)
  const previous = localStorage.getItem(key)
  if (previous && previous !== raw) {
    await validateIntent(JSON.parse(previous), original.owner_id)
    const historyKey = key + ':history', saved = localStorage.getItem(historyKey)
    const history = saved ? z.array(z.string()).parse(JSON.parse(saved)) : []
    if (!history.includes(previous)) history.push(previous)
    const historyRaw = JSON.stringify(history)
    localStorage.setItem(historyKey, historyRaw)
    if (localStorage.getItem(historyKey) !== historyRaw) throw new Error('perpetual_research_intent_not_saved')
  }
  if (session().owner_id !== original.owner_id) throw new Error('perpetual_research_owner_mismatch')
  localStorage.setItem(key, raw)
  if (localStorage.getItem(key) !== raw) throw new Error('perpetual_research_intent_not_saved')
  await readPerpetualResearchIntent(original.owner_id, original.command.partition, original.command.horizon)
  // V1 compatibility index; non-4h requests never overwrite the old pending intent.
  if (original.command.horizon === '4h') localStorage.setItem(perpetualResearchIntentKey(original.owner_id), raw)
}
export function researchIntentMatchesCurrent(intent: PerpetualResearchIntent, current: DecisionSettingsCurrentViewV1 | null, horizon: DecisionHorizonV2 = '4h') {
  const revision = current?.revision, command = intent.command
  return Boolean(revision?.operation === 'upsert' && revision.values && current
    && revision.owner_id === intent.owner_id && canonical(revision.partition) === canonical(command.partition)
    && revision.revision_id === command.expected_settings_revision.ref && revision.payload_hash === command.expected_settings_revision.hash
    && current.state_version === command.expected_settings_state_version && command.horizon === horizon)
}
export async function makePerpetualResearchIntent(value: DecisionSettingsCurrentViewV1, horizon: DecisionHorizonV2 = '4h'): Promise<PerpetualResearchIntent> {
  const current = decisionSettingsCurrentViewV1Schema.parse(value), revision = current.revision
  const { payload_hash: seal, ...body } = revision
  if (revision.owner_id !== session().owner_id || revision.operation !== 'upsert' || !revision.values
    || revision.revision_no !== current.state_version || seal !== await hash(body)
    || revision.settings_key !== 'dsettings_' + await hash([revision.owner_id, revision.partition])) throw new Error('perpetual_research_saved_settings_required')
  const paper = revision.partition.account_kind === 'paper'
  const command = commandSchema.parse({
    schema_version: paper ? 'perpetual-research-command.v3' : horizon === '4h' ? 'perpetual-research-command.v1' : 'perpetual-research-command.v2', request_id: crypto.randomUUID(), partition: revision.partition,
    expected_settings_revision: { ref: revision.revision_id, hash: revision.payload_hash },
    expected_settings_state_version: current.state_version, horizon,
    ...(paper ? { input_policy: 'saved_preferences_current_paper_ledger.v1' } : {}),
  })
  return { owner_id: revision.owner_id, command, command_hash: await hash(command) }
}
async function receipt(raw: unknown, original: PerpetualResearchIntent): Promise<PerpetualResearchReceipt> {
  const result = z.union([perpetualResearchReceiptV1Schema, perpetualResearchReceiptV2Schema, perpetualResearchReceiptV3Schema]).parse(raw), { payload_hash: seal, ...body } = result
  if (session().owner_id !== original.owner_id || result.owner_id !== original.owner_id
    || result.request_id !== original.command.request_id || result.command_hash !== original.command_hash
    || result.command_hash !== await hash(original.command) || canonical(result.partition) !== canonical(original.command.partition)
    || canonical(result.settings_revision) !== canonical(original.command.expected_settings_revision)
    || result.schema_version !== original.command.schema_version.replace('command', 'receipt')
    || result.schema_version === 'perpetual-research-receipt.v3' && (original.command.schema_version !== 'perpetual-research-command.v3' || result.input_policy !== original.command.input_policy)
    || (result.schema_version === 'perpetual-research-receipt.v1' ? '4h' : result.horizon) !== original.command.horizon
    || seal !== await hash(body)) throw new Error('perpetual_research_receipt_identity_mismatch')
  // A session may change while SHA work was pending; do not expose a prior owner's run.
  if (session().owner_id !== original.owner_id) throw new Error('perpetual_research_owner_mismatch')
  return result
}
async function commandResult(raw: unknown, original: PerpetualResearchIntent): Promise<PerpetualResearchCommandResult> {
  if ((raw as {schema_version?:unknown})?.schema_version !== 'perpetual-research-closure-receipt.v1') return receipt(raw, original)
  const result = perpetualResearchClosureReceiptV1Schema.parse(raw), {payload_hash:seal,...body}=result
  if (session().owner_id !== original.owner_id || result.owner_id !== original.owner_id || result.request_id !== original.command.request_id
    || result.command_hash !== original.command_hash || canonical(result.command) !== canonical(original.command) || seal !== await hash(body)) throw new Error('perpetual_research_receipt_identity_mismatch')
  if (session().owner_id !== original.owner_id) throw new Error('perpetual_research_owner_mismatch')
  const key=`decision-hub:perpetual-research-closure.v1:${original.owner_id}:${original.command.request_id}`, saved=canonical(result)
  localStorage.setItem(key,saved)
  if (localStorage.getItem(key)!==saved) throw new Error('perpetual_research_closure_not_saved')
  return result
}
async function diagnosed<T>(original: PerpetualResearchIntent, operation: 'command'|'receipt'|'close', work:()=>Promise<T>):Promise<T> {
  try { return await work() } catch(error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    if (session().owner_id !== original.owner_id) throw error
    const safeCode=error instanceof ApiRequestError && /^[a-z][a-z0-9_]{0,159}$/.test(error.message) ? error.message : 'outcome_unknown'
    const record=canonical({owner_id:original.owner_id,request_id:original.command.request_id,command_hash:original.command_hash,operation,
      status:error instanceof ApiRequestError ? error.status??null : null,reason_code:safeCode,
      error_kind:error instanceof ApiRequestError?error.kind:'unknown',recorded_at:new Date().toISOString()})
    const key=`decision-hub:perpetual-research-diagnostic.v1:${original.owner_id}:${original.command.request_id}`
    localStorage.setItem(key,record)
    if(localStorage.getItem(key)!==record) throw new Error('perpetual_research_diagnostic_not_saved')
    throw error
  }
}
export const perpetualResearch = {
  command: async (value: PerpetualResearchIntent): Promise<PerpetualResearchReceipt> => {
    const original = await validateIntent(value, session().owner_id)
    const stored = await readPerpetualResearchIntent(original.owner_id, original.command.partition, original.command.horizon)
    if (!stored || canonical(stored) !== canonical(original)) throw new Error('perpetual_research_intent_not_saved')
    const identity = session()
    if (identity.owner_id !== original.owner_id) throw new Error('perpetual_research_owner_mismatch')
    try {
      return await diagnosed(original,'command',async()=>receipt(await request(base, { credentials: 'include', method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Settings-CSRF': identity.csrf_token, 'Idempotency-Key': original.command.request_id },
        body: JSON.stringify(original.command) }), original))
    } catch (error) {
      if (!(error instanceof ApiRequestError) || !['timeout', 'network'].includes(error.kind)) throw error
      // An uncertain POST may already have committed. Read its original key once;
      // the existing reader authenticates owner, account, payload and receipt.
      const recovered = await perpetualResearch.receipt(original)
      if (recovered.status === 'closed_unaccepted') throw error
      return recovered
    }
  },
  receipt: async (value: PerpetualResearchIntent, signal?: AbortSignal) => {
    const original = await validateIntent(value, session().owner_id)
    signal?.throwIfAborted()
    return diagnosed(original,'receipt',async()=> {
      const raw = await request(`${base}/commands/${encodeURIComponent(original.command.request_id)}`, { credentials: 'include', method: 'GET', signal })
      signal?.throwIfAborted()
      return commandResult(raw, original)
    })
  },
  close: async (value: PerpetualResearchIntent) => {
    const original=await validateIntent(value,session().owner_id), identity=session()
    return diagnosed(original,'close',async()=>commandResult(await request(`${base}/commands/${encodeURIComponent(original.command.request_id)}/close`,{
      credentials:'include',method:'POST',headers:{'Content-Type':'application/json','X-Settings-CSRF':identity.csrf_token,'Idempotency-Key':original.command.request_id},
      body:JSON.stringify(original.command),
    }),original))
  },
}
