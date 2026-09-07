import { describe, expect, it } from 'vitest'
import {
  SYNTHESIS_TOOL_NAME,
  createSynthesisToolDefinition,
} from '../src/synthesis-tool.js'

function candidate(): Record<string, unknown> {
  return {
    schema_version: 'research-synthesis-candidate.v1',
    request_id: 'research-request:run-1',
    causal_case: null,
    horizons: [],
  }
}

function execution(hasAgent = true): never {
  return {
    callId: 'capture-1',
    rootCallId: 'capture-1',
    name: SYNTHESIS_TOOL_NAME,
    arguments: { candidate: candidate() },
    signal: new AbortController().signal,
    token: Symbol('tool-execution'),
    deferContext() {},
    concludeTurn() {},
    ...(hasAgent ? { agent: { id: 'dsh-session-1' } } : {}),
  } as never
}

describe('Decision Hub synthesis capture tool', () => {
  it('returns only the codegen-validated canonical synthesis object', async () => {
    const definition = createSynthesisToolDefinition()

    expect(definition.name).toBe(SYNTHESIS_TOOL_NAME)
    await expect(definition.execute({ candidate: candidate() }, execution())).resolves.toEqual(
      candidate(),
    )
  })

  it('fails closed for schema drift and calls outside an Agent session', async () => {
    const definition = createSynthesisToolDefinition()

    await expect(definition.execute(
      { candidate: { ...candidate(), extra: true } },
      execution(),
    )).rejects.toThrow()
    await expect(definition.execute(
      { candidate: candidate() },
      execution(false),
    )).rejects.toThrow('decision_hub_synthesis_agent_required')
  })
})
