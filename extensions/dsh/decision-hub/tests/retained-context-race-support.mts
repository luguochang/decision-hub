// Sealed historical prefix replay; IO/registry/token meter are synthetic facades.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
const repoRoot = resolve(import.meta.dirname, '../../../..')
const sdk = process.env.DSH_SESSION_ENTRY ?? resolve(repoRoot, '.cache/dsh-upstream/source/packages/core/session/lib/index.js')
if (createHash('sha256').update(readFileSync(sdk)).digest('hex') !== '6f7e9ef6d0f44be1955c98aac93fa676645ddb8e92e47ee80ee5e6d43bf88a7d') throw new Error('sealed-sdk-decoder-changed')
const { decodeStorageRecord, decodeSeqRanges, foldSurface, deriveEventMessage } = await import(pathToFileURL(sdk).href)
import { nativeCensusCanonical } from '../src/host/native-conversation-census.js'
import { declaredChildResearchDelivery, readChildResearchSources } from '../src/host/child-research-context.js'
import { canReuseRetainedRootContextMessage, makeRetainedRootContextMessage, replaceRetainedRootContextMessage } from '../src/host/retained-context.js'
import { readCanonicalResearchPrompt } from '../src/host/baseline.js'
import { DecisionHubHostBridge } from '../src/host/bridge.js'

const input = JSON.parse(readFileSync(0, 'utf8'))
const hash = (value: unknown) => createHash('sha256').update(nativeCensusCanonical(value)).digest('hex')
const decode = (rows: any[]) => rows.slice(1).flatMap(row => decodeStorageRecord(row.sourceEventSeqs === undefined
  ? row : { ...row, sourceEventSeqs: decodeSeqRanges(row.sourceEventSeqs, row.seq) }))
const root = decode(input.root.rows), rootId = input.root.rows[0].id
const marker = root.find((event: any) => event.seq === 570)!.data
const body = JSON.parse(marker.content[1].text)
const binding = { runId: body.run_id, generation: body.generation, sessionId: rootId,
  requestHash: body.request_hash, requestId: body.command_request_id }
const firstPrompt = root.find((event: any) => event.type === 'user/message' && event.data.source?.kind === 'user'
  && event.data.source.rpcId === binding.requestId)!
const request = readCanonicalResearchPrompt(firstPrompt.data.content[0].text, 8_388_608)
const output: any = { runId: body.run_id, rootArchiveSha256: input.root.sha256, boundaries: [], children: [] }
let delivered: any
for (const last of [568, 590, 591, 592]) {
  const events = root.filter((event: any) => event.seq <= last)
  const surface = foldSurface(events).nodes.map((seq: number) => deriveEventMessage(events[seq])).filter(Boolean)
  const markers = surface.filter((message: any) => message.role === 'user' && message.source?.plugin === 'decision-hub-protected-context')
  let stage = 'declaredChildResearchDelivery'
  try {
    delivered = declaredChildResearchDelivery(events, surface)
    stage = 'canReuseRetainedRootContextMessage-old-marker'
    const oldAuthenticated = last >= 570 && canReuseRetainedRootContextMessage({ binding, events } as any, marker as any, surface as any)
    output.boundaries.push({ last, surfaceCount: surface.length, markers: markers.map((m: any) => ({ id: m.id,
      canonicalSha256: hash(m), equalNative570: hash(m) === hash(marker) })),
      marker570CanonicalSha256: hash(marker), oldAuthenticated, declaredPrefixes: delivered?.prefixes.length ?? 0 })
  } catch (error: any) { output.boundaries.push({ last, stage, error: error.message, stack: error.stack?.split('\n').slice(0, 5) }) }
}
const children = new Map<string, any>()
const errorTime = root.find((event: any) => event.seq === 593)!.time
for (const child of input.children) {
  const events = decode(child.rows), descriptor = body.child_research_prefixes.find((p: any) => p.sessionId === child.rows[0].id)
  const atError = events.filter((event: any) => event.time <= errorTime)
  const historical = events.slice(descriptor.firstLiveSeq, descriptor.lastSeq + 1)
  const fresh = atError.slice(descriptor.firstLiveSeq)
  output.children.push({ id: child.rows[0].id, archiveSha256: child.sha256, decodedCount: events.length,
    contiguous: events.every((event: any, index: number) => event.seq === index),
    pinnedLastSeq: descriptor.lastSeq, pinnedHashMatch: hash(historical) === descriptor.eventsHash,
    pinnedBytes: Buffer.byteLength(nativeCensusCanonical(historical)), atErrorLastSeq: atError.at(-1)?.seq,
    atErrorBytes: Buffer.byteLength(nativeCensusCanonical(fresh)), headerKeys: Object.keys(child.rows[0]) })
  children.set(child.rows[0].id, { meta: child.rows[0], events: atError })
}
const proofs = new Map(input.proofs.map((proof: any) => [proof.execution_session_id + ':' + proof.call_id, proof]))
const descriptors = body.child_research_prefixes.map(({ sessionId, parentSessionId, firstLiveSeq, lifecycleId }: any) =>
  ({ sessionId, parentSessionId, firstLiveSeq, lifecycleId }))
