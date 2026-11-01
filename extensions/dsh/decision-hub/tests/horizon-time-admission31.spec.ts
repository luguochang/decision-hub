// Draft, NOT EXECUTED. Native engineering gate control; no HTTP/DB/model or financial proof.
import { expect, it, vi } from 'vitest'
import { independentReviewReceiptV2Schema, researchSessionRequestSchema, researchSynthesisCandidateSchema, type ResearchSynthesisCandidate } from '@decision-hub/contracts-ts'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { horizonTimeAdmission, qualifiedFrozenCandidate, readQualifiedReviewerContext, registerSynthesisReviewContext, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import type { SessionEventLike } from '../src/host/dsh-public.js'

const submittedAt = Date.parse('2026-10-09T06:47:10.837Z')
const binding = { runId: 'run-horizon-time-control31', requestHash: 'a'.repeat(64), generation: 1,
  ...deterministicIds('run-horizon-time-control31', 'a'.repeat(64)) }
function request(mode: 'live' | 'replay' = 'live', cutoff = '2026-10-09T07:24:07Z') {
  return researchSessionRequestSchema.parse({ schema_version: 'research-session-request.v1', request_id: `research-request:${binding.runId}`,
    run_id: binding.runId, event_id: 'control31', trigger_snapshot_id: 'control-trigger31', domain_pack_ref: 'control', role_profile_ref: 'control',
    execution_mode: mode, research_mode: mode === 'replay' ? 'historical_review' : 'current_decision', pit_cutoff_at: cutoff, current_round: 1, evidence_refs: ['ev-control31'],
    input_evidence: [{ evidence_id: 'ev-control31', kind: 'market', authority: 'exchange', source_id: 'control', source_url: null,
      published_at: null, observed_at: mode === 'replay' ? '2026-10-08T23:00:00Z' : '2026-10-09T06:24:07Z',
      received_at: mode === 'replay' ? '2026-10-08T23:00:00Z' : '2026-10-09T06:24:07Z', content_hash: 'b'.repeat(64), excerpt: '工程控制原件' }],
    evidence_requirements: [{ requirement_id: 'control_fact', description: '工程材料', importance: 'hard', source_priority: ['control'], authority_floor: 'exchange',
      preferred_capabilities: ['official.macro'], freshness_seconds: 3600, minimum_independent_sources: 1, allowed_fallbacks: [], confidence_cap: .5 }],
    target_gaps: [], allowed_capabilities: ['official.macro'], execution_budget: { max_evidence_rounds: 1, max_tool_calls: 8, max_subagents: 1,
      total_deadline_seconds: 3600, per_tool_timeout_seconds: 60, per_model_step_timeout_seconds: 120, max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: mode === 'replay' ? '2030-01-01T01:00:00Z' : '2026-10-09T07:24:07Z', output_schema_ref: 'agentic_research.schema.yaml#/$defs/research_session_result', repair_instructions: null })
}
function candidate(pastDailyReview = false): ResearchSynthesisCandidate {
  return researchSynthesisCandidateSchema.parse({ schema_version: 'research-synthesis-candidate.v1', request_id: request().request_id,
    causal_case: { case_id: 'case-control31', thesis: '工程控制中金融结论仍受保留', evidence_refs: ['ev-control31'], unresolved_questions: [],
      main_chain: [{ link_id: 'main', claim_type: 'fact', statement: '已有受控材料', evidence_refs: ['ev-control31'], confirmation: '核对原件', invalidation: '原件不符', affected_horizons: ['24h'] }],
      opposite_chain: [{ link_id: 'opposite', claim_type: 'scenario', statement: '反方问题尚未回答', evidence_refs: ['ev-control31'], confirmation: '出现反证', invalidation: '独立补证', affected_horizons: ['24h'] }] },
    horizons: [['30m', '2026-10-09T06:55:00Z', '2026-10-09T06:55:00Z'],
      ['24h', '2026-10-10T06:25:00Z', pastDailyReview ? '2026-10-09T00:00:00Z' : '2026-10-10T00:00:00Z'],
      ['72h', '2026-10-12T06:25:00Z', '2026-10-10T00:00:00Z']].map(([horizon, expires_at, next_review_at]) => ({ horizon, expires_at, next_review_at,
      action: 'no_trade', subjective_probability: .5, probability_status: 'uncalibrated', evidence_refs: ['ev-control31'], trigger: `等待${horizon}确认`,
      invalidation: `材料不再支持${horizon}`, missing_facts: [], confidence_cap_reason: '没有金融校准' })) })
}
const event = (seq: number, type: string, data: Record<string, unknown>, time = submittedAt): SessionEventLike => ({ seq, type, data, time })
const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
function fixture(mode: 'live' | 'replay' = 'live', cutoff?: string) {
  const nativeAt = mode === 'replay' ? Date.parse('2030-01-01T00:00:00Z') : submittedAt
  const events = [event(1, 'user/message', { source: { kind: 'user', rpcId: binding.requestId } }, nativeAt - 1)]
  const context = (): SynthesisReviewContext => ({ binding, request: request(mode, cutoff), admittedEvidence: [], events })
  const call = (id: string, name: string, args: unknown, time = nativeAt) => {
    const item = event(events.length + 1, 'tool/call', { callId: id, name, arguments: args }, time); events.push(item); return item
  }
  const error = (id: string, message: string, callSeq: number) => {
    const resultAt = (events.find(item => item.seq === callSeq)?.time ?? submittedAt) + 1
    events.push({ ...event(events.length + 1, 'tool/result', { message: { id: `message-${id}`, source: { kind: 'tool', callId: id },
      content: [{ type: 'tool-result', toolCallId: id, isError: true, content: [{ type: 'text', text: `Error: ${message}` }] }] } }, resultAt),
      surfaceOp: 'append', sourceEventSeqs: [callSeq] } as SessionEventLike)
  }
  const tool = createSynthesisToolDefinition({ reviewContext: async () => context() })
  return { events, context, call, error, tool }
}

it('rejects the real 31 past 24h review before freeze and spends only one reviewer after a corrected candidate', async () => {
  const f = fixture(), stale = candidate(true), staleBytes = JSON.stringify(stale), ready = candidate(), root = {}
  const first = f.call('past', 'decision_hub_synthesis_submit', { candidate: stale })
  let rejected = ''
  try { await f.tool.execute({ candidate: stale }, exec('past')) } catch (error) { rejected = (error as Error).message }
  expect(rejected).toContain('decision_hub_horizon_time_invalid:')
  expect(rejected).toContain('2026-10-09T06:47:10.837Z')
  expect(rejected).toContain('horizon_review_not_after_reference')
  expect(rejected).not.toContain('decision_hub_final_review_required')
  f.error('past', rejected, first.seq)
  expect(qualifiedFrozenCandidate(f.context(), f.events, ['ev-control31'])).toBeNull()
  const freeze = f.call('freeze', 'decision_hub_synthesis_submit', { candidate: ready }, submittedAt + 10)
  await expect(f.tool.execute({ candidate: ready }, exec('freeze'))).rejects.toThrow('decision_hub_final_review_required')
  f.error('freeze', 'decision_hub_final_review_required', freeze.seq)
  const receipt = independentReviewReceiptV2Schema.parse({ schema_version: 'independent-review-receipt.v2', role: 'independent_reviewer',
    target_claim: ready.causal_case!.thesis, evidence_refs: ['ev-control31'], challenge: '风险资格与反方仍未解决', resolution: 'bounded',
    limitations: ['没有金融校准'], reviewer_version: 'decision-hub-frozen-reviewer.v2', issues: [{ issue_id: 'risk-control31', severity: 'important',
      blocking_scope: ['direction', 'report'], evidence_refs: ['ev-control31'], status: 'open', disposition: 'unresolved', resolution_evidence_refs: [],
      effect_on_direction: 'unknown', effect_on_tail_risk: 'unknown', effect_on_monitor: 'unknown', effect_on_report: 'unknown', reason: '时间合法不回答该反方' }] })
  const dispose = vi.fn(), start = vi.fn().mockResolvedValue({ id: 'child-control31', dispose,
    result: Promise.resolve({ stopReason: 'completed', output: [{ type: 'text', text: JSON.stringify(receipt) }] }) })
  const unregister = registerSynthesisReviewContext(root, async () => f.context(), async () => f.context())
  const reviewer = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot',
    persona: 'control', toolFilter: { allow: [] }, maxDepth: 1 }, { root, start, readQualified: readQualifiedReviewerContext,
    receiptMode: async () => 'independent-review-receipt.v2' })
  try {
    f.call('review', 'subagent_reviewer', {}, submittedAt + 1000)
    const wrapped = await reviewer.execute({ description: '受控复核', prompt: '不能改候选' }, exec('review'))
    f.events.push(event(f.events.length + 1, 'tool/result', { callId: 'review', name: 'subagent_reviewer', result: wrapped }, submittedAt + 1001))
    f.call('final', 'decision_hub_synthesis_submit', { candidate: ready }, submittedAt + 2000)
    const result = await f.tool.execute({ candidate: ready }, exec('final')) as { candidate: ResearchSynthesisCandidate; review_gate: { status: string } }
    expect(result.review_gate.status).toBe('matched') // Exact bounded reviewer receipt, not financial acceptance.
    expect(result.candidate).toEqual(ready)
    expect(receipt.resolution).toBe('bounded'); expect(receipt.issues[0]!.status).toBe('open')
    expect(result.candidate.horizons.every(item => item.action === 'no_trade' && item.probability_status === 'uncalibrated')).toBe(true)
    f.call('second-review', 'subagent_reviewer', {}, submittedAt + 3000)
    await expect(reviewer.execute({ description: '禁止第二次', prompt: '禁止第二次' }, exec('second-review'))).rejects.toThrow('decision_hub_reviewer_budget_used')
    expect(start).toHaveBeenCalledTimes(1); expect(dispose).toHaveBeenCalledTimes(1)
    f.call('expired-final', 'decision_hub_synthesis_submit', { candidate: ready }, Date.parse('2026-10-09T06:55:00Z'))
    await expect(f.tool.execute({ candidate: ready }, exec('expired-final'))).rejects.toThrow('horizon_review_not_after_reference')
    expect(start).toHaveBeenCalledTimes(1); expect(JSON.stringify(result.candidate)).toBe(JSON.stringify(ready))
    expect(JSON.stringify(stale)).toBe(staleBytes)
    expect(f.context().request.execution_budget.max_subagents).toBe(1)
  } finally { unregister() }
})

