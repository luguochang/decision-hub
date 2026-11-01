// New protocol controls use synthetic native events, no model, provider call or DB.
import { createHash } from 'node:crypto'
import { describe, expect, it, vi } from 'vitest'
import { researchDecisionSynthesisV1Schema, researchSynthesisCandidateSchema } from '@decision-hub/contracts-ts'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { buildFrozenReviewerPrompt } from '../src/frozen-review-prompt.js'
import { evaluateFinalReviewGate, normalizeSynthesisCandidate, readQualifiedReviewerContext, registerSynthesisReviewContext, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { DECISION_SYNTHESIS_REF, LEGACY_SYNTHESIS_REF, parseSynthesisCandidate } from '../src/synthesis-protocol.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { providerCanonicalJson, registerProviderInputReader, verifySynthesisProviderInput } from '../src/host/provider-input.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { compactSharedSynthesisSchema } from '../src/synthesis-tool-schema.js'

type VisibleSchema = { $ref?: string; oneOf: VisibleSchema[]; properties: Record<string, VisibleSchema>; $defs: Record<string, VisibleSchema> }

it('preserves the full pre-compaction schema semantics and all three action forms', () => {
  const schema = createSynthesisToolDefinition({ reviewContext: async () => fixture().context }).parameters as unknown as VisibleSchema
  const expand = (value: unknown, active = new Set<string>()): unknown => {
    if (Array.isArray(value)) return value.map(child => expand(child, active))
    if (!value || typeof value !== 'object') return value
    const row = Object.fromEntries(Object.entries(value))
    if (typeof row.$ref === 'string') {
      if (active.has(row.$ref)) throw new Error('cyclic schema reference')
      const next = new Set(active); next.add(row.$ref)
      const target = expand(schema.$defs[row.$ref.slice('#/$defs/'.length)], next) as Record<string, unknown>
      const extra = Object.fromEntries(Object.entries(row).filter(([key]) => key !== '$ref' && key !== '$defs')
        .map(([key, child]) => [key, expand(child, active)]))
      const combined = Object.keys(extra).some(key => Object.hasOwn(target, key)) ? { allOf: [target, extra] } : { ...target, ...extra }
      return Object.fromEntries(Object.entries(combined).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))
    }
    return Object.fromEntries(Object.entries(row).filter(([key]) => key !== '$defs')
      .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, child]) => [key, expand(child, active)]))
  }
  expect(schema.oneOf).toHaveLength(3)
  expect(sha(JSON.stringify(expand(schema)))).toBe('90c98ccc72d3b57305178e495ccba2ace4f5845938335a4497558b82e367ba26')
})

it('shares repeated schema nodes without factoring property maps or literal enum/default data', () => {
  const leaf = { type: 'string', description: 'Keep the complete original field description. '.repeat(12), enum: ['alpha', 'beta'] }
  const literal = { type: 'literal data', oneOf: ['not a schema'], $defs: { preserved: true } }
  const raw = { type: 'object', properties: { type: leaf, oneOf: leaf, $defs: leaf },
    required: ['type', 'oneOf', '$defs'], additionalProperties: false, enum: [literal], default: literal }
  const before = JSON.stringify(raw), compact = compactSharedSynthesisSchema(raw)
  expect(JSON.stringify(raw)).toBe(before)
  expect(compact.enum).toEqual(raw.enum); expect(compact.default).toEqual(raw.default)
  const properties = compact.properties as Record<string, { $ref: string }>
  expect(Object.keys(properties)).toEqual(['type', 'oneOf', '$defs'])
  expect(new Set(Object.values(properties).map(value => value.$ref)).size).toBe(1)
  expect(typeof properties.type!.$ref).toBe('string')
  const definitions = compact.$defs as Record<string, unknown>
  expect(definitions[properties.type!.$ref.slice('#/$defs/'.length)]).toEqual(leaf)
})

