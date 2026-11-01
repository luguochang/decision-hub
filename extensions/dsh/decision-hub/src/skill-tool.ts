import { defineTool, parameterSchemaSpecToJsonSchema, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { providerCanonicalJson } from './host/provider-input.js'
import { SKILL_NODE_IDS, SKILL_NODE_INPUTS, SKILL_OPERATION_DESCRIPTION, type SkillHostCoordinator } from './host/skill-host.js'
import { projectCanonicalToolValueSchema } from './synthesis-tool-schema.js'

export type SkillToolTimePolicy = 'legacy-sync' | 'managed-durable'

export function createSkillToolDefinition(
  coordinator: SkillHostCoordinator, timePolicy: SkillToolTimePolicy = 'legacy-sync',
): ToolDefinition {
  if (timePolicy !== 'legacy-sync' && timePolicy !== 'managed-durable') throw new Error('skill_tool_time_policy_invalid')
  const definition = defineTool({
    name: 'decision_hub_skill',
    description: 'Use the exact immutable method selected for this Run. '
      + 'When the authenticated decision-hub-selected-skill context supplies selected_package_provider_input.v1, '
      + 'the complete selected method and compiled next_tool_call are already delivered: proceed directly to that research/workflow step; do not call list/load only to discover that same version. '
      + 'Otherwise keep the legacy protocol: First list, then load its version. '
      + 'The next real model input authenticates loading. Follow that full method, using ordinary decision_hub_research while acquisition is open. Only a frozen same_version_original_read.v1 repair policy replaces acquisition with the server-selected original; it does not refresh market time. '
      + 'execute_step spot_evidence binds the actual research_call_id; features takes empty input; hypothesis takes typed output. '
      + 'After hypothesis delivery, call list on the next turn to obtain its real consumed receipt before final synthesis. '
      + 'Never invent a receipt, execution identity, qualification or market data. An unavailable method grants no permission. '
      + SKILL_OPERATION_DESCRIPTION
      + ' Every execute_step requires the SAME skill_version_id from list/load; it is never implicit. '
      + 'spot_evidence input is exactly {"research_call_id":"<actual completed decision_hub_research call ID>"}, or only under the frozen late repair policy {"source":"frozen_original"}; never combine the two. features input is exactly {}; '
      + 'hypothesis input is exactly {"output":<complete research-skill-hypothesis-output.v1 object below>}. '
      + 'For used_outputs copy the exact node_output objects from successful spot_evidence/features results, not guessed hashes; consumer_json_pointer must locate their real use in mechanism_review. Correct field errors and retry the intended step; do not skip it or invent receipts.',
    // Preserve raw JSON for the shared parser's precise field diagnostics.
    // The model sees the full schema below; no omitted identity is supplied.
    parameters: { operation: { type: 'json' }, skill_version_id: { type: 'json' }, node_id: { type: 'json' }, input: { type: 'json' } },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: providerCanonicalJson(value) }] },
    finalizeContent(_exec, result) {
      if (result.isError) return undefined
      try {
        const text = providerCanonicalJson(result.value)
        return Buffer.byteLength(text, 'utf8') <= 1_000_000 ? [{ type: 'text', text }] : undefined
      } catch { return undefined }
    },
    // Legacy synchronous callbacks retain their SDK budget. Durable callbacks
    // already freeze the authenticated Task deadline in HubClient and obey the
    // parent signal/Host step watchdog; an extra 65s SDK timer would discard a
    // legal in-flight job's observer. The actual SDK has no default when omitted.
    ...(timePolicy === 'legacy-sync' ? { timeoutMs: 65_000 } : {}),
    async execute(args, exec) {
      if (!exec.agent || !exec.callId) throw new Error('skill_native_call_identity_required')
      return await coordinator.execute(args, { agent: exec.agent, callId: String(exec.callId), signal: exec.signal }) as JsonValue
    },
  })
  return { ...definition, parameters: { ...parameterSchemaSpecToJsonSchema({
      operation: { type: 'string', enum: ['list', 'load', 'execute_step'], required: true },
      skill_version_id: { type: 'string', description: 'Required for load and execute_step; 1..128 characters. Copy the exact version returned by list/load; never omit or infer it.' },
      node_id: { type: 'string', enum: SKILL_NODE_IDS, description: 'Required for execute_step. Run spot_evidence, then features, then hypothesis in dependency order.' },
      input: { oneOf: [projectCanonicalToolValueSchema(SKILL_NODE_INPUTS.spot_evidence, true), projectCanonicalToolValueSchema(SKILL_NODE_INPUTS.features, true), projectCanonicalToolValueSchema(SKILL_NODE_INPUTS.hypothesis, true)],
        description: 'Required for execute_step. Match input to node_id: spot_evidence uses research_call_id for a current Gateway result, or exactly source:"frozen_original" only under the explicit frozen late policy; features uses {}; hypothesis uses only output with ALL nested required fields. Exact source references are authenticated by Hub.' },
    } as const) } }
}
