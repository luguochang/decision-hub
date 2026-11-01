import { createHash } from 'node:crypto'
import { providerCanonicalJson, type prepareProviderInput } from './provider-input.js'
import type { SkillHostIdentity } from './skill-host.js'
import type { SkillToolOccurrence } from './skill-transcript.js'

type Prepared = Awaited<ReturnType<typeof prepareProviderInput>>
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
const hash = (v: unknown) => createHash('sha256').update(providerCanonicalJson(v)).digest('hex')
function fail(): never { throw new Error('skill_actual_tool_occurrence_invalid') }

/** Construct locators from the actual saved model input. Hub authenticates it. */
export function skillOccurrence(prepared: Prepared, identity: SkillHostIdentity, callId: string, toolName: string) {
  const p = prepared.identity
  if (p.run_id !== identity.runId || p.generation !== identity.generation || p.request_hash !== identity.requestHash
    || p.root_session_id !== identity.rootSessionId || p.execution_session_id !== identity.executionSessionId
    || identity.rootSessionId !== identity.executionSessionId || prepared.receipt.dispatch_id !== p.dispatch_id
    || prepared.receipt.input_hash !== createHash('sha256').update(prepared.inputJson).digest('hex')) fail()
  const input: unknown = JSON.parse(prepared.inputJson)
  if (!record(input) || !Array.isArray(input.messages)) fail()
  const calls: { mi: number; bi: number; block: Record<string, unknown> }[] = []
  const results: { mi: number; message: Record<string, unknown>; block: Record<string, unknown> }[] = []
  for (const [mi, message] of input.messages.entries()) {
    if (!record(message) || !Array.isArray(message.content)) continue
    for (const [bi, block] of message.content.entries()) {
      if (!record(block)) continue
      if (block.type === 'tool-call' && block.id === callId) {
        if (message.role !== 'assistant' || !record(message.source) || message.source.kind !== 'model') fail()
        calls.push({ mi, bi, block })
      }
      if (block.type === 'tool-result' && block.toolCallId === callId) {
        if (message.role !== 'user' || !record(message.source) || message.source.kind !== 'tool'
          || message.source.callId !== callId || message.content.length !== 1) fail()
        results.push({ mi, message, block })
      }
    }
  }
  if (calls.length !== 1 || results.length !== 1) fail()
  const call = calls[0]!, result = results[0]!
  if (call.mi >= result.mi || call.block.name !== toolName || typeof call.block.arguments !== 'string'
    || result.block.isError === true || !Array.isArray(result.block.content) || result.block.content.length !== 1) fail()
  const text = result.block.content[0]
  if (!record(text) || text.type !== 'text' || typeof text.text !== 'string') fail()
  const args: unknown = JSON.parse(call.block.arguments), value: unknown = JSON.parse(text.text)
  providerCanonicalJson(args); providerCanonicalJson(value)
  const proof: SkillToolOccurrence = { kind: 'provider_input_transcript.v1', input_ref: prepared.receipt.reference,
    dispatch_id: p.dispatch_id, call_id: callId, tool_name: toolName,
    call_json_pointer: `/messages/${call.mi}/content/${call.bi}`, result_json_pointer: `/messages/${result.mi}`,
    call_hash: hash(call.block), result_hash: hash(result.message) }
  return { proof, arguments: args, result: value }
}

export function completedSkillCalls(prepared: Prepared): string[] {
  const input: unknown = JSON.parse(prepared.inputJson)
  if (!record(input) || !Array.isArray(input.messages)) fail()
  const successful = new Set<string>()
  for (const message of input.messages) {
    if (!record(message) || !Array.isArray(message.content)) continue
    for (const block of message.content) if (record(block) && block.type === 'tool-result'
      && block.isError !== true && typeof block.toolCallId === 'string') successful.add(block.toolCallId)
  }
  const output: string[] = []
  for (const message of input.messages) {
    if (!record(message) || !Array.isArray(message.content)) continue
    for (const block of message.content) {
      if (record(block) && block.type === 'tool-call' && block.name === 'decision_hub_skill' && typeof block.id === 'string' && successful.has(block.id)) {
        output.push(block.id)
        if (output.length > 64) throw new Error('skill_transcript_call_census_limit')
      }
    }
  }
  return output
}
