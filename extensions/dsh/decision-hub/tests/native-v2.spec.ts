import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import * as gate from '../src/final-review-gate.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'
import { bodyHash, textHash, reviewOriginalsFromPrompt, canonicalJson } from '../src/perpetual-synthesis-protocol.js'
import { HubClient } from '../src/host/hub-client.js'
import { createSynthesisToolDefinition } from '../src/synthesis-tool.js'

const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const reviewOutput=(authorization:any,prompt:string)=>{
  const lines=prompt.split('\n'), findings=JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON')+1]!)
  findings.summary='Synthetic independent review: exact packet assessed.'
  // An explicit synthetic child answer is checked before the real proof is created.
  const assessment=inputFixture().report_assessment
  if(assessment!==undefined)findings.report_assessment=structuredClone(assessment)
  return [{type:'text',text:JSON.stringify([authorization,findings])}]
}

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

async function reviewedFixture(mode: 'valid' | 'wrong_child' | 'refusal' = 'valid', mutateEvents?: (events: any[], prompt: string) => void): Promise<any> {
  const { context, request } = fixture()
  context.events.push({ seq: 3, type: 'tool/result', data: { callId: 'freeze', isError: true,
    content: [{ type: 'text', text: 'Error: decision_hub_final_review_required' }] } })
  const reviewCall = { seq: 4, time: Date.parse(request.requested_at) + 2000, type: 'tool/call', data: { callId: 'review', name: 'subagent_reviewer', arguments: {} } }
  context.events.push(reviewCall)
  const root = {}, dispose = gate.registerSynthesisReviewContext(root, async () => context, async () => context)
  let childDisposed = false, actualPacket: unknown, actualOriginals: unknown
  const definition = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot',
    persona: 'reviewer', toolFilter: { allow: [] }, maxDepth: 1 }, { root, readQualified: gate.readQualifiedReviewerContext,
    async start(_provider, options): Promise<any> {
      const prompt = (options.prompt[0] as any).text, lines = prompt.split('\n')
      actualPacket = JSON.parse(lines[lines.indexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON') + 1])
      actualOriginals = reviewOriginalsFromPrompt(prompt)
      const authorization = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]); authorization.verdict = 'approved'
      const output = reviewOutput(authorization, prompt)
      const events = [
        { seq: 1, type: 'user/message', data: { content: [{ type: 'text', text: prompt }] } },
        { seq: 2, type: 'assistant/message', data: { message: { content: output } } },
        { seq: 3, type: 'turn/end', data: { reason: { kind: 'completed' } } },
      ]
      mutateEvents?.(events, prompt)
      return { id: 'independent-child', localAgent: { id: 'independent-child', session: { header: { parentSession: mode === 'wrong_child' ? 'other-parent' : context.binding.sessionId }, events } },
        result: Promise.resolve({ stopReason: mode === 'refusal' ? 'refusal' : 'completed', output }), dispose: async () => { childDisposed = true } }
    } })
  try {
    const wrapper = await definition.execute({ description: 'V2 review', prompt: 'untrusted replacement ignored', run_in_background: false },
      { agent: { id: context.binding.sessionId }, callId: 'review', signal: new AbortController().signal } as never)
    context.events.push({ seq: 5, type: 'tool/result', data: { callId: 'review', value: wrapper } })
    const submit = { seq: 6, time: Date.parse(request.requested_at) + 3000, type: 'tool/call', data: { callId: 'submit', name: 'decision_hub_synthesis_submit', arguments: { candidate: request.synthesis, review_request: request } } }
    context.events.push(submit)
    const result = gate.evaluatePerpetualFinalReviewGate(context, request.synthesis, request, 'submit')
    return { context, request, wrapper, result, childDisposed, actualPacket, actualOriginals }
  } finally { dispose() }
}

const runtimeSnapshot = () => {
  const sections = [{ name: 'sandbox:policy', text: 'Controlled sandbox policy.' },
    { name: 'approval:policy', text: 'Controlled approval policy.' },
    { name: 'subagent:delegation', text: 'Controlled delegation policy.' }]
  return { seq: 2, type: 'user/message', data: { role: 'user', id: 'sdk-runtime-snapshot',
    source: { kind: 'plugin', plugin: '@deepseek-ai/dsh-system-prompt', form: 'snapshot', sections },
    content: [{ type: 'text', text: 'Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n' + sections.map(section => section.text).join('\n\n') }] } }
}

