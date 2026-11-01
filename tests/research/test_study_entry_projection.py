"""Draft read-model controls. No Provider, model submission, or production DB."""

from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study, study_for_event
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    Database,
    RunRecord,
    StudyRecord,
)
from packages.query_views.studies import StudyQueryService
from tests.research.test_study_identity import NOW, setup


def _event_study(db: Database, text: str, source: str = "manual") -> tuple[str, str]:
    event_id, _, _ = AdmissionService(db, clock=lambda: NOW).admit(
        ObservationCreate(text=text, source_id=source)
    )
    with db.session() as session:
        study_id = study_for_event(session, event_id, NOW)
    return event_id, study_id


def _decision(
    db: Database,
    study_id: str,
    event_id: str,
    *,
    decision: Literal["admitted", "merged", "blocked", "ignored"] = "ignored",
    run_id: str | None = None,
    n: int = 0,
) -> None:
    with db.session() as session:
        session.add(AdmissionDecisionRecord(
            decision_id=f"entry-decision-{n}", attempt_id=f"entry-attempt-{n}",
            event_id=event_id, study_id=study_id, source_id="fed-press",
            strategy_version="research.v1", policy_version="test.v1",
            decision=decision, reason_code="no_high_impact_title_match",
            details_json="{}", run_id=run_id, decided_at=NOW + timedelta(minutes=n),
        ))


def _saved_studies(db: Database) -> list[tuple[str, str, str, str, str]]:
    with db.session() as session:
        return [(row.study_id, row.title, row.market, row.instrument, row.status)
                for row in session.query(StudyRecord).order_by(StudyRecord.study_id)]


def test_ignored_bank_event_has_no_research_but_keeps_original_study_and_input(tmp_path: Path):
    db = setup(tmp_path)
    title = "Federal Reserve Board announces approval of application by Fleur Capital Corporation"
    event_id, study_id = _event_study(db, title, "fed-press")
    _decision(db, study_id, event_id)
    # A baseline task is not an AI research task.
    RunService(db, clock=lambda: NOW).create(event_id, "bank-baseline")
    original = _saved_studies(db)
    view = StudyQueryService(db).list().items[0]
    assert view.title == title and view.instrument == "BTC-USDT-SWAP" and view.status == "active"
    assert view.entry_projection is not None
    assert view.entry_projection.state == "collected_not_researched"
    assert view.entry_projection.research_runs == 0
    assert view.entry_projection.latest_admission_decision == "ignored"
    assert view.entry_projection.display_title is None
    assert view.entry_projection.display_title_source_run_id is None
    assert _saved_studies(db) == original
    with db.session() as session:
        assert session.query(RunRecord).filter_by(strategy_version="research.v1").count() == 0


def test_later_ignored_receipt_does_not_erase_existing_research(tmp_path: Path):
    db = setup(tmp_path)
    event_id, study_id = _event_study(db, "FOMC monetary policy statement", "fed-press")
    RunService(db, clock=lambda: NOW).create(
        event_id, "real-research-record", strategy_version="research.v1",
        admission_origin="automatic",
    )
    _decision(db, study_id, event_id, n=1)
    view = StudyQueryService(db).list().items[0].entry_projection
    assert view is not None
    assert view.state == "has_research" and view.research_runs == 1
    assert view.latest_admission_decision == "ignored"
    # Registered/admitted is not a claim of completed execution or publication.
    with db.session() as session:
        run = session.query(RunRecord).filter_by(strategy_version="research.v1").one()
        assert run.status == "admitted" and run.artifact_id is None


def test_missing_admission_and_dangling_admission_run_are_unknown(tmp_path: Path):
    db = setup(tmp_path)
    first_event, first_study = _event_study(db, "No admission receipt")
    second_event, second_study = _event_study(db, "An admission points to missing research")
    _decision(db, second_study, second_event, decision="admitted", run_id="missing-run", n=2)
    entries = {item.study_id: item.entry_projection for item in StudyQueryService(db).list().items}
    first, second = entries[first_study], entries[second_study]
    assert first is not None and second is not None
    assert first.state == "unknown"
    assert first.latest_admission_decision is None
    assert second.state == "unknown"
    assert second.research_runs == 0
    assert first_event != second_event


def test_compact_manual_title_uses_original_explicit_input_not_btc_defaults(tmp_path: Path):
    db = setup(tmp_path)
    instruction = "分别由宏观与衍生品研究角色核查最新 BTC 永续市场，再综合研究判断。保留原始证据。"
    event_id, study_id = _event_study(db, instruction)
    run_id, _ = RunService(db, clock=lambda: NOW).create(
        event_id, "explicit-input", strategy_version="research.v1",
    )
    other_event, other_study = _event_study(db, "Review the banking application")
    RunService(db, clock=lambda: NOW).create(
        other_event, "no-explicit-btc", strategy_version="research.v1",
    )
    original = _saved_studies(db)
    entries = {item.study_id: item for item in StudyQueryService(db).list().items}
    assert entries[study_id].title == instruction
    projection = entries[study_id].entry_projection
    assert projection is not None
    assert projection.display_title == "BTC 永续与宏观市场研究"
    assert projection.display_title_source_run_id == run_id
    assert projection.display_title_source_observation_id
    assert entries[other_study].instrument == "BTC-USDT-SWAP"
    other = entries[other_study].entry_projection
    assert other is not None and other.display_title is None
    assert _saved_studies(db) == original


@pytest.mark.parametrize("text", [
    "不要研究 BTC 永续市场，请保留银行审批原文。",
    "研究 BTC 和黄金之间的关系。",
    "研究 BTC 与 ETH 的相对表现。",
])
def test_ambiguous_or_negative_manual_scope_keeps_the_original_heading(tmp_path: Path, text: str):
    db = setup(tmp_path)
    event_id, _ = _event_study(db, text)
    RunService(db, clock=lambda: NOW).create(
        event_id, "ambiguous-input", strategy_version="research.v1",
    )
    view = StudyQueryService(db).list().items[0]
    assert view.title == text
    assert view.entry_projection is not None and view.entry_projection.display_title is None


def test_study_list_reads_one_projection_statement_for_the_whole_page(tmp_path: Path):
    db = setup(tmp_path)
    with db.session() as session:
        for n in range(12):
            ensure_study(
                session, question_key=f"batch-{n}", title=f"batch-{n}",
                market="crypto", instrument="BTC-USDT-SWAP", now=NOW,
            )
    statements = []

    def capture(
        _conn: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _many: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        listing = StudyQueryService(db).list(limit=12)
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert len(listing.items) == 12
    # Count, page, one combined projection query. No per-card Run/Admission reads.
    assert len(statements) == 3
    projection = [sql for sql in statements if "row_number()" in sql.lower()]
    assert len(projection) == 1
    assert "admission_decisions" in projection[0] and "observations" in projection[0]
