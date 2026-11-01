/** Wire relations only. This does not authenticate a quote or authorize a fill. */
import { ContextSelectionSemanticError, parseContextMicroseconds } from './contextSelectionSemantics'

type Value = Record<string, unknown>
const object = (v: unknown): Value => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Value : {}
const list = (v: unknown): unknown[] => Array.isArray(v) ? v : []
const nonnull = (v: unknown) => v !== null && v !== undefined

/** Compare validated fixed-point strings without Number or rounding. */
function decimalCompare(a: unknown, b: unknown): number {
  if (![a, b].every(x => typeof x === 'string' && /^-?[0-9]+(?:\.[0-9]+)?$/.test(x))) return Number.NaN
  const parts = (x: unknown) => {
    const text = String(x), negative = text.startsWith('-')
    const [whole, fraction = ''] = (negative ? text.slice(1) : text).split('.')
    return { negative, digits: whole + fraction, scale: fraction.length }
  }
  const x = parts(a), y = parts(b), scale = Math.max(x.scale, y.scale)
  const left = BigInt(x.digits + '0'.repeat(scale - x.scale)) * (x.negative ? -1n : 1n)
  const right = BigInt(y.digits + '0'.repeat(scale - y.scale)) * (y.negative ? -1n : 1n)
  return left < right ? -1 : left > right ? 1 : 0
}

