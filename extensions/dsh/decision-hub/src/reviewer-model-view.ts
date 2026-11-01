import type { Session } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-compaction/types'
import type { Message } from '@deepseek-ai/dsh-llm'
import type { z } from 'zod'
import type { perpetualFrozenReviewResultSchema } from './final-review-gate.js'
import { perpetualAuthorizationFromOutput, perpetualFindingsFromOutput, reviewOriginalsFromPrompt, textHash } from './perpetual-synthesis-protocol.js'

type CompleteReview = z.infer<typeof perpetualFrozenReviewResultSchema>
type ReviewSession = Pick<Session, 'events' | 'surface' | 'append'>
type Agent = { readonly id: string; readonly session?: ReviewSession }
type TokenMeter = { estimateMessage(message: Message): number }
type Pending = { callId: string; receiptHash: string; text: string }
const fail = () => new Error('decision_hub_perpetual_review_model_view_unavailable')

/** Per-plugin, one-step display handoff from the authenticated producer only. */
export class PerpetualReviewModelViews {
  private readonly pending = new Map<string, Map<string, Pending>>()

  remember(callId: string, wrapper: CompleteReview): void {
    const packet = JSON.parse(wrapper.review_request_json)
    const authorization = perpetualAuthorizationFromOutput(packet, wrapper.original_output.output)
    const findings = perpetualFindingsFromOutput(packet, wrapper.original_output.output, reviewOriginalsFromPrompt(wrapper.actual_prompt), wrapper.actual_prompt)
    const receiptHash = textHash(JSON.stringify(wrapper))
    const text = JSON.stringify({ display_only: true, original_review_call_id: callId, prepared_call_id: wrapper.freeze_call_id,
      original_receipt_sha256: receiptHash, child_session_id: wrapper.child_session_id,
      proof: { candidate_sha256: wrapper.candidate_sha256, review_request_sha256: wrapper.review_request_sha256,
        actual_prompt_sha256: wrapper.actual_prompt_sha256, child_events_sha256: wrapper.child_events_sha256 },
      authorization, findings,
      next_step: authorization.verdict === 'approved'
        ? 'The original native proof remains authoritative. Finalize only this unchanged prepared_call_id within the original remaining budgets; this display grants no new authorization.'
        : 'Preserve this genuine negative verdict and every finding. Within the original remaining Task/tool/child/deadline budgets, supplement permitted evidence and revise the complete draft, prepare again and obtain a NEW independent child review. Never finalize this packet, reuse its authorization, invent approval or reset budgets.' })
    const owned = this.pending.get(wrapper.binding.sessionId) ?? new Map<string, Pending>()
    const prior = owned.get(callId)
    if (prior && prior.receiptHash !== receiptHash) throw fail()
    owned.set(callId, { callId, receiptHash, text })
    this.pending.set(wrapper.binding.sessionId, owned)
  }

  async beforeStep<T>(agent: Agent, meter: TokenMeter | undefined, next: () => Promise<T>): Promise<T> {
    const owned = this.pending.get(agent.id)
    if (!owned?.size) return next()
    const session = agent.session
    if (!session || !meter) throw fail()
    for (const item of owned.values()) {
      const originals = session.events.filter(event => event.type === 'tool/result' && event.surfaceOp === 'append'
        && event.data.message.source.kind === 'tool' && event.data.message.source.callId === item.callId)
      if (originals.length !== 1) throw fail()
      const original = originals[0]!
      if (original.type !== 'tool/result') throw fail()
      const message = original.data.message, result = message.content[0], content = result.content
      if (result.isError || content.length !== 1 || content[0]?.type !== 'text'
        || textHash(content[0].text) !== item.receiptHash || !session.surface.nodes.includes(original.seq)
        || Array.from(item.text).length >= Array.from(content[0].text).length) throw fail()
      const tokens = meter.estimateMessage(message)
      if (!Number.isFinite(tokens) || tokens < 0) throw fail()
      try {
        session.append('compaction/prune', { shadowedRange: { start: original.seq, end: original.seq },
          shadowedSeqs: [original.seq], shadowedTokenCount: tokens })
        session.append('tool/result', { ...original.data, message: { ...message,
          content: [{ ...result, content: [{ ...content[0], text: item.text }] }] } },
        { surfaceOp: { op: 'replace', start: original.seq, end: original.seq }, sourceEventSeqs: [original.seq] })
      } catch { throw fail() }
      owned.delete(item.callId)
    }
    this.pending.delete(agent.id)
    return next()
  }
}
