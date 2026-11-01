import { readFileSync } from 'node:fs'
import * as ts from 'typescript'
import { expect, it, vi } from 'vitest'
import { TaskSkillVisibility, taskSkillCapability } from '../src/host/task-skill-visibility.js'
import { DECISION_SYNTHESIS_REF, LEGACY_SYNTHESIS_REF } from '../src/synthesis-protocol.js'

const binding = { runId: 'run_protocol', sessionId: 'dsh_root', generation: 1,
  requestHash: 'a'.repeat(64), requestId: 'command_protocol' }
const task = { run_id: binding.runId, request_id: `research-request:${binding.runId}`,
  execution_mode: 'live' as const, research_mode: 'current_decision' as const,
  output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2' }

// Exercise the actual Bridge method without constructing workers, registering
// packages, or granting a synthetic selection to this unselected V2 Task.
function preStepSeam() {
  const wire = readFileSync(new URL('../src/host/bridge.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('bridge.ts', wire, ts.ScriptTarget.Latest, true)
  const declaration = tree.statements.find(node => ts.isClassDeclaration(node)
    && node.members.some(member => ts.isMethodDeclaration(member)
      && member.name.getText(tree) === 'selectedSkillPreStep')) as ts.ClassDeclaration
  const method = declaration.members.find(member => ts.isMethodDeclaration(member)
    && member.name.getText(tree) === 'selectedSkillPreStep')!.getText(tree)
  const js = ts.transpileModule(`class BridgeSeam { ${method} }; return BridgeSeam`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText
  const Type = new Function('taskSkillCapability', js)(taskSkillCapability)
  const bridge = new Type()
  bridge.skills = { deliveryDecision: vi.fn(() => undefined),
    rememberDeliveryDecision: vi.fn((_identity, value) => value) }
  bridge.hub = { selectedSkillDelivery: vi.fn(async () => ({ delivery_mode: 'legacy-tool' })) }
  return bridge
}

it('matches the server V1-only selected-package eligibility without granting V2 a spot skill', () => {
  expect(taskSkillCapability(task)).toBe('disabled')
  expect(taskSkillCapability({ ...task, output_schema_ref: DECISION_SYNTHESIS_REF })).toBe('decision')
  expect(taskSkillCapability({ ...task, output_schema_ref: LEGACY_SYNTHESIS_REF })).toBe('disabled')
  expect(() => taskSkillCapability({ ...task, output_schema_ref: 'unknown_protocol' })).toThrow('skill_task_protocol_unknown')
})

it('continues the actual V2 pre-step with its evidence messages and no unsupported selected-delivery request', async () => {
  const bridge = preStepSeam()
  bridge.hub.selectedSkillDelivery.mockRejectedValue(new Error('legacy_skill_unversioned'))
  const decision = { kind: 'enter', messages: [{ role: 'user', content: [{ type: 'text', text: 'admitted V2 evidence' }] }] }
  const result = await bridge.selectedSkillPreStep(decision, task, { id: binding.sessionId }, binding,
    new AbortController().signal, new Date(Date.now() + 3_600_000).toISOString())
  expect(result).toBe(decision)
  expect(bridge.hub.selectedSkillDelivery).not.toHaveBeenCalled()
  expect(bridge.skills.rememberDeliveryDecision).not.toHaveBeenCalled()
})

it('still authenticates selected delivery for the supported V1 live decision Task', async () => {
  const bridge = preStepSeam(), decision = { kind: 'enter', messages: [] }
  expect(await bridge.selectedSkillPreStep(decision, { ...task, output_schema_ref: DECISION_SYNTHESIS_REF },
    { id: binding.sessionId }, binding, new AbortController().signal,
    new Date(Date.now() + 3_600_000).toISOString())).toBe(decision)
  expect(bridge.hub.selectedSkillDelivery).toHaveBeenCalledOnce()
})

it('restricts only the unavailable skill tool while leaving ordinary V2 research and synthesis tools alone', () => {
  const restrictions: string[][] = []
  const lift = vi.fn(), tools = {
    restrict: vi.fn(({ deny }: { deny: string[] }) => { restrictions.push(deny); return lift }),
    guard: vi.fn(() => vi.fn()),
  }
  const agent = { id: binding.sessionId, ctx: { tools } }
  const visibility = new TaskSkillVisibility(() => true)
  visibility.remember(binding, task)
  visibility.apply(agent as never, binding)
  expect(restrictions).toEqual([['decision_hub_skill']])
  expect(lift).not.toHaveBeenCalled()
  expect(restrictions.flat()).not.toContain('decision_hub_research')
  expect(restrictions.flat()).not.toContain('decision_hub_synthesis')
  visibility.disposed(agent as never)
})
