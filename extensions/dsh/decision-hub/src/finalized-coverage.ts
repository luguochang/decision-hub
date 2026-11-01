import { createHash } from 'node:crypto'
import { foldSurface } from '@deepseek-ai/dsh-session/surface'
import { evidenceCandidateSchema, researchCapabilityResultSchema, type EvidenceCandidate,
  type ResearchCapabilityResult } from '@decision-hub/contracts-ts'
import { z } from 'zod'
import type { SynthesisReviewContext } from './final-review-gate.js'
import { authenticatedChildResearchSources, declaredChildResearchDelivery } from './host/child-research-context.js'
import { nativeCensusCanonical } from './host/native-conversation-census.js'
import type { SessionEventLike } from './host/dsh-public.js'

const hash = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex')
const canonical = nativeCensusCanonical
const id = z.string().min(1)
const digest = z.string().regex(/^[a-f0-9]{64}$/)
const seq = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER)
const binding = z.object({ runId: id, requestHash: digest, sessionId: id, requestId: id,
  generation: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER) }).strict()
const prefix = z.object({ sessionId: id, parentSessionId: id, firstLiveSeq: seq, lifecycleId: id,
  lastSeq: z.number().int().min(-1).max(Number.MAX_SAFE_INTEGER), eventsHash: digest,
  providerCalls: z.array(z.object({ callId: id, receiptHash: digest }).strict()) }).strict()
export const childEvidenceSourceSchema = z.object({ evidence_id: id, capability_id: id,
  execution_session_id: id, native_call_id: id }).strict()
export type ChildEvidenceSource = z.infer<typeof childEvidenceSourceSchema>
type NativeFact = ResearchCapabilityResult['facts'][number]
export interface FinalizedChildSelection {
  evidence: readonly EvidenceCandidate[]
  facts: readonly NativeFact[]
  evidence_sources: readonly ChildEvidenceSource[]
}
const fact = researchCapabilityResultSchema.shape.facts.removeDefault().element
export const finalizedCoverageSchema = z.object({ schema_version: z.literal('decision-hub-finalized-coverage.v1'),
  binding, finalize_call_id: id, finalize_call_seq: seq,
  root_cutoff: z.object({ before_seq: seq, events_sha256: digest }).strict(),
  source_snapshot: z.object({ message_id: id, message_seq: seq, body_sha256: digest }).strict(),
  child_prefixes: z.array(prefix).max(63), evidence_sources: z.array(childEvidenceSourceSchema).min(1),
  evidence_candidates: z.array(evidenceCandidateSchema).min(1), facts: z.array(fact) }).strict()
export type FinalizedCoverage = z.infer<typeof finalizedCoverageSchema>