it('checks UTC offsets, missing zones and exact boundaries through the production submit gate', async () => {
  for (const change of ['offset', 'naive', 'equal-reference', 'after-expiry', 'equal-expiry'] as const) {
    const value = structuredClone(candidate())
    if (change === 'offset') { value.horizons[0]!.next_review_at = '2026-10-09T14:55:00+08:00'; value.horizons[0]!.expires_at = '2026-10-09T14:55:00+08:00' }
    if (change === 'naive') value.horizons[0]!.next_review_at = '2026-10-09T06:55:00'
    if (change === 'equal-reference') value.horizons[0]!.next_review_at = '2026-10-09T06:47:10.837Z'
    if (change === 'after-expiry') value.horizons[0]!.next_review_at = '2026-10-09T06:55:00.000001Z'
    if (change === 'equal-expiry') value.horizons[0]!.next_review_at = value.horizons[0]!.expires_at
    const f = fixture(); f.call('submit', 'decision_hub_synthesis_submit', { candidate: value })
    if (change === 'offset' || change === 'equal-expiry') await expect(f.tool.execute({ candidate: value }, exec('submit'))).rejects.toThrow('decision_hub_final_review_required')
    else await expect(f.tool.execute({ candidate: value }, exec('submit'))).rejects.toThrow()
    expect(qualifiedFrozenCandidate(f.context(), f.events, ['ev-control31'])).toBeNull() // No tool result = no qualified freeze.
  }
})

