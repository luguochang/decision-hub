/** Artifact draft: thin host coordinator. No production tool/route is registered. */
import { createHash } from 'node:crypto'
import { z } from 'zod'
import { researchSkillHypothesisOutputV1Schema } from '@decision-hub/contracts-ts'
import { providerCanonicalJson, type prepareProviderInput } from './provider-input.js'
import { parseSkillLoadResponse, type SkillLoadResponse, type SkillToolOccurrence } from './skill-transcript.js'
export type { SkillLoadResponse } from './skill-transcript.js'
import { skillOccurrence, completedSkillCalls } from './skill-occurrence.js'
import { parseSelectedSkillDelivery, parseSelectedSkillDeliveryDecision, selectedSkillProofFromProviderInput, selectedSkillStageRequestId,
  type SelectedSkillDelivery, type SelectedSkillDeliveryDecision, type SelectedSkillInputProof } from './selected-skill-delivery.js'

export const SKILL_TOOL_NAME = 'decision_hub_skill'
export type SkillBusinessArgs = { operation: 'list' }
  | { operation: 'load'; skill_version_id: string }
  | { operation: 'execute_step'; skill_version_id: string; node_id: string; input: Record<string, unknown> }
export type SkillHostIdentity = {
  runId: string; generation: number; requestHash: string; rootSessionId: string; executionSessionId: string
}
export type SkillExecutionContext = { agent: object; callId: string; signal: AbortSignal }
export type SkillStageRecord = {
  stage: 'loaded' | 'consumed'; stage_request_id: string; proof: SkillToolOccurrence
} | { stage: 'loaded'; stage_request_id: string; proof: SelectedSkillInputProof }
export type SkillHostHub = {
  tool(identity: SkillHostIdentity, callId: string, args: SkillBusinessArgs,
    researchOccurrence: SkillToolOccurrence | null, signal: AbortSignal): Promise<unknown>
  record(identity: SkillHostIdentity, request: SkillStageRecord, signal: AbortSignal): Promise<unknown>
}

const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
const digest = (text: string): string => createHash('sha256').update(text, 'utf8').digest('hex')
function fail(code: string): never { throw new Error(code) }
export const SKILL_OPERATION_FIELDS = {
  list: ['operation'],
  load: ['operation', 'skill_version_id'],
  execute_step: ['operation', 'skill_version_id', 'node_id', 'input'],
} as const
export const SKILL_NODE_INPUTS = {
  spot_evidence: z.union([z.object({ research_call_id: z.string().min(1).max(256) }).strict(), z.object({ source: z.literal('frozen_original') }).strict()]),
  features: z.object({}).strict(),
  hypothesis: z.object({ output: researchSkillHypothesisOutputV1Schema }).strict(),
} as const
export const SKILL_NODE_IDS = ['spot_evidence', 'features', 'hypothesis'] as const
const operationExample = {
  list: { operation: 'list' },
  load: { operation: 'load', skill_version_id: '<exact version from list/load>' },
  execute_step: { operation: 'execute_step', skill_version_id: '<same exact version from list/load>', node_id: 'features', input: {} },
} as const
export const SKILL_OPERATION_DESCRIPTION = Object.entries(SKILL_OPERATION_FIELDS)
  .map(([operation, fields]) => `${operation}: required ${fields.join(', ')}; no other fields. Example: ${JSON.stringify(operationExample[operation as keyof typeof operationExample])}.`).join(' ')
