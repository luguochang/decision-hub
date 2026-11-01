"""Real offline v2 Graph publication and assessment; no Provider or Gateway claim.

Only the timing of an in-writer SQL mutation is injected. Citation, Graph,
publication and source authentication always run their real implementation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Generator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.evals.analytical_assessment import assess_outlook_in_session, stable_hub_recipe
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ForecastRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import OWNER
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_graph_citation_membership_v2 import (
    LOCATOR_B,
    SPOT_A,
    _snapshot_final,  # pyright: ignore[reportPrivateUsage]
    snapshot_inputs,
)
from tests.research.test_research_publication_consumer_v2 import (
    _sql,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


class _SnapshotProducer(Protocol):
    __wrapped__: Callable[[Path], Generator[PreparedInputs]]


class _NetworkFixture(Protocol):
    __wrapped__: Callable[[pytest.MonkeyPatch], None]


@dataclass(frozen=True)
class Draft:
    source: PreparedInputs
    final: binding.FrozenGraphFinal
    reviewed: ResearchSessionResult


def _renew_draft_lease(source: PreparedInputs) -> None:
    """Renew a still-live original claim without changing its owner or provenance."""
    with source.database.session() as session:
        run = session.get(RunRecord, source.request.run_id)
        assert run is not None and run.status == "running"
        assert run.lease_owner == OWNER and run.artifact_id is None
        expires_at = as_utc(run.lease_expires_at)
        assert expires_at is not None and expires_at > utcnow()
    started_at = utcnow()
    assert RunService(source.database).renew_lease(
        source.request.run_id, OWNER, lease_seconds=900,
    )
    completed_at = utcnow()
    with source.database.session() as session:
        run = session.get(RunRecord, source.request.run_id)
        assert run is not None and run.status == "running"
        assert run.lease_owner == OWNER and run.artifact_id is None
        expires_at = as_utc(run.lease_expires_at)
        assert expires_at is not None
        assert started_at + timedelta(seconds=900) <= expires_at
        assert expires_at <= completed_at + timedelta(seconds=900)


@pytest.fixture(scope="module")
async def genuine_drafts(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[dict[str, Draft]]:
    """Three actual one-dispatch producers, then isolated SQLite backups per test.

    Both accepted runs use the same recipe but different real Run/Task identities.
    The third run has only the related candidate locator in its top-level refs.
    No source or admission verdict is mocked; the upstream data is typed offline.
    """
    network = pytest.MonkeyPatch()
    cast(_NetworkFixture, forbid_external_connections).__wrapped__(network)
    producers: list[Generator[PreparedInputs]] = []
    drafts: dict[str, Draft] = {}
    try:
        for name, top_ref in (("accepted", SPOT_A), ("peer", SPOT_A), ("rejected", LOCATOR_B)):
            directory = tmp_path_factory.mktemp(f"citation-publication-{name}")
            producer = cast(_SnapshotProducer, snapshot_inputs).__wrapped__(directory)
            producers.append(producer)
            source = next(producer)
            result = await _snapshot_final(source, directory, top_ref=top_ref)
            _renew_draft_lease(source)
            recorded = json.loads((directory / "snapshot-citation-original.json").read_text())
            reference = binding.parse_graph_final_ref(recorded["graph_final_ref"])
            with source.database.session() as session:
                final = binding.read_graph_final_in_session(
                    session, run_id=source.request.run_id, reference=reference,
                    expected_lease_owner=OWNER, require_current_permission=True,
                )
            assert final.result == result
            assert final.receipt.schema_version == "research-graph-final-projection.v2"
            review = ResearchReviewService(source.database).review(
                source.request.run_id, result,
                graph_final_ref=reference, expected_lease_owner=OWNER,
            )
            drafts[name] = Draft(source, final, result.model_copy(update={"reviews": [review]}))
        yield drafts
    finally:
        for producer in reversed(producers):
            producer.close()
        network.undo()


def _copy_draft(original: Draft, directory: Path) -> Draft:
    # The module's real drafts remain claimed while later tests authenticate
    # isolated copies. Renew before taking the copy and its rollback baseline.
    _renew_draft_lease(original.source)
    source_path = original.source.database.sqlite_path
    assert source_path is not None
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "copy.sqlite3"
    with sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True) as reader:
        with sqlite3.connect(destination) as writer:
            reader.backup(writer)
    database = Database(f"sqlite+pysqlite:///{destination.as_posix()}")
    return replace(original, source=replace(original.source, database=database))


@pytest.fixture
def draft(genuine_drafts: dict[str, Draft], tmp_path: Path) -> Generator[Draft]:
    copied = _copy_draft(genuine_drafts["accepted"], tmp_path)
    try:
        yield copied
    finally:
        copied.source.database.engine.dispose()


def _publish(draft: Draft) -> str:
    source = draft.source
    artifact_id, _ = CommitDecisionService(source.database).commit_research_result(
        source.request.run_id, source.request.event_id, draft.reviewed,
        graph_final_ref=draft.final.reference, expected_lease_owner=OWNER,
    )
    return artifact_id


def _originals(source: PreparedInputs) -> dict[str, Any]:
    with source.database.session() as session:
        return {
            "roles": {
                row.output_id: (row.role, row.payload_hash, row.payload_json)
                for row in session.scalars(select(RunRoleOutputRecord))
            },
            "evidence": {
                row.evidence_id: {
                    column.key: getattr(row, column.key)
                    for column in ResearchEvidenceRecord.__table__.columns
                }
                for row in session.scalars(select(ResearchEvidenceRecord))
            },
        }


def test_real_v2_publication_authenticates_once_without_rewriting_originals(
    draft: Draft, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _originals(draft.source)
    _publish(draft)
    after_publication = _originals(draft.source)
    assert after_publication["evidence"] == original["evidence"]
    for output_id, body in original["roles"].items():
        assert after_publication["roles"][output_id] == body
    reader = binding.read_graph_final_in_session
    reads: list[tuple[str, str | None, bool]] = []

    def counted(
        session: Session, *, run_id: str, reference: binding.GraphFinalProjectionRef,
        expected_lease_owner: str | None, require_current_permission: bool,
    ) -> binding.FrozenGraphFinal:
        reads.append((run_id, expected_lease_owner, require_current_permission))
        return reader(
            session, run_id=run_id, reference=reference,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=require_current_permission,
        )

    monkeypatch.setattr(binding, "read_graph_final_in_session", counted)
    before_assessment = _sql(draft.source)
    with draft.source.database.session() as session:
        outlooks = session.scalars(select(AnalyticalOutlookRecord)).all()
        assert {row.horizon for row in outlooks} == {"30m", "24h", "72h"}
        assessed = assess_outlook_in_session(
            session, outlooks[0].outlook_id, assessed_at=utcnow(),
        )
        assert assessed.source_status == assessed.method_status == "verified"
        assert assessed.source_reason is None and len(assessed.stable_recipe_hash or "") == 64
        assert assessed.label_status == "not_due" and not assessed.directional_usable
        assert "non_live_research_source" in assessed.limitations
        assert not session.new and not session.dirty and not session.deleted
    assert reads == [(draft.source.request.run_id, None, False)]
    assert _sql(draft.source) == before_assessment


@pytest.mark.parametrize("column,value", [("quality", "candidate"), ("source_id", "changed")])
def test_commit_final_flush_rechecks_real_material_and_rolls_back(
    draft: Draft, monkeypatch: pytest.MonkeyPatch, column: str, value: str,
) -> None:
    """Inject only SQL timing: the actual late checker must reject its changed input.

    The first real check passes. Mutation occurs after Result/Artifact/source
    flush, immediately before the second real check. No original/checker verdict
    is replaced, so omitting that late call would make this test incorrectly pass
    publication and fail the assertion below.
    """
    checker = binding.verify_graph_citation_membership_in_session
    calls: list[tuple[int, int, str | None]] = []
    originals = _originals(draft.source)
    before = _sql(draft.source)

    def mutate_before_check(
        session: Session, *, run_id: str, reference: binding.GraphFinalProjectionRef,
        expected_lease_owner: str | None, expected_artifact_id: str | None = None,
    ) -> binding.FrozenGraphFinal:
        driver = session.connection().connection.driver_connection
        calls.append((id(session), id(driver), expected_artifact_id))
        if expected_artifact_id is not None:
            run = session.get(RunRecord, run_id, populate_existing=True)
            artifact = session.get(ArtifactRecord, expected_artifact_id)
            assert run is not None and run.status == "running"
            assert run.artifact_id == expected_artifact_id and run.lease_owner == OWNER
            assert artifact is not None and artifact.run_id == run_id
            current = session.get(ResearchEvidenceRecord, SPOT_A, populate_existing=True)
            assert current is not None and current.quality == "accepted"
            changed = session.execute(update(ResearchEvidenceRecord).where(
                ResearchEvidenceRecord.evidence_id == SPOT_A,
            ).values({column: value}).execution_options(synchronize_session=False))
            assert getattr(changed, "rowcount", None) == 1
        return checker(
            session, run_id=run_id, reference=reference,
            expected_lease_owner=expected_lease_owner,
            expected_artifact_id=expected_artifact_id,
        )

    monkeypatch.setattr(binding, "verify_graph_citation_membership_in_session", mutate_before_check)
    with pytest.raises(ValueError):
        _publish(draft)
    assert len(calls) == 2 and calls[0][2] is None and calls[1][2] is not None
    assert calls[0][:2] == calls[1][:2], "Both checks must borrow the same physical writer"
    assert _sql(draft.source) == before
    assert _originals(draft.source) == originals


def test_first_rejected_candidate_saves_evidence_only_and_no_directional_outlook(
    genuine_drafts: dict[str, Draft], tmp_path: Path,
) -> None:
    rejected = _copy_draft(genuine_drafts["rejected"], tmp_path)
    try:
        receipt = rejected.final.receipt
        assert isinstance(receipt, binding.ResearchGraphFinalProjectionV2)
        assert receipt.latest_citation_disposition == "rejected"
        assert receipt.selected_synthesis_observation_ref is None
        assert rejected.final.result.causal_case is None and rejected.final.result.horizons == []
        originals = _originals(rejected.source)
        artifact_id = _publish(rejected)
        with rejected.source.database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact is not None
            assert session.query(AnalyticalOutlookRecord).count() == 0
            assert all(
                row.direction == "no_trade" for row in session.scalars(select(ForecastRecord))
            )
            assert LOCATOR_B not in json.dumps(json.loads(artifact.payload_json)["citations"])
        after = _originals(rejected.source)
        assert after["evidence"] == originals["evidence"]
        assert after["evidence"][LOCATOR_B]["quality"] == "candidate"
        for output_id, body in originals["roles"].items():
            assert after["roles"][output_id] == body
    finally:
        rejected.source.database.engine.dispose()


def test_resigned_v2_graph_body_never_becomes_a_certified_direction(draft: Draft) -> None:
    _publish(draft)
    with draft.source.database.session() as session:
        row = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.role == "research_graph_final",
        ))
        assert row is not None
        body = json.loads(row.payload_json)
        body["projected_result"]["causal_case"]["evidence_refs"] = [LOCATOR_B]
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        parsed = binding.parse_graph_final_projection_declaration(
            payload, schema_version=row.schema_version,
        )
        assert parsed.projected_result.causal_case is not None
        assert parsed.projected_result.causal_case.evidence_refs == [LOCATOR_B]
        row.payload_json = payload
        row.payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    before = _sql(draft.source)
    with draft.source.database.session() as session:
        outlook = session.scalars(select(AnalyticalOutlookRecord)).first()
        assert outlook is not None
        assessed = assess_outlook_in_session(session, outlook.outlook_id, assessed_at=utcnow())
        assert assessed.source_status == "invalid" and assessed.method_status == "unverifiable"
        assert assessed.source_reason == "assessment_graph_reference_mismatch"
        assert assessed.stable_recipe_hash is None and not assessed.directional_usable
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(draft.source) == before


def test_authenticated_v2_recipe_is_instance_free_and_keeps_v1_identity_separate(
    genuine_drafts: dict[str, Draft], tmp_path: Path,
) -> None:
    hashes: list[str] = []
    run_ids: list[str] = []
    for name in ("accepted", "peer"):
        copied = _copy_draft(genuine_drafts[name], tmp_path / name)
        try:
            _publish(copied)
            run_ids.append(copied.source.request.run_id)
            with copied.source.database.session() as session:
                outlook = session.scalars(select(AnalyticalOutlookRecord)).first()
                assert outlook is not None
                assessed = assess_outlook_in_session(
                    session, outlook.outlook_id, assessed_at=utcnow(),
                )
                assert assessed.source_status == assessed.method_status == "verified"
                assert assessed.stable_recipe_hash is not None
                method = read_prepared_execution_in_session(
                    session, copied.source.request.run_id,
                ).method.definition.model_dump(mode="json")
                policy = session.scalar(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.role == "research_compiled_policy",
                ))
                assert policy is not None
                compiled = json.loads(policy.payload_json)["compiled"]
                v1_recipe = stable_hub_recipe(method, compiled)
                v2_recipe = {
                    **v1_recipe,
                    "graph_recipe": binding.authenticated_graph_recipe_identity(
                        copied.final.receipt,
                    ),
                }
                assert assessed.stable_recipe_hash == recipe_hash(v2_recipe)
                # The v1 algorithm is deliberately unchanged. Its compatibility
                # seed is authenticated separately in test_analytical_assessment.
                assert assessed.stable_recipe_hash != recipe_hash(v1_recipe)
                hashes.append(assessed.stable_recipe_hash)
        finally:
            copied.source.database.engine.dispose()
    assert run_ids[0] != run_ids[1] and hashes[0] == hashes[1]
