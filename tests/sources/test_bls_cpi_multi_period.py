"""Pure BLS batching equivalence and the existing native PIT boundary only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.kernel.decision_hub_kernel.application.event_response_path import _response_original
from packages.kernel.decision_hub_kernel.ports import bls_cpi


def _body(*, missing=False):
    series = []
    for offset, (sid, _adjustment, _window) in enumerate(bls_cpi.BLS_SERIES.values()):
        data = [
            {
                "year": str(year),
                "period": f"M{month:02}",
                "value": str(300 + (year - 2024) * 12 + month - 1 + offset * 10),
                "footnotes": [{"code": "P", "text": "fixture-original"}],
            }
            for year in (2024, 2025, 2026)
            for month in range(1, 13)
            if not (missing and offset == 1 and year == 2026 and month == 2)
        ]
        series.append({"seriesID": sid, "data": data})
    return json.dumps(
        {"status": "REQUEST_SUCCEEDED", "Results": {"series": series}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _fingerprint(batches):
    value = [[m.model_dump(mode="json") for m in batch] for batch in batches]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_multi_period_is_legacy_equivalent_one_index_and_preserves_missing_and_pit():
    periods = ("2026-01", "2026-02", "2026-03", "2026-01")
    received = datetime(2026, 6, 1, tzinfo=UTC)
    provenance = {
        "capture_id": "fixture-capture",
        "attempt_id": "fixture-attempt",
        "received_at": received,
        "request_startyear": 2024,
        "request_endyear": 2026,
    }
    # Goldens are produced from the frozen BEFORE scalar parser, not this new
    # helper, so a shared refactor bug cannot pass only by matching itself.
    goldens = {
        False: "dbeb8cda214ba291ff8ddb5ecd2b5054c0810fa4938a787bf5c20e38febc3b0e",
        True: "90f22b73976d523a4af125ff33f4dfd0796051bd892cc1f1a03a8edfcbe2fb12",
    }
    for missing in (False, True):
        body = _body(missing=missing)
        with patch.object(bls_cpi.json, "loads", wraps=json.loads) as loads:
            scalar = tuple(
                bls_cpi.parse_bls_cpi_metrics(body, reference_period=p, provenance=provenance)
                for p in periods
            )
            assert loads.call_count == len(periods)
        with patch.object(bls_cpi.json, "loads", wraps=json.loads) as loads:
            batches = bls_cpi.parse_bls_cpi_metric_batches(
                body, reference_periods=periods, provenance=provenance
            )
            assert loads.call_count == 1
        assert [[x.model_dump(mode="json") for x in batch] for batch in batches] == [
            [x.model_dump(mode="json") for x in batch] for batch in scalar
        ]
        assert _fingerprint(batches) == goldens[missing]
        assert all(len(batch) == 4 for batch in batches)
        first = batches[0][0]
        assert first.actual_value == (Decimal(324) / Decimal(323) - 1) * 100
        assert first.prior_value == (Decimal(323) / Decimal(322) - 1) * 100
        assert all(
            m.received_at == received
            and m.capture_id == "fixture-capture"
            and m.attempt_id == "fixture-attempt"
            and m.public_at is None
            and m.consensus is None
            for batch in batches
            for m in batch
        )
        if missing:
            core = batches[1][1]
            assert core.reference_period == "2026-02" and core.actual_value is None
            assert "actual_target_missing" in core.missing_reasons
        later = dict(
            provenance, capture_id="different-capture", received_at=received + timedelta(days=1)
        )
        rebuilt = bls_cpi.parse_bls_cpi_metric_batches(
            body, reference_periods=periods, provenance=later
        )
        assert rebuilt[0][0].capture_id == "different-capture"
        assert rebuilt[0][0].received_at == later["received_at"]
        assert rebuilt[0][0].actual_value == first.actual_value

    # Parsing has no PIT grant. The existing native caller still rejects a
    # future-known original instead of backdating it or using an older month.
    future = SimpleNamespace(known_at=received + timedelta(seconds=1))
    with pytest.raises(ValueError, match="cpi_release_history_original_clock_invalid"):
        _response_original(
            None,
            "fixture",
            archive=object(),
            cutoff=received,
            kind="response_release",
            reader=lambda *_a, **_k: future,
        )
    with pytest.raises(ValueError, match="bls_reference_period_invalid"):
        bls_cpi.parse_bls_cpi_metric_batches(
            _body(), reference_periods=("2026-13",), provenance=provenance
        )
