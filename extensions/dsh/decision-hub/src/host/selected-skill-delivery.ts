import { createHash } from 'node:crypto'
import { researchSkillSelectedDeliveryV1Schema, researchSkillSelectedDeliveryResponseV1Schema,
  type ResearchSkillSelectedDeliveryV1, type ResearchSkillSelectedDeliveryLegacyV1,
  type ResearchSkillSelectedProviderInputV1 } from '@decision-hub/contracts-ts'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import type { NativeSessionPublic, NativeTokenMeterPublic } from './dsh-public.js'
import { providerCanonicalJson, type prepareProviderInput } from './provider-input.js'

export const SELECTED_SKILL_PLUGIN = 'decision-hub-selected-skill'
export const SELECTED_SKILL_DELIVERY_MODE = 'selected_package_provider_input.v1' as const
export type SkillDeliveryMode = 'legacy-tool' | typeof SELECTED_SKILL_DELIVERY_MODE

export const selectedSkillDeliverySchema = researchSkillSelectedDeliveryV1Schema
export const selectedSkillDeliveryDecisionSchema = researchSkillSelectedDeliveryResponseV1Schema
export type SelectedSkillDelivery = ResearchSkillSelectedDeliveryV1
export type SelectedSkillDeliveryDecision = ResearchSkillSelectedDeliveryV1 | ResearchSkillSelectedDeliveryLegacyV1
export type SelectedSkillIdentity = {
  runId: string; generation: number; requestHash: string
  rootSessionId: string; executionSessionId: string
}
type Prepared = Awaited<ReturnType<typeof prepareProviderInput>>
export type SelectedSkillInputProof = ResearchSkillSelectedProviderInputV1

const sha = (body: string) => createHash('sha256').update(body, 'utf8').digest('hex')
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
function fail(): never { throw new Error('skill_selected_delivery_invalid') }
function immutable<T>(value: T): T {
  if (typeof value === 'object' && value !== null) {
    for (const child of Object.values(value)) immutable(child)
    Object.freeze(value)
  }
  return value
}
function checkIdentity(delivery: SelectedSkillDeliveryDecision, identity: SelectedSkillIdentity): void {
  if (identity.rootSessionId !== identity.executionSessionId
    || delivery.run_id !== identity.runId || delivery.generation !== identity.generation
    || delivery.request_hash !== identity.requestHash || delivery.root_session_id !== identity.rootSessionId) fail()
}

export function parseSelectedSkillDeliveryDecision(value: unknown, identity: SelectedSkillIdentity): SelectedSkillDeliveryDecision {
  const parsed = selectedSkillDeliveryDecisionSchema.safeParse(value)
  if (!parsed.success) fail()
  checkIdentity(parsed.data, identity)
  return parsed.data.delivery_mode === 'legacy-tool' ? immutable(parsed.data) : parseSelectedSkillDelivery(parsed.data, identity)
}

/** Shape/content validation does not grant permission; Hub reauthenticates originals. */
export function parseSelectedSkillDelivery(value: unknown, identity: SelectedSkillIdentity): SelectedSkillDelivery {
  const parsed = selectedSkillDeliverySchema.safeParse(value)
  if (!parsed.success) fail()
  const result = parsed.data
  checkIdentity(result, identity)
  if (sha(result.delivery.method_body) !== result.delivery.method_body_hash
    || Buffer.byteLength(result.delivery.method_body, 'utf8') > 262_144
    || Buffer.byteLength(providerCanonicalJson(result), 'utf8') > 1_000_000) fail()
  return immutable(result)
}

export function selectedSkillMessageId(delivery: SelectedSkillDelivery): string {
  return 'selected_skill_' + sha(providerCanonicalJson(delivery))
}

/** SDK appends this real pre-step plugin message; no tool occurrence is invented. */
export function makeSelectedSkillMessage(delivery: SelectedSkillDelivery): UserMessage {
  return {
    id: selectedSkillMessageId(delivery) as UserMessage['id'], role: 'user',
    source: { kind: 'plugin', plugin: SELECTED_SKILL_PLUGIN },
    content: [{ type: 'text', text: providerCanonicalJson(delivery) }],
  }
}

function possibleDelivery(message: Record<string, unknown>, expectedId: string): boolean {
  if (message.id === expectedId || record(message.source) && message.source.plugin === SELECTED_SKILL_PLUGIN) return true
  if (!Array.isArray(message.content)) return false
  return message.content.some(block => {
    if (!record(block) || block.type !== 'text' || typeof block.text !== 'string') return false
    try { const body: unknown = JSON.parse(block.text); return record(body) && body.schema_version === 'research-selected-skill-delivery.v1' }
    catch { return false }
  })
}

/** Returns the exact real occurrence or rejects any duplicate/changed source. */
export function selectedSkillMessageIndex(messages: readonly unknown[], delivery: SelectedSkillDelivery): number | null {
  const body = providerCanonicalJson(delivery), expectedId = selectedSkillMessageId(delivery)
  const occurrences = messages.flatMap((message, index) => record(message) && possibleDelivery(message, expectedId) ? [{ message, index }] : [])
  if (occurrences.length === 0) return null
  if (occurrences.length !== 1) fail()
  const { message, index } = occurrences[0]!
  if (message.id !== expectedId || message.role !== 'user' || !record(message.source)
    || message.source.kind !== 'plugin' || message.source.plugin !== SELECTED_SKILL_PLUGIN
    || Object.keys(message.source).length !== 2 || !Array.isArray(message.content) || message.content.length !== 1) fail()
  const text = message.content[0]
  if (!record(text) || text.type !== 'text' || text.text !== body || Object.keys(text).length !== 2) fail()
  return index
}

