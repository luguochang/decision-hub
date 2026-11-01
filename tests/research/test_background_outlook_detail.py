"""Candidate behavior controls on a disposable DB; no AI/source qualification."""
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord as Job, AnalyticalObservationRecord as Observation,
    AnalyticalOutlookRecord as Outlook,
)
from packages.evals import assessment_basis
from packages.evals.assessment_basis import build_assessment_basis
from tests.evals.test_assessment_basis import NOW, _job, ordinary

from packages.query_views import analytical_outlooks as module


@pytest.fixture
def records(ordinary):
    with ordinary.session() as session:
        row = session.get(Outlook, "basis-daily")
        row.raw_payload_json = '{"story":"original retained judgment"}'
        session.add(Observation(
            outlook_id=row.outlook_id, status="pending", attempt=0,
            next_attempt_at=NOW + timedelta(days=7), window_json='{"retained":"window"}',
        ))
    return ordinary


def make_job(database):
    assessed, basis = build_assessment_basis(database, "basis-daily", assessed_at=NOW, archive=None)
    # A genuine basis for an intentionally UNVERIFIED ordinary daily control.
    assert assessed.source_status == "unverifiable" and not assessed.directional_usable
    with database.session() as session:
        session.add(Job(**vars(_job(assessed, basis))))
    return assessed


def assert_readonly(database, operation):
    statements = []
    def trace(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())
    event.listen(database.engine, "before_cursor_execute", trace)
    try:
        result = operation()
    finally:
        event.remove(database.engine, "before_cursor_execute", trace)
    assert statements and not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}
    return result


@pytest.mark.parametrize("damage", ["missing", "pending", "expired", "basis_hash", "assessment_hash"])
def test_unavailable_background_job_keeps_record_and_never_full_assesses(records, monkeypatch, damage):
    if damage != "missing":
        make_job(records)
        with records.session() as session:
            job = session.get(Job, "basis-daily")
            if damage == "pending": job.status = "pending"
            if damage == "expired": job.next_attempt_at = NOW - timedelta(seconds=1)
            if damage == "basis_hash": job.basis_hash = "0" * 64
            if damage == "assessment_hash": job.assessment_hash = "0" * 64
    calls = []
    real_reader = assessment_basis.read_valid_assessment
    def read(*args, **kwargs):
        calls.append(args[1].outlook_id)
        return real_reader(*args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("background_detail_called_full_assessor_or_cohort_selection")
    monkeypatch.setattr(module, "read_valid_assessment", read)
    monkeypatch.setattr(module, "assess_outlook_in_session", forbidden)
    query = module.AnalyticalOutlookQuery(records, clock=lambda: NOW, background_assessments=True)
    monkeypatch.setattr(query, "_recipe_selection", forbidden)
    result = assert_readonly(records, lambda: query.get("basis-daily", limit=10))
    assessment = result["assessment"]
    expected = "assessment_background_pending" if damage in {"missing", "pending"} else "assessment_recheck_pending"
    assert assessment["source_status"] == assessment["method_status"] == "unverifiable"
    assert assessment["label_status"] == "unverifiable" and assessment["source_reason"] == expected
    assert assessment["selection_status"] == "unchecked" and assessment["directional_usable"] is False
    assert assessment["stable_recipe_hash"] is None and assessment["source_refs"] == []
    assert assessment["verified_direction_correct"] is None and assessment["verified_directional_return_pct"] is None
    assert result["outlook"]["outlook_id"] == "basis-daily"
    assert result["outlook"]["observation"]["status"] == "pending"
    assert result["raw_payload"] == {"story": "original retained judgment"}
    assert result["window"] == {"retained": "window"}
    assert calls == ([] if damage == "missing" else ["basis-daily"])
    with records.session() as session:
        job = session.get(Job, "basis-daily")
        assert (job is None) == (damage == "missing")
        if damage == "pending": assert job.status == "pending"
        if damage == "basis_hash": assert job.basis_hash == "0" * 64
        if damage == "assessment_hash": assert job.assessment_hash == "0" * 64


def test_valid_actual_basis_still_runs_original_validation_and_selection(records, monkeypatch):
    genuine_assessment = make_job(records)
    real_reader = assessment_basis.read_valid_assessment
    calls = []
    def read(*args, **kwargs):
        result = real_reader(*args, **kwargs)
        calls.append(result)
        assert result is not None
        return result
    monkeypatch.setattr(module, "read_valid_assessment", read)
    monkeypatch.setattr(module, "assess_outlook_in_session", lambda *a, **kw: pytest.fail("valid job rebuilt"))
    query = module.AnalyticalOutlookQuery(records, clock=lambda: NOW, background_assessments=True)
    result = assert_readonly(records, lambda: query.get("basis-daily", limit=10))
    assert len(calls) == 1
    assert result["assessment"] == genuine_assessment.model_copy(update={"selection_status": "unkeyed", "directional_usable": False}).model_dump(mode="json")
    assert result["assessment"]["source_reason"] not in {"assessment_background_pending", "assessment_recheck_pending"}
    # This fixture remains unverified: cache validity is not a genuine AI proof.
    assert result["assessment"]["source_status"] == "unverifiable" and result["assessment"]["directional_usable"] is False


def test_non_background_keeps_actual_original_full_fallback(records, monkeypatch):
    original_assess = module.assess_outlook_in_session
    calls = []
    def assess(*args, **kwargs):
        calls.append(args[1])
        value = original_assess(*args, **kwargs)
        calls.append(value)
        return value
    monkeypatch.setattr(module, "assess_outlook_in_session", assess)
    query = module.AnalyticalOutlookQuery(records, clock=lambda: NOW, background_assessments=False)
    result = assert_readonly(records, lambda: query.get("basis-daily", limit=10))
    assert calls[0] == "basis-daily" and len(calls) == 2
    assert result["assessment"] == calls[1].model_copy(update={"selection_status": "unkeyed", "directional_usable": False}).model_dump(mode="json")
