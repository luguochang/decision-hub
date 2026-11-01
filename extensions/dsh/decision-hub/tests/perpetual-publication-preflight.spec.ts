import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { perpetualPreparationWitnessV1Schema } from '@decision-hub/contracts-ts'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { resolvePerpetualPreparedCall } from '../src/final-review-gate.js'
import { preparePerpetualSynthesis } from '../src/perpetual-preparation.js'
import { bodyHash, canonicalJson } from '../src/perpetual-synthesis-protocol.js'

const METHOD_REF = 'decision-method:perpetual-assumed-unit-mean-net.v1'
const METHOD_CODE = '49e4ab99ae4a86616fbd335316ccc9d9a78b26e3c5ed168ff3df97e707011d03'

function sourceFixture(options: { state?: 'unknown' | 'flat' | 'long' | 'short'; methodCode?: string; methodSchema?: string } = {}) {
  const value = JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json', import.meta.url), 'utf8'))
  const state = options.state ?? 'unknown'
  const methodCode = options.methodCode ?? METHOD_CODE
  const original = (role: string) => value.options.review_originals.originals.find((row: any) => row.source_kind === 'server_material' && row.role === role)
  const reseal = (row: any, body: unknown, originalRef: unknown = row.original_ref) => {
    row.body_json = canonicalJson(body)
    row.source_ref.hash = bodyHash(body)
    row.original_ref = originalRef
  }
  const methodBody = {
    schema_version: options.methodSchema ?? 'perpetual-comparison-method.v1',
    qualification: 'research_trial', financial_qualified: false,
    selection_kind: 'assumed_unit_mean_net', dependencies: { fixture: 'a'.repeat(64) },
    method_code_hash: methodCode, executable_quantity_supported: false,
    required_inputs: ['explicit_costs'],
  }
  const methodRef = { ref: METHOD_REF, hash: bodyHash(methodBody) }
  reseal(original('comparison_method'), methodBody, methodRef)
  value.options.binding.method_ref = methodRef
  value.options.binding.method_code_hash = methodCode

  const policyRow = original('allowed_domain_policy')
  const policy = JSON.parse(policyRow.body_json)
  policy.method_ref = methodRef
  policy.method_code_hash = methodCode
  reseal(policyRow, policy)

  const costBody = {
    kind: 'assumed_fixed_unit_cash', entry_trade_cost_usdt: '0.07', exit_trade_cost_usdt: '0.07',
    long_funding_cash_usdt: '0', trade_cost_multiplier: '1',
  }
  const costRef = { ref: 'decision-cost:perpetual-reference-unit.v1#base-cost', hash: bodyHash(costBody) }
  reseal(original('cost_policy'), costBody, costRef)
  reseal(original('position_context'), { state })

  for (const candidate of value.draft.candidates) candidate.cost_ref = structuredClone(costRef)
  if (state === 'long' || state === 'short') {
    Object.assign(value.draft.candidates[0], { position_branch: state, action: 'no_change', target_side: state })
  }
  return { ...value, costRef }
}

function liveFixture(source = sourceFixture()) {
  const { draft, options } = source
  const catalog = options.review_originals
  const binding = {
    runId: catalog.run_id, requestHash: catalog.request_hash, generation: 1,
    ...deterministicIds(catalog.run_id, catalog.request_hash, 1),
  }
  const args = { action: 'prepare', request_id: options.request_id, draft }
  const context: any = {
    binding, perpetualBinding: options.binding, perpetualReviewOriginals: catalog, admittedEvidence: [],
    request: {
      run_id: catalog.run_id, request_id: options.request_id, input_evidence: [],
      output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
      execution_mode: 'replay', pit_cutoff_at: options.requested_at, execution_budget: { max_subagents: 4 },
    },
    events: [
      { seq: 1, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } },
      { seq: 2, time: Date.parse(options.requested_at), type: 'tool/call', data: { callId: 'prepare', name: 'decision_hub_synthesis_submit', arguments: args } },
    ],
  }
  const tool = createSynthesisToolDefinition({ reviewContext: async () => context })
  const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
  return { ...source, args, context, tool, exec }
}

