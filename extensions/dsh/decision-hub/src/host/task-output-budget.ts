import type { LlmCallConfig } from '@deepseek-ai/dsh-llm'
import { synthesisProtocol } from '../synthesis-protocol.js'
import type { CancellationBinding } from './command-cancellation.js'

/** Deployment output space, not a claim about a model/account's maximum. */
export const PERPETUAL_OUTPUT_TOKEN_FLOOR = 32768
type Task = Readonly<{ run_id: string; request_id: string; output_schema_ref: string }>
type Entry = Readonly<{ binding: CancellationBinding; outputSchema: string }>

/** Select before the official SDK writes its header and provider input receipt.
 * The caller supplies only the authenticated original Task, before waking its
 * driver. Existing model dispatch continues to own live execution permission.
 */
export class TaskOutputBudgets {
  private readonly entries = new Map<string, Entry>()
  constructor(private readonly current: (binding: CancellationBinding) => boolean) {}

  remember(binding: CancellationBinding, task: Task): void {
    if (task.run_id !== binding.runId || task.request_id !== `research-request:${binding.runId}`) {
      throw new Error('output_budget_task_identity_invalid')
    }
    synthesisProtocol(task.output_schema_ref)
    const old = this.entries.get(binding.sessionId)
    if (old && (old.binding.runId !== binding.runId || old.binding.requestHash !== binding.requestHash
      || old.outputSchema !== task.output_schema_ref)) throw new Error('output_budget_task_identity_changed')
    this.entries.set(binding.sessionId, Object.freeze({ binding: Object.freeze({ ...binding }), outputSchema: task.output_schema_ref }))
  }

  activate(binding: CancellationBinding): void {
    const entry = this.entries.get(binding.sessionId)
    if (!entry || entry.binding.runId !== binding.runId || entry.binding.requestHash !== binding.requestHash) {
      throw new Error('output_budget_task_identity_invalid')
    }
    this.entries.set(binding.sessionId, Object.freeze({ ...entry, binding: Object.freeze({ ...binding }) }))
  }

  select(sessionId: string, config: LlmCallConfig): LlmCallConfig {
    const entry = this.entries.get(sessionId)
    if (!entry || !this.current(entry.binding) || synthesisProtocol(entry.outputSchema) !== 'decision_v2'
      || config.maxTokens !== undefined && config.maxTokens >= PERPETUAL_OUTPUT_TOKEN_FLOOR) return config
    return { ...config, maxTokens: PERPETUAL_OUTPUT_TOKEN_FLOOR }
  }

  forget(sessionId: string): void { this.entries.delete(sessionId) }
}
