from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts import MacroEventPayload, TextEnvelope
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.macro_event import (
    list_macro_event_vintages,
    list_macro_events,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    MacroEventRecord,
    MacroEventVintageRecord,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_macro_calendar_release_and_revision_keep_point_in_time_vintages(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'macro-events.sqlite3'}")
    database.create_all()
    admission = AdmissionService(database, clock=lambda: NOW)

    scheduled = _envelope(
        "CPI scheduled with prior 2.8 and consensus 2.9",
        payload=MacroEventPayload(
            external_event_id="bls-cpi-2026-09",
            family="cpi",
            scheduled_at=NOW + timedelta(days=2),
            unit="%",
            prior_value=2.8,
            consensus_value=2.9,
            status="scheduled",
        ),
    )
    scheduled_payload = scheduled.macro_event
    assert scheduled_payload is not None
    released = _envelope(
        "CPI released at 3.1 versus consensus 2.9",
        payload=scheduled_payload.model_copy(
            update={"actual_value": 3.1, "vintage_at": NOW, "status": "released"}
        ),
        received_at=NOW + timedelta(days=2, minutes=1),
    )
    revised = _envelope(
        "CPI revised to 3.0",
        payload=scheduled_payload.model_copy(
            update={
                "actual_value": 3.0,
                "vintage_at": NOW + timedelta(days=3),
                "revision_flag": True,
                "status": "revised",
            }
        ),
        received_at=NOW + timedelta(days=3),
    )

    admission.admit_envelopes((scheduled, released, revised))

    with database.session() as session:
        rows = session.query(MacroEventRecord).all()
        vintages = (
            session.query(MacroEventVintageRecord)
            .order_by(MacroEventVintageRecord.observed_at)
            .all()
        )
        assert len(rows) == 1
        assert len(vintages) == 3
        assert rows[0].prior_value == 2.8
        assert rows[0].consensus_value == 2.9
        assert rows[0].actual_value == 3.0
        assert rows[0].surprise == 3.0 - 2.9
        assert rows[0].revision_flag is True
        assert [item.status for item in vintages] == ["scheduled", "released", "revised"]

    view = list_macro_events(database)
    assert view[0].family == "cpi"
    assert view[0].consensus_value == 2.9
    assert view[0].actual_value == 3.0
    assert view[0].status == "revised"
    assert len(list_macro_event_vintages(database, view[0].macro_event_id)) == 3
    assert len(list_macro_events(database, scope="history", now=NOW + timedelta(days=4))) == 1
    assert len(list_macro_events(database, scope="upcoming", now=NOW)) == 1


def test_official_release_advances_calendar_state_without_inventing_actual_value(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'macro-release.sqlite3'}")
    database.create_all()
    release_at = NOW + timedelta(days=2, minutes=1)
    scheduled = _envelope(
        "CPI release is scheduled",
        payload=MacroEventPayload(
            external_event_id="bls-cpi-release",
            family="cpi",
            scheduled_at=NOW + timedelta(days=2),
            status="scheduled",
        ),
    )
    release_text = "Consumer Price Index release published by BLS"
    released = TextEnvelope(
        source_id="bls-releases",
        source_type=SourceType.official_feed,
        observed_at=release_at,
        published_at=release_at,
        received_at=release_at,
        raw_text=release_text,
        language="en",
        event_hint="bls-releases",
        event_family="cpi",
        source_url=TypeAdapter(HttpUrl).validate_python(
            "https://www.bls.gov/news.release/cpi.nr0.htm"
        ),
        content_hash=hashlib.sha256(release_text.encode()).hexdigest(),
    )

    AdmissionService(database, clock=lambda: release_at).admit_envelopes(
        (scheduled, released)
    )

    view = list_macro_events(database, now=release_at)
    assert len(view) == 1
    assert view[0].status == "released"
    assert view[0].actual_value is None
    assert view[0].consensus_value is None
    assert view[0].surprise is None


def _envelope(
    text: str,
    *,
    payload: MacroEventPayload,
    received_at: datetime = NOW,
) -> TextEnvelope:
    return TextEnvelope(
        source_id="bls-calendar",
        source_type=SourceType.official_feed,
        observed_at=received_at,
        received_at=received_at,
        raw_text=text,
        language="en",
        event_hint=payload.external_event_id,
        event_family=payload.family,
        scheduled_at=payload.scheduled_at,
        source_url=TypeAdapter(HttpUrl).validate_python("https://www.bls.gov/schedule/"),
        macro_event=payload,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
