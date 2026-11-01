// Real official SDK loop/registry/compaction; local scripted adapters only, no Provider or production DB.
import { createRequire } from 'node:module'
import { createServer, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { afterEach, expect, it, vi } from 'vitest'
import { createResearchToolDefinition } from '../src/research-tool.js'
import { loadResearchQueryCatalog } from '../src/query-catalog.js'
import { makeClaimedRootContextMessage, makeRetainedRootContextMessage, verifyRetainedRootContext } from '../src/host/retained-context.js'
import { authenticatedFixture, catalog, researchArgs } from './research-managed-fixture.js'

const sdkRoot = process.env.DSH_UPSTREAM_SOURCE_ROOT ?? resolve(import.meta.dirname, '../../../../.cache/dsh-upstream/source')
const sdkRequire = createRequire(resolve(sdkRoot, 'packages/compaction/compaction-basic/package.json'))
const sdk = async (name: string) => await import(/* @vite-ignore */ pathToFileURL(sdkRequire.resolve(name)).href)
const [{ Context }, { default: AgentLoop }, { mountAgentLoopTestDependencies }, { default: SessionProjection },
  { default: TokenMeter }, { BasicCompactionEngine }, { LlmAdapter, ToolCallId, createUserMessage }, timeoutPolicy] = await Promise.all([
  sdk('@deepseek-ai/cordis'), sdk('@deepseek-ai/dsh-agent-loop'), sdk('@deepseek-ai/dsh-agent-loop-testkit'),
  sdk('@deepseek-ai/dsh-session-projection'), sdk('@deepseek-ai/dsh-token-meter'), sdk('@deepseek-ai/dsh-compaction-basic'),
  sdk('@deepseek-ai/dsh-llm'), import(/* @vite-ignore */ pathToFileURL(resolve(sdkRoot, 'packages/guard/timeout-policy/lib/index.js')).href),
])
afterEach(() => vi.useRealTimers())
const pause = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

async function realLoop(errorControl = false) {
  const f = await authenticatedFixture(), ctx = new Context()
  await mountAgentLoopTestDependencies(ctx)
  await ctx.plugin(SessionProjection)
  await ctx.plugin(AgentLoop, { agents: [] })
  await ctx.plugin(TokenMeter)
  await ctx.plugin(timeoutPolicy)
  const requests: any[] = [], guards: any[] = []
  class ScriptedAdapter extends LlmAdapter {
    async resolveModel(provider: string, model: string) { return { provider, id: model, name: model, context: { contextWindow: 100_000 } } }
    async * stream(options: any) {
      requests.push(options)
      if (requests.length === 1) {
        yield { type: 'block-start', index: 0, blockType: 'tool-call' }
        yield { type: 'block-end', index: 0, block: { type: 'tool-call', id: ToolCallId('native-spot-control'),
          name: 'decision_hub_research', arguments: JSON.stringify(researchArgs) } }
        yield { type: 'finish', reason: { kind: 'tool-calls' } }
      } else {
        yield { type: 'block-start', index: 0, blockType: 'text' }
        yield { type: 'block-end', index: 0, block: { type: 'text', text: 'Synthetic second request reached.' } }
        yield { type: 'finish', reason: { kind: 'stop' } }
      }
    }
  }
  let summaries = 0
  class ScriptedCompaction extends BasicCompactionEngine {
    constructor(ctx: import('@deepseek-ai/cordis').Context, config: { auto: boolean }) {
      super(ctx, config)
    }
    async summarize() {
      await pause(45_000)
      summaries++
      return { summary: [{ type: 'text', text: summaries === 1 ? 'Checkpoint detail. '.repeat(80) : 'Checkpoint.' }], provider: 'mock', model: 'mock' }
    }
  }
  // Drive two real SDK compactions at its pre-step seam; no pressure/model-window policy is under test.
  const compact = new ScriptedCompaction(ctx, { auto: false })
  ctx.llm.registerAdapter(['mock'], new ScriptedAdapter())
  const queryCatalog = catalog(f.binding.sessionId, f.request.deadline_at)
  const definition = createResearchToolDefinition({ serviceUrl: 'http://fixture.invalid/execute', authKey: 'synthetic',
    timeoutMs: 65_000, queryCatalog, catalogForAgent: async () => queryCatalog,
    fetchImpl: async (_url, init) => await new Promise<Response>((resolve, reject) => {
      const call = JSON.parse(String(init?.body)).request_id
      // The durable server can finish after the native HTTP observer has aborted.
      setTimeout(() => {
        const result = f.spot(call)
        f.admittedEvidence.push(...result.evidence_candidates)
        resolve(Response.json(result))
      }, 138_000)
      init?.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
    }) })
  ctx.tools.register(errorControl ? { ...definition, timeoutMs: 65_000 } : definition)
  ctx.on('agent/pre-step', async ({ agent, step, signal }: any, next: any) => {
    const decision = await next()
    if (step === 2) {
      for (let n = 0; n < 2; n++) {
        const nodes = agent.session.surface.nodes
        await compact.compactRegion(nodes[0], nodes.at(-1), agent, signal)
      }
    }
    const context = f.context(agent.session.events)
    const message = step === 1 ? makeClaimedRootContextMessage(context, f.buildHash)
      : makeRetainedRootContextMessage(context, f.buildHash)
    return { ...decision, messages: [...decision.messages, message] }
  })
  ctx.on('llm/stream', (options: any, next: any) => {
    const agent = ctx.agents.get(f.binding.sessionId)
    verifyRetainedRootContext(options, f.dispatch, f.link, f.context(agent.session.events))
    guards.push(options.messages)
    return next()
  })
  const agent = ctx.agentLoop.create(f.binding.sessionId, { provider: 'mock', model: 'mock' })
  const idle = new Promise<void>(resolve => {
    const off = ctx.on('agent/status', ({ agent: current, status }: any) => {
      if (current === agent && status === 'idle') { off(); resolve() }
    })
  })
  agent.followup(createUserMessage({ source: { kind: 'user', rpcId: f.binding.requestId }, content: [{ type: 'text', text: 'Synthetic authenticated canonical control prompt.' }] }))
  await vi.advanceTimersByTimeAsync(400_000)
  await idle
  return { f, ctx, agent, requests, guards, summaries }
}

it('retains the real 138s native result across two SDK compactions into the second guarded model input', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const r = await realLoop()
  try {
    expect(r.summaries, JSON.stringify(r.agent.session.events.at(-1))).toBe(2)
    expect(r.agent.session.events.filter((event: any) => event.type === 'compaction/end')).toHaveLength(2)
    const nativeResult = r.agent.session.events.find((event: any) => event.type === 'tool/result')
    expect(nativeResult.data.error).toBeUndefined()
    expect(r.requests).toHaveLength(2)
    expect(r.guards).toHaveLength(2)
    const second = r.requests[1].messages.find((message: any) => message.source?.plugin === 'decision-hub-protected-context')
    expect(JSON.parse(second.content[1].text).evidence_candidates).toContainEqual(expect.objectContaining({
      evidence_id: 'late-spot-evidence-control', content_hash: 'e'.repeat(64), tool_call_id: 'native-spot-control',
    }))
    expect(nativeResult.data.message.content[0].isError).toBe(false)
  } finally { await r.ctx.fiber.dispose() }
}, 15_000)

