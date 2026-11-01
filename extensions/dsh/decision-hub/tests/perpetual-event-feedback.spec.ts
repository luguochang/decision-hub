import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { readQualifiedReviewerContext, registerSynthesisReviewContext } from '../src/final-review-gate.js'
import { bodyHash } from '../src/perpetual-synthesis-protocol.js'

type VisibleSchemaNode = {
  $ref?: string; $defs?: Record<string, VisibleSchemaNode>; properties?: Record<string, VisibleSchemaNode>
  oneOf?: VisibleSchemaNode[]; description?: string; const?: unknown; enum?: unknown[]
}
function visibleNode(root: VisibleSchemaNode, source: VisibleSchemaNode | undefined): VisibleSchemaNode {
  if (!source) throw new Error('visible schema node missing')
  let node = source
  const seen = new Set<string>()
  while (node.$ref) {
    if (!node.$ref.startsWith('#/$defs/') || seen.has(node.$ref)) throw new Error('invalid visible local reference')
    seen.add(node.$ref)
    const { $ref, ...annotations } = node
    const definition = root.$defs?.[$ref.slice('#/$defs/'.length)]
    if (!definition) throw new Error('visible definition missing')
    node = { ...definition, ...annotations }
  }
  return node
}

const fixture = () => {
  const { draft, options } = JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
  draft.market_event_conditioned = false
  for (const estimate of [draft.event_probability.occurrence_distribution, draft.event_probability.outcome_given_occurrence]) {
    estimate.kind = 'unavailable'
    estimate.probabilities = null
    estimate.source_ref = null
  }
  const catalog = options.review_originals
  const binding = { runId: catalog.run_id, requestHash: catalog.request_hash, generation: 1,
    ...deterministicIds(catalog.run_id, catalog.request_hash, 1) }
  const args = { action: 'prepare', request_id: options.request_id, draft }
  const context: any = { binding, perpetualBinding: options.binding, perpetualReviewOriginals: catalog,
    admittedEvidence: [], request: { run_id: catalog.run_id, request_id: options.request_id, input_evidence: [],
      output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
      execution_mode: 'replay', pit_cutoff_at: options.requested_at, execution_budget: { max_subagents: 4 } },
    events: [{ seq: 1, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } },
      { seq: 2, time: Date.parse(options.requested_at), type: 'tool/call',
        data: { callId: 'prepare', name: 'decision_hub_synthesis_submit', arguments: args } }] }
  const tool = createSynthesisToolDefinition({ reviewContext: async () => context })
  const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
  return { draft, context, args, tool, exec }
}

describe('repairable ordinary-market event conditioning feedback', () => {
  it('rejects a foreign event with the exact field and required relation', async () => {
    const { draft, args, tool, exec } = fixture()
    const foreign = structuredClone(draft.assumptions.find((row: any) => row.role === 'event_definition'))
    foreign.assumption_id += '-foreign'
    draft.assumptions.push(foreign)
    draft.event_probability.outcome_given_occurrence.conditioning_ref = { local_id: foreign.assumption_id }
    const saved = structuredClone(args)
    await expect(tool.execute(args, exec('prepare'))).rejects.toMatchObject({ issues: [expect.objectContaining({
      code: 'custom', path: ['draft', 'event_probability', 'outcome_given_occurrence', 'conditioning_ref'],
      message: expect.stringContaining('must equal event_probability.event_definition_ref'),
    })] })
    expect(args).toEqual(saved)
    const parameters = tool.parameters as unknown as VisibleSchemaNode
    let prepare = visibleNode(parameters, parameters.oneOf?.[1])
    while (prepare.oneOf) prepare = visibleNode(parameters, prepare.oneOf[0])
    const authoredDraft = visibleNode(parameters, prepare.properties?.draft)
    const version = visibleNode(parameters, authoredDraft.properties?.schema_version)
    expect(version.const ?? version.enum?.[0]).toBe(draft.schema_version)
    const event = visibleNode(parameters, authoredDraft.properties?.event_probability)
    const visible = visibleNode(parameters, event.properties?.outcome_given_occurrence).description
    expect(visible).toContain('conditioning_ref must equal event_probability.event_definition_ref')
    expect(visible).toContain('unavailable')
    expect(visible).toContain('market_event_conditioned=false')
  })

  it('derives only the missing own-event identity for an unavailable outcome and preserves the authored source', async () => {
    const { draft, args, tool, exec } = fixture()
    draft.event_probability.outcome_given_occurrence.conditioning_ref = null
    const saved = structuredClone(args)
    const prepared: any = await tool.execute(args, exec('prepare'))
    expect(prepared.candidate.event_probability.outcome_given_occurrence.conditioning_ref).toEqual(prepared.candidate.event_probability.event_definition_ref)
    expect(prepared.candidate.event_probability.outcome_given_occurrence.kind).toBe('unavailable')
    expect(prepared.candidate.event_probability.outcome_given_occurrence.probabilities).toBeNull()
    expect(prepared.candidate.market_distribution.event_scenario_ref).toBeNull()
    expect(prepared.preparation.source_draft_hash).toBe(bodyHash(saved.draft))
    expect(args).toEqual(saved)
  })

  it('prepares and qualifies the own-event reference while preserving ordinary null probabilities', async () => {
    const { draft, context, args, tool, exec } = fixture()
    draft.event_probability.outcome_given_occurrence.conditioning_ref = structuredClone(draft.event_probability.event_definition_ref)
    const saved = structuredClone(args)
    const prepared: any = await tool.execute(args, exec('prepare'))
    expect(prepared.schema_version).toBe('decision-hub-perpetual-preparation.v1')
    expect(prepared.preparation.prepared_call_id).toBe('prepare')
    expect(prepared.candidate.event_probability.outcome_given_occurrence.conditioning_ref).toEqual(prepared.candidate.event_probability.event_definition_ref)
    expect(prepared.candidate.event_probability.outcome_given_occurrence.probabilities).toBeNull()
    expect(prepared.candidate.event_probability.occurrence_distribution.probabilities).toBeNull()
    expect(prepared.candidate.market_distribution.event_scenario_ref).toBeNull()
    expect(args).toEqual(saved)
    context.events.push({ seq: 3, type: 'tool/result', data: { callId: 'prepare', value: prepared } },
      { seq: 4, time: Date.parse(prepared.review_request.requested_at) + 1, type: 'tool/call',
        data: { callId: 'review', name: 'subagent_reviewer', arguments: {} } })
    const root = {}, dispose = registerSynthesisReviewContext(root, async () => context, async () => context)
    try {
      const qualified: any = await readQualifiedReviewerContext(root, { id: context.binding.sessionId } as never, 'review', new AbortController().signal)
      expect(qualified.protocol).toBe('decision_v2')
      expect(qualified.freeze_call_id).toBe('prepare')
      expect(qualified.candidate).toEqual(prepared.candidate)
    } finally { dispose() }
  })
})
