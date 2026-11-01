/** Pure declarations: no clock, IO, SQL origin, permission, or generated import. */
export const CONTEXT_SELECTION_MAX_BYTES = 16_777_216
export const CONTEXT_SELECTION_LINK_MAX_BYTES = 16_384
export const CONTEXT_SELECTION_MAX_CONSIDERED_INPUTS = 2050

const timestampPattern = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})$/
const utcLimit = 3652059n * 86_400_000_000n

export class ContextSelectionSemanticError extends Error {
  constructor(readonly code: string) { super(code) }
}

function timestampParts(value: unknown): { microseconds: bigint; fraction: string } {
  if (typeof value !== 'string') throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
  const match = timestampPattern.exec(value)
  if (!match || match[0] !== value) throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number)
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 ||
      day > lengths[month - 1] || hour > 23 || minute > 59 || second > 59) {
    throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
  }
  const previous = year - 1
  let days = 365 * previous + Math.floor(previous / 4) - Math.floor(previous / 100) + Math.floor(previous / 400)
  for (let index = 0; index < month - 1; index += 1) days += lengths[index]
  days += day - 1
  const zone = match[8]
  let offset = 0
  if (zone !== 'Z') {
    const offsetHour = Number(zone.slice(1, 3)), offsetMinute = Number(zone.slice(4, 6))
    if (offsetHour > 23 || offsetMinute > 59) throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
    offset = (offsetHour * 60 + offsetMinute) * (zone[0] === '+' ? 1 : -1)
  }
  const fraction = match[7] ?? ''
  const seconds = BigInt(days) * 86_400n + BigInt(hour * 3600 + minute * 60 + second - offset * 60)
  const microseconds = seconds * 1_000_000n + BigInt((fraction.slice(0, 6) + '000000').slice(0, 6))
  if (microseconds < 0n || microseconds >= utcLimit) throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
  return { microseconds, fraction }
}

export function contextTimestampShapeValid(value: unknown): boolean {
  try { timestampParts(value); return true } catch { return false }
}

export function parseContextMicroseconds(value: unknown, inherited = false): bigint {
  const { microseconds, fraction } = timestampParts(value)
  if (fraction.length > 6) {
    if (!inherited) throw new ContextSelectionSemanticError('context_selection_invalid_timestamp')
    if (/[^0]/.test(fraction.slice(6))) throw new ContextSelectionSemanticError('context_selection_time_precision_unrepresentable')
  }
  return microseconds
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function contextSelectionTimeIssueCodes(raw: unknown): string[] {
  const value = record(raw), issues: string[] = []
  const issue = (code: string) => { if (!issues.includes(code)) issues.push(code) }
  const pending: unknown[] = [raw]
  while (pending.length) {
    const item = pending.pop()
    if (typeof item === 'number' && !Number.isFinite(item)) issue('context_selection_non_finite_number')
    else if (Array.isArray(item)) { for (const child of item) pending.push(child) }
    else if (item !== null && typeof item === 'object') { for (const child of Object.values(item)) pending.push(child) }
  }
  const instant = (time: unknown, inherited = false): bigint | undefined => {
    try { return parseContextMicroseconds(time, inherited) } catch (error) {
      issue(error instanceof ContextSelectionSemanticError ? error.code : 'context_selection_invalid_timestamp')
      return undefined
    }
  }
  const recorded = instant(value.recorded_at)
  if (value.schema_version === 'research-context-selection.v1') {
    const pit = instant(value.original_pit_cutoff_at), effective = instant(value.effective_eligibility_cutoff_at)
    const decision = instant(value.decision_cutoff_before_selection), request = record(value.projected_request)
    const rawPit = instant(request.pit_cutoff_at, true)
    if (pit !== undefined && rawPit !== undefined && pit !== rawPit) issue('context_selection_original_pit_mismatch')
    if (value.execution_mode === 'live') {
      const clock = instant(value.live_selection_clock_at)
      if (pit !== undefined && clock !== undefined && effective !== (pit < clock ? pit : clock)) issue('context_selection_effective_cutoff_mismatch')
      if (recorded !== undefined && clock !== undefined && recorded < clock) issue('context_selection_recorded_before_selection_clock')
    } else if (value.execution_mode === 'replay') {
      if (value.live_selection_clock_at !== null) issue('context_selection_replay_clock_not_null')
      if (pit !== undefined && effective !== pit) issue('context_selection_effective_cutoff_mismatch')
    }
    if (value.decision_cutoff_basis === 'replay_original_pit' && decision !== pit) issue('context_selection_decision_cutoff_mismatch')
    for (const rawWitness of Array.isArray(value.considered_inputs) ? value.considered_inputs : []) {
      const witness = record(rawWitness)
      instant(record(witness.input).received_at, true)
      if (witness.row !== null && witness.row !== undefined) instant(record(witness.row).received_at, true)
    }
    for (const rawInput of Array.isArray(request.input_evidence) ? request.input_evidence : []) {
      const received = instant(record(rawInput).received_at, true)
      if (received !== undefined && effective !== undefined && received > effective) issue('context_selection_projected_received_after_cutoff')
    }
    if (request.run_id !== value.run_id || request.event_id !== value.event_id ||
        request.current_round !== value.graph_round || request.execution_mode !== value.execution_mode ||
        record(value.task_anchor).run_id !== value.run_id) issue('context_selection_declared_identity_mismatch')
  } else if (value.schema_version === 'research-context-selection-execution-link.v1') {
    const selectionTime = instant(record(value.selection_row_identity).recorded_at)
    if (recorded !== undefined && selectionTime !== undefined && selectionTime > recorded) issue('context_selection_link_before_selection')
    for (const key of ['execution_row_identity', 'prompt_integrity_row_identity']) {
      const row = record(value[key]), rowTime = instant(row.recorded_at)
      if (recorded !== undefined && rowTime !== undefined && rowTime !== recorded) issue('context_selection_execution_time_mismatch')
      if (row.run_id !== value.run_id) issue('context_selection_declared_identity_mismatch')
    }
    if (record(value.selection_row_identity).run_id !== value.run_id) issue('context_selection_declared_identity_mismatch')
    if (value.turn_kind === 'research_round' && value.selection_full_request_hash !== value.actual_execution_full_hash) issue('context_selection_normal_full_hash_mismatch')
  }
  return issues
}

/** Count the same serialized text as storage. JS object reserialization is not its authority. */
export function contextPayloadUtf8Size(serialized: string, limit?: number): number {
  let size = 0
  for (let index = 0; index < serialized.length; index += 1) {
    const unit = serialized.charCodeAt(index)
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const low = serialized.charCodeAt(index + 1)
      if (!(low >= 0xdc00 && low <= 0xdfff)) throw new ContextSelectionSemanticError('context_selection_invalid_unicode')
      index += 1; size += 4
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new ContextSelectionSemanticError('context_selection_invalid_unicode')
    } else size += unit < 0x80 ? 1 : unit < 0x800 ? 2 : 3
    if (limit !== undefined && size > limit) throw new ContextSelectionSemanticError('context_selection_payload_too_large')
  }
  return size
}
