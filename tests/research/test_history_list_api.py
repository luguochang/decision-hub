from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    MacroEventRecord,
    ObservationRecord,
    RunRecord,
)
from tests.evolution.test_research_worker import NOW, _admitted_run


def test_research_runs_can_traverse_more_than_500_and_filter_visible_fields(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, event_id, first_run_id = _admitted_run(tmp_path)
    with database.session() as session:
        first = session.get(RunRecord, first_run_id)
        assert first is not None
        first.status = "running"
        session.add(
            ObservationRecord(
                observation_id="observation-other",
                event_id="event-other",
                source_id="other-source",
                source_type="transcript",
                observed_at=NOW,
                received_at=NOW,
                language="en",
                text="Unrelated policy update",
                content_hash="a" * 64,
            )
        )
        session.add_all(
            RunRecord(
                run_id=f"run-page-{index:04d}",
                event_id=event_id,
                status="completed",
                strategy_version="research.v1",
                admission_origin="automatic",
                created_at=NOW,
                updated_at=NOW,
            )
            for index in range(505)
        )
        session.add(
            RunRecord(
                run_id="run-other-source",
                event_id="event-other",
                status="failed",
                strategy_version="research.v1",
                admission_origin="manual",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            RunRecord(
                run_id="run-baseline-ignored",
                event_id=event_id,
                status="completed",
                strategy_version="baseline.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with TestClient(create_app(database, source_connectors=[])) as client:
        first_page = client.get("/v1/research/runs", params={"limit": 500})
        cursor = first_page.headers["X-Next-Cursor"]
        second_page = client.get(
            "/v1/research/runs", params={"limit": 500, "cursor": cursor}
        )
        offset_page = client.get(
            "/v1/research/runs", params={"limit": 20, "offset": 500}
        )
        researching = client.get(
            "/v1/research/runs", params={"status": "researching"}
        )
        source = client.get(
            "/v1/research/runs",
            params={"source_id": "other-source", "origin": "manual"},
        )
        searched = client.get(
            "/v1/research/runs", params={"q": "elevated inflation risks"}
        )
        invalid = client.get(
            "/v1/research/runs", params={"cursor": cursor, "offset": 1}
        )
        mismatched = client.get(
            "/v1/research/runs", params={"cursor": cursor, "status": "completed"}
        )

    assert first_page.status_code == 200
    assert first_page.headers["X-Total-Count"] == "507"
    assert first_page.headers["X-Has-More"] == "true"
    assert len(first_page.json()) == 500
    assert second_page.status_code == 200
    assert second_page.headers["X-Has-More"] == "false"
    assert len(second_page.json()) == 7
    all_ids = [item["run_id"] for item in first_page.json() + second_page.json()]
    assert len(all_ids) == len(set(all_ids)) == 507
    assert "run-baseline-ignored" not in all_ids
    assert [item["run_id"] for item in offset_page.json()] == all_ids[500:]
    assert [item["run_id"] for item in researching.json()] == [first_run_id]
    assert [item["run_id"] for item in source.json()] == ["run-other-source"]
    assert searched.headers["X-Total-Count"] == "506"
    assert invalid.status_code == mismatched.status_code == 400


def test_macro_event_search_count_and_stable_detail_endpoint(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'macro-history.sqlite3'}")
    database.create_all()
    now = datetime.now(UTC)
    with database.session() as session:
        session.add_all(
            MacroEventRecord(
                macro_event_id=f"macro-{index}",
                event_id=f"event-{index}",
                external_event_id=f"calendar-cpi-{index}",
                family="cpi" if index < 3 else "fomc",
                scheduled_at=now - timedelta(days=index + 1),
                source_id="official-calendar",
                status="released",
                created_at=now,
                updated_at=now,
            )
            for index in range(4)
        )

    with TestClient(create_app(database, source_connectors=[])) as client:
        default = client.get("/v1/macro-events")
        filtered = client.get(
            "/v1/macro-events",
            params={
                "scope": "history",
                "family": "cpi",
                "q": "calendar-cpi",
                "limit": 1,
                "offset": 1,
            },
        )
        direct = client.get("/v1/macro-events/macro-0")
        missing = client.get("/v1/macro-events/missing")
        chinese = client.get(
            "/v1/macro-events", params={"q": "消费者物价", "limit": 1, "offset": 1}
        )
        fed = client.get("/v1/macro-events", params={"q": "美联储"})
        no_match = client.get("/v1/macro-events", params={"q": "不存在的事件名称"})

    assert chinese.headers["X-Total-Count"] == "3"
    assert [item["macro_event_id"] for item in chinese.json()] == ["macro-1"]
    assert [item["macro_event_id"] for item in fed.json()] == ["macro-3"]
    assert no_match.json() == []
    assert default.status_code == 200
    assert default.headers["X-Total-Count"] == "4"
    assert [item["macro_event_id"] for item in default.json()] == [
        "macro-0", "macro-1", "macro-2", "macro-3"
    ]
    assert filtered.status_code == 200
    assert filtered.headers["X-Total-Count"] == "3"
    assert [item["macro_event_id"] for item in filtered.json()] == ["macro-1"]
    assert direct.status_code == 200
    assert direct.json()["macro_event_id"] == "macro-0"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "macro_event_not_found"


def test_home_selects_latest_saved_recheck_beyond_busy_first_page(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, event_id, first_run = _admitted_run(tmp_path)
    with database.session() as session:
        session.get(RunRecord, first_run).status = "failed"
        session.get(RunRecord, first_run).updated_at = NOW
        for index in range(130):
            session.add(RunRecord(
                run_id=f"busy-{index:03}", event_id=event_id, status="queued",
                strategy_version="research.v1", admission_origin="automatic",
                created_at=NOW, updated_at=NOW + timedelta(hours=2),
            ))
        for run_id, origin, version, hour in [
            ("manual-report", "manual", "research.v1", 0),
            ("automatic-report", "scheduled_recheck", "research.v1", 1),
            ("baseline-report", "automatic", "baseline.v1", 3),
        ]:
            session.add(RunRecord(
                run_id=run_id, event_id=event_id, status="degraded", strategy_version=version,
                admission_origin=origin, artifact_id=f"art-{run_id}", created_at=NOW,
                updated_at=NOW + timedelta(hours=hour),
            ))
            session.add(ArtifactRecord(
                artifact_id=f"art-{run_id}", run_id=run_id, event_id=event_id,
                gate_status="research_only", headline=run_id, summary="saved report",
                payload_json="{}", created_at=NOW + timedelta(hours=hour),
            ))
        session.add(RunRecord(
            run_id="missing-artifact", event_id=event_id, status="completed",
            strategy_version="research.v1", artifact_id="missing", created_at=NOW,
            updated_at=NOW + timedelta(hours=4),
        ))
    with TestClient(create_app(database, source_connectors=[])) as client:
        body = client.get("/v1/research/home")
        assert body.status_code == 200
        data = body.json()
        assert data["latest_report"]["run_id"] == "automatic-report"
        assert data["latest_report"]["status"] == "research_only"
        assert data["latest_report_headline"] == "automatic-report"
        assert data["latest_report_preview"] == {
            "run_id": "automatic-report", "artifact_id": "art-automatic-report",
            "kind": "legacy", "text": "旧版本报告：未保存可核对的综合判断，原始内容仅作参考。",
        }
        expected_time = (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert data["latest_report_at"] == expected_time
        assert data["latest_run"]["run_id"] == "missing-artifact"
        assert data["active_count"] == 130
        assert len(data["active_runs"]) == 3
        assert [r["run_id"] for r in data["active_runs"]] == ["busy-129", "busy-128", "busy-127"]
    with database.session() as session:
        assert session.query(RunRecord).count() == 135
        assert session.query(ArtifactRecord).count() == 3


def test_home_preserves_empty_and_no_report_states(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'empty-home.sqlite3'}")
    database.create_all()
    with TestClient(create_app(database, source_connectors=[])) as client:
        empty = client.get("/v1/research/home").json()
        assert empty["latest_report"] is empty["latest_run"] is None
        assert empty["active_runs"] == [] and empty["active_count"] == 0
        with database.session() as session:
            session.add(RunRecord(
                run_id="failed-home", event_id="event-home", status="failed",
                strategy_version="research.v1", created_at=NOW, updated_at=NOW,
            ))
        failed = client.get("/v1/research/home").json()
        assert failed["latest_report"] is None
        assert failed["latest_run"]["run_id"] == "failed-home"
        assert failed["active_count"] == 0


def test_calendar_family_options_cover_ledger_and_exclude_invalidated_records(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'calendar-facets.sqlite3'}")
    database.create_all()
    now = datetime.now(UTC)
    with database.session() as session:
        for index in range(55):
            session.add(MacroEventRecord(
                macro_event_id=f"facet-{index}", event_id=f"event-facet-{index}",
                external_event_id=f"external-facet-{index}",
                family="fomc" if index < 53 else "ecb" if index == 53 else "invalid-family",
                scheduled_at=now + timedelta(days=index + 1), source_id="official-calendar",
                status="scheduled", created_at=now, updated_at=now,
                invalidated_at=now if index == 54 else None,
            ))
    with TestClient(create_app(database, source_connectors=[])) as client:
        assert len(client.get("/v1/macro-events").json()) == 50
        assert client.get("/v1/macro-events", params={"q": "no match"}).json() == []
        families = client.get("/v1/macro-events/families")
        assert families.status_code == 200
        assert families.json() == ["ecb", "fomc"]
        assert client.get("/v1/macro-events/facet-53").json()["family"] == "ecb"
        assert client.get("/v1/macro-events/families").json() == families.json()
