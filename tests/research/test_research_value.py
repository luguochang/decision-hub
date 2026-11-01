from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.contracts_py.decision_hub_contracts import CoverageAssessment, DshSessionPrompt
from packages.kernel.decision_hub_kernel.application import research_value
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    EventWatchRecord,
    ResearchEvidenceRecord,
    RunRecord,
)
from packages.provider_adapters.research import CryptoMacroFactPack
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient, _identity, _request

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _save_request(
    database, run_id, event_id, *, cutoff=NOW, family="geopolitical_shock", full_task=True
):
    request = _request(run_id).model_copy(update={
        "event_id": event_id, "pit_cutoff_at": cutoff,
        "evidence_requirements": list(
            CryptoMacroFactPack.from_pack(PACK_ROOT).contract_requirements_for_event(family)
        ),
    })
    if full_task:
        from packages.kernel.decision_hub_kernel.application.research_task import (
            ResearchTaskService,
        )

        ResearchTaskService(database, clock=lambda: NOW).freeze(request)
    links = DshSessionLinkService(database, clock=lambda: NOW)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    submission = runtime._submission(request)
    links.reserve(submission, _identity())
    links.store_prompt(DshSessionPrompt(
        schema_version="dsh-session-prompt.v1", run_id=run_id, generation=1,
        dsh_session_id=submission.deterministic_session_id,
        request_id=submission.deterministic_request_id, request_hash=submission.request_hash,
        prompt=build_trusted_web_research_prompt(request),
    ))
    return request


def test_value_evaluation_reuses_durable_facts_when_result_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'value.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-value",
                event_id="event-value",
                status="failed",
                strategy_version="research.v1",
                runtime_version="dsh.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    EventWatchService(database, clock=lambda: NOW).ensure_watch(
        event_id="event-value",
        source_id="manual",
        event_family="geopolitical_shock",
        scheduled_at=NOW - timedelta(days=1),
        window_offsets=("t-5m", "t+1m"),
    )
    request = _save_request(database, "run-value", "event-value", cutoff=NOW - timedelta(days=1))

    durable_evidence = object()
    durable_facts = object()
    observed: dict[str, object] = {}

    def list_evidence(_service: object, run_id: str) -> list[object]:
        assert run_id == "run-value"
        return [durable_evidence]

    def list_facts(_store: object, run_id: str) -> list[object]:
        assert run_id == "run-value"
        return [durable_facts]

    def assess(
        _requirements: object,
        evidence: object,
        *,
        cutoff_at: object,
        assessed_at: object,
        facts: object,
    ) -> CoverageAssessment:
        observed.update(
            requirements=_requirements,
            evidence=evidence,
            facts=facts,
            cutoff_at=cutoff_at,
            assessed_at=assessed_at,
        )
        return CoverageAssessment(
            status="insufficient",
            covered_requirement_ids=[],
            gaps=[],
            conflicts=[],
            hard_coverage_ratio=0.25,
            soft_coverage_ratio=0.5,
            assessed_at=NOW,
        )

    monkeypatch.setattr(
        research_value.ResearchEvidenceService, "list_run_evidence", list_evidence
    )
    monkeypatch.setattr(research_value.ResearchFactStore, "list_run_facts", list_facts)
    monkeypatch.setattr(research_value, "assess_evidence_sufficiency", assess)

    evaluation = ResearchValueEvaluationService(
        database, pack_root=PACK_ROOT, clock=lambda: NOW
    ).evaluate("run-value", evaluation_version="fallback-v1")

    assert observed["evidence"] == [durable_evidence]
    assert observed["facts"] == [durable_facts]
    assert observed["requirements"] == request.evidence_requirements
    assert observed["cutoff_at"] == NOW - timedelta(days=1)
    importance = {
        item.requirement_id: item.importance
        for item in observed["requirements"]  # type: ignore[union-attr]
    }
    assert importance["expectation_pricing"] == "soft"
    assert importance["macro_transmission"] == "soft"
    assert evaluation.hard_coverage_ratio == 0.25
    assert evaluation.mode == "failed"
    assert evaluation.coverage_basis == "saved_task_request"
    assert evaluation.coverage_cutoff_at == request.pit_cutoff_at


def test_current_value_version_changes_when_durable_evidence_arrives(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'value-version.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-version",
                event_id="event-version",
                status="failed",
                strategy_version="research.v1",
                runtime_version="dsh.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    EventWatchService(database, clock=lambda: NOW).ensure_watch(
        event_id="event-version",
        source_id="manual",
        event_family="central_bank_speech",
        scheduled_at=NOW - timedelta(days=1),
        window_offsets=("t-5m", "t+1m"),
    )

    service = ResearchValueEvaluationService(database, pack_root=PACK_ROOT, clock=lambda: NOW)
    before = service.evaluate_current("run-version")

    with database.session() as session:
        watch = session.query(EventWatchRecord).filter_by(event_id="event-version").one()
        watch.event_family = "geopolitical_shock"
    after_policy = service.evaluate_current("run-version")

    with database.session() as session:
        session.add(
            ResearchEvidenceRecord(
                evidence_id="late-evidence",
                run_id="run-version",
                capability_id="official.macro",
                requirement_id="event_identity",
                kind="official",
                authority="official",
                source_id="federalreserve.gov",
                source_url="https://www.federalreserve.gov/",
                published_at=NOW - timedelta(minutes=1),
                observed_at=NOW - timedelta(seconds=30),
                received_at=NOW - timedelta(seconds=20),
                content_hash="0" * 64,
                excerpt="Late durable evidence retained after terminal failure.",
                structured_payload_ref=None,
                tool_call_id="late-call",
                research_session_id="late-session",
                round=1,
                quality="accepted",
                freshness_status="fresh",
                conflict_group=None,
                accepted_at=NOW,
            )
        )

    after = service.evaluate_current("run-version")

    assert before.evaluation_version.startswith("research-value.v4:")
    assert after_policy.evaluation_version != before.evaluation_version
    assert after.evaluation_version.startswith("research-value.v4:")
    assert after.evaluation_version != before.evaluation_version
    assert len(after.evaluation_version) == 82
    assert after.evaluation_id != before.evaluation_id
    with database.session() as session:
        session.get(ResearchEvidenceRecord, "late-evidence").quality = "rejected"
    changed = service.evaluate_current("run-version")
    assert changed.evaluation_version != after.evaluation_version
    assert changed.accepted_evidence_count == 0


