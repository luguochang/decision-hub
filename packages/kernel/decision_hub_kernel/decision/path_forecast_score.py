"""Finite paired empirical losses. No unregistered CI, alarm match or promotion."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from ..ports.market_path import aware_utc, canonical_hash
from ..ports.path_forecast import (
    PathForecastParameters,
    PathForecastParametersV1,
    PathObservationReceipt,
    RiskPathForecast,
)
from .path_forecast import PARAMETERS, THRESHOLDS, verify_path_forecast
from .path_score_inference import MatchedEventBlock, PairedBrierLoss, paired_day_bootstrap


def verify_observation(receipt: PathObservationReceipt) -> None:
    PathObservationReceipt.model_validate(receipt.model_dump(mode="json"))
    if canonical_hash(receipt.model_dump(mode="json", exclude={"payload_hash"})) != (
        receipt.payload_hash
    ):
        raise ValueError("path_observation_payload_changed")


def observation_complete(receipt: PathObservationReceipt) -> bool:
    path = receipt.path
    return bool(
        path.window_end_at <= path.as_of
        and path.window_status == "complete"
        and path.quality == "observed"
        and path.anchor_status == "verified"
        and path.anchor_price is not None
        and path.observed_count == path.expected_count == 1440
        and not path.missing_ranges
        and not path.missing_open_at
        and not path.pending_count
        and path.mae_long is not None
        and path.mae_short is not None
    )


def _mean(values: list[Decimal]) -> str | None:
    if not values:
        return None
    with localcontext() as context:
        context.prec, context.rounding = PARAMETERS.decimal_precision, ROUND_HALF_EVEN
        return str(sum(values, Decimal(0)) / len(values))


def _deltas(pairs: list[tuple[Decimal, Decimal]]) -> list[Decimal]:
    with localcontext() as context:
        context.prec, context.rounding = PARAMETERS.decimal_precision, ROUND_HALF_EVEN
        return [left - right for left, right in pairs]


def _score_path_forecasts_v1(
    predictions: tuple[RiskPathForecast, ...],
    observations: tuple[PathObservationReceipt, ...],
    *,
    expected_slots: tuple[tuple[str, datetime], ...],
    as_of: datetime,
) -> dict:
    """Score only immutable on-time pairs; every registered slot remains in the denominator."""
    clock = aware_utc(as_of)
    slots = tuple((scope, aware_utc(cutoff)) for scope, cutoff in expected_slots)
    if len(set(slots)) != len(slots):
        raise ValueError("path_score_duplicate_expected_slot")
    expected = set(slots)
    forecasts = {}
    for forecast in predictions:
        verify_path_forecast(forecast)
        key = (forecast.scope.scope_id, forecast.cutoff_at, forecast.model_version)
        if key[:2] not in expected or key in forecasts or forecast.published_at > clock:
            raise ValueError("path_score_forecast_identity_invalid")
        forecasts[key] = forecast
    labels = {}
    for receipt in observations:
        verify_observation(receipt)
        key = receipt.scope.scope_id, receipt.input_cutoff_at
        if key not in expected or receipt.label_known_at > clock:
            raise ValueError("path_score_observation_identity_invalid")
        prior = labels.get(key)
        if (
            prior is not None
            and prior.label_known_at == receipt.label_known_at
            and (prior.payload_hash != receipt.payload_hash)
        ):
            raise ValueError("path_score_conflicting_label_time")
        if prior is None or prior.label_known_at < receipt.label_known_at:
            labels[key] = receipt
    reasons, exclusions, targets, complete_days = Counter(), [], [], set()
    scopes = sorted({scope for scope, _ in slots})
    for scope in scopes:
        scope_slots = [(identity, cutoff) for identity, cutoff in slots if identity == scope]
        for direction in ("long", "short"):
            for threshold in THRESHOLDS:
                baseline_losses, candidate_losses, pairs, paired_days = [], [], [], set()
                for key in scope_slots:
                    base = forecasts.get((*key, PARAMETERS.baseline))
                    candidate = forecasts.get((*key, PARAMETERS.candidate))
                    label = labels.get(key)
                    excluded = []
                    if label is None:
                        excluded.append("label_unavailable")
                    elif not observation_complete(label):
                        excluded.append("label_incomplete")
                    if base is None:
                        excluded.append("baseline_unpublished")
                    if candidate is None:
                        excluded.append("candidate_unpublished")
                    same_inputs = bool(
                        base
                        and candidate
                        and (
                            base.feature_hash,
                            base.model_hash,
                            base.parameter_hash,
                            base.window_start_at,
                            base.window_end_at,
                        )
                        == (
                            candidate.feature_hash,
                            candidate.model_hash,
                            candidate.parameter_hash,
                            candidate.window_start_at,
                            candidate.window_end_at,
                        )
                    )
                    if base and candidate and not same_inputs:
                        excluded.append("pair_input_identity_mismatch")
                    losses = []
                    for method, forecast in (("baseline", base), ("candidate", candidate)):
                        frequency = (
                            next(
                                (
                                    item
                                    for item in forecast.frequencies
                                    if item.direction == direction and item.threshold == threshold
                                ),
                                None,
                            )
                            if forecast
                            else None
                        )
                        if forecast and forecast.publication_status != "on_time":
                            excluded.append(method + "_late")
                        if forecast and (frequency is None or frequency.probability is None):
                            excluded.append(method + "_insufficient")
                        eligible = bool(
                            label
                            and observation_complete(label)
                            and forecast
                            and forecast.publication_status == "on_time"
                            and frequency
                            and frequency.probability is not None
                        )
                        loss = None
                        if eligible:
                            value = getattr(label.path, "mae_" + direction)
                            with localcontext() as context:
                                context.prec = PARAMETERS.decimal_precision
                                context.rounding = ROUND_HALF_EVEN
                                loss = (frequency.probability - int(value >= threshold)) ** 2
                            (baseline_losses if method == "baseline" else candidate_losses).append(
                                loss
                            )
                        losses.append(loss)
                    if all(loss is not None for loss in losses) and same_inputs:
                        pairs.append((losses[0], losses[1]))
                        paired_days.add(key[1].date().isoformat())
                        if direction == "long" and threshold == Decimal("0.02"):
                            complete_days.add(key[1].date().isoformat())
                    if excluded:
                        exclusions.append(
                            {
                                "scope_id": scope,
                                "cutoff_at": key[1].isoformat(),
                                "direction": direction,
                                "threshold": str(threshold),
                                "reasons": excluded,
                            }
                        )
                        reasons.update(excluded)
                targets.append(
                    {
                        "scope_id": scope,
                        "direction": direction,
                        "threshold": str(threshold),
                        "primary": direction == "long" and threshold == Decimal("0.02"),
                        "expected_slots": len(scope_slots),
                        "baseline_scored": len(baseline_losses),
                        "candidate_scored": len(candidate_losses),
                        "paired_scored": len(pairs),
                        "baseline_brier": _mean(baseline_losses),
                        "candidate_brier": _mean(candidate_losses),
                        "paired_baseline_brier": _mean([left for left, _ in pairs]),
                        "paired_candidate_brier": _mean([right for _, right in pairs]),
                        "paired_delta": _mean(_deltas(pairs)),
                        "independent_day_blocks": len(paired_days),
                        "ci": None,
                        "ci_status": "not_registered",
                    }
                )
    complete_count = sum(observation_complete(label) for label in labels.values())
    observed_event_days = {
        label.input_cutoff_at.date().isoformat()
        for label in labels.values()
        if observation_complete(label) and label.path.mae_long >= Decimal("0.02")
    }
    return {
        "schema_version": "path-shadow-score.v1",
        "expected_slot_count": len(slots),
        "expected_prediction_count": 2 * len(slots),
        "published_prediction_count": len(forecasts),
        "unpublished_prediction_count": 2 * len(slots) - len(forecasts),
        "on_time_prediction_count": sum(
            f.publication_status == "on_time" for f in forecasts.values()
        ),
        "late_prediction_count": sum(f.publication_status == "late" for f in forecasts.values()),
        "complete_label_count": complete_count,
        "partial_label_count": len(labels) - complete_count,
        "unavailable_label_count": len(slots) - len(labels),
        "independent_paired_day_blocks": len(complete_days),
        "observed_primary_event_day_blocks": len(observed_event_days),
        "independent_event_blocks": None,
        "minimum_time_blocks": PARAMETERS.minimum_time_blocks,
        "minimum_event_blocks": PARAMETERS.minimum_event_blocks,
        "targets": targets,
        "exclusion_count_basis": "target_evaluations",
        "exclusions": exclusions,
        "exclusion_counts": dict(reasons),
        "alarm_metrics": {
            "precision": None,
            "recall": None,
            "false_episodes_per_asset_day": None,
            "lead_time": None,
            "status": "unknown",
            "reason": "forecast_alarm_and_authenticated_delivery_exposure_not_registered",
        },
        "ci": None,
        "ci_status": "not_registered",
        "promotion_allowed": False,
        "conclusion": "insufficient_evidence",
        "reasons": [
            "block_ci_and_power_protocol_not_registered",
            "authenticated_matching_event_blocks_unavailable",
            *(
                ["fewer_than_100_independent_day_blocks"]
                if len(complete_days) < PARAMETERS.minimum_time_blocks
                else []
            ),
        ],
    }


def score_path_forecasts(
    predictions: tuple[RiskPathForecast, ...],
    observations: tuple[PathObservationReceipt, ...],
    *,
    expected_slots: tuple[tuple[str, datetime], ...],
    as_of: datetime,
    parameters: PathForecastParameters | PathForecastParametersV1 = PARAMETERS,
    event_blocks: tuple[MatchedEventBlock, ...] | None = None,
    alarm_score: dict | None = None,
) -> dict:
    """Stored V1 is exact; prospective V2 adds the frozen primary CI protocol."""
    score = _score_path_forecasts_v1(
        predictions,
        observations,
        expected_slots=expected_slots,
        as_of=as_of,
    )
    if parameters.protocol_version == "path-shadow-24h.v1":
        if parameters != PathForecastParametersV1():
            raise ValueError("path_score_legacy_parameters_changed")
        return score
    if parameters != PathForecastParameters():
        raise ValueError("path_score_inference_parameters_changed")
    forecasts = {(f.scope.scope_id, f.cutoff_at, f.model_version): f for f in predictions}
    labels = {}
    for label in observations:
        key = label.scope.scope_id, label.input_cutoff_at
        if key not in labels or label.label_known_at > labels[key].label_known_at:
            labels[key] = label

    def pairs_for(slots):
        pairs = []
        for scope, cutoff in slots:
            baseline = forecasts.get((scope, cutoff, parameters.baseline))
            candidate = forecasts.get((scope, cutoff, parameters.candidate))
            label = labels.get((scope, cutoff))
            if not baseline or not candidate or not label or not observation_complete(label):
                continue
            left = next(
                f
                for f in baseline.frequencies
                if f.direction == "long" and f.threshold == Decimal("0.02")
            )
            right = next(
                f
                for f in candidate.frequencies
                if f.direction == "long" and f.threshold == Decimal("0.02")
            )
            if (
                left.probability is None
                or right.probability is None
                or (
                    baseline.publication_status != "on_time"
                    or candidate.publication_status != "on_time"
                    or baseline.feature_known_at is None
                    or candidate.feature_known_at is None
                    or (
                        baseline.feature_hash,
                        baseline.model_hash,
                        baseline.parameter_hash,
                        baseline.window_start_at,
                        baseline.window_end_at,
                    )
                    != (
                        candidate.feature_hash,
                        candidate.model_hash,
                        candidate.parameter_hash,
                        candidate.window_start_at,
                        candidate.window_end_at,
                    )
                )
            ):
                continue
            outcome = int(label.path.mae_long >= Decimal("0.02"))
            pairs.append(
                PairedBrierLoss(
                    scope_id=scope,
                    cutoff_at=cutoff,
                    baseline_loss=(left.probability - outcome) ** 2,
                    candidate_loss=(right.probability - outcome) ** 2,
                    input_known_at=max(baseline.feature_known_at, candidate.feature_known_at),
                    baseline_published_at=baseline.published_at,
                    candidate_published_at=candidate.published_at,
                    label_known_at=label.label_known_at,
                    window_end_at=label.path.window_end_at,
                )
            )
        return tuple(pairs)

    slots = tuple((scope, aware_utc(cutoff)) for scope, cutoff in expected_slots)
    inference = paired_day_bootstrap(
        pairs_for(slots), expected_pairs=slots, event_blocks=event_blocks, as_of=as_of
    )
    for target in score["targets"]:
        target["ci_reasons"] = ["secondary_inference_protocol_not_registered"]
        target["ci_status"] = "insufficient"
        if target["primary"]:
            scoped = tuple(slot for slot in slots if slot[0] == target["scope_id"])
            # A global event qualifies a target only with its original scope census.
            cutoffs = {cutoff for _, cutoff in scoped}
            scoped_events = (
                None
                if event_blocks is None or any(not event.scope_ids for event in event_blocks)
                else tuple(
                    event
                    for event in event_blocks
                    if aware_utc(event.cutoff_at) in cutoffs
                    and target["scope_id"] in event.scope_ids
                )
            )
            detail = paired_day_bootstrap(
                pairs_for(scoped), expected_pairs=scoped, event_blocks=scoped_events, as_of=as_of
            )
            target["ci"], target["ci_status"], target["ci_reasons"] = (
                detail["ci"],
                detail["status"],
                detail["reasons"],
            )
    score.update(
        {
            "schema_version": "path-shadow-score.v2",
            "ci": inference["ci"],
            "ci_status": inference["status"],
            "inference": inference,
            "independent_event_blocks": inference["event_blocks"],
            "alarm_originals": [],
            "reasons": list(
                dict.fromkeys(
                    inference["reasons"]
                    + [
                        "actual_usage_cost_qualification_unavailable",
                        "prediction_calibration_and_promotion_not_authorized",
                    ]
                )
            ),
        }
    )
    if alarm_score is not None:
        if alarm_score.get("schema_version") != "forecast-alarm-score.v1":
            raise ValueError("path_score_alarm_original_schema_invalid")
        score["alarm_metrics"] = alarm_score["metrics"]
        score["alarm_originals"] = alarm_score.get("original_refs", [])
    return score
