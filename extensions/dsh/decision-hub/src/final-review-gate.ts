import { baselineResults, type ResearchBaselineDelivery } from './host/baseline.js'
import { createHash } from 'node:crypto'
import { independentReviewReceiptV2Schema, researchDecisionSynthesisV1Schema, researchSynthesisCandidateSchema, researchCapabilityResultSchema, type EvidenceCandidate, type ResearchCapabilityResult, type ResearchSessionRequest, type ResearchSynthesisCandidate } from '@decision-hub/contracts-ts'
import { z } from 'zod'
import { isDecisionCandidate, parseSynthesisCandidate, researchCandidate, synthesisProtocol, type SynthesisCandidate } from './synthesis-protocol.js'
import { deterministicIds } from './host/session-correlation.js'
import type { CancellationBinding } from './host/command-cancellation.js'
import type { ModelAgentLineage, SessionEventLike } from './host/dsh-public.js'
import { actionReviewRequestV2Schema, researchDecisionSynthesisV2Schema, actionReviewRequestV3Schema, researchDecisionSynthesisV3Schema, type ResearchDecisionSynthesisV2 } from '@decision-hub/contracts-ts'
import { bodyHash, canonicalJson, parsePerpetualReviewPacket, parsePerpetualSynthesis, textHash, validatePerpetualAuthorization, validatePerpetualReviewOriginals, reviewOriginalsFromPrompt, modelAssumptionsFromPrompt, perpetualFindingsFromOutput, primaryPerpetualSynthesis, primaryPerpetualReviewPacket, type PerpetualSynthesis, type PerpetualReviewPacket, type PreviousPerpetualReview } from './perpetual-synthesis-protocol.js'
import { perpetualPrepareArgumentsV1Schema, perpetualPrepareArgumentsV2Schema, perpetualFinalizeArgumentsV1Schema, perpetualPreparationResultV1Schema, perpetualPreparationResultV2Schema, perpetualPreparationWitnessV1Schema, perpetualPreparationWitnessV2Schema, type DecisionReviewOriginalsV1, type PerpetualPreparationWitnessV1, type PerpetualPreparationWitnessV2 } from '@decision-hub/contracts-ts'
import { assertCurrentPerpetualPublicationCostBinding, preparePerpetualSynthesis } from './perpetual-preparation.js'
import { authenticatedChildResearchSources, type ChildResearchSource, type PendingEvidenceDelivery } from './host/child-research-context.js'
import { hasExactReviewerPrompt } from './reviewer-system-snapshot.js'
import { finalizedCoverageSchema, type ChildEvidenceSource, type FinalizedChildSelection } from './finalized-coverage.js'

export const perpetualPrepareArgumentsSchema = z.union([perpetualPrepareArgumentsV1Schema, perpetualPrepareArgumentsV2Schema])
export const perpetualPreparationResultSchema = z.union([perpetualPreparationResultV1Schema, perpetualPreparationResultV2Schema])
type PerpetualPreparationWitness = PerpetualPreparationWitnessV1 | PerpetualPreparationWitnessV2

export interface SynthesisReviewContext {
  readonly binding: CancellationBinding
  readonly request: ResearchSessionRequest
  readonly events: readonly SessionEventLike[]
  readonly admittedEvidence: readonly EvidenceCandidate[]
  /** Separately owned native namespaces, already authenticated against Provider call originals. */
  readonly childResearchSources?: readonly ChildResearchSource[]
  readonly deliveredPendingEvidence?: readonly PendingEvidenceDelivery[]
  readonly serverBaseline?: ResearchBaselineDelivery
  readonly perpetualReviewOriginals?: DecisionReviewOriginalsV1
  readonly perpetualBinding?: Pick<ResearchDecisionSynthesisV2, 'opportunity_id' | 'method_ref' | 'method_code_hash'>
    & Pick<ResearchDecisionSynthesisV2['candidate_set'], 'context' | 'candidate_target_ref' | 'anchor_rule_ref'>
}
export type CurrentSynthesisReviewContextReader = (agent: ModelAgentLineage, signal: AbortSignal, currentCallId?: string) => Promise<SynthesisReviewContext>
export type SynthesisReviewContextReader = (agent: ModelAgentLineage, candidateRequestId: string, signal: AbortSignal, currentCallId?: string) => Promise<SynthesisReviewContext>

// This registry wires the live Host service to a tool plugin. It stores no candidate,
// receipt, attempt or checkpoint; every decision is rebuilt from official durable events.
const readers = new WeakMap<object, { synthesis: SynthesisReviewContextReader; current?: CurrentSynthesisReviewContextReader }>()
export function registerSynthesisReviewContext(root: object, reader: SynthesisReviewContextReader, current?: CurrentSynthesisReviewContextReader): () => void {
  if (readers.has(root)) throw new Error('decision_hub_synthesis_reader_conflict')
  const installed = { synthesis: reader, current }
  readers.set(root, installed)
  return () => { if (readers.get(root) === installed) readers.delete(root) }
}
export async function readSynthesisReviewContext(root: object, agent: ModelAgentLineage, candidateRequestId: string, signal: AbortSignal, currentCallId?: string): Promise<SynthesisReviewContext> {
  const reader = readers.get(root)
  if (!reader) throw new Error('decision_hub_synthesis_reader_unavailable')
  return reader.synthesis(agent, candidateRequestId, signal, currentCallId)
}

type JsonRecord = Record<string, unknown>

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function uniqueStrings(value: unknown): unknown {
  if (!Array.isArray(value)) return value
  if (!value.every(item => typeof item === 'string')) return value
  return [...new Set(value)]
}

function normalizeLinks(value: unknown): unknown {
  if (!Array.isArray(value)) return value
  return value.map((link) => (
    isRecord(link)
      ? { ...link, evidence_refs: uniqueStrings(link.evidence_refs) }
      : link
  ))
}

/**
 * Repair representation-only mistakes before schema validation.
 *
 * This never invents a claim or Evidence ID. It only removes duplicate IDs and
 * fills the causal-case summary list from IDs already cited by the submitted
 * links. The Hub still verifies every resulting ID against the trusted Session.
 */
export function normalizeSynthesisCandidate(value: unknown): unknown {
  if (!isRecord(value)) return value
  const normalized: JsonRecord = { ...value }
  // Some providers occasionally place the horizon array inside causal_case
  // even though the contract makes it a sibling of causal_case. This is a
  // representation-only repair: move the exact array, without changing any
  // horizon object, only when the required top-level field is absent. If both
  // locations are present we leave the payload untouched so the strict schema
  // reports the ambiguity instead of silently choosing one version.
  if (
    value.horizons === undefined
    && isRecord(value.causal_case)
    && Array.isArray(value.causal_case.horizons)
  ) {
    const { horizons, ...causalCase } = value.causal_case
    normalized.horizons = horizons
    normalized.causal_case = causalCase
  }
  if (Array.isArray(normalized.horizons)) {
    normalized.horizons = normalized.horizons.map((horizon) => (
      isRecord(horizon)
        ? { ...horizon, evidence_refs: uniqueStrings(horizon.evidence_refs) }
        : horizon
    ))
  }
  if (!isRecord(normalized.causal_case)) return normalized

  const causalCase: JsonRecord = {
    ...normalized.causal_case,
    main_chain: normalizeLinks(normalized.causal_case.main_chain),
    opposite_chain: normalizeLinks(normalized.causal_case.opposite_chain),
  }
  if (normalized.causal_case.evidence_refs === undefined) {
    const cited: string[] = []
    for (const chainName of ['main_chain', 'opposite_chain']) {
      const chain = causalCase[chainName]
      if (!Array.isArray(chain)) continue
      for (const link of chain) {
        if (!isRecord(link) || !Array.isArray(link.evidence_refs)) continue
        for (const evidenceId of link.evidence_refs) {
          if (typeof evidenceId === 'string') cited.push(evidenceId)
        }
      }
    }
    causalCase.evidence_refs = [...new Set(cited)]
  } else {
    causalCase.evidence_refs = uniqueStrings(normalized.causal_case.evidence_refs)
  }
  normalized.causal_case = causalCase
  return normalized
}

const legacyReceiptSchema = z.object({
  schema_version: z.literal('independent-review-receipt.v1'), role: z.literal('independent_reviewer'),
  target_claim: z.string().min(1), evidence_refs: z.array(z.string().min(1)).min(1), challenge: z.string().min(1),
  resolution: z.enum(['accepted', 'bounded', 'requires_new_evidence', 'rejected']),
  limitations: z.preprocess(value => typeof value === 'string' ? [value] : value, z.array(z.string().min(1))).default([]),
}).strict()
const receiptSchema = z.union([legacyReceiptSchema, independentReviewReceiptV2Schema])
type Receipt = z.infer<typeof receiptSchema>

/** Return safe schema paths only; never quote untrusted review text in errors. */
export function validateFrozenReviewerOutput(output: unknown, version: Receipt['schema_version'], target: string,
  allowed: ReadonlySet<string>): { receipt: Receipt | null; errors: readonly string[] } {
  const values = records(output).filter(row => row.schema_version === version)
  if (values.length !== 1) return { receipt: null, errors: ['receipt:exactly_one_required'] }
  const parsed = (version === 'independent-review-receipt.v2' ? independentReviewReceiptV2Schema : legacyReceiptSchema).safeParse(values[0])
  if (!parsed.success) return { receipt: null, errors: parsed.error.issues.slice(0, 16).map(issue => {
    const expected = 'options' in issue && Array.isArray(issue.options) ? `:expected=${issue.options.join('|')}` : ''
    return `${issue.path.join('.') || 'receipt'}:${issue.code}${expected}`
  }) }
  if (parsed.data.target_claim !== target) return { receipt: null, errors: ['target_claim:frozen_target_mismatch'] }
  if (!reviewReceiptHasValidReferences(parsed.data, allowed)) return { receipt: null, errors: ['receipt:references_or_issue_semantics_invalid'] }
  return { receipt: parsed.data, errors: [] }
}