function nonemptyId(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 128 }
function argumentFailure(value: Record<string, unknown>, invalidFields: string[] = [], issues: unknown[] = []): never {
  const operation = typeof value.operation === 'string' && Object.hasOwn(SKILL_OPERATION_FIELDS, value.operation)
    ? value.operation as keyof typeof SKILL_OPERATION_FIELDS : null
  const expected: readonly string[] = operation === null ? ['operation'] : SKILL_OPERATION_FIELDS[operation]
  const missing = expected.filter(key => !Object.hasOwn(value, key))
  const unexpected = Object.keys(value).filter(key => !expected.includes(key)).length
  fail('skill_business_arguments_invalid ' + providerCanonicalJson({
    schema_version: 'skill-argument-error.v1', operation,
    missing_fields: missing, invalid_fields: invalidFields, unexpected_field_count: unexpected,
    expected_fields: expected, issues,
    repair: 'Use the same exact skill_version_id returned by list/load on every load and execute_step. Do not invent a version or identity. Keep the intended node and correct its input using the visible node schema; do not replace it with another example node.',
    example: operation === 'execute_step' && value.node_id === 'spot_evidence'
      ? { ...operationExample.execute_step, node_id: 'spot_evidence', input: { research_call_id: '<actual completed decision_hub_research call ID>' } }
      : operation === 'execute_step' && value.node_id === 'hypothesis'
        ? null // The complete nested schema is visible; never fabricate provenance-bearing example output.
        : operation === null ? operationExample.list : operationExample[operation],
  }))
}
export function parseSkillBusinessArgs(value: unknown): SkillBusinessArgs {
  if (!isRecord(value)) argumentFailure({}, ['$'])
  providerCanonicalJson(value)
  if (typeof value.operation !== 'string' || !Object.hasOwn(SKILL_OPERATION_FIELDS, value.operation)) argumentFailure(value, ['operation'])
  const operation = value.operation as keyof typeof SKILL_OPERATION_FIELDS
  const expected: readonly string[] = SKILL_OPERATION_FIELDS[operation]
  const invalid: string[] = []
  if (operation !== 'list' && Object.hasOwn(value, 'skill_version_id') && !nonemptyId(value.skill_version_id)) invalid.push('skill_version_id')
  if (operation === 'execute_step') {
    if (Object.hasOwn(value, 'node_id') && !SKILL_NODE_IDS.includes(value.node_id as typeof SKILL_NODE_IDS[number])) invalid.push('node_id')
    if (Object.hasOwn(value, 'input') && !isRecord(value.input)) invalid.push('input')
  }
  if (invalid.length || expected.some(key => !Object.hasOwn(value, key)) || Object.keys(value).some(key => !expected.includes(key))) argumentFailure(value, invalid)
  if (operation === 'execute_step') {
    const parsed = SKILL_NODE_INPUTS[value.node_id as keyof typeof SKILL_NODE_INPUTS].safeParse(value.input)
    if (!parsed.success) {
      const issues = parsed.error.issues.slice(0, 32).map(issue => ({
        path: ['input', ...issue.path], code: issue.code,
        ...('options' in issue ? { allowed: issue.options } : {}),
        ...('expected' in issue ? { expected: issue.expected } : {}),
      }))
      argumentFailure(value, issues.map(issue => issue.path.join('.')), issues)
    }
  }
  // Shape validation never authorizes execution. The ordinary coordinator and
  // Hub still authenticate the exact version, provider originals and ordering.
  return value as SkillBusinessArgs
}
function validateLoad(value: SkillLoadResponse, versionId: string): void {
  value = parseSkillLoadResponse(value)
  const hashes = [value.version_hash, value.package_hash, value.workflow_hash, value.compiled_plan_hash, value.method_body_hash]
  if (!['research-skill-load.v1', 'research-skill-load.v2'].includes(value.schema_version) || value.status !== 'delivered'
      || value.skill_version_id !== versionId || !nonemptyId(value.use_id)
      || hashes.some(hash => !/^[a-f0-9]{64}$/.test(hash)) || typeof value.method_body !== 'string'
      || value.method_body.length === 0 || digest(value.method_body) !== value.method_body_hash) fail('skill_load_response_invalid')
  // Reuse existing complete native result/provider-input byte bound. Never
  // truncate a method; oversized delivery fails with the original retained.
  if (Buffer.byteLength(providerCanonicalJson(value), 'utf8') > 1_000_000) fail('skill_load_response_too_large')
}

export class SkillHostCoordinator {
  private readonly inputs = new Map<string, Awaited<ReturnType<typeof prepareProviderInput>>>()
  private readonly recorded = new Set<string>()
  private readonly pending = new Map<string, { stage: 'loaded' | 'consumed'; stage_request_id: string; proof: SkillToolOccurrence }>()
  // Immutable delivery content is not permission or a loaded receipt. The Hub
  // reauthenticates the actual recorded input before accepting the new proof.
  private readonly selected = new Map<string, SelectedSkillDelivery>()
  private readonly deliveryDecisions = new Map<string, SelectedSkillDeliveryDecision>()
  private readonly pendingSelected = new Map<string, Extract<SkillStageRecord, { proof: SelectedSkillInputProof }>>()
  constructor(private readonly hub: SkillHostHub,
    private readonly resolveIdentity: (context: SkillExecutionContext) => Promise<SkillHostIdentity>) {}

  deliveryDecision(identity: SkillHostIdentity): SelectedSkillDeliveryDecision | undefined {
    return this.deliveryDecisions.get(this.key(identity))
  }

  rememberDeliveryDecision(identity: SkillHostIdentity, value: unknown): SelectedSkillDeliveryDecision {
    const decision = parseSelectedSkillDeliveryDecision(value, identity), key = this.key(identity)
    const prior = this.deliveryDecisions.get(key)
    if (prior !== undefined && providerCanonicalJson(prior) !== providerCanonicalJson(decision)) fail('skill_selected_delivery_changed')
    if (prior === undefined && this.deliveryDecisions.size >= 16) fail('skill_session_census_limit')
    if (decision.delivery_mode !== 'legacy-tool') this.rememberSelectedDelivery(identity, decision)
    this.deliveryDecisions.set(key, decision)
    return decision
  }

  rememberSelectedDelivery(identity: SkillHostIdentity, value: unknown): SelectedSkillDelivery {
    const delivery = parseSelectedSkillDelivery(value, identity)
    const key = this.key(identity), prior = this.selected.get(key)
    if (prior !== undefined && providerCanonicalJson(prior) !== providerCanonicalJson(delivery)) fail('skill_selected_delivery_changed')
    if (prior === undefined && this.selected.size >= 16) fail('skill_session_census_limit')
    this.selected.set(key, delivery)
    return delivery
  }

