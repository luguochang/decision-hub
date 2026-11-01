import type { RiskSummaryView, RiskTimelineView, RiskAlertView, RiskScope } from './risk'

export const RISK_AT = '2026-10-08T00:00:00Z'
export const riskScopeFixture: RiskScope = { scope_id: 'okx:spot:BTC-USDT:trade', label: '比特币 BTC', instrument: 'BTC-USDT', venue: 'okx', market_type: 'spot', price_type: 'trade', enabled: true }
export const riskAlertFixture: RiskAlertView = {
  schema_version: 'market-risk-alert.v1', alert_id: 'alert_fixture', scope_id: riskScopeFixture.scope_id,
  plan_id: 'plan_fixture', severity: 'warning', status: 'active', headline: '价格变化达到复核条件',
  explanation: '这是一条已观察到的变化，不保证后续方向。', triggered_at: RISK_AT, resolved_at: null,
  condition_label: '分钟路径变化超过门槛', observed_value: -2.1, threshold_value: -2, value_unit: '%',
  report_run_id: null, displayed_at: null, acknowledged_at: null,
}
export const riskSummaryFixture: RiskSummaryView = {
  schema_version: 'market-risk-summary.v1', scope_id: riskScopeFixture.scope_id, as_of: RISK_AT,
  risk_level: 'unknown', data_status: 'fresh', headline: '变化条件未触发，路径风险尚未量化',
  explanation: '尚未校准方向与路径风险。',
  quote: { price: '44000.1', quoted_at: RISK_AT, known_at: RISK_AT, source_name: 'OKX分钟收盘', price_kind: 'close_1m', change_24h_pct: null },
  window: { kind: 'observed_path', start_at: '2026-10-07T23:30:00Z', end_at: RISK_AT, samples: 30, expected_samples: 30, coverage_ratio: 1, max_adverse_move_pct: -1.2 },
  strongest_counterargument: null,
  research: { run_id: null, artifact_id: null, status: 'legacy_unquantified', summary: null },
  monitors: [{ plan_id: 'plan_fixture', scope_id: riskScopeFixture.scope_id, status: 'active', last_evaluated_at: RISK_AT, next_evaluation_at: '2026-10-08T00:01:00Z', error_code: null, last_truth: 'false', active_alert_id: null }],
  recent_alerts: [],
}
export const riskTimelineFixture: RiskTimelineView = {
  schema_version: 'market-risk-timeline.v1', scope_id: riskScopeFixture.scope_id,
  start: '2026-10-07T23:30:00Z', end: RISK_AT, as_of: RISK_AT, data_status: 'fresh',
  points: [{ at: '2026-10-07T23:30:00Z', price: '44000' }, { at: '2026-10-07T23:31:00Z', price: '44000.1' }],
  missing_ranges: [], alerts: [],
}
