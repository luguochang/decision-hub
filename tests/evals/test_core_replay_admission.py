"""Exercise actual Replay gateway and Kernel admission with the unchanged Pack policy."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import AnyUrl
from sqlalchemy.orm import Session

import tools.research_evaluation.audit_core_replay_dataset as audit_tool
from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchEvaluationCase,
)
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.fact_store import (
    ResearchFactStore,
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchEvidenceRecord,
    ResearchFactRecord,
)
from packages.provider_adapters.research import (
    ReplayResearchCapabilityAdapter,
    ResearchSourceRegistry,
    load_replay_archive,
)

PACK = Path("packs/crypto_macro")
MANIFEST = PACK / "evaluations/r2r_dev_core_v3/manifest.json"


def _single_case(
    requirement_id: str = "event_identity", **evidence_updates: Any,
) -> ResearchEvaluationCase:
    template = load_research_evaluation_dataset(MANIFEST).cases[0]
    fixture = next(item for item in template.archived_capability_fixtures
                   if item.requirement_id == requirement_id)
    candidate = fixture.evidence_candidates[0].model_copy(update=evidence_updates)
    return template.model_copy(update={
        "evidence_requirements": [item for item in template.evidence_requirements
                                  if item.requirement_id == requirement_id],
        "expected_hard_requirement_ids": [requirement_id],
        "archived_capability_fixtures": [fixture.model_copy(update={
            "evidence_candidates": [candidate], "facts": [],
        })],
    })


def _with_official_fact(case: ResearchEvaluationCase) -> ResearchEvaluationCase:
    fixture = case.archived_capability_fixtures[0]
    candidate = fixture.evidence_candidates[0]
    semantic: dict[str, Any] = {
        "requirement_id": candidate.requirement_id, "metric_family": "macro.policy",
        "field": "target_rate", "instrument": None, "venue": None, "value": "4.75",
        "unit": "percent", "window_start_at": None, "window_end_at": None,
        "event_offset": None, "published_at": candidate.published_at,
        "source_id": candidate.source_id, "independence_group": "federal-reserve",
        "delay_class": "historical", "payload_schema_ref": "test.official-policy.v1",
        "attributes": {},
    }
    payload_hash = research_fact_payload_hash(**semantic)
    fact = FactEnvelope.model_validate({
        **semantic, "schema_version": "fact-envelope.v1",
        "fact_id": research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash,
        ),
        "evidence_id": candidate.evidence_id, "observed_at": candidate.observed_at,
        "received_at": candidate.received_at, "quality": "candidate",
        "payload_hash": payload_hash,
    })
    return case.model_copy(update={
        "archived_capability_fixtures": [fixture.model_copy(update={"facts": [fact]})],
    })


def _archive(tmp_path: Path, case: ResearchEvaluationCase) -> Path:
    path = tmp_path / "case.json"
    path.write_text(case.model_dump_json(), encoding="utf-8")
    return path


async def _run(tmp_path: Path, case: ResearchEvaluationCase) -> dict[str, Any]:
    return cast(dict[str, Any], await audit_tool._audit_case(
        case, _archive(tmp_path, case), audit_tool._replay_manifest(PACK),
        ResearchSourceRegistry.from_pack(PACK),
    ))


@pytest.mark.asyncio
async def test_audit_runs_real_kernel_evidence_and_fact_admission_at_frozen_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _with_official_fact(_single_case())
    original = case.model_dump(mode="json")
    evidence_calls, fact_calls = [], []
    evidence_accept = ResearchEvidenceService.accept_candidates_in_session
    fact_accept = ResearchFactStore.accept_facts_in_session

    def evidence_spy(
        self: ResearchEvidenceService, session: Session, **kwargs: Any,
    ) -> list[EvidenceCandidate]:
        assert all(item.quality == "candidate" for item in kwargs["candidates"])
        evidence_calls.append(kwargs)
        return evidence_accept(self, session, **kwargs)

    def fact_spy(
        self: ResearchFactStore, session: Session, **kwargs: Any,
    ) -> list[FactEnvelope]:
        fact_calls.append(kwargs)
        admitted = fact_accept(self, session, **kwargs)
        assert admitted[0].quality == "accepted"
        return admitted

    monkeypatch.setattr(ResearchEvidenceService, "accept_candidates_in_session", evidence_spy)
    monkeypatch.setattr(ResearchFactStore, "accept_facts_in_session", fact_spy)
    unused_data_dir = tmp_path / "must-not-create-default-data"
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(unused_data_dir))
    report = await _run(tmp_path, case)

    assert len(evidence_calls) == len(fact_calls) == 1
    assert evidence_calls[0]["cutoff_at"] == fact_calls[0]["cutoff_at"] == case.cutoff_at
    assert report["raw_evidence_quality_counts"] == {"candidate": 1}
    assert report["accepted_evidence_count"] == report["accepted_fact_count"] == 1
    assert report["hard_coverage_ratio"] == 1
    admitted = report["evidence_admission"][0]
    archived = case.archived_capability_fixtures[0].evidence_candidates[0]
    assert admitted["source_id"] == archived.source_id
    assert admitted["source_ref"] == "fed.monetary_policy"
    assert archived.published_at is not None
    assert admitted["published_at"] == archived.published_at.isoformat()
    assert admitted["observed_at"] == archived.observed_at.isoformat()
    assert admitted["received_at"] == archived.received_at.isoformat()
    assert admitted["content_hash"] == archived.content_hash
    assert case.model_dump(mode="json") == original
    assert not unused_data_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["missing_publication", "stale", "search_locator"])
async def test_kernel_keeps_missing_publication_stale_and_search_material_unusable(
    tmp_path: Path, condition: str,
) -> None:
    case = _single_case()
    candidate = case.archived_capability_fixtures[0].evidence_candidates[0]
    changes: dict[str, dict[str, Any]] = {
        "missing_publication": {"published_at": None},
        "stale": {"published_at": case.cutoff_at - timedelta(days=8)},
        "search_locator": {"authority": "search_derived"},
    }
    case = _single_case(**changes[condition])
    report = await _run(tmp_path, case)

    assert report["hard_coverage_ratio"] == report["accepted_evidence_count"] == 0
    assert report["retained_evidence_count"] == 1
    admitted = report["evidence_admission"][0]
    assert admitted["quality"] == ("stale" if condition == "stale" else "candidate")
    assert admitted["observed_at"] == candidate.observed_at.isoformat()
    if condition == "missing_publication":
        assert admitted["published_at"] is None
        assert admitted["freshness_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["future_receipt", "publication_after_observation"])
async def test_gateway_rejects_invalid_fixture_pit_without_rewriting_dates(
    tmp_path: Path, condition: str,
) -> None:
    case = _single_case()
    candidate = case.archived_capability_fixtures[0].evidence_candidates[0]
    changes: dict[str, dict[str, Any]] = {
        "future_receipt": {"received_at": case.cutoff_at + timedelta(seconds=1)},
        "publication_after_observation": {
            "published_at": candidate.observed_at + timedelta(seconds=1),
        },
    }
    report = await _run(tmp_path, _single_case(**changes[condition]))

    assert report["hard_coverage_ratio"] == report["retained_evidence_count"] == 0
    assert report["admission_failures"] == [{
        "requirement_id": "event_identity", "error_code": "research_pit_violation",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requirement_id", "url", "error_code"),
    [
        ("crypto_spot_confirmation", None, "research_source_unknown"),
        ("crypto_spot_confirmation", "https://api.binance.com/api/v3/ticker/price",
         "research_source_not_approved"),
        ("event_identity", "https://www.reuters.com/world/test",
         "research_source_not_approved"),
    ],
)
async def test_original_registry_denies_unknown_archives_typed_only_and_unapproved_sources(
    tmp_path: Path, requirement_id: str, url: str | None, error_code: str,
) -> None:
    changes = {"source_url": AnyUrl(url)} if url is not None else {}
    report = await _run(tmp_path, _single_case(requirement_id, **changes))

    assert report["hard_coverage_ratio"] == report["retained_evidence_count"] == 0
    assert report["accepted_fact_count"] == 0
    assert report["source_denials"][0]["error_code"] == error_code
    assert report["admission_failures"] == []


@pytest.mark.asyncio
async def test_fact_failure_rolls_back_evidence_and_cannot_contribute_to_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _with_official_fact(_single_case())
    archive = load_replay_archive(_archive(tmp_path, case))
    manifest = audit_tool._replay_manifest(PACK)
    adapter = ReplayResearchCapabilityAdapter(
        capability_id=manifest.capability_id, fixtures=archive.queries,
        requirement_fixtures=archive.requirements,
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest], [adapter], enabled_capabilities=[manifest.capability_id],
        clock=lambda: case.cutoff_at,
    )
    accept = ResearchFactStore.accept_facts_in_session

    def fail_after_actual_fact_admission(
        self: ResearchFactStore, session: Session, **kwargs: Any,
    ) -> list[FactEnvelope]:
        admitted = accept(self, session, **kwargs)
        assert admitted[0].quality == "accepted"
        raise ValueError("test_fact_persistence_failure")

    monkeypatch.setattr(
        ResearchFactStore, "accept_facts_in_session", fail_after_actual_fact_admission,
    )
    database = audit_tool._AuditDatabase()
    try:
        report = await audit_tool._audit_case_in_database(
            case, gateway, ResearchSourceRegistry.from_pack(PACK), database,
        )
        with database.session() as session:
            assert session.query(ResearchEvidenceRecord).count() == 0
            assert session.query(ResearchFactRecord).count() == 0
        assert report["hard_coverage_ratio"] == report["accepted_evidence_count"] == 0
        assert report["retained_fact_count"] == 0
        assert report["admission_failures"] == [{
            "requirement_id": "event_identity", "error_code": "test_fact_persistence_failure",
        }]
    finally:
        database.engine.dispose()
