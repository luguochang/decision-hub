// Local relations/calendar validation; does not authenticate a source or owner.
import { decisionCandidateV2Issues } from './decisionCandidateV2Semantics'
export function decisionV2UtcTimestampValid(value: string): boolean {
  const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,6})?Z$/.exec(value)
  if (!match || match[0] !== value) return false
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number)
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] && hour <= 23 && minute <= 59 && second <= 59
}

type Row = Record<string, any>

function comparisonIssues(raw: unknown): string[] {
  const issues: string[] = []
  const check = (ok: boolean, code: string) => { if (!ok) issues.push('decision_v2_comparison_' + code) }
  const same = (a: Row | null, b: Row | null): boolean => a === null || b === null ? a === b : a.ref === b.ref && a.hash === b.hash
  const unit = 1000000000000000000n
  const decimal = (value: unknown): bigint | null => {
    if (typeof value !== 'string' || !/^-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?$/.test(value)) return null
    const negative = value.startsWith('-'), [whole, fraction = ''] = value.replace(/^-/, '').split('.')
    const result = BigInt(whole) * unit + BigInt(fraction.padEnd(18, '0'))
    return negative ? -result : result
  }
  const mass = (values: unknown[]) => {
    const numbers = values.map(decimal)
    return numbers.some(value => value === null) || numbers.reduce<bigint>((sum, value) => sum + value!, 0n) === unit
  }
  const time = (value: string): bigint => {
    const millis = Date.parse(value)
    if (!Number.isFinite(millis)) return 0n // The canonical scalar rejects invalid dates.
    const fraction = /\.([0-9]{1,6})Z$/.exec(value)?.[1] ?? ''
    return BigInt(millis) * 1000n + BigInt(fraction.padEnd(6, '0').slice(3))
  }
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(visit); return }
    if (!value || typeof value !== 'object') return
    const row = value as Row, kind = row.schema_version
    if (kind === 'event-scenario-preparation-manifest.v1') return
    if (['target_ref', 'probabilities', 'as_of', 'kind'].every(key => Object.hasOwn(row, key))) {
      const probabilities = row.probabilities
      if (probabilities === null) check(row.kind === 'unavailable', 'unknown_probability_kind')
      else check(row.kind !== 'unavailable' && row.source_ref !== null &&
        new Set(probabilities.map((item: Row) => item.outcome_id)).size === probabilities.length &&
        mass(probabilities.map((item: Row) => item.value)), 'probability_partition')
      if (row.kind === 'calibrated') check(row.calibration_ref !== null, 'calibration_ref')
    }
    if (kind === 'target-spec.v1') check(time(row.window_end) - time(row.window_start) === ({ '4h': 14400000000n, '24h': 86400000000n, '72h': 259200000000n } as Row)[row.horizon], 'target_window')
    if (kind === 'event-probability.v1') check(same(row.occurrence_distribution.target_ref, row.occurrence_target_ref) && same(row.outcome_given_occurrence.target_ref, row.outcome_target_ref) && same(row.outcome_given_occurrence.conditioning_ref, row.event_definition_ref), 'event_target')
    if (kind === 'conditional-market-distribution.v1') {
      check(same(row.direction_distribution.target_ref, row.market_target_ref) && same(row.direction_distribution.conditioning_ref, row.conditioning_ref), 'market_target')
      const probabilities = row.direction_distribution.probabilities
      if (probabilities !== null) check(probabilities.length === 3 && ['up', 'neutral', 'down'].every(key => probabilities.some((item: Row) => item.outcome_id === key)), 'direction_partition')
    }
    if (row.kind === 'assumed_complete_paths') check(new Set(row.scenarios.map((item: Row) => item.scenario_id)).size === row.scenarios.length && mass(row.scenarios.map((item: Row) => item.probability)) && row.scenarios.every((item: Row) => { const number = decimal(item.terminal_return_fraction); return number === null || number > -unit }), 'path_partition')
    if (Object.keys(row).length === 3 && ['net_profit', 'zero', 'net_loss'].every(key => Object.hasOwn(row, key))) check(mass(Object.values(row)), 'outcome_mass')
    if (Object.hasOwn(row, 'comparison_to_forecast_price_ratio') && Object.hasOwn(row, 'forecast_price_ref') && (Object.hasOwn(row, 'comparison_price_ref') || Object.hasOwn(row, 'quote_ref'))) check(decimal(row.comparison_to_forecast_price_ratio) === unit || !same(row.forecast_price_ref, row.comparison_price_ref ?? row.quote_ref), 'price_bridge')
    if (kind === 'decision-method-inputs.v1' || kind === 'decision-method-inputs.v2' || kind === 'action-comparison.v2') {
      const bindings = row.bindings, market = row.market_distribution, event = row.event_probability
      const forecast = time(row.forecast_as_of), cutoff = time(kind === 'decision-method-inputs.v1' || kind === 'decision-method-inputs.v2' ? row.comparison_as_of : row.as_of)
      check(forecast <= cutoff, 'cutoff_order')
      check(time(row.price_bridge.known_at) <= cutoff, 'price_known_at')
      check(same(market.instrument_ref, bindings.instrument) && same(market.market_target_ref, bindings.target) && same(market.path_distribution_ref, bindings.paths) && same(market.event_scenario_ref, bindings.event_probability), 'market_bindings')
      check([event.occurrence_distribution, event.outcome_given_occurrence, market.direction_distribution].every(item => time(item.as_of) === forecast), 'probability_cutoff')
      const ids = row.position_branch === 'flat' ? ['open_long', 'open_short', 'no_change'] : ['no_change', 'reduce_half', 'close']
      if (kind === 'decision-method-inputs.v1' || kind === 'decision-method-inputs.v2') {
        const target = row.target, paths = row.path_distribution, utility = row.utility_policy
        check(row.scope_id === 'crypto:perp:' + row.instrument_id + ':trade:1m', 'scope')
        check(same(target.instrument_ref, bindings.instrument) && same(market.conditioning_ref, target.conditioning_ref) && same(paths.market_target_ref, bindings.target) && same(paths.conditioning_ref, target.conditioning_ref), 'target_bindings')
        check(time(row.forecast_known_at_max) <= forecast && time(row.comparison_known_at_max) <= cutoff && forecast <= time(target.window_start) && cutoff < time(target.window_end), 'target_clock')
        check(utility.policy_ref === bindings.utility.ref && utility.hash === bindings.utility.hash && same(utility.account_ref, kind === 'decision-method-inputs.v2' && row.account_snapshot !== null ? row.account_snapshot.original : bindings.account), 'utility_bindings')
        if (row.account_snapshot !== null) check(bindings.account !== null && time(row.account_snapshot.as_of) <= cutoff, 'account_cutoff')
        const refs = row.action_target_refs
        check(Object.keys(refs).length === ids.length && ids.every(key => Object.hasOwn(refs, key)) && new Set(Object.values(refs).map(item => JSON.stringify([(<Row>item).ref, (<Row>item).hash]))).size === ids.length, 'action_targets')
        if (paths.supports_market_direction) {
          const probabilities = market.direction_distribution.probabilities, threshold = decimal(target.threshold_fraction)
          if (threshold !== null && paths.scenarios.every((item: Row) => decimal(item.probability) !== null && decimal(item.terminal_return_fraction) !== null)) {
            const grouped: Record<string, bigint> = { up: 0n, neutral: 0n, down: 0n }
            for (const item of paths.scenarios) {
              const terminal = decimal(item.terminal_return_fraction)!
              grouped[terminal > threshold ? 'up' : terminal < -threshold ? 'down' : 'neutral'] += decimal(item.probability)!
            }
            check(probabilities !== null && probabilities.length === 3 && probabilities.every((item: Row) => grouped[item.outcome_id] === decimal(item.value)), 'direction_coherence')
          }
        }
      } else {
        check(['crypto:perp:okx:BTC-USDT-SWAP:trade:1m', 'crypto:perp:okx:ETH-USDT-SWAP:trade:1m'].includes(row.scope_id) && same(row.target_ref, bindings.target), 'output_target')
        const rows = row.candidate_results
        check(rows.length === ids.length && new Set(rows.map((item: Row) => item.candidate_id)).size === ids.length && ids.every(key => rows.some((item: Row) => item.candidate_id === key)), 'candidate_partition')
        const ranked = [...(row.selected_action_id === null ? [] : [row.selected_action_id]), ...row.alternative_action_ids], allowed = rows.filter((item: Row) => item.feasibility === 'unknown').map((item: Row) => item.candidate_id)
        check(new Set(ranked).size === ranked.length && ranked.length === allowed.length && allowed.every((id: string) => ranked.includes(id)), 'selected_candidates')
        for (const item of rows) check(same(item.outcome.target_ref, item.action_target_ref) && same(item.outcome.source_ref, bindings.paths) && same(item.outcome.conditioning_ref, market.conditioning_ref), 'outcome_bindings')
      }
    }
    Object.values(row).forEach(visit)
  }
  visit(raw)
  return issues
}