function insertRuntimeSnapshot(events: any[]): void {
  for (const event of events.slice(1)) event.seq += 1
  events.splice(1, 0, runtimeSnapshot())
}

const snapshotAttacks: Array<[string, (events: any[]) => void]> = [
    ['unknown plugin', (events: any[]) => { events[1].data.source.plugin = 'unknown-plugin' }],
    ['unknown source kind', (events: any[]) => { events[0].data.source = { kind: 'unknown' } }],
    ['extra ordinary prompt', (events: any[]) => { events.splice(1, 0, structuredClone(events[0])) }],
    ['changed product prompt', (events: any[]) => { events[0].data.content[0].text += ' altered' }],
    ['altered snapshot body', (events: any[]) => { events[1].data.content[0].text += ' altered' }],
    ['unknown section', (events: any[]) => { events[1].data.source.sections[0].name = 'unknown:policy' }],
    ['empty section', (events: any[]) => { events[1].data.source.sections[0].text = '' }],
    ['duplicate section', (events: any[]) => { events[1].data.source.sections[1] = events[1].data.source.sections[0] }],
    ['extra source key', (events: any[]) => { events[1].data.source.untrusted = true }],
    ['multiple snapshots', (events: any[]) => { events.splice(1, 0, structuredClone(events[1])) }],
    ['snapshot before prompt', (events: any[]) => { events.unshift(events.splice(1, 1)[0]) }],
    ['late snapshot', (events: any[]) => { events.splice(2, 0, events.splice(1, 1)[0]) }],
    ['invalid role', (events: any[]) => { events[1].data.role = 'assistant' }],
    ['empty id', (events: any[]) => { events[1].data.id = '' }],
    ['noncompleted turn', (events: any[]) => { events.at(-1).data.reason.kind = 'error' }],
    ['changed assistant output', (events: any[]) => { events.at(-2).data.message.content = [{ type: 'text', text: 'different' }] }],
]

describe('official runtime snapshot in independent V2 reviewer transcript', () => {
  it('accepts the exact SDK snapshot while retaining its complete native hash envelope', async () => {
    const value = await reviewedFixture('valid', insertRuntimeSnapshot)
    expect(value.result.status).toBe('matched')
    expect(value.wrapper.child_events_sha256).toBe(textHash(value.wrapper.child_events_json))
    expect(JSON.parse(value.wrapper.child_events_json)[1]).toEqual(runtimeSnapshot())
  })
  it.each(snapshotAttacks)('rejects %s', async (_name, mutate) => {
    await expect(reviewedFixture('valid', (events) => { insertRuntimeSnapshot(events); mutate(events) }))
      .rejects.toThrow('child_transcript_mismatch')
  })
  it('rejects a wrong parent even with a valid official snapshot', async () => {
    await expect(reviewedFixture('wrong_child', insertRuntimeSnapshot)).rejects.toThrow('child_identity_invalid')
  })
  it('rechecks snapshot provenance and timing when consuming the frozen wrapper', async () => {
    const value = await reviewedFixture('valid', insertRuntimeSnapshot)
    const result = value.context.events.find((event: any) => event.seq === 5)
    for (const mode of ['unknown_plugin', 'late_snapshot']) {
      const child = JSON.parse(value.wrapper.child_events_json)
      if (mode === 'unknown_plugin') child[1].data.source.plugin = 'unknown-plugin'
      else child.splice(2, 0, child.splice(1, 1)[0])
      const child_events_json = JSON.stringify(child)
      result.data.value = { ...value.wrapper, child_events_json, child_events_sha256: textHash(child_events_json) }
      expect(gate.evaluatePerpetualFinalReviewGate(value.context, value.request.synthesis, value.request, 'submit').status).toBe('unreviewed')
    }
  })
})

