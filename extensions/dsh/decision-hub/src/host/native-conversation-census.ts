import { createHash, randomUUID } from 'node:crypto'

export interface NativeCensusBinding {
  readonly run_id: string
  readonly generation: number
  readonly root_session_id: string
  readonly request_hash: string
  readonly plugin_build_hash: string
}
export interface NativeCensusEvent {
  readonly type: string
  readonly seq: number
  readonly time: number
  readonly data: Readonly<Record<string, unknown>>
}
export interface NativeCensusAgent {
  readonly id: string
  readonly status: 'idle' | 'running'
  readonly session: {
    readonly id: string
    readonly header: { readonly parentSession?: string; readonly seedLength?: number }
    readonly firstLiveSeq: number
    readonly seq: number
    readonly events: readonly NativeCensusEvent[]
  }
}
export interface NativeResearchSourceDescriptor {
  readonly sessionId: string
  readonly parentSessionId: string
  readonly firstLiveSeq: number
  readonly lifecycleId: string
}
export interface NativeSessionCensusV1 {
  readonly session_id: string
  readonly parent_session_id: string | null
  readonly lifecycle_id: string
  readonly birth_status: 'observed' | 'missing' | 'remote_unavailable'
  readonly first_live_seq: number | null
  readonly native_first_seq: number | null
  readonly native_last_seq: number | null
  readonly settled: boolean
  readonly terminal_seq: number | null
  readonly terminal_kind: string | null
  readonly capture_status: 'complete' | 'unknown'
  readonly missing_codes: readonly string[]
  readonly events_json: string | null
  readonly events_sha256: string | null
}
export interface NativeConversationCensusV1 extends NativeCensusBinding {
  readonly schema_version: 'decision-hub-native-conversation-census.v1'
  readonly registry_epoch_id: string
  readonly registry_status: 'complete' | 'unknown'
  readonly observer_installed_before_root_birth: boolean
  readonly registered_session_ids: readonly string[]
  readonly missing_codes: readonly string[]
  readonly sessions: readonly NativeSessionCensusV1[]
  readonly proof_scope: 'official_session_native_events'
  readonly model_understanding_verified: false
  readonly budget_qualified: false
  readonly financial_qualified: false
  readonly census_sha256: string
}

const MAX_BYTES = 8_388_608
const MAX_SESSIONS = 64
const MAX_EVENTS = 50_000
const controlKinds = new Set(['step/start', 'step/end', 'turn/start', 'turn/end'])
const sha = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex')

