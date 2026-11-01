"""Necessary mathematical census/clock controls, not archive or market evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.decision.history_outcome_score_v2 import (
    OutcomeScoreInputsV2,
    calculate_outcome_score_v2 as calculate_verified_score,
)
from packages.kernel.decision_hub_kernel.ports.history_dataset_forecast_v2 import (
    HistoryDatasetProspectiveForecastV2,
    HistoryDatasetRiskFrequencyV2,
)
from packages.kernel.decision_hub_kernel.ports.history_observed_training import seal
from packages.kernel.decision_hub_kernel.ports.history_outcome_v2 import (
    SCOPES,
    HistoryOutcomeAcceptanceV2,
    HistoryOutcomeObservationV2,
    HistoryOutcomeOpportunityV2,
    HistoryOutcomeProtocolV2,
    HistoryOutcomePublicationV2,
    OutcomeModelGrantV2,
    OutcomeOriginalRefV2,
    seal_outcome,
)
from packages.kernel.decision_hub_kernel.ports.market_path import MarketCandle, canonical_hash

H = datetime(2026, 10, 1, tzinfo=UTC)
K = H - timedelta(hours=1)
Z = "0" * 64


def common(at=K, fixture=False):
    return dict(
        owner_claim="isolated-formula-control",
        request_id="finite-control",
        fixture_mode=fixture,
        source_files=(("not-real-fixture-source", Z),),
        source_original_ref="not-real-fixture-source-original",
        source_original_hash=Z,
        produced_at=at,
        recorded_at=at,
    )


def ref(value, identifier):
    return OutcomeOriginalRefV2(
        identifier=identifier,
        payload_hash=value.payload_hash,
        original_ref="not-real-original/" + identifier,
        original_hash=Z,
    )


def confirmation(subject, reference, protocol_ref, *, kind, opportunity=None):
    instant = subject.recorded_at + timedelta(seconds=1)
    values = common(instant, subject.fixture_mode)
    values["request_id"] = "confirmation-" + reference.identifier
    return seal_outcome(
        HistoryOutcomeAcceptanceV2,
        **values,
        acceptance_id="hacceptance2_" + canonical_hash(
            [subject.owner_claim, kind, reference.identifier]
        )[:48],
        protocol=protocol_ref,
        opportunity=opportunity,
        accepted_kind=kind,
        subject=reference,
        subject_recorded_at=subject.recorded_at,
        independently_verified_at=instant,
        readback_evidence_ref="not-real-independent-readback",
        readback_evidence_hash=Z,
    )


def calculate_outcome_score_v2(protocol, reference, inputs, *, as_of):
    accepted = confirmation(protocol, reference, reference, kind="protocol")
    return calculate_verified_score(
        protocol, reference, inputs, as_of=as_of,
        registration_acceptance=accepted,
        registration_acceptance_ref=ref(accepted, accepted.acceptance_id),
    )


def enrolled(fixture=False):
    model_grants = tuple(
        OutcomeModelGrantV2(
            scope_id=scope,
            model=OutcomeOriginalRefV2(
                identifier="hfit2_" + str(index + 1) * 48,
                payload_hash=Z,
                original_ref="not-real-model",
                original_hash=Z,
            ),
            dataset=OutcomeOriginalRefV2(
                identifier="hds2_dataset_" + "3" * 48,
                payload_hash=Z,
                original_ref="not-real-dataset",
                original_hash=Z,
            ),
            model_known_at=K,
        )
        for index, scope in enumerate(SCOPES)
    )
    protocol = seal_outcome(
        HistoryOutcomeProtocolV2,
        **common(fixture=fixture),
        protocol_id="heval2_" + "4" * 48,
        registered_at=K,
        first_hour=H,
        last_hour=H + timedelta(hours=23),
        model_grants=model_grants,
    )
    protocol_ref = ref(protocol, protocol.protocol_id)
    inputs = []
    for index in range(24):
        for grant in model_grants:
            hour = H + timedelta(hours=index)
            opportunity = seal_outcome(
                HistoryOutcomeOpportunityV2,
                **common(fixture=fixture),
                opportunity_id="hopportunity2_"
                + canonical_hash([protocol.protocol_id, grant.scope_id, hour.isoformat()])[:48],
                protocol=protocol_ref,
                scope_id=grant.scope_id,
                model=grant.model,
                hour_at=hour,
                registered_at=K,
                dispatch_deadline_at=hour + timedelta(minutes=5),
            )
            inputs.append(
                OutcomeScoreInputsV2(opportunity, ref(opportunity, opportunity.opportunity_id))
            )
    return protocol, protocol_ref, tuple(inputs)


def attach(item, state, *, fixture=False, label=True):
    opportunity = item.opportunity
    cutoff = opportunity.hour_at
    actual = cutoff if state != "missing" else None
    pub_at = cutoff + timedelta(minutes=6)
    forecast = None
    if state in ("on_time", "late"):
        instrument = opportunity.scope_id.split(":")[3]
        closes = tuple(
            MarketCandle(
                scope_id=opportunity.scope_id,
                venue="okx",
                instrument=instrument,
                open_at=cutoff - timedelta(minutes=31 - index),
                close_at=cutoff - timedelta(minutes=30 - index),
                open=Decimal(100),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100),
                received_at=cutoff - timedelta(minutes=30 - index),
                capture_ref="fixture-raw",
                capture_hash=Z,
                row_index=index,
            )
            for index in range(31)
        )
        forecast = seal(
            HistoryDatasetProspectiveForecastV2,
            forecast_id="hforecast2_" + "6" * 48,
            owner_claim=opportunity.owner_claim,
            request_id="formula-forecast",
            scope_id=opportunity.scope_id,
            model_id=opportunity.model.identifier,
            model_hash=opportunity.model.payload_hash,
            model_known_at=K,
            model_authenticated_at=cutoff + timedelta(seconds=1),
            model_original_ref="not-real-model",
            model_original_hash=Z,
            cutoff_at=cutoff,
            latest_close_at=cutoff,
            feature_known_at=cutoff,
            selected_closes=closes,
            feature_original_ref="not-real-features",
            feature_original_hash=Z,
            original_readset=(("raw", "fixture-raw", Z),),
            realized_vol_30m=Decimal(0),
            selected_group="low",
            frequencies=tuple(
                HistoryDatasetRiskFrequencyV2(
                    threshold=threshold,
                    baseline_n=60,
                    conditional_n=30,
                    baseline_long_probability=Decimal(".2"),
                    baseline_short_probability=Decimal(".3"),
                    conditional_long_probability=Decimal(".35"),
                    conditional_short_probability=Decimal(".2"),
                )
                for threshold in ("0.02", "0.05")
            ),
            source_files=(("not-real-source", Z),),
            source_original_ref="not-real-source",
            source_original_hash=Z,
            produced_at=cutoff + timedelta(seconds=1),
            recorded_at=cutoff + timedelta(seconds=2),
            fixture_mode=fixture,
        )
    publication = seal_outcome(
        HistoryOutcomePublicationV2,
        **common(pub_at, fixture),
        publication_id="hpublication2_" + canonical_hash([opportunity.opportunity_id])[:48],
        protocol=opportunity.protocol,
        opportunity=item.opportunity_ref,
        scope_id=opportunity.scope_id,
        model=opportunity.model,
        hour_at=cutoff,
        registered_at=K,
        classification=state,
        reason_code="isolated_formula_control",
        dispatch=None
        if actual is None
        else OutcomeOriginalRefV2(
            identifier="fixture-dispatch", payload_hash=Z, original_ref="fixture", original_hash=Z
        ),
        forecast=None if forecast is None else ref(forecast, forecast.forecast_id),
        actual_cutoff_at=actual,
        label_cutoff_at=cutoff,
        label_anchor_basis="scheduled_counterfactual"
        if actual is None
        else "actual_first_dispatch",
        verified_readback_at=None
        if forecast is None
        else cutoff + timedelta(seconds=30 if state == "on_time" else 61),
        diagnosed_at=pub_at,
    )
    publication_ref = ref(publication, publication.publication_id)
    observation = None
    if label:
        end = cutoff + timedelta(days=1, minutes=1)
        observation = seal_outcome(
            HistoryOutcomeObservationV2,
            **common(end + timedelta(seconds=1), fixture),
            observation_id="hobservation2_" + canonical_hash([opportunity.opportunity_id])[:48],
            protocol=opportunity.protocol,
            opportunity=item.opportunity_ref,
            publication=publication_ref,
            scope_id=opportunity.scope_id,
            label_cutoff_at=cutoff,
            window_start_at=cutoff + timedelta(minutes=1),
            window_end_at=end,
            label_deadline_at=end + timedelta(days=1),
            observed_at=end,
            accepted_at=None,
            attempt=1,
            classification="verified",
            reason_code="isolated_complete_shape_control",
            anchor_status="verified",
            anchor_price=Decimal(100),
            observed_count=1440,
            pending_count=0,
            window_status="complete",
            mae_long=Decimal(".03"),
            mae_short=Decimal(".01"),
            path_semantic_hash=Z,
            path_manifest_hash=Z,
            observation_original_ref="not-real-path",
            observation_original_hash=Z,
        )
    observation_ref = None if observation is None else ref(observation, observation.observation_id)
    accepted = None if observation is None else confirmation(
        observation, observation_ref, opportunity.protocol,
        kind="observation", opportunity=item.opportunity_ref,
    )
    return replace(
        item,
        publication=publication,
        publication_ref=publication_ref,
        observation=observation,
        observation_ref=observation_ref,
        forecast=forecast,
        acceptance=accepted,
        acceptance_ref=None if accepted is None else ref(accepted, accepted.acceptance_id),
    )


def test_whole_census_counts_all_six_failure_states_without_doubling():
    protocol, reference, inputs = enrolled()
    states = ("on_time", "late", "blocked", "failed", "missing", "unconfirmed")
    values = tuple(
        attach(item, states[index]) if index < 6 else item for index, item in enumerate(inputs)
    )
    score = calculate_outcome_score_v2(
        protocol, reference, values, as_of=H + timedelta(hours=26, minutes=2)
    )
    assert score["expected_total"] == 48 and score["publication_due_count"] == 48
    assert score["label_due_count"] == 6 and score["verified_label_count"] == 6
    assert score["published_count"] == 2 and score["on_time_count"] == score["paired_count"] == 1
    assert score["publication_counts"]["unconfirmed"] == 43
    main = score["targets"][0]
    assert main.n_pair == 1 and main.baseline_brier == Decimal(".64")
    assert main.conditional_brier == Decimal(".4225") and main.paired_gain == Decimal(".2175")
    assert main.ci is None and score["ci"] is None


def test_no_worker_still_retains_48_opportunities_null_score():
    protocol, reference, inputs = enrolled()
    score = calculate_outcome_score_v2(protocol, reference, inputs, as_of=H + timedelta(minutes=6))
    assert score["expected_total"] == 48 and score["publication_due_count"] == 2
    assert (
        score["publication_counts"]["unconfirmed"] == 2
        and score["publication_counts"]["pending"] == 46
    )
    assert score["paired_count"] == 0 and all(
        target.baseline_brier is None for target in score["targets"]
    )


def test_duplicate_missing_and_foreign_model_are_rejected():
    protocol, reference, inputs = enrolled()
    for changed in (inputs[:-1], (*inputs[:-1], inputs[0])):
        with pytest.raises(ValueError):
            calculate_outcome_score_v2(protocol, reference, changed, as_of=H)
    different = inputs[0].opportunity.model.model_copy(update={"payload_hash": "f" * 64})
    invalid = inputs[0].opportunity.model_dump(exclude={"payload_hash"})
    invalid["model"] = different
    changed = seal_outcome(HistoryOutcomeOpportunityV2, **invalid)
    with pytest.raises(ValueError, match="enrollment_changed"):
        calculate_outcome_score_v2(
            protocol,
            reference,
            (
                replace(
                    inputs[0],
                    opportunity=changed,
                    opportunity_ref=ref(changed, changed.opportunity_id),
                ),
                *inputs[1:],
            ),
            as_of=H,
        )


def test_future_results_cannot_enter_an_older_as_of():
    protocol, reference, inputs = enrolled()
    item = attach(inputs[0], "on_time")
    with pytest.raises(ValueError, match="observation_binding_invalid"):
        calculate_outcome_score_v2(
            protocol, reference, (item, *inputs[1:]), as_of=H + timedelta(minutes=10)
        )


def test_one_minute_and_incomplete_label_do_not_become_complete():
    protocol, reference, inputs = enrolled()
    item = attach(inputs[0], "on_time")
    values = item.observation.model_dump(exclude={"payload_hash"})
    values["observed_count"] = 1
    with pytest.raises(ValueError, match="complete_label_required"):
        seal_outcome(HistoryOutcomeObservationV2, **values)
    values = item.observation.model_dump(exclude={"payload_hash"})
    values["window_end_at"] = values["window_start_at"] + timedelta(minutes=1)
    with pytest.raises(ValueError, match="label_window_or_clock_invalid"):
        seal_outcome(HistoryOutcomeObservationV2, **values)


def test_fixture_late_and_partial_never_enter_pair():
    protocol, reference, inputs = enrolled(fixture=True)
    item = attach(inputs[0], "on_time", fixture=True)
    score = calculate_outcome_score_v2(
        protocol, reference, (item, *inputs[1:]), as_of=H + timedelta(hours=24, minutes=2)
    )
    assert (
        score["paired_count"] == 0
        and "fixture_not_financial_evidence" in score["cases"][0].exclusions
    )
    protocol, reference, inputs = enrolled()
    for state, label in (("late", True), ("on_time", False)):
        item = attach(inputs[0], state, label=label)
        score = calculate_outcome_score_v2(
            protocol, reference, (item, *inputs[1:]), as_of=H + timedelta(hours=24, minutes=2)
        )
        assert score["paired_count"] == 0