it('preserves reference sibling descriptions and simultaneous constraints', () => {
  const description = 'A sibling annotation must remain visible alongside the referenced schema.'
  const raw = { type: 'object', properties: { sample: { $ref: '#/$defs/text', minLength: 3, description } },
    $defs: { text: { type: 'string', minLength: 2 } } }
  const compact = compactSharedSynthesisSchema(raw)
  const definitions = compact.$defs as Record<string, Record<string, unknown>>
  const resolve = (row: Record<string, unknown>): Record<string, unknown> => typeof row.$ref === 'string'
    ? resolve(definitions[row.$ref.slice('#/$defs/'.length)]!) : row
  const sample = resolve((compact.properties as Record<string, Record<string, unknown>>).sample!)
  expect((sample.allOf as Record<string, unknown>[]).map(resolve)).toEqual([
    { type: 'string', minLength: 2 }, { minLength: 3, description },
  ])
  expect(JSON.stringify(compact)).toContain(description)
})

const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const at = '2026-10-13T00:00:00Z', later = '2026-10-13T00:10:00Z', until = '2026-10-14T00:00:00Z'
const scope = 'crypto:spot:okx:BTC-USDT:trade:1m'
const binding = { runId: 'run-decision-native', requestHash: 'a'.repeat(64), generation: 1,
  ...deterministicIds('run-decision-native', 'a'.repeat(64)) }
const old = researchSynthesisCandidateSchema.parse({ schema_version: 'research-synthesis-candidate.v1', request_id: `research-request:${binding.runId}`,
  causal_case: { case_id: 'case-1', thesis: '工程控制：条件成立后研究试用，非已校准收益。',
    main_chain: [{ link_id: 'main', claim_type: 'fact', statement: '工程材料', evidence_refs: ['ev-1'], confirmation: '核原件', invalidation: '材料相反', affected_horizons: ['24h'] }],
    opposite_chain: [{ link_id: 'counter', claim_type: 'scenario', statement: '条件可失效', evidence_refs: ['ev-1'], confirmation: '反证', invalidation: '独立原件', affected_horizons: ['24h'] }],
    unresolved_questions: [], evidence_refs: ['ev-1'] },
  horizons: ['30m', '24h', '72h'].map(horizon => ({ horizon, action: 'no_trade', subjective_probability: .5, probability_status: 'uncalibrated',
    evidence_refs: ['ev-1'], trigger: '待确认', invalidation: '证据改变', expires_at: until, next_review_at: later, missing_facts: [], confidence_cap_reason: '未校准' })) })
