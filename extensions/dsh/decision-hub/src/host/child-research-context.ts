import { createHash } from 'node:crypto'
import type { ResearchProviderToolEmissionRecorded } from '@decision-hub/contracts-ts'
import type { CancellationBinding } from './command-cancellation.js'
import { isSessionEventLike, type SessionControllerPublic, type SessionEventLike } from './dsh-public.js'
import type { HubClient } from './hub-client.js'
import { nativeCensusCanonical, type NativeResearchSourceDescriptor } from './native-conversation-census.js'

export interface ChildResearchPrefix extends NativeResearchSourceDescriptor {
  readonly lastSeq: number
  readonly eventsHash: string
  readonly providerCalls: readonly { readonly callId: string; readonly receiptHash: string }[]
}
export interface ChildResearchSource {
  readonly prefix: ChildResearchPrefix
  readonly events: readonly SessionEventLike[]
  readonly providerEmissions: readonly ResearchProviderToolEmissionRecorded[]
}
export type PendingEvidenceDelivery = { readonly evidence_id: string; readonly requirement_id: string;
  readonly reason_code: 'native_delivery_pending' }
const authentic = new WeakSet<object>()
function fail(): never { throw new Error('research_provider_native_context_invalid') }
const digest = (value: unknown) => createHash('sha256').update(nativeCensusCanonical(value)).digest('hex')
function frozen<T>(value: T): T {
  if (typeof value === 'object' && value !== null) { Object.values(value).forEach(frozen); Object.freeze(value) }
  return value
}
function researchCalls(events: readonly SessionEventLike[]) {
  return events.flatMap(event => {
    if (event.type !== 'tool/call') return []
    const row = event.data, id = row.callId ?? row.toolCallId ?? row.id, name = row.toolName ?? row.name
    if (!['decision_hub_research', 'research_capability_execute'].some(tool => String(name).endsWith(tool))) return []
    let args = row.arguments
    if (typeof args === 'string') { try { args = JSON.parse(args) } catch { fail() } }
    if (typeof id !== 'string' || !id || args === undefined) fail()
    return [{ id, name, args }]
  })
}

