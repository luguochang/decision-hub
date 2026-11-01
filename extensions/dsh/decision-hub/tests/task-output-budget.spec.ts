import { describe, expect, it } from 'vitest'
import { TaskOutputBudgets, PERPETUAL_OUTPUT_TOKEN_FLOOR } from '../src/host/task-output-budget.js'

const binding = { runId: 'run', sessionId: 'root', requestId: 'request', requestHash: 'a'.repeat(64), generation: 1 }
const task = { run_id: 'run', request_id: 'research-request:run', output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2' }

describe('frozen Task conversation output budget', () => {
  it('selects only the authenticated V2 root and keeps higher explicit values', () => {
    const budgets = new TaskOutputBudgets(() => true)
    budgets.remember(binding, task)
    const ordinary = { provider: 'codexai', model: 'gpt-6.1-sol', maxTokens: 8192 }
    expect(budgets.select('root', ordinary).maxTokens).toBe(PERPETUAL_OUTPUT_TOKEN_FLOOR)
    expect(budgets.select('child', ordinary)).toBe(ordinary)
    expect(budgets.select('root', { ...ordinary, maxTokens: 65536 }).maxTokens).toBe(65536)
    budgets.forget('root')
    expect(budgets.select('root', ordinary)).toBe(ordinary)
  })

  it('does not alter legacy, failed fences, or substitute a frozen identity', () => {
    const budgets = new TaskOutputBudgets(() => false)
    budgets.remember(binding, task)
    const config = { provider: 'codexai', model: 'gpt-6.1-sol', maxTokens: 8192 }
    expect(budgets.select('root', config)).toBe(config)
    expect(() => budgets.remember(binding, { ...task, run_id: 'foreign' })).toThrow('output_budget_task_identity_invalid')
    expect(() => budgets.remember({ ...binding, requestHash: 'b'.repeat(64) }, task)).toThrow('output_budget_task_identity_changed')
    const legacy = new TaskOutputBudgets(() => true)
    legacy.remember(binding, { ...task, output_schema_ref: 'agentic_research.schema.yaml#/$defs/research_session_result' })
    expect(legacy.select('root', config)).toBe(config)
  })
})