describe('native decision_v2 exact freeze and independent review protocol', () => {
  it('rejects unsupported causal sources and substituted readonly material before review', () => {
    const one = fixture()
    one.context.perpetualReviewOriginals.originals[0].body_json = '{}'
    expect(() => gate.evaluatePerpetualFinalReviewGate(one.context, one.request.synthesis, one.request, 'freeze')).toThrow('original_body_mismatch')
    const two = fixture()
    two.request.synthesis.analysis.main_chain[0].evidence_refs = [{ ref: 'invented:source', hash: 'f'.repeat(64) }]
    two.request.synthesis_ref.hash = bodyHash(two.request.synthesis)
    expect(() => gate.evaluatePerpetualFinalReviewGate(two.context, two.request.synthesis, two.request, 'freeze')).toThrow('analysis_original_unavailable')
  })
  it('retains the selected review originals when unrelated later evidence is available', async () => {
    const value = await reviewedFixture(), body = { excerpt: 'Later admitted evidence is not retroactive review input.' }
    value.context.perpetualReviewOriginals.originals.push({ source_ref: { ref: 'review-original:run_evidence:later', hash: bodyHash(body) }, original_ref: null,
      source_kind: 'run_evidence', role: 'later', body_json: canonicalJson(body) })
    expect(gate.evaluatePerpetualFinalReviewGate(value.context, value.request.synthesis, value.request, 'submit').status).toBe('matched')
  })
  it('requires an actual frozen packet and authenticated admission before spending review', () => {
    expect(typeof (gate as any).evaluatePerpetualFinalReviewGate).toBe('function')
    const { context, request } = fixture()
    expect(() => (gate as any).evaluatePerpetualFinalReviewGate(context, request.synthesis, request, 'freeze')).toThrow('decision_hub_final_review_required')
    expect(() => (gate as any).evaluatePerpetualFinalReviewGate({ ...context, perpetualBinding: undefined }, request.synthesis, request, 'freeze')).toThrow('perpetual_admission')
    const bad = structuredClone(request); bad.synthesis.candidate_set.candidates[0].unit_notional_usdt = '200'
    expect(() => (gate as any).evaluatePerpetualFinalReviewGate(context, bad.synthesis, bad, 'freeze')).toThrow()
  })
  it('launches one separate child with the exact full packet then matches its actual native transcript', async () => {
    const value = await reviewedFixture()
    expect(value.actualPacket).toEqual(value.request)
    expect((value.actualOriginals as any).originals.some((row: any) => row.role === 'comparison_method')).toBe(true)
    expect((value.actualPacket as any).synthesis.analysis.opposite_chain.length).toBeGreaterThan(0)
    expect(value.childDisposed).toBe(true)
    expect(value.result.status).toBe('matched')
    expect(value.result.review_call_id).toBe('review')
    value.context.events.push({ seq: 7, type: 'tool/result', data: { callId: 'submit', value: { schema_version: 'decision-hub-synthesis-tool-result.v3',
      candidate: value.request.synthesis, review_request: value.request, review_gate: value.result } } })
    if (process.env.D2_WRITE_NATIVE_FIXTURE === '1') writeFileSync(process.env.D2_NATIVE_OUTPUT ?? new URL('../../../../artifacts/d2-native-v2/native-transcript.json', import.meta.url), JSON.stringify(value, null, 2))
    const reviewResult = value.context.events.find((event: any) => event.seq === 5)
    reviewResult.data.value = { ...value.wrapper, child_parent_session_id: 'other-parent' }
    expect(gate.evaluatePerpetualFinalReviewGate(value.context, value.request.synthesis, value.request, 'submit').status).toBe('unreviewed')
    reviewResult.data.value = { ...value.wrapper, schema_version: 'decision-hub-frozen-review-result.v3' }
    expect(gate.evaluatePerpetualFinalReviewGate(value.context, value.request.synthesis, value.request, 'submit').status).toBe('unreviewed')
  })
  it('fails closed on actual child refusal and parent mismatch and disposes the child', async () => {
    await expect(reviewedFixture('refusal')).rejects.toThrow('declined')
    await expect(reviewedFixture('wrong_child')).rejects.toThrow('child_identity_invalid')
  })
  it('reads only exact authenticated server admission identity through the existing Hub client', async () => {
    const { context } = fixture()
    const reply = { schema_version: 'decision-v2-native-binding.v1', run_id: context.request.run_id,
      request_hash: context.binding.requestHash, generation: 1, binding_ref: { ref: 'synthetic:immutable-admission', hash: 'b'.repeat(64) }, ...context.perpetualBinding, review_originals: context.perpetualReviewOriginals }
    let actualUrl = ''
    const client = new HubClient({ baseUrl: 'http://127.0.0.1:1', callbackKey: 'test-key', ownerId: 'owner', timeoutMs: 1000, attempts: 1,
      fetchImpl: async (url) => { actualUrl = String(url); return new Response(JSON.stringify(reply), { status: 200 }) } })
    const value = await client.perpetualResearchBinding(context.request.run_id, context.binding.requestHash, 1,
      new AbortController().signal, new Date(Date.now() + 30_000).toISOString())
    expect(value).toEqual({ binding: context.perpetualBinding, reviewOriginals: context.perpetualReviewOriginals })
    expect(actualUrl).toContain(`/decision-binding?request_hash=${context.binding.requestHash}&generation=1`)
    reply.run_id = 'other-run'
    await expect(client.perpetualResearchBinding(context.request.run_id, context.binding.requestHash, 1,
      new AbortController().signal, new Date(Date.now() + 30_000).toISOString())).rejects.toThrow('admission_identity_mismatch')
  })
  it('uses compact generated exact Tool shapes and routes successful V2 submission explicitly', async () => {
    const value = await reviewedFixture()
    const tool = createSynthesisToolDefinition({ reviewContext: async () => value.context })
    expect(JSON.stringify(tool.parameters).length).toBeLessThan(130_000)
    const result: any = await tool.execute({ candidate: value.request.synthesis, review_request: value.request },
      { agent: { id: value.context.binding.sessionId }, callId: 'submit', signal: new AbortController().signal } as never)
    expect(result.schema_version).toBe('decision-hub-synthesis-tool-result.v3')
    expect(result.review_gate.status).toBe('matched')
  })
})

