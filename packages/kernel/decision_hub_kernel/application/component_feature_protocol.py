"""Fixed feature conformance and same-original ablation; no inferred financial qualification."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from ..decision.path_forecast_score import score_path_forecasts, verify_observation
from ..decision.path_risk import compute_path_risk
from ..decision.path_score_inference import MatchedEventBlock
from ..persistence.db import as_utc
from ..persistence.path_forecast_records import (
    RiskEvaluationReportRecord,
    RiskPathObservationRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from ..persistence.risk_records import MarketCandleRecord
from ..ports.component_protocol import (
    LOCAL_EXECUTION_POLICY,
    ComponentOriginalRef,
    ComponentProtocolResult,
    verify_local_execution_cost,
)
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.path_forecast import PathObservationReceipt, parse_path_parameters
from .component_candle_originals import read_candle_original_in_session
from .market_component_functions import FeatureComponentInput
from .market_features import build_market_features
from .risk_path_ledger import REPORT, REPORT_V2, RiskPathLedger, checked_payload

FEATURE_PROTOCOL = {
    "version": "component-feature-native-originals-protocol.v1",
    "input": "market-feature-component-input.v1",
    "output": "market-feature-snapshot.v1",
    "formula": "closed-bar-fixed-market-features.v1",
    "lineage": "actual-native-candle-and-capture-full-clock.v1",
    "ablation": "same-input-BASE_EMPIRICAL_V1-vs-COND_VOL_V1-realized-vol-partition",
    "inference": "utc-day-paired-percentile-bootstrap.v2",
    "minimum_time_blocks": 100,
    "minimum_event_blocks": 30,
    "maximum_capture_bars": 1470,
    "maximum_reports": 1,
    "execution_cost": LOCAL_EXECUTION_POLICY,
    "financial_qualification": "not_granted_by_local_feature_upgrade",
    "trading_authority": False,
}
FEATURE_PROTOCOL_HASH = canonical_hash(FEATURE_PROTOCOL)


def _candle_ref(session, candle):
    identity = (
        "candle_"
        + canonical_hash([candle.scope_id, canonical_hash(candle.semantic_payload())])[:48]
    )
    row = session.get(MarketCandleRecord, identity, populate_existing=True)
    if row is None:
        raise ValueError("component_candle_original_missing")
    return ComponentOriginalRef(
        original_ref="market_candle:" + identity,
        original_hash=canonical_hash(json.loads(row.candle_json)),
    )


def _read_label(session, row, context):
    receipt = PathObservationReceipt.model_validate(
        checked_payload(row.payload_json, row.payload_hash)
    )
    verify_observation(receipt)
    if (
        receipt.scope.scope_id,
        receipt.input_cutoff_at,
        receipt.label_known_at,
        receipt.path.window_end_at,
        receipt.path.manifest_hash,
        receipt.path.quality,
    ) != (
        row.scope_id,
        as_utc(row.cutoff_at),
        as_utc(row.label_known_at),
        as_utc(row.window_end_at),
        row.path_hash,
        row.quality,
    ):
        raise ValueError("component_feature_label_row_changed")
    if len(receipt.originals) > 1440:
        raise ValueError("component_feature_label_bound_exceeded")
    bars = []
    for original in receipt.originals:
        identity = "candle_" + canonical_hash([receipt.scope.scope_id, original.revision_hash])[:48]
        candle_row = session.get(MarketCandleRecord, identity, populate_existing=True)
        if candle_row is None:
            raise ValueError("component_feature_label_candle_missing")
        ref = ComponentOriginalRef(
            original_ref="market_candle:" + identity,
            original_hash=canonical_hash(json.loads(candle_row.candle_json)),
        )
        bar = read_candle_original_in_session(
            session,
            reference=ref,
            archive=context.raw_archive,
            expected_scope_id=receipt.scope.scope_id,
            cutoff_at=receipt.path.as_of,
        )
        if (
            bar.open_at,
            canonical_hash(bar.semantic_payload()),
            bar.capture_ref,
            bar.capture_hash,
            bar.row_index,
            bar.received_at,
            bar.known_at,
        ) != (
            original.open_at,
            original.revision_hash,
            original.capture_ref,
            original.capture_hash,
            original.row_index,
            original.received_at,
            original.known_at,
        ):
            raise ValueError("component_feature_label_candle_binding_invalid")
        bars.append(bar)
    rebuilt = compute_path_risk(
        receipt.scope,
        tuple(bars),
        receipt.path.window_start_at,
        receipt.path.window_end_at,
        as_of=receipt.path.as_of,
    )
    if rebuilt != receipt.path:
        raise ValueError("component_feature_label_recomputation_changed")
    return receipt


def _read_ablation(session, reference, context, manifest, component_trial, as_of):
    identity = reference.original_ref.removeprefix("risk_evaluation_report:")
    row = session.get(RiskEvaluationReportRecord, identity, populate_existing=True)
    if row is None or row.payload_hash != reference.original_hash:
        raise ValueError("component_feature_ablation_original_changed")
    body = checked_payload(row.payload_json, row.payload_hash)
    trial = session.get(RiskShadowTrialRecord, row.trial_id, populate_existing=True)
    if trial is None:
        raise ValueError("component_feature_ablation_trial_missing")
    protocol = RiskPathLedger.protocol(trial)
    parameters = parse_path_parameters(protocol["parameters"])
    expected_version = REPORT if parameters.protocol_version == "path-shadow-24h.v1" else REPORT_V2
    computed = aware_utc(datetime.fromisoformat(body["computed_at"]))
    if (
        (
            body["report_id"],
            body["trial_id"],
            body["protocol_hash"],
            body["report_version"],
            computed,
        )
        != (
            row.report_id,
            row.trial_id,
            row.protocol_hash,
            expected_version,
            as_utc(row.computed_at),
        )
        or row.protocol_hash != trial.protocol_hash
        or computed > as_of
    ):
        raise ValueError("component_feature_ablation_row_changed")
    if manifest.scope_id not in {s["scope_id"] for s in protocol["scopes"]}:
        raise ValueError("component_feature_ablation_scope_invalid")
    if (
        as_utc(trial.registered_at) < component_trial.registered_at
        or as_utc(trial.first_cutoff_at) < component_trial.start_at
        or as_utc(trial.last_window_end_at) > component_trial.end_at
    ):
        raise ValueError("component_feature_ablation_not_prospective_for_component")
    slots = tuple(
        (scope["scope_id"], as_utc(trial.first_cutoff_at) + timedelta(days=i))
        for scope in protocol["scopes"]
        for i in range(parameters.shadow_days)
    )
    if len(body["prediction_refs"]) > 112 or len(body["observation_refs"]) > 56:
        raise ValueError("component_feature_ablation_census_bound_exceeded")
    forecasts = []
    for ref in body["prediction_refs"]:
        pred = session.get(RiskPredictionRecord, ref["prediction_id"], populate_existing=True)
        if (
            pred is None
            or pred.trial_id != trial.trial_id
            or pred.payload_hash != ref["payload_hash"]
        ):
            raise ValueError("component_feature_prediction_original_changed")
        forecasts.append(RiskPathLedger.prediction_payload(pred))
    labels = []
    for ref in body["observation_refs"]:
        label = session.get(
            RiskPathObservationRecord, ref["observation_id"], populate_existing=True
        )
        if label is None or label.payload_hash != ref["payload_hash"]:
            raise ValueError("component_feature_observation_original_changed")
        labels.append(_read_label(session, label, context))
    alarm = None
    review = body.get("alarm_score_original")
    if review is not None:
        reader = getattr(context.path_ledger, "forecast_alarm_original_reader", None)
        if reader is None:
            raise ValueError("component_feature_alarm_original_reader_unavailable")
        alarm = reader(session, **review)
        if (
            alarm["score_original_ref"] != review
            or alarm["original_refs"] != body["score"]["alarm_originals"]
        ):
            raise ValueError("component_feature_alarm_original_changed")
    blocks = (
        None
        if alarm is None
        else tuple(
            MatchedEventBlock(
                block_id=r["block_id"],
                cutoff_at=datetime.fromisoformat(r["cutoff_at"]),
                known_at=datetime.fromisoformat(r["known_at"]),
                original_ref=r["original_ref"],
                original_hash=r["original_hash"],
                scope_ids=tuple(r["scope_ids"]),
            )
            for r in alarm["matched_event_blocks"]
        )
    )
    score = score_path_forecasts(
        tuple(forecasts),
        tuple(labels),
        expected_slots=slots,
        as_of=computed,
        parameters=parameters,
        event_blocks=blocks,
        alarm_score=alarm,
    )
    if score != body["score"]:
        raise ValueError("component_feature_ablation_score_changed")
    return score, computed


class FeatureOriginalProtocol:
    kind = "feature"
    protocol_id = FEATURE_PROTOCOL["version"]
    protocol_hash = FEATURE_PROTOCOL_HASH

    def evaluate(self, session, *, manifest, trial, captures, original_refs, as_of, context):
        clock = aware_utc(as_of)
        if manifest.kind != self.kind or trial.protocol_hash != self.protocol_hash:
            raise ValueError("component_feature_protocol_binding_invalid")
        reasons = []
        refs = {r.original_ref: r for r in original_refs}
        known = trial.registered_at
        for ref in original_refs:
            if ref.original_ref.startswith("market_candle:"):
                actual = read_candle_original_in_session(
                    session,
                    reference=ref,
                    archive=context.raw_archive,
                    expected_scope_id=manifest.scope_id,
                    cutoff_at=clock,
                )
                known = max(known, actual.known_at)
            elif not ref.original_ref.startswith("risk_evaluation_report:"):
                raise ValueError("component_feature_reference_namespace_invalid")
        metrics = {
            "captures": len(captures),
            "native_bars": 0,
            "formula_verified": 0,
            "ablation": None,
            "trading_authority": False,
        }
        if not captures:
            reasons.append("component_feature_captures_missing")
        for capture in captures:
            intent = capture["intent"]
            body = context.raw_archive.read(intent["input_ref"])
            if hashlib.sha256(body).hexdigest() != intent["input_hash"]:
                raise ValueError("component_feature_input_raw_changed")
            value = FeatureComponentInput.model_validate_json(body)
            cutoff = aware_utc(datetime.fromisoformat(intent["cutoff_at"]))
            if (
                value.scope.scope_id != manifest.scope_id
                or value.as_of != cutoff
                or len(value.candles) > 1470
            ):
                raise ValueError("component_feature_capture_scope_invalid")
            native = []
            for candle in value.candles:
                ref = _candle_ref(session, candle)
                actual = read_candle_original_in_session(
                    session,
                    reference=ref,
                    archive=context.raw_archive,
                    expected_scope_id=manifest.scope_id,
                    cutoff_at=cutoff,
                )
                if actual != candle:
                    raise ValueError("component_feature_input_native_changed")
                prior = refs.setdefault(ref.original_ref, ref)
                if prior != ref:
                    raise ValueError("component_feature_reference_conflict")
                native.append(actual)
                known = max(known, actual.known_at)
            expected = build_market_features(
                value.scope, tuple(native), cutoff, stale_after_seconds=value.stale_after_seconds
            )
            if expected.model_dump(mode="json") != capture["output"]:
                raise ValueError("component_feature_formula_changed")
            metrics["formula_verified"] += 1
            metrics["native_bars"] += len(native)
            if expected.status != "fresh":
                reasons.append("component_feature_complete_closed_window_unavailable")
        reports = [r for r in original_refs if r.original_ref.startswith("risk_evaluation_report:")]
        if len(reports) > 1:
            raise ValueError("component_feature_ablation_bound_exceeded")
        blocks = 0
        if not reports:
            reasons.append("component_feature_same_input_ablation_originals_missing")
        else:
            score, report_known = _read_ablation(
                session,
                reports[0],
                context,
                manifest,
                trial,
                clock,
            )
            known = max(known, report_known)
            blocks = score["independent_paired_day_blocks"]
            metrics["ablation"] = score
            reasons.extend(score["reasons"])
        cost_verified, cost_reasons = verify_local_execution_cost(captures)
        reasons.extend(cost_reasons)
        sample_qualified = False
        if reports:
            # Local component approval is separate from the path report's trading authority.
            reasons = [
                r
                for r in reasons
                if r
                not in {
                    "actual_usage_cost_qualification_unavailable",
                    "prediction_calibration_and_promotion_not_authorized",
                }
            ]
            sample_qualified = score.get("ci_status") == "available"
            if sample_qualified and Decimal(score["ci"]["lower"]) <= 0:
                reasons.append("component_feature_positive_paired_gain_not_demonstrated")
                sample_qualified = False
        metrics["local_execution_cost_verified"] = cost_verified
        metrics["financial_cost_qualified"] = False
        return ComponentProtocolResult(
            protocol_id=self.protocol_id,
            protocol_hash=self.protocol_hash,
            status="passed"
            if sample_qualified and cost_verified and not reasons
            else "insufficient",
            reasons=tuple(dict.fromkeys(reasons)),
            original_refs=tuple(refs.values()),
            known_at=known,
            independent_blocks=blocks,
            sample_qualified=sample_qualified,
            cost_verified=cost_verified,
            metrics=metrics,
        )