function candidate() {
  const value = researchDecisionSynthesisV1Schema.parse({ schema_version: 'research-decision-synthesis.v1', request_id: old.request_id,
    research_candidate_v1: old, action_intents: [{ schema_version: 'research-action-candidate.v1', scope_id: scope, horizon: '24h', as_of: at,
      valid_until: until, next_review_at: later, position_context: 'flat', action: 'enter_long',
      probability: { target_id: 'target-1', kind: 'unavailable', value: null, provenance_ref: null },
      entry: [{ id: 'entry-1', purpose: 'entry', expression: { op: 'compare', feature: 'close', scope_id: scope, unit: 'USDT/BTC', cmp: 'gte', threshold: '80000', max_age_seconds: 120 },
        min_fresh_samples: 2, max_age_seconds: 120, evaluation_seconds: 60, calendar_ref: 'crypto-24x7.v1', valid_until: until,
        original_text: '条件满足后建仓', input_requirements: [{ metric: 'close', scope, source: 'okx', unit: 'USDT/BTC' }] }], exit: [], invalidation: [],
      size_policy_ref: 'size-1', cost_policy_ref: 'cost-1', target_spec_ref: 'target-1', evidence_refs: ['ev-1'], claim_refs: [], mechanism_review_ref: 'review-1', reason: '合成示例，无校准资格' }],
    mechanism_reviews: [{ schema_version: 'research-mechanism-review.v1', review_id: 'review-1', run_id: binding.runId, cutoff: at, scope_id: scope, horizon: '24h',
      nodes: [{ id: 'n1', kind: 'expectation', description: '预期变化', refs: ['ev-1'] }, { id: 'n2', kind: 'observable', description: '待核变量', refs: [] }],
      edges: [{ id: 'e1', from_node: 'n1', to_node: 'n2', hypothesis: '传导假说', lag: null, support_refs: ['ev-1'], counter_refs: [], testable_feature_refs: [], status: 'hypothesis' }],
      alternative_refs: [], priced_in_status: 'unknown', priced_in_refs: [], unresolved: ['待独立核验'], affected_plan_refs: [] }], claim_signal_refs: [], skill_consumption_refs: [] })
  const baseAction = value.action_intents[0]!, baseReview = value.mechanism_reviews[0]!
  for (const horizon of ['30m', '24h', '72h'] as const) {
    const reviewId = horizon === '24h' ? baseReview.review_id : `review-${horizon}`
    if (horizon !== '24h') value.mechanism_reviews.push({ ...structuredClone(baseReview), horizon, review_id: reviewId })
    for (const position of ['flat', 'long'] as const) {
      if (horizon === '24h' && position === 'flat') continue
      if (position === 'flat') {
        value.action_intents.push({ ...structuredClone(baseAction), horizon,
          position_context: 'flat', action: 'enter_long', mechanism_review_ref: reviewId })
      } else {
        value.action_intents.push({ ...structuredClone(baseAction), horizon,
          position_context: 'long', action: 'hold', mechanism_review_ref: reviewId })
      }
    }
  }
  return value
}
const receipt = { schema_version: 'independent-review-receipt.v2', role: 'independent_reviewer', target_claim: old.causal_case!.thesis,
  evidence_refs: ['ev-1'], challenge: '缺少校准，保持未知', resolution: 'bounded', limitations: ['仅工程控制'], reviewer_version: 'decision-hub-frozen-reviewer.v2',
  issues: [{ issue_id: 'risk-1', severity: 'important', blocking_scope: ['execution'], evidence_refs: ['ev-1'], status: 'open', disposition: 'unresolved', resolution_evidence_refs: [],
    effect_on_direction: 'unknown', effect_on_tail_risk: 'unknown', effect_on_monitor: 'unknown', effect_on_report: 'unknown', reason: '没有金融效果证明' }] }
const event = (seq: number, type: string, data: Record<string, unknown>) => ({ seq, type, data, time: Date.parse(at) })
function fixture(value = candidate()) {
  const prompt = buildFrozenReviewerPrompt(value, '{}', 'independent-review-receipt.v2', DECISION_SYNTHESIS_REF)
  const review = { schema_version: 'decision-hub-frozen-review-result.v3', schema_repair: null, binding,
    freeze_call_id: 'freeze', freeze_seq: 2, candidate_json: prompt.candidate_json, candidate_sha256: prompt.candidate_sha256,
    expected_target_claim: prompt.expected_target_claim, actual_prompt: prompt.prompt, actual_prompt_sha256: sha(prompt.prompt), child_session_id: 'child-1',
    original_output: { kind: 'foreground', runId: 'child-1', output: [{ type: 'text', text: JSON.stringify(receipt) }] } }
  const context: SynthesisReviewContext = { binding, admittedEvidence: [], request: { run_id: binding.runId, request_id: old.request_id,
    execution_mode: 'replay', pit_cutoff_at: at, output_schema_ref: DECISION_SYNTHESIS_REF, allowed_capabilities: [],
    input_evidence: [{ evidence_id: 'ev-1' }], execution_budget: { max_subagents: 1 } } as never,
    events: [event(1, 'user/message', { source: { kind: 'user', rpcId: binding.requestId } }),
      event(2, 'tool/call', { callId: 'freeze', name: 'decision_hub_synthesis_submit', arguments: { candidate: value } }),
      event(3, 'tool/result', { callId: 'freeze', isError: true, content: [{ type: 'text', text: 'Error: decision_hub_final_review_required' }] }),
      event(4, 'tool/call', { callId: 'review', name: 'subagent_reviewer', arguments: {} }),
      event(5, 'tool/result', { callId: 'review', result: review }),
      event(6, 'tool/call', { callId: 'final', name: 'decision_hub_synthesis_submit', arguments: { candidate: value } })] }
  return { value, prompt, review, context }
}
const exec = { agent: { id: binding.sessionId }, callId: 'final', signal: new AbortController().signal } as never