it('keeps replay on the original cutoff while live uses the durable native call rather than the future deadline', async () => {
  const stale = candidate(true), historical = fixture('replay', '2026-10-08T23:59:59.999999Z')
  const replayCall = historical.call('submit', 'decision_hub_synthesis_submit', { candidate: stale }, Date.parse('2030-01-01T00:00:00Z'))
  expect(horizonTimeAdmission(historical.context(), stale, replayCall).issues).toEqual([])
  await expect(historical.tool.execute({ candidate: stale }, exec('submit'))).rejects.toThrow('decision_hub_final_review_required')
  const live = fixture(), call = live.call('submit', 'decision_hub_synthesis_submit', { candidate: candidate() })
  const check = horizonTimeAdmission(live.context(), candidate(), call)
  expect(check.reference_at).toBe('2026-10-09T06:47:10.837Z'); expect(check.issues).toEqual([])
  expect(check.reference_at).not.toBe(live.context().request.deadline_at)
  const precise = candidate(), micros = fixture('replay', '2026-10-09T06:47:10.837001Z')
  precise.horizons[0]!.next_review_at = '2026-10-09T06:47:10.837002Z'
  micros.call('precise', 'decision_hub_synthesis_submit', { candidate: precise })
  await expect(micros.tool.execute({ candidate: precise }, exec('precise'))).rejects.toThrow('decision_hub_final_review_required')
  call.time = Number.NaN
  expect(horizonTimeAdmission(live.context(), candidate(), call).issues).toContainEqual({ loc: ['reference_at'], code: 'horizon_reference_timezone_invalid' })
})

