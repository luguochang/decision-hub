// Native event fixtures exercise the engineering gate, not Provider/market quality.
import { createHash } from 'node:crypto'
import { expect, it, vi } from 'vitest'
import { researchSynthesisCandidateSchema, independentReviewReceiptV2Schema } from '@decision-hub/contracts-ts'
import { buildFrozenReviewerPrompt } from '../src/frozen-review-prompt.js'
import { evaluateFinalReviewGate, normalizeSynthesisCandidate, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { LEGACY_SYNTHESIS_REF } from '../src/synthesis-protocol.js'

const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const canonical = (value: unknown): string => JSON.stringify(value, (_key, child) => child && !Array.isArray(child) && typeof child === 'object'
  ? Object.fromEntries(Object.entries(child).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) : child)
const binding = { runId: 'run-controlled-review', requestHash: 'a'.repeat(64), generation: 1,
  ...deterministicIds('run-controlled-review', 'a'.repeat(64)), }
const candidate = researchSynthesisCandidateSchema.parse({ schema_version: 'research-synthesis-candidate.v1', request_id: `research-request:${binding.runId}`,
  causal_case: { case_id: 'case-control', thesis: '受控工程结论', main_chain: [{ link_id: 'main', claim_type: 'fact', statement: '受控事实', evidence_refs: ['ev-control'], confirmation: '核对原件', invalidation: '原件不符', affected_horizons: ['24h'] }],
    opposite_chain: [{ link_id: 'opposite', claim_type: 'scenario', statement: '证据可能不足', evidence_refs: ['ev-control'], confirmation: '缺证', invalidation: '补证', affected_horizons: ['24h'] }], unresolved_questions: [], evidence_refs: ['ev-control'] },
  horizons: ['30m', '24h', '72h'].map(horizon => ({ horizon, action: 'no_trade', subjective_probability: 0.5, probability_status: 'uncalibrated', evidence_refs: ['ev-control'], trigger: '等待', invalidation: '证据变化', expires_at: '2026-10-08T12:00:00Z', next_review_at: '2026-10-08T11:00:00Z', missing_facts: [], confidence_cap_reason: '没有金融校准' })) })
const receipt = independentReviewReceiptV2Schema.parse({ schema_version: 'independent-review-receipt.v2', role: 'independent_reviewer', target_claim: candidate.causal_case!.thesis,
  evidence_refs: ['ev-control'], challenge: '量化资格不足', resolution: 'bounded', limitations: ['没有校准'], reviewer_version: 'decision-hub-frozen-reviewer.v2',
  issues: [{ issue_id: 'risk-1', severity: 'important', blocking_scope: ['direction', 'report'], evidence_refs: ['ev-control'], status: 'deferred', disposition: 'unresolved', resolution_evidence_refs: [], effect_on_direction: 'unknown', effect_on_tail_risk: 'unknown', effect_on_monitor: 'unknown', effect_on_report: 'unknown', reason: '证据不足' }] })
const input = buildFrozenReviewerPrompt(candidate, '{}', 'independent-review-receipt.v2')
const event = (seq: number, type: string, data: Record<string, unknown>) => ({ seq, type, data, time: 0 })
const wrapper = (value: unknown) => ({ schema_version: 'decision-hub-frozen-review-result.v1', binding,
  freeze_call_id: 'freeze', freeze_seq: 2, candidate_json: input.candidate_json, candidate_sha256: input.candidate_sha256,
  expected_target_claim: input.expected_target_claim, actual_prompt: input.prompt, actual_prompt_sha256: sha(input.prompt), child_session_id: 'child-control',
  original_output: { kind: 'foreground', runId: 'child-control', output: [{ type: 'text', text: JSON.stringify(value) }] } })
function context(value: unknown = receipt): SynthesisReviewContext {
  return { binding, admittedEvidence: [], request: { run_id: binding.runId, request_id: candidate.request_id, output_schema_ref: LEGACY_SYNTHESIS_REF,
    execution_mode: 'replay', pit_cutoff_at: '2026-10-08T10:00:00Z',
    allowed_capabilities: [], input_evidence: [{ evidence_id: 'ev-control' }], execution_budget: { max_subagents: 1 } } as never,
    events: [event(1, 'user/message', { source: { kind: 'user', rpcId: binding.requestId } }),
      event(2, 'tool/call', { callId: 'freeze', name: 'decision_hub_synthesis_submit', arguments: { candidate } }),
      event(3, 'tool/result', { callId: 'freeze', name: 'decision_hub_synthesis_submit', isError: true, content: [{ type: 'text', text: 'Error: decision_hub_final_review_required' }] }),
      event(4, 'tool/call', { callId: 'review', name: 'subagent_reviewer', arguments: {} }),
      event(5, 'tool/result', { callId: 'review', name: 'subagent_reviewer', result: wrapper(value) }),
      event(6, 'tool/call', { callId: 'final', name: 'decision_hub_synthesis_submit', arguments: { candidate } })] }
}

it('binds the complete V2 issues hash while preserving unknown effects', () => {
  const gate = evaluateFinalReviewGate(context(), candidate, 'final', normalizeSynthesisCandidate)
  expect(gate.status).toBe('matched')
  expect(gate.review_receipt_hash).toBe(sha(canonical(receipt)))
  const changed = structuredClone(receipt); changed.issues[0]!.reason = '另一份真实反方'
  expect(evaluateFinalReviewGate(context(changed), candidate, 'final', normalizeSynthesisCandidate).review_receipt_hash).not.toBe(gate.review_receipt_hash)
})

it('rejects unadmitted effect refs, missing fourth effects and nonaccepted empty issues', () => {
  const badRef = structuredClone(receipt); badRef.issues[0]!.resolution_evidence_refs = ['not-admitted']
  const missing = structuredClone(receipt) as Record<string, any>; delete missing.issues[0].effect_on_report
  const empty = { ...receipt, issues: [], resolution: 'requires_new_evidence' }
  for (const value of [badRef, missing, empty]) expect(evaluateFinalReviewGate(context(value), candidate, 'final', normalizeSynthesisCandidate).status).toBe('unreviewed')
})

it('preserves V1 and exact accepted-empty V2 without inventing any effects', () => {
  const { issues: _issues, reviewer_version: _version, ...summary } = receipt
  expect(evaluateFinalReviewGate(context({ ...summary, schema_version: 'independent-review-receipt.v1' }), candidate, 'final', normalizeSynthesisCandidate).status).toBe('matched')
  expect(evaluateFinalReviewGate(context({ ...receipt, resolution: 'accepted', issues: [] }), candidate, 'final', normalizeSynthesisCandidate).status).toBe('matched')
})

it('uses the authenticated version once and disposes the existing foreground reviewer', async () => {
  const start = vi.fn().mockResolvedValue({ id: 'child-control', result: Promise.resolve({ stopReason: 'completed', output: [{ type: 'text', text: JSON.stringify(receipt) }] }), dispose: vi.fn() })
  const services = { root: {}, receiptMode: vi.fn().mockResolvedValue('independent-review-receipt.v2'), start,
    readQualified: vi.fn().mockResolvedValue({ context: context(), candidate, candidate_json: input.candidate_json,
      candidate_sha256: input.candidate_sha256, freeze_call_id: 'freeze', freeze_seq: 2, evidence_digest: '{}', trusted_evidence_ids: ['ev-control'] }) }
  const config = { provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'controlled-reviewer', toolFilter: { allow: [] }, maxDepth: 1 } as const
  const tool = createFrozenReviewerToolDefinition(config as never, services as never)
  const exec = { agent: {}, callId: 'review', signal: new AbortController().signal } as never
  const result = await tool.execute({ description: '审查', prompt: '模型输入不能换候选' }, exec) as ReturnType<typeof wrapper>
  expect(result.actual_prompt).toContain('Return only one independent-review-receipt.v2')
  expect(start).toHaveBeenCalledTimes(1)
  expect((await start.mock.results[0]!.value).dispose).toHaveBeenCalledTimes(1)
  services.receiptMode.mockRejectedValueOnce(new Error('authenticated-link-mismatch'))
  await expect(tool.execute({ description: '审查', prompt: '请求' }, exec)).rejects.toThrow('authenticated-link-mismatch')
  expect(start).toHaveBeenCalledTimes(1)
})
