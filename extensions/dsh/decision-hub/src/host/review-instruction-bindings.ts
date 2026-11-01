import { createHash } from 'node:crypto'
import { foldSurface } from '@deepseek-ai/dsh-session/surface'
import type { DshSessionResult } from '@decision-hub/contracts-ts'
import { parentBaseline } from '../reviewer-system-snapshot.js'
import { deterministicIds, type Correlation } from './session-correlation.js'
import type { SessionEventLike } from './dsh-public.js'

type Bindings = NonNullable<DshSessionResult['reviewer_instruction_bindings']>
interface Inspection { meta: { id: string }; events: SessionEventLike[] }
const invalid = (): never => { throw new Error('host_reviewer_instruction_binding_invalid') }
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function isInstruction(event: SessionEventLike): boolean {
  return event.type === 'user/message' && (record(event.data.source)?.kind === 'agent-instructions'
    || record(record(event.data.message)?.source)?.kind === 'agent-instructions')
}
function reviewerIdentity(event: SessionEventLike): string | null {
  if (event.type !== 'tool/call') return null
  const name = event.data.name ?? event.data.toolName
  if (typeof name !== 'string' || !/(?:^|[./])subagent_reviewer$/.test(name)) return null
  const id = event.data.callId ?? event.data.toolCallId ?? event.data.id
  return typeof id === 'string' && id.trim() ? id : invalid()
}

function reviewerContext(correlation: Correlation, prefix: readonly SessionEventLike[]) {
  const source = prefix.filter(event => event.type === 'user/message'
    && record(event.data.source)?.kind === 'user' && typeof record(event.data.source)?.rpcId === 'string').at(-1)?.data.source
  const requestId = record(source)?.rpcId
  for (let generation = 1; generation <= correlation.generation; generation++) {
    if (deterministicIds(correlation.runId, correlation.requestHash, generation).requestId === requestId) {
      return { reviewer_generation: generation, reviewer_request_id: requestId as string }
    }
  }
  return invalid()
}

/** Derive only from complete native originals, before the result notification filter. */
export function deriveReviewerInstructionBindings(correlation: Correlation, inspection: Inspection,
  resultEvents: readonly SessionEventLike[]): Bindings | undefined {
  const bindings: Bindings['bindings'] = [], ids = new Set<string>()
  for (const call of resultEvents) {
    const callId = reviewerIdentity(call)
    if (callId === null) continue
    const prefix = inspection.events.filter(event => event.seq < call.seq)
    if (!prefix.some(isInstruction)) continue // Historical transcripts without instructions retain their wire shape.
    if (inspection.meta.id !== correlation.sessionId || !Number.isSafeInteger(call.seq) || call.seq < 1
      || inspection.events[call.seq] !== call || prefix.length !== call.seq || ids.has(callId)
      || prefix.some((event, index) => event.seq !== index || !Number.isFinite(event.time))) invalid()
    const expectedIds = deterministicIds(correlation.runId, correlation.requestHash, correlation.generation)
    if (expectedIds.sessionId !== correlation.sessionId || expectedIds.requestId !== correlation.requestId) invalid()
    const expected = parentBaseline(inspection.events, call.seq)
    if (expected === null) invalid()
    // Also validate a single-baseline prefix; the shared legacy resolver deliberately
    // preserves its old single-baseline behavior, whereas this new proof requires full fold.
    const surface = foldSurface(prefix as unknown as Parameters<typeof foldSurface>[0])
    const active = prefix.filter(event => event.type === 'user/message' && event.data === expected
      && surface.nodes.includes(event.seq))
    if (active.length !== 1) invalid()
    const baseline = active[0]!
    ids.add(callId)
    bindings.push({ reviewer_call_id: callId, reviewer_call_seq: call.seq, ...reviewerContext(correlation, prefix),
      parent_prefix_last_seq: call.seq - 1,
      parent_prefix_sha256: createHash('sha256').update(JSON.stringify(prefix)).digest('hex'),
      baseline_event: { seq: baseline.seq, time: baseline.time, type: 'user/message',
        data: structuredClone(expected) as Bindings['bindings'][number]['baseline_event']['data'] } })
  }
  return bindings.length === 0 ? undefined : { schema_version: 'dsh-reviewer-instruction-bindings.v1',
    run_id: correlation.runId, dsh_session_id: correlation.sessionId, generation: correlation.generation,
    request_id: correlation.requestId, request_hash: correlation.requestHash, bindings }
}