/** Lossless canonical protocol bytes; no stringify coercion/drop is allowed. */
export function nativeCensusCanonical(value: unknown, seen = new Set<object>(), depth = 0): string {
  if (depth > 256) throw new Error('native_census_json_invalid')
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) throw new Error('native_census_json_invalid')
    return JSON.stringify(value)
  }
  if (typeof value !== 'object' || value === null || (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype)
    || seen.has(value)) throw new Error('native_census_json_invalid')
  seen.add(value)
  try {
    return Array.isArray(value)
      ? `[${value.map(item => nativeCensusCanonical(item, seen, depth + 1)).join(',')}]`
      : `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${nativeCensusCanonical((value as Record<string, unknown>)[key], seen, depth + 1)}`).join(',')}}`
  } finally { seen.delete(value) }
}
function detached<T>(value: T): T {
  const copy: T = JSON.parse(nativeCensusCanonical(value))
  const freeze = (body: unknown): void => {
    if (typeof body !== 'object' || body === null) return
    for (const child of Object.values(body)) freeze(child)
    Object.freeze(body)
  }
  freeze(copy)
  return copy
}
type Entry = {
  agent?: WeakRef<NativeCensusAgent>
  session?: WeakRef<NativeCensusAgent['session']>
  body: NativeSessionCensusV1
  seen: Map<number, string>
  errors: Set<string>
  research?: ResearchResidency
}
type ResearchResidency = {
  agent?: WeakRef<NativeCensusAgent>
  session?: WeakRef<NativeCensusAgent['session']>
  headerHash: string
  disposed: boolean
  invalid: boolean
  sealedSeq: number | null
  sealedHash: string | null
}

/** One exact admitted Run/generation. It grants no authority to its callers. */
export class NativeConversationCensusCollector {
  private readonly epoch = randomUUID()
  private readonly entries = new Map<string, Entry>()
  private readonly errors = new Set<string>()
  private readonly binding: NativeCensusBinding
  private rootFinalized = false

  constructor(binding: NativeCensusBinding, private readonly installedBeforeBirth: boolean,
    private readonly maxBytes = MAX_BYTES) {
    if (!Number.isSafeInteger(binding.generation) || binding.generation < 1
      || !/^dsh_[a-f0-9]{64}$/.test(binding.root_session_id)
      || !/^[a-f0-9]{64}$/.test(binding.request_hash) || !/^[a-f0-9]{64}$/.test(binding.plugin_build_hash)
      || !binding.run_id || !Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > MAX_BYTES) throw new Error('native_census_binding_invalid')
    this.binding = detached(binding)
    if (!installedBeforeBirth) this.errors.add('native_registry_observer_unavailable')
    if (binding.generation !== 1) this.errors.add('native_previous_generation_uncaptured')
  }

  /** Fixed safe uncertainty; it is never a producer authority or completion flag. */
  unknown(code: 'native_registry_observer_unavailable' | 'native_root_terminal_unavailable'
    | 'native_remote_registry_unscoped' | 'native_lifecycle_observation_failed'): void {
    this.errors.add(code)
  }

  /** Call only from official agent/created, before the first runtime-driving hook. */
  created(agent: NativeCensusAgent): void {
    const parent = agent.session.header.parentSession ?? null
    const prior = this.entries.get(agent.id)
    if (prior) {
      if (this.rootFinalized) this.errors.add('native_registry_changed_after_terminal')
      // An idempotent repeated callback is harmless; a second residency is not
      // covered by v1. Never overwrite the first authenticated native capture.
      if (prior.agent?.deref() !== agent) prior.errors.add('native_session_reentered')
      this.resumeResearch(prior, agent)
      return
    }
    if (agent.id !== this.binding.root_session_id && !this.entries.has(parent ?? '')) return
    if (this.rootFinalized) this.errors.add('native_registry_changed_after_terminal')
    if (this.entries.size >= MAX_SESSIONS) { this.errors.add('native_registry_sessions_exceeded'); return }
    const first = agent.session.firstLiveSeq
    const entry: Entry = { agent: new WeakRef(agent), session: new WeakRef(agent.session), seen: new Map(), errors: new Set(),
      body: { session_id: agent.id, parent_session_id: parent, lifecycle_id: randomUUID(), birth_status: 'observed',
        first_live_seq: Number.isSafeInteger(first) && first >= 0 ? first : null, native_first_seq: null, native_last_seq: null,
        settled: false, terminal_seq: null, terminal_kind: null, capture_status: 'unknown', missing_codes: [], events_json: null, events_sha256: null } }
    this.entries.set(agent.id, entry)
    if (agent.session.id !== agent.id || (agent.id === this.binding.root_session_id && parent !== null)
      || entry.body.first_live_seq === null) entry.errors.add('native_session_identity_invalid')
    if (agent.id === this.binding.root_session_id && first !== 0) entry.errors.add('native_root_resumed_history_uncaptured')
    try {
      entry.research = { agent: new WeakRef(agent), session: new WeakRef(agent.session),
        headerHash: sha(nativeCensusCanonical(agent.session.header)), disposed: false,
        invalid: entry.errors.has('native_session_identity_invalid'), sealedSeq: null, sealedHash: null }
    } catch { entry.errors.add('native_session_identity_invalid') }
    // Import published preparation control events as well as future firehose
    // events. Constructor seed is excluded by the official firstLiveSeq.
    this.observeRange(entry, agent.session)
  }

  /** An independently declared missing/remote child never becomes a zero-call success. */
  unavailable(sessionId: string, parentId: string, remote = false): void {
    if (!this.entries.has(parentId) || !sessionId || this.entries.has(sessionId)) return
    if (this.entries.size >= MAX_SESSIONS) { this.errors.add('native_registry_sessions_exceeded'); return }
    this.entries.set(sessionId, { seen: new Map(), errors: new Set([remote ? 'native_remote_source_unavailable' : 'native_session_birth_missing']),
      body: { session_id: sessionId, parent_session_id: parentId, lifecycle_id: randomUUID(), birth_status: remote ? 'remote_unavailable' : 'missing',
        first_live_seq: null, native_first_seq: null, native_last_seq: null, settled: false, terminal_seq: null,
        terminal_kind: null, capture_status: 'unknown', missing_codes: [], events_json: null, events_sha256: null } })
  }

  event(session: NativeCensusAgent['session'], event: NativeCensusEvent): void {
    const entry = this.entries.get(session.id)
    if (!entry) {
      const parent = session.header.parentSession
      if (parent) this.unavailable(session.id, parent)
      else if (session.id === this.binding.root_session_id) this.errors.add('native_root_birth_missing')
      return
    }
    const research = entry.research
    if (research && (research.disposed || research.session?.deref() !== session
      || entry.body.parent_session_id !== (session.header.parentSession ?? null))) research.invalid = true
    if (entry.session?.deref() !== session || entry.body.parent_session_id !== (session.header.parentSession ?? null)) {
      entry.errors.add('native_session_identity_changed'); return
    }
    if (!Number.isSafeInteger(event.seq) || event.seq < (entry.body.first_live_seq ?? 0)) {
      entry.errors.add('native_event_sequence_invalid'); return
    }
    this.observeEvent(entry, event)
  }

  /** SDK emits this after quiescence, before session detach. Capture synchronously. */
  disposed(agent: NativeCensusAgent): void {
    const entry = this.entries.get(agent.id)
    if (!entry) {
      const parent = agent.session.header.parentSession
      if (parent) this.unavailable(agent.id, parent)
      return
    }
    this.sealResearch(entry, agent)
    this.capture(entry, agent)
    entry.agent = undefined
    entry.session = undefined
  }

  /** Existing root terminal authority must call this with its same idle root Agent. */
  finalizeRoot(agent: NativeCensusAgent): void {
    if (agent.id !== this.binding.root_session_id) { this.errors.add('native_root_identity_invalid'); return }
    const entry = this.entries.get(agent.id)
    if (!entry) { this.errors.add('native_root_birth_missing'); return }
    this.capture(entry, agent)
    this.rootFinalized = true
  }

  private observeEvent(entry: Entry, event: NativeCensusEvent): void {
    if (!controlKinds.has(event.type)) return
    try {
      const encoded = nativeCensusCanonical(event)
      const prior = entry.seen.get(event.seq)
      if (prior !== undefined && prior !== encoded) entry.errors.add('native_event_sequence_conflict')
      else if (prior === undefined) entry.seen.set(event.seq, encoded)
      if (this.chargedBytes() > this.maxBytes) { entry.errors.add('native_census_bytes_exceeded'); entry.seen.clear() }
    } catch { entry.errors.add('native_census_json_invalid') }
  }

  private observeRange(entry: Entry, session: NativeCensusAgent['session']): readonly NativeCensusEvent[] | null {
    const first = entry.body.first_live_seq
    if (first === null || !Number.isSafeInteger(session.seq) || session.seq < first
      || session.events.length !== session.seq || session.seq - first > MAX_EVENTS) {
      entry.errors.add('native_event_range_invalid'); return null
    }
    const own = session.events.slice(first)
    if (own.some((event, index) => event.seq !== first + index)) {
      entry.errors.add('native_event_range_invalid'); return null
    }
    for (const event of own) this.observeEvent(entry, event)
    return own
  }

  private capture(entry: Entry, agent: NativeCensusAgent): void {
    if (entry.agent?.deref() !== agent || entry.session?.deref() !== agent.session
      || agent.session.id !== agent.id || entry.body.parent_session_id !== (agent.session.header.parentSession ?? null)) {
      entry.errors.add('native_session_identity_changed'); return
    }
    if (agent.status !== 'idle') { entry.errors.add('native_session_not_quiescent'); return }
    const own = this.observeRange(entry, agent.session)
    if (own === null) return
    if (entry.errors.has('native_census_json_invalid')) return
    const selected = own.filter(event => controlKinds.has(event.type))
    const actual = new Map(selected.map(event => [event.seq, nativeCensusCanonical(event)]))
    if ([...entry.seen].some(([seq, bytes]) => actual.get(seq) !== bytes)) entry.errors.add('native_event_final_snapshot_changed')
    const starts = new Map<number, number>(), ends = new Map<number, number>()
    const stepStarts = new Map<string, number>(), stepEnds = new Map<string, number>()
    for (const event of selected) {
      const turn = event.data.turn
      if (!Number.isSafeInteger(turn) || (turn as number) < 0) { entry.errors.add('native_turn_identity_invalid'); continue }
      const table = event.type === 'turn/start' ? starts : event.type === 'turn/end' ? ends : null
      if (table) {
        if (table.has(turn as number)) entry.errors.add('native_turn_identity_invalid')
        table.set(turn as number, event.seq)
      } else {
        const step = event.data.step, key = `${turn}:${step}`
        if (!Number.isSafeInteger(step) || (step as number) < 0) entry.errors.add('native_step_identity_invalid')
        const steps = event.type === 'step/start' ? stepStarts : stepEnds
        if (steps.has(key)) entry.errors.add('native_step_identity_invalid')
        steps.set(key, event.seq)
      }
    }
    if (starts.size === 0 || starts.size !== ends.size
      || [...starts].some(([turn, seq]) => !ends.has(turn) || ends.get(turn)! <= seq)
      || stepStarts.size !== stepEnds.size
      || [...stepStarts].some(([key, seq]) => !stepEnds.has(key) || stepEnds.get(key)! <= seq)) entry.errors.add('native_session_terminal_missing')
    const terminal = selected.filter(event => event.type === 'turn/end').at(-1)
    const reason = terminal?.data.reason
    const kind = typeof reason === 'object' && reason !== null && 'kind' in reason && typeof reason.kind === 'string' ? reason.kind : null
    if (kind !== 'completed') entry.errors.add('native_session_not_completed')
    const notifications = selected.map(event => ({ method: 'session.event', payload: { sessionId: agent.id, event } }))
    const encoded = nativeCensusCanonical(notifications)
    entry.body = { ...entry.body, settled: true, native_first_seq: own[0]?.seq ?? null, native_last_seq: own.at(-1)?.seq ?? null,
      terminal_seq: terminal?.seq ?? null, terminal_kind: kind, events_json: encoded, events_sha256: sha(encoded) }
    entry.seen.clear()
    if (this.chargedBytes() > this.maxBytes) {
      entry.errors.add('native_census_bytes_exceeded')
      entry.body = { ...entry.body, events_json: null, events_sha256: null }
    }
  }

  private chargedBytes(): number {
    return [...this.entries.values()].reduce((total, entry) => total + 512
      + Buffer.byteLength(entry.body.events_json ?? '', 'utf8')
      + [...entry.seen.values()].reduce((bytes, event) => bytes + Buffer.byteLength(event, 'utf8'), 0), 0)
  }

  /** Research continuity is separate from the single-residency census-v1 proof. */
  private researchRange(entry: Entry, agent: NativeCensusAgent): boolean {
    const session = agent.session, first = entry.body.first_live_seq
    return first !== null && agent.id === entry.body.session_id && session.id === agent.id
      && entry.body.parent_session_id === (session.header.parentSession ?? null)
      && Number.isSafeInteger(session.firstLiveSeq) && session.firstLiveSeq >= first
      && Number.isSafeInteger(session.seq) && session.seq >= session.firstLiveSeq
      && session.events.length === session.seq && session.events.every((event, index) => event.seq === index)
  }

  private sealResearch(entry: Entry, agent: NativeCensusAgent): void {
    const research = entry.research
    if (!research || research.invalid) return
    try {
      if (research.disposed || research.agent?.deref() !== agent || research.session?.deref() !== agent.session
        || agent.status !== 'idle' || !this.researchRange(entry, agent)
        || sha(nativeCensusCanonical(agent.session.header)) !== research.headerHash) throw new Error('invalid')
      research.sealedSeq = agent.session.seq
      research.sealedHash = sha(nativeCensusCanonical(agent.session.events))
      research.disposed = true
      research.agent = undefined
      research.session = undefined
    } catch { research.invalid = true }
  }

  private resumeResearch(entry: Entry, agent: NativeCensusAgent): void {
    const research = entry.research
    if (!research || research.invalid) return
    if (research.agent?.deref() === agent && research.session?.deref() === agent.session) return
    try {
      const session = agent.session, floor = research.sealedSeq
      // The new constructor seed must be exactly the observed, disposed log.
      // firstLiveSeq advances per residency; the birth descriptor floor does not.
      if (!research.disposed || this.rootFinalized || entry.body.session_id === this.binding.root_session_id
        || floor === null || research.sealedHash === null || !this.researchRange(entry, agent)
        || session.firstLiveSeq !== floor || sha(nativeCensusCanonical(session.header)) !== research.headerHash
        || sha(nativeCensusCanonical(session.events.slice(0, floor))) !== research.sealedHash) throw new Error('invalid')
      // SDK appends this marker iff the restored seed does not already end in one.
      if (floor > 0 && session.events[floor - 1]?.type !== 'session/end-seed'
        && (session.events[floor]?.type !== 'session/end-seed'
          || Object.keys(session.events[floor]!.data).length !== 0)) throw new Error('invalid')
      research.agent = new WeakRef(agent)
      research.session = new WeakRef(session)
      research.disposed = false
    } catch { research.invalid = true }
  }

  /** Birth-observed lineage only. Tool originals must be independently inspected. */
  researchSourceDescriptors(): readonly NativeResearchSourceDescriptor[] {
    return detached([...this.entries.values()].filter(entry => entry.body.session_id !== this.binding.root_session_id
      && entry.body.birth_status === 'observed' && entry.body.first_live_seq !== null).map(entry => {
      if (entry.errors.has('native_session_identity_invalid') || !entry.research || entry.research.invalid
        || entry.body.parent_session_id === null) {
        throw new Error('research_provider_native_context_invalid')
      }
      return { sessionId: entry.body.session_id, parentSessionId: entry.body.parent_session_id,
        firstLiveSeq: entry.body.first_live_seq!, lifecycleId: entry.body.lifecycle_id }
    }).sort((a, b) => a.sessionId.localeCompare(b.sessionId)))
  }

  snapshot(): NativeConversationCensusV1 {
    const sessions = [...this.entries.values()].map(entry => {
      const reasons = new Set(entry.errors)
      if (!entry.body.settled) reasons.add('native_session_not_settled')
      if (entry.body.events_json === null) reasons.add('native_session_events_unavailable')
      return { ...entry.body, capture_status: reasons.size === 0 ? 'complete' as const : 'unknown' as const,
        missing_codes: [...reasons].sort() }
    }).sort((a, b) => a.session_id.localeCompare(b.session_id))
    const reasons = new Set(this.errors)
    if (!this.rootFinalized) reasons.add('native_root_terminal_unavailable')
    if (!this.entries.has(this.binding.root_session_id)) reasons.add('native_root_birth_missing')
    for (const session of sessions) for (const code of session.missing_codes) reasons.add(code)
    let body = { schema_version: 'decision-hub-native-conversation-census.v1' as const, registry_epoch_id: this.epoch, ...this.binding,
      registry_status: reasons.size === 0 ? 'complete' as const : 'unknown' as const,
      observer_installed_before_root_birth: this.installedBeforeBirth, registered_session_ids: sessions.map(item => item.session_id),
      missing_codes: [...reasons].sort(), sessions, proof_scope: 'official_session_native_events' as const,
      model_understanding_verified: false as const, budget_qualified: false as const, financial_qualified: false as const }
    // Nested raw JSON is escaped by the envelope. Account for the actual wire
    // bytes, not just the inner bodies. Overflow cannot grant complete coverage.
    if (Buffer.byteLength(nativeCensusCanonical(body), 'utf8') + 84 > this.maxBytes) {
      body = { ...body, registry_status: 'unknown',
        missing_codes: [...new Set([...body.missing_codes, 'native_census_bytes_exceeded'])].sort(),
        sessions: body.sessions.map(item => ({ ...item, capture_status: 'unknown',
          events_json: null, events_sha256: null,
          missing_codes: [...new Set([...item.missing_codes, 'native_census_bytes_exceeded'])].sort() })) }
    }
    const result = detached({ ...body, census_sha256: sha(nativeCensusCanonical(body)) })
    if (Buffer.byteLength(nativeCensusCanonical(result), 'utf8') > MAX_BYTES) throw new Error('native_census_bytes_exceeded')
    return result
  }
}