describe('versioned decision synthesis native Tool/review', () => {
  it('exposes both complete schemas with resolvable local refs and a bounded visible size', () => {
    const tool = createSynthesisToolDefinition({ reviewContext: async () => fixture().context })
    const schema = tool.parameters as unknown as VisibleSchema
    const resolve = (row: VisibleSchema): VisibleSchema => row.$ref ? resolve(schema.$defs[row.$ref.slice('#/$defs/'.length)]!) : row
    const archived = resolve(schema.oneOf[0]!), candidates = resolve(archived.properties.candidate!)
    expect(resolve(candidates.oneOf[0]!).oneOf).toHaveLength(2)
    const walk = (value: unknown): void => {
      if (!value || typeof value !== 'object') return
      if (Array.isArray(value)) { value.forEach(walk); return }
      const row = value as Record<string, unknown>
      if (typeof row.$ref === 'string') expect(schema.$defs[row.$ref.slice('#/$defs/'.length)]).toBeDefined()
      Object.values(row).forEach(walk)
    }
    walk(schema)
    const bytes = Buffer.byteLength(JSON.stringify(schema))
    expect(bytes).toBeLessThan(64_000)
    console.log(JSON.stringify({ decision_synthesis_visible_schema_bytes: bytes, shared_definitions: Object.keys(schema.$defs).length }))
  })
  it('selects exclusively by exact frozen request ref and refuses cross-mode fallback', () => {
    expect(parseSynthesisCandidate(LEGACY_SYNTHESIS_REF, old, normalizeSynthesisCandidate)).toEqual(old)
    expect(parseSynthesisCandidate('research-session-result.v1', old, normalizeSynthesisCandidate)).toEqual(old)
    expect(() => parseSynthesisCandidate(DECISION_SYNTHESIS_REF, old, normalizeSynthesisCandidate)).toThrow()
    expect(() => parseSynthesisCandidate(LEGACY_SYNTHESIS_REF, candidate(), normalizeSynthesisCandidate)).toThrow()
    expect(() => parseSynthesisCandidate('unknown', candidate(), normalizeSynthesisCandidate)).toThrow('protocol_unsupported')
  })
  it('binds complete wrapper to v2 gate and v3 frozen review', async () => {
    const f = fixture(), providerInput = vi.fn().mockResolvedValue({})
    const tool = createSynthesisToolDefinition({ reviewContext: async () => f.context, providerInput })
    const result = await tool.execute({ candidate: f.value }, exec) as { schema_version: string; candidate: unknown; review_gate: { schema_version: string; status: string; candidate_json: string } }
    expect(result.schema_version).toBe('decision-hub-synthesis-tool-result.v2')
    expect(result.review_gate.schema_version).toBe('decision-hub-final-review-gate.v2')
    expect(result.review_gate.status).toBe('matched')
    expect(result.candidate).toEqual(f.value)
    expect(result.review_gate.candidate_json).toBe(JSON.stringify(f.value))
    expect(providerInput).toHaveBeenCalledWith(f.context, binding.sessionId, 'final', { candidate: f.value }, expect.any(AbortSignal))
    expect(f.prompt.prompt).toContain('decision-hub-frozen-review-input.v2')
    expect(f.prompt.prompt).toContain('every action_intent and condition')
  })
  it.each(['reason', 'threshold', 'mechanism', 'skill'] as const)('does not reuse review after only %s changes', field => {
    const f = fixture(), changed = structuredClone(f.value)
    if (field === 'reason') changed.action_intents[0]!.reason += ' changed'
    if (field === 'threshold') {
      const expression = changed.action_intents[0]!.entry[0]!.expression
      if (expression.op === 'compare') expression.threshold = '81000'
    }
    if (field === 'mechanism') changed.mechanism_reviews[0]!.unresolved.push('another gap')
    if (field === 'skill') changed.skill_consumption_refs.push({ version_id: 'skill-1', version_hash: 'a'.repeat(64), consumption_receipt_ref: 'receipt-1', consumption_receipt_hash: 'b'.repeat(64) })
    f.context.events[5]!.data.arguments = { candidate: changed }
    const result = evaluateFinalReviewGate(f.context, changed, 'final', normalizeSynthesisCandidate)
    expect(result.schema_version).toBe('decision-hub-final-review-gate.v2')
    expect(result.status).toBe('unreviewed')
    expect(result.reason).toBe('frozen_candidate_changed')
    expect(result.review_receipt_hash).toBeNull()
  })
  it('refuses a legacy review wrapper for new candidate and unadmitted action evidence', () => {
    const f = fixture()
    f.review.schema_version = 'decision-hub-frozen-review-result.v2'
    expect(evaluateFinalReviewGate(f.context, f.value, 'final', normalizeSynthesisCandidate).status).toBe('unreviewed')
    const value = candidate(); value.action_intents[0]!.evidence_refs = ['ev-invented']
    const bad = fixture(value)
    expect(() => evaluateFinalReviewGate(bad.context, value, 'final', normalizeSynthesisCandidate)).toThrow('evidence_ref_unattested')
  })
  it('uses only nested old representation repair, never repairs action evidence duplicates', () => {
    const value = candidate(); value.research_candidate_v1.horizons[0]!.evidence_refs.push('ev-1')
    const normalized = parseSynthesisCandidate(DECISION_SYNTHESIS_REF, value, normalizeSynthesisCandidate)
    expect(normalized).toEqual(candidate())
    value.action_intents[0]!.evidence_refs.push('ev-1')
    expect(() => parseSynthesisCandidate(DECISION_SYNTHESIS_REF, value, normalizeSynthesisCandidate)).toThrow()
  })
  it('rejects an expired action review even when old horizons remain current', () => {
    const f = fixture()
    const late = { ...f.context, request: { ...f.context.request, pit_cutoff_at: '2026-10-13T00:11:00Z' } }
    expect(() => evaluateFinalReviewGate(late, f.value, 'final', normalizeSynthesisCandidate)).toThrow('action_review_not_after_reference')
  })
  it('rejects new-mode bare old output before provider proof', async () => {
    const f = fixture(), providerInput = vi.fn()
    const tool = createSynthesisToolDefinition({ reviewContext: async () => f.context, providerInput })
    await expect(tool.execute({ candidate: old }, exec)).rejects.toThrow()
    expect(providerInput).not.toHaveBeenCalled()
  })
  it('launches reviewer with full wrapper and returns frozen result v3', async () => {
    const f = fixture(), dispose = vi.fn()
    const start = vi.fn().mockResolvedValue({ id: 'child-1', dispose, result: Promise.resolve({ stopReason: 'completed', output: [{ type: 'text', text: JSON.stringify(receipt) }] }) })
    const services = { root: {}, readQualified: vi.fn().mockResolvedValue({ context: f.context, candidate: f.value,
      candidate_json: f.prompt.candidate_json, candidate_sha256: f.prompt.candidate_sha256, freeze_call_id: 'freeze', freeze_seq: 2, trusted_evidence_ids: ['ev-1'], evidence_digest: '{}' }),
      receiptMode: vi.fn().mockResolvedValue('independent-review-receipt.v2'), start }
    const tool = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'review', toolFilter: { allow: [] }, maxDepth: 1 }, services)
    const result = await tool.execute({ description: 'review', prompt: 'untrusted' }, exec) as { schema_version: string; candidate_json: string }
    expect(result.schema_version).toBe('decision-hub-frozen-review-result.v3')
    expect(result.candidate_json).toBe(JSON.stringify(f.value))
    expect(start).toHaveBeenCalledOnce(); expect(dispose).toHaveBeenCalledOnce()
  })
})

