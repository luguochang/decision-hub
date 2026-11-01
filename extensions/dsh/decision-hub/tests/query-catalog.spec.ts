import { createHash } from 'node:crypto'
import { describe, expect, it, vi } from 'vitest'
import { loadResearchQueryCatalog } from '../src/query-catalog.js'
import { installScopedResearchCatalog, createResearchToolDefinition } from '../src/research-tool.js'

// Keys match the server's canonical JSON order; this also checks cross-language hashing.
const content = {
  capabilities: [{ allowed_domains: ['query1.finance.yahoo.com'], capability_id: 'market.cross_asset', modes: ['live'], routes: [{ allowed_domains: ['query1.finance.yahoo.com'], fields: ['change_5d', 'level', 'return_5d'], modes: ['live'], provider_id: 'yahoo-finance-public', requires_event_window: false, sources: null, symbols: ['DXY', 'US10Y'], symbols_required: true }] }],
  schema_version: 'research-query-catalog.v1',
}
const payload = { ...content, catalog_hash: createHash('sha256').update(JSON.stringify(content)).digest('hex') }
const config = { serviceUrl: 'http://127.0.0.1:8037/decision-hub/v1/research-capabilities/execute', authKey: 'test-only' }

describe('Gateway query catalog shared by root and delegated research tools', () => {
  it('authenticates a same-service read and puts exact route fields into every definition', async () => {
    const fetchImpl = vi.fn(async () => Response.json(payload))
    const catalog = await loadResearchQueryCatalog({ ...config, fetchImpl })
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [URL, RequestInit]
    expect(String(url)).toBe('http://127.0.0.1:8037/decision-hub/v1/research-capabilities/catalog')
    expect(new Headers(init.headers).get('x-decision-hub-bridge-key')).toBe('test-only')
    expect(init.redirect).toBe('error')
    for (let i = 0; i < 2; i++) {
      const tool = createResearchToolDefinition({ ...config, timeoutMs: 65000, queryCatalog: catalog })
      expect(tool.description).toContain('query1.finance.yahoo.com')
      expect(tool.description).toContain('change_5d')
      expect(tool.description).toContain(catalog.catalog_hash)
      expect(tool.description).not.toContain('test-only')
    }
  })

  it('rejects missing or corrupted catalogs instead of substituting guessed fields', async () => {
    await expect(loadResearchQueryCatalog({ ...config, fetchImpl: async () => new Response('', { status: 503 }) })).rejects.toThrow('research_catalog_http_503')
    await expect(loadResearchQueryCatalog({ ...config, fetchImpl: async () => Response.json({ ...payload, catalog_hash: '0'.repeat(64) }) })).rejects.toThrow('research_catalog_hash_mismatch')
    await expect(loadResearchQueryCatalog({ ...config, fetchImpl: async () => Response.json({ ...payload, capabilities: [] }) })).rejects.toThrow('research_catalog_hash_mismatch')
  })

  it('records the actual bounded fetch deadline in its native timeout marker', async () => {
    vi.useFakeTimers()
    try {
      const pending = loadResearchQueryCatalog({
        ...config,
        catalogTimeoutMs: 123,
        fetchImpl: async () => await new Promise<Response>(() => undefined),
      })
      const rejected = expect(pending).rejects.toThrow(
        'research_catalog_transport_timeout phase=fetch deadline_ms=123',
      )
      await vi.advanceTimersByTimeAsync(123)
      await rejected
    } finally {
      vi.useRealTimers()
    }
  })

})

function scopedPayload(status = 'bound', sessionId = 'root-session') {
  const value = { ...content, ...(status === 'legacy_unbound' ? { capabilities: [] } : {}), scope: {
    dsh_session_id: sessionId, execution_mode: status === 'bound' ? 'live' : null,
    generation: 1, method_hash: status === 'bound' ? 'b'.repeat(64) : null,
    deadline_at: status === 'bound' ? new Date(Date.now() + 600_000).toISOString() : null,
    request_hash: 'a'.repeat(64), run_id: 'run-scoped', status,
  } }
  function canonical(item: unknown): string {
    if (Array.isArray(item)) return `[${item.map(canonical).join(',')}]`
    if (item !== null && typeof item === 'object') return `{${Object.entries(item).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`
    return JSON.stringify(item)
  }
  return { ...value, catalog_hash: createHash('sha256').update(canonical(value)).digest('hex') }
}

it('never accepts a global or another session catalog for a scoped request', async () => {
  for (const body of [payload, scopedPayload('bound', 'wrong-session')]) {
    await expect(loadResearchQueryCatalog({ ...config, sessionId: 'root-session', fetchImpl: async () => Response.json(body) })).rejects.toThrow('research_catalog_scope_identity_mismatch')
  }
})


