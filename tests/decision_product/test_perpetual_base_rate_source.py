"""1H history originals -> frozen base rate, on a real temporary archive/SQLite; no network."""

import json
from dataclasses import asdict
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from tests.decision_product.test_okx_perpetual_original import INST, NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

HOUR_MS = 3_600_000
THETA = "0.001"


def hourly_pages(fixture, *, fault=None, hours=24 * 95):
    """Newest-first pages of 100 confirmed 1H bars ending at NOW, alternating 100/101/100/99."""
    from packages.kernel.decision_hub_kernel.ports.perpetual_hourly_history import (
        VERSION,
        public_url,
    )

    db, archive, _, _ = fixture
    end_ms = int(NOW.timestamp() * 1000)
    opens = [end_ms - HOUR_MS * index for index in range(1, hours + 1)]
    if fault == "gap":
        del opens[100:400]
    pattern = ("100", "101", "100", "99")
    after, ids = end_ms, []
    for page in range(0, len(opens), 100):
        selected = opens[page : page + 100]
        data = []
        for stamp in selected:
            price = pattern[((end_ms - stamp) // HOUR_MS) % 4]
            confirmed = "0" if fault == "unconfirmed" and page == 0 else "1"
            data.append([str(stamp), price, price, price, price, "7", "0.07", "7", confirmed])
        raw = json.dumps({"code": "0", "data": data}, separators=(",", ":")).encode()
        digest, body_ref = archive.put(raw)
        sequence = page // 100
        at = NOW + timedelta(milliseconds=100 * (1 + sequence))
        original = CaptureOriginal(
            f"hourly_{sequence}",
            f"{VERSION}:{INST}:{'2' * 32}:{sequence}",
            public_url(INST, after + (HOUR_MS if fault == "cursor" and sequence == 3 else 0)),
            200,
            body_ref,
            digest,
            len(raw),
            VERSION,
            None,
            at,
            at + timedelta(milliseconds=10),
            at + timedelta(milliseconds=20),
        )
        with db.session() as session:
            session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
        ids.append(original.capture_id)
        after = selected[-1]
    if fault == "raw":
        target = archive.root / digest[:2] / digest
        target.write_bytes(raw + b" ")
    return tuple(ids)


def read(fixture, ids, *, as_of=NOW + timedelta(minutes=1)):
    from packages.kernel.decision_hub_kernel.application.perpetual_base_rate_source import (
        read_direction_base_rate_in_session,
    )

    db, archive, _, _ = fixture
    with db.session() as session:
        return read_direction_base_rate_in_session(
            session,
            archive,
            inst_id=INST,
            capture_ids=ids,
            as_of=as_of,
            threshold_fraction=THETA,
            horizon_seconds=14400,
        )


def test_hourly_originals_reproduce_deterministic_base_rate(persisted_original):
    ids = hourly_pages(persisted_original)
    first = read(persisted_original, ids)
    assert first["status"] == "available"
    assert first["window_90d"]["status"] == first["window_30d"]["status"] == "available"
    # The 4-bar cycle returns to the same price after 4h: every 4h return is neutral.
    assert first["window_90d"]["counts"] == {
        "up": 0, "neutral": first["window_90d"]["samples"], "down": 0
    }
    assert first["capture_ids"] == list(ids)
    assert first["conditional_information"] is False
    assert read(persisted_original, ids) == first


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("unconfirmed", "perp_hourly_confirmed_row_required"),
        ("cursor", "perp_hourly_cursor_chain_invalid"),
        ("gap", "closes_missing"),
    ],
)
def test_invalid_or_incomplete_originals_are_explicitly_unavailable(
    persisted_original, fault, reason
):
    result = read(persisted_original, hourly_pages(persisted_original, fault=fault))
    assert result["status"] == "unavailable"
    assert result["reason"] == reason


def test_substituted_raw_bytes_are_unavailable_never_a_rate(persisted_original):
    ids = hourly_pages(persisted_original, fault="raw")
    result = read(persisted_original, ids)
    assert (result["status"], result["reason"]) == ("unavailable", "raw_capture_archive_corrupt")
    assert "window_90d" not in result


def test_capture_recorded_after_cutoff_is_not_point_in_time(persisted_original):
    ids = hourly_pages(persisted_original)
    result = read(persisted_original, ids, as_of=NOW)
    assert result["status"] == "unavailable"
    assert result["reason"] == "perp_hourly_capture_clock_invalid"