it('preserves old prompt and candidate hashes captured from the pre-edit source', () => {
  const prompt = buildFrozenReviewerPrompt(old, '{}', 'independent-review-receipt.v2', LEGACY_SYNTHESIS_REF)
  expect(sha(prompt.prompt)).toBe('ac076e29cf519b500f84f8b1451c104a78c7cca2d944c81d907a69b15f27dd60')
  expect(prompt.candidate_sha256).toBe('a29f2aa3f804251f7c5120136088ea94e66035389262d1807fe360c15e81573a')
})

it('provider arguments proof binds action fields even when the old candidate hash stays identical', async () => {
  const f = fixture(), root = {}, args = { candidate: f.value }
  const proof = { schema_version: 'research-provider-tool-emission-recorded.v1', run_id: binding.runId, generation: binding.generation,
    root_session_id: binding.sessionId, execution_session_id: binding.sessionId, dispatch_id: 'control-dispatch',
    call_id: 'final', tool_name: 'decision_hub_synthesis_submit', arguments_hash: sha(providerCanonicalJson(args)),
    raw_arguments_hash: sha(JSON.stringify(args)), candidate_hash: sha(JSON.stringify(old)), proof_scope: 'dsh_llm_generate_options',
    input_ref: { output_id: 'input-control', payload_hash: 'b'.repeat(64) },
    reference: { output_id: 'emit-control', role: 'model_emit:control', schema_version: 'research-provider-tool-emission.v1', payload_hash: 'c'.repeat(64), recorded_at: at } }
  const catalog = { schema_version: 'research-action-catalog.v1', scope_id: scope, instrument: 'BTC-USDT', qualification: 'research_trial_only' }
  const unregister = registerProviderInputReader(root, {
    linkBySession: async () => ({ run_id: binding.runId, generation: binding.generation, dsh_session_id: binding.sessionId, request_hash: binding.requestHash, harness_required: false }),
    providerToolEmission: async () => proof,
    actionSynthesisPreflight: async () => ({ schema_version: 'action-synthesis-preflight.v1', emission: proof, catalog,
      catalog_hash: sha(providerCanonicalJson(catalog)), issues: [], issues_truncated: false }),
  } as never)
  try {
    const signal = new AbortController().signal
    await expect(verifySynthesisProviderInput(root, f.context, binding.sessionId, 'final', args, signal)).resolves.toBeTruthy()
    const altered = structuredClone(args); altered.candidate.action_intents[0]!.reason += ' silently changed'
    await expect(verifySynthesisProviderInput(root, f.context, binding.sessionId, 'final', altered, signal)).rejects.toThrow('emission_identity_mismatch')
  } finally { unregister() }
})