const called: any[] = []
const base = { binding, descriptors, signal: new AbortController().signal, deadlineAt: input.deadlineAt,
  controller: { inspect: async (id: string) => children.get(id) },
  hub: { providerToolEmission: async (_run: string, _generation: number, child: string, call: string) => {
    const value = proofs.get(child + ':' + call)
    called.push({ child, call, found: value !== undefined })
    if (!value) throw new Error('replay-original-proof-missing')
    return value
  } } }
let refreshed: any
for (const [name, extra] of [['pinned', { declared: body.child_research_prefixes }], ['refresh', {}]] as const) {
  const before = called.length
  try {
    const sources = await readChildResearchSources({ ...base, ...extra } as any)
    if (name === 'refresh') refreshed = sources
    output[name] = { passed: true, proofReads: called.slice(before), prefixes: sources.map(s => s.prefix) }
  } catch (error: any) { output[name] = { passed: false, error: error.message, stack: error.stack?.split('\n').slice(0, 7), proofReads: called.slice(before) } }
}
const realNow = Date.now
Date.now = () => errorTime - 1
try {
  output.replacement = []
  for (const [snapshotLast, liveLast] of [[591, 591], [591, 592], [592, 592]]) {
    const events = root.filter((event: any) => event.seq <= snapshotLast)
    const live = root.filter((event: any) => event.seq <= liveLast)
    const context = { binding, request, events, admittedEvidence: body.evidence_candidates, childResearchSources: refreshed }
    const appended: any[] = []
    let stage = 'makeRetainedRootContextMessage'
    const derive = () => foldSurface(live).nodes.map((seq: number) => deriveEventMessage(live[seq])).filter(Boolean)
    try {
      const proposed = makeRetainedRootContextMessage(context as any, body.plugin_build_hash)
      stage = 'canReuseRetainedRootContextMessage'
      const reuse = canReuseRetainedRootContextMessage(context as any, proposed, derive() as any)
      stage = 'replaceRetainedRootContextMessage'
      const replaced = replaceRetainedRootContextMessage(context as any, proposed, { id: rootId, header: input.root.rows[0],
        events: live, deriveMessages: derive, append(type: string, data: any, extra: any = {}) {
          const event = { seq: live.length, time: errorTime - 1, type, data, ...extra }
          appended.push({ seq: event.seq, type: event.type }); live.push(event)
        } } as any, { estimateMessage: () => 1 } as any)
      output.replacement.push({ snapshotLast, liveLast, passed: true, reuse, replaced, appended })
    } catch (error: any) { output.replacement.push({ snapshotLast, liveLast, passed: false, stage,
      error: error.message, stack: error.stack?.split('\n').slice(0, 7), appended }) }
  }
  output.bridge = []
  for (const scenario of ['stable591', 'relay591to592', 'historical-rewrite', 'cancel-during-reread', 'deadline-during-reread']) {
    const live = structuredClone(root.filter((event: any) => event.seq <= (scenario === 'stable591' ? 591 : 592)))
    let reads = 0, canceled = false, now = errorTime - 1
    Date.now = () => now
    const appended: any[] = []
    const session: any = { id: rootId, header: input.root.rows[0], firstLiveSeq: 0, get seq() { return live.length }, events: live,
      deriveMessages: () => foldSurface(live).nodes.map((seq: number) => deriveEventMessage(live[seq])).filter(Boolean),
      append(type: string, data: any, extra: any = {}) {
        const event = { seq: live.length, time: now, type, data, ...extra }
        appended.push({ seq: event.seq, type: event.type }); live.push(event)
      } }
    const agent: any = { id: rootId, status: 'running', session }
    const correlation = { ...binding, deadlineAt: input.link.deadline_at, terminalAt: null, state: 'running' }
    const bridge: any = Object.create(DecisionHubHostBridge.prototype)
    Object.assign(bridge, {
      config: { runtimeMode: 'live', sourceCommit: input.link.upstream_identity.source_commit,
        sourceVersion: input.link.upstream_identity.source_version, pluginBuildHash: body.plugin_build_hash, operationTimeoutMs: 60_000 },
      ctx: { agents: { get: (id: string) => id === rootId ? agent : undefined }, get: () => ({ estimateMessage: () => 1 }) },
      correlations: { getSession: () => correlation, getRun: () => correlation },
      hub: { linkBySession: async () => input.link },
      cancellations: { requested: () => canceled }, terminalDrains: { closingForSession: () => false },
      activity: { assertNotFenced: () => {} },
      readSynthesisReviewContext: async () => {
        reads++
        if (reads > 1 && scenario === 'historical-rewrite') live[570].time++
        if (reads > 1 && scenario === 'cancel-during-reread') canceled = true
        if (reads > 1 && scenario === 'deadline-during-reread') now = Date.parse(input.link.deadline_at)
        return { binding, request, events: structuredClone(live.slice(0, reads === 1 ? 592 : live.length)),
          admittedEvidence: body.evidence_candidates, childResearchSources: refreshed }
      },
    })
    try {
      const result = await bridge.retainedPreStep({ agent, messages: [], turn: 0, step: 0,
        signal: new AbortController().signal }, async () => ({ kind: 'enter', messages: [] }))
      output.bridge.push({ scenario, passed: true, reads, resultKind: result.kind, appended })
    } catch (error: any) { output.bridge.push({ scenario, passed: false, reads, error: error.message,
      stack: error.stack?.split('\n').slice(0, 6), appended }) }
  }
} finally { Date.now = realNow }
process.stdout.write(JSON.stringify(output, null, 2) + '\n')
