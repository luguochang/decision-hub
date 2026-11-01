import { createHash, randomUUID } from 'node:crypto'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import type { SynthesisReviewContext } from '../final-review-gate.js'
import type { NativeSessionPublic, NativeTokenMeterPublic } from './dsh-public.js'
import { HubClientError } from './hub-client.js'
import { providerCanonicalJson } from './provider-input.js'
import { makeClaimedRootContextMessage, protectedOriginal } from './retained-context.js'
import { deterministicIds } from './session-correlation.js'

const invalid = () => new HubClientError('research_provider_native_context_invalid', false)
const controlPlugin = 'decision-hub-protected-context'

function predecessor(context: SynthesisReviewContext, pluginBuildHash: string,
  session: NativeSessionPublic, tokenMeter: NativeTokenMeterPublic | undefined) {
  if (session.id !== context.binding.sessionId || session.header?.parentSession !== undefined
    || !session.header || !session.events || typeof session.deriveMessages !== 'function'
    || providerCanonicalJson(session.events) !== providerCanonicalJson(context.events)) throw invalid()
  // Fresh projection and the authenticated predecessor RPC must both qualify
  // before changing the official surface. This creates no authorization cache.
  makeClaimedRootContextMessage(context, pluginBuildHash)
  const surface = session.deriveMessages()
  const markers = surface.filter(message => message.role === 'user' && message.source.kind === 'plugin'
    && message.source.plugin === controlPlugin)
  if (markers.length === 0) return undefined
  if (markers.length !== 1 || typeof session.append !== 'function'
    || !tokenMeter || typeof tokenMeter.estimateMessage !== 'function') throw invalid()
  const actual = markers[0]!, previous = protectedOriginal(context, actual)
  const binding = context.binding
  const ids = deterministicIds(binding.runId, binding.requestHash, binding.generation - 1)
  const expected = { run_id: binding.runId, root_session_id: binding.sessionId,
    request_hash: binding.requestHash, generation: binding.generation - 1, plugin_build_hash: pluginBuildHash,
    request_id: context.request.request_id, command_request_id: ids.requestId }
  for (const key of Object.keys(expected) as (keyof typeof expected)[]) {
    if (previous[key] !== expected[key]) throw invalid()
  }
  const old = context.events.find(event => event.type === 'user/message' && event.data.id === actual.id)!
  const prompts = context.events.filter(event => event.type === 'user/message'
    && typeof event.data.source === 'object' && event.data.source !== null
    && (event.data.source as Record<string, unknown>).kind === 'user'
    && (event.data.source as Record<string, unknown>).rpcId === ids.requestId)
  if (prompts.length !== 1 || prompts[0]!.seq >= old.seq) throw invalid()
  const shadowedTokenCount = tokenMeter.estimateMessage(actual)
  if (!Number.isSafeInteger(shadowedTokenCount) || shadowedTokenCount < 0) throw invalid()
  return { surface, actual, old, previous, shadowedTokenCount }
}

/** Retire only a proven predecessor control. Its complete native event remains;
 * the new complete control still enters after the SDK's real step and prompt. */
export function retireClaimedRootContextPredecessor(context: SynthesisReviewContext, pluginBuildHash: string,
  session: NativeSessionPublic, tokenMeter: NativeTokenMeterPublic | undefined): boolean {
  if (context.binding.generation === 1) return false
  const prior = predecessor(context, pluginBuildHash, session, tokenMeter)
  if (prior === undefined) return false
  const { surface, actual, old, previous, shadowedTokenCount } = prior
  const binding = context.binding
  const notice: UserMessage = { id: randomUUID() as UserMessage['id'], role: 'user',
    source: { kind: 'plugin', plugin: 'decision-hub-context-handoff' }, content: [
      { type: 'text', text: 'Historical control handoff only. The previous control is retired. This notice contains no research evidence, current instructions, permissions or budget; the next complete authenticated control supplies the current generation.' },
      { type: 'text', text: providerCanonicalJson({ schema_version: 'decision-hub-context-handoff.v1',
        run_id: binding.runId, root_session_id: binding.sessionId,
        previous_generation: previous.generation, current_generation: binding.generation,
        previous_command_request_id: previous.command_request_id, current_command_request_id: binding.requestId,
        previous_marker_id: actual.id, previous_marker_seq: old.seq,
        previous_marker_hash: createHash('sha256').update(providerCanonicalJson(actual)).digest('hex') }) },
    ] }
  session.append!('compaction/prune', { shadowedRange: { start: old.seq, end: old.seq },
    shadowedSeqs: [old.seq], shadowedTokenCount })
  session.append!('user/message', notice, { surfaceOp: { op: 'replace', start: old.seq, end: old.seq },
    sourceEventSeqs: [old.seq] })
  if (providerCanonicalJson(session.deriveMessages!())
    !== providerCanonicalJson(surface.map(message => message === actual ? notice : message))) throw invalid()
  return true
}
