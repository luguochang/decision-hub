// Recorded values; actor authority, IO scheduling and token meter are offline facades.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { DecisionHubHostBridge } from '../src/host/bridge.js'
import { declaredChildResearchDelivery } from '../src/host/child-research-context.js'
import { makeRetainedRootContextMessage, replaceRetainedRootContextMessage, verifyRetainedRootContext } from '../src/host/retained-context.js'
import { providerCanonicalJson } from '../src/host/provider-input.js'

const input = JSON.parse(readFileSync(0, 'utf8'))
const repo = resolve(import.meta.dirname, '../../../..')
const sdk = process.env.DSH_SESSION_ENTRY ?? resolve(repo, '.cache/dsh-upstream/source/packages/core/session/lib/index.js')
assert.equal(createHash('sha256').update(readFileSync(sdk)).digest('hex'), '6f7e9ef6d0f44be1955c98aac93fa676645ddb8e92e47ee80ee5e6d43bf88a7d')
const { decodeStorageRecord, decodeSeqRanges, foldSurface, deriveEventMessage } = await import(pathToFileURL(sdk).href)
const decode = (session: any) => session.records.flatMap((row: any) => decodeStorageRecord(row.sourceEventSeqs === undefined
  ? row : { ...row, sourceEventSeqs: decodeSeqRanges(row.sourceEventSeqs, row.seq) }))
const allRoot = decode(input.root), children = input.children.map((row: any) => ({ meta: row.header_projection, events: decode(row) }))
assert(allRoot.every((event: any, index: number) => event.seq === index))
for (const child of children) assert(child.events.every((event: any, index: number) => event.seq === index))
const bad = allRoot.find((event: any) => event.seq === input.boundaries.original_bad_marker_seq).data
const badBody = JSON.parse(bad.content[1].text)
const markerAt = allRoot[input.boundaries.original_bad_marker_seq].time
const failureAt = allRoot[input.boundaries.failure_event_seq].time
const wireTime = (value: string) => value.includes('T') ? value : value.replace(' ', 'T') + 'Z'
const rowById = new Map(input.raw_evidence_metadata.map((row: any) => [row.evidence_id, row]))
// This is a controlled visibility schedule, not a claimed historical HTTP response.
const atMarker = input.independent_evidence.filter((row: any) => Date.parse(wireTime((rowById.get(row.evidence_id) as any).accepted_at)) <= markerAt)
const missingId = input.boundaries.delayed_evidence_id
const beforeAdmission = atMarker.filter((row: any) => row.evidence_id !== missingId)
assert.equal(atMarker.length, 16)
assert.equal(beforeAdmission.length, 15)
const binding = { runId: input.dispatch.run_id, sessionId: input.dispatch.dsh_session_id,
  generation: input.dispatch.generation, requestHash: input.dispatch.request_hash,
  requestId: badBody.command_request_id }
const upstream = JSON.parse(input.link_record.upstream_identity_json)
const link = { ...input.link_record, upstream_identity: upstream, deadline_at: wireTime(input.link_record.deadline_at),
  state: 'running', terminal_at: null } // Explicit test authority facade; no historical state claim.
const descriptors = input.census.sessions.filter((row: any) => row.session_id !== binding.sessionId)
  .map((row: any) => ({ sessionId: row.session_id, parentSessionId: row.parent_session_id,
    firstLiveSeq: row.first_live_seq, lifecycleId: row.lifecycle_id }))
const surface = (events: any[]) => foldSurface(events).nodes.map((seq: number) => deriveEventMessage(events[seq])).filter(Boolean)
const fingerprint = (value: unknown) => createHash('sha256').update(providerCanonicalJson(value)).digest('hex')
let clock = markerAt
const realNow = Date.now
Date.now = () => clock