it('prepares one authored draft, reviews its actual result, and finalizes by handle', async () => {
  const original=inputFixture(), corpus=JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json',import.meta.url),'utf8'))
  const authored=original.draft??corpus.draft, source=original.draft?original.review_originals:corpus.options.review_originals
  const {context,request:old}=fixture(); context.perpetualReviewOriginals=source
  if(context.request.execution_mode==='replay') context.events[1].time=Date.parse(context.request.pit_cutoff_at)
  context.events[1].data.arguments={action:'prepare',request_id:context.request.request_id,draft:authored}
  const phases:any[]=[]
  const tool=createSynthesisToolDefinition({reviewContext:async(_agent,_request,_signal,callId)=>{
    expect(gate.perpetualSnapshotSourceCallId(context.events,callId)).toBe('freeze')
    return context
  },providerInput:async(_c,_s,_id,args)=>{phases.push(structuredClone(args))}})
  const exec=(callId:string)=>({agent:{id:context.binding.sessionId},callId,signal:new AbortController().signal} as never)
  const prepared:any=await tool.execute(context.events[1].data.arguments,exec('freeze'))
  expect(prepared.schema_version).toBe('decision-hub-perpetual-preparation.v1')
  expect(prepared.preparation.prepared_call_id).toBe('freeze')
  const request=prepared.review_request
  context.events.push({seq:3,type:'tool/result',data:{callId:'freeze',value:prepared}})
  context.events.push({seq:4,time:Date.parse(old.requested_at)+2000,type:'tool/call',data:{callId:'review',name:'subagent_reviewer',arguments:{}}})
  const root={},dispose=gate.registerSynthesisReviewContext(root,async()=>context,async(_agent,_signal,callId)=>{
    expect(callId).toBe('review')
    expect(gate.perpetualSnapshotSourceCallId(context.events,callId)).toBe('freeze')
    return context
  })
  let childDisposed=false,actualPacket:any,actualOriginals:any,actualAssumptions:any
  const reviewer=createFrozenReviewerToolDefinition({provider:'spawn',toolName:'subagent_reviewer',backgroundMode:'one-shot',persona:'legacy persona must not be used',toolFilter:{allow:[]},maxDepth:1},{root,readQualified:gate.readQualifiedReviewerContext,
    async start(_provider,options):Promise<any>{
      const prompt=(options.prompt[0] as any).text, lines=prompt.split('\n')
      expect(options.persona).toContain('action-review-authorization.v2')
      actualPacket=JSON.parse(lines[lines.indexOf('BEGIN_PERPETUAL_REVIEW_PACKET_JSON')+1]); actualOriginals=reviewOriginalsFromPrompt(prompt)
      actualAssumptions=JSON.parse(lines[lines.indexOf('BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON')+1])
      expect(actualAssumptions).toEqual(authored.assumptions)
      const authorization=JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON')+1]);authorization.verdict='approved'
      const output=reviewOutput(authorization,prompt),events=[{seq:1,type:'user/message',data:{content:[{type:'text',text:prompt}]}},{seq:2,type:'assistant/message',data:{message:{content:output}}},{seq:3,type:'turn/end',data:{reason:{kind:'completed'}}}]
      return{id:'independent-child',localAgent:{id:'independent-child',session:{header:{parentSession:context.binding.sessionId},events}},result:Promise.resolve({stopReason:'completed',output}),dispose:async()=>{childDisposed=true}}
    }})
  try{
    const wrapper=await reviewer.execute({description:'Independent V2 review',prompt:'ignored',run_in_background:false},exec('review'))
    context.events.push({seq:5,type:'tool/result',data:{callId:'review',value:wrapper}})
    const args={action:'finalize',request_id:context.request.request_id,prepared_call_id:'freeze'}
    context.events.push({seq:6,time:Date.parse(old.requested_at)+3000,type:'tool/call',data:{callId:'submit',name:'decision_hub_synthesis_submit',arguments:args}})
    const result:any=await tool.execute(args,exec('submit'))
    expect(result.schema_version).toBe('decision-hub-synthesis-tool-result.v4');expect(result.review_gate.status).toBe('matched')
    expect(result.candidate).toEqual(prepared.candidate);expect(result.preparation).toEqual(prepared.preparation)
    expect(phases).toEqual([context.events[1].data.arguments,args]);expect(JSON.stringify(args).length).toBeLessThan(300)
    context.events.push({seq:7,type:'tool/result',data:{callId:'submit',value:result}})
    const value={context,request,wrapper,result:result.review_gate,preparation:prepared.preparation,childDisposed,actualPacket,actualOriginals,actualAssumptions}
    if(process.env.D2_WRITE_NATIVE_FIXTURE==='1')writeFileSync(process.env.D2_NATIVE_OUTPUT!,JSON.stringify(value,null,2))
    context.events[1].data.arguments.draft.candidates[0].unit_notional_usdt='101'
    expect(()=>gate.evaluatePerpetualFinalReviewGate(context,prepared.candidate,request,'submit')).toThrow('call_packet_mismatch')
  }finally{dispose()}
})

it('revises a genuinely negative child packet and authorizes only the latest preparation', async () => {
  const original=inputFixture(), corpus=JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json',import.meta.url),'utf8'))
  const {context,request:old}=fixture();context.perpetualReviewOriginals=original.draft?original.review_originals:corpus.options.review_originals
  const first=structuredClone(original.draft??corpus.draft),second=structuredClone(first)
  second.analysis.thesis+=' Revised after independent counterevidence: explicitly conditional, not an observed cause.'
  let seq=1,clock=Date.parse(old.requested_at)+1000,child=0
  context.events=context.events.slice(0,1)
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
    if(process.env.D2_REVISION_OUTPUT)writeFileSync(process.env.D2_REVISION_OUTPUT,JSON.stringify(value,null,2))
    context.events.at(-2).data.arguments.prepared_call_id='p1'
    expect(()=>gate.resolvePerpetualPreparedCall(context,'p1','f2')).toThrow()
  }finally{dispose()}
})
