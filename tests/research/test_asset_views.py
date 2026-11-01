from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from packages.kernel.decision_hub_kernel.persistence.db import (
    RawCaptureRecord,
    SourcePollAttemptRecord,
)
from packages.query_views.studies import direction_changes
from tests.research.test_study_identity import NOW, setup


def client_for(db, root):
    app = FastAPI()
    app.include_router(asset_router(db, root, clock=lambda: NOW))
    return TestClient(app)


def test_report_direction_comparison_keeps_method_scope_and_missing_versions_distinct():
    from types import SimpleNamespace

    def outlook(action, method="v1", instrument="BTC-USDT"):
        return SimpleNamespace(
            method_family="ai_synthesis",
            method_version=method,
            instrument=instrument,
            horizon="24h",
            action=action,
        )

    changed = direction_changes([outlook("long")], [outlook("short")], has_previous=True)
    assert changed[0]["changed"] is True
    assert changed[0]["comparison_status"] == "compared"
    new_method = direction_changes([outlook("long", "v2")], [outlook("long")], has_previous=True)
    assert (
        new_method[0]["changed"] is None and new_method[0]["comparison_status"] == "method_changed"
    )
    other_instrument = direction_changes(
        [outlook("long", instrument="XAU-USD")], [outlook("long")], has_previous=True
    )
    assert {r["comparison_status"] for r in other_instrument} == {
        "missing_previous",
        "missing_current",
    }
    assert (
        direction_changes([outlook("long")], [], has_previous=False)[0]["comparison_status"]
        == "first_recorded"
    )


def test_history_counts_whole_window_and_excludes_future_and_older_records(tmp_path):
    db = setup(tmp_path)
    with db.session() as session:
        for number, (status, when) in enumerate(
            [
                ("succeeded", NOW),
                ("empty", NOW),
                ("failed", NOW),
                ("failed", NOW - timedelta(days=14)),
                ("succeeded", NOW + timedelta(seconds=1)),
            ]
        ):
            session.add(
                SourcePollAttemptRecord(
                    attempt_id=f"a{number}",
                    source_id="fed",
                    trigger_type="scheduled",
                    status=status,
                    started_at=when,
                    completed_at=when,
                    accepted_count=number,
                    received_count=number,
                    duplicate_count=0,
                    revision_count=0,
                )
            )
    client = client_for(db, tmp_path)
    data = client.get("/v1/sources/history?days=14").json()
    assert data["timezone"] == "UTC" and data["start_at"].endswith("00:00:00Z")
    assert {r["status"]: r["attempts"] for r in data["items"]} == {
        "succeeded": 1,
        "empty": 1,
        "failed": 1,
    }
    assert client.get("/v1/sources/history?days=32").status_code == 422


def test_poll_raw_files_are_bound_to_exact_attempt_and_old_absence_is_empty(tmp_path):
    db = setup(tmp_path)
    with db.session() as session:
        session.add(
            SourcePollAttemptRecord(
                attempt_id="a1",
                source_id="fed",
                trigger_type="manual",
                status="failed",
                started_at=NOW,
            )
        )
        session.add(
            SourcePollAttemptRecord(
                attempt_id="old",
                source_id="fed",
                trigger_type="manual",
                status="empty",
                started_at=NOW,
            )
        )
        for name, context in [("correct", "source:a1"), ("other", "source:a11")]:
            session.add(
                RawCaptureRecord(
                    capture_id=name,
                    context_id=context,
                    url="https://example.test/feed",
                    status_code=403,
                    body_hash="a" * 64,
                    body_ref="sha256/" + "a" * 64,
                    byte_count=20,
                    media_type="text/html",
                    adapter_version="v1",
                    started_at=NOW,
                    completed_at=NOW,
                    recorded_at=NOW,
                )
            )
    client = client_for(db, tmp_path)
    data = client.get("/v1/source-poll-attempts/a1/captures").json()
    assert [r["capture_id"] for r in data] == ["correct"]
    assert data[0]["status_code"] == 403
    assert client.get("/v1/source-poll-attempts/old/captures").json() == []
    assert client.get("/v1/source-poll-attempts/missing/captures").status_code == 404


@pytest.mark.asyncio
async def test_job_coverage_checks_frozen_partitions_and_rejects_corruption(tmp_path):
    from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
    from packages.provider_adapters.historical import (
        HistoricalDataArchive,
        HistoricalDataJobRunner,
        HistoricalDatasetBatch,
    )
    from tests.historical.test_data_jobs import _Loader, _request

    db = setup(tmp_path)
    jobs = DataJobService(db, clock=lambda: NOW)
    job, _ = jobs.create(_request())
    client = client_for(db, tmp_path)
    assert client.get(f"/v1/data-jobs/{job.job_id}/coverage").status_code == 404
    archive = HistoricalDataArchive(tmp_path / "historical")
    request = _request()
    runner = HistoricalDataJobRunner(
        jobs,
        archive,
        {
            "crypto.btc_usdt.ohlcv_1h": _Loader(
                HistoricalDatasetBatch(
                    dataset_id="crypto.btc_usdt.ohlcv_1h",
                    source_id="test-market",
                    rows=[{"timestamp": request.window_start_at.isoformat(), "close": "123"}],
                    unit="usdt",
                    frequency="1h",
                    instrument="BTC-USDT",
                    venue="test",
                    contract_type="spot",
                    publication_time_precision="second",
                    revision_policy="immutable",
                    gaps=["missing_hours"],
                )
            ),
        },
        clock=lambda: NOW,
    )
    completed = await runner.run(job.job_id)
    result = client.get(f"/v1/data-jobs/{job.job_id}/coverage")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["verified"] is True
    assert data["payload"]["datasets"][0]["row_count"] == 1
    assert (
        client.get(f"/v1/data-jobs/{job.job_id}/coverage?partition=0").json()["payload"]["rows"][0][
            "close"
        ]
        == "123"
    )
    assert client.get(f"/v1/data-jobs/{job.job_id}/coverage?partition=99").status_code == 404
    digest = completed.partition_refs[0].rsplit("/", 1)[-1]
    (archive.partitions / f"{digest}.json").write_text("{}")
    assert client.get(f"/v1/data-jobs/{job.job_id}/coverage").status_code == 409
