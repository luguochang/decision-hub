/** Root GenerateOptions consumption only. No child enforcement/deploy qualification. */
import { createHash } from 'node:crypto'
import { z } from 'zod'
import type { DshModelDispatchBinding } from '@decision-hub/contracts-ts'
import type { ModelDispatchOptions } from './dsh-public.js'
import { HubClientError, type HubClient } from './hub-client.js'

const hash = z.string().regex(/^[a-f0-9]{64}$/)
const id = z.string().min(1).max(256)
export const hostScopeSchema = z.object({
  host_instance_id: z.string().uuid(), process_id: z.number().int().positive(),
  started_at: z.string().datetime({ offset: true }), hub_base_url: z.string().min(1).max(2048),
  workspace_id: id, workspace_cwd_hash: hash,
}).strict()
export type HostScope = z.infer<typeof hostScopeSchema>
const methodSchema = z.object({
  schema_version: z.literal('research-harness-execution-method.v4'), run_id: id, event_id: id,
  method_v3_payload_hash: hash, consumption_payload_hash: hash, artifact_bytes_sha256: hash,
  pack_definition_hash: hash, full_task_hash: hash, compiled_policy_hash: hash,
  plugin_build_hash: hash, proof_scope: z.literal('root_model_request_consumption'),
  native_execution_proof: z.literal('unverified'), eligible_for_deployment: z.literal(false),
  recorded_at: z.string().datetime({ offset: true }),
}).strict()
export const witnessSummarySchema = z.object({ payload_hash: hash, host_scope: hostScopeSchema }).strict()
export const harnessExecutionViewSchema = z.object({
  schema_version: z.literal('research-harness-execution-view.v1'), method: methodSchema,
  method_payload_hash: hash, run_id: id, dsh_session_id: id, request_id: id,
  request_hash: hash, generation: z.number().int().positive(), plugin_build_hash: hash,
  prompt: z.string(), prompt_hash: hash, witness: witnessSummarySchema.nullable(),
}).strict()
export const rootConsumptionSchema = z.object({
  schema_version: z.literal('research-harness-root-consumption.v1'), run_id: id,
  dsh_session_id: id, request_id: id, request_hash: hash, generation: z.number().int().positive(),
  plugin_build_hash: hash, method_v4_payload_hash: hash, prompt_hash: hash,
  host_scope: hostScopeSchema, execution_session_id: id,
  model_input_json: z.string().min(1).max(1_000_000), model_input_sha256: hash,
  proof_scope: z.literal('root_model_request_consumption'), eligible_for_deployment: z.literal(false),
}).strict()
export type RootConsumption = z.infer<typeof rootConsumptionSchema>
const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex')
function sameScope(a: HostScope, b: HostScope): boolean {
  return a.host_instance_id === b.host_instance_id && a.process_id === b.process_id
    && Date.parse(a.started_at) === Date.parse(b.started_at) && a.hub_base_url === b.hub_base_url
    && a.workspace_id === b.workspace_id && a.workspace_cwd_hash === b.workspace_cwd_hash
}

export function actualRootConsumption(
  view: z.infer<typeof harnessExecutionViewSchema>, options: ModelDispatchOptions,
  scope: HostScope, pluginHash: string,
): RootConsumption {
  if (options.sessionId !== view.dsh_session_id || pluginHash !== view.plugin_build_hash
    || view.method.plugin_build_hash !== pluginHash || sha(view.prompt) !== view.prompt_hash) {
    throw new HubClientError('harness_root_witness_identity_mismatch', false)
  }
  const matches = options.messages?.filter(m => m.role === 'user' && m.source.kind === 'user'
    && m.content.some(b => b.type === 'text' && b.text === view.prompt)) ?? []
  if (matches.length !== 1) throw new HubClientError('harness_full_prompt_not_consumed', false)
  const raw = JSON.stringify({ messages: options.messages, system: options.system ?? null, tools: options.tools ?? [] })
  if (Buffer.byteLength(raw, 'utf8') > 1_000_000) throw new HubClientError('harness_model_input_oversize', false)
  return rootConsumptionSchema.parse({
    schema_version: 'research-harness-root-consumption.v1', run_id: view.run_id,
    dsh_session_id: view.dsh_session_id, request_id: view.request_id,
    request_hash: view.request_hash, generation: view.generation, plugin_build_hash: pluginHash,
    method_v4_payload_hash: view.method_payload_hash, prompt_hash: view.prompt_hash,
    host_scope: scope, execution_session_id: options.sessionId,
    model_input_json: raw, model_input_sha256: sha(raw),
    proof_scope: 'root_model_request_consumption', eligible_for_deployment: false,
  })
}

export async function consumeHarnessModel(
  hub: Pick<HubClient, 'harnessExecution' | 'recordHarnessRoot' | 'bindModel' | 'modelBindingBudgetMs'>,
  binding: DshModelDispatchBinding, options: ModelDispatchOptions, scope: HostScope, pluginHash: string,
  originalDeadlineAt?: string,
): Promise<void> {
  // Live preparation owns the original Task deadline; standalone callers keep
  // the existing <=30s ABI. Neither a proof nor binding refills this lifetime.
  const budget = originalDeadlineAt === undefined ? hub.modelBindingBudgetMs()
    : Date.parse(originalDeadlineAt) - Date.now()
  if (!Number.isFinite(budget) || budget <= 0 || budget > 2_147_483_647) {
    throw new HubClientError('dsh_model_binding_budget_elapsed', false)
  }
  const began = performance.now()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), budget)
  const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal
  try {
    const view = await hub.harnessExecution(binding.run_id, binding.generation, scope, signal, originalDeadlineAt)
    signal.throwIfAborted()
    if (view.run_id !== binding.run_id || view.dsh_session_id !== binding.dsh_session_id
      || view.request_hash !== binding.request_hash || view.generation !== binding.generation
      || view.plugin_build_hash !== binding.plugin_build_hash || pluginHash !== binding.plugin_build_hash) {
      throw new HubClientError('harness_root_witness_identity_mismatch', false)
    }
    let witness = view.witness
    if (witness !== null && !sameScope(witness.host_scope, scope)) {
      throw new HubClientError('harness_host_scope_mismatch', false)
    }
    if (witness === null) {
      // Children never mint a root witness, regardless of self-declared prompt contents.
      witness = await hub.recordHarnessRoot(actualRootConsumption(view, options, scope, pluginHash), signal, originalDeadlineAt)
      if (!sameScope(witness.host_scope, scope)) {
        throw new HubClientError('harness_host_scope_mismatch', false)
      }
    }
    const remaining = Math.floor(budget - (performance.now() - began))
    if (remaining < (originalDeadlineAt === undefined ? 1_000 : 1)) throw new HubClientError('dsh_model_binding_budget_elapsed', false)
    await hub.bindModel(binding, signal, { witnessHash: witness.payload_hash, budgetMs: remaining }, originalDeadlineAt)
    signal.throwIfAborted()
  } catch (error) {
    if (options.signal?.aborted) throw new HubClientError('dsh_model_binding_cancelled', false)
    if (controller.signal.aborted) throw new HubClientError('dsh_model_binding_budget_elapsed', false)
    throw error
  } finally { clearTimeout(timer) }
}
