import { bodyHash } from '../src/perpetual-synthesis-protocol.js'
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import * as gate from '../src/final-review-gate.js'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { canonicalJson, textHash } from '../src/perpetual-synthesis-protocol.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { PerpetualReviewModelViews } from '../src/reviewer-model-view.js'
import { apply as applyReviewer } from '../src/reviewer-tool.js'


const instruction = (id: string, seq = 0): any => ({seq, type:'user/message', data:{role:'user',id,
  source:{kind:'agent-instructions',form:'instructions',baseline:true,baselineIdentity:'synthetic-config',
    changes:[{action:'set',scope:'.',path:'AGENTS.md',digest:'a'.repeat(40)}]},
  content:[{type:'text',text:'Synthetic workspace instructions.'}]}})

async function reviewed(verdict: 'approved' | 'needs_revision' | 'rejected' = 'approved', extraUser = false, reviewIssues?: unknown[]) {
  const { draft, options } = JSON.parse(readFileSync(new URL('fixtures/perpetual-preparation.json', import.meta.url), 'utf8'))
  const runId = options.review_originals.run_id, requestHash = options.review_originals.request_hash
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash, 1) }
  const at = Date.parse(options.requested_at)
  const context: any = { binding, perpetualBinding: options.binding, perpetualReviewOriginals: options.review_originals,
    request: { request_id: options.request_id, run_id: runId, output_schema_ref: 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',
      allowed_capabilities: [], input_evidence: [], execution_mode: 'live', execution_budget: { max_subagents: 4 } }, admittedEvidence: [],
    events: [{ seq: 1, time: at, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } },
      { seq: 2, time: at, type: 'tool/call', data: { callId: 'prepare', name: 'decision_hub_synthesis_submit', arguments: { action: 'prepare', request_id: options.request_id, draft } } }] }
  const exec = (callId: string) => ({ agent: { id: binding.sessionId }, callId, signal: new AbortController().signal } as never)
  const synthesis = createSynthesisToolDefinition({ reviewContext: async () => context })
  const prepared: any = await synthesis.execute(context.events[1].data.arguments, exec('prepare'))
  context.events.push({ seq: 3, time: at, type: 'tool/result', data: { callId: 'prepare', value: prepared } },
    { seq: 4, time: at + 1000, type: 'tool/call', data: { callId: 'review', name: 'subagent_reviewer', arguments: {} } })
  const root = {}, unregister = gate.registerSynthesisReviewContext(root, async () => context, async () => context)
  context.events.unshift(instruction('parent'))
  const modelViews = new PerpetualReviewModelViews()
  let originalEvents: unknown[] = [], disposed = false, persona = ''
  const reviewer = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot', persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 },
    { root, readQualified: gate.readQualifiedReviewerContext, rememberModelView: (callId, result) => modelViews.remember(callId, result), async start(_provider, start): Promise<any> {
      persona = start.persona ?? ''
      const prompt = (start.prompt[0] as any).text, lines = prompt.split('\n')
      const authorization = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]); authorization.verdict = verdict
      const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]); findings.summary = 'Synthetic independent review.'
      if (verdict !== 'approved') findings.issues = reviewIssues ?? [{ issue_id: 'finding_1', candidate_ids: [], field_paths: ['/analysis/thesis'], concern: 'Synthetic unsupported claim.', requested_change: 'State the uncertainty.', evidence_refs: [] }]
      const output = [{ type: 'text', text: JSON.stringify([authorization, findings]) }]
      // Official SDK assistant/chunk envelopes can exceed 1MB even with a
      // modest token-sized stream. These controlled chunks contain no live data.
      originalEvents = [{ seq: 0, time: at + 1000, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] } },
        ...Array.from({ length: 8000 }, (_, index) => ({ seq: index + 1, time: at + 1001 + index, type: 'assistant/chunk',
          data: { turn: 0, step: 0, chunk: { type: 'reasoning-delta', index: 0, text: '.' } } })),
        { seq: 8001, time: at + 9001, type: 'assistant/message', data: { message: { content: output } } },
        { seq: 8002, time: at + 9002, type: 'turn/end', data: { turn: 0, reason: { kind: 'completed' } } }]
      originalEvents.splice(1, 0, instruction('child', 1))
      if (extraUser) originalEvents.splice(1, 0, { seq: 0.5, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Additional instructions are not authorized.' }] } })
      expect(JSON.stringify(originalEvents).length).toBeGreaterThan(1_000_000)
      return { id: 'independent-child', localAgent: { id: 'independent-child', session: { header: { parentSession: binding.sessionId }, events: originalEvents } },
        result: Promise.resolve({ stopReason: 'completed', output }), dispose: async () => { disposed = true } }
    } })
  try {
    const wrapper: any = await reviewer.execute({ description: 'Independent review', prompt: 'ignored', run_in_background: false }, exec('review'))
    const content = await reviewer.finalizeContent!(exec('review'), { value: wrapper, isError: false } as never)
    const result: any = { seq: 5, time: at + 10000, type: 'tool/result', data: { callId: 'review', value: wrapper } }
    context.events.push(result)
    const finalize = { action: 'finalize', request_id: options.request_id, prepared_call_id: 'prepare' }
    context.events.push({ seq: 6, time: at + 11000, type: 'tool/call', data: { callId: 'finalize', name: 'decision_hub_synthesis_submit', arguments: finalize } })
    return { context, prepared, wrapper, result, content, originalEvents, modelViews, disposed, persona, finalize: () => synthesis.execute(finalize, exec('finalize')) }
  } finally { unregister() }
}


