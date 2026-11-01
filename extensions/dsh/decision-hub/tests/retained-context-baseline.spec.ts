// Synthetic SDK event/HTTP originals; no Provider call or financial qualification.
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, expect, it, vi } from 'vitest'
import { createHash } from 'node:crypto'
import { MessageId } from '@deepseek-ai/dsh-llm'
import { HubClient } from '../src/host/hub-client.js'
import { consumeHarnessModel, type HostScope } from '../src/host/harness-execution.js'
import { guardedModelStream } from '../src/host/model-dispatch.js'
import { dshModelDispatchBindingSchema, dshRunSessionLinkViewSchema, evidenceCandidateSchema,
  researchCapabilityResultSchema, researchSessionRequestSchema } from '@decision-hub/contracts-ts'
import { baselineResults, readResearchBaseline } from '../src/host/baseline.js'
import type { ModelDispatchOptions, SessionEventLike } from '../src/host/dsh-public.js'
import { providerCanonicalJson, registerProviderInputReader, verifySynthesisProviderInput } from '../src/host/provider-input.js'
import { makeClaimedRootContextMessage, verifyRetainedRootContext } from '../src/host/retained-context.js'
import { deterministicIds } from '../src/host/session-correlation.js'
import { DECISION_SYNTHESIS_REF } from '../src/synthesis-protocol.js'
import type { SynthesisReviewContext } from '../src/final-review-gate.js'

const servers: Server[] = []
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => {
    server.closeAllConnections(); server.close(() => resolve())
  })))
})