it('keeps an uncertain pre-launch failure spent instead of trusting its quoted time error', async () => {
  const f = fixture(), root = {}, short = candidate()
  short.horizons[0]!.next_review_at = '2026-10-09T06:47:11.837Z'
  const freeze = f.call('freeze', 'decision_hub_synthesis_submit', { candidate: short })
  await expect(f.tool.execute({ candidate: short }, exec('freeze'))).rejects.toThrow('decision_hub_final_review_required')
  f.error('freeze', 'decision_hub_final_review_required', freeze.seq)
  const unregister = registerSynthesisReviewContext(root, async () => f.context(), async () => f.context())
  try {
    const review = f.call('uncertain-review', 'subagent_reviewer', {}, submittedAt + 2000)
    let rejected = ''
    try { await readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'uncertain-review', new AbortController().signal) } catch (error) { rejected = (error as Error).message }
    f.error('uncertain-review', rejected, review.seq)
    const last = f.events.at(-1)! as SessionEventLike & { sourceEventSeqs: number[] }
    last.sourceEventSeqs = [] // Remove the official call/result proof while keeping the quoted error.
    const ready = candidate(); f.call('renewed', 'decision_hub_synthesis_submit', { candidate: ready }, submittedAt + 3000)
    const blocked = await f.tool.execute({ candidate: ready }, exec('renewed')) as { review_gate: { reason: string } }
    expect(blocked.review_gate.reason).toBe('review_attempt_used')
    f.call('second', 'subagent_reviewer', {}, submittedAt + 4000)
    await expect(readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'second', new AbortController().signal)).rejects.toThrow('decision_hub_reviewer_budget_used')
  } finally { unregister() }
})

it('refuses a freeze that expires while waiting before child start and allows a proved pre-launch correction', async () => {
  const f = fixture(), root = {}, short = candidate()
  short.horizons[0]!.next_review_at = '2026-10-09T06:47:11.837Z'
  const freeze = f.call('freeze', 'decision_hub_synthesis_submit', { candidate: short })
  await expect(f.tool.execute({ candidate: short }, exec('freeze'))).rejects.toThrow('decision_hub_final_review_required')
  f.error('freeze', 'decision_hub_final_review_required', freeze.seq)
  const unregister = registerSynthesisReviewContext(root, async () => f.context(), async () => f.context())
  try {
    const review = f.call('too-late-review', 'subagent_reviewer', {}, submittedAt + 2000)
    let rejected = ''
    try { await readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'too-late-review', new AbortController().signal) } catch (error) { rejected = (error as Error).message }
    expect(rejected).toContain('decision_hub_horizon_time_invalid:'); f.error('too-late-review', rejected, review.seq)
    const ready = candidate(), renewed = f.call('renewed-freeze', 'decision_hub_synthesis_submit', { candidate: ready }, submittedAt + 3000)
    await expect(f.tool.execute({ candidate: ready }, exec('renewed-freeze'))).rejects.toThrow('decision_hub_final_review_required')
    f.error('renewed-freeze', 'decision_hub_final_review_required', renewed.seq)
    f.call('qualified-review', 'subagent_reviewer', {}, submittedAt + 4000)
    const qualified = await readQualifiedReviewerContext(root, { id: binding.sessionId } as never, 'qualified-review', new AbortController().signal)
    expect(qualified.freeze_call_id).toBe('renewed-freeze')
    expect(qualified.candidate).toEqual(ready)
  } finally { unregister() }
})
