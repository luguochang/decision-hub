"""Explicit PAPER bridge; copied native/source originals are controlled offline evidence."""

import pytest


def test_event_manual_v2_keeps_exact_event_identity_and_rejects_caller_side():
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        EventManualPreparationCommand,
        parse_manual_preparation,
    )

    ref = dict(ref="original", hash="a" * 64)
    command = dict(
        schema_version="perpetual-manual-prepare.v2",
        request_id="event-prepare",
        partition=dict(
            account_kind="paper",
            account_id="paper",
            instrument_id="okx:BTC-USDT-SWAP",
            profile_ref="profile",
        ),
        settings_revision=ref,
        settings_state_version=1,
        revision=ref,
        horizon="4h",
        event_plan_ref=ref,
        selection_ref=ref,
        branch_ref=ref,
        event_review_ref=ref,
        event_publication_ref=ref,
    )
    actual = parse_manual_preparation(command)
    assert isinstance(actual, EventManualPreparationCommand)
    assert actual.model_dump(mode="json") == command
    with pytest.raises(ValueError):
        parse_manual_preparation({**command, "target_side": "long"})
    with pytest.raises(ValueError):
        parse_manual_preparation({**command, "schema_version": "perpetual-manual-prepare.v3"})


def test_manual_command_decoders_preserve_non_object_validation_errors():
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        parse_manual_display,
        parse_manual_execution,
        parse_manual_preparation,
    )

    for decode in (parse_manual_preparation, parse_manual_display, parse_manual_execution):
        for value in (None, [], 1):
            with pytest.raises(ValueError):
                decode(value)


@pytest.fixture
def published_event_copy(tmp_path, monkeypatch):
    """Reuse the parent's completed controlled proof; do not rerun its native chain."""
    import json
    import shutil
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import (
        OKXPerpetualSource,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    from packages.provider_adapters.research.raw_capture import RawBodyArchive

    packet = Path(__file__).parents[2] / "artifacts/d2-event-scenario-action-plans"
    handoff = json.loads((packet / "manual-source-handoff.json").read_text("utf-8"))
    assert handoff["source_kind"] == "controlled_offline_published_fixture"
    original_db = Path(handoff["database_path"])
    assert "pytest" in str(original_db).lower() and original_db.is_file()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "1")
    db_path = tmp_path / "manual-copy.sqlite3"
    with sqlite3.connect(original_db.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(db_path) as target:
            source.backup(target)
    shutil.copytree(handoff["raw_archive_path"], tmp_path / "raw")
    shutil.copytree(handoff["data_archive_path"], tmp_path / "data/raw-captures")
    db = Database("sqlite:///" + db_path.as_posix())
    archive = RawBodyArchive(tmp_path / "raw")
    source = OKXPerpetualSource(db, archive)
    advice = json.loads((packet / "task2-watch/advice-readback.json").read_text("utf-8"))["value"]
    clock = [
        datetime.fromisoformat(advice["event_scenario_plan"]["read_at"].replace("Z", "+00:00"))
    ]
    try:
        yield dict(
            db=db,
            source=source,
            archive=archive,
            handoff=handoff,
            advice=advice,
            clock=clock,
            packet=packet,
        )
    finally:
        db.engine.dispose()


def _prepare_display(case, *, suffix=""):
    from datetime import timedelta

    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    client, entry, _ = client_for(case, clock=lambda: case["clock"][0])
    event = case["advice"]["event_scenario_plan"]
    command = dict(
        schema_version="perpetual-manual-prepare.v2",
        request_id="event-prepare" + suffix,
        **case["handoff"]["settings_selection"],
        revision=event["selection_original"]["plan_revision_ref"],
        event_plan_ref=event["plan_ref"],
        selection_ref=event["selection_ref"],
        branch_ref=event["selection"]["branch_ref"],
        event_review_ref=event["review_authorization_ref"],
        event_publication_ref=event["publication_ref"],
    )
    command.pop("owner_id")

    def post(command):
        route = (
            "prepare"
            if "-prepare." in command["schema_version"]
            else ("display" if "-display." in command["schema_version"] else "intents")
        )
        return client.post(
            BASE + "/paper-execution/" + route,
            json=command,
            headers={"Idempotency-Key": command["request_id"]},
        )

    reply = post(command)
    assert reply.status_code == 200, reply.text
    prepared = reply.json()
    guide = client.get(BASE + "/paper-execution/input-guide/" + command["request_id"])
    assert guide.status_code == 200, guide.text
    assert guide.json()["target_side"] == "short"
    assert guide.json()["context"]["selection_ref"] == event["selection_ref"]
    case["clock"][0] += timedelta(seconds=3)
    shown_command = dict(
        **prepared["response"]["context"],
        schema_version="perpetual-manual-display.v2",
        request_id="event-display" + suffix,
    )
    shown = post(shown_command)
    assert shown.status_code == 200, shown.text
    policy = dict(
        max_entry_notional_usdt="80",
        stop_price_usdt="110",
        take_profit_price_usdt="90",
        exit_at=(case["clock"][0] + timedelta(seconds=60))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        simulation_acknowledged=True,
    )
    intent = dict(
        **prepared["response"]["context"],
        **policy,
        schema_version="perpetual-manual-intent-command.v2",
        request_id="event-intent" + suffix,
        display_receipt=dict(ref=shown.json()["request_id"], hash=shown.json()["receipt_hash"]),
    )
    return dict(
        client=client,
        entry=entry,
        post=post,
        prepare=command,
        prepared=prepared,
        guide=guide.json(),
        display=shown.json(),
        intent=intent,
    )


def _manual_service(case):
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        PerpetualPaperService,
    )

    paper = PerpetualPaperService(case["db"], case["source"], now=lambda: case["clock"][0])
    protocols = PerpetualPaperProtocolService(
        case["db"], case["source"], clock=lambda: case["clock"][0]
    )
    return PerpetualManualPaperService(case["db"], paper, protocols, clock=lambda: case["clock"][0])