async function fixture(stale: boolean) {
  const at = Date.now(), stamp = (offset: number) => new Date(at + offset).toISOString()
  const runId = 'run-retained-baseline-control', requestHash = 'a'.repeat(64), buildHash = 'b'.repeat(64)
  const binding = { runId, requestHash, generation: 1, ...deterministicIds(runId, requestHash) }
  const request = researchSessionRequestSchema.parse({
    schema_version: 'research-session-request.v1', request_id: `research-request:${runId}`, run_id: runId,
    event_id: 'event-control', trigger_snapshot_id: 'trigger-control', domain_pack_ref: 'control', role_profile_ref: 'control',
    execution_mode: 'live', research_mode: 'current_decision', pit_cutoff_at: stamp(3_600_000), current_round: 1,
    evidence_refs: ['input-control'], input_evidence: [{ evidence_id: 'input-control', kind: 'market', authority: 'exchange',
      source_id: 'control-input', source_url: null, published_at: null, observed_at: stamp(-2_000), received_at: stamp(-1_900),
      content_hash: 'c'.repeat(64), excerpt: 'Synthetic frozen input.' }],
    evidence_requirements: ['macro_context', 'crypto_spot_confirmation'].map(requirement_id => ({
      requirement_id, description: 'Synthetic declared requirement.', importance: 'hard', source_priority: ['control-source'],
      authority_floor: 'official', preferred_capabilities: [requirement_id === 'macro_context' ? 'official.macro' : 'spot_market_facts'],
      freshness_seconds: 300, minimum_independent_sources: 1, allowed_fallbacks: [], confidence_cap: .5,
    })),
    target_gaps: [], allowed_capabilities: ['official.macro', 'spot_market_facts'],
    execution_budget: { max_evidence_rounds: 2, max_tool_calls: 12, max_subagents: 1, total_deadline_seconds: 3600,
      per_tool_timeout_seconds: 60, per_model_step_timeout_seconds: 600, max_structured_repairs: 1, max_estimated_cost_usd: 1 },
    deadline_at: stamp(3_600_000), output_schema_ref: DECISION_SYNTHESIS_REF, repair_instructions: null,
  })
  const evidence = evidenceCandidateSchema.parse({ evidence_id: 'macro-evidence-control', requirement_id: 'macro_context',
    kind: 'official', authority: 'official', source_id: 'control-source', source_url: null, published_at: stamp(-1_800),
    observed_at: stamp(-1_700), received_at: stamp(-1_600), content_hash: 'd'.repeat(64), excerpt: 'Synthetic admitted macro original.',
    structured_payload_ref: null, tool_call_id: 'baseline-macro-control', research_session_id: binding.sessionId,
    round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null })
  const result = researchCapabilityResultSchema.parse({ schema_version: 'research-capability-result.v1',
    request_id: 'baseline-macro-control', capability_id: 'official.macro', provider: 'control-source', evidence_candidates: [evidence],
    facts: [{ schema_version: 'fact-envelope.v1', fact_id: 'macro-fact-control', evidence_id: evidence.evidence_id,
      requirement_id: 'macro_context', metric_family: 'macro.control', field: 'level', instrument: null, venue: null,
      value: '3.2', unit: 'percent', window_start_at: null, window_end_at: null, event_offset: null,
      observed_at: evidence.observed_at, received_at: evidence.received_at, published_at: evidence.published_at,
      source_id: evidence.source_id, independence_group: 'control-source', quality: 'accepted', delay_class: 'realtime',
      payload_schema_ref: 'synthetic-control.v1', payload_hash: 'e'.repeat(64), attributes: { synthetic: true } }],
    cost_usd: 0, completed_at: stamp(-1_500) })
  const reference = (role: string) => ({ output_id: `output-${role}`, role, schema_version: `${role}.v1`,
    payload_hash: 'f'.repeat(64), recorded_at: stamp(-1_400) })
  const delivery = { schema_version: 'research-baseline-delivery.v1', status: 'hard_gap', run_id: runId,
    research_session_id: binding.sessionId, generation: 1, request_hash: requestHash, method_hash: '1'.repeat(64),
    plan_ref: reference('baseline-plan'), batch_ref: reference('baseline-batch'), results: [result],
    errors: [{ request_id: 'baseline-spot-control', capability_id: 'spot_market_facts', requirement_id: 'crypto_spot_confirmation',
      error_code: stale ? 'spot_research_original_stale' : 'spot_research_original_missing' }],
    coverage: { status: 'insufficient', covered_requirement_ids: ['macro_context'], gaps: [{ requirement_id: 'crypto_spot_confirmation',
      importance: 'hard', reason_code: stale ? 'stale' : 'missing', query_hint: 'Read exact-asset committed data.',
      attempted_capabilities: ['spot_market_facts'], blocks_directional_output: true }], conflicts: [],
      hard_coverage_ratio: .5, soft_coverage_ratio: 0, assessed_at: stamp(-1_300) },
    remaining_tool_calls: 10, acquisition_close_at: stamp(2_400_000), recorded_at: stamp(-1_200), origin: 'server_baseline',
    skill_execution_verified: false, model_understanding_verified: false }
  let received: unknown
  const server = createServer((incoming, response) => {
    const chunks: Buffer[] = []
    incoming.on('data', chunk => chunks.push(Buffer.from(chunk)))
    incoming.once('end', () => {
      received = { method: incoming.method, path: incoming.url, command: JSON.parse(Buffer.concat(chunks).toString('utf8')) }
      response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(delivery))
    })
  })
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const serverBaseline = await readResearchBaseline({
    serviceUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}/decision-hub/v1/research-capabilities/execute`,
    authKey: 'synthetic-baseline-key', binding, request, signal: new AbortController().signal, deadlineAt: request.deadline_at,
  })
  expect(received).toMatchObject({ method: 'POST', path: '/decision-hub/v1/research-capabilities/baseline',
    command: { run_id: runId, research_session_id: binding.sessionId, generation: 1, request_hash: requestHash } })
  const event = (seq: number, type: string, data: Record<string, unknown>): SessionEventLike => ({ seq, type, time: at, data })
  // The SDK has real configuration/inbox events before appending its first prompt.
  const prefix = [event(0, 'session/start', { sessionId: binding.sessionId }),
    event(1, 'agent/configured', {}), event(2, 'agent/status', { status: 'running' }),
    event(3, 'agent/inbox/enqueued', {}), event(4, 'agent/inbox/drained', {}), event(5, 'agent/inbox/spliced', {})]
  const before: SynthesisReviewContext = { binding, request, events: prefix, admittedEvidence: [evidence], serverBaseline }
  const claimed = makeClaimedRootContextMessage(before, buildHash)
  const envelope = JSON.parse((claimed.content[1] as { text: string }).text)
  expect(envelope).toMatchObject({ schema_version: 'decision-hub-protected-context.v4', native_first_seq: 0, native_last_seq: 5 })
  const prompt = { id: MessageId('claimed-prompt-control'), role: 'user' as const, source: { kind: 'user' as const, rpcId: binding.requestId },
    content: [{ type: 'text' as const, text: 'Synthetic authenticated canonical Hub prompt.' }] }
  const events = [...prefix, event(6, 'step/start', {}), event(7, 'user/message', prompt),
    event(8, 'agent/state', {}), event(9, 'agent/state', {}), event(10, 'user/message', { ...claimed })]
  const context: SynthesisReviewContext = { ...before, events }
  const dispatch = dshModelDispatchBindingSchema.parse({ schema_version: 'dsh-model-dispatch-binding.v1', run_id: runId,
    dsh_session_id: binding.sessionId, request_hash: requestHash, generation: 1, plugin_build_hash: buildHash,
    execution_session_id: null, parent_session_id: null, provider: 'synthetic-route', model: 'synthetic-model',
    purpose: 'conversation', reasoning_effort: null, temperature: null, max_tokens: 4096, stop_sequences_hash: null })
  const link = dshRunSessionLinkViewSchema.parse({ schema_version: 'dsh-run-session-link.v1', run_id: runId,
    dsh_session_id: binding.sessionId, request_hash: requestHash, generation: 1, state: 'running',
    upstream_identity: { source_commit: '2'.repeat(40), source_version: '0.1.2-alpha.2', package_versions: {}, plugin_build_hash: buildHash },
    deadline_at: request.deadline_at, max_tool_calls: 12, tool_calls_started: 2, model_step_timeout_ms: 600_000,
    accepted_at: stamp(-1_000), last_seen_at: stamp(-900), terminal_at: null, last_seq: 10, error_code: null,
    trace_ref: null, result_ref: null, result_hash: null, created_at: stamp(-2_000), updated_at: stamp(-900) })
  const options: ModelDispatchOptions = { sessionId: binding.sessionId, provider: 'synthetic-route', model: 'synthetic-model',
    messages: [prompt, claimed], system: 'Synthetic system.', tools: [] }
  expect(baselineResults(context)).toEqual([result])
  return { claimed, context, dispatch, link, options }
}

it.each([true, false])('verifies the initial nonempty SDK prefix with stale baseline=%s', async stale => {
  const f = await fixture(stale)
  expect(() => verifyRetainedRootContext(f.options, f.dispatch, f.link, f.context)).not.toThrow()
  expect((f.claimed.content[0] as { text: string }).text.includes('spot_research_original_stale')).toBe(stale)
})

it('verifies complete retained input above 1 MB with the dedicated 8 MiB limit', async () => {
  const f = await fixture(false)
  const expanded = { ...f.options, system: 'x'.repeat(1_100_000) }
  expect(() => verifyRetainedRootContext(expanded, f.dispatch, f.link, f.context)).not.toThrow()
  expect(() => verifyRetainedRootContext({ ...expanded, system: 'x'.repeat(8 * 1024 * 1024) },
    f.dispatch, f.link, f.context)).toThrow('dsh_provider_input_oversize')
})

it.each(['fact', 'evidence', 'bounds', 'prompt', 'baseline-authenticity'] as const)('rejects %s tampering after the legal initial append', async mutation => {
  const f = await fixture(false)
  let context = f.context, options = f.options
  if (mutation === 'baseline-authenticity') {
    context = { ...context, serverBaseline: structuredClone(context.serverBaseline) }
  } else if (mutation === 'prompt') {
    options = { ...options, messages: options.messages!.filter(message => message.id !== 'claimed-prompt-control') }
  } else {
    const body = JSON.parse((f.claimed.content[1] as { text: string }).text)
    if (mutation === 'fact') body.facts[0].value = '9.9'
    if (mutation === 'evidence') body.evidence_candidates[0].excerpt = 'Changed admitted original.'
    if (mutation === 'bounds') body.native_last_seq = 10
    const changed = { ...f.claimed, content: [f.claimed.content[0]!, { type: 'text' as const, text: providerCanonicalJson(body) }] }
    // Change the native original and frozen surface together: rejection must
    // come from authenticated contents/bounds, not a mismatching copied message.
    context = { ...context, events: context.events.map(event => event.seq === 10 ? { ...event, data: changed } : event) }
    options = { ...options, messages: options.messages!.map(message => message.id === changed.id ? changed : message) }
  }
  expect(() => verifyRetainedRootContext(options, f.dispatch, f.link, context)).toThrow('research_provider_native_context_invalid')
})


it('keeps original one-hour Harness preparation beyond 30s then witnesses provider input before delegate', async () => {
  const f=await fixture(false), order:string[]=[], received: {path:string;method:string;headers:Headers}[]=[]
  const hash=(value:string)=>createHash('sha256').update(value).digest('hex')
  const scope:HostScope={host_instance_id:'00000000-0000-4000-8000-000000000001',process_id:123,started_at:new Date().toISOString(),hub_base_url:'http://fixture.invalid',workspace_id:'workspace',workspace_cwd_hash:'c'.repeat(64)}
  const prompt=(f.options.messages![0].content[0] as {text:string}).text
  const witness='d'.repeat(64), deadline=f.context.request.deadline_at
  const view={schema_version:'research-harness-execution-view.v1',run_id:f.dispatch.run_id,dsh_session_id:f.dispatch.dsh_session_id,request_id:f.context.binding.requestId,request_hash:f.dispatch.request_hash,generation:1,plugin_build_hash:f.dispatch.plugin_build_hash,prompt,prompt_hash:hash(prompt),method_payload_hash:'e'.repeat(64),witness:null,
    method:{schema_version:'research-harness-execution-method.v4',run_id:f.dispatch.run_id,event_id:'event-control',method_v3_payload_hash:'a'.repeat(64),consumption_payload_hash:'b'.repeat(64),artifact_bytes_sha256:'c'.repeat(64),pack_definition_hash:'d'.repeat(64),full_task_hash:'e'.repeat(64),compiled_policy_hash:'f'.repeat(64),plugin_build_hash:f.dispatch.plugin_build_hash,proof_scope:'root_model_request_consumption',native_execution_proof:'unverified',eligible_for_deployment:false,recorded_at:new Date().toISOString()}}
  const delay=(ms:number,signal?:AbortSignal|null)=>new Promise<void>((resolve,reject)=>{const timer=setTimeout(resolve,ms);signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(signal.reason)},{once:true})})
  const hub=new HubClient({baseUrl:'http://fixture.invalid',callbackKey:'synthetic',timeoutMs:30_000,attempts:1,ownerId:'synthetic',managedReadDeadline:()=>deadline,fetchImpl:async(url,init)=>{
    const path=new URL(String(url)).pathname;received.push({path,method:init?.method??'GET',headers:new Headers(init?.headers)})
    if(path.endsWith('/link'))return Response.json({...f.link,harness_required:true})
    if(path.endsWith('/harness-execution')){order.push('harness-read');await delay(18_000,init?.signal);return Response.json(view)}
    if(path.endsWith('/harness-root-consumption')){order.push('root-witness');await delay(15_000,init?.signal);return Response.json({payload_hash:witness,host_scope:scope})}
    if(path.endsWith('/model-binding')){order.push('bind');await delay(12_000,init?.signal);return Response.json(JSON.parse(String(init?.body)))}
    throw new Error('unexpected synthetic endpoint')
  }})
  vi.spyOn(hub,'linkBySession').mockResolvedValue({...f.link,harness_required:true})
  vi.spyOn(hub,'recordProviderInput').mockImplementation(async payload=>{order.push('provider-input');return {dispatch_id:payload.dispatch_id,input_hash:payload.input_hash,reference:{output_id:'original-input',payload_hash:'f'.repeat(64)}} as any})
  vi.spyOn(hub,'recordProviderFinish').mockImplementation(async payload=>{order.push('provider-finish');return {dispatch_id:payload.dispatch_id,finish_hash:payload.finish_hash,status:payload.status,input_ref:payload.input_ref} as any})
  vi.useFakeTimers({toFake:['Date','performance','setTimeout','clearTimeout']})
  try{
    const chunks:unknown[]=[]
    const consume=(async()=>{for await(const chunk of guardedModelStream(f.options,async function*(){order.push('delegate');yield {type:'finish',reason:{kind:'stop'}}},hub,f.dispatch.plugin_build_hash,undefined,
      async(binding,actual,original)=>consumeHarnessModel(hub,binding,actual,scope,f.dispatch.plugin_build_hash,original),async()=>{order.push('scope');return scope},async()=>{order.push('retained');return f.context},
      async()=>{order.push('after-input')},()=>{order.push('deadline');return deadline},()=>{order.push('watchdog')}))chunks.push(chunk)})()
    await vi.advanceTimersByTimeAsync(46_000);await consume
    expect(chunks).toEqual([{type:'finish',reason:{kind:'stop'}}])
    expect(order).toEqual(['deadline','harness-read','root-witness','bind','scope','retained','provider-input','after-input','watchdog','delegate','provider-finish'])
    const bind=received.find(row=>row.path.endsWith('/model-binding'))!
    expect(bind.headers.get('x-decision-hub-harness-witness')).toBe(witness)
    expect(bind.headers.get('x-decision-hub-model-binding-deadline-at')).toBe(deadline)
    expect(received.find(row=>row.path.endsWith('/harness-execution'))?.method).toBe('POST')
    expect(received.filter(row=>row.path.includes('harness-')).every(row=>row.headers.get('x-decision-hub-research-proof-deadline-at')===deadline)).toBe(true)
  }finally{vi.useRealTimers()}
  // The default production transport is real loopback Node HTTP, not fetch.
  let witnessed:string|undefined, boundDeadline:string|undefined, requests=0
  const server=createServer((req,res)=>{requests++;witnessed=String(req.headers['x-decision-hub-harness-witness']);boundDeadline=String(req.headers['x-decision-hub-model-binding-deadline-at']);const parts:Buffer[]=[];req.on('data',chunk=>parts.push(chunk));req.on('end',()=>{res.setHeader('content-type','application/json');res.end(Buffer.concat(parts).toString())})})
  servers.push(server);await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
  const realHub=new HubClient({baseUrl:`http://127.0.0.1:${(server.address() as AddressInfo).port}`,callbackKey:'synthetic',timeoutMs:30_000,attempts:1,ownerId:'synthetic',managedReadDeadline:()=>deadline})
  await realHub.bindModel(f.dispatch,new AbortController().signal,{witnessHash:witness,budgetMs:30_000},deadline)
  expect(witnessed).toBe(witness);expect(boundDeadline).toBe(deadline)
  await expect(realHub.bindModel(f.dispatch,undefined,{witnessHash:'bad-header',budgetMs:30_000},deadline)).rejects.toThrow('dsh_model_binding_budget_invalid')
  expect(requests).toBe(1)
  // Harness preserves its legacy reviewer ABI, but actual V2 calls require
  // their own durable Provider emission; the witness cannot replace it.
  const root={}, args={action:'finalize',request_id:f.context.request.request_id,prepared_call_id:'actual-prepare'}
  const context={...f.context,request:{...f.context.request,output_schema_ref:'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2'}}
  let providerReads=0
  const proof={run_id:f.dispatch.run_id,generation:1,root_session_id:f.dispatch.dsh_session_id,execution_session_id:f.dispatch.dsh_session_id,call_id:'finalize-call',tool_name:'decision_hub_synthesis_submit',arguments_hash:hash(providerCanonicalJson(args)),candidate_hash:null}
  const dispose=registerProviderInputReader(root,{linkBySession:async()=>({...f.link,harness_required:true}),providerToolEmission:async()=>{providerReads++;return proof as any}})
  try{
    expect(await verifySynthesisProviderInput(root,context,f.dispatch.dsh_session_id,'finalize-call',args,new AbortController().signal)).toBe(proof)
    proof.arguments_hash='a'.repeat(64)
    await expect(verifySynthesisProviderInput(root,context,f.dispatch.dsh_session_id,'finalize-call',args,new AbortController().signal)).rejects.toThrow('dsh_provider_emission_identity_mismatch')
    expect(providerReads).toBe(2)
  }finally{dispose()}
})
