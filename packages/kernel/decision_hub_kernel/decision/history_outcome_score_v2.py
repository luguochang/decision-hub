"""Whole registered census and same-forecast paired Brier for hourly outcomes.

The service must authenticate all originals before calling this pure function.
Schema/hash validation here is not archive/SQL authority or financial proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from ..ports.history_dataset_forecast_v2 import HistoryDatasetProspectiveForecastV2
from ..ports.history_observed_training import verify_payload
from ..ports.history_outcome_v2 import (
    LABEL_STATES,
    PUBLICATION_STATES,
    SCOPES,
    TARGETS,
    HistoryOutcomeAcceptanceV2,
    HistoryOutcomeCaseResultV2,
    HistoryOutcomeObservationV2,
    HistoryOutcomeOpportunityV2,
    HistoryOutcomeProtocolV2,
    HistoryOutcomePublicationV2,
    HistoryOutcomeTargetScoreV2,
    OutcomeContractV2,
    OutcomeOriginalRefV2,
    verify_outcome,
)
from ..ports.market_path import aware_utc


@dataclass(frozen=True, slots=True)
class OutcomeScoreInputsV2:
    opportunity: HistoryOutcomeOpportunityV2
    opportunity_ref: OutcomeOriginalRefV2
    publication: HistoryOutcomePublicationV2 | None = None
    publication_ref: OutcomeOriginalRefV2 | None = None
    observation: HistoryOutcomeObservationV2 | None = None
    observation_ref: OutcomeOriginalRefV2 | None = None
    forecast: HistoryDatasetProspectiveForecastV2 | None = None
    acceptance: HistoryOutcomeAcceptanceV2 | None = None
    acceptance_ref: OutcomeOriginalRefV2 | None = None


def _ref(
    value: OutcomeContractV2 | HistoryDatasetProspectiveForecastV2,
    reference: OutcomeOriginalRefV2 | None,
    identity: str,
) -> None:
    if (
        reference is None
        or reference.identifier != identity
        or reference.payload_hash != value.payload_hash
    ):
        raise ValueError("history_outcome_score_original_identity_invalid")


def calculate_outcome_score_v2(
    protocol: HistoryOutcomeProtocolV2,
    protocol_ref: OutcomeOriginalRefV2,
    inputs: tuple[OutcomeScoreInputsV2, ...],
    *,
    as_of: datetime,
    registration_acceptance: HistoryOutcomeAcceptanceV2 | None = None,
    registration_acceptance_ref: OutcomeOriginalRefV2 | None = None,
) -> dict:
    protocol = verify_outcome(protocol)
    clock = aware_utc(as_of)
    _ref(protocol, protocol_ref, protocol.protocol_id)
    if clock < protocol.recorded_at or len(inputs) != 48:
        raise ValueError("history_outcome_score_registered_census_required")
    registered = None
    if registration_acceptance is not None:
        registered = verify_outcome(registration_acceptance)
        _ref(registered, registration_acceptance_ref, registered.acceptance_id)
        if (
            registered.accepted_kind != "protocol"
            or registered.subject != protocol_ref
            or registered.protocol != protocol_ref
            or registered.owner_claim != protocol.owner_claim
            or registered.subject_recorded_at != protocol.recorded_at
            or not protocol.recorded_at <= registered.independently_verified_at
            < protocol.first_hour
            or registered.recorded_at > clock
        ):
            raise ValueError("history_outcome_registration_confirmation_invalid")
    elif registration_acceptance_ref is not None:
        raise ValueError("history_outcome_unbound_registration_confirmation")
    expected = {
        (scope, protocol.first_hour + timedelta(hours=index))
        for index in range(24)
        for scope in SCOPES
    }
    seen = set()
    pub_counts = dict.fromkeys(PUBLICATION_STATES, 0)
    label_counts = dict.fromkeys(LABEL_STATES, 0)
    losses = {target: [] for target in TARGETS}
    results = []
    grants = {grant.scope_id: grant for grant in protocol.model_grants}
    for item in sorted(
        inputs, key=lambda value: (value.opportunity.hour_at, value.opportunity.scope_id)
    ):
        opportunity = verify_outcome(item.opportunity)
        _ref(opportunity, item.opportunity_ref, opportunity.opportunity_id)
        key = opportunity.scope_id, opportunity.hour_at
        grant = grants.get(opportunity.scope_id)
        if (
            key not in expected
            or key in seen
            or opportunity.protocol != protocol_ref
            or opportunity.owner_claim != protocol.owner_claim
            or opportunity.registered_at != protocol.registered_at
            or grant is None
            or opportunity.model != grant.model
            or opportunity.fixture_mode != protocol.fixture_mode
            or opportunity.recorded_at > clock
        ):
            raise ValueError("history_outcome_score_enrollment_changed")
        seen.add(key)
        due = clock >= opportunity.dispatch_deadline_at
        pub = item.publication
        exclusions = []
        if registered is None:
            exclusions.append("enrollment_confirmation_unavailable")
        cutoff = opportunity.hour_at
        if pub is None:
            if (
                item.publication_ref is not None
                or item.observation is not None
                or item.forecast is not None
            ):
                raise ValueError("history_outcome_score_unbound_result")
            pub_state = "unconfirmed" if due else "pending"
            if due:
                exclusions.append("publication_reconciliation_pending")
        else:
            pub = verify_outcome(pub)
            _ref(pub, item.publication_ref, pub.publication_id)
            if (
                pub.protocol != protocol_ref
                or pub.opportunity != item.opportunity_ref
                or pub.owner_claim != opportunity.owner_claim
                or pub.scope_id != opportunity.scope_id
                or pub.model != opportunity.model
                or pub.hour_at != opportunity.hour_at
                or pub.registered_at != opportunity.registered_at
                or pub.fixture_mode != opportunity.fixture_mode
                or pub.recorded_at > clock
            ):
                raise ValueError("history_outcome_score_publication_binding_invalid")
            pub_state, cutoff = pub.classification, pub.label_cutoff_at
        label_end = cutoff + timedelta(days=1, minutes=1)
        label_due = clock >= label_end
        observation = item.observation
        if observation is None:
            if (
                item.observation_ref is not None
                or item.acceptance is not None
                or item.acceptance_ref is not None
            ):
                raise ValueError("history_outcome_score_unbound_observation")
            label_state = (
                "pending_maturity"
                if not label_due
                else "missing_at_deadline"
                if clock >= label_end + timedelta(days=1)
                else "partial"
            )
            if label_due:
                exclusions.append("label_original_unavailable")
        else:
            observation = verify_outcome(observation)
            _ref(observation, item.observation_ref, observation.observation_id)
            if (
                pub is None
                or observation.protocol != protocol_ref
                or observation.opportunity != item.opportunity_ref
                or observation.publication != item.publication_ref
                or observation.owner_claim != opportunity.owner_claim
                or observation.scope_id != opportunity.scope_id
                or observation.label_cutoff_at != cutoff
                or observation.fixture_mode != opportunity.fixture_mode
                or observation.recorded_at > clock
            ):
                raise ValueError("history_outcome_score_observation_binding_invalid")
            label_state = observation.classification
            acceptance = item.acceptance
            if acceptance is None:
                if item.acceptance_ref is not None:
                    raise ValueError("history_outcome_unbound_label_confirmation")
                if label_state == "verified":
                    label_state = (
                        "missing_at_deadline" if clock >= observation.label_deadline_at else "partial"
                    )
                exclusions.append("label_confirmation_unavailable")
            else:
                acceptance = verify_outcome(acceptance)
                _ref(acceptance, item.acceptance_ref, acceptance.acceptance_id)
                if (
                    acceptance.accepted_kind != "observation"
                    or acceptance.subject != item.observation_ref
                    or acceptance.protocol != protocol_ref
                    or acceptance.opportunity != item.opportunity_ref
                    or acceptance.owner_claim != opportunity.owner_claim
                    or acceptance.fixture_mode != protocol.fixture_mode
                    or acceptance.subject_recorded_at != observation.recorded_at
                    or acceptance.independently_verified_at < observation.observed_at
                    or acceptance.recorded_at > clock
                ):
                    raise ValueError("history_outcome_label_confirmation_invalid")
                if acceptance.independently_verified_at > observation.label_deadline_at:
                    label_state = "late_diagnostic"
        forecast = item.forecast
        if forecast is not None:
            forecast = verify_payload(forecast)
            if (
                pub is None
                or pub.forecast is None
                or pub.actual_cutoff_at is None
                or pub.forecast.identifier != forecast.forecast_id
                or pub.forecast.payload_hash != forecast.payload_hash
                or forecast.owner_claim != opportunity.owner_claim
                or forecast.scope_id != opportunity.scope_id
                or forecast.model_id != opportunity.model.identifier
                or forecast.model_hash != opportunity.model.payload_hash
                or forecast.cutoff_at != pub.actual_cutoff_at
                or forecast.fixture_mode != protocol.fixture_mode
                or forecast.recorded_at > clock
                or pub.verified_readback_at is not None
                and pub.verified_readback_at < forecast.recorded_at
            ):
                raise ValueError("history_outcome_score_forecast_binding_invalid")
        if protocol.fixture_mode:
            exclusions.append("fixture_not_financial_evidence")
        if pub_state != "on_time":
            exclusions.append("publication_" + pub_state)
        if label_state != "verified":
            exclusions.append("label_" + label_state)
        if forecast is None:
            exclusions.append("forecast_original_unavailable")
        paired = not exclusions
        if paired:
            with localcontext() as context:
                context.prec, context.rounding = 28, ROUND_HALF_EVEN
                frequencies = {frequency.threshold: frequency for frequency in forecast.frequencies}
                for direction, threshold in TARGETS:
                    frequency = frequencies[threshold]
                    baseline = getattr(frequency, "baseline_" + direction + "_probability")
                    conditional = getattr(frequency, "conditional_" + direction + "_probability")
                    actual = Decimal(getattr(observation, "mae_" + direction) >= Decimal(threshold))
                    losses[(direction, threshold)].append(
                        ((baseline - actual) ** 2, (conditional - actual) ** 2)
                    )
        pub_counts[pub_state] += 1
        label_counts[label_state] += 1
        results.append(
            HistoryOutcomeCaseResultV2(
                opportunity_id=opportunity.opportunity_id,
                scope_id=opportunity.scope_id,
                hour_at=opportunity.hour_at,
                publication_state=pub_state,
                label_state=label_state,
                label_cutoff_at=cutoff,
                publication_due=due,
                label_due=label_due,
                eligible_pair=paired,
                exclusions=tuple(exclusions),
                opportunity=item.opportunity_ref,
                publication=item.publication_ref,
                observation=item.observation_ref,
                forecast=None if pub is None else pub.forecast,
                acceptance=item.acceptance_ref,
            )
        )
    if seen != expected:
        raise ValueError("history_outcome_score_census_missing")
    targets = []
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        for direction, threshold in TARGETS:
            pairs = losses[(direction, threshold)]
            size = len(pairs)
            baseline = sum((left for left, right in pairs), Decimal(0)) / size if size else None
            conditional = sum((right for left, right in pairs), Decimal(0)) / size if size else None
            gain = sum((left - right for left, right in pairs), Decimal(0)) / size if size else None
            targets.append(
                HistoryOutcomeTargetScoreV2(
                    direction=direction,
                    threshold=threshold,
                    n_baseline=size,
                    n_conditional=size,
                    n_pair=size,
                    baseline_brier=baseline,
                    conditional_brier=conditional,
                    paired_gain=gain,
                )
            )
    return dict(
        actual_as_of=clock,
        expected_total=48,
        publication_due_count=sum(case.publication_due for case in results),
        label_due_count=sum(case.label_due for case in results),
        publication_counts=pub_counts,
        label_counts=label_counts,
        published_count=pub_counts["on_time"] + pub_counts["late"],
        on_time_count=pub_counts["on_time"],
        verified_label_count=label_counts["verified"],
        paired_count=sum(case.eligible_pair for case in results),
        targets=tuple(targets),
        cases=tuple(results),
        primary_target="long:0.02",
        ci=None,
        ci_status="not_registered_for_hourly_overlapping_windows",
    )
