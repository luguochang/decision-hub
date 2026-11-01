"""One genuine archived close window, selected research horizon, existing services."""

from datetime import datetime, timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
# Register the real publication models before persisted_original creates tables.
from tests.decision_product.test_perpetual_action_publication import (
    assert_actual_report_then_unique_perpetual_action,
    authenticate_provider,
    lawful_packet_for,
)
from tests.decision_product.test_perpetual_research_binding import AT
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command, counts


def selected_command(case, horizon):
    return {
        **command(case),
        "request_id": "selected-" + horizon,
        "schema_version": "perpetual-research-command.v1"
        if horizon == "4h"
        else "perpetual-research-command.v2",
        "horizon": horizon,
    }


@pytest.mark.parametrize("horizon,seconds", [("24h", 86400), ("72h", 259200), ("4h", 14400)])
def test_signed_selected_horizon_freezes_exact_real_target_and_original_receipt(
    binding_case, horizon, seconds, tmp_path
):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, StudyRecord

    case = binding_case
    client, service, collector = client_for(case)
    body = selected_command(case, horizon)
    reply = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    receipt = reply.json()
    assert receipt["schema_version"] == (
        "perpetual-research-receipt.v1" if horizon == "4h" else "perpetual-research-receipt.v2"
    )
    if horizon != "4h":
        assert receipt["horizon"] == horizon
    with case["db"].session() as session:
        run = session.get(RunRecord, receipt["run_id"])
        study = session.get(StudyRecord, run.study_id)
        admission, ref = read_perpetual_admission_identity_in_session(session, run.run_id)
        assert study.horizon_scope == admission["context"]["horizon"] == horizon
        target, rule = admission["candidate_target"], admission["anchor_rule"]
        assert target["horizon"] == rule["horizon"] == horizon
        assert target["duration_seconds"] == rule["duration_seconds"] == seconds
        threshold = next(
            row for row in admission["close_window"]["thresholds"] if row["horizon"] == horizon
        )
        assert target["threshold_fraction"] == threshold["fraction"]
        assert admission["opportunity"]["time_end"] == target["valid_until"]
        assert datetime.fromisoformat(target["valid_until"]) == AT + timedelta(hours=1)
        assert ref == receipt["admission_ref"]
    assert client.get(BASE + "/research/commands/" + body["request_id"]).json() == receipt
    baseline = counts(case)
    assert (
        client.post(
            BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
        ).json()
        == receipt
    )
    altered = {
        **body,
        "schema_version": "perpetual-research-command.v2",
        "horizon": "72h" if horizon != "72h" else "24h",
    }
    assert (
        client.post(
            BASE + "/research", json=altered, headers={"Idempotency-Key": body["request_id"]}
        ).status_code
        == 409
    )
    assert counts(case) == baseline and collector.calls == 1
    if horizon == "72h":
        from packages.kernel.decision_hub_kernel.application.research_binding import (
            read_research_bundle_in_session,
        )
        from tests.decision_product.test_perpetual_research_binding import factory_for

        case.update(run_id=receipt["run_id"], event_id=receipt["event_id"])
        factory, _ = factory_for(case, tmp_path)
        request = factory.build(case["event_id"], case["run_id"])
        with case["db"].session() as session:
            task = read_research_bundle_in_session(session, request.run_id).task
            assert task.scope.horizon_scope == ["72h"]
    with case["db"].session() as session:
        session.get(StudyRecord, receipt["study_id"]).horizon_scope = (
            "24h" if horizon != "24h" else "4h"
        )
    with case["db"].session() as session:
        with pytest.raises(ValueError, match="perpetual_research_horizon_original_mismatch"):
            service.binding.read_admission_in_session(session, receipt["run_id"])


def test_advice_isolates_horizon_before_latest_limit(binding_case):
    from tests.decision_product.test_perpetual_advice_query import params

    case = binding_case
    client, _, _ = client_for(case)
    for horizon in ("24h", "4h"):
        body = selected_command(case, horizon)
        reply = client.post(
            BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
        )
        assert reply.status_code == 200, reply.text
    for horizon in ("4h", "24h", "72h"):
        reply = client.get(BASE + "/advice/current", params={**params(case), "horizon": horizon})
        assert reply.status_code == 200, reply.text
        value = reply.json()
        assert value["horizon"] == horizon
        assert value["schema_version"] == "perpetual-advice-current." + (
            "v1" if horizon == "4h" else "v2"
        )
        assert value["status"] == ("not_started" if horizon == "72h" else "researching")
    assert (
        client.get(BASE + "/advice/current", params={**params(case), "horizon": "30m"}).status_code
        == 422
    )


def test_actual_24h_signed_entry_to_native_graph_unique_action_and_saved_report(
    binding_case, tmp_path, monkeypatch
):
    """Synthetic child backend; real archived source/service writes/complete read path."""
    import json
    from pathlib import Path

    from tests.decision_product.test_perpetual_native_graph import build_perpetual_native_graph_case

    case = binding_case
    client, _, collector = client_for(case)
    body = selected_command(case, "24h")
    response = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert response.status_code == 200, response.text
    original = response.json()
    case.update(run_id=original["run_id"], event_id=original["event_id"])
    value = build_perpetual_native_graph_case(
        case,
        tmp_path,
        monkeypatch,
        packet_builder=lawful_packet_for,
        before_graph=lambda value: authenticate_provider(value, monkeypatch),
        phased=True,
    )
    receipt, advice = assert_actual_report_then_unique_perpetual_action(
        value,
        monkeypatch,
        horizon="24h",
        saved_report_example_path="artifacts/d2-multi-horizon/actual-24h-saved-report.json",
    )
    target = advice["target"]
    assert target["duration_seconds"] == 86400
    assert datetime.fromisoformat(target["window_end"]) - datetime.fromisoformat(
        target["window_start"]
    ) == timedelta(hours=24)
    expected = next(
        row for row in value["materials"]["close_window"]["thresholds"] if row["horizon"] == "24h"
    )
    assert target["threshold_fraction"] == expected["fraction"]
    assert advice["schema_version"] == "perpetual-advice-current.v2"
    assert (
        advice["snapshot"]["context"]["horizon"]
        == advice["revision"]["horizon"]
        == advice["comparison"]["horizon"]
        == "24h"
    )
    assert client.get(BASE + "/research/commands/" + body["request_id"]).json() == original
    assert collector.calls == 1
    Path("artifacts/d2-multi-horizon/actual-24h-advice.json").write_text(
        json.dumps(advice, ensure_ascii=False, indent=2), "utf-8"
    )
