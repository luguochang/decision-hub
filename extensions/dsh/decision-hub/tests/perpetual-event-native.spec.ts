import { readFileSync, writeFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'
import { bodyHash, buildPerpetualReviewerPrompt, canonicalJson, perpetualAuthorizationFromOutput, perpetualFindingsFromOutput } from '../src/perpetual-synthesis-protocol.js'
import * as gate from '../src/final-review-gate.js'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { unpackEventScenarioManifest } from '../src/event-scenario-manifest.js'

const fixture = () => JSON.parse(readFileSync(new URL('../../../../artifacts/d2-event-scenario-action-plans/native-event-fixture.json', import.meta.url), 'utf8'))

it('prepares the exact server-composed event comparisons beside the explicit ordinary projection', () => {
  const { draft, options } = fixture(), before = canonicalJson(draft)
  const result: any = preparePerpetualSynthesis(draft, options)
  const material = JSON.parse(options.review_originals.originals.find((row: any) => row.role === 'event_scenario_plan').body_json)
  const expanded = unpackEventScenarioManifest(material.manifest)
  expect(result.candidate.schema_version).toBe('research-decision-synthesis.v3')
  expect(result.candidate).not.toHaveProperty('event_plan')
  expect(result.candidate.event_plan_ref.hash).toBe(bodyHash(expanded.event_plan))
  expect(result.candidate.event_plan_manifest_ref).toEqual(material.event_plan_manifest_ref)
  expect(expanded.event_plan.branches).toHaveLength(9)
  expect(result.candidate.primary_v2.schema_version).toBe('research-decision-synthesis.v2')
  expect(result.review_request.primary_review_request.synthesis).toEqual(result.candidate.primary_v2)
  expect(result.event_plan_hash).toBe(bodyHash(expanded.event_plan))
  expect(result.source_draft_hash).toBe(bodyHash(draft))
  expect(canonicalJson(draft)).toBe(before)
  const prompt = buildPerpetualReviewerPrompt(result.review_request, '', options.requested_at, options.review_originals, result.assumptions)
  expect(prompt).toContain(JSON.stringify(result.review_request))
  expect(prompt).toContain('fixed event')
  expect(prompt).toContain('post-event')
  expect(prompt.split('event-scenario-preparation-manifest.v1')).toHaveLength(2)
  expect(Buffer.byteLength(prompt, 'utf8')).toBeLessThanOrEqual(524_288)
  expect(Buffer.byteLength(canonicalJson(options.review_originals), 'utf8')).toBeLessThanOrEqual(384_000)
  const lines = prompt.split('\n'), auth = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
  expect(auth.schema_version).toBe('action-review-authorization.v3')
  expect(auth.primary_authorization.schema_version).toBe('action-review-authorization.v2')
  expect(auth.event_plan_scope.branches).toEqual(result.review_request.event_plan_domain.branches)
  expect(Date.parse(auth.event_plan_scope.valid_until)).toBeGreaterThan(Date.parse(auth.primary_authorization.valid_until))
  auth.verdict = auth.primary_authorization.verdict = auth.event_plan_scope.verdict = 'approved'
  const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
  const output = [{ type: 'text', text: JSON.stringify([auth, findings]) }]
  expect(perpetualAuthorizationFromOutput(result.review_request, output).verdict).toBe('approved')
  expect(perpetualFindingsFromOutput(result.review_request, output, options.review_originals, prompt)).toEqual(findings)
  auth.event_plan_scope.valid_until = auth.primary_authorization.valid_until
  expect(() => perpetualAuthorizationFromOutput(result.review_request, [{ type: 'text', text: JSON.stringify([auth, findings]) }])).toThrow()
})

it('rejects a substituted plan or source draft before independent review', () => {
  const { draft, options } = fixture()
  draft.event_plan_draft.plan_id += '-changed'
  expect(() => preparePerpetualSynthesis(draft, options)).toThrow('event_plan_source_draft_mismatch')
  const other = fixture(), row = other.options.review_originals.originals.find((item: any) => item.role === 'event_scenario_plan')
  const material = JSON.parse(row.body_json); material.manifest.expanded_hash = 'f'.repeat(64)
  row.body_json = canonicalJson(material); row.source_ref.hash = bodyHash(material)
  expect(() => preparePerpetualSynthesis(other.draft, other.options)).toThrow()
  const valid = JSON.parse(fixture().options.review_originals.originals.find((item: any) => item.role === 'event_scenario_plan').body_json).manifest
  const duplicate = structuredClone(valid); duplicate.originals.push(structuredClone(duplicate.originals[0]))
  duplicate.originals.sort((left: any, right: any) => left.hash.localeCompare(right.hash))
  expect(() => unpackEventScenarioManifest(duplicate)).toThrow()
  const external = structuredClone(valid); external.root_ref = { ref: 'event-scenario-manifest:' + 'f'.repeat(64), hash: 'f'.repeat(64) }
  expect(() => unpackEventScenarioManifest(external)).toThrow()
  const orphan = structuredClone(valid), orphanBody = { unreferenced: 'x'.repeat(200) }, orphanHash = bodyHash(orphanBody)
  orphan.originals.push({ hash: orphanHash, body: orphanBody })
  orphan.originals.sort((left: any, right: any) => left.hash.localeCompare(right.hash))
  expect(() => unpackEventScenarioManifest(orphan)).toThrow()
})

it('requires actual separate child prompt/output and finalizes the complete event wrapper unchanged', async () => {
  const { draft, options, request } = fixture(), at = Date.parse(options.requested_at)
  const catalog = options.review_originals
  const binding = { runId: catalog.run_id, requestHash: catalog.request_hash, generation: 1, ...deterministicIds(catalog.run_id, catalog.request_hash, 1) }
  const args = { action: 'prepare', request_id: options.request_id, draft }
  const context: any = { binding, perpetualBinding: options.binding, perpetualReviewOriginals: catalog, admittedEvidence: [],
    request: request ?? { request_id: options.request_id, run_id: catalog.run_id, output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3',
      allowed_capabilities: [], input_evidence: [], execution_mode: 'live', execution_budget: { max_subagents: 4 } },
    events: [{ seq: 1, time: at, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } },
      { seq: 2, time: at, type: 'tool/call', data: { callId: 'prepare', name: 'decision_hub_synthesis_submit', arguments: args } }] }
  const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
  const synthesis = createSynthesisToolDefinition({ reviewContext: async () => context })
  const prepared: any = await synthesis.execute(args, exec('prepare'))
  const unitCosts = JSON.parse(catalog.originals.find((row: any) => row.role === 'cost_assumptions').body_json)
  const costOriginal = catalog.originals.find((row: any) => row.role === 'cost_policy').original_ref
  const rawUnitRows = draft.primary_draft.candidates.filter((row: any) => row.size_mode === 'unit_notional'
    && row.quantity_before === null && row.quantity_after === null && row.action !== 'conditional'
    && canonicalJson(row.cost_ref) === canonicalJson(unitCosts.base_cost_ref)
    && canonicalJson(row.cost_ref) === canonicalJson(costOriginal))
  expect(rawUnitRows.length).toBeGreaterThan(0)
  expect(rawUnitRows.every((row: any) => row.unit_notional_usdt === '1')).toBe(true)
  expect(unitCosts.unit_notional_usdt).toBe('100')
  for (const rawRow of rawUnitRows) {
    const normalized = prepared.candidate.primary_v2.candidate_set.candidates.find((row: any) => row.candidate_id === rawRow.candidate_id)
    expect(normalized.unit_notional_usdt).toBe('100')
    expect(normalized.quantity_before).toBeNull()
    expect(normalized.quantity_after).toBeNull()
  }
  expect(prepared.preparation.source_draft_hash).toBe(bodyHash(draft))
  const material = JSON.parse(catalog.originals.find((row: any) => row.role === 'event_scenario_plan').body_json)
  const expanded = unpackEventScenarioManifest(material.manifest)
  expect(prepared.schema_version).toBe('decision-hub-perpetual-preparation.v2')
  expect(prepared.preparation.schema_version).toBe('perpetual-preparation-witness.v2')
  expect(prepared.preparation.event_plan_hash).toBe(bodyHash(expanded.event_plan))
  expect(expanded.event_plan.branches).toHaveLength(9)
  context.events.push({ seq: 3, time: at, type: 'tool/result', data: { callId: 'prepare', value: prepared } },
    { seq: 4, time: at + 1000, type: 'tool/call', data: { callId: 'review', name: 'subagent_reviewer', arguments: {} } })
  const root = {}, unregister = gate.registerSynthesisReviewContext(root, async () => context, async () => context)
  const reviewer = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 },
    { root, readQualified: gate.readQualifiedReviewerContext, async start(_provider, start): Promise<any> {
      const prompt = (start.prompt[0] as any).text, lines = prompt.split('\n')
      expect(prompt).toContain(JSON.stringify(prepared.review_request))
      expect(prompt.split('event-scenario-preparation-manifest.v1')).toHaveLength(2)
      expect(Buffer.byteLength(prompt, 'utf8')).toBeLessThanOrEqual(524_288)
      expect(start.persona).toContain('action-review-authorization.v3')
      const auth = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
      auth.verdict = auth.primary_authorization.verdict = auth.event_plan_scope.verdict = 'approved'
      const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
      const output = [{ type: 'text', text: JSON.stringify([auth, findings]) }]
      const events = [{ seq: 0, time: at + 1000, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] } },
        { seq: 1, time: at + 1001, type: 'assistant/message', data: { message: { content: output } } },
        { seq: 2, time: at + 1002, type: 'turn/end', data: { reason: { kind: 'completed' } } }]
      return { id: 'event-child', localAgent: { id: 'event-child', session: { header: { parentSession: binding.sessionId }, events } },
        result: Promise.resolve({ stopReason: 'completed', output }), dispose: async () => {} }
    } })
  try {
    const wrapper: any = await reviewer.execute({ description: 'Review frozen event branches', prompt: 'ignored', run_in_background: false }, exec('review'))
    context.events.push({ seq: 5, time: at + 1003, type: 'tool/result', data: { callId: 'review', value: wrapper } })
    const finalize = { action: 'finalize', request_id: options.request_id, prepared_call_id: 'prepare' }
    context.events.push({ seq: 6, time: at + 1004, type: 'tool/call', data: { callId: 'finalize', name: 'decision_hub_synthesis_submit', arguments: finalize } })
    const final: any = await synthesis.execute(finalize, exec('finalize'))
    expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v4')
    expect(final.review_gate.status).toBe('matched')
    expect(final.candidate).toEqual(prepared.candidate)
    expect(final.preparation).toEqual(prepared.preparation)
    context.events.push({ seq: 7, time: at + 1005, type: 'tool/result', data: { callId: 'finalize', value: final } })
    writeFileSync(new URL('../../../../artifacts/d2-event-scenario-action-plans/native-event-ts-proof.json', import.meta.url),
      JSON.stringify({ fixture_kind: 'controlled_sdk_child_transcript', model_invoked: false, prepared, review: wrapper, final,
        binding, request: context.request, events: context.events, representation_bytes: {
          manifest: Buffer.byteLength(canonicalJson(material.manifest), 'utf8'),
          expanded_material: Buffer.byteLength(canonicalJson(expanded), 'utf8'),
          full_event_plan: Buffer.byteLength(canonicalJson(expanded.event_plan), 'utf8'),
          review_catalog: Buffer.byteLength(canonicalJson(catalog), 'utf8'),
          actual_prompt: Buffer.byteLength(wrapper.actual_prompt, 'utf8'),
          review_packet: Buffer.byteLength(JSON.stringify(prepared.review_request), 'utf8'),
          preparation_result: Buffer.byteLength(JSON.stringify(prepared), 'utf8'),
          final_result: Buffer.byteLength(JSON.stringify(final), 'utf8'),
          native_review_proof: Buffer.byteLength(JSON.stringify(wrapper), 'utf8'),
        } }, null, 2))
    expect(Buffer.byteLength(JSON.stringify(final), 'utf8')).toBeLessThanOrEqual(1_000_000)
    context.events[4].data.value = { ...wrapper, actual_prompt: wrapper.actual_prompt + ' ' }
    expect((await synthesis.execute(finalize, exec('finalize')) as any).review_gate.status).toBe('unreviewed')
  } finally { unregister() }
})
