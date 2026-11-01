/** Synthetic UI fixtures only; these never register or substitute product forecasts. */
import type { PathCurrentTrial, PathPredictions, PathScoreView, PathTrialSummary } from './riskPaths'

export const PATH_BTC = 'crypto:spot:okx:BTC-USDT:trade:1m'
export const PATH_ETH = 'crypto:spot:okx:ETH-USDT:trade:1m'
export const PATH_AT = '2026-10-10T00:00:10Z'
const HASH = 'a'.repeat(64)
export const pathTrialFixture: PathTrialSummary = {
  trial_id: 'synthetic-trial-1', state: 'running', scope_ids: [PATH_BTC, PATH_ETH],
  protocol_version: 'path-shadow-24h.v1', shadow_days: 28,
  registered_at: '2026-10-09T23:50:00Z', first_cutoff_at: '2026-10-10T00:00:00Z',
  last_cutoff_at: '2026-11-06T00:00:00Z', last_window_end_at: '2026-11-07T00:01:00Z',
  label_deadline_at: '2026-11-08T00:01:00Z', finished_at: null, conclusion: null,
  expected_slot_count: 56, expected_prediction_count: 112, published_prediction_count: 4,
  unpublished_prediction_count: 108, future_prediction_count: 108, missed_prediction_count: 0,
  complete_label_count: 0, pending_label_count: 56, unavailable_mature_label_count: 0,
  job_error_count: 0, deadline_elapsed: false, report_available: false, promotion_allowed: false,
}
export const pathCurrentFixture: PathCurrentTrial = {
  schema_version: 'risk-path-current-trial.v1', scope_id: PATH_BTC, as_of: PATH_AT, trial: pathTrialFixture,
}
export const pathPredictionsFixture: PathPredictions = {
  schema_version: 'risk-path-predictions.v1', scope_id: PATH_BTC, trial_id: pathTrialFixture.trial_id,
  as_of: PATH_AT, predictions: [],
}
export const pathScorePendingFixture: PathScoreView = {
  schema_version: 'risk-path-score-view.v1', as_of: PATH_AT, trial: pathTrialFixture,
  report: null, receipt_verification: 'stored_payload_hash',
}
export const pathScoreFinalFixture: PathScoreView = {
  ...pathScorePendingFixture, as_of: '2026-11-08T00:01:00Z',
  trial: { ...pathTrialFixture, state: 'completed', finished_at: '2026-11-08T00:01:00Z',
    conclusion: 'insufficient_evidence', future_prediction_count: 0, missed_prediction_count: 108,
    complete_label_count: 2, pending_label_count: 0, unavailable_mature_label_count: 54,
    deadline_elapsed: true, report_available: true },
  report: {
    report_id: 'synthetic-evaluation-1', trial_id: pathTrialFixture.trial_id,
    report_version: 'path-shadow-evaluation.v1', computed_at: '2026-11-08T00:01:00Z',
    protocol_hash: HASH, payload_hash: HASH, prediction_refs: [], observation_refs: [],
    score: {
      schema_version: 'path-shadow-score.v1', expected_slot_count: 56, expected_prediction_count: 112,
      published_prediction_count: 4, unpublished_prediction_count: 108, on_time_prediction_count: 4,
      late_prediction_count: 0, complete_label_count: 2, partial_label_count: 0, unavailable_label_count: 54,
      independent_paired_day_blocks: 1, observed_primary_event_day_blocks: 0, independent_event_blocks: null,
      minimum_time_blocks: 100, minimum_event_blocks: 30,
      targets: [PATH_BTC, PATH_ETH].flatMap(scope_id => (['long', 'short'] as const).flatMap(direction => (['0.02', '0.05'] as const).map(threshold => ({
        scope_id, direction, threshold, primary: direction === 'long' && threshold === '0.02',
        expected_slots: 28, baseline_scored: 1, candidate_scored: 1, paired_scored: 1,
        baseline_brier: '0.25', candidate_brier: '0.20', paired_baseline_brier: '0.25',
        paired_candidate_brier: '0.20', paired_delta: '0.05', independent_day_blocks: 1,
        ci: null, ci_status: 'not_registered' as const,
      })))),
      exclusion_count_basis: 'target_evaluations', exclusions: [], exclusion_counts: {},
      alarm_metrics: { precision: null, recall: null, false_episodes_per_asset_day: null, lead_time: null,
        status: 'unknown', reason: 'forecast_alarm_and_authenticated_delivery_exposure_not_registered' },
      ci: null, ci_status: 'not_registered', promotion_allowed: false, conclusion: 'insufficient_evidence',
      reasons: ['fewer_than_100_independent_day_blocks'],
    },
  },
}

export const pathScoreV2FinalFixture: PathScoreView = {
  ...pathScoreFinalFixture,
  trial: { ...pathScoreFinalFixture.trial, protocol_version: 'path-shadow-24h.v2' },
  report: {
    ...pathScoreFinalFixture.report!, report_version: 'path-shadow-evaluation.v2', alarm_score_original: null,
    score: {
      ...pathScoreFinalFixture.report!.score, schema_version: 'path-shadow-score.v2',
      ci: null, ci_status: 'insufficient', alarm_originals: [],
      targets: pathScoreFinalFixture.report!.score.targets.map(item => ({ ...item,
        ci: null, ci_status: 'insufficient', ci_reasons: ['fewer_than_100_independent_day_blocks'] })),
      inference: {
        status: 'insufficient', ci: null, protocol: 'utc-day-paired-percentile-bootstrap.v2',
        protocol_hash: HASH, confidence: '0.95', seed: 271828, replicates: 2000,
        unit: 'UTC-day', estimator: 'equal_day_mean_baseline_minus_candidate_brier',
        expected_pairs: 56, complete_pairs: 2, independent_day_blocks: 1, event_blocks: null,
        reasons: ['fewer_than_100_independent_day_blocks', 'authenticated_matching_event_blocks_unavailable'],
        promotion_allowed: false,
      },
    },
  },
}