function reader(events: any[], late: boolean, mode = 'stable', delivery?: any) {
  let committed = !late, canceled = false
  const calls: string[] = [], proofReads: string[] = []
  const session = { id: binding.sessionId, header: input.root.header_projection, events,
    firstLiveSeq: 0, get seq() { return events.length }, deriveMessages: () => surface(events),
    append(type: string, data: any, extra: any = {}) { events.push({ type, data, seq: events.length, time: clock, ...extra }) } }
  const agent: any = { id: binding.sessionId, status: 'running', session }
  const correlation = { ...binding, state: 'running', terminalAt: null, deadlineAt: link.deadline_at }
  const bridge: any = Object.create(DecisionHubHostBridge.prototype)
  const currentLink = structuredClone(link)
  if (mode === 'wrong-root') currentLink.run_id = 'different-run'
  if (mode === 'wrong-generation') currentLink.generation++
  Object.assign(bridge, {
    config: { runtimeMode: 'live', sourceCommit: upstream.source_commit, sourceVersion: upstream.source_version,
      pluginBuildHash: input.dispatch.plugin_build_hash, operationTimeoutMs: 60_000, maxBodyBytes: 8_388_608 },
    ctx: { agents: { get: (id: string) => id === agent.id ? agent : undefined },
      sessionController: { inspect: async (id: string) => {
        calls.push('inspect:' + (id === agent.id ? 'root' : 'child'))
        if (id === agent.id) return { meta: input.root.header_projection, events: structuredClone(events) }
        const original = children.find((child: any) => child.meta.id === id)
        assert(original, 'recorded_child_required')
        const selected = structuredClone(original.events.filter((event: any) => mode === 'outside-prefix' || event.time <= markerAt))
        if (id === 'b229a701-43a7-43d3-9aca-e632a429d300' && selected.some((event: any) => event.seq === 92)) committed = true
        if (mode === 'changed-prefix' && id === 'b229a701-43a7-43d3-9aca-e632a429d300') selected[92].time++
        return { meta: original.meta, events: selected }
      } } },
    correlations: { getSession: () => correlation, getRun: () => correlation },
    cancellations: { requested: () => canceled }, terminalDrains: { closingForSession: () => false },
    activity: { assertNotFenced: () => {} },
    nativeCensuses: new Map([[binding.sessionId, { correlation, collector: { researchSourceDescriptors: () => mode === 'no-child' ? [] : descriptors } }]]),
    hub: { linkBySession: async () => currentLink,
      researchDetail: async () => {
        calls.push('detail')
        const selected = structuredClone(mode === 'outside-prefix' ? input.independent_evidence : committed ? atMarker : beforeAdmission)
        const row = selected.find((item: any) => item.evidence_id === missingId)
        if (mode === 'missing-selected') return { run: { run_id: binding.runId }, evidence: selected.filter((item: any) => item.evidence_id !== missingId) }
        if (mode === 'changed-selected' && row) row.excerpt += ' changed for negative test'
        if (mode === 'lost-accepted' && row) row.quality = 'rejected'
        return { run: { run_id: binding.runId }, evidence: selected }
      },
      // Unused by this projection. No fabricated perpetual binding is supplied.
      perpetualResearchBinding: async () => undefined,
      providerToolEmission: async (run: string, generation: number, child: string, call: string) => {
        calls.push('proof'); proofReads.push(child + ':' + call)
        assert.equal(run, binding.runId); assert.equal(generation, binding.generation)
        const matches = input.proofs.filter((proof: any) => proof.execution_session_id === child && proof.call_id === call)
        assert.equal(matches.length, 1, 'recorded_provider_proof_required')
        const proof = structuredClone(matches[0])
        assert(Date.parse(proof.reference.recorded_at) <= clock, 'future_proof_forbidden')
        if (mode === 'wrong-provider') proof.generation++
        if (mode === 'cancel-during-proof') canceled = true
        if (mode === 'deadline-during-proof') clock = Date.parse(link.deadline_at)
        return proof
      } },
  })
  return { calls, proofReads, session, agent, async read() {
    return await bridge.readSynthesisReviewContext(agent, `research-request:${binding.runId}`,
      new AbortController().signal, currentLink, undefined, undefined, undefined, delivery ?? { refresh: true })
  } }
}
const errorText = (error: any) => {
  const message = String(error?.message ?? '')
  const known = /^(research_provider_native_context_invalid|decision_hub_review_context_(?:link_mismatch|binding_changed))$/.test(message)
  return { name: error?.name ?? null, constructor_name: error?.constructor?.name ?? null, code: error?.code ?? null,
    message: known ? message : null, message_sha256: known ? null : fingerprint(message),
    production_frames: String(error?.stack ?? '').split('\n').flatMap(line => {
      const match = line.replaceAll('\\', '/').match(/\/src\/host\/((?:retained-context|bridge|child-research-context)\.ts:\d+:\d+)/)
      return match ? [match[1]] : []
    }) }
}
const result: any = { recorded_run_id: binding.runId, source_refs: input.source_refs,
  scope: 'causal data replay with explicit authority/IO facades; not business acceptance', scenarios: [] }