it('preserves old gate JSON and receipt hashes captured from pre-edit source', () => {
  const f = fixture(), prompt = buildFrozenReviewerPrompt(old, '{}', 'independent-review-receipt.v2')
  const context = { ...f.context, request: { ...f.context.request, output_schema_ref: LEGACY_SYNTHESIS_REF } }
  context.events[1]!.data.arguments = { candidate: old }; context.events[5]!.data.arguments = { candidate: old }
  context.events[4]!.data.result = { ...f.review, schema_version: 'decision-hub-frozen-review-result.v2',
    candidate_json: prompt.candidate_json, candidate_sha256: prompt.candidate_sha256, actual_prompt: prompt.prompt, actual_prompt_sha256: sha(prompt.prompt) }
  const current = evaluateFinalReviewGate(context, old, 'final', normalizeSynthesisCandidate)
  expect(sha(JSON.stringify(current))).toBe('488e450bc01d3a8afb43873e2e467a3b387d760d71e44982af551343140bbe96')
  expect(current.review_receipt_hash).toBe('091a5cb89f7c3881909a5aca0988e9783c87fddabd6889c94c7735af0a8153c5')
})

describe('live failure repairs: visible condition contract and immutable review timing', () => {
  function visibleFields() {
    const schema = createSynthesisToolDefinition({ reviewContext: async () => fixture().context }).parameters as unknown as Record<string, any>
    const resolve = (row: any): any => row.$ref ? resolve(schema.$defs[row.$ref.slice('#/$defs/'.length)]) : row
    const archived = resolve(schema.oneOf[0]), candidates = resolve(archived.properties.candidate)
    const value = resolve(resolve(candidates.oneOf[0]).oneOf[1])
    const actions = resolve(resolve(value.properties.action_intents).items).oneOf.map(resolve)
    const mechanism = resolve(resolve(value.properties.mechanism_reviews).items)
    return { resolve, actions, mechanism }
  }
  function liveFixture() {
    const f = fixture()
    f.context.request.execution_mode = 'live'
    f.context.events[5]!.time = Date.parse('2026-10-13T00:11:00Z')
    return f
  }
  it('gives the model the current catalog purposes while preserving historical DTO parsing', () => {
    const { resolve, actions } = visibleFields()
    for (const [name, allowed] of [['entry', ['entry']], ['exit', ['exit', 'risk_upgrade']], ['invalidation', ['invalidation']]] as const) {
      for (const action of actions) {
        const purpose = resolve(resolve(resolve(action.properties[name]).items).properties.purpose)
        expect(purpose.enum).toEqual(name === 'exit' ? ['exit'] : allowed)
      }
      for (const value of ['entry', 'exit', 'risk_upgrade', 'invalidation'] as const) {
        const changed = candidate(), condition = { ...changed.action_intents[0]!.entry[0]!, purpose: value }
        changed.action_intents[0]!.entry = []
        changed.action_intents[0]![name] = [condition]
        if ((allowed as readonly string[]).includes(value)) expect(parseSynthesisCandidate(DECISION_SYNTHESIS_REF, changed, normalizeSynthesisCandidate)).toEqual(changed)
        else expect(() => parseSynthesisCandidate(DECISION_SYNTHESIS_REF, changed, normalizeSynthesisCandidate)).toThrow('condition_binding_invalid')
      }
    }
  })
  it('reports the exact invalidation purpose and allowed correction without mutating the candidate', () => {
    const value = candidate()
    value.action_intents[0]!.invalidation = [{ ...value.action_intents[0]!.entry[0]!, id: 'invalid-1', purpose: 'risk_upgrade' }]
    const original = JSON.stringify(value)
    expect(() => parseSynthesisCandidate(DECISION_SYNTHESIS_REF, value, normalizeSynthesisCandidate)).toThrow('"loc":["action_intents","0","invalidation","0","purpose"]')
    expect(() => parseSynthesisCandidate(DECISION_SYNTHESIS_REF, value, normalizeSynthesisCandidate)).toThrow('"actual":"risk_upgrade","allowed":["invalidation"]')
    expect(JSON.stringify(value)).toBe(original)
  })
  it('describes alternative_refs as admitted evidence IDs and reports the bad causal-link location', () => {
    const { resolve, mechanism } = visibleFields()
    expect(resolve(mechanism.properties.alternative_refs).description).toContain('Evidence IDs')
    expect(resolve(mechanism.properties.alternative_refs).description).toContain('not causal-link IDs')
    const value = candidate(); value.mechanism_reviews[0]!.alternative_refs = ['counter']
    const f = fixture(value)
    expect(() => evaluateFinalReviewGate(f.context, value, 'final', normalizeSynthesisCandidate)).toThrow('alternative_refs')
    expect(() => evaluateFinalReviewGate(f.context, value, 'final', normalizeSynthesisCandidate)).toThrow('ev-1')
  })
  it('resubmits the exact reviewed bytes after review becomes due while keeping real expiry future', async () => {
    const f = liveFixture(), before = JSON.stringify(f.value)
    const result = await createSynthesisToolDefinition({ reviewContext: async () => f.context }).execute({ candidate: f.value }, exec) as { candidate: unknown; review_gate: { status: string; candidate_sha256: string } }
    expect(result.review_gate.status).toBe('matched')
    expect(result.review_gate.candidate_sha256).toBe(sha(before))
    expect(JSON.stringify(result.candidate)).toBe(before)
  })
  it('can start the single reviewer after review-due time using the genuine freeze call', async () => {
    const f = liveFixture(), root = {}
    f.context.events[3]!.time = Date.parse('2026-10-13T00:11:00Z')
    const unregister = registerSynthesisReviewContext(root, async () => f.context, async () => f.context)
    try {
      const qualified = await readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'review', new AbortController().signal)
      expect(qualified.freeze_call_id).toBe('freeze')
      expect(qualified.freeze_seq).toBe(2)
      expect(qualified.candidate_sha256).toBe(sha(JSON.stringify(f.value)))
    } finally { unregister() }
  })
  it('keeps generation-2 continuation bound to the unchanged original reviewed bytes', async () => {
    const f = liveFixture()
    const result = await createSynthesisToolDefinition({ reviewContext: async () => f.context }).execute({ candidate: f.value }, exec)
    const nextBinding = { ...binding, generation: 2, ...deterministicIds(binding.runId, binding.requestHash, 2) }
    const context = { ...f.context, binding: nextBinding, events: [...f.context.events,
      event(7, 'tool/result', { callId: 'final', result }),
      event(8, 'user/message', { source: { kind: 'user', rpcId: nextBinding.requestId } }),
      { ...event(9, 'tool/call', { callId: 'continuation', name: 'decision_hub_synthesis_submit', arguments: { candidate: f.value } }), time: Date.parse('2026-10-13T00:12:00Z') }] }
    expect(evaluateFinalReviewGate(context, f.value, 'continuation', normalizeSynthesisCandidate).status).toBe('matched')
    const changed = structuredClone(f.value)
    for (const horizon of changed.research_candidate_v1.horizons) horizon.next_review_at = '2026-10-13T00:20:00Z'
    for (const action of changed.action_intents) action.next_review_at = '2026-10-13T00:20:00Z'
    context.events[8]!.data.arguments = { candidate: changed }
    expect(evaluateFinalReviewGate(context, changed, 'continuation', normalizeSynthesisCandidate)).toMatchObject({ status: 'unreviewed', reason: 'continuation_candidate_changed', review_receipt_hash: null })
  })
  it('refuses to start the reviewer once the original action has genuinely expired', async () => {
    const f = liveFixture(), root = {}
    f.context.events[3]!.time = Date.parse(until)
    const unregister = registerSynthesisReviewContext(root, async () => f.context, async () => f.context)
    try {
      await expect(readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'review', new AbortController().signal)).rejects.toThrow('action_expired')
    } finally { unregister() }
  })
  it.each(['horizon', 'action', 'condition'] as const)('still rejects genuinely expired %s at the current submit call', kind => {
    const value = candidate(), expires = '2026-10-13T00:11:00Z'
    if (kind === 'horizon') value.research_candidate_v1.horizons[0]!.expires_at = expires
    if (kind === 'action') { value.action_intents[0]!.valid_until = expires; value.action_intents[0]!.entry[0]!.valid_until = expires }
    if (kind === 'condition') value.action_intents[0]!.entry[0]!.valid_until = expires
    const f = fixture(value); f.context.request.execution_mode = 'live'; f.context.events[5]!.time = Date.parse(expires)
    const code = kind === 'horizon' ? 'horizon_expired' : kind === 'action' ? 'action_expired' : 'action_condition_expired'
    expect(() => evaluateFinalReviewGate(f.context, value, 'final', normalizeSynthesisCandidate)).toThrow(code)
  })
  it.each(['missing-freeze-result', 'wrong-review-hash', 'changed-candidate', 'late-original-freeze'] as const)('does not borrow historical admission from %s', change => {
    const f = liveFixture()
    if (change === 'missing-freeze-result') f.context.events[2]!.data = { callId: 'unrelated', result: {} }
    if (change === 'wrong-review-hash') f.review.candidate_sha256 = 'b'.repeat(64)
    if (change === 'changed-candidate') { f.value = structuredClone(f.value); f.value.action_intents[0]!.reason += ' changed'; f.context.events[5]!.data.arguments = { candidate: f.value } }
    if (change === 'late-original-freeze') f.context.events[1]!.time = Date.parse('2026-10-13T00:10:00Z')
    expect(() => evaluateFinalReviewGate(f.context, f.value, 'final', normalizeSynthesisCandidate)).toThrow('review_not_after_reference')
  })
})