/** No guessed child discovery or inherited seed history participates in research proof. */
export async function readChildResearchSources(input: {
  binding: CancellationBinding
  descriptors: readonly NativeResearchSourceDescriptor[]
  controller: Pick<SessionControllerPublic, 'inspect'>
  hub: Pick<HubClient, 'providerToolEmission'>
  signal: AbortSignal
  deadlineAt: string
  declared?: readonly ChildResearchPrefix[]
}): Promise<readonly ChildResearchSource[]> {
  const { binding, descriptors, signal } = input
  const byId = new Map(descriptors.map(value => [value.sessionId, value]))
  if (byId.size !== descriptors.length || descriptors.length > 63) fail()
  for (const descriptor of descriptors) {
    const seen = new Set([descriptor.sessionId])
    let current = descriptor
    if (descriptor.sessionId === binding.sessionId || !Number.isSafeInteger(descriptor.firstLiveSeq)
      || descriptor.firstLiveSeq < 0 || !descriptor.lifecycleId) fail()
    while (current.parentSessionId !== binding.sessionId) {
      if (seen.has(current.parentSessionId)) fail()
      seen.add(current.parentSessionId)
      const parent = byId.get(current.parentSessionId)
      if (!parent) fail()
      current = parent
    }
  }
  const selected = input.declared ?? descriptors
  if (new Set(selected.map(value => value.sessionId)).size !== selected.length) fail()
  const sources: ChildResearchSource[] = []
  for (const selection of selected) {
    signal.throwIfAborted()
    const descriptor = byId.get(selection.sessionId)
    if (!descriptor || descriptor.parentSessionId !== selection.parentSessionId
      || descriptor.firstLiveSeq !== selection.firstLiveSeq || descriptor.lifecycleId !== selection.lifecycleId) fail()
    const inspected = await input.controller.inspect(descriptor.sessionId, signal)
    signal.throwIfAborted()
    if (inspected.meta.id !== descriptor.sessionId || inspected.meta.parentSession !== descriptor.parentSessionId
      || inspected.events.length > 50_000 || inspected.events.some((event, index) => !isSessionEventLike(event) || event.seq !== index)) fail()
    const pinned = input.declared === undefined ? undefined : selection as ChildResearchPrefix
    const lastSeq = pinned?.lastSeq ?? inspected.events.at(-1)?.seq ?? -1
    if (!Number.isSafeInteger(lastSeq) || lastSeq < descriptor.firstLiveSeq - 1 || lastSeq >= inspected.events.length) fail()
    const events = structuredClone(inspected.events.slice(descriptor.firstLiveSeq, lastSeq + 1))
    if (Buffer.byteLength(nativeCensusCanonical(events), 'utf8') > 8_388_608) fail()
    const eventsHash = digest(events), calls = researchCalls(events)
    if (new Set(calls.map(call => call.id)).size !== calls.length) fail()
    if (pinned && eventsHash !== pinned.eventsHash) fail()
    const providerEmissions: ResearchProviderToolEmissionRecorded[] = []
    for (const call of calls) {
      const proof = await input.hub.providerToolEmission(binding.runId, binding.generation, descriptor.sessionId,
        call.id, signal, input.deadlineAt)
      signal.throwIfAborted()
      if (proof.run_id !== binding.runId || proof.generation !== binding.generation
        || proof.root_session_id !== binding.sessionId || proof.execution_session_id !== descriptor.sessionId
        || proof.call_id !== call.id || proof.tool_name !== call.name || proof.arguments_hash !== digest(call.args)) fail()
      providerEmissions.push(structuredClone(proof))
    }
    const providerCalls = providerEmissions.map(proof => ({ callId: proof.call_id, receiptHash: digest(proof) }))
    if (pinned && nativeCensusCanonical(pinned.providerCalls) !== nativeCensusCanonical(providerCalls)) fail()
    // An empty child need not expand the delivered root wire.
    if (calls.length === 0 && input.declared === undefined) continue
    const source = frozen({ prefix: { ...descriptor, lastSeq, eventsHash, providerCalls }, events, providerEmissions })
    authentic.add(source)
    sources.push(source)
  }
  return frozen(sources)
}

export function authenticatedChildResearchSources(sources: readonly ChildResearchSource[] | undefined): readonly ChildResearchSource[] {
  if (sources === undefined) return []
  if (sources.some(source => !authentic.has(source) || digest(source.events) !== source.prefix.eventsHash)) fail()
  return sources
}

/** A declared prefix must itself be an exact official root message. */
export function declaredChildResearchDelivery(events: readonly SessionEventLike[], messages: readonly unknown[]):
  { prefixes: readonly ChildResearchPrefix[]; pending: readonly PendingEvidenceDelivery[] } | undefined {
  const markers = messages.filter((value: any) => value?.role === 'user' && value?.source?.kind === 'plugin'
    && value.source.plugin === 'decision-hub-protected-context') as any[]
  const actual = markers.at(-1)
  if (!actual) return undefined
  let body: any
  try { body = JSON.parse(actual.content?.[1]?.text) } catch { return fail() }
  if (body.schema_version !== 'decision-hub-protected-context.v5'
    && body.schema_version !== 'decision-hub-protected-context.v6') return undefined
  const originals = events.filter(event => event.type === 'user/message' && event.data.id === actual.id)
  if (originals.length !== 1 || nativeCensusCanonical(originals[0]!.data) !== nativeCensusCanonical(actual)
    || !Array.isArray(body.child_research_prefixes) || !Array.isArray(body.pending_evidence_delivery)) fail()
  return { prefixes: body.child_research_prefixes, pending: body.pending_evidence_delivery }
}
