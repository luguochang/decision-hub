from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import Database, DataJobRecord


def test_owner_can_create_and_read_queued_data_job(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    payload = {
        "idempotency_key": "three-month-api-job",
        "requested_by": "owner",
        "dataset_ids": ["crypto.btc_usdt.ohlcv_1h", "macro.fred.dgs2"],
        "window_start_at": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
        "window_end_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
    }
    with TestClient(create_app(database, source_connectors=[])) as client:
        denied = client.post(
            "/v1/data-jobs",
            json=payload,
            headers={"Idempotency-Key": "three-month-api-job"},
        )
        assert denied.status_code == 403

        created = client.post(
            "/v1/data-jobs",
            json=payload,
            headers={"Idempotency-Key": "three-month-api-job", "X-Owner-Id": "owner"},
        )
        assert created.status_code == 202
        assert created.json()["status"] == "queued"
        job_id = created.json()["job_id"]

        same = client.post(
            "/v1/data-jobs",
            json=payload,
            headers={"Idempotency-Key": "three-month-api-job", "X-Owner-Id": "owner"},
        )
        assert same.status_code == 202
        assert same.json()["job_id"] == job_id

        detail = client.get(f"/v1/data-jobs/{job_id}")
        listing = client.get("/v1/data-jobs")
        assert detail.status_code == 200
        assert listing.json() == [detail.json()]


def test_data_job_paging_filters_all_history_and_preserves_cutoff(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'paging.sqlite3'}")
    now = datetime(2026, 9, 30, tzinfo=UTC)
    with TestClient(create_app(database, source_connectors=[])) as client:
        with database.session() as session:
            for index in range(503):
                session.add(
                    DataJobRecord(
                        job_id=f"datajob_{index:032x}",
                        idempotency_key=f"paging-fixture-{index}",
                        request_hash="a" * 64,
                        requested_by="owner",
                        dataset_ids_json='["macro.fred.dgs2"]'
                        if index == 501
                        else '["crypto.btc_usdt.ohlcv_1h"]',
                        window_start_at=now - timedelta(days=1),
                        window_end_at=now,
                        status="failed" if index == 501 else "completed",
                        created_at=now,
                        updated_at=now,
                    )
                )
        first = client.get("/v1/data-jobs", params={"limit": 500})
        assert len(first.json()) == 500
        assert first.headers["X-Total-Count"] == "503"
        page = client.get("/v1/data-jobs", params={"limit": 20, "offset": 500})
        assert [row["job_id"] for row in page.json()] == [
            f"datajob_{i:032x}" for i in range(500, 503)
        ]
        filtered = client.get(
            "/v1/data-jobs", params={"status": "failed", "dataset": "macro.fred.dgs2"}
        )
        assert filtered.headers["X-Total-Count"] == "1"
        assert filtered.json()[0]["job_id"] == f"datajob_{501:032x}"
        for dataset in ("macro.fred.dgs", "%", "macro_fred_dgs2"):
            assert client.get("/v1/data-jobs", params={"dataset": dataset}).json() == []
        assert (
            client.get(
                "/v1/data-jobs", params={"created_before": (now - timedelta(seconds=1)).isoformat()}
            ).json()
            == []
        )
        assert (
            client.get("/v1/data-jobs", params={"created_before": now.isoformat()}).headers[
                "X-Total-Count"
            ]
            == "503"
        )
        assert (
            client.get(
                "/v1/data-jobs", params={"created_before": "2026-09-30T00:00:00"}
            ).status_code
            == 422
        )
        assert client.get("/v1/data-jobs", params={"status": "made-up"}).status_code == 422
        assert client.get("/v1/data-jobs", params={"offset": -1}).status_code == 422
