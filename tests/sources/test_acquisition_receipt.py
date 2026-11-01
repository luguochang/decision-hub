from __future__ import annotations

from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts import SourceAcquisition


def test_source_acquisition_receipt_is_content_hash_addressable():
    receipt = SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "receipt-1",
            "source_id": "fixture-public-page",
            "url": "https://example.test/page",
            "access_mode": "playwright",
            "status": "succeeded",
            "observed_at": datetime.now(UTC).isoformat(),
            "status_code": 200,
            "artifacts": [
                {
                    "kind": "dom",
                    "path": "dom.html",
                    "content_hash": "a" * 64,
                },
                {
                    "kind": "trace",
                    "path": "trace.zip",
                    "content_hash": "b" * 64,
                },
            ],
            "error_code": None,
        }
    )
    assert receipt.access_mode == "playwright"
    assert len(receipt.artifacts) == 2