try {
  const rootOnly = reader(structuredClone(allRoot.slice(0, input.boundaries.producer_events_last + 1)), false, 'no-child')
  const rootOnlyContext = await rootOnly.read()
  result.no_child = { sources: rootOnlyContext.childResearchSources.length,
    proof_reads: rootOnly.proofReads.length, detail_reads: rootOnly.calls.filter(x => x === 'detail').length }
  let stableEvents: any[]
  for (const name of ['late-admission', 'stable']) {
    clock = markerAt
    const events = structuredClone(allRoot.slice(0, input.boundaries.producer_events_last + 1))
    const unchanged = fingerprint(events), r = reader(events, name === 'late-admission')
    const context = await r.read(), rootUnchanged = fingerprint(events) === unchanged
    const proposed = makeRetainedRootContextMessage(context, input.dispatch.plugin_build_hash)
    const body = JSON.parse((proposed.content[1] as any).text)
    const replaced = replaceRetainedRootContextMessage(context, proposed, r.session as any, { estimateMessage: () => 1 } as any)
    assert(replaced)
    const actualSurface = surface(events), fresh = reader(events, false, 'stable', { messages: actualSurface })
    const freshContext = await fresh.read()
    let verified = true, error: any = null
    try { verifyRetainedRootContext({ ...allRoot[351].data.header.config, sessionId: binding.sessionId,
      messages: actualSurface, system: allRoot[351].data.header.system, tools: allRoot[351].data.header.tools },
    input.dispatch, link, freshContext) } catch (value) { verified = false; error = errorText(value) }
    result.scenarios.push({ name, evidence: body.evidence_candidates.length, facts: body.facts.length,
      has_delayed_evidence: body.evidence_candidates.some((row: any) => row.evidence_id === missingId),
      full_canonical_fresh_verifier_passed: verified, error, detail_reads: r.calls.filter(x => x === 'detail').length,
      root_events_unchanged_during_reads: rootUnchanged, producer_order: r.calls, proof_reads: r.proofReads.length,
      fresh_reconstructed_sources_are_new_objects: freshContext.childResearchSources.every((source: any, index: number) => source !== context.childResearchSources[index]) })
    if (name === 'stable') stableEvents = structuredClone(events)
  }
  clock = failureAt
  const originalEvents = structuredClone(allRoot.slice(0, 352)), originalSurface = surface(originalEvents)
  const historical = await reader(originalEvents, false, 'outside-prefix', { messages: originalSurface }).read()
  let oldRejected = false, oldError: any = null
  try { verifyRetainedRootContext({ ...allRoot[351].data.header.config, sessionId: binding.sessionId,
    messages: originalSurface, system: allRoot[351].data.header.system, tools: allRoot[351].data.header.tools }, input.dispatch, link, historical) }
  catch (error) { oldRejected = true; oldError = errorText(error) }
  result.original_bad_marker_still_rejected = oldRejected
  result.original_bad_marker_error = oldError
  result.negative_cases = []
  for (const mode of ['missing-selected', 'changed-selected', 'lost-accepted', 'wrong-root', 'wrong-generation',
    'wrong-provider', 'changed-prefix', 'cancel-during-proof', 'deadline-during-proof', 'outside-prefix']) {
    clock = failureAt
    const events = structuredClone(stableEvents!), messages = surface(events)
    let passed = true, error: any = null
    try {
      const fresh = await reader(events, false, mode, { messages }).read()
      verifyRetainedRootContext({ ...allRoot[351].data.header.config, sessionId: binding.sessionId,
        messages, system: allRoot[351].data.header.system, tools: allRoot[351].data.header.tools }, input.dispatch, link, fresh)
    } catch (value) { passed = false; error = errorText(value) }
    result.negative_cases.push({ mode, passed, error })
  }
} finally { Date.now = realNow }
console.log(JSON.stringify(result, null, 2))
