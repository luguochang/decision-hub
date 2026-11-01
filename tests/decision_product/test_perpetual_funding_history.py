"""RawCapture/PIT history originals, not actual account cash settlement."""

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


def original(fixture, *, realized="-0.00002", time=None, url=None):
    from packages.kernel.decision_hub_kernel.ports.perpetual_funding_history import (
        VERSION,
        funding_history_url,
    )

    db, archive, _, _ = fixture
    raw = json.dumps(
        {
            "code": "0",
            "data": [
                dict(
                    instId=INST,
                    instType="SWAP",
                    fundingTime=time or str(int((NOW - timedelta(hours=1)).timestamp() * 1000)),
                    fundingRate="0.99",
                    realizedRate=realized,
                    method="current_period",
                    formulaType="withRate",
                )
            ],
        }
    ).encode()
    digest, ref = archive.put(raw)
    item = CaptureOriginal(
        "settled-history",
        f"{VERSION}:{INST}:1" + "0" * 31,
        url or funding_history_url(INST),
        200,
        ref,
        digest,
        len(raw),
        VERSION,
        None,
        NOW,
        NOW,
        NOW,
    )
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(item), media_type="application/json"))
    return item


def read(fixture, item, cutoff=NOW):
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )

    db, archive, _, _ = fixture
    with db.session() as session:
        return PerpetualFundingHistorySource(db, archive).read_in_session(
            session, capture_id=item.capture_id, inst_id=INST, as_of=cutoff
        )


def test_actual_realized_signed_rate_stays_distinct_from_forecast_and_missing_cash_inputs(
    persisted_original,
):
    item = original(persisted_original)
    value = read(persisted_original, item)
    row = value.settlements[0]
    assert row.settled_rate == "-0.00002" and row.forecast_rate == "0.99"
    assert row.settlement_mark is None and row.cashflow_usdt is None
    assert value.coverage_complete is False and "settlement_mark_unavailable" in value.gaps
    assert read(persisted_original, item).reference == value.reference


def test_missing_realized_rate_is_unknown_not_current_rate_or_zero(persisted_original):
    value = read(persisted_original, original(persisted_original, realized=""))
    assert value.settlements[0].settled_rate is None
    assert "realized_rate_unavailable" in value.gaps


@pytest.mark.parametrize("fault", ["spot", "indication", "seconds", "future", "pit", "raw"])
def test_history_rejects_wrong_instrument_endpoint_units_time_or_changed_raw(
    persisted_original, fault
):
    from packages.kernel.decision_hub_kernel.ports.perpetual_funding_history import (
        funding_history_url,
    )

    kwargs = {}
    if fault == "spot":
        kwargs["url"] = funding_history_url(INST).replace(INST, "BTC-USDT")
    if fault == "indication":
        kwargs["url"] = funding_history_url(INST).replace("funding-rate-history", "funding-rate")
    if fault == "seconds":
        kwargs["time"] = str(int(NOW.timestamp()))
    if fault == "future":
        kwargs["time"] = str(int((NOW + timedelta(hours=1)).timestamp() * 1000))
    item = original(persisted_original, **kwargs)
    if fault == "raw":
        path = persisted_original[1].root / item.body_hash[:2] / item.body_hash
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        read(persisted_original, item, NOW - timedelta(seconds=1) if fault == "pit" else NOW)