function invalid(): never { throw new Error('decision_hub_finalized_coverage_source_invalid') }
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function toolIdentity(event: SessionEventLike): { id: string; name: string } | null {
  const rows = [event.data, ...(Array.isArray(record(event.data.message)?.content)
    ? record(event.data.message)!.content as unknown[] : [])]
  for (const value of rows) {
    const row = record(value), callId = row?.callId ?? row?.toolCallId ?? row?.id
    const name = row?.name ?? row?.toolName
    if (typeof callId === 'string' && typeof name === 'string') return { id: callId, name }
  }
  return null
}
function actualCall(context: SynthesisReviewContext, callId: string) {
  const matches = context.events.filter(event => event.type === 'tool/call'
    && toolIdentity(event)?.id === callId && toolIdentity(event)?.name === 'decision_hub_synthesis_submit')
  if (matches.length !== 1 || !Number.isSafeInteger(matches[0]!.seq) || matches[0]!.seq < 1) invalid()
  const call = matches[0]!, before = context.events.filter(event => event.seq < call.seq)
  if (before.length !== call.seq || before.some((event, index) => event.seq !== index)) invalid()
  return { call, before }
}
function activeDeclaration(context: SynthesisReviewContext, call: SessionEventLike) {
  // The SDK fold validates native order and legal replacements. Raw history can
  // retain several identical old declarations; only the actual surface selects.
  const surface = foldSurface(context.events as Parameters<typeof foldSurface>[0])
  const nodes = new Set(surface.nodes)
  const messages = context.events.filter(event => nodes.has(event.seq) && event.type === 'user/message')
    .map(event => event.data)
  const declared = declaredChildResearchDelivery(context.events, messages)
  const markers = messages.filter(message => record(message.source)?.kind === 'plugin'
    && record(message.source)?.plugin === 'decision-hub-protected-context')
  const message = markers.at(-1)
  if (!declared || !message) invalid()
  const source = record(message.source)
  if (!source || Object.keys(source).sort().join(',') !== 'kind,plugin' || message.role !== 'user') invalid()
  const originals = context.events.filter(event => event.type === 'user/message' && event.data.id === message.id)
  const content = message.content
  if (originals.length !== 1 || originals[0]!.seq >= call.seq || !Array.isArray(content)
    || content.length !== 2 || record(content[0])?.type !== 'text'
    || record(content[1])?.type !== 'text' || typeof record(content[1])?.text !== 'string') invalid()
  const text = record(content[1])!.text as string, body = record(JSON.parse(text))
  if (!body || canonical(body) !== text || message.id !== body.snapshot_id
    || body.run_id !== context.binding.runId || body.root_session_id !== context.binding.sessionId
    || body.command_request_id !== context.binding.requestId || body.request_id !== context.request.request_id
    || body.request_hash !== context.binding.requestHash || body.generation !== context.binding.generation) invalid()
  const children = authenticatedChildResearchSources(context.childResearchSources)
  if (canonical(children.map(source => source.prefix)) !== canonical(declared.prefixes)) invalid()
  return { children, source_snapshot: { message_id: id.parse(message.id), message_seq: originals[0]!.seq,
    body_sha256: hash(text) } }
}
function orderedUnique(values: readonly string[]): boolean {
  return values.every((value, index) => index === 0 || values[index - 1]! < value)
}
function validateSelection(context: SynthesisReviewContext, value: FinalizedCoverage) {
  const maximum = context.request.execution_budget.max_tool_calls
  if (!Number.isSafeInteger(maximum) || maximum < 1
    || value.evidence_candidates.length > 20 * maximum || value.facts.length > 200 * maximum
    || value.evidence_sources.length > 20 * maximum
    || value.child_prefixes.reduce((sum, item) => sum + item.providerCalls.length, 0) > maximum) invalid()
  if (!orderedUnique(value.evidence_candidates.map(item => item.evidence_id))
    || !orderedUnique(value.facts.map(item => item.fact_id))) invalid()
  const children = new Map(value.child_prefixes.map(item => [item.sessionId, item]))
  if (children.size !== value.child_prefixes.length) invalid()
  for (const child of children.values()) {
    if (child.sessionId === context.binding.sessionId || child.parentSessionId === child.sessionId
      || child.firstLiveSeq > child.lastSeq + 1 || new Set(child.providerCalls.map(item => item.callId)).size !== child.providerCalls.length) invalid()
    const seen = new Set<string>([child.sessionId])
    let parent = child.parentSessionId
    while (children.has(parent)) {
      if (seen.has(parent)) invalid()
      seen.add(parent); parent = children.get(parent)!.parentSessionId
    }
  }
  const requirements = new Set(context.request.evidence_requirements.map(item => item.requirement_id))
  const evidence = new Map(value.evidence_candidates.map(item => [item.evidence_id, item]))
  for (const item of evidence.values()) {
    if (item.quality !== 'accepted' || item.research_session_id !== context.binding.sessionId
      || !requirements.has(item.requirement_id)) invalid()
  }
  const sources = new Set<string>(), covered = new Set<string>()
  for (const source of value.evidence_sources) {
    const child = children.get(source.execution_session_id), key = canonical(source)
    if (!evidence.has(source.evidence_id) || !context.request.allowed_capabilities.includes(source.capability_id)
      || !child?.providerCalls.some(item => item.callId === source.native_call_id) || sources.has(key)) invalid()
    sources.add(key); covered.add(source.evidence_id)
  }
  if (covered.size !== evidence.size) invalid()
  for (const item of value.facts) {
    if (item.quality !== 'accepted' || evidence.get(item.evidence_id)?.requirement_id !== item.requirement_id) invalid()
  }
}
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0
export function buildFinalizedCoverage(context: SynthesisReviewContext, callId: string,
  selected: FinalizedChildSelection): FinalizedCoverage | null {
  // Validate WeakSet-owned sources even when their result contributes nothing;
  // authentication failure must never become a silent root-only v4 fallback.
  authenticatedChildResearchSources(context.childResearchSources)
  if (selected.evidence.length === 0) return null
  const { call, before } = actualCall(context, callId), declaration = activeDeclaration(context, call)
  const value = finalizedCoverageSchema.parse({ schema_version: 'decision-hub-finalized-coverage.v1',
    binding: context.binding, finalize_call_id: callId, finalize_call_seq: call.seq,
    root_cutoff: { before_seq: call.seq, events_sha256: hash(canonical(before)) },
    source_snapshot: declaration.source_snapshot, child_prefixes: declaration.children.map(source => source.prefix),
    evidence_candidates: [...selected.evidence].sort((a, b) => compare(a.evidence_id, b.evidence_id)),
    facts: [...selected.facts].sort((a, b) => compare(a.fact_id, b.fact_id)),
    evidence_sources: [...selected.evidence_sources].sort((a, b) => {
      for (const key of ['evidence_id', 'capability_id', 'execution_session_id', 'native_call_id'] as const) {
        const order = compare(a[key], b[key]); if (order) return order
      }
      return 0
    }) })
  validateSelection(context, value)
  return value
}
