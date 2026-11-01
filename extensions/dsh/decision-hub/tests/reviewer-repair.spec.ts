// Synthetic SDK lifecycle controls. No Provider/financial/model-understanding proof.
import { createHash } from 'node:crypto'
import { expect, it, vi } from 'vitest'
import { researchSynthesisCandidateSchema, independentReviewReceiptV2Schema } from '@decision-hub/contracts-ts'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { buildFrozenReviewerPrompt } from '../src/frozen-review-prompt.js'
import { evaluateFinalReviewGate, normalizeSynthesisCandidate, protectedRootReviewBodies, validateFrozenReviewerOutput, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { LEGACY_SYNTHESIS_REF } from '../src/synthesis-protocol.js'

const sha = (text: string) => createHash('sha256').update(text).digest('hex')
const binding = { runId: 'run-repair-control', requestHash: 'a'.repeat(64), generation: 1, ...deterministicIds('run-repair-control', 'a'.repeat(64)) }
const candidate = researchSynthesisCandidateSchema.parse({ schema_version: 'research-synthesis-candidate.v1', request_id: `research-request:${binding.runId}`,
  causal_case: { case_id: 'repair-control', thesis: '保留缺证的工程控制', main_chain: [{ link_id: 'main', claim_type: 'fact', statement: '已有受控原件', evidence_refs: ['ev-control'], confirmation: '核原件', invalidation: '原件不符', affected_horizons: ['24h'] }],
    opposite_chain: [{ link_id: 'other', claim_type: 'scenario', statement: '风险仍未知', evidence_refs: ['ev-control'], confirmation: '补证', invalidation: '证据不同', affected_horizons: ['24h'] }], unresolved_questions: [], evidence_refs: ['ev-control'] },
  horizons: ['30m','24h','72h'].map(horizon => ({ horizon, action: 'no_trade', subjective_probability: .5, probability_status: 'uncalibrated', evidence_refs: ['ev-control'], trigger: '等待', invalidation: '证据变化', expires_at: '2026-10-08T20:00:00Z', next_review_at: '2026-10-08T19:00:00Z', missing_facts: [], confidence_cap_reason: '未经校准' })) })
const valid = independentReviewReceiptV2Schema.parse({ schema_version: 'independent-review-receipt.v2', role: 'independent_reviewer', target_claim: candidate.causal_case!.thesis, evidence_refs: ['ev-control'], challenge: '未知保留', resolution: 'bounded', limitations: ['没有金融资格'], reviewer_version: 'decision-hub-frozen-reviewer.v2',
  issues: [{ issue_id: 'scope-risk', severity: 'important', blocking_scope: ['direction'], evidence_refs: ['ev-control'], status: 'open', disposition: 'unresolved', resolution_evidence_refs: [], effect_on_direction: 'unknown', effect_on_tail_risk: 'unknown', effect_on_monitor: 'unknown', effect_on_report: 'unknown', reason: '风险没有证据回答' }] })
const malformed = { ...valid, issues: [{ ...valid.issues[0], blocking_scope: ['direction','monitor'] }] }
const text = (value: unknown) => [{ type: 'text', text: JSON.stringify(value) }]
const e = (seq: number, type: string, data: Record<string, unknown>) => ({ seq, type, data, time: 0 })
const prompt = buildFrozenReviewerPrompt(candidate, '{}', 'independent-review-receipt.v2')
function context(wrapper?: unknown): SynthesisReviewContext {
  const events = [e(1,'user/message',{source:{kind:'user',rpcId:binding.requestId}}), e(2,'tool/call',{callId:'freeze',name:'decision_hub_synthesis_submit',arguments:{candidate}}), e(3,'tool/result',{callId:'freeze',name:'decision_hub_synthesis_submit',isError:true,content:[{type:'text',text:'Error: decision_hub_final_review_required'}]}), e(4,'tool/call',{callId:'review',name:'subagent_reviewer',arguments:{}})]
  if (wrapper) events.push(e(5,'tool/result',{callId:'review',name:'subagent_reviewer',result:wrapper}),e(6,'tool/call',{callId:'final',name:'decision_hub_synthesis_submit',arguments:{candidate}}))
  return {binding,admittedEvidence:[],events,request:{run_id:binding.runId,request_id:candidate.request_id,output_schema_ref:LEGACY_SYNTHESIS_REF,execution_mode:'replay',pit_cutoff_at:'2026-10-08T18:00:00Z',input_evidence:[{evidence_id:'ev-control'}],execution_budget:{max_subagents:1}} as never}
}
function setup(second: unknown = valid, first: unknown = malformed, parent = binding.sessionId) {
  const events: ReturnType<typeof e>[] = []
  const followup = vi.fn((message: Record<string,unknown>) => events.push(e(10,'turn/start',{turn:1}),e(11,'user/message',message),e(12,'step/start',{turn:1})))
  const child = { id:'child-repair',status:'idle',session:{header:{parentSession:parent},events},followup,cancel:vi.fn(),whenIdle:vi.fn(async()=>{events.push(e(13,'assistant/message',{message:{content:text(second)}}),e(14,'turn/end',{turn:1,reason:{kind:'completed'}}))}) }
  const dispose=vi.fn(),run={id:'child-repair',localAgent:child,result:Promise.resolve({stopReason:'completed',output:text(first)}),dispose}
  const start=vi.fn().mockResolvedValue(run)
  const qualified={context:context(),candidate,candidate_json:prompt.candidate_json,candidate_sha256:prompt.candidate_sha256,freeze_call_id:'freeze',freeze_seq:2,evidence_digest:'{}',trusted_evidence_ids:['ev-control']}
  const services={root:{},receiptMode:vi.fn().mockResolvedValue('independent-review-receipt.v2'),start,readQualified:vi.fn().mockResolvedValue(qualified)}
  const tool=createFrozenReviewerToolDefinition({provider:'spawn',toolName:'subagent_reviewer',backgroundMode:'one-shot',persona:'control',toolFilter:{allow:[]},maxDepth:1},services as never)
  const controller=new AbortController(); const execute=()=>tool.execute({description:'控制',prompt:'不能替代原候选'}, {agent:{},callId:'review',signal:controller.signal} as never)
  return {tool,execute,controller,services,start,followup,dispose,child}
}
it('reports actual invalid monitor enum safely instead of discarding its finding',()=>{
  const checked=validateFrozenReviewerOutput(text(malformed),'independent-review-receipt.v2',valid.target_claim,new Set(['ev-control']))
  expect(checked.receipt).toBeNull();expect(checked.errors.join(' ')).toContain('issues.0.blocking_scope.1:invalid_enum_value')
  expect(checked.errors.join(' ')).toContain('direction|path_risk|execution|report')
})
it('uses exactly one same-child correction and binds full valid receipt plus first raw output',async()=>{
  const s=setup();const result=await s.execute() as any
  expect(s.start).toHaveBeenCalledTimes(1);expect(s.followup).toHaveBeenCalledTimes(1);expect(s.dispose).toHaveBeenCalledTimes(1)
  expect(result.schema_version).toBe('decision-hub-frozen-review-result.v2');expect(result.schema_repair.invalid_output_sha256).toBe(sha(result.schema_repair.invalid_output_json))
  expect(JSON.parse(result.schema_repair.invalid_output_json).output).toEqual(text(malformed));expect(result.original_output.output).toEqual(text(valid))
  expect(evaluateFinalReviewGate(context(result),candidate,'final',normalizeSynthesisCandidate).status).toBe('matched')
  const bodies=protectedRootReviewBodies(context(result));expect(bodies).toHaveLength(1);expect(bodies[0]!.receipt).toEqual(valid);expect(bodies[0]!.review_call_id).toBe('review')
  expect(bodies[0]!.receipt.issues[0]!.effect_on_monitor).toBe('unknown')
})
it('stops after the second malformed receipt with no extra child or loop',async()=>{
  const s=setup(malformed);await expect(s.execute()).rejects.toThrow('decision_hub_reviewer_schema_repair_invalid')
  expect(s.start).toHaveBeenCalledTimes(1);expect(s.followup).toHaveBeenCalledTimes(1);expect(s.dispose).toHaveBeenCalledTimes(1)
})
it('refuses a changed child-parent identity before repair and still disposes',async()=>{
  const s=setup(valid,malformed,'another-root');await expect(s.execute()).rejects.toThrow('repair_child_identity_invalid')
  expect(s.followup).not.toHaveBeenCalled();expect(s.dispose).toHaveBeenCalledTimes(1)
})
it('cancels same-child repair and joins cleanup without producing a receipt',async()=>{
  const s=setup();s.child.whenIdle.mockImplementationOnce(async()=>{s.controller.abort();})
  await expect(s.execute()).rejects.toThrow();expect(s.child.cancel).toHaveBeenCalledWith({kind:'parent'});expect(s.dispose).toHaveBeenCalledTimes(1)
})
it('rejects a corrected receipt targeting another candidate without changing it',async()=>{
  const s=setup({...valid,target_claim:'其他候选'});await expect(s.execute()).rejects.toThrow('target_claim:frozen_target_mismatch')
  expect(s.followup).toHaveBeenCalledTimes(1);expect(s.dispose).toHaveBeenCalledTimes(1)
})
it('keeps already valid V2 at one child and no repair, and V1 legacy envelope unchanged',async()=>{
  const s=setup(valid,valid);const result=await s.execute() as any;expect(result.schema_repair).toBeNull();expect(s.followup).not.toHaveBeenCalled()
  const old=setup();old.services.receiptMode.mockResolvedValueOnce('independent-review-receipt.v1');const legacy=await old.execute() as any
  expect(legacy.schema_version).toBe('decision-hub-frozen-review-result.v1');expect(legacy).not.toHaveProperty('schema_repair');expect(old.followup).not.toHaveBeenCalled();expect(old.dispose).toHaveBeenCalledTimes(1)
})
it('rejects tampered repair suffix even if it quotes a valid receipt',async()=>{
  const s=setup();const result=await s.execute() as any;result.schema_repair.events_json+=' '
  expect(evaluateFinalReviewGate(context(result),candidate,'final',normalizeSynthesisCandidate).status).toBe('unreviewed')
  expect(protectedRootReviewBodies(context(result))).toEqual([])
})