/** Replace only an exact older native surface node. Its original bytes remain
 * in the SDK event log and cannot qualify this generation's loaded proof. */
export function replaceHistoricalSelectedSkillMessage(delivery: SelectedSkillDelivery,
  session: NativeSessionPublic | undefined, tokenMeter: NativeTokenMeterPublic | undefined): boolean {
  const surface = session?.deriveMessages?.() ?? []
  const expectedId = selectedSkillMessageId(delivery)
  const markers = surface.filter(message => possibleDelivery(message as unknown as Record<string, unknown>, expectedId))
  if (markers.length === 0) return false
  if (markers.length !== 1) fail()
  const actual = markers[0]!
  if (actual.id === expectedId) {
    selectedSkillMessageIndex(surface, delivery)
    return false
  }
  if (session?.id !== delivery.root_session_id || session.header?.parentSession !== undefined
    || !session.events || typeof session.append !== 'function' || !tokenMeter
    || typeof tokenMeter.estimateMessage !== 'function' || actual.content.length !== 1
    || actual.content[0]?.type !== 'text') fail()
  let decoded: unknown
  try { decoded = JSON.parse(actual.content[0].text) } catch { fail() }
  const parsed = selectedSkillDeliverySchema.safeParse(decoded)
  if (!parsed.success || parsed.data.generation >= delivery.generation) fail()
  const older = parseSelectedSkillDelivery(parsed.data, {
    runId: delivery.run_id, generation: parsed.data.generation, requestHash: delivery.request_hash,
    rootSessionId: delivery.root_session_id, executionSessionId: delivery.root_session_id,
  })
  if (providerCanonicalJson(actual) !== providerCanonicalJson(makeSelectedSkillMessage(older))) fail()
  const originals = session.events.filter(event => event.type === 'user/message'
    && record(event.data) && event.data.id === actual.id)
  if (originals.length !== 1 || providerCanonicalJson(originals[0]!.data) !== providerCanonicalJson(actual)
    || session.events.some(event => event.type === 'user/message' && record(event.data) && event.data.id === expectedId)) fail()
  const original = originals[0]!, proposed = makeSelectedSkillMessage(delivery)
  const count = tokenMeter.estimateMessage(actual)
  if (!Number.isSafeInteger(original.seq) || original.seq < 0 || !Number.isSafeInteger(count) || count < 0) fail()
  session.append('compaction/prune', { shadowedRange: { start: original.seq, end: original.seq },
    shadowedSeqs: [original.seq], shadowedTokenCount: count })
  session.append('user/message', proposed, { surfaceOp: { op: 'replace', start: original.seq, end: original.seq },
    sourceEventSeqs: [original.seq] })
  if (providerCanonicalJson(session.deriveMessages!())
    !== providerCanonicalJson(surface.map(message => message === actual ? proposed : message))) fail()
  return true
}

/** Only the actual saved Provider input can propose loaded; Kernel verifies it again. */
export function selectedSkillProofFromProviderInput(prepared: Prepared, delivery: SelectedSkillDelivery): SelectedSkillInputProof {
  const p = prepared.identity
  checkIdentity(delivery, { runId: p.run_id, generation: p.generation, requestHash: p.request_hash,
    rootSessionId: p.root_session_id, executionSessionId: p.execution_session_id })
  if (prepared.receipt.producer_authenticated !== true
    || prepared.receipt.proof_scope !== 'dsh_llm_generate_options'
    || prepared.receipt.dispatch_id !== p.dispatch_id || prepared.receipt.input_hash !== sha(prepared.inputJson)
    || !/^[a-f0-9]{64}$/.test(prepared.receipt.reference.payload_hash)
    || prepared.receipt.reference.schema_version !== 'research-provider-input.v1'
    || Buffer.byteLength(prepared.inputJson, 'utf8') > 1_000_000) fail()
  let body: unknown
  try { body = JSON.parse(prepared.inputJson) } catch { fail() }
  if (!record(body) || !Array.isArray(body.messages)) fail()
  const index = selectedSkillMessageIndex(body.messages, delivery)
  if (index === null) fail()
  return {
    kind: 'selected_skill_provider_input.v1', input_ref: { ...prepared.receipt.reference },
    dispatch_id: p.dispatch_id, selection_ref: { ...delivery.selection_ref },
    selected_event_ref: { ...delivery.selected_event_ref },
    message_json_pointer: `/messages/${index}`, body_json_pointer: `/messages/${index}/content/0/text`,
    envelope_hash: sha(providerCanonicalJson(delivery)), method_body_hash: delivery.delivery.method_body_hash,
    delivered_package_hash: delivery.delivery.package_hash,
  }
}

export function selectedSkillStageRequestId(identity: SelectedSkillIdentity, proof: SelectedSkillInputProof): string {
  const wire = { run_id: identity.runId, generation: identity.generation, request_hash: identity.requestHash,
    root_session_id: identity.rootSessionId, execution_session_id: identity.executionSessionId }
  return 'skill_selected_' + sha(providerCanonicalJson([wire, proof.selection_ref, proof.input_ref]))
}
