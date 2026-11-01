import { createHash } from 'node:crypto'
import type { Context } from '@deepseek-ai/cordis'
import {
  researchSynthesisCandidateSchema, perpetualFinalizeArgumentsV1Schema, type PerpetualPreparationResultV1, type PerpetualPreparationResultV2,
  type ResearchSynthesisCandidate,
} from '@decision-hub/contracts-ts'
import { defineTool, parameterSchemaSpecToJsonSchema, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import {
  evaluateFinalReviewGate, readSynthesisReviewContext, normalizeSynthesisCandidate, synthesisToolResultSchema,
  type FinalReviewGateMetadata, type SynthesisReviewContextReader,
  evaluatePerpetualFinalReviewGate, preparePerpetualToolCall, resolvePerpetualPreparedCall, type preparedPerpetualSynthesisToolResultSchema, type perpetualSynthesisToolResultSchema,
  perpetualPrepareArgumentsSchema, perpetualPreparationResultSchema, trustedChildSynthesisCoverage,
  type finalizedPerpetualSynthesisToolResultSchema,
} from './final-review-gate.js'
import { buildFinalizedCoverage } from './finalized-coverage.js'
import { verifySynthesisProviderInput } from './host/provider-input.js'
import { isDecisionCandidate, parseSynthesisCandidate, type SynthesisCandidate } from './synthesis-protocol.js'
import { decisionSynthesisToolParameters, perpetualSynthesisToolParameters, synthesisCandidateToolSchema } from './synthesis-tool-schema.js'
import { canonicalJson, parsePerpetualReviewPacket, parsePerpetualSynthesis, textHash } from './perpetual-synthesis-protocol.js'
import { synthesisProtocol } from './synthesis-protocol.js'
import type { z } from 'zod'

export const name = 'decision-hub-synthesis-tool'
export const inject = ['tools']
export const SYNTHESIS_TOOL_NAME = 'decision_hub_synthesis_submit'
const MAX_CANONICAL_SYNTHESIS_RESULT_BYTES = 1_000_000

export { normalizeSynthesisCandidate } from './final-review-gate.js'

const parameters = {
  candidate: {
    ...synthesisCandidateToolSchema,
    required: true,
    description: (
      'The complete research-synthesis-candidate.v1 object. It must use the exact '
      + 'request_id and Evidence IDs from the canonical request and successful research results. '
      + 'Follow every nested field in this schema. horizons is a top-level sibling of causal_case. '
      + 'Every causal link requires both confirmation and invalidation. Put a mechanism explanation '
      + 'inside statement; do not replace the required fields with new keys. Lists of IDs must be unique. '
      + 'The canonical validator additionally enforces non-empty strings, probability bounds, list bounds '
      + 'and timezone-aware datetimes. Missing facts remain missing; do not invent evidence or calibrated probabilities.'
    ),
  },
} as const

/**
 * Capture synthesis through DSH's validated Tool boundary.
 *
 * The Zod validator is generated from the canonical YAML contract. Returning
 * the validated object as structured Tool output lets the Hub attest it from
 * the DSH trajectory even when the model adds prose to its final message.
 */
export interface SynthesisToolConfig {
  readonly reviewContext?: SynthesisReviewContextReader
  readonly providerInput?: (context: Awaited<ReturnType<SynthesisReviewContextReader>>, executionSessionId: string,
    callId: string, actualArguments: unknown, signal: AbortSignal) => Promise<unknown>
}
export type SynthesisToolResult = {
  readonly schema_version: 'decision-hub-synthesis-tool-result.v1' | 'decision-hub-synthesis-tool-result.v2'
  readonly candidate: SynthesisCandidate
  readonly review_gate: FinalReviewGateMetadata
}

export function createSynthesisToolDefinition(config: SynthesisToolConfig = {}): ToolDefinition {
  const definition = defineTool({
    name: SYNTHESIS_TOOL_NAME,
    description: (
      'For decision_v2, prepare one complete authored draft and call the independent foreground reviewer. A genuine needs_revision/rejected result includes concrete findings: within the SAME original Task/gen/tool/child/deadline budgets, gather allowed evidence, revise the complete draft, prepare it again and obtain a NEW independent child review. Preserve every old attempt. Only finalize the latest approved prepared_call_id; old approvals never authorize changed packets. Product code computes references; never calculate SHA256 or copy nested packets. The following single-review instructions apply to legacy protocols. Freeze the complete final Decision Hub research synthesis after all '
      + 'approved evidence work is complete. A final-review-required error freezes this exact candidate; use the single budgeted reviewer, then resubmit unchanged. Invalid schema must be corrected '
      + 'inside this DSH session; this Tool does not fetch data, publish, notify, or trade.'
    ),
    // Preserve the original raw arguments for provider attestation and the
    // existing representation-only normalizer. The visible nested contract
    // guides new calls; strict Zod validation still checks the normalized value.
    parameters: { candidate: { type: 'json' }, review_request: { type: 'json' }, action:{type:'string'}, request_id:{type:'string'}, draft:{type:'json'}, prepared_call_id:{type:'string'} } as const,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    finalizeContent(exec, result) {
      // DSH invokes this after generic spill handling. The durable tool result
      // must retain this exact complete wrapper so the Hub can attest it; a
      // preview or a spill path cannot replace its original JSON witness.
      if (result.isError) return undefined
      try {
        const text = JSON.stringify(result.value)
        if (text === undefined || Buffer.byteLength(text, 'utf8') > MAX_CANONICAL_SYNTHESIS_RESULT_BYTES) return undefined
        const prepared=perpetualPreparationResultSchema.safeParse(result.value)
        if(prepared.success && prepared.data.preparation.prepared_call_id===exec.callId) return [{type:'text',text}]
        const parsed = synthesisToolResultSchema.safeParse(result.value)
        if (!parsed.success) return undefined
        const { candidate, review_gate: gate } = parsed.data
        if ((parsed.data.schema_version === 'decision-hub-synthesis-tool-result.v3' || parsed.data.schema_version === 'decision-hub-synthesis-tool-result.v4'
          || parsed.data.schema_version === 'decision-hub-synthesis-tool-result.v5')
          && (parsed.data.review_gate.review_request_json !== JSON.stringify(parsed.data.review_request)
            || textHash(parsed.data.review_gate.review_request_json) !== parsed.data.review_gate.review_request_sha256)) return undefined
        if (parsed.data.schema_version === 'decision-hub-synthesis-tool-result.v5') {
          const coverage = parsed.data.coverage_delivery
          if (gate.status !== 'matched' || canonicalJson(coverage.binding) !== canonicalJson(gate.binding)
            || coverage.finalize_call_id !== exec.callId || coverage.root_cutoff.before_seq !== coverage.finalize_call_seq
            || coverage.source_snapshot.message_seq >= coverage.finalize_call_seq) return undefined
        }
        if (gate.binding.sessionId !== exec.agent?.id
          || gate.candidate_json !== JSON.stringify(candidate)
          || gate.candidate_sha256 !== createHash('sha256').update(gate.candidate_json, 'utf8').digest('hex')) return undefined
        if (gate.status === 'matched'
          ? gate.reason !== 'exact_target' || gate.review_receipt_hash === null || gate.review_call_id === null
          : gate.reason === 'exact_target' || gate.review_receipt_hash !== null || gate.review_call_id !== null) return undefined
        // Preserve serialization from the executed value, not a parsed clone.
        // Unreviewed remains unreviewed; this projection grants no permission.
        return [{ type: 'text', text }]
      } catch {
        // Error/cancellation, unsupported output and corrupt values keep the
        // registry-owned result. Never reconstruct a witness from spill files.
        return undefined
      }
    },
    async execute(args, exec): Promise<ResearchSynthesisCandidate | SynthesisToolResult | PerpetualPreparationResultV1 | PerpetualPreparationResultV2 | z.infer<typeof preparedPerpetualSynthesisToolResultSchema> | z.infer<typeof perpetualSynthesisToolResultSchema> | z.infer<typeof finalizedPerpetualSynthesisToolResultSchema>> {
      if (exec.agent === undefined) throw new Error('decision_hub_synthesis_agent_required')
      // The unconfigured factory retains the legacy shape boundary. The live
      // plugin reads the server-owned request before selecting a validator.
      if (config.reviewContext === undefined) return researchSynthesisCandidateSchema.parse(normalizeSynthesisCandidate(args.candidate))
      if(args.action!==undefined){
        const phase=args.action==='prepare'?perpetualPrepareArgumentsSchema.parse(args):perpetualFinalizeArgumentsV1Schema.parse(args)
        const context=await config.reviewContext(exec.agent,phase.request_id,exec.signal,exec.callId)
        if(synthesisProtocol(context.request.output_schema_ref)!=='decision_v2') throw new Error('decision_hub_cross_protocol_preparation')
        if(config.providerInput) await config.providerInput(context,exec.agent.id,exec.callId,args,exec.signal)
        exec.signal.throwIfAborted()
        if(phase.action==='prepare') return preparePerpetualToolCall(context,args,exec.callId)
        const prepared=resolvePerpetualPreparedCall(context,phase.prepared_call_id,exec.callId)
        const review_gate = evaluatePerpetualFinalReviewGate(context,prepared.candidate,prepared.review_request,exec.callId)
        const child = trustedChildSynthesisCoverage(context)
        if (child.evidence.length === 0) return {schema_version:'decision-hub-synthesis-tool-result.v4',...prepared,review_gate}
        if (review_gate.status !== 'matched' || review_gate.reason !== 'exact_target') throw new Error('decision_hub_finalized_coverage_review_required')
        const coverage_delivery = buildFinalizedCoverage(context, exec.callId, child)
        if (coverage_delivery === null) throw new Error('decision_hub_finalized_coverage_missing')
        const result = {schema_version:'decision-hub-synthesis-tool-result.v5' as const,...prepared,review_gate,coverage_delivery}
        if (Buffer.byteLength(JSON.stringify(result), 'utf8') > MAX_CANONICAL_SYNTHESIS_RESULT_BYTES) {
          throw new Error('decision_hub_finalized_coverage_oversized')
        }
        return result
      }
      const raw = args.candidate
      if (typeof raw !== 'object' || raw === null || Array.isArray(raw)
        || !('request_id' in raw) || typeof raw.request_id !== 'string' || !raw.request_id) throw new Error('decision_hub_synthesis_request_required')
      const context = await config.reviewContext(exec.agent, raw.request_id, exec.signal,exec.callId)
      if (synthesisProtocol(context.request.output_schema_ref) === 'decision_v2') {
        const candidate = parsePerpetualSynthesis(raw), packet = parsePerpetualReviewPacket(args.review_request, candidate)
        if (candidate.schema_version !== 'research-decision-synthesis.v2' || packet.schema_version !== 'action-review-request.v2') throw new Error('decision_hub_perpetual_event_prepare_required')
        if (config.providerInput) await config.providerInput(context, exec.agent.id, exec.callId, args, exec.signal)
        exec.signal.throwIfAborted()
        return { schema_version: 'decision-hub-synthesis-tool-result.v3', candidate, review_request: packet,
          review_gate: evaluatePerpetualFinalReviewGate(context, candidate, packet, exec.callId) }
      }
      if (args.review_request !== undefined) throw new Error('decision_hub_cross_protocol_review_packet')
      const candidate = parseSynthesisCandidate(context.request.output_schema_ref, raw, normalizeSynthesisCandidate)
      exec.signal.throwIfAborted()
      if (config.providerInput) {
        await config.providerInput(context, exec.agent.id, exec.callId, args, exec.signal)
        exec.signal.throwIfAborted()
      }
      const reviewGate = evaluateFinalReviewGate(context, candidate, exec.callId, normalizeSynthesisCandidate)
      return { schema_version: isDecisionCandidate(candidate) ? 'decision-hub-synthesis-tool-result.v2' : 'decision-hub-synthesis-tool-result.v1', candidate, review_gate: reviewGate }
    },
  })
  const visible = parameterSchemaSpecToJsonSchema(parameters)
  return { ...definition, parameters: config.reviewContext === undefined ? { ...visible } : { ...perpetualSynthesisToolParameters(decisionSynthesisToolParameters(visible)) } }
}

export function apply(ctx: Context): void {
  ctx.tools.register(createSynthesisToolDefinition({
    reviewContext: (agent, requestId, signal, callId) => readSynthesisReviewContext(ctx.root, agent, requestId, signal, callId),
    providerInput: (context, sessionId, callId, args, signal) => verifySynthesisProviderInput(ctx.root, context, sessionId, callId, args, signal),
  }))
}
