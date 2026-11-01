"""Original-source authentication and independent archived labels; offline only."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.hub_worker import research as factory_module
from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.evals.analytical_assessment import (
    assess_outlook_in_session,
    stable_hub_recipe,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
    ResearchGraphFinalProjection,
    parse_graph_final_ref,
    read_graph_final_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.evals.test_prospective_observations import archived_case
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    _active,  # pyright: ignore[reportPrivateUsage] # Existing lawful test lease.
    _prepared_inputs,  # pyright: ignore[reportPrivateUsage] # Test Pack frozen by real Factory.
)
from tests.research.test_analytical_outlooks import NOW, prepared, publish
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_publication_consumer_v2 import (
    _sql,  # pyright: ignore[reportPrivateUsage] # Existing original-row snapshot.
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@dataclass(frozen=True)
class Published:
    source: PreparedInputs
    outlook_id: str


class _NetworkFixture(Protocol):
    __wrapped__: Callable[[pytest.MonkeyPatch], None]


@pytest.fixture(scope="module")
async def published_seed(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Published]:
    """One bounded controlled Graph publication, never a signed success injection.

    The isolated test Pack declares all event inquiries soft before Factory
    freezes it. MaterialHost supplies typed evidence/facts through the real
    Runtime and Graph writers. This replay seed tests source authentication;
    it cannot demonstrate a complete production inquiry or earn a direction score.
    Every mutation test gets an independent SQLite backup of these original rows.
    """
    directory = tmp_path_factory.mktemp("analytical-original-seed")
    producer = _prepared_inputs(
        directory, max_rounds=1, max_repairs=1, repair_soft_scope=True,
    )

    def historical_creation_scope(
        _session: Session, _run_id: str,
    ) -> AbstractContextManager[None]:
        return nullcontext()

    def omit_new_selection(
        _session: Session, *, run_id: str, recorded_at: datetime,
    ) -> None:
        return None

    # Produce the historical v1 fixture before the new recipe sidecar existed.
    # This short creation seam never deletes or rewrites a frozen sidecar;
    # Runtime, Graph and publication verification below remain genuine.
    with pytest.MonkeyPatch.context() as legacy_factory:
        legacy_factory.setattr(
            factory_module, "graph_recipe_task_creation_scope",
            historical_creation_scope,
        )
        legacy_factory.setattr(
            factory_module, "freeze_graph_recipe_selection_in_session",
            omit_new_selection,
        )
        inputs = next(producer)
    # A module producer runs before function-scoped autouse fixtures. Apply the
    # same network denial for its complete lifetime, including Windows loop wakeup.
    network_patch = pytest.MonkeyPatch()
    cast(_NetworkFixture, forbid_external_connections).__wrapped__(network_patch)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), MaterialHost(inputs), poll_interval_seconds=0.001,
    )
    runtime_closed = False
    try:
        graph = build_agentic_research_graph(
            runtime, ResearchEvidenceService(inputs.database), expected_lease_owner=OWNER,
        )
        state = await graph.ainvoke(initial_research_state(_active(inputs)))
        reference = GraphFinalProjectionRef.model_validate_json(
            json.dumps(state["graph_final_projection_ref"]),
        )
        with inputs.database.session() as session:
            final = read_graph_final_in_session(
                session, run_id=inputs.request.run_id, reference=reference,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
        assert final.result.horizons, "Controlled real Graph must actually produce Outlooks"
        review = ResearchReviewService(inputs.database).review(
            inputs.request.run_id, final.result, graph_final_ref=reference,
            expected_lease_owner=OWNER,
        )
        reviewed = final.result.model_copy(update={"reviews": [review]})
        CommitDecisionService(inputs.database).commit_research_result(
            inputs.request.run_id, inputs.request.event_id, reviewed,
            graph_final_ref=reference, expected_lease_owner=OWNER,
        )
        with inputs.database.session() as session:
            outlook = session.query(AnalyticalOutlookRecord).filter_by(horizon="30m").one()
            assert session.query(AnalyticalOutlookRecord).count() == 3
            identity = outlook.outlook_id
        await runtime.close()
        runtime_closed = True
        yield Published(inputs, identity)
    finally:
        if not runtime_closed:
            await runtime.close()
        network_patch.undo()
        producer.close()


@pytest.fixture
def published(published_seed: Published, tmp_path: Path) -> Generator[Published]:
    source_path = published_seed.source.database.sqlite_path
    assert source_path is not None
    copied_path = tmp_path / "originals-copy.sqlite3"
    # Backup includes the seed's committed WAL without touching seed rows.
    with sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True) as original:
        with sqlite3.connect(copied_path) as copied:
            original.backup(copied)
    database = Database(f"sqlite+pysqlite:///{copied_path.as_posix()}")
    try:
        yield Published(
            replace(published_seed.source, database=database), published_seed.outlook_id,
        )
    finally:
        database.engine.dispose()


def _row(session: Session, source: PreparedInputs, role: str) -> RunRoleOutputRecord:
    return session.query(RunRoleOutputRecord).filter_by(
        run_id=source.request.run_id, role=role,
    ).one()


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _end(outlook: AnalyticalOutlookRecord) -> datetime:
    end = as_utc(outlook.window_end_at)
    assert end is not None
    return end


async def test_genuine_graph_publication_authenticates_without_rewriting_or_today_pack(
    published: Published,
) -> None:
    source = published.source
    before = _sql(source)
    with source.database.session() as session:
        method = read_prepared_execution_in_session(session, source.request.run_id).method
        graph = ResearchGraphFinalProjection.model_validate_json(
            _row(session, source, "research_graph_final").payload_json,
        )
        saved = session.get(ResearchResultRecord, source.request.run_id)
        assert saved is not None
        persisted = ResearchSessionResult.model_validate_json(saved.payload_json)
        identity = (method.definition.profile_ref, method.definition.runtime_version)
        assert identity == ("decision-research.web.v1", "dsh-web-0.1.0+222222222222")
        assert (
            graph.projected_result.profile_ref, graph.projected_result.runtime_version
        ) == identity
        assert (persisted.profile_ref, persisted.runtime_version) == identity
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert assessed.source_status == assessed.method_status == "verified"
        assert assessed.source_reason is None and len(assessed.stable_recipe_hash or "") == 64
        assert assessed.selection_status == "unchecked" and assessed.label_status == "not_due"
        assert not assessed.directional_usable
        assert "non_live_research_source" in assessed.limitations
        assert {ref.role for ref in assessed.source_refs} >= {
            "research_task_request", "research_execution_method", "research_graph_final",
            "research_publication_source", "research_result",
        }
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(source) == before


@pytest.mark.parametrize("result_kind", ["declared", "persisted"])
@pytest.mark.parametrize("field", ["profile_ref", "runtime_version"])
async def test_result_method_identity_guard_rejects_drift_after_real_original_verification(
    published: Published, monkeypatch: pytest.MonkeyPatch, result_kind: str, field: str,
) -> None:
    """Isolate this layer after the real prior checks, without corrupting SQLite.

    The seam delegates the original Outlook verifier first. Only then does it
    change one decoded Result DTO; all original SHA, execution, Graph, review
    and publication checks have really run against the genuine seed. This
    targeted projection fault is not a claim that Kernel accepted altered rows.
    """
    from packages.evals import analytical_assessment as assessment_module

    source = published.source
    before = _sql(source)
    original_verify = assessment_module._verify_outlook  # pyright: ignore[reportPrivateUsage]
    original_recipe = assessment_module.stable_hub_recipe
    verified: list[str] = []
    recipe_calls: list[bool] = []

    def change_verified_projection(
        session: Session, outlook: AnalyticalOutlookRecord, run: RunRecord,
        persisted: ResearchSessionResult, declared: ResearchSessionResult,
    ) -> None:
        original_verify(session, outlook, run, persisted, declared)
        verified.append(outlook.outlook_id)
        target = declared if result_kind == "declared" else persisted
        setattr(target, field, "changed-after-real-source-verification")

    def counted_recipe(definition: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
        recipe_calls.append(True)
        return original_recipe(definition, compiled)

    monkeypatch.setattr(assessment_module, "_verify_outlook", change_verified_projection)
    monkeypatch.setattr(assessment_module, "stable_hub_recipe", counted_recipe)
    with source.database.session() as session:
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert verified == [published.outlook_id] and not recipe_calls
        assert assessed.source_status == "invalid" and assessed.method_status == "unverifiable"
        assert assessed.source_reason == f"assessment_{result_kind}_method_identity_mismatch"
        assert assessed.stable_recipe_hash is None and not assessed.source_refs
        assert not assessed.directional_usable
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(source) == before


@pytest.mark.parametrize("role", ["research_execution_method", "research_graph_final"])
async def test_result_method_identity_guard_keeps_original_raw_sha_precondition(
    published: Published, monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    from packages.evals import analytical_assessment as assessment_module

    source = published.source
    with source.database.session() as session:
        row = _row(session, source, role)
        # The JSON remains parseable and semantically identical. Its original
        # byte hash is deliberately retained, so the real raw SHA check fails.
        row.payload_json += " "
        json.loads(row.payload_json)
        assert row.payload_hash != _sha(row.payload_json)
    before = _sql(source)
    original_verify = assessment_module._verify_outlook  # pyright: ignore[reportPrivateUsage]
    reached: list[str] = []

    def counted_verify(
        session: Session, outlook: AnalyticalOutlookRecord, run: RunRecord,
        persisted: ResearchSessionResult, declared: ResearchSessionResult,
    ) -> None:
        reached.append(outlook.outlook_id)
        original_verify(session, outlook, run, persisted, declared)

    monkeypatch.setattr(assessment_module, "_verify_outlook", counted_verify)
    with source.database.session() as session:
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert not reached
        assert assessed.source_status == "invalid" and assessed.method_status == "unverifiable"
        assert assessed.source_reason == "assessment_source_receipt_invalid"
        assert assessed.stable_recipe_hash is None and not assessed.source_refs
        assert not assessed.directional_usable
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(source) == before


def test_saved_publication_json_ref_uses_json_boundary_without_weakening_strict_dto(
    published: Published,
) -> None:
    with published.source.database.session() as session:
        body = json.loads(_row(
            session, published.source, "research_publication_source",
        ).payload_json)
        saved_ref = body["graph_final_ref"]
        # The original stored JSON has timestamp strings. Native Python inputs
        # remain strict; the existing JSON decoder accepts the lawful boundary.
        with pytest.raises(ValidationError):
            GraphFinalProjectionRef.model_validate(saved_ref)
        reference = parse_graph_final_ref(saved_ref)
        assert reference.model_dump(mode="json") == saved_ref
        assessed = assess_outlook_in_session(
            session, published.outlook_id, assessed_at=utcnow(),
        )
        assert assessed.source_status == assessed.method_status == "verified"
        assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("role", [
    "research_task_request", "research_execution_method", "research_graph_final",
    "research_publication_source",
])
async def test_resigned_original_does_not_gain_authenticated_provenance(
    published: Published, role: str,
) -> None:
    source = published.source
    with source.database.session() as session:
        row = _row(session, source, role)
        body = json.loads(row.payload_json)
        if role == "research_execution_method":
            body["definition"]["runtime_version"] = "changed-after-publication"
            body["method_hash"] = recipe_hash(body["definition"])
        else:
            body["run_id"] = "another-run"
        row.payload_json = json.dumps(body, ensure_ascii=False)
        row.payload_hash = _sha(row.payload_json)
    before = _sql(source)
    with source.database.session() as session:
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert assessed.source_status == "invalid" and assessed.method_status == "unverifiable"
        assert assessed.source_reason and assessed.stable_recipe_hash is None
        assert assessed.label_status == "not_due" and not assessed.directional_usable
    assert _sql(source) == before


@pytest.mark.parametrize("fault", ["missing_method", "result", "artifact", "eligibility"])
async def test_missing_or_changed_publication_originals_never_count(
    published: Published, fault: str,
) -> None:
    source = published.source
    with source.database.session() as session:
        if fault == "missing_method":
            session.delete(_row(session, source, "research_execution_method"))
        elif fault == "result":
            row = session.get(ResearchResultRecord, source.request.run_id)
            assert row is not None
            body = json.loads(row.payload_json)
            body["runtime_version"] = "changed-after-publication"
            row.payload_json, row.payload_hash = json.dumps(body), _sha(json.dumps(body))
        elif fault == "artifact":
            outlook = session.get(AnalyticalOutlookRecord, published.outlook_id)
            assert outlook is not None
            artifact = session.get(ArtifactRecord, outlook.artifact_id)
            assert artifact is not None
            artifact.headline = "Changed after publication"
        else:
            outlook = session.get(AnalyticalOutlookRecord, published.outlook_id)
            assert outlook is not None
            outlook.eligibility = "eligible"
            outlook.exclusion_reasons_json = "[]"
    with source.database.session() as session:
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        expected = "unverifiable" if fault == "missing_method" else "invalid"
        assert assessed.source_status == expected
        assert not assessed.directional_usable and assessed.stable_recipe_hash is None


async def test_stable_recipe_excludes_run_bindings_but_keeps_policy_and_selector(
    published: Published,
) -> None:
    with published.source.database.session() as session:
        definition: dict[str, Any] = json.loads(
            _row(session, published.source, "research_execution_method").payload_json,
        )["definition"]
        compiled: dict[str, Any] = json.loads(
            _row(session, published.source, "research_compiled_policy").payload_json,
        )["compiled"]
    original = recipe_hash(stable_hub_recipe(definition, compiled))
    other = copy.deepcopy(definition)
    other.update(deadline_at="2099-01-01T00:00:00Z", request_pack_hash="changed-run-pack")
    other["bundle"] = {"different_run": True}
    other["research_policy_prompt_hash"] = "different-input-prompt"
    other["policy_binding"].update(
        run_id="another-run", compiled_policy_hash="different-scope",
        full_task_hash="different-task", binding_hash="different-binding",
    )
    assert recipe_hash(stable_hub_recipe(other, compiled)) == original
    other["policy_binding"]["checker_code_hash"] = "f" * 64
    assert recipe_hash(stable_hub_recipe(other, compiled)) != original
    changed_resources = copy.deepcopy(compiled)
    changed_resources["gate_resource"]["raw_sha256"] = "e" * 64
    assert recipe_hash(stable_hub_recipe(definition, changed_resources)) != original
    # Pure V3 projection controls; this alone does not authenticate a V3 Run.
    v3 = copy.deepcopy(definition)
    v3["schema_version"] = "research-execution-method.v3"
    v3["context_selection_binding"] = {
        "mode": "required_before_rpc", "selection_schema": "research-context-selection.v3",
        "ranking_rules_ref": "relevance-authority-recency.v2",
        "time_rule_ref": "received-cutoff.v1",
        "first_selection_payload_hash": "run-specific",
    }
    first = recipe_hash(stable_hub_recipe(v3, compiled))
    v3["context_selection_binding"]["first_selection_payload_hash"] = "another-run"
    assert recipe_hash(stable_hub_recipe(v3, compiled)) == first
    v3["context_selection_binding"]["ranking_rules_ref"] = "changed-selector.v3"
    assert recipe_hash(stable_hub_recipe(v3, compiled)) != first


@pytest.mark.parametrize("family,status", [
    ("ai_synthesis", "pending"), ("ai_synthesis", "retry"), ("daily_rule", "pending"),
])
def test_legacy_and_daily_keep_real_observation_progress_without_source_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str, status: str,
) -> None:
    database, research = prepared(tmp_path, monkeypatch)
    publish(database, research)
    with database.session() as session:
        outlook = session.query(AnalyticalOutlookRecord).filter_by(horizon="30m").one()
        outlook.method_family = family
        observation = session.get(AnalyticalObservationRecord, outlook.outlook_id)
        assert observation is not None
        observation.status, observation.error_code = status, "retry_original_receipt" if (
            status == "retry"
        ) else None
        identity, now = outlook.outlook_id, _end(outlook) + timedelta(minutes=1)
    with database.session() as session:
        assessed = assess_outlook_in_session(session, identity, assessed_at=now)
        assert assessed.source_status == assessed.method_status == "unverifiable"
        assert assessed.label_status == assessed.observation_status == status
        assert not assessed.directional_usable and assessed.source_reason
        if family == "daily_rule":
            assert assessed.source_reason == "daily_rule_original_assessment_not_implemented"
        before_due = assess_outlook_in_session(session, identity, assessed_at=NOW)
        assert before_due.label_status == "not_due"
        with pytest.raises(ValueError, match="assessment_time_timezone_required"):
            assess_outlook_in_session(session, identity, assessed_at=now.replace(tzinfo=None))


@pytest.mark.parametrize("fault", ["none", "label_archive", "saved_price", "input_archive"])
async def test_archived_label_recomputed_independently_of_unverified_legacy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    database, now = await archived_case(tmp_path, monkeypatch, action="long")
    archive = RawBodyArchive(tmp_path / "raw-captures")
    with database.session() as session:
        outlook = session.query(AnalyticalOutlookRecord).filter_by(horizon="30m").one()
        identity = outlook.outlook_id
        if fault in {"label_archive", "input_archive"}:
            capture = session.get(RawCaptureRecord, "exit" if fault == "label_archive" else "input")
            assert capture is not None
            assert capture.body_hash is not None
            (archive.root / capture.body_hash[:2] / capture.body_hash).write_bytes(b"corrupt")
        elif fault == "saved_price":
            observation = session.get(AnalyticalObservationRecord, identity)
            assert observation is not None
            observation.market_return_pct = 99
    with database.session() as session:
        assessed = assess_outlook_in_session(session, identity, assessed_at=now, archive=archive)
        assert assessed.source_status == "unverifiable" and not assessed.directional_usable
        if fault in {"label_archive", "saved_price"}:
            assert assessed.label_status == "invalid" and assessed.label_reason
        else:
            assert assessed.label_status == "verified"
            assert assessed.verified_direction_correct is True
            assert assessed.verified_directional_return_pct == pytest.approx(2)
            assert assessed.label_capture_ids == ["entry", "exit"]
            if fault == "input_archive":
                assert "frozen_input_unverified" in assessed.limitations and assessed.label_reason
        assert not session.new and not session.dirty and not session.deleted


async def test_missing_input_archive_does_not_erase_verified_market_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, now = await archived_case(tmp_path, monkeypatch)
    with database.session() as session:
        for row in session.query(ParsedVersionRecord).all():
            row.capture_ids_json = "[]"
        identity = session.query(AnalyticalOutlookRecord).filter_by(horizon="30m").one().outlook_id
    with database.session() as session:
        assessed = assess_outlook_in_session(
            session, identity, assessed_at=now, archive=RawBodyArchive(tmp_path / "raw-captures"),
        )
        assert assessed.label_status == "verified" and assessed.verified_direction_correct is None
        assert "frozen_input_unverified" in assessed.limitations and not assessed.directional_usable


async def test_assessment_authenticates_graph_once_and_keeps_original_source_hashes(
    published: Published, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.evals import analytical_assessment as assessment_module
    from packages.kernel.decision_hub_kernel.application import research_graph_binding
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        FrozenGraphFinal,
    )

    source = published.source
    original_reader = research_graph_binding.read_graph_final_in_session
    calls: list[tuple[str, GraphFinalProjectionRef, str | None, bool]] = []

    def counted_read(
        session: Session, *, run_id: str, reference: GraphFinalProjectionRef,
        expected_lease_owner: str | None, require_current_permission: bool,
    ) -> FrozenGraphFinal:
        calls.append((run_id, reference, expected_lease_owner, require_current_permission))
        return original_reader(
            session, run_id=run_id, reference=reference,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=require_current_permission,
        )

    monkeypatch.setattr(research_graph_binding, "read_graph_final_in_session", counted_read)
    # Count an old directly imported alias too, so the regression would fail
    # if the redundant first deep read were accidentally restored.
    if "read_graph_final_in_session" in vars(assessment_module):
        monkeypatch.setattr(assessment_module, "read_graph_final_in_session", counted_read)
    before = _sql(source)
    with source.database.session() as session:
        source_row = _row(session, source, "research_publication_source")
        reference = parse_graph_final_ref(json.loads(source_row.payload_json)["graph_final_ref"])
        expected = {row.role: row.payload_hash for row in session.query(RunRoleOutputRecord)
                    .filter_by(run_id=source.request.run_id)}
        saved = session.get(ResearchResultRecord, source.request.run_id)
        assert saved is not None
        expected["research_result"] = saved.payload_hash
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert calls == [(source.request.run_id, reference, None, False)]
        assert assessed.source_status == assessed.method_status == "verified"
        assert assessed.source_reason is None and assessed.stable_recipe_hash
        assert len(assessed.source_refs) == 8
        assert {ref.role: ref.payload_hash for ref in assessed.source_refs} == {
            role: expected[role] for role in (
                "research_request_pack", "research_task_request", "research_compiled_policy",
                "research_execution_method", "research_graph_final", "research_publication_source",
                "independent_reviewer", "research_result",
            )
        }
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(source) == before


async def test_parseable_resigned_graph_projected_result_never_grants_source_authentication(
    published: Published,
) -> None:
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        ResearchGraphFinalProjection,
    )

    source = published.source
    changed_version = "changed-projected-result-after-publication"
    with source.database.session() as session:
        row = _row(session, source, "research_graph_final")
        body = json.loads(row.payload_json)
        body["projected_result"]["runtime_version"] = changed_version
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parsed = ResearchGraphFinalProjection.model_validate_json(payload)
        assert parsed.projected_result.runtime_version == changed_version
        row.payload_json, row.payload_hash = payload, _sha(payload)
    before = _sql(source)
    with source.database.session() as session:
        assessed = assess_outlook_in_session(session, published.outlook_id, assessed_at=utcnow())
        assert assessed.source_status == "invalid" and assessed.method_status == "unverifiable"
        assert assessed.source_reason and assessed.stable_recipe_hash is None
        assert not assessed.directional_usable and not assessed.source_refs
        assert not session.new and not session.dirty and not session.deleted
    assert _sql(source) == before
