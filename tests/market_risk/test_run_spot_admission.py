"""Real isolated Run writer/admission originals; synthetic market captures, no model."""

import hashlib
import json
from datetime import timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    quality_context_for_spot_run_in_session,
    read_run_spot_admission_in_session,
    require_run_research_scope_in_session,
    spot_run_context_inputs_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.kernel.decision_hub_kernel.persistence.run_spot_admission import (
    RunSpotAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from tests.market_risk.test_spot_scope_admission import prepared

assert prepared is not None


def create_case(prepared, instrument="BTC-USDT"):
    db, now, _, _, runtime = prepared
    service, effective, host = runtime(instrument, capture=True)
    token = service.approve_trial(request_id="first", effective=effective, host=host)
    event_id, _, _ = AdmissionService(db, clock=lambda: now[0]).admit(
        ObservationCreate(
            text=f"A controlled {instrument} market risk question.",
            source_id="risk-monitor",
            source_type=SourceType.transcript,
            observed_at=now[0] - timedelta(seconds=1),
            language="en",
        )
    )
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="risk24h",
            title="risk24h",
            market="crypto",
            instrument=instrument,
            horizon_scope="24h",
            now=now[0],
        )

    def resolver(session, study, at):
        assert study.instrument == instrument
        return service.resolve_current_in_session(
            session, effective=effective, host=host, assessed_at=at
        )

    runs = RunService(db, clock=lambda: now[0], spot_admission_resolver=resolver)
    return db, now, token, runs, event_id, study_id


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
def test_new_run_freezes_scope_and_old_run_survives_expiry(prepared, instrument):
    db, now, token, runs, event_id, study_id = create_case(prepared, instrument)
    run_id, created = runs.create(
        event_id, "first-run", strategy_version="research.v1", study_id=study_id
    )
    assert created
    with db.session() as session:
        frozen = read_run_spot_admission_in_session(session, run_id)
        assert frozen is not None and frozen.admission_id == token.admission_id
        assert frozen.instrument == instrument and frozen.horizon_scope == "24h"
        inputs = spot_run_context_inputs_in_session(session, run_id)
        context = quality_context_for_spot_run_in_session(session, run_id)
        assert context.additional_required_context_refs == tuple(
            item.evidence_id for item in inputs
        )
        assert not context.context_fidelity_verified and not context.path_risk_model_qualified
        assert json.loads(inputs[1].excerpt)["qualification"] == "unknown"
    now[0] += timedelta(hours=1)
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        assert require_run_research_scope_in_session(session, run).instrument == instrument
    assert runs.create(
        event_id, "first-run", strategy_version="research.v1", study_id=study_id
    ) == (run_id, False)
    with pytest.raises(
        ValueError, match="spot_effective_host_not_ready|spot_current_admission_unavailable"
    ):
        runs.create(event_id, "second-run", strategy_version="research.v1", study_id=study_id)
    with db.session() as session:
        assert (
            session.query(RunRecord).count() == session.query(RunSpotAdmissionRecord).count() == 1
        )


@pytest.mark.parametrize("damage", ["payload", "resigned_scope", "approval"])
def test_frozen_originals_reject_damage_even_with_resigned_payload(prepared, damage):
    db, _, _, runs, event_id, study_id = create_case(prepared)
    run_id, _ = runs.create(event_id, strategy_version="research.v1", study_id=study_id)
    with db.session() as session:
        row = session.get(RunSpotAdmissionRecord, run_id)
        assert row is not None
        if damage == "approval":
            original = session.get(SpotResearchAdmissionRecord, row.admission_id)
            assert original is not None
            original.proof_json = original.proof_json.replace("BTC-USDT", "ETH-USDT")
        else:
            row.payload_json = row.payload_json.replace(
                '"horizon_scope":"24h"', '"horizon_scope":"72h"'
            )
            if damage == "resigned_scope":
                row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with db.session() as session, pytest.raises(ValueError, match="run_spot_admission_"):
        read_run_spot_admission_in_session(session, run_id)


def test_same_writer_rollback_removes_run_and_frozen_admission(prepared):
    db, _, _, runs, event_id, study_id = create_case(prepared)
    with pytest.raises(RuntimeError), db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        run_id, _ = runs.create(
            event_id, strategy_version="research.v1", study_id=study_id, session=session
        )
        assert session.get(RunSpotAdmissionRecord, run_id) is not None
        raise RuntimeError("caller rollback")
    with db.session() as session:
        assert (
            session.query(RunRecord).count() == session.query(RunSpotAdmissionRecord).count() == 0
        )


def test_0077_is_additive_and_downgrade_preserves_audit(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from packages.kernel.decision_hub_kernel.persistence.db import Base, Database

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    Base.metadata.create_all(
        db.engine,
        tables=[
            table
            for table in Base.metadata.sorted_tables
            if table.name != RunSpotAdmissionRecord.__tablename__
        ],
    )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(db.engine.url))
    config.attributes["configure_logger"] = False
    command.stamp(config, "0076_legacy_index_reconciliation")
    command.upgrade(config, "0077_run_spot_admissions")
    actual = inspect(db.engine)
    assert RunSpotAdmissionRecord.__tablename__ in actual.get_table_names()
    columns = {item["name"] for item in actual.get_columns(RunSpotAdmissionRecord.__tablename__)}
    assert columns == set(RunSpotAdmissionRecord.__table__.columns.keys())
    with pytest.raises(RuntimeError, match="audit_preservation"):
        command.downgrade(config, "0076_legacy_index_reconciliation")
    assert RunSpotAdmissionRecord.__tablename__ in inspect(db.engine).get_table_names()
    db.engine.dispose()
