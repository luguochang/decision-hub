"""Two saved real FRED originals, copied into private SQLite; no formal writes/fetches.

These are descriptive compatibility controls, not historical PIT or forecast qualification.
Skip explicitly when the saved local native snapshot is unavailable on another checkout.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.hub_worker.market_enhancement_bootstrap import MarketEnhancementTick, _authenticated_fred
from packages.kernel.decision_hub_kernel.application.market_enhancements import (
    MacroStateInput,
    macro_state,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRecord,
    StudyRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = (
    ROOT / "artifacts/market-risk-implementation-20261007/B2/query-index01/private-probe.sqlite3"
)
FORMAL_RAW = ROOT.parent / "decision-hub/data/core-live-20260913/raw-captures"
IDS = (
    "fv_e06f761258fb95ce4d31f3a53fd6868088776e578ed98077",
    "fv_2fd8305600e0cd1d491375eb299754e0e9f2252b5367dc7c",
)
CUTOFF = datetime.fromisoformat("2026-10-07T22:34:37.128037+00:00")


@pytest.fixture
def saved_legacy(tmp_path, monkeypatch):
    if not SNAPSHOT.is_file() or not FORMAL_RAW.is_dir():
        pytest.skip(
            "Exact saved local native FRED snapshot/archive unavailable; never synthesize it"
        )
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    source = create_engine("sqlite+pysqlite:///file:" + SNAPSHOT.as_posix() + "?mode=ro&uri=true")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'native-legacy.sqlite3'}")
    database.create_all()
    original_archive, private_archive = (
        RawBodyArchive(FORMAL_RAW),
        RawBodyArchive(tmp_path / "raw-captures"),
    )
    copied = {}

    def retain(row):
        key = (type(row), tuple(getattr(row, item.name) for item in row.__table__.primary_key))
        copied[key] = {column.name: getattr(row, column.name) for column in row.__table__.columns}

    with Session(source, autoflush=False) as session:
        session.connection().exec_driver_sql("PRAGMA query_only=ON")
        for identity in IDS:
            version = session.get(FactVersionRecord, identity)
            assert version is not None, "Missing actual saved FRED original"
            fact = session.get(ResearchFactRecord, version.original_fact_id)
            evidence = session.get(ResearchEvidenceRecord, fact.evidence_id)
            parsed = session.get(ParsedVersionRecord, version.parsed_version_id)
            link = session.get(EvidenceParsedLinkRecord, fact.evidence_id)
            run = session.get(RunRecord, fact.run_id)
            study = session.get(StudyRecord, run.study_id)
            for row in (study, run, evidence, parsed, link, fact, version):
                retain(row)
            import json

            for capture_id in json.loads(parsed.capture_ids_json):
                capture = session.get(RawCaptureRecord, capture_id)
                retain(capture)
                digest, ref = private_archive.put(original_archive.read(capture.body_ref))
                assert (ref, digest) == (capture.body_ref, capture.body_hash)
    source.dispose()
    with database.session() as session:
        # Exact saved native columns/JSON/clocks are retained, not reconstructed.
        for (model, _), values in copied.items():
            session.execute(model.__table__.insert().values(**values))
    yield database, private_archive
    database.engine.dispose()


def test_saved_accepted_legacy_originals_private_equivalence(saved_legacy):
    database, archive = saved_legacy
    with database.session() as session:
        rows = [session.get(FactVersionRecord, identity) for identity in IDS]
        before = [row.payload_json for row in rows]
        releases = []
        for row in rows:
            release, binding = _authenticated_fred(session, row, archive, CUTOFF)
            assert release.source.known_at == as_utc(row.known_at) == binding.known_at
            assert release.source.public_at is None and release.vintage_at is None
            assert release.source.public_precision == "unknown"
            assert release.observation_date.isoformat() == "2026-09-28"
            assert len(binding.captures) == 1
            releases.append(release)
        result = macro_state(
            MacroStateInput(instrument="BTC-USDT", releases=tuple(releases)), cutoff_at=CUTOFF
        )
        assert (
            not result.data_pit_verified
            and not result.probability_qualified
            and not result.promotion
        )
        assert result.probability is None
        assert len([value for value in result.features if value.name.endswith(":level")]) == 2
        assert result.missing_reasons
        assert [row.payload_json for row in rows] == before


def test_normal_worker_entry_reuses_two_saved_originals_for_both_assets(saved_legacy):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.hub_api.market_enhancement_routes import market_enhancement_router
    from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope

    database, archive = saved_legacy
    tick = MarketEnhancementTick(database, archive.root.parent, clock=lambda: CUTOFF)
    assert tick.process() == 6
    assert tick.process() == 0  # Saved originals/receipts remain idempotent.
    app = FastAPI()
    app.include_router(market_enhancement_router(database, clock=lambda: CUTOFF))
    client = TestClient(app)
    for instrument in ("BTC-USDT", "ETH-USDT"):
        response = client.get(f"/v1/risk/scopes/{spot_scope(instrument).scope_id}/enhancements")
        assert response.status_code == 200
        macro = response.json()["routes"][0]
        assert macro["original_count"] == 2 and macro["excluded_source_count"] == 0
        assert macro["observation_date"] == "2026-09-28"
        assert macro["data_pit_verified"] is False
        assert macro["admission_state"] == "not_admitted" and macro["probability"] is None


@pytest.mark.parametrize("damage", ["bad_hash", "wrong_date", "wrong_value"])
def test_saved_legacy_damage_remains_rejected(saved_legacy, damage):
    database, archive = saved_legacy
    with database.session() as session:
        capture = session.scalar(select(RawCaptureRecord).order_by(RawCaptureRecord.capture_id))
        raw, ref, digest = archive.read(capture.body_ref), capture.body_ref, capture.body_hash
        if damage == "bad_hash":
            (archive.root / digest[:2] / digest).write_bytes(raw + b"private corruption")
        else:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            fields, rows = reader.fieldnames, list(reader)
            changed = False
            for row in rows:
                date_key = "DATE" if "DATE" in row else "observation_date"
                if row[date_key] == "2026-09-28":
                    if damage == "wrong_date":
                        row[date_key] = "2026-09-29"
                    else:
                        series = next(key for key in fields if key in {"DGS2", "DGS10"})
                        row[series] = str(Decimal(row[series]) + 1)
                    changed = True
            assert changed
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            body = output.getvalue().encode()
            capture.body_hash, capture.body_ref = archive.put(body)
            capture.byte_count = len(body)
            assert capture.body_ref != ref
        target = next(
            identity
            for identity in IDS
            if capture.capture_id
            in session.get(
                ParsedVersionRecord, session.get(FactVersionRecord, identity).parsed_version_id
            ).capture_ids_json
        )
    with database.session() as session:
        expected = {
            "bad_hash": "raw_capture_archive_corrupt",
            "wrong_date": "enhancement_fred_matching_raw_unavailable",
            "wrong_value": "enhancement_fred_raw_value_mismatch",
        }[damage]
        with pytest.raises(ValueError, match=expected):
            _authenticated_fred(session, session.get(FactVersionRecord, target), archive, CUTOFF)


def test_nonaccepted_legacy_is_explicitly_excluded(saved_legacy):
    database, archive = saved_legacy
    with database.session() as session:
        version = session.get(FactVersionRecord, IDS[0])
        fact = session.get(ResearchFactRecord, version.original_fact_id)
        fact.quality = "candidate"
    with (
        database.session() as session,
        pytest.raises(ValueError, match="enhancement_fred_fact_or_evidence_not_accepted"),
    ):
        _authenticated_fred(session, session.get(FactVersionRecord, IDS[0]), archive, CUTOFF)