const directSession = 'direct-managed-session'
function directExecution(signal = new AbortController().signal) {
  return { callId: 'direct-call', agent: { id: directSession }, signal } as any
}
function directDefinition(extra: any) {
  return createResearchToolDefinition({ serviceUrl: 'http://fixture.invalid/execute', authKey: 'synthetic',
    timeoutMs: 65_000, ...extra })
}
const resultResponse = () => Response.json({ schema_version: 'research-capability-result.v1',
  request_id: 'direct-call', capability_id: 'spot_market_facts', provider: 'synthetic', evidence_candidates: [],
  facts: [], cost_usd: 0, completed_at: new Date().toISOString() })

it('authenticates the immutable scoped deadline as part of the existing catalog hash', async () => {
  const original = catalog(directSession, new Date(Date.now() + 600_000).toISOString())
  const options = { serviceUrl: 'http://fixture.invalid/execute', authKey: 'synthetic', sessionId: directSession }
  expect((await loadResearchQueryCatalog({ ...options, fetchImpl: async () => Response.json(original) })).scope)
    .toMatchObject({ deadline_at: original.scope.deadline_at })
  await expect(loadResearchQueryCatalog({ ...options, fetchImpl: async () => Response.json({ ...original,
    scope: { ...original.scope, deadline_at: new Date(Date.now() + 900_000).toISOString() } }) }))
    .rejects.toThrow('research_catalog_hash_mismatch')
})

it('starts a known absolute Task deadline before awaiting the catalog and never posts after expiry', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const queryCatalog = catalog(directSession, new Date(Date.now() + 15_000).toISOString())
  let posts = 0
  const pending = directDefinition({ queryCatalog, catalogForAgent: async () => { await pause(20_000); return queryCatalog },
    fetchImpl: async () => { posts++; return resultResponse() } }).execute(researchArgs, directExecution())
  const rejected = expect(pending).rejects.toThrow('decision_hub_research_task_deadline_elapsed')
  await vi.advanceTimersByTimeAsync(15_000)
  await rejected
  expect(posts).toBe(0)
})

