"""Archived synthetic venue path and pure publication: engineering, not financial validation."""

import importlib
import importlib.util
import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_forecast_experience"


def test_forecast_experience_service_is_installed():
    assert importlib.util.find_spec(MODULE), (
        "saved perpetual target has no experience producer or next-Task reader"
    )


def future_pages(case, end, *, prefix, count=241, missing=False, price="110"):
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
    from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import (
        VERSION,
        millis,
        public_url,
    )

    stamps = [end - timedelta(minutes=index) for index in range(count)]
    if missing:
        stamps.pop(90)
    cursor = millis(end)
    ids = []
    for page in range((len(stamps) + 299) // 300):
        selected = stamps[page * 300 : (page + 1) * 300]
        rows = []
        for close in selected:
            value = "100" if close == end - timedelta(minutes=count - 1) else price
            if count > 300 and (end - close).total_seconds() % 120 == 0:
                value = "100"
            rows.append(
                [str(millis(close) - 60000), value, value, value, value, "7", "0.07", "7", "1"]
            )
        raw = json.dumps({"code": "0", "data": rows}, separators=(",", ":")).encode()
        digest, locator = case["archive"].put(raw)
        at = end + timedelta(seconds=1 + page)
        capture = CaptureOriginal(
            prefix + str(page),
            f"{VERSION}:BTC-USDT-SWAP:{'2' * 32}:base:{page}",
            public_url("BTC-USDT-SWAP", cursor),
            200,
            locator,
            digest,
            len(raw),
            VERSION,
            None,
            at,
            at + timedelta(milliseconds=10),
            at + timedelta(milliseconds=20),
        )
        with case["db"].session() as session:
            session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
        ids.append(capture.capture_id)
        cursor = int(rows[-1][0])
    return tuple(ids)


def test_saved_4h_target_mature_archive_experience_and_next_task(
    binding_case, tmp_path, monkeypatch
):
    module = importlib.import_module(MODULE)
    from packages.contracts_py.decision_hub_contracts import ObservationCreate
    from packages.contracts_py.decision_hub_contracts.models import SourceType
    from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        PerpetualResearchBindingService,
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from tests.decision_product import test_perpetual_action_publication as publication_fixture
    from tests.decision_product.test_perpetual_advice_query import persisted_publication
    from tests.decision_product.test_perpetual_research_binding import AT, OWNER, factory_for

    case = binding_case
    packet_builder = publication_fixture.packet_for

    def synthetic_catalog_packet(request, materials):
        # Only the old pure derive fixture needs this authored catalog projection.
        # It is never passed to the real authenticated review-materials builder.
        from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

        catalog = {
            "originals": [
                {
                    "source_kind": "server_material",
                    "role": "close_window",
                    "source_ref": {
                        "ref": "synthetic-pure-close-window",
                        "hash": canonical_hash(materials["close_window"]),
                    },
                }
            ]
        }
        return packet_builder(request, {**materials, "review_originals": catalog})

    monkeypatch.setattr(publication_fixture, "packet_for", synthetic_catalog_packet)
    derived = persisted_publication(case)
    target = next(item for item in derived.originals if item.role == "final_target").body_json
    from datetime import datetime

    end = datetime.fromisoformat(json.loads(target)["window_end"].replace("Z", "+00:00"))
    ids = future_pages(case, end, prefix="future_")
    now = end + timedelta(seconds=10)
    service = module.PerpetualForecastExperienceService(
        case["db"], case["source"], clock=lambda: now
    )
    original = service.record(run_id=case["run_id"], capture_ids=ids)
    assert original.body["status"] == "observed"
    assert original.body["category"] == "neutral"  # actual fixture has frozen high-volatility theta
    assert Decimal(original.body["return_fraction"]) == Decimal("0.1")
    from packages.kernel.decision_hub_kernel.ports.perpetual_forecast_experience import (
        classify_forecast_return,
        score_direction,
    )

    assert classify_forecast_return("100", "110", "0.1")[1] == "neutral"
    assert (
        score_direction(
            [
                {"outcome_id": "up", "value": "0.7"},
                {"outcome_id": "neutral", "value": "0.2"},
                {"outcome_id": "down", "value": "0.1"},
            ],
            "neutral",
        )
        == "1.14"
    )
    assert len(original.body["closes"]) == 241
    assert original.body["financial_qualified"] is False
    assert "net_win_rate" not in original.body and "account_snapshot" not in original.body
    assert service.record(run_id=case["run_id"], capture_ids=ids) == original
    with case["db"].session() as session:
        assert service.read_in_session(session, original.ref, cutoff_at=now) == original
        assert (
            module.perpetual_experience_context_in_session(
                session,
                owner_id=OWNER,
                instrument_id="okx:ETH-USDT-SWAP",
                horizon="4h",
                cutoff_at=now,
            )
            == ()
        )
        assert (
            module.perpetual_experience_context_in_session(
                session,
                owner_id="another-owner",
                instrument_id="okx:BTC-USDT-SWAP",
                horizon="4h",
                cutoff_at=now,
            )
            == ()
        )
        assert (
            module.perpetual_experience_context_in_session(
                session,
                owner_id=OWNER,
                instrument_id="okx:BTC-USDT-SWAP",
                horizon="24h",
                cutoff_at=now,
            )
            == ()
        )
        assert (
            module.perpetual_experience_context_in_session(
                session,
                owner_id=OWNER,
                instrument_id="okx:BTC-USDT-SWAP",
                horizon="4h",
                cutoff_at=AT,
            )
            == ()
        )

    # An independent next actual Factory Task, not a model/Graph or skill-consumed claim.
    history = future_pages(case, end, prefix="next_history_", count=1500)
    event_id, _, accepted = AdmissionService(case["db"], clock=lambda: now).admit(
        ObservationCreate(
            source_id="next-experience-task",
            source_type=SourceType.transcript,
            source_url="https://example.com/next-experience-task",
            text="Next Federal Reserve research question.",
            observed_at=now,
            published_at=now,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert accepted
    run_id, _ = RunService(case["db"], clock=lambda: now).create(
        event_id, strategy_version="research.v1"
    )
    with case["db"].session() as session:
        session.get(RunRecord, run_id).study_id = session.get(RunRecord, case["run_id"]).study_id
    binding = PerpetualResearchBindingService(case["db"], case["source"], now=lambda: now)
    binding.freeze_admission(
        run_id=run_id,
        owner_id=OWNER,
        partition=case["settings"].partition,
        capture_ids=history,
        as_of=now,
        valid_until=now + timedelta(hours=1),
    )
    next_case = {**case, "event_id": event_id, "run_id": run_id}
    factory, _ = factory_for(next_case, tmp_path / "next-pack")
    factory.clock = lambda: now
    factory.perpetual_binding_service = binding
    request = factory.build(event_id, run_id)
    selected = [
        item
        for item in request.input_evidence
        if item.source_id == "perpetual_forecast_experience.v1"
    ]
    assert len(selected) == 1 and selected[0].content_hash == original.ref.hash
    assert selected[0].evidence_id in request.context_manifest.effective_inputs
    assert "不是交易收益" in selected[0].excerpt
    assert factory.build(event_id, run_id) == request

    # A new saved target cannot label an incomplete exact minute window.
    incomplete = future_pages(case, end, prefix="missing_", missing=True)
    with pytest.raises(ValueError, match="exact_minute_missing"):
        service.prepare(run_id=case["run_id"], capture_ids=incomplete)
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

    with case["db"].session() as session:
        row = session.get(RawCaptureRecord, ids[0])
        digest = row.body_hash
    raw_path = case["archive"].root / digest[:2] / digest
    raw_path.write_bytes(raw_path.read_bytes() + b" ")
    with (
        case["db"].session() as session,
        pytest.raises(ValueError, match="capture_invalid|hash_mismatch|raw_capture_archive_corrupt"),
    ):
        service.read_in_session(session, original.ref, cutoff_at=now)
