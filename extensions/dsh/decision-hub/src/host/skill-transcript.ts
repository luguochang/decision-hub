import { researchSkillLoadResponseV1Schema, researchSkillLoadResponseV2Schema } from '@decision-hub/contracts-ts'
/** Artifact draft. Locator construction is not Hub authentication or a loaded event. */
import { createHash } from 'node:crypto'
import { providerCanonicalJson, type prepareProviderInput } from './provider-input.js'

type PreparedInput = Awaited<ReturnType<typeof prepareProviderInput>>
type CallbackRef = PreparedInput['receipt']['reference']
export type SkillLoadResponse = ReturnType<typeof researchSkillLoadResponseV1Schema.parse>
  | ReturnType<typeof researchSkillLoadResponseV2Schema.parse>
export function parseSkillLoadResponse(value: unknown): SkillLoadResponse {
  // Host shape compatibility does not authorize a Kernel protocol downgrade.
  const schema = record(value) && value.schema_version === 'research-skill-load.v1'
    ? researchSkillLoadResponseV1Schema : researchSkillLoadResponseV2Schema
  const parsed = schema.safeParse(value)
  if (!parsed.success) fail('skill_load_response_invalid')
  return parsed.data
}
export type SkillToolOccurrence = {
  kind: 'provider_input_transcript.v1'
  input_ref: CallbackRef
  dispatch_id: string
  call_id: string
  tool_name: string
  call_json_pointer: string
  result_json_pointer: string
  call_hash: string
  result_hash: string
}
export type SkillDeliveredOriginal = {
  runId: string
  generation: number
  requestHash: string
  rootSessionId: string
  executionSessionId: string
  callId: string
  toolName: string
  arguments: unknown
  result: unknown
  methodBody: string
}
export type LoadedLocatorProposal = {
  native_load: SkillToolOccurrence
  provider_input_ref: CallbackRef
  dispatch_id: string
  method_body_hash: string
  provider_input_method_loci: readonly string[]
  provider_input_result_loci: readonly string[]
}
const sha = (text: string): string => createHash('sha256').update(text, 'utf8').digest('hex')
const canonicalHash = (value: unknown): string => sha(providerCanonicalJson(value))
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
function fail(code: string): never { throw new Error(code) }

/**
 * Use only the returned actual prepareProviderInput value at Host middleware.
 * The Hub must independently read input_ref and authenticate these locators.
 * Neither this return value nor a cast to its type grants trusted status.
 */
export function proposeLoadedFromProviderInput(
  prepared: PreparedInput, delivered: SkillDeliveredOriginal,
): LoadedLocatorProposal {
  const identity = prepared.identity
  if (identity.run_id !== delivered.runId || identity.generation !== delivered.generation
      || identity.request_hash !== delivered.requestHash || identity.root_session_id !== delivered.rootSessionId
      || identity.execution_session_id !== delivered.executionSessionId
      || prepared.receipt.dispatch_id !== identity.dispatch_id
      || prepared.receipt.input_hash !== sha(prepared.inputJson)
      || prepared.receipt.reference.role !== 'research_provider_input'
      || !/^[a-f0-9]{64}$/.test(prepared.receipt.reference.payload_hash)) fail('skill_provider_input_identity_mismatch')
  if (delivered.executionSessionId !== delivered.rootSessionId) fail('skill_root_session_required')
  if (!delivered.callId || delivered.toolName !== 'decision_hub_skill'
      || typeof delivered.methodBody !== 'string' || !delivered.methodBody
      || Buffer.byteLength(prepared.inputJson, 'utf8') > 1_000_000) fail('skill_delivered_original_invalid')
  let parsed: unknown
  try { parsed = JSON.parse(prepared.inputJson) } catch { fail('skill_provider_input_json_invalid') }
  if (!record(parsed) || !Array.isArray(parsed.messages)) fail('skill_provider_input_json_invalid')
  const calls: { block: Record<string, unknown>; path: string; index: number }[] = []
  const results: { block: Record<string, unknown>; message: Record<string, unknown>; path: string; index: number }[] = []
  for (const [index, message] of parsed.messages.entries()) {
    if (!record(message) || !Array.isArray(message.content)) fail('skill_provider_message_invalid')
    for (const [blockIndex, block] of message.content.entries()) {
      if (!record(block)) continue
      const path = `/messages/${index}/content/${blockIndex}`
      if (block.type === 'tool-call' && block.id === delivered.callId) {
        if (message.role !== 'assistant' || !record(message.source) || message.source.kind !== 'model') fail('skill_load_call_role_invalid')
        calls.push({ block, path, index })
      }
      if (block.type === 'tool-result' && block.toolCallId === delivered.callId) {
        if (message.role !== 'user' || !record(message.source) || message.source.kind !== 'tool'
            || message.source.callId !== delivered.callId || message.content.length !== 1) fail('skill_load_result_role_invalid')
        results.push({ block, message, path: `/messages/${index}`, index })
      }
    }
  }
  if (calls.length !== 1 || results.length !== 1) fail('skill_load_occurrence_ambiguous_or_missing')
  const call = calls[0]!, result = results[0]!
  if (call.index >= result.index || call.block.name !== delivered.toolName
      || typeof call.block.arguments !== 'string' || result.block.isError === true) fail('skill_load_occurrence_invalid')
  let callArguments: unknown
  try { callArguments = JSON.parse(call.block.arguments) } catch { fail('skill_load_arguments_invalid') }
  if (providerCanonicalJson(callArguments) !== providerCanonicalJson(delivered.arguments)) fail('skill_load_arguments_mismatch')
  const content = result.block.content
  if (!Array.isArray(content) || content.length !== 1 || !record(content[0])
      || content[0].type !== 'text' || typeof content[0].text !== 'string') fail('skill_load_complete_result_required')
  let actualResult: unknown
  try { actualResult = JSON.parse(content[0].text) } catch { fail('skill_load_complete_result_required') }
  parseSkillLoadResponse(actualResult)
  parseSkillLoadResponse(delivered.result)
  if (providerCanonicalJson(actualResult) !== providerCanonicalJson(delivered.result)) fail('skill_load_result_mismatch')
  // The load response is one canonical JSON text block. Exact method bytes are
  // independently hashed, not inferred from a skill name or tool description.
  if (!record(actualResult) || actualResult.method_body !== delivered.methodBody) fail('skill_load_method_body_mismatch')
  const resultTextPath = `${result.path}/content/0/content/0/text`
  return {
    native_load: {
      kind: 'provider_input_transcript.v1', input_ref: prepared.receipt.reference,
      dispatch_id: identity.dispatch_id, call_id: delivered.callId, tool_name: delivered.toolName,
      call_json_pointer: call.path, result_json_pointer: result.path,
      call_hash: canonicalHash(call.block), result_hash: canonicalHash(result.message),
    },
    provider_input_ref: prepared.receipt.reference, dispatch_id: identity.dispatch_id,
    method_body_hash: sha(delivered.methodBody),
    // Locus is the real JSON-string containing the complete response. The Hub
    // parses that string and checks method_body; no invented native seq exists.
    provider_input_method_loci: [resultTextPath], provider_input_result_loci: [resultTextPath],
  }
}