describe('live publication cost-original preflight', () => {
  it('rejects a selected flat no-change candidate with an exact repair path before returning a witness', async () => {
    const value = liveFixture()
    value.draft.candidates[2].cost_ref = null
    const saved = structuredClone(value.args)
    await expect(value.tool.execute(value.args, value.exec('prepare'))).rejects.toMatchObject({ issues: [expect.objectContaining({
      code: 'custom', path: ['draft', 'candidates', 2, 'cost_ref'],
      message: expect.stringMatching(/^decision_hub_preparation_candidate_cost_original_missing:.*candidate_id=no_change.*cost_policy\.original_ref.*not cost_policy\.source_ref/),
    })] })
    expect(value.args).toEqual(saved)
  })

  it('accepts the exact authenticated cost-policy original without changing authored bytes', async () => {
    const value = liveFixture()
    const saved = structuredClone(value.args)
    const prepared: any = await value.tool.execute(value.args, value.exec('prepare'))
    expect(prepared.schema_version).toBe('decision-hub-perpetual-preparation.v1')
    expect(prepared.candidate.candidate_set.candidates.map((row: any) => row.cost_ref)).toEqual([
      value.costRef, value.costRef, value.costRef,
    ])
    expect(value.args).toEqual(saved)
  })

  it('keeps held no-change bound to the same cost original without changing funding fields', async () => {
    const value = liveFixture(sourceFixture({ state: 'long' }))
    const funding = structuredClone(value.draft.candidates.map((row: any) => row.funding_ref))
    value.draft.candidates[0].cost_ref = null
    await expect(value.tool.execute(value.args, value.exec('prepare'))).rejects.toMatchObject({ issues: [expect.objectContaining({
      path: ['draft', 'candidates', 0, 'cost_ref'], message: expect.stringContaining('selected_position_branch=long'),
    })] })
    value.draft.candidates[0].cost_ref = structuredClone(value.costRef)
    const prepared: any = await value.tool.execute(value.args, value.exec('prepare'))
    expect(prepared.candidate.candidate_set.candidates.map((row: any) => row.funding_ref)).toEqual(funding)
  })

  it('skips a distinct method identity but fails an inconsistent catalog claiming the approved identity', async () => {
    const other = liveFixture(sourceFixture({ methodCode: 'c'.repeat(64) }))
    for (const row of other.draft.candidates) row.cost_ref = null
    await expect(other.tool.execute(other.args, other.exec('prepare'))).resolves.toMatchObject({ schema_version: 'decision-hub-perpetual-preparation.v1' })

    const inconsistent = liveFixture(sourceFixture({ methodSchema: 'perpetual-comparison-method.v0' }))
    await expect(inconsistent.tool.execute(inconsistent.args, inconsistent.exec('prepare'))).rejects.toThrow('decision_hub_preparation_comparison_method_original_invalid')
  })

  it('reopens an exact historical preparation with null cost refs without applying the live preflight', () => {
    const value = liveFixture()
    for (const row of value.draft.candidates) row.cost_ref = null
    const requestedAt = new Date(Date.parse(value.options.requested_at)).toISOString()
    const normalized = preparePerpetualSynthesis(value.draft, { ...value.options, requested_at: requestedAt })
    const preparation = perpetualPreparationWitnessV1Schema.parse({
      schema_version: 'perpetual-preparation-witness.v1', prepared_call_id: 'prepare', prepared_seq: 2,
      requested_at: requestedAt, source_draft_hash: normalized.source_draft_hash,
      normalized_synthesis_hash: normalized.normalized_synthesis_hash,
      normalized_review_request_hash: normalized.normalized_review_request_hash, assumptions: normalized.assumptions,
    })
    const result = { schema_version: 'decision-hub-perpetual-preparation.v1', preparation, candidate: normalized.candidate, review_request: normalized.review_request }
    value.context.events.push(
      { seq: 3, type: 'tool/result', data: { callId: 'prepare', value: result } },
      { seq: 4, time: Date.parse(value.options.requested_at) + 1, type: 'tool/call', data: { callId: 'finalize', name: 'decision_hub_synthesis_submit', arguments: { action: 'finalize', request_id: value.options.request_id, prepared_call_id: 'prepare' } } },
    )
    expect(resolvePerpetualPreparedCall(value.context, 'prepare', 'prepare')).toEqual({
      candidate: normalized.candidate, review_request: normalized.review_request, preparation,
    })
  })
})
