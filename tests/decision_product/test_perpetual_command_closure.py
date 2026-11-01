"""Actual SQLite command fencing; no model or Graph execution."""

import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
    parse_perpetual_research_command,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.decision_product.test_perpetual_research_entry import (
    BASE,
    OWNER,
    client_for,
    command,
    counts,
)


def test_explicit_close_fences_late_writer_and_accepted_original_wins(binding_case):
    case = binding_case
    client, service, collector = client_for(case)
    body = command(case)
    endpoint = BASE + "/research/commands/" + body["request_id"] + "/close"
    before = counts(case)
    reply = client.post(endpoint, json=body, headers={"Idempotency-Key": body["request_id"]})
    assert reply.status_code == 200, reply.text
    closed = reply.json()
    assert closed["status"] == "closed_unaccepted"
    assert closed["command"] == body
    assert client.get(BASE + "/research/commands/" + body["request_id"]).json() == closed
    assert (
        client.post(endpoint, json=body, headers={"Idempotency-Key": body["request_id"]}).json()
        == closed
    )
    assert counts(case) == tuple(n + (i == 3) for i, n in enumerate(before))
    # A former in-flight request cannot pass the writer even with an old prepared value.
    with pytest.raises(ValueError, match="perpetual_research_command_closed"):
        with case["db"].session() as session:
            begin_immediate(session)
            service.commit_in_session(
                session, OWNER, parse_perpetual_research_command(body), object()
            )
    refused = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert (
        refused.status_code == 409
        and refused.json()["detail"] == "perpetual_research_command_closed"
    )
    assert collector.calls == 0
    altered = {**body, "expected_settings_state_version": 2}
    assert (
        client.post(
            endpoint, json=altered, headers={"Idempotency-Key": body["request_id"]}
        ).status_code
        == 409
    )
    accepted_body = {**body, "request_id": "accepted-original"}
    accepted = client.post(
        BASE + "/research",
        json=accepted_body,
        headers={"Idempotency-Key": accepted_body["request_id"]},
    )
    assert accepted.status_code == 200, accepted.text
    after = counts(case)
    recovered = client.post(
        BASE + "/research/commands/accepted-original/close",
        json=accepted_body,
        headers={"Idempotency-Key": "accepted-original"},
    )
    assert recovered.json() == accepted.json()
    assert counts(case) == after
    # The manual closure endpoint cannot cancel a separately authorized automatic slot.
    automatic_body = {**body, "request_id": "existing-automatic-slot"}
    with case["db"].session() as session:
        session.add(
            RiskResearchRequestRecord(
                request_id=automatic_body["request_id"],
                scope_id="synthetic-auto-scope",
                instrument="BTC-USDT-SWAP",
                created_at=AT,
                updated_at=AT,
                available_at=AT,
            )
        )
    refused = client.post(
        BASE + "/research/commands/existing-automatic-slot/close",
        json=automatic_body,
        headers={"Idempotency-Key": automatic_body["request_id"]},
    )
    assert refused.status_code == 409
    assert (
        refused.json()["detail"] == "perpetual_research_automatic_request_requires_schedule_pause"
    )
    assert counts(case) == after
