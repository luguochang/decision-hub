"""A only: explicit manual authority, actual settlement/next admission, atomic originals."""

import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    OWNER,
)
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)

POLICY = "saved_preferences_current_paper_ledger.v1"


def test_explicit_v3_contract_and_unforgeable_planned_run_authority():
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
        parse_perpetual_research_command,
    )

    body = dict(
        schema_version="perpetual-research-command.v3",
        request_id="explicit-current-paper",
        partition=dict(
            account_kind="paper",
            account_id="owner-paper",
            instrument_id="okx:BTC-USDT-SWAP",
            profile_ref="profile:exact",
        ),
        expected_settings_revision=dict(ref="settings:exact", hash="a" * 64),
        expected_settings_state_version=1,
        horizon="4h",
        input_policy=POLICY,
    )
    parsed = parse_perpetual_research_command(body)
    assert parsed.model_dump(mode="json") == body
    for changed in (
        {key: value for key, value in body.items() if key != "input_policy"},
        {**body, "input_policy": "inferred"},
        {**body, "authority": {"origin": "manual_v3"}},
        {**body, "partition": {**body["partition"], "account_kind": "user_declared"}},
    ):
        with pytest.raises(ValueError):
            parse_perpetual_research_command(changed)
    legacy = {key: value for key, value in body.items() if key != "input_policy"}
    for version, horizon in (("v1", "4h"), ("v2", "24h")):
        old = {
            **legacy,
            "schema_version": "perpetual-research-command." + version,
            "horizon": horizon,
        }
        assert parse_perpetual_research_command(old).model_dump(mode="json") == old

    from packages.kernel.decision_hub_kernel.application import perpetual_research_inputs as inputs

    at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    database = SimpleNamespace(engine=object())
    authority = inputs.issue_manual_input_authority(
        parsed,
        authenticated_owner=OWNER,
        planned_run_id="run_exact",
        database=database,
        prepared_at=at,
        valid_until=at + timedelta(hours=1),
    )
    assert authority.witness.planned_run_id == "run_exact"
    assert authority.witness.command.model_dump(mode="json") == body
    session = SimpleNamespace(get_bind=lambda: database.engine)
    with pytest.raises(ValueError, match="unissued"):
        inputs.prepare_effective_inputs_in_session(
            session, authority=copy.copy(authority), saved=None, paper_projection=None
        )
    with pytest.raises(ValueError, match="authority"):
        inputs.prepare_effective_inputs_in_session(
            session, authority={"input_policy": POLICY}, saved=None, paper_projection=None
        )


def _business_counts(case):
    from packages.kernel.decision_hub_kernel.persistence.action_records import (
        DecisionOpportunityRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        EventRecord,
        OutboxRecord,
        ProductCommandRecord,
        RunRecord,
        RunRoleOutputRecord,
        StudyRecord,
    )

    with case["db"].session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                EventRecord,
                StudyRecord,
                RunRecord,
                RunRoleOutputRecord,
                ProductCommandRecord,
                DecisionOpportunityRecord,
                OutboxRecord,
            )
        )