def test_no_collection_is_unavailable_not_an_error(persisted_original):
    result = read(persisted_original, ())
    assert (result["status"], result["reason"]) == ("unavailable", "hourly_history_not_collected")


def test_request_url_is_exact_public_hourly_endpoint():
    from packages.kernel.decision_hub_kernel.ports.perpetual_hourly_history import (
        public_url,
        request_cursor,
    )

    url = public_url(INST, int(NOW.timestamp() * 1000))
    assert "bar=1H" in url and "limit=100" in url
    assert request_cursor(url, INST) == int(NOW.timestamp() * 1000)
    with pytest.raises(ValueError):
        public_url("BTC-USDT", int(NOW.timestamp() * 1000))
    with pytest.raises(ValueError):
        request_cursor(url.replace("bar=1H", "bar=1m"), INST)


def test_signed_entry_freezes_base_rate_and_rejects_changed_original(binding_case):
    from packages.kernel.decision_hub_kernel.application.perpetual_base_rate_source import (
        CollectedHourlyHistory,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        _authenticate_admission,
        read_perpetual_admission_identity_in_session,
    )
    from tests.decision_product.test_perpetual_research_binding import AT
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

    case = binding_case
    fixture = (case["db"], case["source"].archive, case["source"], None)
    ids = hourly_pages(fixture)
    client, service, _ = client_for(case)
    calls = []

    async def history(*, inst_id):
        calls.append(inst_id)
        return CollectedHourlyHistory(ids, AT)

    service.collect_history = history
    body = command(case)
    reply = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    assert calls == [INST]
    with case["db"].session() as session:
        admission, _ = read_perpetual_admission_identity_in_session(session, reply.json()["run_id"])
        frozen = admission["direction_base_rate"]
        assert frozen["status"] == "available", frozen
        assert frozen["capture_ids"] == list(ids)
        assert frozen["horizon_seconds"] == 14400
        threshold = next(
            row["fraction"] for row in admission["close_window"]["thresholds"]
            if row["horizon"] == "4h"
        )
        assert frozen["threshold_fraction"] == threshold
        _authenticate_admission(session, admission, purpose="frozen_read")
        changed = json.loads(json.dumps(admission))
        changed["direction_base_rate"]["window_90d"]["counts"]["up"] += 1
        with pytest.raises(ValueError, match="perpetual_research_base_rate_original_changed"):
            _authenticate_admission(session, changed, purpose="frozen_read")


def test_injected_close_collector_never_collects_public_history(binding_case):
    import asyncio

    from tests.decision_product.test_perpetual_research_entry import client_for

    _, service, _ = client_for(binding_case)
    assert asyncio.run(service.collect_history(inst_id=INST)) is None


@pytest.mark.parametrize(
    ("frozen", "skill_present", "reason"),
    [
        (None, None, None),
        ({"status": "unavailable"}, False, "base_rate_unavailable"),
        ("available", True, None),
    ],
)
def test_scoring_view_compares_against_frozen_base_rate(monkeypatch, frozen, skill_present, reason):
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_forecast_scoring_view as view,
    )
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_research_binding as binding,
    )

    if frozen == "available":
        frozen = {"status": "available", "window_90d": {"status": "available", "probabilities": [
            {"outcome_id": "up", "value": "0.34"},
            {"outcome_id": "neutral", "value": "0.33"},
            {"outcome_id": "down", "value": "0.33"},
        ]}}
    admission = {} if frozen is None else {"direction_base_rate": frozen}
    monkeypatch.setattr(
        binding, "read_perpetual_admission_identity_in_session", lambda _s, _r: (admission, {})
    )
    body = {"run_id": "r", "category": "neutral", "direction_estimate": {"probabilities": [
        {"outcome_id": "up", "value": "0.4"},
        {"outcome_id": "neutral", "value": "0.2"},
        {"outcome_id": "down", "value": "0.4"},
    ]}}
    result = view._base_rate_comparison(None, body)
    if frozen is None:
        assert result is None
        return
    assert result["reason"] == reason
    assert (result["brier_skill"] is not None) is skill_present
    if skill_present:
        # 40/20/40 scored on a neutral outcome is worse than the base rate.
        assert result["brier_skill"].startswith("-")