def test_authenticated_b_short_normal_prepare_guide_display_intent_fill_and_stale_account(
    published_event_copy,
    monkeypatch,
):
    import json
    from datetime import datetime, timedelta
    from decimal import Decimal

    import httpx

    from packages.kernel.decision_hub_kernel.application import perpetual_paper
    from packages.kernel.decision_hub_kernel.application.action_execution import (
        ActionExecutionService,
    )
    from packages.provider_adapters.research import raw_capture

    case = published_event_copy
    flow = _prepare_display(case)
    # A second displayed intent genuinely binds the same pre-fill flat ledger.
    stale = _prepare_display(case, suffix="-stale")
    assert case["advice"]["revision"]["selected_candidate"]["target_side"] != "short"
    assert (
        datetime.fromisoformat(case["advice"]["revision"]["valid_until"].replace("Z", "+00:00"))
        < case["clock"][0]
    )
    queued = flow["post"](flow["intent"])
    assert queued.status_code == 200, queued.text
    original = queued.json()
    intent = original["response"]["intent"]
    assert intent["target_side"] == "short" and intent["candidate_id"] == "open_short"
    assert original["response"]["native_reviewed"] is False
    assert original["response"]["financial_qualified"] is False
    assert flow["post"](flow["intent"]).json() == original

    def wall():
        case["clock"][0] += timedelta(milliseconds=1)
        return case["clock"][0]

    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall()

    monkeypatch.setattr(raw_capture, "datetime", Wall)
    monkeypatch.setattr(perpetual_paper, "utcnow", wall)
    monkeypatch.setattr(
        perpetual_paper, "monotonic_ns", lambda: int(case["clock"][0].timestamp() * 1_000_000_000)
    )

    prices = ["99.9", "100.1"]

    async def captured(transport, request):
        row = dict(ts=str(int(case["clock"][0].timestamp() * 1000)))
        if request.url.path == "/api/v5/market/books":
            assert request.url.params["instId"] == case["handoff"]["settings_selection"][
                "partition"
            ]["instrument_id"].removeprefix("okx:")
            row.update(
                bids=[[prices[0], "200", "0", "1"]],
                asks=[[prices[1], "200", "0", "1"]],
                seqId=int(case["clock"][0].timestamp() * 1000),
            )
        else:
            assert request.url.path == "/api/v5/public/time"
        return httpx.Response(200, request=request, json=dict(code="0", data=[row]))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", captured)
    service = _manual_service(case)
    executor = ActionExecutionService(
        case["db"],
        market_store=None,
        feature_root=case["archive"].root,
        clock=lambda: case["clock"][0],
        enabled=True,
        perpetual_execution=service,
    )
    case["clock"][0] = datetime.fromisoformat(intent["eligible_at"]) + timedelta(seconds=2)
    result = executor.process_due()
    assert result["errors"] == [], result
    execution = service.read_execution(case["handoff"]["owner_id"], intent["id"])
    assert execution["receipt"]["status"] == "filled", execution
    assert execution["fill"]["order_side"] == "sell"
    assert Decimal(execution["fill"]["filled_contracts"]) > 0
    assert execution["episode"]["target_side"] == "short"
    assert service.execute_intent(intent["id"]) == execution["receipt"]
    changed = stale["post"](stale["intent"])
    assert changed.status_code == 409
    # The same V2 command continues through the existing short protective exit.
    prices[:] = ["89.8", "89.9"]
    case["clock"][0] += timedelta(seconds=6)
    assert executor.process_due()["errors"] == []
    case["clock"][0] += timedelta(seconds=6)
    assert executor.process_due()["errors"] == []
    protected = service.read_execution(case["handoff"]["owner_id"], intent["id"])
    assert protected["episode"]["remaining_contracts"] == "0"
    case["clock"][0] += timedelta(seconds=301)
    expired = flow["post"]({**flow["prepare"], "request_id": "event-after-expiry"})
    assert expired.status_code == 409
    (case["packet"] / "manual-wire-example.json").write_text(
        json.dumps(
            dict(
                source_kind="copied_controlled_offline_published_fixture",
                prepare=flow["prepare"],
                prepared=flow["prepared"],
                guide=flow["guide"],
                display=flow["display"],
                intent_command=flow["intent"],
                queued=original,
                execution=execution,
                protective_exit=protected,
                stale_account_status=changed.status_code,
                expired_status=expired.status_code,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_changed_authenticated_selection_withdraws_accepted_event_entry(published_event_copy):
    import json
    from dataclasses import asdict
    from datetime import datetime, timedelta

    from packages.kernel.decision_hub_kernel.application.event_scenario_publication import (
        read_published_event_scenario_plan_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.event_scenario_watch import (
        event_scenario_advice_view_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
    from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import (
        VERSION,
        millis,
        public_url,
    )

    case = published_event_copy
    flow = _prepare_display(case)
    changed = {
        **flow["intent"],
        "request_id": "event-wrong-selection",
        "selection_ref": {**flow["intent"]["selection_ref"], "hash": "0" * 64},
    }
    assert flow["post"](changed).status_code == 409
    event = case["advice"]["event_scenario_plan"]
    active_at = case["clock"][0]
    case["clock"][0] = datetime.fromisoformat(
        event["review_scope"]["valid_until"].replace("Z", "+00:00")
    )
    expired = flow["post"]({**flow["prepare"], "request_id": "event-expired-review-flat"})
    assert expired.status_code == 409 and expired.json()["detail"] == "perp_manual_event_not_active"
    case["clock"][0] = active_at
    # A newly confirmable minute will change the selection inside the intent's
    # unchanged 30-second quote deadline, via the existing actual source reader.
    case["clock"][0] += timedelta(seconds=40)
    reply = flow["post"](flow["intent"])
    assert reply.status_code == 200, reply.text
    intent = reply.json()["response"]["intent"]
    branch = next(
        row
        for row in event["plan"]["branches"]
        if row["branch_ref"] == event["selection"]["branch_ref"]
    )
    start = datetime.fromisoformat(branch["target"]["window_start"].replace("Z", "+00:00"))
    end = start + timedelta(minutes=3)
    raw = json.dumps(
        dict(
            code="0",
            data=[
                [
                    str(millis(end - timedelta(minutes=1))),
                    "120",
                    "120",
                    "120",
                    "120",
                    "7",
                    "0.07",
                    "8.4",
                    "1",
                ]
            ],
        ),
        separators=(",", ":"),
    ).encode()
    digest, body_ref = case["archive"].put(raw)
    capture = CaptureOriginal(
        "manual-event-changed-close",
        f"{VERSION}:BTC-USDT-SWAP:" + "a" * 32 + ":base:0",
        public_url("BTC-USDT-SWAP", millis(end)),
        200,
        body_ref,
        digest,
        len(raw),
        VERSION,
        None,
        end + timedelta(milliseconds=10),
        end + timedelta(milliseconds=20),
        end + timedelta(milliseconds=30),
    )
    with case["db"].session() as session:
        session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
    case["clock"][0] = end + timedelta(milliseconds=100)
    watch = PerpetualPlanFollowupService(case["db"], flow["entry"], now=lambda: case["clock"][0])
    watch.discover()
    with case["db"].session() as session:
        bundle = read_published_event_scenario_plan_in_session(
            session,
            run_id=case["handoff"]["run_id"],
            owner_id=case["handoff"]["owner_id"],
            at=case["clock"][0],
        )
        current = event_scenario_advice_view_in_session(session, bundle=bundle, at=case["clock"][0])
    assert current["selection_ref"] != event["selection_ref"]
    assert current["selection"]["status"] == "uncovered_requires_research"
    result = _manual_service(case).execute_intent(intent["id"])
    assert result["status"] == "no_fill"
    assert result["reason"] == "perp_manual_event_selection_changed"
    assert result["fill_id"] is None
    (case["packet"] / "manual-selection-change-wire.json").write_text(
        json.dumps(
            dict(
                source_kind="copied_controlled_offline_published_fixture",
                intent=reply.json(),
                previous_selection_ref=event["selection_ref"],
                current_selection_ref=current["selection_ref"],
                current_selection=current["selection"],
                quote_capture=asdict(capture),
                result=result,
                expired_flat_status=expired.status_code,
                expired_flat_detail=expired.json()["detail"],
            ),
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