def test_actual_settlement_next_manual_v3_without_settings_save_and_atomic_history(
    binding_case, monkeypatch
):
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_research_entry as entry_module,
    )
    from packages.kernel.decision_hub_kernel.application import perpetual_research_inputs as inputs
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        CollectedPerpetualCloseWindow,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        ADMISSION_ROLE,
        _read,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
        parse_perpetual_research_command,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_records import (
        PerpetualPaperHeadRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
        account_key,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from tests.decision_product.test_perpetual_forecast_experience import future_pages
    from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

    case = binding_case
    _, manual, clock, executor = normal_queue_lifecycle(case, monkeypatch, return_runtime=True)
    partition = case["settings"].partition
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(session, OWNER, partition)
        unvalued_ledger = PerpetualPaperRepository(session).current(OWNER, partition.account_id)
    assert unvalued_ledger["funding_complete"] is False
    clock[0] = minute_end(clock[0]) + timedelta(minutes=1, seconds=10)
    at = clock[0]
    ids = future_pages(case, minute_end(at), prefix="account_A_closes_", count=1500)
    collected = CollectedPerpetualCloseWindow(ids, at, at)
    client, entry, _ = client_for(case, clock=lambda: clock[0])

    async def collect(*, inst_id):
        assert inst_id == "BTC-USDT-SWAP"
        return collected

    entry.collect_window = collect
    base = {
        **command(case),
        "schema_version": "perpetual-research-command.v3",
        "input_policy": POLICY,
    }
    prior_body = {**base, "request_id": "A-before-funding", "horizon": "24h"}
    prior_reply = client.post(
        BASE + "/research", json=prior_body, headers={"Idempotency-Key": prior_body["request_id"]}
    )
    assert prior_reply.status_code == 200, prior_reply.text
    prior = prior_reply.json()
    with case["db"].session() as session:
        prior_admission, _ = _read(session, prior["run_id"], ADMISSION_ROLE)
        prior_inputs = inputs.read_effective_inputs_in_session(
            session, prior_admission, purpose="frozen_read"
        )
        assert prior_inputs.effective_values.account_snapshot.equity is None

    async def funding_response(transport, request):
        if request.url.path == "/api/v5/public/time":
            rows = [{"ts": str(int(clock[0].timestamp() * 1000))}]
        else:
            assert request.url.path == "/api/v5/public/funding-rate-history"
            rows = []
        return httpx.Response(200, request=request, json={"code": "0", "data": rows})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", funding_response)
    manual.funding_history = PerpetualFundingHistorySource(case["db"], case["archive"])
    clock[0] += timedelta(seconds=2)
    result = executor.process_due()
    assert result["errors"] == [] and result["funding_reconciled"] == 1, result
    with case["db"].session() as session:
        settled = PerpetualPaperRepository(session).current(OWNER, partition.account_id)
        assert settled["funding_complete"] is True
        assert settled["revision"] != unvalued_ledger["revision"]
        assert DecisionSettingsService.current_in_session(session, OWNER, partition) == saved
        old = inputs.read_effective_inputs_in_session(
            session, prior_admission, purpose="frozen_read"
        )
        assert old == prior_inputs
        with pytest.raises(ValueError, match="account_original_changed"):
            inputs.read_effective_inputs_in_session(
                session, prior_admission, purpose="active_research"
            )
    assert client.get(BASE + "/research/commands/" + prior_body["request_id"]).json() == prior
    with monkeypatch.context() as patch:
        from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import (
            OKXPerpetualOriginalReader,
        )

        def unavailable(*args, **kwargs):
            raise OSError("controlled_archive_unavailable")

        patch.setattr(OKXPerpetualOriginalReader, "read_contract_in_session", unavailable)
        assert client.get(BASE + "/research/commands/" + prior_body["request_id"]).json() == prior

    for failure in ("after_binding", "after_admission", "after_verification"):
        body = {**base, "request_id": "A-rollback-" + failure}
        parsed = parse_perpetual_research_command(body)
        issued = entry.prepare(OWNER, parsed, collected)
        before = _business_counts(case)
        with monkeypatch.context() as patch:
            if failure == "after_binding":
                original = inputs.save_account_binding_in_session

                def fail(*args, _original=original, _failure=failure, **kwargs):
                    _original(*args, **kwargs)
                    raise ValueError("forced_" + _failure)

                patch.setattr(inputs, "save_account_binding_in_session", fail)
            elif failure == "after_admission":
                original = entry_module._save

                def fail(*args, _original=original, _failure=failure, **kwargs):
                    _original(*args, **kwargs)
                    raise ValueError("forced_" + _failure)

                patch.setattr(entry_module, "_save", fail)
            else:
                original = type(issued.inputs).verify_after_writes

                def fail(
                    self, *args, _original=original, _issued=issued, _failure=failure, **kwargs
                ):
                    _original(self, *args, **kwargs)
                    if self is _issued.inputs:
                        raise ValueError("forced_" + _failure)

                patch.setattr(type(issued.inputs), "verify_after_writes", fail)
            with pytest.raises(ValueError, match="forced_" + failure):
                entry.commit(OWNER, parsed, issued)
        assert _business_counts(case) == before
        assert entry.receipt(OWNER, body["request_id"]) is None

    race = parse_perpetual_research_command({**base, "request_id": "A-ledger-head-race"})
    issued = entry.prepare(OWNER, race, collected)
    key = account_key(OWNER, partition.account_id)
    with case["db"].session() as session:
        begin_immediate(session)
        head = session.get(PerpetualPaperHeadRecord, key)
        head.current_revision_id = unvalued_ledger["revision"]["ref"]
        head.state_version = unvalued_ledger["state_version"]
    before = _business_counts(case)
    with pytest.raises(ValueError, match="original_preparation.*changed"):
        entry.commit(OWNER, race, issued)
    assert _business_counts(case) == before
    with case["db"].session() as session:
        begin_immediate(session)
        head = session.get(PerpetualPaperHeadRecord, key)
        head.current_revision_id = settled["revision"]["ref"]
        head.state_version = settled["state_version"]

    body = {**base, "request_id": "A-after-real-settlement"}
    response = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["schema_version"] == "perpetual-research-receipt.v3"
    assert receipt["input_policy"] == POLICY and receipt["financial_qualified"] is False
    with case["db"].session() as session:
        admission, ref = _read(session, receipt["run_id"], ADMISSION_ROLE)
        effective = inputs.read_effective_inputs_in_session(
            session, admission, purpose="active_research"
        )
        assert ref == receipt["admission_ref"]
        assert admission["schema_version"] == "perpetual-research-admission.v2"
        assert admission["input_binding_ref"] == receipt["input_binding_ref"]
        assert effective.binding.ledger_revision.model_dump(mode="json") == settled["revision"]
        assert (
            effective.effective_values.account_snapshot.original
            == effective.binding.ledger_revision
        )
        assert effective.effective_values.account_snapshot.equity is not None
        old_policy = saved.revision.values.utility_policy.model_dump(mode="json")
        new_policy = effective.effective_values.utility_policy.model_dump(mode="json")
        dynamic = {"hash", "account_ref", "position_scenario"}
        assert {k: v for k, v in old_policy.items() if k not in dynamic} == {
            k: v for k, v in new_policy.items() if k not in dynamic
        }
        assert DecisionSettingsService.current_in_session(session, OWNER, partition) == saved
        rows = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == receipt["run_id"],
                RunRoleOutputRecord.role == inputs.ACCOUNT_BINDING_ROLE,
            )
        ).all()
        assert len(rows) == 1
        altered = copy.deepcopy(admission)
        altered["effective_values"]["account_snapshot"]["equity"] = "999999"
        with pytest.raises(ValueError, match="effective.*mismatch"):
            inputs.read_effective_inputs_in_session(session, altered, purpose="frozen_read")
    assert (
        client.post(
            BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
        ).json()
        == receipt
    )
    legacy = {key: value for key, value in body.items() if key != "input_policy"}
    legacy.update(schema_version="perpetual-research-command.v1", request_id="A-legacy-still-exact")
    rejected = client.post(
        BASE + "/research", json=legacy, headers={"Idempotency-Key": legacy["request_id"]}
    )
    assert (
        rejected.status_code == 409
        and rejected.json()["detail"] == "decision_settings_paper_original_mismatch"
    )
    closed_body = {**body, "request_id": "A-v3-close"}
    closed = entry.close_unaccepted(OWNER, closed_body)
    assert closed.command.model_dump(mode="json") == closed_body
    assert entry.receipt(OWNER, closed_body["request_id"]) == closed
    assert (
        json.loads(closed.model_dump_json())["schema_version"]
        == "perpetual-research-closure-receipt.v1"
    )