/** A strict receipt is still a finding, never proof of financial qualification. */
export function reviewReceiptHasValidReferences(receipt: Receipt, allowed: ReadonlySet<string>): boolean {
  const valid = (refs: readonly string[]) => refs.length === new Set(refs).size && refs.every(ref => allowed.has(ref))
  if (!valid(receipt.evidence_refs) || !receipt.challenge.trim() || !receipt.limitations.every(item => item.trim())) return false
  if (receipt.schema_version === 'independent-review-receipt.v1') return true
  if (receipt.issues.length === 0 && receipt.resolution !== 'accepted') return false
  if (!receipt.reviewer_version.trim() || receipt.issues.length !== new Set(receipt.issues.map(issue => issue.issue_id)).size) return false
  return receipt.issues.every(issue => issue.issue_id.trim() && issue.reason.trim()
    && valid(issue.evidence_refs) && valid(issue.resolution_evidence_refs)
    && issue.blocking_scope.length === new Set(issue.blocking_scope).size
    && (issue.status !== 'answered' || issue.disposition === 'accepted')
    && (issue.status !== 'rejected' || issue.disposition === 'rebutted')
    && (!['open', 'deferred'].includes(issue.status) || issue.disposition === 'unresolved'))
}
type RecordValue = Record<string, unknown>
function record(value: unknown): RecordValue | null { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : null }
function decode(value: unknown): unknown {
  if (typeof value !== 'string') return value
  if (Buffer.byteLength(value, 'utf8') > 1_000_000) return null
  try { return JSON.parse(value) } catch { return null }
}
// These strings are complete V4 native proof, not a model-authored argument.
// Their exact scope, hashes and transcript are authenticated by the V4 reader.
function decodePerpetualReviewProof(value: unknown): unknown {
  if (typeof value !== 'string') return value
  try { return JSON.parse(value) } catch { return null }
}
function records(value: unknown, depth = 0): RecordValue[] {
  if (depth > 12) return []
  const decoded = decode(value)
  if (Array.isArray(decoded)) return decoded.flatMap(item => records(item, depth + 1))
  const row = record(decoded)
  return row ? [row, ...Object.values(row).flatMap(item => records(item, depth + 1))] : []
}
function field(row: RecordValue, names: string[]): string | null {
  for (const name of names) { if (typeof row[name] === 'string' && row[name]) return row[name] as string }
  return null
}
function callIdentity(event: SessionEventLike): { id: string; name: string; arguments: unknown } | null {
  const rows = [event.data, ...records(event.data).filter(row => row.type === 'tool-call' || row.type === 'tool_call')]
  for (const row of rows) {
    const id = field(row, ['toolCallId', 'callId', 'id']), name = field(row, ['toolName', 'name'])
    if (id && name) return { id, name, arguments: decode(row.arguments) }
  }
  return null
}
function successfulResult(event: SessionEventLike, callId: string): boolean {
  if (!['tool/result', 'tool/call-result'].includes(event.type)) return false
  if (event.data.error != null || event.data.isError === true || event.data.status === 'error') return false
  const rows = returnedRows(event, callId)
  return rows.length === 1 && !isFailed(rows[0]!)
}
function rpc(event: SessionEventLike): string | null {
  if (event.type !== 'user/message') return null
  const source = record(event.data.source)
  return source?.kind === 'user' && typeof source.rpcId === 'string' ? source.rpcId : null
}
function canonical(value: unknown): string {
  const sort = (item: unknown): unknown => Array.isArray(item) ? item.map(sort)
    : record(item) ? Object.fromEntries(Object.entries(item as RecordValue).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, child]) => [key, sort(child)])) : item
  return JSON.stringify(sort(value))
}
function normalized(context: SynthesisReviewContext, event: SessionEventLike, normalize: (value: unknown) => unknown): SynthesisCandidate | null {
  const args = record(callIdentity(event)?.arguments)
  try { return parseSynthesisCandidate(context.request.output_schema_ref, args?.candidate, normalize) }
  catch { return null }
}
export type FinalReviewGateMetadata = {
  readonly schema_version: 'decision-hub-final-review-gate.v1' | 'decision-hub-final-review-gate.v2'
  readonly status: 'matched' | 'unreviewed'
  readonly reason: 'exact_target' | 'review_attempt_used' | 'subagent_budget_exhausted' | 'review_instruction_exhausted' | 'frozen_candidate_changed' | 'continuation_candidate_changed' | 'no_causal_candidate'
  readonly binding: CancellationBinding
  readonly candidate_json: string
  readonly candidate_sha256: string
  readonly review_receipt_hash: string | null
  readonly review_call_id: string | null
}

const REQUIRED = 'decision_hub_final_review_required'
const bindingSchema = z.object({ runId: z.string().min(1), requestHash: z.string().regex(/^[a-f0-9]{64}$/),
  sessionId: z.string().min(1), requestId: z.string().min(1), generation: z.number().int().min(1) }).strict()
const metadataSchema = z.object({ schema_version: z.literal('decision-hub-final-review-gate.v1'),
  status: z.enum(['matched', 'unreviewed']), reason: z.enum(['exact_target', 'review_attempt_used', 'subagent_budget_exhausted',
    'review_instruction_exhausted', 'frozen_candidate_changed', 'continuation_candidate_changed', 'no_causal_candidate']),
  binding: bindingSchema, candidate_json: z.string().max(1_000_000), candidate_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  review_receipt_hash: z.string().regex(/^[a-f0-9]{64}$/).nullable(), review_call_id: z.string().min(1).nullable(),
}).strict()
const synthesisResultSchema = z.object({ schema_version: z.literal('decision-hub-synthesis-tool-result.v1'),
  candidate: researchSynthesisCandidateSchema, review_gate: metadataSchema }).strict()
const metadataV2Schema = metadataSchema.extend({ schema_version: z.literal('decision-hub-final-review-gate.v2') }).strict()
const synthesisResultV2Schema = z.object({ schema_version: z.literal('decision-hub-synthesis-tool-result.v2'),
  candidate: researchDecisionSynthesisV1Schema, review_gate: metadataV2Schema }).strict()
// Reused by the tool's last-mile content projection; parsing is not attestation.
export const perpetualGateSchema = metadataSchema.extend({ schema_version: z.literal('decision-hub-final-review-gate.v3'),
  review_request_json: z.string().max(1_000_000), review_request_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  freeze_call_id: z.string().min(1).nullable(), freeze_seq: z.number().int().min(0).nullable(),
}).strict()
export const perpetualSynthesisToolResultSchema = z.object({ schema_version: z.literal('decision-hub-synthesis-tool-result.v3'),
  candidate: researchDecisionSynthesisV2Schema, review_request: actionReviewRequestV2Schema, review_gate: perpetualGateSchema }).strict()
export const preparedPerpetualSynthesisToolResultSchema = perpetualSynthesisToolResultSchema.extend({ schema_version:z.literal('decision-hub-synthesis-tool-result.v4'),
  candidate:z.union([researchDecisionSynthesisV2Schema,researchDecisionSynthesisV3Schema]),
  review_request:z.union([actionReviewRequestV2Schema,actionReviewRequestV3Schema]),
  preparation:z.union([perpetualPreparationWitnessV1Schema,perpetualPreparationWitnessV2Schema]) }).strict()
export const finalizedPerpetualSynthesisToolResultSchema = preparedPerpetualSynthesisToolResultSchema.extend({
  schema_version: z.literal('decision-hub-synthesis-tool-result.v5'), coverage_delivery: finalizedCoverageSchema }).strict()
export const synthesisToolResultSchema = z.union([synthesisResultSchema, synthesisResultV2Schema, perpetualSynthesisToolResultSchema,
  preparedPerpetualSynthesisToolResultSchema, finalizedPerpetualSynthesisToolResultSchema])
