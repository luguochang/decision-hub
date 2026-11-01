"""Recovered SWAP captures remain usable without erasing failed originals."""

import json
from dataclasses import asdict
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_condition_inputs import (
    read_perpetual_condition_inputs_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from packages.kernel.decision_hub_kernel.ports.perpetual_close import VERSION, millis, public_url
from tests.decision_product.test_okx_perpetual_original import INST, NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)


def test_successful_swap_capture_recovers_after_failed_captures_without_losing_audit(
    persisted_original,
):
    db, archive, source, metadata = persisted_original
    url = public_url(INST, millis(NOW))
    failed_raw = b'{"code":"500","msg":"temporarily unavailable"}'
    failed_hash, failed_ref = archive.put(failed_raw)
    data = [
        [str(millis(NOW) - 60000), "67000", "67000", "67000", "67000", "7", "1", "7", "1"]
    ]
    good_raw = json.dumps({"code": "0", "data": data}, separators=(",", ":")).encode()
    good_hash, good_ref = archive.put(good_raw)
    captures = [
        CaptureOriginal(
            "old_http_failure", f"{VERSION}:{INST}:{'1' * 32}:base:0", url,
            503, failed_ref, failed_hash, len(failed_raw), VERSION, None,
            NOW - timedelta(seconds=62), NOW - timedelta(seconds=61),
            NOW - timedelta(seconds=60),
        ),
        CaptureOriginal(
            "old_transport_failure", f"{VERSION}:{INST}:{'2' * 32}:base:0", url,
            None, None, None, 0, VERSION, "ReadTimeout",
            NOW - timedelta(seconds=32), NOW - timedelta(seconds=31),
            NOW - timedelta(seconds=30),
        ),
        CaptureOriginal(
            "recovered_success", f"{VERSION}:{INST}:{'3' * 32}:base:0", url,
            200, good_ref, good_hash, len(good_raw), VERSION, None,
            NOW, NOW + timedelta(milliseconds=10), NOW + timedelta(milliseconds=20),
        ),
    ]
    with db.session() as session:
        session.add_all([
            RawCaptureRecord(**asdict(capture), media_type="application/json")
            for capture in captures
        ])
    reference = source.metadata_contract_reference(metadata.capture_id)

    def read(session):
        return read_perpetual_condition_inputs_in_session(
            session,
            instrument_ref=reference,
            scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            as_of=NOW + timedelta(seconds=1),
            source=source,
        )

    with db.session() as session:
        result = read(session)
        assert [(bar.price, bar.close_at, bar.capture_id, bar.capture_hash)
                for bar in result.closes] == [("67000", NOW, "recovered_success", good_hash)]
        assert session.get(RawCaptureRecord, "old_http_failure").status_code == 503
        assert session.get(RawCaptureRecord, "old_transport_failure").error_code == "ReadTimeout"
        assert archive.read(failed_ref) == failed_raw

    # A successful status must still authenticate its raw original, not bypass parsing.
    path = archive.root / good_hash[:2] / good_hash
    path.write_bytes(good_raw.replace(b'67000', b'68000'))
    with db.session() as session, pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
        read(session)