  async execute(value: unknown, context: SkillExecutionContext): Promise<unknown> {
    const args = parseSkillBusinessArgs(value)
    context.signal.throwIfAborted()
    if (!context.callId || !context.agent) fail('skill_native_call_identity_required')
    const identity = await this.resolveIdentity(context)
    if (identity.executionSessionId !== identity.rootSessionId) fail('skill_root_session_required')
    let proof: SkillToolOccurrence | null = null
    if (args.operation === 'execute_step' && args.node_id === 'spot_evidence' && typeof args.input.research_call_id === 'string') {
      const prepared = this.inputs.get(this.key(identity))
      if (!prepared || typeof args.input.research_call_id !== 'string') fail('skill_gateway_original_input_required')
      proof = skillOccurrence(prepared, identity, args.input.research_call_id, 'decision_hub_research').proof
    }
    const result = await this.hub.tool(identity, context.callId, args, proof, context.signal)
    context.signal.throwIfAborted()
    if (args.operation === 'load') validateLoad(result as SkillLoadResponse, args.skill_version_id)
    return result
  }

  /** AFTER actual input recording, BEFORE model dispatch; restart reads the real transcript. */
  async afterRecordedProviderInput(prepared: Awaited<ReturnType<typeof prepareProviderInput>>, signal: AbortSignal): Promise<number> {
    const p = prepared.identity
    if (p.root_session_id !== p.execution_session_id) return 0
    const identity = { runId: p.run_id, generation: p.generation, requestHash: p.request_hash,
      rootSessionId: p.root_session_id, executionSessionId: p.execution_session_id }
    const key = this.key(identity)
    if (!this.inputs.has(key) && this.inputs.size >= 16) fail('skill_session_census_limit')
    let count = 0
    const selected = this.selected.get(key)
    if (selected !== undefined) {
      // Construct locators from this actual input even if an earlier identical
      // stage is pending. Never create loaded from a merely prepared message.
      const proof = selectedSkillProofFromProviderInput(prepared, selected)
      const originalKey = providerCanonicalJson([key, 'selected', selected.selection_ref])
      if (!this.recorded.has(originalKey)) {
        if (!this.pendingSelected.has(originalKey)) this.pendingSelected.set(originalKey, {
          stage: 'loaded', stage_request_id: selectedSkillStageRequestId(identity, proof), proof,
        })
        signal.throwIfAborted()
        await this.hub.record(identity, this.pendingSelected.get(originalKey)!, signal)
        this.pendingSelected.delete(originalKey)
        this.recorded.add(originalKey)
        count++
      }
    }
    for (const callId of completedSkillCalls(prepared)) {
      const occurrence = skillOccurrence(prepared, identity, callId, SKILL_TOOL_NAME)
      const args = parseSkillBusinessArgs(occurrence.arguments)
      const stage = args.operation === 'load' ? 'loaded'
        : args.operation === 'execute_step' && args.node_id === 'hypothesis' ? 'consumed' : null
      if (stage === null) continue
      if (stage === 'loaded' && selected !== undefined) continue
      if (stage === 'loaded') validateLoad(occurrence.result as SkillLoadResponse, (args as { skill_version_id: string }).skill_version_id)
      const originalKey = providerCanonicalJson([key, callId, stage])
      if (this.recorded.has(originalKey)) continue
      if (!this.pending.has(originalKey)) this.pending.set(originalKey, {
        stage, stage_request_id: 'skill_record_' + digest(originalKey), proof: occurrence.proof,
      })
      signal.throwIfAborted()
      await this.hub.record(identity, this.pending.get(originalKey)!, signal)
      this.pending.delete(originalKey)
      this.recorded.add(originalKey)
      count++
    }
    this.inputs.set(key, prepared)
    return count
  }
  forgetSession(runId: string): void {
    for (const [key, input] of this.inputs) if (input.identity.run_id === runId) this.inputs.delete(key)
    for (const key of this.recorded) if (this.originalRun(key) === runId) this.recorded.delete(key)
    for (const key of this.pending.keys()) if (this.originalRun(key) === runId) this.pending.delete(key)
    for (const [key, delivery] of this.selected) if (delivery.run_id === runId) this.selected.delete(key)
    for (const [key, decision] of this.deliveryDecisions) if (decision.run_id === runId) this.deliveryDecisions.delete(key)
    for (const key of this.pendingSelected.keys()) if (this.originalRun(key) === runId) this.pendingSelected.delete(key)
  }
  private originalRun(originalKey: string): string {
    const identityKey = JSON.parse(originalKey)[0]
    return JSON.parse(identityKey)[0]
  }
  private key(identity: SkillHostIdentity): string {
    return providerCanonicalJson([identity.runId, identity.generation, identity.requestHash, identity.executionSessionId])
  }
}
