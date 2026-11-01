// Shape relations only; original readers authenticate execution and admission.
import { ContextSelectionSemanticError, parseContextMicroseconds as micros } from './contextSelectionSemantics'

type Row = Record<string, any>
const row = (value: unknown): Row => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const sameRef = (a: Row, b: Row) => a.ref === b.ref && a.hash === b.hash

export function researchMethodIssueCodes(key: string, raw: unknown): string[] {
  try { return relations(key, raw) }
  catch (error) {
    if (error instanceof ContextSelectionSemanticError) return [error.code]
    throw error
  }
}

function relations(key: string, raw: unknown): string[] {
  const value = row(raw), issues: string[] = []
  const nested = (name: string, item: unknown) => issues.push(...researchMethodIssueCodes(name, item))
  if (key === 'research_method_view_v2') {
    for (const item of value.methods) nested('research_method_view_v2_item_v2', item)
  } else if (key === 'research_method_view_v2_item_v2') {
    if ((value.generation_binding_ref === null) !== (value.original_selection_ref === null)) issues.push('method_generation_reference_pair_invalid')
    if ((value.generation === 1) !== (value.generation_binding_ref === null)) issues.push('method_generation_original_required')
    if ((value.origin === 'trial_registered') !== (value.trial !== null)) issues.push('method_trial_origin_mismatch')
    if (value.trial !== null) {
      if (value.skill_version_id !== value.trial.candidate.ref) issues.push('method_trial_candidate_mismatch')
      nested('research_method_trial_provenance', value.trial)
    }
  } else if (key === 'research_method_trial_provenance') {
    if (!(micros(value.admitted_at) <= micros(value.start_at) && micros(value.start_at) < micros(value.end_at)) || value.run_ordinal > value.max_runs) issues.push('method_trial_window_or_ordinal_invalid')
    if (sameRef(value.parent, value.candidate) || micros(value.behavior.completed_at) > micros(value.admitted_at)) issues.push('method_trial_provenance_invalid')
    nested('research_method_view_v2_trial_source', value.source)
    nested('research_method_view_v2_behavior_summary', value.behavior)
  } else if (key === 'research_method_view_v2_trial_source') {
    if (value.scope_id !== `crypto:spot:okx:${value.instrument}:trade:1m`) issues.push('method_trial_scope_invalid')
    if (value.terminal_kind === 'closed_episode') {
      if (value.outcome_status !== 'settled' || value.settled_at === null || value.remaining_quantity !== '0') issues.push('method_trial_closed_episode_invalid')
    } else if (value.execution_status === 'filled' || value.outcome_status !== 'unscorable') issues.push('method_trial_attempt_terminal_invalid')
    if (value.outcome_status === 'unscorable' && value.net_pnl !== null) issues.push('method_trial_unknown_return_required')
  } else if (key === 'research_method_view_v2_behavior_summary') {
    const cases: Row[] = value.cases, count = value.case_count
    if (count !== cases.length || value.applies_case_count !== cases.filter(c => c.purpose === 'applies').length || value.excludes_case_count !== cases.filter(c => c.purpose === 'excludes').length || value.historical_pit_case_count !== cases.filter(c => c.material_kind === 'historical_pit').length || value.synthetic_control_case_count !== cases.filter(c => c.material_kind === 'synthetic_control').length || value.attempts.length !== count * 2 || value.outputs.length !== count * 2) issues.push('method_trial_behavior_census_invalid')
  } else if (key === 'research_skill_generation_binding_v1' || key === 'research_skill_generation_binding_v2') {
    const known = micros(value.method_first_known_at), cutoff = micros(value.producer_cutoff_at), prepared = micros(value.prepared_at), selected = micros(value.selected_at), deadline = micros(value.binding_deadline_at)
    if (!(known <= cutoff && cutoff <= prepared && prepared <= selected && selected < deadline)) issues.push('skill_generation_contract_time_order_invalid')
    const original = value.original_selection_ref, current = value.prepared_execution_ref
    if (original.output_id !== value.selection_id || original.role !== 'research_skill_selection' || original.schema_version !== (key === 'research_skill_generation_binding_v2' ? 'research-skill-selection.v3' : value.selection_origin === 'trial_registered' ? 'research-skill-selection.v2' : 'research-skill-selection.v1') || !(cutoff <= micros(original.recorded_at) && micros(original.recorded_at) <= prepared) || micros(current.recorded_at) !== prepared || current.role !== `dsh_execution_request:${value.generation}`) issues.push('skill_generation_contract_reference_invalid')
    if (value.selection_origin === 'trial_registered' && !sameRef(value.target, value.trial_target)) issues.push('skill_generation_contract_trial_target_invalid')
  }
  return issues
}