@pytest.mark.parametrize("damage", ["missing", "requirement", "identity", "ambiguous", "truncated"])
def test_missing_or_invalid_saved_request_has_unknown_coverage(tmp_path, damage):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'basis.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(RunRecord(
            run_id="r", event_id="e", status="failed", strategy_version="research.v1",
            created_at=NOW, updated_at=NOW,
        ))
    if damage != "missing":
        _save_request(db, "r", "e", full_task=False)
        with db.session() as session:
            prompt = session.get(DshSessionPromptRecord, ("r", 1))
            if damage == "requirement":
                prompt.prompt = prompt.prompt.replace('"importance":"hard"', '"importance":"soft"')
            elif damage == "identity":
                prompt.request_hash = "f" * 64
            elif damage == "ambiguous":
                from packages.kernel.decision_hub_kernel.application.research_value_basis import (
                    CONTROL_MARKER,
                )
                prompt.prompt += CONTROL_MARKER + "{}"
            else:
                prompt.prompt = prompt.prompt.split("\nBEGIN_UNTRUSTED_RESEARCH_DATA\n")[0]
    service = ResearchValueEvaluationService(
        db, pack_root=tmp_path / "missing-pack", clock=lambda: NOW
    )
    value = service.evaluate_current("r")
    assert value.coverage_basis == ("unavailable" if damage == "missing" else "invalid")
    assert value.hard_coverage_ratio is None and value.soft_coverage_ratio is None
    assert value.coverage_basis_ref is None
    assert service.evaluate_current("r") == value


def test_saved_requirements_ignore_current_pack_and_event_family(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'frozen.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(RunRecord(
            run_id="r", event_id="e", status="failed", strategy_version="research.v1",
            created_at=NOW, updated_at=NOW,
        ))
    _save_request(db, "r", "e")
    first = ResearchValueEvaluationService(
        db, pack_root=PACK_ROOT, clock=lambda: NOW
    ).evaluate_current("r")
    second = ResearchValueEvaluationService(
        db, pack_root=tmp_path / "nonexistent", clock=lambda: NOW
    ).evaluate_current("r")
    assert first == second
    assert first.coverage_basis == "saved_task_request"
    assert first.hard_coverage_ratio == 0


def test_refresh_is_additive_idempotent_and_refuses_active_runs(tmp_path):
    from tools.ops.refresh_research_values import refresh

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'refresh.sqlite3'}")
    db.create_all()
    with db.session() as session:
        for name, status in (("old", "failed"), ("active", "running")):
            session.add(RunRecord(
                run_id=name, event_id="e", status=status, strategy_version="research.v1",
                created_at=NOW, updated_at=NOW,
            ))
    original = ResearchValueEvaluationService(
        db, pack_root=PACK_ROOT, clock=lambda: NOW
    ).evaluate("old", evaluation_version="old-snapshot")
    assert refresh(db, ["old"])["old_evaluations"] == 1
    with pytest.raises(ValueError, match="terminal_research_run_required"):
        refresh(db, ["old", "active"], apply=True)
    first = refresh(db, ["old"], apply=True)
    second = refresh(db, ["old"], apply=True)
    assert first["appended"] == 1 and first["old_evaluations_preserved"]
    assert second["appended"] == 0 and second["old_evaluations_preserved"]
    assert ResearchValueEvaluationService(db, pack_root=PACK_ROOT).evaluate(
        "old", evaluation_version="old-snapshot"
    ) == original


def test_citation_traceability_resolves_evidence_ids_and_ignores_stale_rows() -> None:
    artifact = SimpleNamespace(
        payload_json=json.dumps({"citations": ["ev-1", "ev-2"]}),
    )
    evidence = [
        SimpleNamespace(
            evidence_id="ev-1",
            content_ref=None,
            source_url="https://example.test/one",
            source_id="source-one",
            quality="accepted",
        ),
        SimpleNamespace(
            evidence_id="ev-2",
            content_ref="hub://documents/two",
            source_url=None,
            source_id="source-two",
            quality="accepted",
        ),
        SimpleNamespace(
            evidence_id="stale-1",
            content_ref=None,
            source_url="https://example.test/stale",
            source_id="stale-source",
            quality="stale",
        ),
    ]

    assert research_value._citation_traceability(artifact, evidence) == 1.0


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "degraded"])
def test_refresh_accepts_all_terminal_research_statuses(tmp_path, status):
    from tools.ops.refresh_research_values import refresh

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'terminal.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(RunRecord(
            run_id="r", event_id="e", status=status, strategy_version="research.v1",
            created_at=NOW, updated_at=NOW,
        ))
    assert refresh(db, ["r"])["run_ids"] == ["r"]
    assert refresh(db, ["r"], apply=True)["appended"] == 1
    assert refresh(db, ["r"], apply=True)["appended"] == 0