const foregroundSchema = z.object({ kind: z.literal('foreground'), runId: z.string().min(1), output: z.array(z.unknown()) }).strict()
const frozenReviewResultV1Schema = z.object({ schema_version: z.literal('decision-hub-frozen-review-result.v1'),
  binding: bindingSchema, freeze_call_id: z.string().min(1), freeze_seq: z.number().int().min(0),
  candidate_json: z.string().max(1_000_000), candidate_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  expected_target_claim: z.string().min(1), actual_prompt: z.string().max(1_000_000),
  actual_prompt_sha256: z.string().regex(/^[a-f0-9]{64}$/), child_session_id: z.string().min(1), original_output: foregroundSchema,
}).strict()
const repairSchema = z.object({ schema_version: z.literal('decision-hub-review-schema-repair.v1'), attempt: z.literal(1),
  child_parent_session_id: z.string().min(1), invalid_output_json: z.string().max(1_000_000),
  invalid_output_sha256: z.string().regex(/^[a-f0-9]{64}$/), validation_errors: z.array(z.string().min(1).max(512)).min(1).max(16),
  repair_prompt: z.string().max(32_000), repair_prompt_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  event_start_seq: z.number().int().min(0), event_end_seq: z.number().int().min(0),
  events_json: z.string().max(1_000_000), events_sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict()
const frozenReviewResultV2Schema = frozenReviewResultV1Schema.extend({
  schema_version: z.literal('decision-hub-frozen-review-result.v2'), schema_repair: repairSchema.nullable(),
}).strict()
const frozenReviewResultV3Schema = frozenReviewResultV2Schema.extend({ schema_version: z.literal('decision-hub-frozen-review-result.v3') }).strict()
export const frozenReviewResultSchema = z.union([frozenReviewResultV1Schema, frozenReviewResultV2Schema, frozenReviewResultV3Schema])
export const perpetualFrozenReviewResultSchema = z.object({ schema_version: z.literal('decision-hub-frozen-review-result.v4'),
  binding: bindingSchema, freeze_call_id: z.string().min(1), freeze_seq: z.number().int().min(0),
  candidate_json: z.string().max(1_000_000), candidate_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  review_request_json: z.string().max(1_000_000), review_request_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  actual_prompt: z.string().max(1_000_000), actual_prompt_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  child_session_id: z.string().min(1), child_parent_session_id: z.string().min(1),
  child_events_json: z.string(), child_events_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  original_output: foregroundSchema,
}).strict()

/** Exact one child-owned follow-up suffix. This does not attest model understanding. */
export function validFrozenReviewRepair(wrapper: z.infer<typeof frozenReviewResultSchema>, allowed: ReadonlySet<string>): boolean {
  if (wrapper.schema_version === 'decision-hub-frozen-review-result.v1' || wrapper.schema_repair === null) return true
  const repair = wrapper.schema_repair
  if (repair.child_parent_session_id !== wrapper.binding.sessionId || hash(repair.invalid_output_json) !== repair.invalid_output_sha256
    || hash(repair.repair_prompt) !== repair.repair_prompt_sha256 || hash(repair.events_json) !== repair.events_sha256) return false
  const before = foregroundSchema.safeParse(decode(repair.invalid_output_json)), suffix = decode(repair.events_json)
  if (!before.success || before.data.runId !== wrapper.child_session_id || !Array.isArray(suffix) || suffix.length === 0) return false
  if (validateFrozenReviewerOutput(before.data.output, 'independent-review-receipt.v2', wrapper.expected_target_claim, allowed).receipt !== null) return false
  const events = suffix.map(record)
  if (events.some(event => event === null || !Number.isSafeInteger(event.seq))
    || events.some((event, index) => index > 0 && Number(event!.seq) <= Number(events[index - 1]!.seq))
    || events[0]!.seq !== repair.event_start_seq || events.at(-1)!.seq !== repair.event_end_seq) return false
  const prompts = events.filter(event => event!.type === 'user/message')
  const ends = events.filter(event => event!.type === 'turn/end')
  if (prompts.length !== 1 || ends.length !== 1 || record(record(ends[0]!.data)?.reason)?.kind !== 'completed') return false
  const message = record(record(prompts[0]!.data)?.message) ?? record(prompts[0]!.data)
  if (record(message?.source)?.kind !== 'user' || canonical(message?.content) !== canonical([{ type: 'text', text: repair.repair_prompt }])) return false
  const answers = events.filter(event => event!.type === 'assistant/message').map(event => record(record(event!.data)?.message)?.content)
    .filter(content => Array.isArray(content) && content.length > 0)
  return answers.length > 0 && canonical(answers.at(-1)) === canonical(wrapper.original_output.output)
}
const reviewInputSchema = z.object({ schema_version: z.literal('decision-hub-frozen-review-input.v1'),
  candidate_sha256: z.string().regex(/^[a-f0-9]{64}$/), expected_target_claim: z.string().min(1),
  frozen_candidate: researchSynthesisCandidateSchema }).strict()
const reviewInputV2Schema = reviewInputSchema.extend({ schema_version: z.literal('decision-hub-frozen-review-input.v2'),
  frozen_candidate: researchDecisionSynthesisV1Schema }).strict()
function hash(value: string): string { return createHash('sha256').update(value, 'utf8').digest('hex') }
function sameBinding(value: CancellationBinding, expected: CancellationBinding): boolean { return canonical(value) === canonical(expected) }

// Read only the actual returned Tool payload. Do not treat a quoted wrapper in
// model prose, the child's research text, or arbitrary nested properties as it.
function returnedRows(event: SessionEventLike, callId: string): RecordValue[] {
  if (!['tool/result', 'tool/call-result'].includes(event.type)) return []
  const message = record(event.data.message)
  const items = Array.isArray(message?.content) ? message.content : []
  const typed = items.flatMap(item => { const row = record(item); return row && ['tool-result', 'tool_result'].includes(String(row.type)) ? [row] : [] })
  return (typed.length ? typed : [event.data]).filter(row => field(row, ['toolCallId', 'callId', 'id']) === callId)
}
// Official pruning appends a display replacement of the same durable message;
// it is not a second tool execution. Retain the original full result as proof.
function prunedResultProjection(events: readonly SessionEventLike[], call: SessionEventLike, event: SessionEventLike): boolean {
  const id = callIdentity(call)?.id, metadata = record(event), surface = record(metadata?.surfaceOp)
  if (!id || event.type !== 'tool/result' || !surface || surface.op !== 'replace'
    || !Number.isSafeInteger(surface.start) || surface.start !== surface.end
    || canonical(surface) !== canonical({ op: 'replace', start: surface.start, end: surface.start })
    || canonical(metadata?.sourceEventSeqs) !== canonical([surface.start])) return false
  const originals = events.filter(item => item.seq === surface.start && item.seq > call.seq && item.seq < event.seq)
  const prunes = events.filter(item => item.seq === event.seq - 1 && item.type === 'compaction/prune')
  if (originals.length !== 1 || prunes.length !== 1) return false
  const original = originals[0]!, prune = prunes[0]!, originalMeta = record(original)
  if (original.type !== 'tool/result' || originalMeta?.surfaceOp !== 'append'
    || canonical(originalMeta.sourceEventSeqs) !== canonical([call.seq])
    || canonical(prune.data.shadowedRange) !== canonical({ start: original.seq, end: original.seq })
    || canonical(prune.data.shadowedSeqs) !== canonical([original.seq])) return false
  const before = record(original.data.message), after = record(event.data.message)
  const source = record(before?.source)
  if (!before || !after || typeof before.id !== 'string' || !before.id
    || source?.kind !== 'tool' || source.callId !== id
    || !Number.isSafeInteger(original.data.turn) || !Number.isSafeInteger(original.data.step)) return false
  const { message: originalMessage, ...originalData } = original.data
  const { message: replacementMessage, ...replacementData } = event.data
  const { content: originalContent, ...originalIdentity } = before
  const { content: replacementContent, ...replacementIdentity } = after
  if (canonical(originalData) !== canonical(replacementData) || canonical(originalIdentity) !== canonical(replacementIdentity)
    || !Array.isArray(originalContent) || originalContent.length !== 1
    || !Array.isArray(replacementContent) || replacementContent.length !== 1) return false
  const originalRows = returnedRows(original, id), replacementRows = returnedRows(event, id)
  if (originalRows.length !== 1 || replacementRows.length !== 1
    || !successfulResult(original, id) || !successfulResult(event, id)) return false
  const { content: full, ...originalRow } = originalRows[0]!
  const { content: shortened, ...replacementRow } = replacementRows[0]!
  if (canonical(originalRow) !== canonical(replacementRow) || !Array.isArray(full) || !Array.isArray(shortened)
    || full.length !== shortened.length || full.length === 0) return false
  let shrunk = false
  for (let index = 0; index < full.length; index++) {
    const sourceBlock = record(full[index]), displayBlock = record(shortened[index])
    if (!sourceBlock || !displayBlock) return false
    if (sourceBlock.type !== 'text') { if (canonical(sourceBlock) !== canonical(displayBlock)) return false; continue }
    const { text: sourceText, ...sourceFields } = sourceBlock, { text: displayText, ...displayFields } = displayBlock
    if (canonical(sourceFields) !== canonical(displayFields) || typeof sourceText !== 'string' || typeof displayText !== 'string'
      || Array.from(displayText).length > Array.from(sourceText).length) return false
    shrunk ||= Array.from(displayText).length < Array.from(sourceText).length
  }
  return shrunk
}
function originalResultEvents(events: readonly SessionEventLike[], call: SessionEventLike): SessionEventLike[] {
  const id = callIdentity(call)?.id
  if (!id) return []
  const originals: SessionEventLike[] = []
  for (const event of events.filter(item => item.seq > call.seq && returnedRows(item, id).length > 0)) {
    const surface = record(event)?.surfaceOp
    if (surface === undefined || surface === 'append') originals.push(event)
    else if (!prunedResultProjection(events, call, event)) return []
  }
  return originals
}
function returnedPayloads(event: SessionEventLike, callId: string, decodePayload = decode): unknown[] {
  return returnedRows(event, callId).flatMap(row => {
    if (Array.isArray(row.content)) return row.content.flatMap(block => {
      const item = record(block)
      return item?.type === 'text' && typeof item.text === 'string' ? [decodePayload(item.text)] : []
    })
    return ['value', 'result', 'output'].flatMap(key => row[key] !== undefined ? [decodePayload(row[key])] : [])
  })
}
function returnedPerpetualReviewPayloads(event: SessionEventLike, callId: string): unknown[] {
  return returnedPayloads(event, callId, decodePerpetualReviewProof)
    .filter(value => record(value)?.schema_version === 'decision-hub-frozen-review-result.v4')
}
function isFailed(row: RecordValue): boolean { return row.error != null || row.isError === true || row.status === 'error' }
function requiredPair(events: readonly SessionEventLike[], call: SessionEventLike): boolean {
  const identity = callIdentity(call)
  if (!identity) return false
  const results = originalResultEvents(events, call)
  if (results.length !== 1) return false
  const result = results[0]!
  return returnedRows(result, identity.id).some(row => {
    if (!isFailed(row) && !isFailed(result.data)) return false
    const content = row.content
    if (Array.isArray(content) && content.length === 1) {
      const block = record(content[0])
      return block?.type === 'text' && (block.text === REQUIRED || block.text === `Error: ${REQUIRED}`)
    }
    return record(row.error)?.code === REQUIRED || row.error_code === REQUIRED
  })
}
function entries(candidate: SynthesisCandidate): { loc: string[]; evidence_ref: string }[] {
  const result: { loc: string[]; evidence_ref: string }[] = []
  const append = (refs: readonly string[], prefix: string[]) => refs.forEach((ref, index) => result.push({ loc: [...prefix, String(index)], evidence_ref: ref }))
  const causalCase = researchCandidate(candidate).causal_case
  if (causalCase) {
    append(causalCase.evidence_refs, ['causal_case', 'evidence_refs'])
    for (const name of ['main_chain', 'opposite_chain'] as const) causalCase[name].forEach((link, index) => append(link.evidence_refs, ['causal_case', name, String(index), 'evidence_refs']))
  }
  researchCandidate(candidate).horizons.forEach((horizon, index) => append(horizon.evidence_refs, ['horizons', String(index), 'evidence_refs']))
  if (isDecisionCandidate(candidate)) {
    candidate.action_intents.forEach((action, index) => append(action.evidence_refs, ['action_intents', String(index), 'evidence_refs']))
    const claimIds = new Set(candidate.claim_signal_refs)
    candidate.mechanism_reviews.forEach((review, index) => {
      const prefix = ['mechanism_reviews', String(index)]
      review.nodes.forEach((node, nodeIndex) => append(node.refs.filter(ref => !claimIds.has(ref)), [...prefix, 'nodes', String(nodeIndex), 'refs']))
      review.edges.forEach((edge, edgeIndex) => {
        append(edge.support_refs, [...prefix, 'edges', String(edgeIndex), 'support_refs'])
        append(edge.counter_refs, [...prefix, 'edges', String(edgeIndex), 'counter_refs'])
      })
      append(review.alternative_refs, [...prefix, 'alternative_refs'])
      append(review.priced_in_refs, [...prefix, 'priced_in_refs'])
    })
  }
  return result
}

/** Match the native accepted-only mapper boundary; the final Graph still checks originals. */
type NativeFact = ResearchCapabilityResult['facts'][number]
type TrustedSynthesisEvidence = {
  ids: readonly string[]; evidence: readonly EvidenceCandidate[]; facts: readonly NativeFact[];
  observedEvidence: readonly EvidenceCandidate[];
  pendingEvidenceDelivery: readonly { evidence_id: string; requirement_id: string; reason_code: 'native_delivery_pending' }[];
}
export function trustedSynthesisEvidence(context: SynthesisReviewContext, preparingFirstBaseline = false): TrustedSynthesisEvidence {
  return collectTrustedSynthesisEvidence(context, preparingFirstBaseline)
}
type NativeEvidenceSource = { execution_session_id: string; native_call_id: string }
function collectTrustedSynthesisEvidence(context: SynthesisReviewContext, preparingFirstBaseline: boolean,
  capture?: (result: ResearchCapabilityResult, source: NativeEvidenceSource) => void): TrustedSynthesisEvidence {
  if (!Array.isArray(context.admittedEvidence)) throw new Error('decision_hub_review_context_evidence_unavailable')
  const byId = new Map<string, EvidenceCandidate>(), fingerprints = new Map<string, string>()
  const factsById = new Map<string, NativeFact>()
  const allowedCapabilities = new Set(context.request.allowed_capabilities)
  const collect = (result: ResearchCapabilityResult) => {
    for (const fact of result.facts) {
      const previous = factsById.get(fact.fact_id)
      if (previous && canonical(previous) !== canonical(fact)) throw new Error('decision_hub_review_capability_fact_conflict')
      factsById.set(fact.fact_id, fact)
    }
    for (const item of result.evidence_candidates) {
      if (item.research_session_id !== context.binding.sessionId) continue
      const fingerprint = canonical(item), previous = fingerprints.get(item.evidence_id)
      if (previous !== undefined && previous !== fingerprint) throw new Error('decision_hub_review_capability_evidence_conflict')
      fingerprints.set(item.evidence_id, fingerprint)
      byId.set(item.evidence_id, item)
    }
  }
  const serverResults = baselineResults(context, preparingFirstBaseline)
  const serverEvidenceIds = new Set(serverResults.flatMap(result => result.evidence_candidates.map(item => item.evidence_id)))
  for (const result of serverResults) collect(result)
  const children = synthesisProtocol(context.request.output_schema_ref) === 'decision_v2'
    ? authenticatedChildResearchSources(context.childResearchSources) : []
  // Sequence numbers belong to one native session; never flatten child logs into the root.
  for (const source of [{ events: context.events, sessionId: context.binding.sessionId },
    ...children.map(child => ({ events: child.events, sessionId: child.prefix.sessionId }))]) {
  const events = [...source.events].sort((a, b) => a.seq - b.seq)
  const calls = events.filter(event => event.type === 'tool/call' && ['decision_hub_research', 'research_capability_execute'].some(name => callIdentity(event)?.name.endsWith(name)))
  for (const call of calls) {
    const identity = callIdentity(call)!
    if (calls.filter(item => callIdentity(item)?.id === identity.id).length !== 1) throw new Error('decision_hub_review_capability_pair_ambiguous')
    for (const event of originalResultEvents(events, call).filter(item => successfulResult(item, identity.id))) {
      for (const payload of returnedPayloads(event, identity.id)) {
        const parsed = researchCapabilityResultSchema.safeParse(payload)
        if (!parsed.success || !allowedCapabilities.has(parsed.data.capability_id)) continue
        const args = record(identity.arguments)
        const declaredRequest = args ? field(args, ['request_id', 'requestId']) : null
        const requestId = parsed.data.request_id, productRequest = context.request.request_id
        if (requestId !== productRequest && !requestId.startsWith(`${productRequest}:`) && requestId !== identity.id && requestId !== declaredRequest) continue
        collect(parsed.data)
        capture?.(parsed.data, { execution_session_id: source.sessionId, native_call_id: identity.id })
      }
    }
  }
  }
  const rows = new Map<string, EvidenceCandidate>()
  for (const item of context.admittedEvidence) {
    if (rows.has(item.evidence_id)) throw new Error('decision_hub_review_durable_evidence_conflict')
    rows.set(item.evidence_id, item)
  }
  const evidence = [...byId.values()].filter(item => {
    const durable = rows.get(item.evidence_id)
    return item.quality === 'accepted' && durable?.quality === 'accepted'
      && durable.research_session_id === context.binding.sessionId && durable.content_hash === item.content_hash
      && durable.requirement_id === item.requirement_id && durable.tool_call_id === item.tool_call_id
      && (!serverEvidenceIds.has(item.evidence_id) || canonical(durable) === canonical(item))
  }).sort((a, b) => a.evidence_id.localeCompare(b.evidence_id))
  const ids = Object.freeze([...new Set([...context.request.input_evidence.map(item => item.evidence_id), ...evidence.map(item => item.evidence_id)])].sort())
  const facts = [...factsById.values()].filter(item => item.quality === 'accepted' && evidence.some(source => source.evidence_id === item.evidence_id))
  const inputs = new Set(context.request.input_evidence.map(item => item.evidence_id))
  const pendingEvidenceDelivery = context.admittedEvidence.filter(item => item.quality === 'accepted'
    && item.research_session_id === context.binding.sessionId && !inputs.has(item.evidence_id) && !byId.has(item.evidence_id))
    .map(item => ({ evidence_id: item.evidence_id, requirement_id: item.requirement_id, reason_code: 'native_delivery_pending' as const }))
    .sort((a, b) => a.evidence_id.localeCompare(b.evidence_id))
  return { ids, evidence: Object.freeze(evidence), facts: Object.freeze(facts),
    observedEvidence: Object.freeze([...byId.values()]), pendingEvidenceDelivery: Object.freeze(pendingEvidenceDelivery) }
}
/** Same accepted collector, restricted to actual authenticated child results.
 * Provenance is captured at the real call/result, never inferred from a DTO ID. */
export function trustedChildSynthesisCoverage(context: SynthesisReviewContext): FinalizedChildSelection {
  if (authenticatedChildResearchSources(context.childResearchSources).length === 0) {
    return { evidence: [], facts: [], evidence_sources: [] }
  }
  const origins: { item: EvidenceCandidate; source: ChildEvidenceSource }[] = []
  const selected = collectTrustedSynthesisEvidence({ ...context, events: [], serverBaseline: undefined }, false,
    (result, native) => {
      for (const item of result.evidence_candidates) {
        if (item.quality !== 'accepted' || item.research_session_id !== context.binding.sessionId) continue
        origins.push({ item, source: { evidence_id: item.evidence_id, capability_id: result.capability_id, ...native } })
      }
    })
  const accepted = new Map(selected.evidence.map(item => [item.evidence_id, canonical(item)]))
  const sources = new Map<string, ChildEvidenceSource>()
  for (const origin of origins) {
    if (accepted.get(origin.item.evidence_id) === canonical(origin.item)) sources.set(canonical(origin.source), origin.source)
  }
  return { evidence: selected.evidence, facts: selected.facts, evidence_sources: [...sources.values()] }
}
export function synthesisEvidenceRefErrors(candidate: SynthesisCandidate, allowedIds: readonly string[]): readonly { loc: string[]; type: 'evidence_ref_unattested'; msg: string; evidence_ref: string }[] {
  const allowed = new Set(allowedIds)
  return entries(candidate).filter(item => !allowed.has(item.evidence_ref)).map(item => ({ ...item, type: 'evidence_ref_unattested', msg: `${item.evidence_ref} is not an allowed Evidence ID` }))
}
function assertRefs(candidate: SynthesisCandidate, allowedIds: readonly string[]): void {
  const errors = synthesisEvidenceRefErrors(candidate, allowedIds)
  if (errors.length) throw new Error(`decision_hub_synthesis_evidence_ref_unattested ${JSON.stringify({ error_code: 'decision_hub_synthesis_evidence_ref_unattested', errors, allowed_evidence_ids: allowedIds })}`)
}
function successfulFreeze(events: readonly SessionEventLike[], call: SessionEventLike, binding: CancellationBinding, candidate: SynthesisCandidate): boolean {
  const id = callIdentity(call)?.id
  if (!id) return false
  const results = originalResultEvents(events, call)
  if (results.length !== 1 || !successfulResult(results[0]!, id)) return false
  const wrappers = returnedPayloads(results[0]!, id).flatMap(payload => { const parsed = (isDecisionCandidate(candidate) ? synthesisResultV2Schema : synthesisResultSchema).safeParse(payload); return parsed.success ? [parsed.data] : [] })
  if (wrappers.length !== 1) return false
  const wrapper = wrappers[0]!, meta = wrapper.review_gate
  const candidateJson = JSON.stringify(candidate)
  return sameBinding(meta.binding, binding) && canonical(wrapper.candidate) === canonical(candidate)
    && meta.candidate_json === candidateJson && meta.candidate_sha256 === hash(candidateJson)
}
type Freeze = { call: SessionEventLike; candidate: SynthesisCandidate }
type HorizonTimeIssue = { readonly loc: readonly string[]; readonly code: string }
export type HorizonTimeAdmission = {
  readonly reference_at: string | null
  readonly reference_basis: 'native_tool_call' | 'pit_cutoff_at' | 'unavailable'
  readonly issues: readonly HorizonTimeIssue[]
}

// Preserve sub-millisecond offsets when comparing the frozen replay cutoff.
// Model timestamps must carry an explicit zone; daily candle dates are not clocks.
function horizonInstant(value: unknown): bigint | null {
  if (typeof value !== 'string') return null
  const parts = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (!parts) return null
  const milliseconds = Date.parse(`${parts[1]}${parts[3]}`)
  return Number.isFinite(milliseconds)
    ? BigInt(milliseconds) * 1_000_000n + BigInt((parts[2] ?? '').padEnd(9, '0'))
    : null
}

/** Early time admission only. Hub completeness, quality and financial gates still apply. */
export function horizonTimeAdmission(context: SynthesisReviewContext, candidate: SynthesisCandidate, call: SessionEventLike): HorizonTimeAdmission {
  let referenceAt: string | null = null
  let referenceBasis: HorizonTimeAdmission['reference_basis'] = 'unavailable'
  if (context.request.execution_mode === 'replay') {
    referenceAt = context.request.pit_cutoff_at
    referenceBasis = 'pit_cutoff_at'
  } else if (context.request.execution_mode === 'live' && Number.isSafeInteger(call.time)
    && Math.abs(call.time) <= 8_640_000_000_000_000) {
    referenceAt = new Date(call.time).toISOString()
    referenceBasis = 'native_tool_call'
  }
  const reference = horizonInstant(referenceAt), issues: HorizonTimeIssue[] = []
  if (reference === null) issues.push({ loc: ['reference_at'], code: 'horizon_reference_timezone_invalid' })
  researchCandidate(candidate).horizons.forEach((item, index) => {
    const review = horizonInstant(item.next_review_at), expiry = horizonInstant(item.expires_at)
    if (review === null) issues.push({ loc: ['horizons', String(index), 'next_review_at'], code: 'horizon_review_timezone_invalid' })
    if (expiry === null) issues.push({ loc: ['horizons', String(index), 'expires_at'], code: 'horizon_expiry_timezone_invalid' })
    if (reference !== null && review !== null && review <= reference) {
      issues.push({ loc: ['horizons', String(index), 'next_review_at'], code: 'horizon_review_not_after_reference' })
    }
    if (review !== null && expiry !== null && review > expiry) {
      issues.push({ loc: ['horizons', String(index), 'next_review_at'], code: 'horizon_review_after_expiry' })
    }
  })
  if (isDecisionCandidate(candidate) && reference !== null) {
    candidate.action_intents.forEach((item, index) => {
      const asof = horizonInstant(item.as_of), review = horizonInstant(item.next_review_at)
      if (asof !== null && asof > reference) issues.push({ loc: ['action_intents', String(index), 'as_of'], code: 'action_asof_after_reference' })
      if (review !== null && review <= reference) issues.push({ loc: ['action_intents', String(index), 'next_review_at'], code: 'action_review_not_after_reference' })
      for (const name of ['entry', 'exit', 'invalidation'] as const) item[name].forEach((condition, conditionIndex) => {
        const expiry = horizonInstant(condition.valid_until)
        if (expiry !== null && expiry <= reference) issues.push({ loc: ['action_intents', String(index), name, String(conditionIndex), 'valid_until'], code: 'action_condition_expired' })
      })
    })
  }
  return { reference_at: referenceAt, reference_basis: referenceBasis, issues }
}

function horizonTimeError(admission: HorizonTimeAdmission): string {
  return `decision_hub_horizon_time_invalid:${JSON.stringify(admission)}; Correct the reported time fields and resubmit before the single reviewer within the original remaining tool/deadline budgets. A daily data timestamp or future execution deadline is not the live reference. If the reviewer already ran, preserve the bounded result; do not start another reviewer.`
}

function assertHorizonTimes(context: SynthesisReviewContext, candidate: SynthesisCandidate, call: SessionEventLike): void {
  const admission = horizonTimeAdmission(context, candidate, call)
  if (admission.issues.length) throw new Error(horizonTimeError(admission))
}

/** New decision protocol separates immutable admission from current validity.
 * A due review is not an expired action. The caller still needs an exact native
 * freeze/reviewer proof before it can reuse the historical admission clock. */
function decisionCurrentValidity(context: SynthesisReviewContext, candidate: SynthesisCandidate, call: SessionEventLike): HorizonTimeAdmission {
  const reference = horizonTimeAdmission(context, candidate, call)
  const at = horizonInstant(reference.reference_at), issues: HorizonTimeIssue[] = []
  if (at === null) issues.push({ loc: ['reference_at'], code: 'horizon_reference_timezone_invalid' })
  else if (isDecisionCandidate(candidate)) {
    candidate.research_candidate_v1.horizons.forEach((item, index) => {
      const expiry = horizonInstant(item.expires_at)
      if (expiry === null || expiry <= at) issues.push({ loc: ['horizons', String(index), 'expires_at'], code: expiry === null ? 'horizon_expiry_timezone_invalid' : 'horizon_expired' })
    })
    candidate.action_intents.forEach((item, index) => {
      const asof = horizonInstant(item.as_of), expiry = horizonInstant(item.valid_until)
      if (asof === null || asof > at) issues.push({ loc: ['action_intents', String(index), 'as_of'], code: asof === null ? 'action_asof_timezone_invalid' : 'action_asof_after_reference' })
      if (expiry === null || expiry <= at) issues.push({ loc: ['action_intents', String(index), 'valid_until'], code: expiry === null ? 'action_expiry_timezone_invalid' : 'action_expired' })
      for (const name of ['entry', 'exit', 'invalidation'] as const) item[name].forEach((condition, conditionIndex) => {
        const until = horizonInstant(condition.valid_until)
        if (until === null || until <= at) issues.push({ loc: ['action_intents', String(index), name, String(conditionIndex), 'valid_until'], code: until === null ? 'action_condition_expiry_timezone_invalid' : 'action_condition_expired' })
      })
    })
  }
  return { reference_at: reference.reference_at, reference_basis: reference.reference_basis, issues }
}

function assertDecisionCurrentValidity(context: SynthesisReviewContext, candidate: SynthesisCandidate, call: SessionEventLike): void {
  const admission = decisionCurrentValidity(context, candidate, call)
  if (admission.issues.length) throw new Error(horizonTimeError(admission))
}

export function qualifiedFrozenCandidate(context: SynthesisReviewContext, window: readonly SessionEventLike[], allowedIds: readonly string[], binding = context.binding, admissionCall?: SessionEventLike): Freeze | null {
  for (const call of window) {
    if (call.type !== 'tool/call' || callIdentity(call)?.name !== 'decision_hub_synthesis_submit') continue
    const candidate = normalized(context, call, normalizeSynthesisCandidate)
    if (!candidate || researchCandidate(candidate).causal_case === null || researchCandidate(candidate).horizons.length !== 3
      || researchCandidate(candidate).horizons.some((item, index) => item.horizon !== ['30m', '24h', '72h'][index])
      || candidate.request_id !== context.request.request_id || synthesisEvidenceRefErrors(candidate, allowedIds).length
      || horizonTimeAdmission(context, candidate, isDecisionCandidate(candidate) ? call : admissionCall ?? call).issues.length
      || isDecisionCandidate(candidate) && admissionCall && decisionCurrentValidity(context, candidate, admissionCall).issues.length) continue
    if (requiredPair(window, call) || successfulFreeze(window, call, binding, candidate)) return { call, candidate }
  }
  return null
}
function authorizedWindow(context: SynthesisReviewContext, currentCallId: string, name: string): { events: SessionEventLike[]; call: SessionEventLike; prompts: SessionEventLike[]; latest: SessionEventLike; window: SessionEventLike[] } {
  const binding = context.binding, ids = deterministicIds(binding.runId, binding.requestHash, binding.generation)
  if (ids.sessionId !== binding.sessionId || ids.requestId !== binding.requestId || context.request.run_id !== binding.runId || context.request.request_id !== `research-request:${binding.runId}`) throw new Error('decision_hub_synthesis_binding_mismatch')
  const events = [...context.events].sort((a, b) => a.seq - b.seq)
  const current = events.filter(event => event.type === 'tool/call' && callIdentity(event)?.id === currentCallId && callIdentity(event)?.name === name)
  if (current.length !== 1) throw new Error('decision_hub_synthesis_call_unavailable')
  const call = current[0]!, prompts = events.filter(event => event.seq < call.seq && rpc(event) !== null), latest = prompts.at(-1)
  if (!latest || rpc(latest) !== binding.requestId) throw new Error('decision_hub_synthesis_prompt_mismatch')
  return { events, call, prompts, latest, window: events.filter(event => event.seq > latest.seq && event.seq < call.seq) }
}
function reviewerMatchedBody(events: readonly SessionEventLike[], call: SessionEventLike, freeze: Freeze, candidate: SynthesisCandidate, binding: CancellationBinding, allowed: ReadonlySet<string>) {
  const identity = callIdentity(call)
  if (!identity || call.seq <= freeze.call.seq) return null
  const results = originalResultEvents(events, call)
  if (results.length !== 1 || !successfulResult(results[0]!, identity.id)) return null
  const wrappers = returnedPayloads(results[0]!, identity.id).flatMap(payload => { const parsed = frozenReviewResultSchema.safeParse(payload); return parsed.success ? [parsed.data] : [] })
  if (wrappers.length !== 1) return null
  const wrapper = wrappers[0]!, candidateJson = JSON.stringify(candidate)
  if (isDecisionCandidate(candidate) !== (wrapper.schema_version === 'decision-hub-frozen-review-result.v3')) return null
  if (!sameBinding(wrapper.binding, binding) || wrapper.freeze_call_id !== callIdentity(freeze.call)?.id || wrapper.freeze_seq !== freeze.call.seq
    || canonical(freeze.candidate) !== canonical(candidate) || wrapper.candidate_json !== candidateJson || wrapper.candidate_sha256 !== hash(candidateJson)
    || wrapper.expected_target_claim !== researchCandidate(candidate).causal_case?.thesis || wrapper.actual_prompt_sha256 !== hash(wrapper.actual_prompt)) return null
  if (!validFrozenReviewRepair(wrapper, allowed)) return null
  const lines = wrapper.actual_prompt.split('\n')
  const starts = lines.flatMap((line, index) => line === 'BEGIN_FROZEN_REVIEW_CONTROL_JSON' ? [index] : [])
  const finishes = lines.flatMap((line, index) => line === 'END_FROZEN_REVIEW_CONTROL_JSON' ? [index] : [])
  if (starts.length !== 1 || finishes.length !== 1 || finishes[0] !== starts[0]! + 2) return null
  const inputText = lines[starts[0]! + 1]!
  const input = (isDecisionCandidate(candidate) ? reviewInputV2Schema : reviewInputSchema).safeParse(decode(inputText))
  if (!input.success || input.data.candidate_sha256 !== hash(candidateJson) || input.data.expected_target_claim !== researchCandidate(candidate).causal_case?.thesis || canonical(input.data.frozen_candidate) !== canonical(candidate)) return null
  const output = foregroundSchema.safeParse(wrapper.original_output)
  if (!output.success || output.data.runId !== wrapper.child_session_id) return null
  const valid = records(output.data.output).flatMap(row => {
    if (!(isDecisionCandidate(candidate) ? ['independent-review-receipt.v2'] : ['independent-review-receipt.v1', 'independent-review-receipt.v2']).includes(String(row.schema_version))) return []
    const parsed = receiptSchema.safeParse(row)
    return parsed.success && reviewReceiptHasValidReferences(parsed.data, allowed) ? [parsed.data] : []
  })
  if (valid.length !== 1) return null
  const receipt = valid[0]!
  return receipt.target_claim === researchCandidate(candidate).causal_case?.thesis && receipt.evidence_refs.every(ref => allowed.has(ref))
    ? { receipt, wrapper, result_seq: results[0]!.seq } : null
}
function reviewerMatched(events: readonly SessionEventLike[], call: SessionEventLike, freeze: Freeze, candidate: SynthesisCandidate, binding: CancellationBinding, allowed: ReadonlySet<string>): Receipt | null {
  return reviewerMatchedBody(events, call, freeze, candidate, binding, allowed)?.receipt ?? null
}

export interface ProtectedRootReviewBody {
  readonly schema_version: 'decision-hub-protected-review-body.v1'
  readonly run_id: string
  readonly generation: number
  readonly root_session_id: string
  readonly request_hash: string
  readonly review_call_id: string
  readonly review_call_seq: number
  readonly review_result_seq: number
  readonly frozen_candidate_sha256: string
  readonly wrapper_sha256: string
  readonly receipt_sha256: string
  readonly receipt: z.infer<typeof independentReviewReceiptV2Schema>
}

/** Protection is rebuilt from authenticated current context, never read-latest. */
export function protectedRootReviewBodies(context: SynthesisReviewContext): readonly ProtectedRootReviewBody[] {
  const events = [...context.events].sort((a, b) => a.seq - b.seq)
  const ids = deterministicIds(context.binding.runId, context.binding.requestHash, context.binding.generation)
  if (ids.sessionId !== context.binding.sessionId || ids.requestId !== context.binding.requestId
    || context.request.run_id !== context.binding.runId) throw new Error('decision_hub_protected_review_binding_invalid')
  const latest = events.filter(event => rpc(event) !== null).at(-1)
  if (!latest || rpc(latest) !== context.binding.requestId) throw new Error('decision_hub_protected_review_prompt_invalid')
  const window = events.filter(event => event.seq > latest.seq)
  const trusted = trustedSynthesisEvidence(context), allowed = new Set(trusted.ids)
  const freeze = qualifiedFrozenCandidate(context, window, trusted.ids)
  if (!freeze) return []
  const matches = window.filter(call => call.type === 'tool/call' && callIdentity(call)?.name === 'subagent_reviewer')
    .flatMap(call => {
      const matched = reviewerMatchedBody(events, call, freeze, freeze.candidate, context.binding, allowed)
      if (!matched || matched.receipt.schema_version !== 'independent-review-receipt.v2') return []
      return [{ schema_version: 'decision-hub-protected-review-body.v1' as const, run_id: context.binding.runId,
        generation: context.binding.generation, root_session_id: context.binding.sessionId, request_hash: context.binding.requestHash,
        review_call_id: callIdentity(call)!.id, review_call_seq: call.seq, review_result_seq: matched.result_seq,
        frozen_candidate_sha256: matched.wrapper.candidate_sha256, wrapper_sha256: hash(canonical(matched.wrapper)),
        receipt_sha256: hash(canonical(matched.receipt)), receipt: matched.receipt }]
    })
  if (matches.length > 1) throw new Error('decision_hub_protected_review_ambiguous')
  return matches
}
/** Delivery state only; never a substitute for a native synthesis submission. */
export function protectedRootSynthesisContinuation(context: SynthesisReviewContext, nowMs: number) {
  if (synthesisProtocol(context.request.output_schema_ref) !== 'decision') return null
  const reviews = protectedRootReviewBodies(context)
  if (reviews.length === 0) return null
  const review = reviews[0]!, events = [...context.events].sort((a, b) => a.seq - b.seq)
  const latest = events.filter(event => rpc(event) !== null).at(-1)!
  const window = events.filter(event => event.seq > latest.seq)
  const allowed = trustedSynthesisEvidence(context).ids
  const freeze = qualifiedFrozenCandidate(context, window, allowed)
  if (!freeze || !isDecisionCandidate(freeze.candidate)) throw new Error('decision_hub_protected_synthesis_freeze_missing')
  const candidateJson = JSON.stringify(freeze.candidate)
  if (hash(candidateJson) !== review.frozen_candidate_sha256) throw new Error('decision_hub_protected_synthesis_candidate_mismatch')
  // An actually recorded successful matched submission ends the obligation.
  // Merely mentioning acceptance in prose, or changing the Action, does not.
  for (const call of window) {
    if (call.seq <= review.review_result_seq || call.type !== 'tool/call'
      || callIdentity(call)?.name !== 'decision_hub_synthesis_submit') continue
    const value = normalized(context, call, normalizeSynthesisCandidate)
    if (!value || JSON.stringify(value) !== candidateJson || !successfulFreeze(events, call, context.binding, value)) continue
    const gate = evaluateFinalReviewGate(context, value, callIdentity(call)!.id, normalizeSynthesisCandidate)
    if (gate.status === 'matched' && gate.review_call_id === review.review_call_id
      && gate.review_receipt_hash === review.receipt_sha256) return null
  }
  if (!Number.isSafeInteger(nowMs)) throw new Error('decision_hub_protected_synthesis_clock_invalid')
  // Due next_review_at alone is deliberately not expiry. Exact reviewed bytes
  // remain unchanged; the normal submission gate repeats current validity.
  const validity = decisionCurrentValidity(context, freeze.candidate,
    { type: 'tool/call', seq: events.at(-1)!.seq + 1, time: nowMs, data: {} })
  return {
    schema_version: 'decision-hub-synthesis-completion.v1',
    state: validity.issues.length ? 'reviewed_candidate_expired' : 'resubmit_required',
    run_id: context.binding.runId, generation: context.binding.generation,
    root_session_id: context.binding.sessionId,
    freeze_call_id: callIdentity(freeze.call)!.id, freeze_seq: freeze.call.seq,
    review_call_id: review.review_call_id, review_result_seq: review.review_result_seq,
    candidate_json: candidateJson, candidate_sha256: review.frozen_candidate_sha256,
    review_receipt_sha256: review.receipt_sha256,
    next_tool: validity.issues.length ? null : 'decision_hub_synthesis_submit',
    validity_issues: validity.issues,
  }
}

export interface QualifiedReviewerContext {
  readonly context: SynthesisReviewContext
  readonly candidate: SynthesisCandidate
  readonly candidate_json: string
  readonly candidate_sha256: string
  readonly freeze_call_id: string
  readonly freeze_seq: number
  readonly trusted_evidence_ids: readonly string[]
  readonly evidence_digest: string
}
function evidenceDigest(context: SynthesisReviewContext, trusted: ReturnType<typeof trustedSynthesisEvidence>): string {
  const inputs = context.request.input_evidence.map(item => ({ origin: 'frozen_input', evidence: item }))
  const acquired = trusted.evidence.map(item => ({ origin: 'accepted_acquisition', evidence: item }))
  const facts = trusted.facts
  const keptEvidence: (typeof inputs[number] | typeof acquired[number])[] = [], keptFacts: NativeFact[] = []
  const base = { evidence: keptEvidence, facts: keptFacts, omitted_evidence_count: inputs.length + acquired.length, omitted_fact_count: facts.length }
  for (const item of [...inputs, ...acquired]) {
    keptEvidence.push(item); base.omitted_evidence_count -= 1
    if (Buffer.byteLength(JSON.stringify(base), 'utf8') > 256_000) { keptEvidence.pop(); base.omitted_evidence_count += 1 }
  }
  for (const item of facts) {
    keptFacts.push(item); base.omitted_fact_count -= 1
    if (Buffer.byteLength(JSON.stringify(base), 'utf8') > 256_000) { keptFacts.pop(); base.omitted_fact_count += 1 }
  }
  return JSON.stringify(base)
}
// An early call rejected before a qualified freeze cannot have started a child.
// Prove that from the exact original native failure and its authorized prefix;
// an arbitrary error string, missing result, or a post-freeze failure never exempts an attempt.
function reviewerAttemptSpent(context: SynthesisReviewContext, call: SessionEventLike): boolean {
  if (context.binding.generation !== 1) return true
  const id = callIdentity(call)?.id
  if (!id) return true
  try {
    const view = authorizedWindow(context, id, 'subagent_reviewer')
    const results = originalResultEvents(view.events, call)
    if (results.length !== 1) return true
    const result = results[0]!, meta = record(result), message = record(result.data.message), source = record(message?.source)
    const rows = returnedRows(result, id)
    if (result.type !== 'tool/result' || meta?.surfaceOp !== 'append'
      || canonical(meta.sourceEventSeqs) !== canonical([call.seq])
      || typeof message?.id !== 'string' || !message.id || source?.kind !== 'tool' || source.callId !== id
      || rows.length !== 1 || rows[0]!.type !== 'tool-result' || rows[0]!.isError !== true
      || result.data.error != null || rows[0]!.error != null || rows[0]!.status === 'error') return true
    const content = rows[0]!.content
    if (!Array.isArray(content) || content.length !== 1) return true
    const block = record(content[0])
    if (!block) return true
    const prefix = { ...context, events: view.events.filter(event => event.seq < call.seq) }
    const trusted = trustedSynthesisEvidence(prefix)
    const freeze = qualifiedFrozenCandidate(prefix, view.window, trusted.ids)
    if (canonical(block) === canonical({ type: 'text', text: 'Error: decision_hub_reviewer_qualified_freeze_required' })) return freeze !== null
    // Only an exact official pre-launch time rejection exempts this attempt.
    // Missing/ambiguous results and arbitrary error strings still spend it.
    if (freeze !== null) {
      const admission = isDecisionCandidate(freeze.candidate)
        ? decisionCurrentValidity(prefix, freeze.candidate, call) : horizonTimeAdmission(prefix, freeze.candidate, call)
      if (admission.issues.length && canonical(block) === canonical({ type: 'text', text: `Error: ${horizonTimeError(admission)}` })) return false
    }
    return true
  } catch { return true }
}
export async function readQualifiedReviewerContext(root: object, agent: ModelAgentLineage, currentCallId: string, signal: AbortSignal): Promise<QualifiedReviewerContext | QualifiedPerpetualReviewerContext> {
  const reader = readers.get(root)?.current
  if (!reader) throw new Error('decision_hub_reviewer_reader_unavailable')
  const context = await reader(agent, signal, currentCallId)
  signal.throwIfAborted()
  if (synthesisProtocol(context.request.output_schema_ref) === 'decision_v2') return qualifiedPerpetualReviewer(context, currentCallId)
  const view = authorizedWindow(context, currentCallId, 'subagent_reviewer')
  if (context.binding.generation !== 1) throw new Error('decision_hub_reviewer_budget_used')
  // The current real reviewer call is included in the immutable log. Count
  // every started or uncertain attempt. Only a proved pre-launch rejection is recoverable.
  const attempts = view.events.filter(event => event.seq < view.call.seq && event.type === 'tool/call')
  if (attempts.some(event => callIdentity(event)?.name === 'subagent_reviewer' && reviewerAttemptSpent(context, event))) throw new Error('decision_hub_reviewer_budget_used')
  if (attempts.filter(event => ['subagent', 'dsh.internal.subagent'].includes(callIdentity(event)?.name ?? '')).length >= context.request.execution_budget.max_subagents) throw new Error('decision_hub_reviewer_subagent_budget_exhausted')
  const trusted = trustedSynthesisEvidence(context)
  const freeze = qualifiedFrozenCandidate(context, view.window, trusted.ids, context.binding, view.call)
  if (!freeze) {
    const previous = qualifiedFrozenCandidate(context, view.window, trusted.ids)
    if (previous) {
      if (isDecisionCandidate(previous.candidate)) assertDecisionCurrentValidity(context, previous.candidate, view.call)
      else assertHorizonTimes(context, previous.candidate, view.call)
    }
    throw new Error('decision_hub_reviewer_qualified_freeze_required')
  }
  if (isDecisionCandidate(freeze.candidate)) {
    assertHorizonTimes(context, freeze.candidate, freeze.call)
    assertDecisionCurrentValidity(context, freeze.candidate, view.call)
  } else assertHorizonTimes(context, freeze.candidate, view.call)
  const candidateJson = JSON.stringify(freeze.candidate)
  return { context, candidate: freeze.candidate, candidate_json: candidateJson, candidate_sha256: hash(candidateJson),
    freeze_call_id: callIdentity(freeze.call)!.id, freeze_seq: freeze.call.seq, trusted_evidence_ids: trusted.ids, evidence_digest: evidenceDigest(context, trusted) }
}

export function evaluateFinalReviewGate(context: SynthesisReviewContext, candidate: SynthesisCandidate, currentCallId: string, normalize: (value: unknown) => unknown): FinalReviewGateMetadata {
  const protocol = synthesisProtocol(context.request.output_schema_ref)
  if ((protocol === 'decision') !== isDecisionCandidate(candidate)) throw new Error('decision_hub_synthesis_protocol_mismatch')
  const { events, call, prompts, latest, window } = authorizedWindow(context, currentCallId, 'decision_hub_synthesis_submit')
  if (context.request.request_id !== candidate.request_id) throw new Error('decision_hub_synthesis_binding_mismatch')
  const sourceCandidate = normalized(context, call, normalize)
  if (!sourceCandidate || canonical(sourceCandidate) !== canonical(candidate)) throw new Error('decision_hub_synthesis_call_candidate_mismatch')
  if (isDecisionCandidate(candidate) && candidate.mechanism_reviews.some(item => item.run_id !== context.request.run_id)) throw new Error('decision_hub_synthesis_mechanism_run_mismatch')
  const trusted = trustedSynthesisEvidence(context)
  assertRefs(candidate, trusted.ids)
  if (isDecisionCandidate(candidate)) assertDecisionCurrentValidity(context, candidate, call)
  else if (researchCandidate(candidate).horizons.length) assertHorizonTimes(context, candidate, call)
  const binding = context.binding, allowed = new Set(trusted.ids)
  if (researchCandidate(candidate).causal_case === null) {
    if (isDecisionCandidate(candidate)) assertHorizonTimes(context, candidate, call)
    const candidateJson = JSON.stringify(candidate)
    return { schema_version: protocol === 'decision' ? 'decision-hub-final-review-gate.v2' : 'decision-hub-final-review-gate.v1', status: 'unreviewed', reason: 'no_causal_candidate',
      binding: Object.freeze({ ...binding }), candidate_json: candidateJson, candidate_sha256: hash(candidateJson),
      review_receipt_hash: null, review_call_id: null }
  }
  if (researchCandidate(candidate).horizons.length !== 3 || researchCandidate(candidate).horizons.some((item, index) => item.horizon !== ['30m', '24h', '72h'][index])) {
    throw new Error('decision_hub_review_complete_candidate_required')
  }
  const freeze = qualifiedFrozenCandidate(context, window, trusted.ids, binding, call)
  const frozenChanged = freeze !== null && canonical(freeze.candidate) !== canonical(candidate)
  const matched = (scope: readonly SessionEventLike[], frozen: Freeze | null, scopeBinding: CancellationBinding) => frozen === null ? [] : scope
    .filter(event => event.type === 'tool/call' && callIdentity(event)?.name === 'subagent_reviewer')
    .flatMap(event => { const receipt = reviewerMatched(scope, event, frozen, candidate, scopeBinding, allowed); return receipt ? [{ event, receipt }] : [] })
  let matching = matched(window, freeze, binding), continuationChanged = false, matchedFreeze = freeze
  if (binding.generation === 2 && matching.length === 0) {
    const previousBinding = { ...binding, generation: 1, ...deterministicIds(binding.runId, binding.requestHash, 1) }
    const previousPrompt = prompts.at(-2)
    if (previousPrompt && rpc(previousPrompt) === previousBinding.requestId) {
      const previous = events.filter(event => event.seq > previousPrompt.seq && event.seq < latest.seq)
      const previousFreeze = qualifiedFrozenCandidate(context, previous, trusted.ids, previousBinding, call)
      const original = previous.filter(event => {
        if (event.type !== 'tool/call' || callIdentity(event)?.name !== 'decision_hub_synthesis_submit') return false
        const value = normalized(context, event, normalize)
        return value !== null && successfulFreeze(previous, event, previousBinding, value)
      }).at(-1)
      const originalCandidate = original ? normalized(context, original, normalize) : null
      if (previousFreeze && original && originalCandidate && successfulFreeze(previous, original, previousBinding, originalCandidate)
        && canonical(previousFreeze.candidate) === canonical(originalCandidate) && canonical(originalCandidate) === canonical(candidate)) {
        matching = matched(previous, previousFreeze, previousBinding)
        matchedFreeze = previousFreeze
      }
      else continuationChanged = true
    } else continuationChanged = true
  }
  const chosen = !frozenChanged && !continuationChanged ? matching.at(-1) : undefined
  // Only the exact authenticated reviewer binding can carry admission forward.
  // An unreviewed or altered candidate must qualify at its own native call.
  if (isDecisionCandidate(candidate)) assertHorizonTimes(context, candidate, chosen && matchedFreeze ? matchedFreeze.call : call)
  const candidateJson = JSON.stringify(candidate)
  const metadata = (reason: FinalReviewGateMetadata['reason']): FinalReviewGateMetadata => ({
    schema_version: protocol === 'decision' ? 'decision-hub-final-review-gate.v2' : 'decision-hub-final-review-gate.v1', status: chosen ? 'matched' : 'unreviewed', reason,
    binding: Object.freeze({ ...binding }), candidate_json: candidateJson, candidate_sha256: hash(candidateJson),
    review_receipt_hash: chosen ? hash(canonical(chosen.receipt)) : null, review_call_id: chosen ? callIdentity(chosen.event)!.id : null,
  })
  if (frozenChanged) return metadata('frozen_candidate_changed')
  if (continuationChanged) return metadata('continuation_candidate_changed')
  if (chosen) return metadata('exact_target')
  if (events.some(event => event.seq < call.seq && event.type === 'tool/call' && callIdentity(event)?.name === 'subagent_reviewer' && reviewerAttemptSpent(context, event)) || binding.generation !== 1) return metadata('review_attempt_used')
  const subagents = events.filter(event => event.seq < call.seq && event.type === 'tool/call'
    && (['subagent', 'dsh.internal.subagent'].includes(callIdentity(event)?.name ?? '')
      || (callIdentity(event)?.name === 'subagent_reviewer' && reviewerAttemptSpent(context, event)))).length
  if (subagents >= context.request.execution_budget.max_subagents) return metadata('subagent_budget_exhausted')
  if (freeze) return metadata('review_instruction_exhausted')
  throw new Error(REQUIRED)
}

type PerpetualFreeze = { call: SessionEventLike; candidate: PerpetualSynthesis; packet: PerpetualReviewPacket; preparation?:PerpetualPreparationWitness }
/** Select only an official original call; the server authenticates its Provider receipt. */
export function perpetualSnapshotSourceCallId(events: readonly SessionEventLike[], currentCallId?: string): string | undefined {
  if (currentCallId === undefined) return undefined
  const current = events.filter(event => event.type === 'tool/call' && callIdentity(event)?.id === currentCallId)
  if (current.length !== 1) throw new Error('decision_hub_perpetual_snapshot_call_missing')
  const call = current[0]!, identity = callIdentity(call)!, args = record(identity.arguments)
  if (identity.name === 'decision_hub_synthesis_submit' && args?.action === 'prepare') return currentCallId
  const fullPacket = identity.name === 'decision_hub_synthesis_submit' && args
    && canonical(Object.keys(args).sort()) === canonical(['candidate', 'review_request'])
  const sources = events.filter(event => event.seq < call.seq && event.type === 'tool/call'
    && callIdentity(event)?.name === 'decision_hub_synthesis_submit').filter(event => {
      const source = callIdentity(event)!, input = record(source.arguments)
      if (input?.action === 'prepare') {
        const results = originalResultEvents(events, event)
        return results.length === 1 && successfulResult(results[0]!, source.id)
          && returnedPayloads(results[0]!, source.id).some(value => {
            const parsed = perpetualPreparationResultSchema.safeParse(value)
            return parsed.success && parsed.data.preparation.prepared_call_id === source.id
              && parsed.data.preparation.prepared_seq === event.seq
          })
      }
      return input && canonical(Object.keys(input).sort()) === canonical(['candidate', 'review_request'])
        && requiredPair(events, event)
    })
  if (fullPacket && sources.length === 0) return currentCallId
  const source = sources.at(-1)
  if (!source || !(identity.name === 'subagent_reviewer'
    || fullPacket && canonical(args) === canonical(callIdentity(source)!.arguments)
    || identity.name === 'decision_hub_synthesis_submit' && args?.action === 'finalize'
      && args.prepared_call_id === callIdentity(source)!.id)) throw new Error('decision_hub_perpetual_snapshot_prepare_required')
  return callIdentity(source)!.id
}
export interface QualifiedPerpetualReviewerContext {
  readonly protocol: 'decision_v2'
  readonly preparation?: PerpetualPreparationWitness
  readonly context: SynthesisReviewContext
  readonly candidate: PerpetualSynthesis
  readonly review_request: PerpetualReviewPacket
  readonly candidate_json: string
  readonly candidate_sha256: string
  readonly freeze_call_id: string
  readonly freeze_seq: number
  readonly reviewer_call_seq: number
  readonly reviewer_started_at: string
  readonly trusted_evidence_ids: readonly string[]
  readonly evidence_digest: string
  readonly previous_review?: PreviousPerpetualReview
}
function perpetualAdmission(context: SynthesisReviewContext, candidate: PerpetualSynthesis, packet: PerpetualReviewPacket, call: SessionEventLike): void {
  const primary = primaryPerpetualSynthesis(candidate), primaryPacket = primaryPerpetualReviewPacket(packet)
  if (synthesisProtocol(context.request.output_schema_ref) !== 'decision_v2' || candidate.request_id !== context.request.request_id) throw new Error('decision_hub_perpetual_protocol_request_mismatch')
  const frozen = context.perpetualBinding
  if (!frozen) throw new Error('decision_hub_perpetual_admission_missing')
  if (!['okx:BTC-USDT-SWAP','okx:ETH-USDT-SWAP'].includes(primary.candidate_set.context.instrument_id) || !['4h','24h','72h'].includes(primary.candidate_set.context.horizon)) throw new Error('decision_hub_perpetual_first_slice_scope_invalid')
  const actual = { opportunity_id: primary.opportunity_id, context: primary.candidate_set.context,
    candidate_target_ref: primary.candidate_set.candidate_target_ref, anchor_rule_ref: primary.candidate_set.anchor_rule_ref,
    method_ref: primary.method_ref, method_code_hash: primary.method_code_hash }
  if (canonical(actual) !== canonical(frozen)) throw new Error('decision_hub_perpetual_admission_binding_mismatch')
  if (!context.perpetualReviewOriginals) throw new Error('decision_hub_perpetual_review_originals_missing')
  const originals = validatePerpetualReviewOriginals(candidate, context.perpetualReviewOriginals)
  if (packet.schema_version === 'action-review-request.v3') parsePerpetualReviewPacket(packet, candidate, originals)
  if (originals.run_id !== context.binding.runId || originals.request_hash !== context.binding.requestHash || Date.parse(originals.as_of) > call.time!) throw new Error('decision_hub_perpetual_review_original_scope_mismatch')
  const at = context.request.execution_mode === 'replay' ? Date.parse(context.request.pit_cutoff_at ?? '') : call.time
  if (!Number.isSafeInteger(at) || Date.parse(candidate.as_of) > at! || Date.parse(packet.requested_at) > at!
    || at! >= Date.parse(primary.candidate_set.valid_until) || at! >= Date.parse(primaryPacket.allowed_domain.valid_until)
    || packet.schema_version === 'action-review-request.v3' && at! >= Date.parse(packet.event_plan_domain.freeze_before)) throw new Error('decision_hub_perpetual_candidate_expired')
}
function preparedPerpetualCall(context:SynthesisReviewContext,args:unknown,call:SessionEventLike):PerpetualFreeze {
      // Reconstruct an older attempt from its actual frozen child catalog;
      // the history validator below authenticates that child before transition.
      const id = callIdentity(call)!.id
      for (const reviewCall of context.events.filter(event => event.type === 'tool/call' && event.seq > call.seq && callIdentity(event)?.name === 'subagent_reviewer')) {
        const results = originalResultEvents(context.events,reviewCall)
        if(results.length!==1 || !successfulResult(results[0]!,callIdentity(reviewCall)!.id)) continue
        const wrappers = returnedPerpetualReviewPayloads(results[0]!,callIdentity(reviewCall)!.id).map(value=>perpetualFrozenReviewResultSchema.safeParse(value)).filter(row=>row.success && row.data.freeze_call_id===id)
        if(wrappers.length===1) { context={...context,perpetualReviewOriginals:reviewOriginalsFromPrompt(wrappers[0]!.data!.actual_prompt)}; break }
      }
      const input=perpetualPrepareArgumentsSchema.parse(args)
      if (input.request_id!==context.request.request_id || !context.perpetualBinding || !context.perpetualReviewOriginals || !Number.isSafeInteger(call.time)) throw new Error('decision_hub_perpetual_prepare_identity_mismatch')
      const requested_at=new Date(call.time!).toISOString()
      const normalized=preparePerpetualSynthesis(input.draft,{request_id:input.request_id,binding:context.perpetualBinding,review_originals:context.perpetualReviewOriginals,requested_at})
      perpetualAdmission(context,normalized.candidate,normalized.review_request,call)
      const witness={prepared_call_id:callIdentity(call)!.id,prepared_seq:call.seq,requested_at,
        source_draft_hash:normalized.source_draft_hash,normalized_synthesis_hash:normalized.normalized_synthesis_hash,normalized_review_request_hash:normalized.normalized_review_request_hash,assumptions:normalized.assumptions}
      const preparation = 'event_plan_hash' in normalized
        ? perpetualPreparationWitnessV2Schema.parse({schema_version:'perpetual-preparation-witness.v2',...witness,
          primary_v2_hash:normalized.primary_v2_hash,primary_review_request_hash:normalized.primary_review_request_hash,
          event_plan_draft_hash:normalized.event_plan_draft_hash,event_plan_hash:normalized.event_plan_hash})
        : perpetualPreparationWitnessV1Schema.parse({schema_version:'perpetual-preparation-witness.v1',...witness})
      return {call,candidate:normalized.candidate,packet:normalized.review_request,preparation}
}
function perpetualCall(context: SynthesisReviewContext, call: SessionEventLike): PerpetualFreeze | null {
  try {
    const args = record(callIdentity(call)?.arguments)
    if (!args) return null
    if (args.action === 'prepare') {
      return preparedPerpetualCall(context,args,call)
    }
    if (args.action === 'finalize') {
      const input=perpetualFinalizeArgumentsV1Schema.parse(args)
      if(input.request_id!==context.request.request_id) return null
      const frozen=perpetualFreeze(context,context.events.filter(event=>event.seq<call.seq))
      if(!frozen?.preparation || frozen.preparation.prepared_call_id!==input.prepared_call_id) return null
      perpetualAdmission(context,frozen.candidate,frozen.packet,call)
      return {...frozen,call}
    }
    if (canonical(Object.keys(args).sort()) !== canonical(['candidate', 'review_request'])) return null
    const candidate = parsePerpetualSynthesis(args.candidate), packet = parsePerpetualReviewPacket(args.review_request, candidate)
    perpetualAdmission(context, candidate, packet, call)
    return { call, candidate, packet }
  } catch { return null }
}
function originalPerpetualFreezes(context: SynthesisReviewContext, events: readonly SessionEventLike[]): PerpetualFreeze[] {
  const values:PerpetualFreeze[]=[]
  for (const call of events) {
    if (call.type !== 'tool/call' || callIdentity(call)?.name !== 'decision_hub_synthesis_submit') continue
    if (record(callIdentity(call)?.arguments)?.action==='finalize') continue
    const value = perpetualCall(context, call)
    if (!value) {
      const id=callIdentity(call)!.id, results=originalResultEvents(context.events,call)
      if(record(callIdentity(call)?.arguments)?.action==='prepare' && results.length===1 && successfulResult(results[0]!,id)) throw new Error('decision_hub_perpetual_preparation_history_invalid')
      continue
    }
    if (requiredPair(context.events, call)) { values.push(value); continue }
    const id = callIdentity(call)!.id, results = originalResultEvents(context.events, call)
    if (results.length === 1 && successfulResult(results[0]!, id)) {
      if(value.preparation){
        const prepared=returnedPayloads(results[0]!,id).map(payload=>perpetualPreparationResultSchema.safeParse(payload)).filter(result=>result.success)
        const expected={schema_version:value.preparation.schema_version==='perpetual-preparation-witness.v2'?'decision-hub-perpetual-preparation.v2':'decision-hub-perpetual-preparation.v1',preparation:value.preparation,candidate:value.candidate,review_request:value.packet}
        if(prepared.length===1 && canonical(prepared[0]!.data)===canonical(expected)) values.push(value)
        else throw new Error('decision_hub_perpetual_preparation_history_invalid')
        continue
      }
      const bodies = returnedPayloads(results[0]!, id).map(payload => perpetualSynthesisToolResultSchema.safeParse(payload)).filter(result => result.success)
      if (bodies.length === 1 && canonical(bodies[0]!.data!.candidate) === canonical(value.candidate)
        && canonical(bodies[0]!.data!.review_request) === canonical(value.packet)
        && !values.some(row=>canonical(row.candidate)===canonical(value.candidate) && canonical(row.packet)===canonical(value.packet))) values.push(value)
    }
  }
  return values
}
function perpetualHistory(context:SynthesisReviewContext,events:readonly SessionEventLike[]) {
  const freezes=originalPerpetualFreezes(context,events), attempts:{freeze:PerpetualFreeze;review:NonNullable<ReturnType<typeof perpetualReviewMatch>>|null}[]=[]
  const children=new Set<string>()
  for(const freeze of freezes){
    const previous=attempts.at(-1)
    if(previous && (!previous.review || previous.review.call.seq>=freeze.call.seq || previous.review.result.seq>=freeze.call.seq
      || previous.review.authorization.verdict==='approved' || !previous.review.findings
      || previous.freeze.preparation?.source_draft_hash===freeze.preparation?.source_draft_hash)) throw new Error('decision_hub_perpetual_revision_requires_negative_review')
    const next=freezes.find(row=>row.call.seq>freeze.call.seq)?.call.seq??Infinity
    const calls=events.filter(event=>event.type==='tool/call' && event.seq>freeze.call.seq && event.seq<next && callIdentity(event)?.name==='subagent_reviewer')
    if(calls.length>1)throw new Error('decision_hub_perpetual_prepare_already_reviewed')
    const matches=calls.flatMap(call=>{const value=perpetualReviewMatch(context,call,freeze);return value?[value]:[]})
    if(matches.length>1)throw new Error('decision_hub_perpetual_review_ambiguous')
    const review=matches[0]??null
    if(review){
      if(children.has(review.wrapper.child_session_id))throw new Error('decision_hub_perpetual_review_child_reused')
      children.add(review.wrapper.child_session_id)
      const marker='BEGIN_PREVIOUS_NEGATIVE_REVIEW_JSON',lines=review.wrapper.actual_prompt.split('\n')
      if(previous){
        const expected={authorization:previous.review!.authorization,findings:previous.review!.findings!},at=lines.indexOf(marker)
        if(at<0 || lines.lastIndexOf(marker)!==at || lines[at+1]!==canonicalJson(expected)
          || lines[at+2]!=='END_PREVIOUS_NEGATIVE_REVIEW_JSON' || lines[at+3]!==`PREVIOUS_NEGATIVE_REVIEW_SHA256=${bodyHash(expected)}`)throw new Error('decision_hub_perpetual_previous_review_changed')
      }else if(lines.includes(marker))throw new Error('decision_hub_perpetual_previous_review_unavailable')
    }
    attempts.push({freeze,review})
  }
  return {attempts,active:attempts.at(-1)??null}
}
function perpetualFreeze(context:SynthesisReviewContext,events:readonly SessionEventLike[]):PerpetualFreeze|null {
  return perpetualHistory(context,events).active?.freeze??null
}
export function preparePerpetualToolCall(context:SynthesisReviewContext,args:unknown,callId:string){
  const view=authorizedWindow(context,callId,'decision_hub_synthesis_submit')
  if(context.binding.generation!==1 || canonical(callIdentity(view.call)?.arguments)!==canonical(args)) throw new Error('decision_hub_perpetual_prepare_identity_mismatch')
  const prior=perpetualHistory(context,view.window).active
  if(prior && (!prior.review || prior.review.authorization.verdict==='approved' || !prior.review.findings)) throw new Error('decision_hub_perpetual_revision_requires_negative_review')
  const value=preparedPerpetualCall(context,args,view.call)
  if(!value.preparation) throw new Error('decision_hub_perpetual_prepare_invalid')
  if(prior?.freeze.preparation?.source_draft_hash===value.preparation.source_draft_hash)throw new Error('decision_hub_perpetual_revision_unchanged')
  assertCurrentPerpetualPublicationCostBinding(value.candidate,context.perpetualReviewOriginals!)
  return perpetualPreparationResultSchema.parse({schema_version:value.preparation.schema_version==='perpetual-preparation-witness.v2'?'decision-hub-perpetual-preparation.v2':'decision-hub-perpetual-preparation.v1',preparation:value.preparation,candidate:value.candidate,review_request:value.packet})
}
export function resolvePerpetualPreparedCall(context:SynthesisReviewContext,preparedCallId:string,callId:string){
  const view=authorizedWindow(context,callId,'decision_hub_synthesis_submit'), value=perpetualCall(context,view.call)
  if(!value?.preparation || value.preparation.prepared_call_id!==preparedCallId) throw new Error('decision_hub_perpetual_preparation_handle_mismatch')
  return {candidate:value.candidate,review_request:value.packet,preparation:value.preparation}
}
function qualifiedPerpetualReviewer(context: SynthesisReviewContext, callId: string): QualifiedPerpetualReviewerContext {
  const view = authorizedWindow(context, callId, 'subagent_reviewer')
  if (context.binding.generation !== 1) throw new Error('decision_hub_reviewer_budget_used')
  if (view.events.filter(event => event.seq < view.call.seq && event.type === 'tool/call'
    && ['subagent', 'dsh.internal.subagent', 'subagent_reviewer'].includes(callIdentity(event)?.name ?? '')).length >= context.request.execution_budget.max_subagents) throw new Error('decision_hub_reviewer_subagent_budget_exhausted')
  const history=perpetualHistory(context,view.window), freeze=history.active?.freeze
  if (!freeze) throw new Error('decision_hub_reviewer_qualified_freeze_required')
  if(history.active?.review || view.window.some(event=>event.type==='tool/call' && event.seq>freeze.call.seq && callIdentity(event)?.name==='subagent_reviewer'))throw new Error('decision_hub_perpetual_prepare_already_reviewed')
  const prior=history.attempts.at(-2)?.review
  perpetualAdmission(context, freeze.candidate, freeze.packet, view.call)
  const trusted = trustedSynthesisEvidence(context)
  return { protocol: 'decision_v2', context, candidate: freeze.candidate, review_request: freeze.packet, ...(freeze.preparation ? {preparation:freeze.preparation}:{}),
    candidate_json: JSON.stringify(freeze.candidate), candidate_sha256: hash(JSON.stringify(freeze.candidate)),
    freeze_call_id: callIdentity(freeze.call)!.id, freeze_seq: freeze.call.seq,
    reviewer_call_seq: view.call.seq, reviewer_started_at: new Date(view.call.time!).toISOString(), trusted_evidence_ids: trusted.ids, evidence_digest: evidenceDigest(context, trusted),
    ...(prior?.findings?{previous_review:{authorization:prior.authorization,findings:prior.findings}}:{}) }
}
function perpetualReviewMatch(context: SynthesisReviewContext, call: SessionEventLike, freeze: PerpetualFreeze) {
  const id = callIdentity(call)?.id
  if (!id || call.seq <= freeze.call.seq) return null
  const results = originalResultEvents(context.events, call)
  if (results.length !== 1 || !successfulResult(results[0]!, id)) return null
  const values = returnedPerpetualReviewPayloads(results[0]!, id).map(value => perpetualFrozenReviewResultSchema.safeParse(value)).filter(result => result.success)
  if (values.length !== 1) return null
  const wrapper = values[0]!.data!
  if (!sameBinding(wrapper.binding, context.binding) || wrapper.freeze_call_id !== callIdentity(freeze.call)?.id || wrapper.freeze_seq !== freeze.call.seq
    || wrapper.candidate_json !== JSON.stringify(freeze.candidate) || wrapper.candidate_sha256 !== hash(wrapper.candidate_json)
    || wrapper.review_request_json !== JSON.stringify(freeze.packet) || wrapper.review_request_sha256 !== hash(wrapper.review_request_json)
    || wrapper.actual_prompt_sha256 !== hash(wrapper.actual_prompt) || wrapper.child_events_sha256 !== hash(wrapper.child_events_json)
    || wrapper.child_parent_session_id !== context.binding.sessionId || wrapper.child_session_id === context.binding.sessionId
    || wrapper.original_output.runId !== wrapper.child_session_id) return null
  const lines = wrapper.actual_prompt.split('\n'), start = lines.indexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON')
  if (start < 0 || lines.lastIndexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON') !== start || lines[start + 2] !== 'END_PERPETUAL_REVIEW_PACKET_JSON'
    || lines[start + 1] !== wrapper.review_request_json) return null
  const child = decodePerpetualReviewProof(wrapper.child_events_json)
  if (!Array.isArray(child) || child.length === 0) return null
  const answers = child.filter(event => record(event)?.type === 'assistant/message')
  const ends = child.filter(event => record(event)?.type === 'turn/end')
  if (!hasExactReviewerPrompt(child, wrapper.actual_prompt, context.events, call.seq)
    || answers.length === 0 || canonical(record(record(answers.at(-1))?.data)?.message && record(record(record(answers.at(-1))?.data)?.message)?.content
      || record(record(answers.at(-1))?.data)?.content) !== canonical(wrapper.original_output.output)
    || ends.length !== 1 || record(record(record(ends[0])?.data)?.reason)?.kind !== 'completed') return null
  const outputs = records(wrapper.original_output.output).filter(row => row.schema_version === (freeze.packet.schema_version === 'action-review-request.v3' ? 'action-review-authorization.v3' : 'action-review-authorization.v2'))
  if (outputs.length !== 1) return null
  try {
    if(freeze.preparation && canonical(modelAssumptionsFromPrompt(wrapper.actual_prompt))!==canonical(freeze.preparation.assumptions)) return null
    const selected = validatePerpetualReviewOriginals(freeze.candidate, reviewOriginalsFromPrompt(wrapper.actual_prompt), freeze.preparation?.assumptions.flatMap(row=>row.evidence_refs))
    const available = context.perpetualReviewOriginals!
    if (selected.run_id !== context.binding.runId || selected.request_hash !== context.binding.requestHash || Date.parse(selected.as_of) > call.time!
      || selected.originals.some(row => !available.originals.some(actual => canonical(actual) === canonical(row)))) return null
    const authorization = validatePerpetualAuthorization(freeze.packet, outputs[0])
    if (Date.parse(authorization.reviewed_at) !== call.time) return null
    const findings=lines.includes('FINDINGS_FIXED_FIELDS_JSON') ? perpetualFindingsFromOutput(freeze.packet,wrapper.original_output.output,selected,wrapper.actual_prompt):null
    if(freeze.preparation){
      const replay=preparedPerpetualCall({...context,events:[],perpetualReviewOriginals:selected},callIdentity(freeze.call)!.arguments,freeze.call)
      if(canonical(replay.preparation)!==canonical(freeze.preparation) || canonical(replay.candidate)!==canonical(freeze.candidate))return null
    }
    return { wrapper, authorization, findings, call, result: results[0]! }
  } catch { return null }
}
export function evaluatePerpetualFinalReviewGate(context: SynthesisReviewContext, raw: unknown, reviewRequest: unknown, callId: string): z.infer<typeof perpetualGateSchema> {
  const candidate = parsePerpetualSynthesis(raw), packet = parsePerpetualReviewPacket(reviewRequest, candidate)
  const view = authorizedWindow(context, callId, 'decision_hub_synthesis_submit')
  perpetualAdmission(context, candidate, packet, view.call)
  const actual = perpetualCall(context, view.call)
  if (!actual || canonical(actual.candidate) !== canonical(candidate) || canonical(actual.packet) !== canonical(packet)) throw new Error('decision_hub_perpetual_call_packet_mismatch')
  const freeze = perpetualFreeze(context, view.window)
  const changed = freeze !== null && (canonical(freeze.candidate) !== canonical(candidate) || canonical(freeze.packet) !== canonical(packet))
  const matches = freeze && !changed ? view.window.filter(event => event.type === 'tool/call' && callIdentity(event)?.name === 'subagent_reviewer')
    .flatMap(call => { const match = perpetualReviewMatch(context, call, freeze); return match ? [match] : [] }) : []
  if (matches.length > 1) throw new Error('decision_hub_perpetual_review_ambiguous')
  const chosen = matches[0], candidateJson = JSON.stringify(candidate), packetJson = JSON.stringify(packet)
  const metadata = (reason: z.infer<typeof perpetualGateSchema>['reason']): z.infer<typeof perpetualGateSchema> => ({
    schema_version: 'decision-hub-final-review-gate.v3', status: chosen ? 'matched' : 'unreviewed', reason,
    binding: { ...context.binding }, candidate_json: candidateJson, candidate_sha256: hash(candidateJson),
    review_request_json: packetJson, review_request_sha256: hash(packetJson),
    freeze_call_id: freeze ? callIdentity(freeze.call)!.id : null, freeze_seq: freeze?.call.seq ?? null,
    review_receipt_hash: chosen ? bodyHash(chosen.authorization) : null, review_call_id: chosen ? callIdentity(chosen.call)!.id : null,
  })
  if (changed) return metadata('frozen_candidate_changed')
  if (chosen) {
    if(actual.preparation && chosen.authorization.verdict!=='approved')throw new Error('decision_hub_perpetual_review_not_approved')
    return metadata('exact_target')
  }
  if (view.events.some(event => event.seq < view.call.seq && event.type === 'tool/call' && callIdentity(event)?.name === 'subagent_reviewer') || context.binding.generation !== 1) return metadata('review_attempt_used')
  if (view.events.filter(event => event.seq < view.call.seq && event.type === 'tool/call'
    && ['subagent', 'dsh.internal.subagent', 'subagent_reviewer'].includes(callIdentity(event)?.name ?? '')).length >= context.request.execution_budget.max_subagents) return metadata('subagent_budget_exhausted')
  if (freeze) return metadata('review_instruction_exhausted')
  throw new Error(REQUIRED)
}

