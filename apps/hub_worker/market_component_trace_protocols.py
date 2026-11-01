"""Finite native trace protocols. Borrow the caller's snapshot and never publish rights.

Codec/DSL conformance is distinct from financial performance, cost qualification,
notification acceptance, and actual user exposure. Missing originals stay in the
registered window denominator; damaged originals raise rather than downgrade.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.notification_envelope import (
    NotificationEnvelopeV2,
)
from packages.kernel.decision_hub_kernel.application.component_candle_originals import (
    read_candle_original_in_session,
)
from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    PathComponentInput,
    RuleComponentInput,
)
from packages.kernel.decision_hub_kernel.application.market_features import (
    MarketFeatureSnapshot,
    build_market_features,
)
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.risk_monitor import (
    content_hash,
    evaluate_condition,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    OutboxRecord,
    RawCaptureRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    MarketCandleRecord,
    MarketPathAcquisitionRecord,
    RiskAlertDeliveryReceiptRecord,
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorPlanRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_protocol import (
    LOCAL_EXECUTION_POLICY,
    ComponentExecutionUsage,
    ComponentOriginalRef,
    ComponentProtocolResult,
    verify_local_execution_cost,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    aware_utc,
    canonical_hash,
    path_hashes,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.sources import verify_notification_envelope

MAX_CAPTURES = 512
MAX_NATIVE_CANDLES = 20000
MAX_EVALUATIONS = 10000


def _policy(kind):
    return {
        "schema": "component-native-trace-policy.v1",
        "kind": kind,
        "cadence_seconds": 60,
        "window_denominator": "registered_start_inclusive_end_exclusive",
        "source_first_known": "max_native_capture_completed_and_recorded",
        "native_first_known": "actual_market_candle_known_at_no_backdate",
        "cost": LOCAL_EXECUTION_POLICY,
        "sample": "full_registered_window_and_native_pit_conformance",
        "max_captures": MAX_CAPTURES,
        "max_native_candles": MAX_NATIVE_CANDLES,
        "max_evaluations": MAX_EVALUATIONS,
        "exposure": "display_or_provider_acceptance_is_not_window_exposure",
    }


def _time(value):
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


class _Trace:
    def __init__(self, session, manifest, trial, captures, original_refs, as_of, context, kind):
        self.session, self.manifest, self.trial, self.context = session, manifest, trial, context
        self.as_of, self.refs = aware_utc(as_of), {r.original_ref: r for r in original_refs}
        self.raw_cache, self.candle_cache, self.feature_cache = {}, {}, {}
        self.raw_sizes, self.source_records, self.native_by_id = {}, {}, {}
        if (
            manifest.kind != kind
            or trial.version_id != manifest.version_id
            or trial.manifest_hash != manifest.payload_hash
            or trial.protocol_hash != canonical_hash(_policy(kind))
            or self.as_of < trial.registered_at
            or len(captures) > MAX_CAPTURES
        ):
            raise ValueError("component_trace_protocol_binding_invalid")
        installed = context.installed
        self.item = (
            installed.get(manifest.version_id)
            if hasattr(installed, "get")
            else next(
                (v for v in installed or () if v.version_id == manifest.version_id),
                None,
            )
        )
        if self.item is None:
            raise ValueError("component_trace_installed_missing")
        if self.item.manifest(parent_version_id=manifest.parent_version_id) != manifest:
            raise ValueError("component_trace_installed_changed")
        self.captures = captures

    def add(self, namespace, identity, body):
        reference = ComponentOriginalRef(
            original_ref=namespace + ":" + str(identity),
            original_hash=canonical_hash(body),
        )
        existing = self.refs.get(reference.original_ref)
        if existing is not None and existing != reference:
            raise ValueError("component_trace_original_identity_changed")
        self.refs[reference.original_ref] = reference
        return reference

    def raw(self, ref, digest):
        key = ref, digest
        if key not in self.raw_cache:
            data = self.context.raw_archive.read(ref)
            if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("component_trace_raw_changed")
            self.raw_cache[key] = json.loads(data)
            self.raw_sizes[key] = len(data)
        return self.raw_cache[key]

    def capture(self, body):
        # Re-read native audit in the same physical Session, even when invoked directly.
        from .market_component_originals import ProductionComponentOriginalReader

        row = self.session.get(MarketComponentAuditRecord, body["audit_id"], populate_existing=True)
        actual = ProductionComponentOriginalReader._audit(row, self.trial)
        if actual != body or actual["schema_version"] not in (
            "market-component-shadow-execution.v1",
            "market-component-shadow-execution.v2",
        ):
            raise ValueError("component_trace_capture_changed")
        intent = body["intent"]
        cutoff, source_known, known = map(
            _time,
            (
                intent["cutoff_at"],
                intent["source_known_at"],
                body["known_at"],
            ),
        )
        if not self.trial.start_at <= cutoff < self.trial.end_at or not (
            source_known <= cutoff <= known <= self.as_of
        ):
            raise ValueError("component_trace_capture_clock_invalid")
        if body["schema_version"] == "market-component-shadow-execution.v2":
            usage = ComponentExecutionUsage.model_validate(body["execution_usage"])
            if not cutoff <= usage.started_at <= usage.completed_at == known:
                raise ValueError("component_trace_execution_clock_invalid")
        payload = self.raw(intent["input_ref"], intent["input_hash"])
        output = self.item.callback(payload, dict(self.item.parameters))
        output = json.loads(json.dumps(output, allow_nan=False))
        self.item.verify()
        if (canonical_hash(payload), output, canonical_hash(output)) != (
            body["input_hash"],
            body["output"],
            body["output_hash"],
        ):
            raise ValueError("component_trace_callback_changed")
        self.add("component_shadow", row.audit_id, actual)
        return payload, cutoff, source_known

    def candle(self, row):
        key = row.candle_id, canonical_hash(json.loads(row.candle_json))
        if key not in self.candle_cache:
            if len(self.candle_cache) >= MAX_NATIVE_CANDLES:
                raise ValueError("component_trace_native_census_bound_exceeded")
            reference = self.add("market_candle", row.candle_id, json.loads(row.candle_json))
            self.candle_cache[key] = read_candle_original_in_session(
                self.session,
                reference=reference,
                archive=self.context.raw_archive,
                expected_scope_id=self.manifest.scope_id,
                cutoff_at=self.as_of,
            )
            self.native_by_id[row.candle_id] = self.candle_cache[key]
        return self.candle_cache[key]

    def source_capture(self, candle, cutoff):
        payload = self.raw(candle.capture_ref, candle.capture_hash)
        raw_key = candle.capture_ref, candle.capture_hash
        if raw_key not in self.source_records:
            self.source_records[raw_key] = self.session.scalars(
                select(RawCaptureRecord)
                .where(
                    RawCaptureRecord.body_ref == candle.capture_ref,
                    RawCaptureRecord.body_hash == candle.capture_hash,
                )
                .limit(129)
            ).all()
        records = self.source_records[raw_key]
        if len(records) > 128:
            raise ValueError("component_trace_source_census_bound_exceeded")
        matches = [
            r
            for r in records
            if r.status_code == 200
            and r.error_code is None
            and as_utc(r.completed_at) == candle.received_at
            and MarketPathStore._url_matches(r.url, spot_scope(candle.instrument), candle)
        ]
        if not matches:
            raise ValueError("component_trace_source_capture_missing")
        # A raw body can recur. Select the first authentic receipt for this exact response time.
        record = min(matches, key=lambda r: as_utc(r.recorded_at))
        known = max(as_utc(record.completed_at), as_utc(record.recorded_at))
        if (
            as_utc(record.started_at) > as_utc(record.completed_at)
            or as_utc(record.recorded_at) < as_utc(record.completed_at)
            or known > cutoff
            or record.byte_count != self.raw_sizes[raw_key]
        ):
            raise ValueError("component_trace_source_first_known_invalid")
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise ValueError("component_trace_source_shape_invalid")
        try:
            values = payload["data"][candle.row_index]
            if (
                len(values) != 9
                or str(values[8]) != "1"
                or int(values[0]) != int(candle.open_at.timestamp() * 1000)
            ):
                raise ValueError("component_trace_source_row_invalid")
            for field, index in (
                ("open", 1),
                ("high", 2),
                ("low", 3),
                ("close", 4),
                ("volume_base", 5),
                ("volume_quote", 7),
            ):
                if getattr(candle, field) != Decimal(str(values[index])):
                    raise ValueError("component_trace_source_value_invalid")
        except (IndexError, KeyError, TypeError, ArithmeticError) as exc:
            raise ValueError("component_trace_source_row_invalid") from exc
        self.add(
            "raw_capture",
            record.capture_id,
            {
                "capture_id": record.capture_id,
                "url": record.url,
                "body_ref": record.body_ref,
                "body_hash": record.body_hash,
                "byte_count": record.byte_count,
                "started_at": as_utc(record.started_at).isoformat(),
                "completed_at": as_utc(record.completed_at).isoformat(),
                "recorded_at": as_utc(record.recorded_at).isoformat(),
                "status_code": record.status_code,
                "error_code": record.error_code,
                "adapter_version": record.adapter_version,
            },
        )
        return known

    def sample(self, sample, stale_seconds):
        """Authenticate only a native acquisition feature; DTO hash alone is insufficient."""
        if sample.scope_id != self.manifest.scope_id or sample.as_of > self.as_of:
            raise ValueError("component_trace_sample_basis_invalid")
        if sample.input_ref.startswith("risk-data://unavailable/"):
            if (
                sample.close_return is not None
                or sample.data_as_of is not None
                or (
                    sample.input_ref != "risk-data://unavailable/" + sample.input_hash
                    or sample.manifest_hash != sample.input_hash
                )
            ):
                raise ValueError("component_trace_unknown_sample_invalid")
            return False
        key = sample.input_ref, sample.input_hash, sample.as_of, stale_seconds
        if key in self.feature_cache:
            return self.feature_cache[key]
        row = self.session.scalar(
            select(MarketPathAcquisitionRecord)
            .where(
                MarketPathAcquisitionRecord.scope_id == sample.scope_id,
                MarketPathAcquisitionRecord.feature_ref == sample.input_ref,
                MarketPathAcquisitionRecord.feature_hash == sample.input_hash,
                MarketPathAcquisitionRecord.status == "succeeded",
                MarketPathAcquisitionRecord.known_at <= sample.as_of,
            )
            .order_by(MarketPathAcquisitionRecord.known_at.desc())
            .limit(1)
        )
        if row is None:
            raise ValueError("component_trace_native_feature_missing")
        receipt = json.loads(row.receipt_json)
        if (
            canonical_hash(receipt) != row.receipt_hash
            or any(
                receipt.get(field) != getattr(row, field)
                for field in ("scope_id", "acquisition_id", "status", "feature_ref", "feature_hash")
            )
            or _time(receipt["known_at"]) != as_utc(row.known_at)
            or _time(receipt["started_at"]) != as_utc(row.started_at)
            or _time(receipt["window_start_at"]) != as_utc(row.window_start_at)
            or _time(receipt["window_end_at"]) != as_utc(row.window_end_at)
            or row.completed_at is None
            or not as_utc(row.started_at) <= as_utc(row.completed_at) == as_utc(row.known_at)
        ):
            raise ValueError("component_trace_acquisition_changed")
        path = Path(row.feature_ref).resolve()
        root = self.context.raw_archive.root.resolve().parent
        if not path.is_relative_to(root) or path.suffix != ".json":
            raise ValueError("component_trace_feature_path_invalid")
        data = path.read_bytes()
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != row.feature_hash:
            raise ValueError("component_trace_feature_changed")
        feature = MarketFeatureSnapshot.model_validate_json(data)
        scope = spot_scope(feature.scope.instrument)
        if (
            scope != feature.scope
            or scope.scope_id != sample.scope_id
            or aware_utc(feature.as_of) > sample.as_of
            or feature.known_at is None
            or aware_utc(feature.known_at) > sample.as_of
        ):
            raise ValueError("component_trace_feature_time_invalid")
        rows = self.session.scalars(
            select(MarketCandleRecord)
            .where(
                MarketCandleRecord.scope_id == sample.scope_id,
                MarketCandleRecord.open_at >= as_utc(row.window_end_at) - timedelta(minutes=1471),
                MarketCandleRecord.open_at < as_utc(row.window_end_at),
                MarketCandleRecord.known_at <= aware_utc(feature.as_of),
            )
            .order_by(
                MarketCandleRecord.open_at,
                MarketCandleRecord.known_at,
                MarketCandleRecord.candle_id,
            )
            .limit(MAX_NATIVE_CANDLES + 1)
        ).all()
        if len(rows) > MAX_NATIVE_CANDLES:
            raise ValueError("component_trace_native_census_bound_exceeded")
        latest = {}
        for candle_row in rows:
            latest[as_utc(candle_row.open_at)] = candle_row
        bars = tuple(self.candle(r) for r in latest.values())
        for bar in bars:
            self.source_capture(bar, feature.as_of)
        expected = build_market_features(
            scope, bars, feature.as_of, stale_after_seconds=stale_seconds
        )
        if expected != feature or receipt.get("manifest_hash") != feature.manifest_hash:
            raise ValueError("component_trace_feature_recompute_changed")
        stale = (sample.as_of - feature.latest_close_at).total_seconds() > stale_seconds
        state = "stale" if stale else feature.status
        reason = (
            "latest_closed_bar_stale"
            if stale
            else next(
                (v for v in feature.reasons if v != "trade_candles_do_not_supply_bid_ask_spread"),
                None,
            )
        )
        if (
            sample.data_as_of,
            sample.close_return,
            sample.data_state,
            sample.reason,
            sample.manifest_hash,
            sample.window_seconds,
        ) != (
            feature.latest_close_at,
            feature.return_30m if state == "fresh" else None,
            state,
            reason,
            feature.manifest_hash,
            1800,
        ):
            raise ValueError("component_trace_sample_recompute_changed")
        self.add("market_acquisition", row.acquisition_id, receipt)
        self.feature_cache[key] = True
        return True

    def result(self, handler, metrics, reasons, *, sample_qualified=False):
        cost_verified, cost_reasons = verify_local_execution_cost(self.captures)
        reasons = tuple(dict.fromkeys((*reasons, *cost_reasons)))
        metrics["local_execution_policy"] = LOCAL_EXECUTION_POLICY
        metrics["qualification_scope"] = "component_codec_and_rule_safety_only"
        return ComponentProtocolResult(
            protocol_id=handler.protocol_id,
            protocol_hash=handler.protocol_hash,
            status="passed"
            if sample_qualified and cost_verified and not reasons
            else "insufficient",
            reasons=reasons,
            original_refs=tuple(self.refs.values()),
            known_at=self.as_of,
            independent_blocks=len(metrics.get("utc_day_blocks", [])),
            sample_qualified=sample_qualified,
            cost_verified=cost_verified,
            metrics=metrics,
        )


class SourceParserOriginalProtocol:
    kind = "source_parser"
    protocol_id = "component-source-parser-native-trace.v1"
    protocol_hash = canonical_hash(_policy(kind))

    def evaluate(self, session, *, manifest, trial, captures, original_refs, as_of, context):
        trace = _Trace(session, manifest, trial, captures, original_refs, as_of, context, self.kind)
        frames, days, ticks, reasons = [], set(), set(), []
        for body in captures:
            payload, cutoff, source_known = trace.capture(body)
            path = PathComponentInput.model_validate(payload).path
            scope = spot_scope(path.scope.instrument)
            start, end = aware_utc(path.window_start_at), aware_utc(path.window_end_at)
            if (
                path.scope != scope
                or scope.scope_id != manifest.scope_id
                or not start < end <= cutoff
                or (end - start).total_seconds() > 86400
                or start.timestamp() % 60
                or end.timestamp() % 60
                or path.page_count < 1
                or path.request_count < path.page_count
                or path.duplicate_count < 0
            ):
                raise ValueError("component_trace_path_basis_invalid")
            if path_hashes(scope, path.candles) != (path.semantic_hash, path.manifest_hash):
                raise ValueError("component_trace_path_hash_invalid")
            opened, raw_known, native_known = set(), [], []
            for bar in path.candles:
                if bar.scope_id != scope.scope_id or not start <= bar.open_at < end:
                    raise ValueError("component_trace_path_bar_basis_invalid")
                if bar.open_at in opened:
                    raise ValueError("component_trace_path_bar_duplicate")
                opened.add(bar.open_at)
                raw_known.append(trace.source_capture(bar, cutoff))
                candle_id = (
                    "candle_"
                    + canonical_hash(
                        [
                            bar.scope_id,
                            canonical_hash(bar.semantic_payload()),
                        ]
                    )[:48]
                )
                actual = trace.native_by_id.get(candle_id)
                if actual is None:
                    row = session.get(MarketCandleRecord, candle_id, populate_existing=True)
                    if row is None:
                        raise ValueError("component_trace_native_candle_missing")
                    actual = trace.candle(row)
                if actual.semantic_payload() != bar.semantic_payload():
                    raise ValueError("component_trace_native_semantic_changed")
                native_known.append(actual.known_at)
            expected_open = {
                start + timedelta(minutes=i) for i in range(int((end - start).total_seconds() / 60))
            }
            missing = expected_open - opened
            if set(map(aware_utc, path.missing_open_at)) != missing or path.status != (
                "partial" if missing else "complete"
            ):
                raise ValueError("component_trace_path_gap_mismatch")
            if raw_known and max(raw_known) > source_known:
                raise ValueError("component_trace_claimed_source_known_too_early")
            native_pit = bool(native_known) and max(native_known) <= cutoff
            if not native_pit:
                reasons.append("insufficient_for_downstream_pit")
            if missing:
                reasons.append("component_source_path_incomplete")
            days.add(cutoff.date().isoformat())
            ticks.add(int((cutoff - trial.start_at).total_seconds() // 60))
            frames.append(
                {
                    "cutoff_at": cutoff.isoformat(),
                    "bar_count": len(opened),
                    "expected_bars": len(expected_open),
                    "missing_bars": len(missing),
                    "source_first_known_at": max(raw_known).isoformat() if raw_known else None,
                    "native_market_known_at": max(native_known).isoformat()
                    if native_known
                    else None,
                    "downstream_pit_verified": native_pit,
                    "codec_conformant": True,
                }
            )
        expected = int((trial.end_at - trial.start_at).total_seconds() + 59) // 60
        missing_frames = max(0, expected - len(ticks))
        if not frames:
            reasons.append("component_source_capture_unavailable")
        if trace.as_of < trial.end_at:
            reasons.append("component_trial_not_mature")
        if missing_frames:
            reasons.append("component_registered_window_capture_incomplete")
        qualified = bool(frames) and trace.as_of >= trial.end_at and not reasons
        return trace.result(
            self,
            {
                "codec_conformant_captures": len(frames),
                "frames": frames,
                "utc_day_blocks": sorted(days),
                "expected_capture_ticks": expected,
                "observed_capture_ticks": len(ticks),
                "missing_capture_ticks": missing_frames,
                "trading_authority": False,
                "probability_qualified": False,
            },
            reasons,
            sample_qualified=qualified,
        )


class MonitorRuleOriginalProtocol:
    kind = "monitor_rule"
    protocol_id = "component-monitor-rule-native-trace.v1"
    protocol_hash = canonical_hash(_policy(kind))

    def evaluate(self, session, *, manifest, trial, captures, original_refs, as_of, context):
        trace = _Trace(session, manifest, trial, captures, original_refs, as_of, context, self.kind)
        plans, captures_verified, missing_native, missing_evals = {}, 0, 0, 0
        for body in captures:
            payload, cutoff, _ = trace.capture(body)
            value = RuleComponentInput.model_validate(payload)
            if (
                value.rule.scope_id != manifest.scope_id
                or value.sample.as_of != cutoff
                or (value.now > _time(body["known_at"]) or value.now < cutoff)
            ):
                raise ValueError("component_trace_rule_cutoff_invalid")
            native = trace.sample(value.sample, value.rule.stale_after_seconds)
            missing_native += int(not native)
            rule_hash = content_hash(value.rule.model_dump(mode="json"))
            plan_id = "rplan_" + content_hash([manifest.scope_id, value.rule.condition_id])[:48]
            plan = session.get(RiskMonitorPlanRecord, plan_id, populate_existing=True)
            if plan is None:
                missing_evals += 1
                continue
            if (
                plan.rule_hash != rule_hash
                or json.loads(plan.rule_json) != value.rule.model_dump(mode="json")
                or plan.scope_id != manifest.scope_id
                or plan.rule_version != value.rule.condition_id
            ):
                raise ValueError("component_trace_native_rule_changed")
            plans[plan_id] = (plan, value.rule)
            evaluations = session.scalars(
                select(RiskConditionEvaluationRecord)
                .where(
                    RiskConditionEvaluationRecord.plan_id == plan_id,
                    RiskConditionEvaluationRecord.evaluated_at == value.now,
                )
                .limit(3)
            ).all()
            matched = [
                e
                for e in evaluations
                if json.loads(e.input_json) == value.sample.model_dump(mode="json")
            ]
            if len(matched) != 1:
                missing_evals += 1
                continue
            captures_verified += 1
        counts = dict(
            expected_ticks=0,
            evaluated_ticks=0,
            missing_ticks=0,
            true_ticks=0,
            false_ticks=0,
            unknown_ticks=0,
            error_ticks=0,
            episodes=0,
            recoveries=0,
            outbox_intents=0,
            provider_accepted=0,
            displayed_alerts=0,
            acknowledged_alerts=0,
        )
        days, reasons = set(), []
        for plan, rule in plans.values():
            rows = session.scalars(
                select(RiskConditionEvaluationRecord)
                .where(
                    RiskConditionEvaluationRecord.plan_id == plan.plan_id,
                    RiskConditionEvaluationRecord.evaluated_at <= min(trace.as_of, trial.end_at),
                )
                .order_by(
                    RiskConditionEvaluationRecord.evaluated_at,
                    RiskConditionEvaluationRecord.tick_at,
                )
                .limit(MAX_EVALUATIONS + 1)
            ).all()
            if len(rows) > MAX_EVALUATIONS:
                raise ValueError("component_trace_evaluation_census_bound_exceeded")
            counts["expected_ticks"] += (
                int((trial.end_at - trial.start_at).total_seconds() + rule.evaluation_seconds - 1)
                // rule.evaluation_seconds
            )
            ticks, last_data, enter, exit_, active = set(), None, 0, 0, None
            expected_alerts = set()
            for row in rows:
                from packages.kernel.decision_hub_kernel.ports.risk_monitor import RiskMonitorInput

                sample = RiskMonitorInput.model_validate_json(row.input_json)
                at, tick = as_utc(row.evaluated_at), as_utc(row.tick_at)
                expected_id = (
                    "reval_"
                    + content_hash(
                        [
                            plan.plan_id,
                            rule.condition_id,
                            tick.isoformat(),
                        ]
                    )[:48]
                )
                if (
                    row.evaluation_id != expected_id
                    or row.rule_version != rule.condition_id
                    or (
                        tick > sample.as_of
                        or sample.as_of > at
                        or row.input_hash != sample.input_hash
                        or row.data_state != sample.data_state
                    )
                ):
                    raise ValueError("component_trace_evaluation_identity_invalid")
                native = trace.sample(sample, rule.stale_after_seconds)
                truth, metric, reason = evaluate_condition(rule, sample, now=at)
                if (
                    sample.data_as_of is not None
                    and last_data is not None
                    and sample.data_as_of < last_data
                ):
                    truth, metric, reason = "unknown", None, "risk_input_time_regressed"
                if (row.truth_state, json.loads(row.value_json), row.reason) != (
                    truth,
                    str(metric) if metric is not None else None,
                    reason,
                ):
                    raise ValueError("component_trace_evaluation_recompute_changed")
                if truth in ("unknown", "error"):
                    enter, exit_ = 0, 0
                else:
                    new_bar = sample.data_as_of is not None and (
                        last_data is None or sample.data_as_of > last_data
                    )
                    if new_bar:
                        last_data = sample.data_as_of
                        enter = enter + 1 if truth == "true" else 0
                        exit_ = exit_ + 1 if truth == "false" else 0
                    if truth == "true" and active is None and enter >= rule.enter_confirm_bars:
                        active = "rep_" + content_hash([plan.plan_id, row.evaluation_id])[:48]
                        if trial.start_at <= tick < trial.end_at:
                            expected_alerts.add("ralert_" + content_hash([active])[:48])
                    elif (
                        truth == "false" and active is not None and exit_ >= rule.exit_confirm_bars
                    ):
                        alert = session.scalar(
                            select(RiskAlertRecord).where(
                                RiskAlertRecord.episode_id == active,
                            )
                        )
                        if (
                            alert is None
                            or alert.closed_at is None
                            or as_utc(alert.closed_at) != at
                        ):
                            raise ValueError("component_trace_recovery_original_invalid")
                        if trial.start_at <= tick < trial.end_at:
                            counts["recoveries"] += 1
                        active = None
                if row.episode_id != active:
                    raise ValueError("component_trace_episode_recompute_changed")
                trace.add(
                    "risk_evaluation",
                    row.evaluation_id,
                    {
                        "evaluation_id": row.evaluation_id,
                        "plan_id": row.plan_id,
                        "rule_version": row.rule_version,
                        "tick_at": tick.isoformat(),
                        "evaluated_at": at.isoformat(),
                        "truth_state": row.truth_state,
                        "data_state": row.data_state,
                        "value": json.loads(row.value_json),
                        "reason": row.reason,
                        "input": json.loads(row.input_json),
                        "input_hash": row.input_hash,
                        "episode_id": row.episode_id,
                    },
                )
                if trial.start_at <= tick < trial.end_at:
                    ticks.add(tick)
                    days.add(tick.date().isoformat())
                    counts[truth + "_ticks"] += 1
                    missing_native += int(not native)
            counts["evaluated_ticks"] += len(ticks)
            alerts = session.scalars(
                select(RiskAlertRecord)
                .where(
                    RiskAlertRecord.plan_id == plan.plan_id,
                    RiskAlertRecord.created_at >= trial.start_at,
                    RiskAlertRecord.created_at < trial.end_at,
                    RiskAlertRecord.created_at <= trace.as_of,
                )
                .limit(MAX_EVALUATIONS + 1)
            ).all()
            if len(alerts) > MAX_EVALUATIONS:
                raise ValueError("component_trace_alert_census_bound_exceeded")
            if {alert.alert_id for alert in alerts} != expected_alerts:
                raise ValueError("component_trace_episode_alert_original_missing")
            for alert in alerts:
                self._alert(trace, alert, rule, counts)
        if not plans:
            counts["expected_ticks"] = (
                int((trial.end_at - trial.start_at).total_seconds() + 59) // 60
            )
        counts["missing_ticks"] = max(0, counts["expected_ticks"] - counts["evaluated_ticks"])
        if trace.as_of < trial.end_at:
            reasons.append("component_trial_not_mature")
        if counts["missing_ticks"] or missing_evals or not captures:
            reasons.append("component_registered_window_evaluation_incomplete")
        if missing_native:
            reasons.append("component_monitor_sample_original_unavailable")
        # These native receipts record a point display/ack, not continuous observation.
        qualified = bool(captures) and trace.as_of >= trial.end_at and not reasons
        return trace.result(
            self,
            {
                "counts": counts,
                "utc_day_blocks": sorted(days),
                "captures_with_native_evaluation": captures_verified,
                "captures_without_native_evaluation": missing_evals,
                "samples_without_native_market_original": missing_native,
                "exposure_status": "unknown",
                "exposure_reasons": ["component_full_window_exposure_original_unavailable"],
                "user_benefit_status": "unknown",
                "system_rule_conformance": "verified"
                if (captures_verified and not missing_native and not missing_evals)
                else "insufficient",
                "trading_authority": False,
                "probability_qualified": False,
            },
            reasons,
            sample_qualified=qualified,
        )

    @staticmethod
    def _alert(trace, alert, rule, counts):
        body = json.loads(alert.payload_json)
        evaluation = trace.session.get(
            RiskConditionEvaluationRecord, alert.evaluation_id, populate_existing=True
        )
        if evaluation is None:
            raise ValueError("component_trace_alert_evaluation_missing")
        episode = "rep_" + content_hash([alert.plan_id, alert.evaluation_id])[:48]
        alert_id = "ralert_" + content_hash([episode])[:48]
        if (
            canonical_hash(body) != alert.payload_hash
            or alert.alert_id != alert_id
            or alert.episode_id != episode
            or alert.scope_id != trace.manifest.scope_id
            or alert.rule_version != rule.condition_id
            or alert.severity != rule.severity
            or alert.alarm_kind != "fact_change"
            or body.get("schema_version") != "risk-alert.v1"
            or body.get("alert_id") != alert_id
            or body.get("scope_id") != alert.scope_id
            or body.get("condition") != rule.model_dump(mode="json")
            or body.get("prediction_hit") is not False
            or body.get("alarm_kind") != "fact_change"
            or body.get("input") != json.loads(evaluation.input_json)
            or body.get("value") != json.loads(evaluation.value_json)
            or evaluation.truth_state != "true"
            or evaluation.episode_id != episode
            or _time(body["created_at"]) != as_utc(alert.created_at)
            or as_utc(alert.created_at) != as_utc(evaluation.evaluated_at)
            or as_utc(alert.observed_at) != _time(body["input"]["data_as_of"])
            or as_utc(alert.published_at) < as_utc(alert.created_at)
        ):
            raise ValueError("component_trace_alert_original_invalid")
        trace.add("risk_alert", alert.alert_id, body)
        counts["episodes"] += 1
        outboxes = trace.session.scalars(
            select(OutboxRecord)
            .where(
                OutboxRecord.subject_kind == "risk_alert",
                OutboxRecord.subject_id == alert.alert_id,
            )
            .limit(129)
        ).all()
        if len(outboxes) != 1:
            raise ValueError("component_trace_outbox_episode_identity_invalid")
        outbox = outboxes[0]
        envelope = verify_notification_envelope(
            NotificationEnvelopeV2.model_validate_json(
                outbox.payload_json,
            )
        )
        if (
            envelope.subject_id != alert.alert_id
            or envelope.subject_kind != "risk_alert"
            or envelope.payload_hash != outbox.payload_hash
            or envelope.channel != outbox.channel
            or outbox.artifact_id is not None
            or envelope.dedupe_key != "risk-alert:" + episode
            or envelope.dedupe_key != outbox.dedupe_key
            or envelope.created_at != as_utc(alert.created_at)
        ):
            raise ValueError("component_trace_outbox_original_invalid")
        trace.add(
            "outbox",
            outbox.id,
            {"envelope": envelope.model_dump(mode="json"), "dedupe_key": outbox.dedupe_key},
        )
        counts["outbox_intents"] += 1
        if (
            outbox.provider_accepted_at is not None
            and as_utc(outbox.provider_accepted_at) <= trace.as_of
        ):
            if as_utc(outbox.provider_accepted_at) < envelope.created_at or outbox.sent_at is None:
                raise ValueError("component_trace_provider_acceptance_clock_invalid")
            counts["provider_accepted"] += 1
        receipts = trace.session.scalars(
            select(RiskAlertDeliveryReceiptRecord)
            .where(
                RiskAlertDeliveryReceiptRecord.alert_id == alert.alert_id,
                RiskAlertDeliveryReceiptRecord.received_at <= trace.as_of,
            )
            .limit(513)
        ).all()
        if len(receipts) > 512:
            raise ValueError("component_trace_delivery_census_bound_exceeded")
        seen = set()
        for row in receipts:
            payload = json.loads(row.payload_json)
            if row.receipt_kind == "send":
                # Legacy send body has a different contract; never treat it as user exposure.
                continue
            if (
                row.receipt_kind not in ("display", "ack")
                or row.channel != "pc"
                or payload.get("receipt_id") != row.receipt_id
                or payload.get("alert_id") != alert.alert_id
                or payload.get("kind") != row.receipt_kind
                or payload.get("request_id") != row.request_id
                or _time(payload["received_at"]) != as_utc(row.received_at)
                or as_utc(row.received_at) < as_utc(alert.published_at)
                or row.receipt_id
                != "rrcp_"
                + content_hash(
                    [
                        alert.alert_id,
                        row.receipt_kind,
                        row.request_id,
                    ]
                )[:48]
            ):
                raise ValueError("component_trace_delivery_original_invalid")
            trace.add("risk_delivery", row.receipt_id, payload)
            seen.add(row.receipt_kind)
        counts["displayed_alerts"] += int("display" in seen)
        counts["acknowledged_alerts"] += int("ack" in seen)