it('allows an uncached authenticated catalog read and keeps its original deadline across the full response body', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const queryCatalog = catalog(directSession, new Date(Date.now() + 70_000).toISOString())
  let posts = 0, bodyFinished = false
  const pending = directDefinition({ catalogForAgent: async () => { await pause(20_000); return queryCatalog },
    fetchImpl: async () => {
      posts++
      const response = resultResponse()
      return { ok: true, status: 200, async text() { await pause(60_000); bodyFinished = true; return await response.text() },
        async json() { await pause(60_000); bodyFinished = true; return await response.json() } } as Response
    } }).execute(researchArgs, directExecution())
  const rejected = expect(pending).rejects.toThrow('decision_hub_research_task_deadline_elapsed')
  await vi.advanceTimersByTimeAsync(70_000)
  await rejected
  expect(posts).toBe(1)
  expect(bodyFinished).toBe(false)
})

it('rejects a known expired Task before catalog work or a capability POST', async () => {
  let catalogs = 0, posts = 0
  const queryCatalog = catalog(directSession, new Date(Date.now() - 1).toISOString())
  await expect(directDefinition({ queryCatalog, catalogForAgent: async () => { catalogs++; return queryCatalog },
    fetchImpl: async () => { posts++; return resultResponse() } }).execute(researchArgs, directExecution()))
    .rejects.toThrow('decision_hub_research_task_deadline_elapsed')
  expect({ catalogs, posts }).toEqual({ catalogs: 0, posts: 0 })
})

it('honors parent cancellation while observing a managed capability response', async () => {
  const controller = new AbortController(), queryCatalog = catalog(directSession, new Date(Date.now() + 600_000).toISOString())
  let dispatched!: () => void
  const started = new Promise<void>(resolve => { dispatched = resolve })
  const pending = directDefinition({ queryCatalog, fetchImpl: async (_url: any, init: any) => {
    dispatched()
    return await new Promise<Response>((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason)))
  } }).execute(researchArgs, directExecution(controller.signal))
  const rejected = expect(pending).rejects.toThrow('decision_hub_research_cancelled')
  await started
  controller.abort()
  await rejected
})

it('keeps replay observation at 65s even in a managed-capable shared registration', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const queryCatalog = catalog(directSession, new Date(Date.now() + 600_000).toISOString(), 'replay')
  const definition = directDefinition({ queryCatalog, catalogForAgent: async () => queryCatalog,
    fetchImpl: async (_url: any, init: any) => await new Promise<Response>((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
    }) })
  const pending = definition.execute({ ...researchArgs, mode: 'replay', round: 1,
    observed_at: new Date().toISOString(), cutoff_at: new Date().toISOString() }, directExecution())
  const rejected = expect(pending).rejects.toThrow('decision_hub_research_transport_timeout')
  await vi.advanceTimersByTimeAsync(65_000)
  await rejected
})

it('observes the production local HTTP response beyond 300s without a hidden headers deadline', async () => {
  let received!: () => void, response!: ServerResponse
  const arrived = new Promise<void>(resolve => { received = resolve })
  const server = createServer((incoming, outgoing) => {
    expect(incoming.url).toBe('/invoke-live')
    expect(incoming.headers['x-decision-hub-bridge-key']).toBe('synthetic')
    response = outgoing
    incoming.resume(); incoming.once('end', received)
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    const queryCatalog = catalog(directSession, new Date(Date.now() + 600_000).toISOString())
    const definition = directDefinition({ queryCatalog,
      serviceUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}/execute` })
    let settled = false
    const pending = definition.execute(researchArgs, directExecution()).finally(() => { settled = true })
    await arrived
    await vi.advanceTimersByTimeAsync(310_000)
    expect(settled).toBe(false)
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end(await resultResponse().text())
    await expect(pending).resolves.toMatchObject({ request_id: 'direct-call', capability_id: 'spot_market_facts' })
    expect(vi.getTimerCount()).toBe(0)
  } finally {
    server.closeAllConnections()
    await new Promise<void>(resolve => server.close(() => resolve()))
  }
})

it('still rejects a late durable accepted result whose real native result is TOOL_TIMEOUT', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const r = await realLoop(true)
  try {
    expect(r.summaries, JSON.stringify(r.agent.session.events.at(-1))).toBe(2)
    expect(r.f.admittedEvidence.some(item => item.evidence_id === 'late-spot-evidence-control')).toBe(true)
    expect(r.requests).toHaveLength(1)
    expect(r.agent.session.events.at(-1)).toMatchObject({ type: 'turn/end', data: { reason: {
      kind: 'error', error: { code: 'UNKNOWN', message: expect.stringContaining('research_provider_native_context_invalid') },
    } } })
    const nativeResult = r.agent.session.events.find((event: any) => event.type === 'tool/result')
    expect(nativeResult.data.error).toEqual({ name: 'ToolTimeoutError', code: 'TOOL_TIMEOUT' })
  } finally { await r.ctx.fiber.dispose() }
}, 15_000)