export function decisionProductV2Issues(raw: unknown): string[] {
  const issues: string[] = [...comparisonIssues(raw), ...decisionCandidateV2Issues(raw)]
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(visit); return }
    if (!value || typeof value !== 'object') return
    const row = value as Row, kind = row.schema_version
    if (kind === 'event-scenario-preparation-manifest.v1') return
    if (kind === 'instrument-contract.v1' && ((row.effective_at === null) !== (row.effective_at_source === 'unknown'))) issues.push('decision_v2_instrument_effective_source_pair')
    if (kind === 'position-context.v2') {
      if ((row.ref === null) !== (row.hash === null)) issues.push('decision_v2_position_ref_pair')
      if (['verified', 'paper_policy'].includes(row.source) && row.ref === null) issues.push('decision_v2_position_original_required')
      if (row.source === 'unknown' && (row.state !== 'unknown' || row.ref !== null ||
          ['signed_contracts', 'entry_price', 'isolated_margin', 'leverage', 'liquidation_estimate'].some(key => row[key] !== null))) issues.push('decision_v2_unknown_position_has_values')
      if (row.position_mode === 'hedge') {
        if (!['long', 'short'].includes(row.hedge_leg) || ![row.hedge_leg, 'flat', 'unknown'].includes(row.state)) issues.push('decision_v2_hedge_leg_mismatch')
      } else if (row.hedge_leg !== null) issues.push('decision_v2_net_has_hedge_leg')
    }
    if (kind === 'account-snapshot.v2' && ['verified', 'paper_policy'].includes(row.source) && row.original === null) issues.push('decision_v2_account_original_required')
    if (kind === 'decision-settings-command.v1' || kind === 'decision-settings-revision.v1') {
      if ((row.operation === 'remove') !== (row.values === null)) issues.push('decision_v2_settings_operation_values')
      if (row.values !== null) {
        const body = row.values, partition = row.partition, account = body.account_snapshot
        if (account.account_kind !== partition.account_kind || account.account_id !== partition.account_id) issues.push('decision_v2_settings_account_mismatch')
        if (body.position_context.instrument_id !== partition.instrument_id) issues.push('decision_v2_settings_instrument_mismatch')
        if (body.execution_profile.ref !== partition.profile_ref) issues.push('decision_v2_settings_profile_mismatch')
        if (kind === 'decision-settings-revision.v1' && account.owner_id !== row.owner_id) issues.push('decision_v2_settings_owner_mismatch')
      }
      if (kind === 'decision-settings-revision.v1' && ((row.revision_no === 1) !== (row.parent === null))) issues.push('decision_v2_settings_parent_mismatch')
    }
    const keys = Object.keys(row)
    if (keys.length === 5 && ['instrument', 'execution_profile', 'account_snapshot', 'position_context', 'utility_policy'].every(key => Object.hasOwn(row, key))) {
      const expected = row.instrument, actual = row.position_context.instrument_ref
      if (expected.ref !== actual.ref || expected.hash !== actual.hash) issues.push('decision_v2_settings_instrument_ref_mismatch')
    }
    Object.values(row).forEach(visit)
  }
  visit(raw)
  return issues
}
