import { describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_RESEARCH_TOOL_TIMEOUT_MS,
  RESEARCH_TOOL_NAME,
  createResearchToolDefinition,
} from '../src/research-tool.js'

const SESSION_ID = 'dsh_f66aac8b72d82bc0de3b4940290d1d2607ac8cdc2db0c8a7ebacfc7686afb615'

function args(): Record<string, unknown> {
  return {
    request_id: 'capability-request-1',
    capability_id: 'market.cross_asset',
    requirement_id: 'macro_transmission',
    query: 'Observe rates and USD transmission.',
    target_url: null,
    symbols: ['DGS2', 'DGS10', 'DTWEXBGS'],
    fields: [],
    allowed_domains: ['fred.stlouisfed.org'],
    max_results: 10,
    max_cost_usd: 0.1,
    round: 1,
    mode: 'live',
    observed_at: '2026-09-01T10:00:00Z',
    cutoff_at: '2026-09-01T10:00:00Z',
  }
}

function execution(agentId: string | null): never {
  return {
    callId: 'call-1',
    rootCallId: 'call-1',
    name: RESEARCH_TOOL_NAME,
    arguments: args(),
    signal: new AbortController().signal,
    token: Symbol('tool-execution'),
    deferContext: vi.fn(),
    concludeTurn: vi.fn(),
    ...(agentId === null ? {} : { agent: { id: agentId } }),
  } as never
}

describe('trusted Decision Hub research tool', () => {
  it('keeps wrapper timeout above the Gateway capability deadline', () => {
    expect(DEFAULT_RESEARCH_TOOL_TIMEOUT_MS).toBe(25_000)
    expect(DEFAULT_RESEARCH_TOOL_TIMEOUT_MS).toBeGreaterThan(20_000)
  })

  it('keeps Session identity out of model arguments and injects exec.agent.id', async () => {
    const requests: Array<{ headers: Headers; body: Record<string, unknown> }> = []
    const fetchImpl = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      requests.push({
        headers: new Headers(init?.headers),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      })
      return Response.json({
        schema_version: 'research-capability-result.v1',
        request_id: 'capability-request-1',
        capability_id: 'market.cross_asset',
        provider: 'fred',
        evidence_candidates: [],
        cost_usd: 0,
        completed_at: '2026-09-01T10:00:01Z',
      })
    })
    const definition = createResearchToolDefinition({
      serviceUrl: 'http://127.0.0.1:8002/decision-hub/v1/research-capabilities/execute',
      authKey: 'test-bridge-secret',
      timeoutMs: 20_000,
      fetchImpl,
    })

    expect(definition.name).toBe(RESEARCH_TOOL_NAME)
    expect(definition.parameters.properties).not.toHaveProperty('research_session_id')
    const result = await definition.execute(args(), execution(SESSION_ID))

    expect(result).toMatchObject({ schema_version: 'research-capability-result.v1' })
    expect(requests).toHaveLength(1)
    expect(requests[0]!.body).toMatchObject({
      schema_version: 'research-capability-query.v1',
      research_session_id: SESSION_ID,
    })
    expect(requests[0]!.headers.get('x-decision-hub-bridge-key')).toBe('test-bridge-secret')
  })

  it('fails closed without an Agent and rejects a model-supplied Session field', async () => {
    const fetchImpl = vi.fn()
    const definition = createResearchToolDefinition({
      serviceUrl: 'http://127.0.0.1:8002/decision-hub/v1/research-capabilities/execute',
      authKey: 'test-bridge-secret',
      timeoutMs: 20_000,
      fetchImpl,
    })

    await expect(definition.execute(args(), execution(null))).rejects.toThrow(
      'decision_hub_research_agent_required',
    )
    await expect(definition.execute(
      { ...args(), research_session_id: 'forged-session' },
      execution(SESSION_ID),
    )).rejects.toThrow()
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
