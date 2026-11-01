import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, it } from 'vitest'
import { TaskOutputBudgets, PERPETUAL_OUTPUT_TOKEN_FLOOR } from '../src/host/task-output-budget.js'

it('uses the official request waterfall before the persisted header and adapter call', async () => {
  const sdkRoot = process.env.DSH_UPSTREAM_SOURCE_ROOT
  const sdk = createRequire(sdkRoot === undefined
    ? new URL('../../../../.cache/dsh-upstream/source/packages/core/agent-loop/package.json', import.meta.url)
    : resolve(sdkRoot, 'packages/core/agent-loop/package.json'))
  const load = (name: string) => import(/* @vite-ignore */ pathToFileURL(sdk.resolve(name)).href)
  const [{ Context }, llm, sessions, projections, prompt, tools, registry, loop] = await Promise.all([
    load('@deepseek-ai/cordis'), load('@deepseek-ai/dsh-llm'), load('@deepseek-ai/dsh-session'),
    load('@deepseek-ai/dsh-session-projection'), load('@deepseek-ai/dsh-system-prompt'),
    load('@deepseek-ai/dsh-tools'), load('@deepseek-ai/dsh-agent'), load('@deepseek-ai/dsh-agent-loop'),
  ])
  const calls: Array<{ maxTokens?: number }> = []
  class Adapter extends llm.LlmAdapter {
    async resolveModel(provider: string, model: string) {
      return { provider, id: model, name: model, defaultMaxTokens: 8192 }
    }
    async *stream(options: { maxTokens?: number }) {
      calls.push(options)
      yield { type: 'block-start', index: 0, blockType: 'text' }
      yield { type: 'text-delta', index: 0, text: 'synthetic SDK proof' }
      yield { type: 'block-end', index: 0, block: { type: 'text', text: 'synthetic SDK proof' } }
      yield { type: 'finish', reason: { kind: 'stop' } }
    }
  }
  const ctx = new Context()
  try {
    for (const plugin of [llm.default, sessions.default, projections.default, prompt.default, tools.default, registry.default]) await ctx.plugin(plugin)
    await ctx.plugin(loop.default, { agents: [] })
    ctx.llm.registerAdapter(['synthetic'], new Adapter())
    const budgets = new TaskOutputBudgets(() => true)
    budgets.remember({ runId: 'run-budget', sessionId: 'root-budget', requestId: 'rpc', requestHash: 'a'.repeat(64), generation: 1 },
      { run_id: 'run-budget', request_id: 'research-request:run-budget', output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2' })
    ctx.on('agent/request', async ({ agent }: { agent: { id: string } }, next: () => Promise<any>) => budgets.select(agent.id, await next()))
    const agent = ctx.agentLoop.create(sessions.SessionId('root-budget'), { provider: 'synthetic', model: 'synthetic' })
    agent.followup(llm.createUserMessage({ content: [{ type: 'text', text: 'Exercise output budget only.' }], source: { kind: 'user' } }))
    await agent.whenIdle()
    expect(calls).toHaveLength(1)
    expect(calls[0].maxTokens).toBe(PERPETUAL_OUTPUT_TOKEN_FLOOR)
    const header = agent.session.events.find((event: { type: string }) => event.type === 'request/header')
    expect(header.data.header.config.maxTokens).toBe(PERPETUAL_OUTPUT_TOKEN_FLOOR)
  } finally { await ctx.fiber.dispose() }
}, 20000)
