"""Risk projections use durable local paths and never fetch providers in an HTTP request."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, NamedTuple

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.market_risk_view import (
    RiskAlertView,
    RiskMonitorStatus,
    RiskObservedWindow,
    RiskQuoteView,
    RiskResearchRef,
    RiskScope,
    RiskScopesView,
    RiskSummaryView,
    RiskTimelineView,
)
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.ports.market_path_read_v2 import MarketPathReadPortV2
from packages.kernel.decision_hub_kernel.persistence.operation_phases import OperationPhases
from packages.kernel.decision_hub_kernel.application.risk_monitor import content_hash
from packages.kernel.decision_hub_kernel.decision.path_risk import missing_ranges, verified_bars
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    FactVersionRecord,
    MacroEventRecord,
    RunRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    MarketPathAcquisitionRecord,
    RiskAlertDeliveryReceiptRecord,
    RiskAlertRecord,
    RiskMonitorPlanRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPathScope,
    aware_utc,
    decimal_text,
    path_hashes,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.risk_monitor import RiskConditionV1
from packages.provider_adapters.research.raw_capture import RawBodyArchive


class _SummaryMetrics(NamedTuple):
    status: Literal["fresh", "stale", "unknown"]
    latest_close_at: datetime | None
    last_price: Decimal | None
    known_at: datetime | None
    return_24h: Decimal | None
    window_start_at: datetime
    window_end_at: datetime
    observed_count: int
    mae_long: Decimal | None


def _summary_metrics(
    scope: MarketPathScope,
    candles: tuple[MarketCandle, ...],
    as_of: datetime,
    fallback_end: datetime,
) -> _SummaryMetrics:
    """Project only displayed metrics after MarketPathStore's fresh original checks.

    Complete feature/path contracts retain their hashes and crossings elsewhere.
    This ephemeral view grants no model, calibration or source-read authority.
    """
    clock = aware_utc(as_of)
    bars = verified_bars(scope, candles, clock)
    latest = bars[-1] if bars else None
    end = latest.close_at if latest else aware_utc(fallback_end)
    start = end - timedelta(hours=24)
    if start.timestamp() % 60 or end.timestamp() % 60 or end > clock:
        raise ValueError("market_path_window_invalid")
    by_close = {item.close_at: item for item in bars}
    continuous_30m = latest is not None and all(
        latest.close_at - timedelta(minutes=offset) in by_close for offset in range(31)
    )
    status: Literal["fresh", "stale", "unknown"] = (
        "stale"
        if latest and (clock - latest.close_at).total_seconds() > 180
        else "fresh"
        if continuous_30m
        else "unknown"
    )
    previous = by_close.get(start)
    change = latest.close / previous.close - 1 if latest and previous else None
    observed = tuple(item for item in bars if start <= item.open_at < end)
    by_open = {item.open_at: item for item in observed}
    anchor = by_open.get(start)
    complete = len(observed) == 1440 and all(
        start + timedelta(minutes=offset) in by_open for offset in range(1440)
    )
    mae = (
        max(Decimal(0), 1 - min(item.low for item in observed) / anchor.open)
        if complete and anchor is not None
        else None
    )
    return _SummaryMetrics(
        status,
        latest.close_at if latest else None,
        latest.close if latest else None,
        max(item.known_at for item in bars) if bars else None,
        change,
        start,
        end,
        len(observed),
        mae,
    )


class MarketRiskQuery:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        *,
        enabled: bool = False,
        clock: Callable[[], datetime] = utcnow,
        summary_reader: MarketPathReadPortV2 | None = None,
    ) -> None:
        self.database, self.clock, self.enabled = database, clock, enabled
        self.scopes = {
            scope.scope_id: scope for scope in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))
        }
        self.store = MarketPathStore(
            database, RawBodyArchive(data_root / "raw-captures"), clock=clock
        )
        self.summary_reader = summary_reader if summary_reader is not None else self.store

    def scope(self, scope_id: str) -> MarketPathScope:
        if scope_id not in self.scopes:
            raise KeyError(scope_id)
        return self.scopes[scope_id]

    def catalog(self) -> RiskScopesView:
        return RiskScopesView(
            schema_version="market-risk-scopes.v1",
            as_of=self.clock(),
            items=[
                RiskScope(
                    scope_id=scope.scope_id,
                    label="比特币现货" if scope.instrument.startswith("BTC") else "以太坊现货",
                    instrument=scope.instrument,
                    venue=scope.venue,
                    market_type="spot",
                    price_type="trade",
                    enabled=self.enabled,
                )
                for scope in self.scopes.values()
            ],
        )

    def monitor(self, plan_id: str) -> RiskMonitorStatus:
        with self.database.session() as session:
            row = session.get(RiskMonitorPlanRecord, plan_id)
            if row is None:
                raise KeyError(plan_id)
            active = (
                session.query(RiskAlertRecord.alert_id)
                .filter_by(
                    episode_id=row.active_episode_id,
                )
                .first()
                if row.active_episode_id
                else None
            )
            status = row.state
            error = row.last_error_code
            if row.expires_at is not None and as_utc(row.expires_at) <= self.clock():
                status = "expired"
            elif status in ("active", "degraded"):
                if not self.enabled or row.last_evaluated_at is None:
                    status, error = "unknown", "risk_monitor_not_running"
                elif (self.clock() - as_utc(row.last_evaluated_at)).total_seconds() > 180:
                    status, error = "degraded", "risk_monitor_heartbeat_stale"
            return RiskMonitorStatus.model_validate(
                {
                    "plan_id": row.plan_id,
                    "scope_id": row.scope_id,
                    "status": status,
                    "last_evaluated_at": as_utc(row.last_evaluated_at),
                    "next_evaluation_at": as_utc(row.next_due_at)
                    if status in ("active", "degraded")
                    else None,
                    "error_code": error,
                    "last_truth": row.last_truth,
                    "active_alert_id": active[0] if active else None,
                }
            )

    def alert(self, alert_id: str) -> RiskAlertView:
        with self.database.session() as session:
            row = session.get(RiskAlertRecord, alert_id)
            if row is None:
                raise KeyError(alert_id)
            payload = json.loads(row.payload_json)
            if content_hash(payload) != row.payload_hash:
                raise ValueError("risk_alert_payload_corrupt")
            rule = RiskConditionV1.model_validate(payload["condition"])
            receipts = (
                session.query(RiskAlertDeliveryReceiptRecord).filter_by(alert_id=alert_id).all()
            )
            displayed = [
                as_utc(item.received_at) for item in receipts if item.receipt_kind == "display"
            ]
            acknowledged = [
                as_utc(item.received_at) for item in receipts if item.receipt_kind == "ack"
            ]
            raw_value = payload["input"].get("close_return")
            value = float(raw_value) if raw_value is not None else None
            severity = "critical" if row.severity == "high" else "warning"
            is_fixture = str(payload["input"].get("input_ref", "")).startswith("fixture://")
            research = session.scalar(
                select(RiskResearchRequestRecord)
                .join(
                    RiskResearchTriggerRecord,
                    RiskResearchTriggerRecord.request_id == RiskResearchRequestRecord.request_id,
                )
                .where(RiskResearchTriggerRecord.trigger_key == f"risk_alert:{alert_id}")
            )
            if research is not None and research.scope_id != row.scope_id:
                raise ValueError("risk_alert_research_scope_mismatch")
            return RiskAlertView(
                schema_version="market-risk-alert.v1",
                alert_id=row.alert_id,
                scope_id=row.scope_id,
                plan_id=row.plan_id,
                severity=severity,
                status="resolved" if row.closed_at is not None else "active",
                headline="隔离验收场景已触发" if is_fixture else "市场变化条件已触发",
                explanation=(
                    "这是明确标记的隔离测试输入，用于验证告警显示与回执，不代表真实市场预警或预测成绩。"
                    if is_fixture
                    else "已观察到价格变化；这是事实变化提醒，提前预测资格尚未验证。"
                ),
                triggered_at=as_utc(row.created_at),
                resolved_at=as_utc(row.closed_at),
                condition_label=f"{rule.expression.left.window_seconds // 60}分钟价格变化条件",
                observed_value=value,
                threshold_value=float(rule.expression.right),
                value_unit="fraction",
                report_run_id=research.run_id if research is not None else None,
                research_request={
                    "request_id": research.request_id,
                    "scope_id": research.scope_id,
                    "status": research.status,
                    "error_code": research.error_code,
                    "attempts": research.attempts,
                    "run_id": research.run_id,
                } if research is not None else None,
                displayed_at=min(displayed) if displayed else None,
                acknowledged_at=min(acknowledged) if acknowledged else None,
            )

    def _research(
        self, scope: MarketPathScope, now: datetime
    ) -> tuple[RiskResearchRef, str | None]:
        """Link the saved report; authenticated quality is read only when expanded."""
        with self.database.session() as session:
            artifact = (
                session.query(ArtifactRecord.run_id, ArtifactRecord.artifact_id)
                .join(
                    AnalyticalOutlookRecord,
                    ArtifactRecord.artifact_id == AnalyticalOutlookRecord.artifact_id,
                )
                .filter(
                    AnalyticalOutlookRecord.market == scope.asset_class,
                    AnalyticalOutlookRecord.instrument == scope.instrument,
                    AnalyticalOutlookRecord.horizon == "24h",
                    ArtifactRecord.created_at <= now,
                    ArtifactRecord.gate_status.in_(["publish", "degraded", "research_only"]),
                )
                .order_by(ArtifactRecord.created_at.desc())
                .first()
            )
            if artifact is None:
                return RiskResearchRef(
                    run_id=None, artifact_id=None, status="not_available", summary=None
                ), None
            return RiskResearchRef(
                run_id=artifact.run_id,
                artifact_id=artifact.artifact_id,
                status="partial",
                summary=(
                    "已保存24小时研究结论；展开可核验质量、依据与主要反方。"
                    "当前尚未确认量化风险资格。"
                ),
            ), None

    def _alert_ids(
        self, scope_id: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[str]:
        with self.database.session() as session:
            query = session.query(RiskAlertRecord.alert_id).filter_by(scope_id=scope_id)
            if since is not None:
                query = query.filter(RiskAlertRecord.created_at >= since)
            if until is not None:
                query = query.filter(RiskAlertRecord.created_at <= until)
            return [
                item[0]
                for item in query.order_by(RiskAlertRecord.created_at.desc()).limit(100).all()
            ]

    def summary(
        self, scope_id: str, *, diagnostics: OperationPhases | None = None,
    ) -> RiskSummaryView:
        scope, now = self.scope(scope_id), aware_utc(self.clock())
        end = now.replace(second=0, microsecond=0)
        phases = diagnostics if diagnostics is not None else OperationPhases()
        with phases.phase("risk_summary_research"):
            research, counter = self._research(scope, now)
        with phases.phase("risk_summary_plan_acquisition"):
            with self.database.session() as session:
                plan_ids = [
                    row[0]
                    for row in session.query(RiskMonitorPlanRecord.plan_id)
                    .filter_by(scope_id=scope_id)
                    .all()
                ]
                acquisition = (
                    session.query(MarketPathAcquisitionRecord)
                    .filter(
                        MarketPathAcquisitionRecord.scope_id == scope_id,
                        MarketPathAcquisitionRecord.known_at <= now,
                    )
                    .order_by(MarketPathAcquisitionRecord.slot_at.desc())
                    .first()
                )
                source_interrupted = acquisition is not None and acquisition.health_state == "degraded"
                try:
                    receipt_valid = acquisition is None or (
                        content_hash(json.loads(acquisition.receipt_json)) == acquisition.receipt_hash
                    )
                except (ValueError, TypeError):
                    receipt_valid = False
        with phases.phase("risk_summary_monitors_alerts"):
            monitors = [self.monitor(plan_id) for plan_id in plan_ids]
            alerts = [self.alert(alert_id) for alert_id in self._alert_ids(scope_id, until=now)]
        quote, window = None, None
        status: Literal["fresh", "stale", "missing", "invalid"] = "missing"
        try:
            with phases.phase("risk_summary_bar_load_verify"):
                bars = self.summary_reader.load(
                    scope, start_at=end - timedelta(minutes=1470), end_at=end, as_of=now
                )
            with phases.phase("risk_summary_metrics"):
                features = _summary_metrics(scope, bars, now, end)
                if features.last_price is not None and features.latest_close_at is not None:
                    quote = RiskQuoteView(
                        price=decimal_text(features.last_price),
                        quoted_at=features.latest_close_at,
                        known_at=features.known_at,
                        source_name="OKX分钟收盘",
                        price_kind="close_1m",
                        change_24h_pct=float(features.return_24h * 100)
                        if features.return_24h is not None
                        else None,
                    )
                status = (
                    "fresh"
                    if features.status == "fresh"
                    else "stale"
                    if features.status == "stale"
                    else "missing"
                )
                # This is an observed trailing path, anchored to the latest verified close.
                # Prediction labels retain their scheduled immutable window elsewhere.
                window = RiskObservedWindow.model_validate(
                    {
                        "kind": "observed_path",
                        "start_at": features.window_start_at,
                        "end_at": features.window_end_at,
                        "samples": features.observed_count,
                        "expected_samples": 1440,
                        "coverage_ratio": features.observed_count / 1440.0,
                        "max_adverse_move_pct": float(features.mae_long * 100)
                        if features.mae_long is not None
                        else None,
                    }
                )
        except (ValueError, OSError):
            status = "invalid"
        with phases.phase("risk_summary_view_projection"):
            if source_interrupted and status == "fresh":
                status = "stale"
            if not receipt_valid:
                status = "invalid"
            active_alerts = [alert for alert in alerts if alert.status == "active"]
            level: Literal["unknown", "elevated", "high"] = (
                "high"
                if any(alert.severity == "critical" for alert in active_alerts)
                else "elevated"
                if active_alerts
                else "unknown"
            )
            headline = "市场变化提醒，请检查持有风险" if active_alerts else "路径风险尚未量化"
            if not self.enabled:
                headline = "风险监控尚未开启"
            explanation = (
                "方向观点与盘中持有风险分别判断。风险概率尚未校准，当前变化条件未触发不代表市场安全。"
            )
            if (
                not active_alerts
                and status == "fresh"
                and window is not None
                and window.max_adverse_move_pct is not None
                and window.max_adverse_move_pct >= 2
            ):
                headline = "近24小时已出现明显下跌波动"
                explanation = (
                    f"相对24小时前窗口起点，盘中最多下跌{window.max_adverse_move_pct:.2f}%。"
                    "最近30分钟未触发变化条件，不抵消这段已发生的波动；未来风险仍待评估。"
                )
            if status in ("stale", "missing", "invalid"):
                explanation = "当前数据陈旧、缺失或无法核验；持续风险判断未知，已触发的提醒保留。"
            if source_interrupted:
                explanation = "行情采集已连续失败，持续风险判断未知；保留最近可核验价格和已触发的提醒。"
            return RiskSummaryView.model_validate(
                {
                    "schema_version": "market-risk-summary.v1",
                    "scope_id": scope_id,
                    "as_of": now,
                    "risk_level": level,
                    "data_status": status,
                    "headline": headline,
                    "explanation": explanation,
                    "quote": quote,
                    "window": window,
                    "strongest_counterargument": counter,
                    "research": research,
                    "monitors": monitors,
                    "recent_alerts": alerts[:10],
                }
            )

    def timeline(self, scope_id: str, *, start: datetime, end: datetime) -> RiskTimelineView:
        scope, now = self.scope(scope_id), aware_utc(self.clock())
        start, end = aware_utc(start), aware_utc(end)
        if (
            not start < end <= now
            or (end - start).total_seconds() > 604800
            or start.timestamp() % 60
            or end.timestamp() % 60
        ):
            raise ValueError("risk_timeline_window_invalid")
        status: Literal["fresh", "stale", "missing", "invalid"] = "missing"
        bars = ()
        try:
            bars = self.store.load(scope, start_at=start, end_at=end, as_of=now)
            if bars:
                status = "stale" if (now - bars[-1].close_at).total_seconds() > 180 else "fresh"
        except (ValueError, OSError):
            status = "invalid"
        times = {bar.open_at for bar in bars}
        expected = [
            start + timedelta(minutes=i) for i in range(int((end - start).total_seconds() // 60))
        ]
        gaps = missing_ranges(tuple(stamp for stamp in expected if stamp not in times))
        points = [
            {
                "at": bar.close_at,
                "price": decimal_text(bar.close),
                "low": decimal_text(bar.low),
                "high": decimal_text(bar.high),
            }
            for bar in bars
        ]
        source_points, bucket_size = len(points), 1
        if len(points) > 2000:
            # Retain bucket extremes and endpoints; missing intervals are separate objects.
            selected = {0, len(points) - 1}
            bucket_size = max(2, (len(points) + 899) // 900)
            for i in range(0, len(points), bucket_size):
                indices = range(i, min(i + bucket_size, len(points)))
                selected.add(min(indices, key=lambda j: bars[j].low))
                selected.add(max(indices, key=lambda j: bars[j].high))
            points = [points[i] for i in sorted(selected)]
        semantic, provenance = path_hashes(scope, bars)
        markers, truncated = self._markers(scope, start=start, end=end)
        return RiskTimelineView.model_validate(
            {
                "schema_version": "market-risk-timeline.v1",
                "scope_id": scope_id,
                "start": start,
                "end": end,
                "as_of": now,
                "data_status": status,
                "points": points,
                "missing_ranges": [{"start_at": left, "end_at": right} for left, right in gaps],
                "alerts": [
                    self.alert(key) for key in self._alert_ids(scope_id, since=start, until=end)
                ],
                "aggregation": {
                    "method": "extreme_samples" if bucket_size > 1 else "full_minutes",
                    "source_points": source_points,
                    "output_points": len(points),
                    "bucket_minutes": bucket_size,
                    "path_hash": semantic,
                    "provenance_hash": provenance,
                },
                "markers": markers,
                "markers_truncated": truncated,
            }
        )

    def _markers(self, scope: MarketPathScope, *, start: datetime, end: datetime):
        markers, truncated = [], False
        with self.database.session() as session:
            reports = session.execute(
                select(ArtifactRecord.artifact_id, ArtifactRecord.run_id,
                       ArtifactRecord.headline, ArtifactRecord.created_at)
                .join(RunRecord, RunRecord.run_id == ArtifactRecord.run_id)
                .join(StudyRecord, StudyRecord.study_id == RunRecord.study_id)
                .where(
                    StudyRecord.market == scope.asset_class,
                    StudyRecord.instrument == scope.instrument,
                    ArtifactRecord.created_at >= start,
                    ArtifactRecord.created_at <= end,
                )
                .order_by(ArtifactRecord.created_at.desc())
                .limit(51)
            ).all()
            interrupted = session.execute(
                select(RunRecord.run_id, RunRecord.status, RunRecord.finished_at)
                .join(StudyRecord, StudyRecord.study_id == RunRecord.study_id)
                .where(
                    StudyRecord.market == scope.asset_class,
                    StudyRecord.instrument == scope.instrument,
                    RunRecord.status.in_(("failed", "cancelled")),
                    RunRecord.finished_at >= start,
                    RunRecord.finished_at <= end,
                )
                .order_by(RunRecord.finished_at.desc())
                .limit(51)
            ).all()
            facts = session.execute(
                select(FactVersionRecord.fact_version_id, FactVersionRecord.known_at,
                       FactVersionRecord.observed_at)
                .where(
                    FactVersionRecord.market == scope.asset_class,
                    FactVersionRecord.scope_instrument == scope.instrument,
                    FactVersionRecord.known_at >= start,
                    FactVersionRecord.known_at <= end,
                )
                .order_by(FactVersionRecord.known_at.desc())
                .limit(101)
            ).all()
            events = session.execute(
                select(MacroEventRecord.macro_event_id, MacroEventRecord.created_at,
                       MacroEventRecord.scheduled_at)
                .where(MacroEventRecord.created_at >= start, MacroEventRecord.created_at <= end)
                .order_by(MacroEventRecord.created_at.desc())
                .limit(51)
            ).all()
            for items, limit in ((reports, 50), (interrupted, 50), (facts, 100), (events, 50)):
                truncated = truncated or len(items) > limit

            def add(key, kind, label, at, source, *, occurred=None, run=None, artifact=None):
                markers.append(
                    {
                        "marker_id": key,
                        "kind": kind,
                        "label": label,
                        "at": as_utc(at),
                        "occurred_at": as_utc(occurred),
                        "run_id": run,
                        "artifact_id": artifact,
                        "source_ref": source,
                    }
                )

            for row in reports[:50]:
                add(
                    row.artifact_id,
                    "report",
                    row.headline,
                    row.created_at,
                    row.artifact_id,
                    run=row.run_id,
                    artifact=row.artifact_id,
                )
            for row in interrupted[:50]:
                add(
                    row.run_id,
                    "interruption",
                    "研究执行失败" if row.status == "failed" else "研究已取消",
                    row.finished_at,
                    row.run_id,
                    run=row.run_id,
                )
            for row in facts[:100]:
                add(
                    row.fact_version_id,
                    "fact",
                    "新的研究事实已入库",
                    row.known_at,
                    row.fact_version_id,
                    occurred=row.observed_at,
                )
            for row in events[:50]:
                add(
                    row.macro_event_id,
                    "event",
                    "全球宏观事件已收录（相关性待研究）",
                    row.created_at,
                    row.macro_event_id,
                    occurred=row.scheduled_at,
                )
        return sorted(markers, key=lambda row: (row["at"], row["marker_id"])), truncated