function sharedPreset(fetchImpl: typeof fetch) {
  const hooks = new Map<string, Function>()
  const register = vi.fn((_definition: ReturnType<typeof createResearchToolDefinition>) => vi.fn())
  const ctx = { tools: { register }, on: (name: string, fn: Function) => hooks.set(name, fn) }
  installScopedResearchCatalog(ctx as Parameters<typeof installScopedResearchCatalog>[0], { ...config, timeoutMs: 65000, fetchImpl })
  return { register, async assemble(agent: unknown, visible = true) {
    const original = { tools: visible ? [{ name: 'decision_hub_research', description: 'generic', parameters: {} }] : [], sections: ['preserved'] }
    const result = await hooks.get('system-prompt/assemble')!(original, { agent }, async () => original)
    expect(original.tools[0]?.description).toBe(visible ? 'generic' : undefined)
    expect(result.sections).toEqual(['preserved'])
    return result
  } }
}
interface TestAgent { id: string; session: { header: { parentSession?: string } }; ctx: { agents: { get: () => TestAgent | undefined } } }
function agent(id: string, parent?: TestAgent): TestAgent {
  return { id, session: { header: parent ? { parentSession: parent.id } : {} }, ctx: {
    agents: { get: () => parent },
  } }
}

it('narrows the FIRST assembled request and caches separately for each agent', async () => {
  const fetchImpl = vi.fn(async (url: any) => Response.json(scopedPayload('bound', new URL(url).searchParams.get('research_session_id')!)))
  const preset = sharedPreset(fetchImpl)
  expect(preset.register.mock.calls[0]![0].description).not.toContain('catalog_hash')
  const root = agent('root-session'), other = agent('other-session'), child = agent('child-session', root)
  const first = await preset.assemble(root)
  expect(first.tools[0].description).toContain('root-session')
  for (const field of ['mode', 'round', 'observed_at', 'cutoff_at']) expect(first.tools[0].parameters.properties).not.toHaveProperty(field)
  expect((await preset.assemble(other)).tools[0].description).not.toContain('root-session')
  const definition = (await preset.assemble(child)).tools[0]
  expect(definition.description).toContain('root-session')
  expect(definition.description).not.toContain('official.macro')
  expect(definition.parameters.properties.capability_id.enum).toEqual(['market.cross_asset'])
  expect(definition.description).toContain('symbols_required')
  expect(definition.description).toContain('US10Y')
  expect(definition.parameters.properties).toHaveProperty('evidence_ref')
  expect(definition.parameters.properties).toHaveProperty('content_ref')
  expect((await preset.assemble(root)).tools).toEqual(first.tools)
  expect(fetchImpl).toHaveBeenCalledTimes(3)
  expect(preset.register).toHaveBeenCalledTimes(1)
})

it('does not give a research tool back to a reviewer or fetch for diagnostic previews', async () => {
  const fetchImpl = vi.fn()
  const preset = sharedPreset(fetchImpl)
  expect((await preset.assemble(agent('reviewer', agent('root-session')), false)).tools).toEqual([])
  expect((await preset.assemble(undefined)).tools[0].description).toBe('generic')
  expect(fetchImpl).not.toHaveBeenCalled()
})

it('fails assembly before a model request on scope lookup failure or legacy scope', async () => {
  for (const response of [new Response('', { status: 409 }), Response.json(scopedPayload('legacy_unbound'))]) {
    const preset = sharedPreset(async () => response)
    await expect(preset.assemble(agent('root-session'))).rejects.toThrow(/research_catalog_/)
  }
})


it('managed root and child execution never forwards model time controls', async () => {
  const posted: { url: string; body: Record<string, unknown> }[] = []
  const fetchImpl = vi.fn(async (input: any, init?: RequestInit) => {
    const url = new URL(input)
    if (!init?.body) return Response.json(scopedPayload('bound', url.searchParams.get('research_session_id')!))
    const body = JSON.parse(String(init.body)); posted.push({ url: url.pathname, body })
    return Response.json({ schema_version: 'research-capability-result.v1', request_id: body.request_id,
      capability_id: body.capability_id, provider: 'test-only', evidence_candidates: [], cost_usd: null, completed_at: '2026-10-01T00:00:00Z' })
  })
  const preset = sharedPreset(fetchImpl)
  const root = agent('root-session'), child = agent('child-session', root)
  for (const current of [root, child]) {
    await preset.assemble(current)
    const executor = preset.register.mock.calls[0]![0]
    for (const field of ['mode', 'round', 'observed_at', 'cutoff_at']) expect(executor.parameters.required ?? []).not.toContain(field)
    await executor.execute({ capability_id: 'market.cross_asset', requirement_id: 'macro', query: 'USD', target_url: null,
      symbols: ['DXY'], fields: ['level'], allowed_domains: [], max_results: 1, max_cost_usd: null,
      ...(current === root ? {} : { mode: 'replay', round: 999, cutoff_at: '1900-01-01T00:00:00Z', observed_at: '1900-01-01T00:00:00Z', deadline_at: '2999-01-01T00:00:00Z' }),
    }, { agent: current, callId: current.id + '-call', signal: new AbortController().signal } as never)
  }
  expect(posted).toHaveLength(2)
  for (const post of posted) {
    expect(post.url).toBe('/decision-hub/v1/research-capabilities/invoke-live')
    expect(post.body).toMatchObject({ schema_version: 'research-capability-invocation.v1', research_session_id: 'root-session' })
    for (const field of ['mode', 'round', 'observed_at', 'cutoff_at', 'deadline_at']) expect(post.body).not.toHaveProperty(field)
  }
})
