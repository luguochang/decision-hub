// Local consistency only. Source/session/owner authentication belongs to readers.
type Row = Record<string, any>

export function decisionCandidateV2Issues(raw: unknown): string[] {
  const issues: string[] = []
  const check = (ok: boolean, code: string) => { if (!ok) issues.push('decision_v2_candidate_' + code) }
  const same = (a: unknown, b: unknown): boolean => {
    if (a === b) return true
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => same(item, b[index]))
    if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
    const left = a as Row, right = b as Row
    return Object.keys(left).length === Object.keys(right).length && Object.keys(left).every(key => Object.hasOwn(right, key) && same(left[key], right[key]))
  }
  const time = (value: string): bigint => {
    const millis = Date.parse(value)
    if (!Number.isFinite(millis)) return 0n
    const fraction = /\.([0-9]{1,6})Z$/.exec(value)?.[1] ?? ''
    return BigInt(millis) * 1000n + BigInt(fraction.padEnd(6, '0').slice(3))
  }
  const decimal = (value: unknown): bigint | null => {
    if (typeof value !== 'string' || !/^-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?$/.test(value)) return null
    const negative = value.startsWith('-'), [whole, fraction = ''] = value.replace(/^-/, '').split('.')
    const result = BigInt(whole) * 1000000000000000000n + BigInt(fraction.padEnd(18, '0'))
    return negative ? -result : result
  }
  const abs = (value: bigint) => value < 0n ? -value : value
  const durations: Row = { '4h': 14400, '24h': 86400, '72h': 259200 }
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(visit); return }
    if (!value || typeof value !== 'object') return
    const row = value as Row, kind = row.schema_version
    if (kind === 'event-scenario-preparation-manifest.v1') return
    if (['event-scenario-action-plan-draft.v1', 'event-scenario-action-plan.v1'].includes(kind)) {
      const event = row.event, context = row.context, freeze = time(row.freeze_before)
      check(time(row.known_at_max) <= time(row.as_of) && time(row.as_of) < freeze && freeze < time(event.scheduled_at), 'event_plan_freeze')
      const scenarios = new Map<string, Row>(row.scenario_predicates.map((item: Row) => [item.scenario_id, item]))
      check(scenarios.size === row.scenario_predicates.length, 'event_plan_scenarios')
      check(same(row.outcome_given_occurrence.conditioning_ref, event.event_definition_ref), 'event_plan_p_condition')
      const probabilities: Row[] | null = row.outcome_given_occurrence.probabilities
      check(probabilities !== null && probabilities.length === scenarios.size && probabilities.every(item => scenarios.has(item.outcome_id)), 'event_plan_p_partition')
      check(new Set(row.branches.map((item: Row) => item.branch_id)).size === row.branches.length, 'event_plan_branches')
      for (const branch of row.branches as Row[]) {
        check(scenarios.has(branch.scenario_id), 'event_plan_branch_scenario')
        if (!scenarios.has(branch.scenario_id)) continue
        const condition = scenarios.get(branch.scenario_id)!.definition_ref
        const target = branch.comparison_inputs_by_position.flat.target, domain = branch.applicability_domain
        check(['scheduled_pre_release', 'post_release_response'].includes(target.anchor), 'event_plan_anchor')
        const start = time(event.scheduled_at) + (target.anchor === 'scheduled_pre_release' ? -60000000n : 60000000n)
        check(time(target.window_start) === start, 'event_plan_start')
        check(start <= time(branch.applicable_from) && time(branch.applicable_from) < time(branch.valid_until) && time(branch.valid_until) <= time(domain.valid_until) && time(domain.valid_until) <= time(target.window_end), 'event_plan_expiry')
        check(decimal(domain.price_ratio_min)! <= decimal(domain.price_ratio_max)! && (domain.maximum_elapsed_seconds === 0 || domain.recomparison_allowed), 'event_plan_bridge')
        for (const [position, input] of Object.entries(branch.comparison_inputs_by_position) as [string, Row][]) {
          check(same(input.target, target) && input.position_branch === position && input.forecast_as_of === row.as_of && input.comparison_as_of === row.as_of && same(target.conditioning_ref, condition) && input.instrument_id === context.instrument_id && input.scope_id === context.scope_id && target.horizon === context.horizon, 'event_plan_inputs')
          const market = input.market_distribution, paths = input.path_distribution
          check(same(market.conditioning_ref, condition) && same(paths.conditioning_ref, condition) && same(market.market_target_ref, input.bindings.target) && same(paths.market_target_ref, input.bindings.target), 'event_plan_condition')
          if (paths.supports_market_direction) {
            const grouped: Record<string, bigint> = { up: 0n, neutral: 0n, down: 0n }
            const threshold = decimal(target.threshold_fraction)!
            for (const atom of paths.scenarios as Row[]) {
              const terminal = decimal(atom.terminal_return_fraction)!
              grouped[terminal > threshold ? 'up' : terminal < -threshold ? 'down' : 'neutral'] += decimal(atom.probability)!
            }
            const q: Row[] | null = market.direction_distribution.probabilities
            check(q !== null && q.length === 3 && q.every(item => grouped[item.outcome_id] === decimal(item.value)), 'event_plan_q_grouping')
          }
        }
      }
      if (kind === 'event-scenario-action-plan-draft.v1') check((row.marginal_target === null) === (row.marginal_target_ref === null), 'event_plan_marginal_pair')
    }
    if (kind === 'event-scenario-review-scope.v1') check(time(row.reviewed_at) < time(row.freeze_before) && time(row.reviewed_at) < time(row.valid_until), 'event_review_clock')
    if (kind === 'event-scenario-realization.v1') check(time(row.released_at) <= time(row.public_at) && time(row.public_at) <= time(row.received_at) && time(row.received_at) <= time(row.known_at), 'event_realization_clock')
    if (kind === 'scenario-branch-selection.v1') {
      const selected = row.status === 'selected_preapproved_branch'
      check(selected === ['branch_ref', 'selected_candidate_ref', 'selected_comparison_ref', 'selected_action_id'].every(key => row[key] !== null), 'event_selection_refs')
      check(selected || ['selected_candidate_ref', 'selected_comparison_ref', 'selected_action_id', 'unit_expected_net_if_assumed_fills_usdt', 'recomparison'].every(key => row[key] === null), 'event_selection_unknown')
    }
    if (['perpetual-paper-account-create.v1', 'perpetual-paper-account-summary.v1'].includes(kind)) {
      const references: Row[] = Array.isArray(row.instrument_refs) ? row.instrument_refs : Object.values(row.instrument_refs)
      check(new Set(references.map(item => JSON.stringify([item.ref, item.hash]))).size === references.length, 'paper_instruments')
    }
    if (kind === 'perpetual-paper-account-create-receipt.v1') check(row.owner_id === row.account.owner_id, 'paper_receipt_owner')
    if (kind === 'perpetual-paper-account-list.v1') check(row.accounts.every((item: Row) => item.owner_id === row.owner_id) && new Set(row.accounts.map((item: Row) => item.account_id)).size === row.accounts.length, 'paper_list_partition')
    if (['owner_id', 'scope_id', 'settings_ref', 'profile_ref'].every(key => Object.hasOwn(row, key))) check(row.scope_id === 'crypto:perp:' + row.instrument_id + ':trade:1m', 'scope')
    if (kind === 'action-candidate.v2') {
      const action = row.action, side = row.target_side, branch = row.position_branch
      check((row.quantity_before === null) === (row.quantity_after === null), 'quantity_pair')
      if (row.size_mode === 'unit_notional') check(row.unit_notional_usdt !== null && row.quantity_before === null, 'unit_size')
      else check(row.unit_notional_usdt === null && row.quantity_before !== null, 'contract_size')
      if (action === 'conditional') check(row.nested_action_ref !== null && row.condition_refs.length > 0, 'conditional_child')
      else {
        check(row.nested_action_ref === null, 'nonconditional_child')
        check((action === 'open' && branch === 'flat' && ['long', 'short'].includes(side)) ||
          (['add', 'reduce'].includes(action) && ['long', 'short'].includes(branch) && side === branch) ||
          (action === 'close' && ['long', 'short'].includes(branch) && side === 'flat') ||
          (action === 'no_change' && side === branch), 'action_side')
      }
      const before = decimal(row.quantity_before), after = decimal(row.quantity_after)
      if (before !== null && after !== null) {
        check((before === 0n ? 'flat' : before > 0n ? 'long' : 'short') === branch &&
          (after === 0n ? 'flat' : after > 0n ? 'long' : 'short') === side, 'signed_side')
        if (action !== 'conditional') check((action === 'open' && before === 0n && after !== 0n) ||
          (action === 'add' && before * after > 0n && abs(after) > abs(before)) ||
          (action === 'reduce' && before * after > 0n && abs(after) < abs(before)) ||
          (action === 'close' && before !== 0n && after === 0n) ||
          (action === 'no_change' && before === after), 'quantity_transition')
      }
      if (['cost_ref', 'funding_ref', 'margin_ref', 'fill_ref'].some(key => row[key] === null)) check(row.unknown_reasons.length > 0, 'unknown_inputs')
    }
    if (kind === 'action-candidate-set.v2') {
      const rows: Row[] = row.candidates, refs: Row[] = row.candidate_refs
      check(time(row.as_of) < time(row.valid_until), 'set_expiry')
      check(rows.length === refs.length && new Set(rows.map(item => item.candidate_id)).size === rows.length && new Set(refs.map(item => JSON.stringify([item.ref, item.hash]))).size === refs.length, 'set_members')
      for (const candidate of rows) {
        check(same(candidate.context, row.context), 'set_context')
        check(time(row.as_of) < time(candidate.valid_until) && time(candidate.valid_until) <= time(row.valid_until), 'member_expiry')
        if (candidate.action === 'conditional') {
          const children = rows.filter((_, index) => same(refs[index], candidate.nested_action_ref))
          check(children.length === 1 && children[0].action !== 'conditional' && children[0].candidate_id !== candidate.candidate_id, 'set_conditional_child')
          if (children.length === 1) check(['context', 'target_side', 'position_branch', 'quantity_before', 'quantity_after', 'size_policy_ref', 'size_mode', 'unit_notional_usdt', 'exit_ref', 'cost_ref', 'funding_ref', 'margin_ref', 'fill_ref', 'target_refs'].every(key => same(candidate[key], children[0][key])), 'child_intent')
        }
      }
    }
    if (['publication-anchor-rule.v1', 'pending-publication-target.v1', 'publication-bound-target.v1'].includes(kind)) check(row.duration_seconds === durations[row.horizon], 'rule_duration')
    if (kind === 'publication-bound-target.v1' || kind === 'publication-anchor-receipt.v1') {
      const target = kind === 'publication-bound-target.v1'
      const assigned = time(row.assigned_at), start = time(target ? row.window_start : row.target_start), end = time(target ? row.window_end : row.target_end)
      const expected = assigned - ((assigned % 60000000n + 60000000n) % 60000000n) + 60000000n
      check(start === expected, target ? 'target_strict_minute' : 'strict_minute')
      check(end - start === BigInt(row.duration_seconds) * 1000000n, target ? 'target_duration' : 'receipt_duration')
    }
    if (kind === 'action-review-authorization.v2') check(time(row.reviewed_at) < time(row.valid_until), 'review_expiry')
    if (kind === 'action-comparison-allowed-domain.v1') {
      const rows: Row[] = row.variables
      check(new Set(rows.map(item => JSON.stringify([item.category, item.name]))).size === rows.length, 'domain_variables')
      for (const item of rows) {
        check(time(item.valid_until) <= time(row.valid_until), 'domain_expiry')
        const minimum = decimal(item.minimum), maximum = decimal(item.maximum)
        check(minimum === null || maximum === null || minimum <= maximum, 'domain_bounds')
      }
    }
    if (kind === 'decision-causal-analysis.v1') {
      const links = [...row.main_chain, ...row.opposite_chain]
      check(new Set(links.map(item => item.link_id)).size === links.length, 'analysis_link_ids')
      check(links.every(item => item.evidence_refs.length > 0) || row.unresolved_questions.length > 0, 'analysis_unknown_explicit')
    }
    if (kind === 'research-decision-synthesis.v2') {
      const candidates = row.candidate_set, rule = row.anchor_rule, market = row.market_distribution, target = row.candidate_target
      check(same(row.analysis.candidate_target_ref, candidates.candidate_target_ref), 'analysis_target')
      check(row.as_of === candidates.as_of && time(row.known_at_max) <= time(row.as_of), 'synthesis_cutoff')
      check(same(candidates.candidate_target_ref, rule.candidate_target_ref) && candidates.context.horizon === rule.horizon && time(candidates.valid_until) <= time(rule.valid_until), 'synthesis_rule')
      check(['horizon', 'duration_seconds', 'grid_seconds', 'clock_rule', 'price_basis', 'threshold_fraction', 'method_ref', 'clock_policy_ref', 'valid_until', 'invalidation_refs'].every(key => same(target[key], rule[key])) && same(target.instrument_ref, candidates.context.instrument_ref) && same(target.conditioning_ref, market.conditioning_ref), 'synthesis_target')
      check(same(market.market_target_ref, candidates.candidate_target_ref) && same(market.instrument_ref, candidates.context.instrument_ref) && (market.event_scenario_ref === null || same(market.event_scenario_ref, row.event_probability_ref)), 'synthesis_probability')
      check([market.direction_distribution, row.event_probability.occurrence_distribution, row.event_probability.outcome_given_occurrence].every(item => item.as_of === row.as_of), 'synthesis_probability_cutoff')
      const paths = row.path_distribution
      check(same(paths.market_target_ref, candidates.candidate_target_ref) && same(paths.conditioning_ref, target.conditioning_ref), 'synthesis_paths_scope')
      if (paths.supports_market_direction) {
        const threshold = decimal(target.threshold_fraction)!, probabilities = market.direction_distribution.probabilities
        const grouped: Record<string, bigint> = { up: 0n, neutral: 0n, down: 0n }
        for (const item of paths.scenarios) {
          const terminal = decimal(item.terminal_return_fraction)!
          grouped[terminal > threshold ? 'up' : terminal < -threshold ? 'down' : 'neutral'] += decimal(item.probability)!
        }
        check(probabilities !== null && probabilities.length === 3 && probabilities.every((item: Row) => grouped[item.outcome_id] === decimal(item.value)), 'synthesis_paths_direction')
      }
    }
    if (kind === 'action-review-request.v2') {
      const synth = row.synthesis, domain = row.allowed_domain
      check(same(row.allowed_domain_ref, synth.allowed_domain_ref), 'request_domain')
      check(['candidate_set_ref', 'event_probability_ref', 'market_distribution_ref', 'method_ref', 'method_code_hash'].every(key => same(domain[key], synth[key])) && same(domain.candidate_target_ref, synth.candidate_set.candidate_target_ref) && same(domain.anchor_rule_ref, synth.candidate_set.anchor_rule_ref), 'request_bindings')
      check(time(synth.as_of) <= time(row.requested_at) && time(row.requested_at) < time(domain.valid_until) && time(row.requested_at) < time(synth.candidate_set.valid_until), 'request_expiry')
    }
    if (['decision-snapshot.v2', 'decision-action-plan-revision.v2'].includes(kind)) {
      const context = row.context, bindings = row.bindings
      check(row.scope_id === context.scope_id && row.horizon === context.horizon, 'publication_scope')
      check(['instrument', 'settings', 'account', 'position', 'utility'].every(key => same(bindings[key], context[key + '_ref'])), 'publication_context')
      if (kind === 'decision-snapshot.v2') check(time(row.decision_cutoff_at) <= time(row.known_at), 'snapshot_clock')
      else {
        const candidate = row.selected_candidate
        check(same(candidate.context, context), 'revision_candidate')
        check(row.position_context === 'unknown' || row.position_context === candidate.position_branch, 'revision_position')
        check((row.revision_no === 1) === (row.previous_id === null), 'revision_parent')
        check(time(row.as_of) <= time(row.published_at) && time(row.published_at) < time(row.valid_until) && time(row.valid_until) <= time(candidate.valid_until), 'revision_clock')
        check(same(bindings.final_target, { ref: row.target_spec_ref, hash: row.target_spec_hash }), 'revision_target')
      }
    }
    Object.values(row).forEach(visit)
  }
  visit(raw)
  return issues
}