export function actionExecutionIssueCodes(kind: string, value: Value): string[] {
  const issues: string[] = []
  const check = (ok: boolean, code: string) => { if (!ok && !issues.includes(code)) issues.push(code) }
  const time = (v: unknown): bigint => {
    try { return parseContextMicroseconds(v) } catch (error) {
      if (!(error instanceof ContextSelectionSemanticError)) throw error
      check(false, 'execution_timezone_required'); return 0n
    }
  }
  const d = (key: string, other: unknown = '0') => decimalCompare(value[key], other)
  const present = (key: string) => nonnull(value[key])
  for (const key of Object.keys(value)) {
    if (key.endsWith('_ref') && Object.hasOwn(value, key.slice(0, -4) + '_hash')) check(present(key) === present(key.slice(0, -4) + '_hash'), 'execution_reference_pair_invalid')
  }
  if ('channel' in value) check((value.channel === 'pc_visible') === present('owner_id'), 'execution_channel_owner_invalid')
  if ('version' in value && 'previous_ref' in value) check((value.version === 1) === !present('previous_ref'), 'execution_previous_version_invalid')
  if (kind === 'action_execution_protocol_v1') {
    check(time(value.registered_at) <= time(value.T0) && time(value.T0) < time(value.T1) && time(value.T1) <= time(value.label_deadline), 'execution_protocol_time_invalid')
    const horizons = [value.primary_horizon, ...list(value.diagnostic_horizons)]
    check(new Set(horizons).size === horizons.length, 'execution_protocol_horizon_duplicate')
    check(JSON.stringify([...horizons].sort()) === JSON.stringify(list(value.targets).map(x => object(x).horizon).sort()), 'execution_protocol_target_mismatch')
    for (const values of [list(value.scopes), list(value.channels), list(value.delays), list(value.methods).map(x => object(x).ref)]) check(values.length === new Set(values).size, 'execution_protocol_duplicate_dimension')
  }
  if (kind === 'action_execution_intent_v1') {
    check(value.action === ({ entry: 'enter_long', reduce: 'reduce_long', exit: 'exit_long' } as Record<string, string>)[String(value.phase)], 'execution_phase_action_invalid')
    const conditional = ['condition_entry', 'condition_exit', 'invalidation'].includes(String(value.reason))
    const quote = ['stop', 'target'].includes(String(value.reason)), expiry = value.reason === 'expiry', entry = value.phase === 'entry'
    check(value.source_kind === (conditional ? 'condition' : quote ? 'quote' : 'deadline'), 'execution_source_kind_invalid')
    check(entry === (value.reason === 'condition_entry'), 'execution_entry_reason_invalid')
    for (const key of ['evaluation_ref', 'evaluation_input_ref', 'evaluation_available_at']) check(present(key) === conditional, 'execution_evaluation_source_required')
    check(present('source_quote_ref') === quote && present('availability_hash') === quote, 'execution_quote_source_required')
    check(present('deadline_basis') === expiry, 'execution_deadline_source_required')
    check(present('episode_ref') === !entry, 'execution_episode_source_required')
    check(present('entry_valid_until') === entry, 'execution_entry_deadline_invalid')
    check(present('exit_trigger_deadline') === !entry, 'execution_exit_deadline_invalid')
    const pc = value.channel === 'pc_visible'
    check(pc === present('display_receipt_ref') && pc === present('display_received_at'), 'execution_display_receipt_required')
    const known = ['publication_available_at', 'trigger_input_known_at', 'admission_valid_at', 'observed_committed_at', 'evaluation_available_at', 'display_received_at'].filter(present).map(key => time(value[key]))
    check(time(value.base_action_at) === known.reduce((a, b) => a > b ? a : b), 'execution_base_action_time_invalid')
    check(time(value.eligible_at) === time(value.base_action_at) + BigInt(Number(value.delay_seconds)) * 1_000_000n, 'execution_delay_invalid')
    check(time(value.quote_deadline) === time(value.eligible_at) + 60_000_000n, 'execution_quote_window_invalid')
    check(time(value.created_at) >= time(value.observed_committed_at), 'execution_commit_observation_invalid')
    check(time(value.U_measurement_end) <= time(value.U_knowledge_cutoff) && time(value.U_known_at) <= time(value.U_knowledge_cutoff), 'execution_u_time_invalid')
    if (expiry) check(time(value.deadline_basis) === time(value.exit_trigger_deadline) && time(value.trigger_input_known_at) >= time(value.deadline_basis), 'execution_expiry_basis_invalid')
  }
  if (kind === 'action_fill_v1') {
    check(time(value.provider_observed_at) <= time(value.received_at) && time(value.received_at) <= time(value.available_at) && time(value.available_at) <= time(value.executed_at), 'execution_fill_time_invalid')
  }
  if (kind === 'action_fill_v2') {
    check(time(value.observed_local_lower) <= time(value.observed_local_upper) && time(value.observed_local_upper) <= time(value.received_at) && time(value.received_at) <= time(value.available_at) && time(value.available_at) <= time(value.executed_at), 'execution_fill_local_time_invalid')
    check(time(value.executed_at) - time(value.observed_local_lower) <= 10_000_000n, 'execution_fill_local_quote_stale')
    check(value.clock_ref === 'clock_' + String(value.clock_hash), 'execution_fill_clock_identity_invalid')
  }
  if (kind === 'action_fill_v1' || kind === 'action_fill_v2') {
    check(d('bid', value.ask) <= 0, 'execution_quote_crossed')
    check(value.raw_price === (value.side === 'buy' ? value.ask : value.bid), 'execution_raw_side_price_invalid')
    if (value.side === 'buy') check(!present('gross') && !present('cost') && !present('net') && d('cash_delta') < 0 && d('execution_price', value.raw_price) >= 0, 'execution_buy_accounting_invalid')
    else check(['gross', 'cost', 'net'].every(present) && d('execution_price', value.raw_price) <= 0, 'execution_sell_accounting_invalid')
  }
  if (kind === 'action_paper_account_values_v1') {
    const empty = d('quantity') === 0
    check(!empty || ['raw_basis', 'entry_fees', 'entry_slippage'].every(key => d(key) === 0), 'execution_empty_position_basis_invalid')
    check(empty || d('raw_basis') > 0, 'execution_position_basis_required')
  }
  if (kind === 'action_paper_checkpoint_v1') {
    issues.push(...actionExecutionIssueCodes('action_paper_portfolio_identity_v1', object(value.portfolio_identity)))
    issues.push(...actionExecutionIssueCodes('action_paper_account_values_v1', object(value.account_after)))
    check(value.portfolio_id === object(value.portfolio_identity).id && value.identity_hash === object(value.portfolio_identity).identity_hash, 'execution_checkpoint_identity_invalid')
    const opening = value.event_kind === 'opening'
    check(opening === (value.version === 1) && opening === !present('account_before_hash'), 'execution_opening_version_invalid')
    check(present('fill_ref') === (value.event_kind === 'fill'), 'execution_checkpoint_fill_invalid')
    check(!present('intent_ref') === opening, 'execution_checkpoint_intent_invalid')
    check((list(value.opening_original_refs).length > 0) === opening, 'execution_opening_original_required')
  }
  if (kind === 'action_execution_episode_v1') {
    const bought = value.opening_kind === 'buy_fill'
    check(bought === present('entry_intent_ref') && bought === present('entry_fill_ref'), 'execution_episode_opening_invalid')
    check(d('remaining_quantity', value.initial_quantity) <= 0, 'execution_episode_quantity_invalid')
    check((value.status === 'closed') === (d('remaining_quantity') === 0), 'execution_episode_closed_quantity_invalid')
    if (value.status === 'open') check(d('initial_quantity', value.remaining_quantity) === 0, 'execution_episode_open_quantity_invalid')
    if (value.status === 'partially_closed') check(d('remaining_quantity') > 0 && d('remaining_quantity', value.initial_quantity) < 0, 'execution_episode_partial_quantity_invalid')
    check(d('stop_price', value.reference_price) < 0 && d('reference_price', value.target_price) < 0, 'execution_episode_barriers_invalid')
    check(time(value.entry_at) < time(value.exit_trigger_deadline) && time(value.entry_at) <= time(value.recorded_at), 'execution_episode_time_invalid')
    check(d('price_coverage', '1') <= 0, 'execution_coverage_invalid')
  }
  if (kind === 'action_outcome_v2') {
    check(present('episode_ref') === present('episode_version'), 'execution_outcome_episode_invalid')
    check(d('coverage', '1') <= 0, 'execution_coverage_invalid')
    if (present('quantity') && present('remaining_quantity')) check(d('remaining_quantity', value.quantity) <= 0, 'execution_outcome_quantity_invalid')
    check((value.execution_status === 'pending') === !present('execution_ref'), 'execution_outcome_result_required')
    if (present('entry_at') && present('exit_at')) check(time(value.entry_at) <= time(value.exit_at), 'execution_outcome_time_invalid')
  }
  if (kind === 'action_execution_preview_v1') {
    check(present('portfolio_id') !== present('initial_scenario'), 'execution_preview_identity_invalid')
    check(time(value.as_of) < time(value.expires_at), 'execution_preview_window_invalid')
    if (present('reduce_fraction')) check(d('reduce_fraction', '1') <= 0, 'execution_reduce_fraction_invalid')
    if (value.status === 'available') check(present('quote_ref') && present('U_basis_ref') && present('price') && list(value.gaps).length === 0, 'execution_preview_inputs_required')
  }
  if (kind === 'action_execution_receipt_v1') {
    const filled = value.status === 'filled'
    check(filled === present('fill_ref'), 'execution_receipt_fill_invalid')
    if (filled) check(present('first_quote_ref') && present('checkpoint_ref') && present('episode_ref'), 'execution_receipt_filled_originals_required')
  }
  return [...new Set(issues)]
}