it('authenticates parent baseline on production and consumption without changing native bytes or negative findings', async () => {
  const value = await reviewed('needs_revision')
  expect(JSON.parse(value.wrapper.child_events_json)).toEqual(value.originalEvents)
  expect(value.wrapper.child_events_sha256).toBe(textHash(JSON.stringify(value.originalEvents)))
  const output = JSON.parse(value.wrapper.original_output.output[0].text)
  expect(output[0].verdict).toBe('needs_revision')
  expect(output[1].issues).toHaveLength(1)
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
  // Even a later changed native instruction must not replace the original call expectation.
  const later=instruction('later',5); later.data.source.baseline=false
  value.context.events.push(later)
  await expect(value.finalize()).rejects.toThrow('review_not_approved')
})
it.each(['missing','mutated','ambiguous'])('consumer rejects %s parent proof after a valid producer result', async mode => {
  const value = await reviewed('needs_revision')
  if(mode==='missing') value.context.events.shift()
  if(mode==='mutated') value.context.events[0].data.content[0].text += ' changed'
  if(mode==='ambiguous') value.context.events.unshift(instruction('duplicate'))
  expect((await value.finalize() as any).review_gate.status).toBe('unreviewed')
})
it('consumer rejects changed child baseline even when its hash is recomputed', async () => {
  const value = await reviewed('needs_revision'), events=JSON.parse(value.wrapper.child_events_json)
  events[1].data.source.changes[0].digest='b'.repeat(40)
  const child_events_json=JSON.stringify(events)
  value.result.data.value={...value.wrapper,child_events_json,child_events_sha256:textHash(child_events_json)}
  expect((await value.finalize() as any).review_gate.status).toBe('unreviewed')
})

const inputFixture = () => JSON.parse(readFileSync(process.env.D2_NATIVE_INPUT ?? new URL('../../../../artifacts/d2-native-v2/input-fixture.json', import.meta.url), 'utf8'))
function packet(): any {
  return inputFixture().review_request
}
function fixture(): any {
  const original = inputFixture()
  const request = packet(), runId = original.request.run_id, requestHash = original.request_hash
  request.synthesis.request_id = original.request.request_id
  request.synthesis_ref.hash = bodyHash(request.synthesis)
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash, 1) }
  const current: any = { seq: 2, time: Date.parse(request.requested_at) + 1000, type: 'tool/call', data: {
    callId: 'freeze', name: 'decision_hub_synthesis_submit', arguments: { candidate: request.synthesis, review_request: request } } }
  const context: any = { binding, request: original.request, admittedEvidence: [],
    perpetualReviewOriginals: original.review_originals,
    perpetualBinding: { opportunity_id: request.synthesis.opportunity_id, context: request.synthesis.candidate_set.context,
      candidate_target_ref: request.synthesis.candidate_set.candidate_target_ref, anchor_rule_ref: request.synthesis.candidate_set.anchor_rule_ref,
      method_ref: request.synthesis.method_ref, method_code_hash: request.synthesis.method_code_hash },
    events: [{ seq: 1, type: 'user/message', data: { source: { kind: 'user', rpcId: binding.requestId } } }, current] }
  return { context, request, current }
}


