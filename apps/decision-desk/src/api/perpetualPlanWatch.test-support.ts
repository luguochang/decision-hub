import fixture from '../../../../artifacts/d2-perp-advice-ui/pure-presentation-fixture.json'
import { decisionBodyHash as hash } from './decisionOriginals'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from './perpetualAdvice'

export async function continuousWatchFixture() {
  const selection: PerpetualAdviceSelection = { ...fixture.selection as PerpetualAdviceSelection,
    partition: { ...(fixture.selection as PerpetualAdviceSelection).partition, account_kind: 'paper', account_id: 'B-condition-paper' } }
  const original = fixture.value as unknown as PerpetualAdviceView
  const value: PerpetualAdviceView = { ...original, partition: selection.partition,
    revision: original.revision && { ...original.revision,
      context: { ...original.revision.context, account_kind: 'paper', account_id: 'B-condition-paper' } } }
  const r = value.revision!
  const command = { schema_version: 'perpetual-plan-watch-command.v2', continuation: 'follow_published_revisions',
    input_policy: 'saved_preferences_current_paper_ledger.v1', request_id: 'condition-watch-request', partition: selection.partition,
    horizon: selection.horizon, plan_id: r.plan_id, expected_revision_ref: { ref: r.revision_id, hash: r.payload_hash },
    expected_state_version: 0, expected_settings_revision: selection.settings_revision,
    expected_settings_state_version: selection.settings_state_version, operation: 'watch', mode: 'remind_and_research' }
  const body = { schema_version: 'perpetual-plan-watch-receipt.v2', owner_id: selection.owner_id, request_id: command.request_id,
    command, command_hash: await hash(command),
    watch_id: 'perpwatchcontinuous_' + await hash([selection.owner_id, selection.partition, selection.horizon, selection.settings_revision]),
    state_version: 1, state: 'active', accepted_at: '2026-10-21T00:00:00Z', review_due_at: r.valid_until, auto_review_slot: null,
    origin_run_id: value.run_id!, origin_artifact_id: value.report_id!, plan_revision_ref: command.expected_revision_ref,
    snapshot_ref: { ref: value.snapshot!.id, hash: value.snapshot!.snapshot_hash }, comparison_ref: r.bindings.comparison,
    frozen_forecast_ref: r.bindings.frozen_forecast, publication_ref: { ref: 'condition-publication', hash: 'b'.repeat(64) } }
  const digest = await hash(body)
  const receipt = { ...body, revision: { ref: 'perpwatchcontinuouscmd_' + await hash([selection.owner_id, command.request_id]), hash: digest }, payload_hash: digest }
  const progressBody = { schema_version: 'perpetual-plan-watch-progress.v1', owner_id: selection.owner_id,
    watch_id: receipt.watch_id, watch_ref: receipt.revision, sequence: 1, status: 'following', observed_at: '2026-10-21T00:02:01Z',
    plan_id: r.plan_id, published_at: '2026-10-21T00:00:00Z', review_due_at: r.valid_until, auto_review_slot: null,
    result_hash: 'c'.repeat(64), request_id: null, outbox_ids: [], origin_run_id: value.run_id!, origin_artifact_id: value.report_id!,
    plan_revision_ref: body.plan_revision_ref, snapshot_ref: body.snapshot_ref, comparison_ref: body.comparison_ref,
    frozen_forecast_ref: body.frozen_forecast_ref, publication_ref: body.publication_ref }
  const progress = { ...progressBody, payload_hash: await hash(progressBody) }
  const condition = { assumption_id: 'btc-support', assumption_ref: { ref: 'decision_model_assumption_' + 'a'.repeat(64), hash: 'a'.repeat(64) },
    purpose: 'confirmation', text: 'BTC 连续两根已收盘 1 分钟价格保持支撑位以上', truth: 'true', status: 'waiting',
    streak: 1, required_samples: 2, last_bar_at: '2026-10-21T00:02:00Z', observed_at: '2026-10-21T00:02:01Z',
    reason_codes: [], request_id: null, outbox_ids: [] }
  const monitoring = { schema_version: 'perpetual-condition-monitoring-view.v1', source_run_id: value.run_id!,
    plan_revision_ref: body.plan_revision_ref, state: 'monitoring', new_exposure_blocked: false,
    observed_at: '2026-10-21T00:02:01Z', conditions: [condition] }
  const view = { schema_version: 'perpetual-plan-watch-view.v3', receipt, progress, condition_monitoring: monitoring }
  return { selection, value, receipt, progress, monitoring, condition, view }
}
