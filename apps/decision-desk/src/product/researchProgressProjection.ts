import type { ResearchRunDetailView } from '@decision-hub/contracts-ts'

type WorkRecord = { kind: 'tool' | 'model'; reference: string; session: string; startedAt: string; sequence: number }
export type ProgressProjection = {
  currentConfirmed: boolean
  taskRecordedAt: string
  inFlight: WorkRecord[]
  unfinishedAtStop: WorkRecord[]
  materials: { reference: string; contentHash: string; requirement: string }[]
  gaps: NonNullable<ResearchRunDetailView['run']['coverage']>['gaps']
  duplicateCallRecords: number
  sameSummaryCalls: number
  nativeStop: ResearchRunDetailView['native_execution_failure']
  firstNativeFailure: ResearchRunDetailView['first_native_failure']
  currentNativeFailure: ResearchRunDetailView['current_authenticated_native_failure']
  failureCoverage: ResearchRunDetailView['native_failure_coverage']
  laterWork: { reference: string | null; sequence: number; occurredAt: string }[]
  finalError: string | null
  issues: string[]
}

const active = new Set(['admitted', 'queued', 'researching', 'retry_wait'])
const finiteTime = (value: string) => Number.isFinite(Date.parse(value))

/** Current API records only; elapsed time, heartbeat and prose never prove gain. */
export function projectResearchProgress(detail: ResearchRunDetailView, confirmed = true): ProgressProjection {
  const run = detail.run, issues: string[] = [], pending = new Map<string, WorkRecord>()
  const seenStarts = new Set<string>(), completed = new Set<string>()
  let duplicateCallRecords = 0
  const trace = detail.trace.filter(item => item.run_id === run.run_id).sort((a, b) => a.sequence_no - b.sequence_no)
  if (trace.length !== detail.trace.length) issues.push('trace_run_mismatch')
  for (const item of trace) {
    if (!item.reference_id || !finiteTime(item.occurred_at)) continue
    const kind = item.event_type.startsWith('tool_') ? 'tool' : item.event_type.startsWith('model_step_') ? 'model' : null
    if (!kind) continue
    const key = JSON.stringify([kind, item.research_session_id, item.reference_type, item.reference_id])
    if (item.event_type === 'tool_started' || item.event_type === 'model_step_started') {
      if (seenStarts.has(key)) { duplicateCallRecords++; continue }
      seenStarts.add(key)
      if (!completed.has(key)) pending.set(key, { kind, reference: item.reference_id,
        session: item.research_session_id, startedAt: item.occurred_at, sequence: item.sequence_no })
    } else {
      completed.add(key); pending.delete(key)
    }
  }
  const materialMap = new Map<string, ProgressProjection['materials'][number]>(), conflicts = new Set<string>()
  for (const item of detail.evidence) {
    if (item.quality !== 'accepted') continue
    if (!/^[a-f0-9]{64}$/.test(item.content_hash)) { issues.push('material_hash_unavailable'); continue }
    const prior = materialMap.get(item.evidence_id)
    if (prior && prior.contentHash !== item.content_hash) { conflicts.add(item.evidence_id); continue }
    materialMap.set(item.evidence_id, { reference: item.evidence_id, contentHash: item.content_hash,
      requirement: item.requirement_id })
  }
  for (const ref of conflicts) { materialMap.delete(ref); issues.push('material_identity_conflict') }
  const calls = detail.rounds.flatMap(round => round.tool_invocations.map(call => ({ round: round.round, call })))
  const summaries = new Map<string, Set<string>>()
  for (const { round, call } of calls) {
    const key = JSON.stringify([round, call.capability_id, call.requirement_id, call.query_summary])
    const ids = summaries.get(key) ?? new Set<string>(); ids.add(call.tool_call_id); summaries.set(key, ids)
  }
  // A summary match cannot establish identical actual arguments or duplicate HTTP.
  const sameSummaryCalls = [...summaries.values()].reduce((total, ids) => total + Math.max(0, ids.size - 1), 0)
  const candidateStop = detail.native_execution_failure
  const nativeStop = candidateStop?.run_id === run.run_id && finiteTime(candidateStop.terminal_at) ? candidateStop : null
  const laterWork = nativeStop ? trace.filter(item => Date.parse(item.occurred_at) > Date.parse(nativeStop.terminal_at))
    .map(item => ({ reference: item.reference_id, sequence: item.sequence_no, occurredAt: item.occurred_at })) : []
  const rawCoverage = detail.native_failure_coverage
  const failureCoverage = rawCoverage?.run_id === run.run_id ? rawCoverage : null
  if (rawCoverage && !failureCoverage) issues.push('native_failure_coverage_run_mismatch')
  const currentCandidate = detail.current_authenticated_native_failure
  const currentNativeFailure = currentCandidate?.run_id === run.run_id
    && currentCandidate.event_id === run.event_id && finiteTime(currentCandidate.occurred_at)
    && finiteTime(currentCandidate.recorded_at) && Date.parse(currentCandidate.occurred_at) <= Date.parse(currentCandidate.recorded_at)
    && currentCandidate.generation === failureCoverage?.current_generation
    && failureCoverage.covered_generations.includes(currentCandidate.generation) ? currentCandidate : null
  if (currentCandidate && !currentNativeFailure) issues.push('native_failure_current_identity_invalid')
  const firstCandidate = detail.first_native_failure
  const fullyCovered = failureCoverage?.status === 'complete' && failureCoverage.earliest_status === 'authenticated_failure'
    && failureCoverage.uncovered_generations.length === 0
    && failureCoverage.covered_generations.length === failureCoverage.current_generation
    && failureCoverage.covered_generations.every((generation, index) => generation === index + 1)
  const firstNativeFailure = fullyCovered && firstCandidate?.run_id === run.run_id
    && firstCandidate.event_id === run.event_id && finiteTime(firstCandidate.occurred_at)
    && finiteTime(firstCandidate.recorded_at) && Date.parse(firstCandidate.occurred_at) <= Date.parse(firstCandidate.recorded_at)
    && failureCoverage.covered_generations.includes(firstCandidate.generation) ? firstCandidate : null
  if (firstCandidate && !firstNativeFailure) issues.push('native_failure_earliest_coverage_invalid')
  const open = [...pending.values()]
  return { currentConfirmed: confirmed, taskRecordedAt: run.updated_at,
    inFlight: confirmed && active.has(run.status) ? open : [],
    unfinishedAtStop: active.has(run.status) ? [] : open, materials: [...materialMap.values()],
    gaps: run.coverage?.gaps ?? [], duplicateCallRecords, sameSummaryCalls,
    nativeStop, firstNativeFailure, currentNativeFailure, failureCoverage, laterWork, finalError: run.failure?.error_code ?? null, issues }
}