it('retains negative feedback and fresh re-review after an authenticated baseline', async () => {
  const original=inputFixture(), corpus=JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json',import.meta.url),'utf8'))
  const {context,request:old}=fixture();context.perpetualReviewOriginals=original.draft?original.review_originals:corpus.options.review_originals
  const first=structuredClone(original.draft??corpus.draft),second=structuredClone(first)
  second.analysis.thesis+=' Revised after independent counterevidence: explicitly conditional, not an observed cause.'
  let seq=1,clock=Date.parse(old.requested_at)+1000,child=0
  context.events=[instruction('parent'),...context.events.slice(0,1)]
  const phases:any[]=[],disposed:string[]=[]
  const tool=createSynthesisToolDefinition({reviewContext:async()=>context,providerInput:async(_c,_s,_id,args)=>{phases.push(structuredClone(args))}})
  const exec=(callId:string)=>({agent:{id:context.binding.sessionId},callId,signal:new AbortController().signal} as never)
  const call=(id:string,name:string,args:any)=>context.events.push({seq:++seq,time:clock++,type:'tool/call',data:{callId:id,name,arguments:args}})
  const result=(id:string,value:any)=>context.events.push({seq:++seq,time:clock++,type:'tool/result',data:{callId:id,value}})
  const root={},dispose=gate.registerSynthesisReviewContext(root,async()=>context,async()=>context)
  const reviewer=createFrozenReviewerToolDefinition({provider:'spawn',toolName:'subagent_reviewer',backgroundMode:'one-shot',persona:'legacy',toolFilter:{allow:[]},maxDepth:1},{root,readQualified:gate.readQualifiedReviewerContext,
    async start(_provider,options):Promise<any>{
      const id='revision-child-'+(++child),prompt=(options.prompt[0] as any).text,lines=prompt.split('\n')
      const authorization=JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON')+1]);authorization.verdict=child===1?'needs_revision':'approved'
      const findings={...JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON')+1]!),summary:child===1?'Causal certainty needs correction.':'The revised conditional explanation addresses the concern.',issues:child===1?[{issue_id:'causal-certainty',candidate_ids:[],field_paths:['/analysis/thesis'],concern:'The claim needs explicit uncertainty and an opposing explanation.',requested_change:'Make the thesis conditional and explain the countermechanism.',evidence_refs:[]}]:[]}
      if(child===2){expect(prompt).toContain('BEGIN_PREVIOUS_NEGATIVE_REVIEW_JSON');expect(prompt).toContain('causal-certainty')}
      const output=[{type:'text',text:JSON.stringify([authorization,findings])}]
      const events=[{seq:1,type:'user/message',data:{content:[{type:'text',text:prompt}]}},{seq:2,type:'assistant/message',data:{message:{content:output}}},{seq:3,type:'turn/end',data:{reason:{kind:'completed'}}}]
      events.splice(1,0,instruction('child',1))
      return{id,localAgent:{id,session:{header:{parentSession:context.binding.sessionId},events}},result:Promise.resolve({stopReason:'completed',output}),dispose:async()=>{disposed.push(id)}}
    }})
  try{
    const a={action:'prepare',request_id:context.request.request_id,draft:first};call('p1','decision_hub_synthesis_submit',a)
    const p1:any=await tool.execute(a,exec('p1'));result('p1',p1)
    call('r1','subagent_reviewer',{});const r1=await reviewer.execute({description:'Independent first review',prompt:'ignored',run_in_background:false},exec('r1'));result('r1',r1)
    const b={action:'prepare',request_id:context.request.request_id,draft:second};call('p2','decision_hub_synthesis_submit',b)
    const p2:any=await tool.execute(b,exec('p2'));result('p2',p2)
    call('r2','subagent_reviewer',{});const r2=await reviewer.execute({description:'Independent revised review',prompt:'ignored',run_in_background:false},exec('r2'));result('r2',r2)
    const f={action:'finalize',request_id:context.request.request_id,prepared_call_id:'p2'};call('f2','decision_hub_synthesis_submit',f)
    const final:any=await tool.execute(f,exec('f2'));result('f2',final)
    expect(final.review_gate.review_call_id).toBe('r2');expect(final.preparation.prepared_call_id).toBe('p2');expect(child).toBe(2)
    expect(disposed).toEqual(['revision-child-1','revision-child-2']);expect(phases).toEqual([a,b,f])
    const value={context,preparations:[p1,p2],reviews:[r1,r2],final}
    context.events.at(-2).data.arguments.prepared_call_id='p1'
    expect(()=>gate.resolvePerpetualPreparedCall(context,'p1','f2')).toThrow()
  }finally{dispose()}
})
