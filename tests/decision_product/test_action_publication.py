"""Atomic publication component tests; native authentication is a named fixture.

Only the trusted native reader/source inventory are replaced. Real SQLite read
capture, writer verification, pure derivation, repositories and outbox are used.
These tests neither authenticate a live model nor establish financial value.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, ResearchInputEvidence
from packages.kernel.decision_hub_kernel.application import action_publication as publication
from packages.kernel.decision_hub_kernel.application.action_plan import ActionPlanService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    _ReadSourceGuard,
    parse_graph_final_ref,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionSnapshotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    ResearchEvidenceRecord,
    RunRoleOutputRecord,
    SourceAcquisitionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

from .test_action_derivation import AT, NOW, inputs


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def context(tmp_path, monkeypatch):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'publication.sqlite3'}")
    database.create_all()
    body, opportunity, _ = inputs()
    body["synthesis"]["claim_signal_refs"] = []
    for candidate in body["synthesis"]["action_intents"]:
        candidate["claim_refs"] = []
    evidence = []
    for number, evidence_id in enumerate(("evidence-1", "evidence-2", "alternative-1")):
        material = {
            "requirement_id": "requirement-1",
            "kind": "official",
            "authority": "official",
            "source_id": f"source-{number}",
            "source_url": f"https://source{number}.example/item",
            "published_at": AT,
            "excerpt": f"Isolated test evidence {number}",
            "structured_payload_ref": None,
        }
        evidence.append(
            EvidenceCandidate.model_validate(
                {
                    **material,
                    "evidence_id": evidence_id,
                    "content_hash": research_evidence_content_hash(**material),
                    "observed_at": AT,
                    "received_at": AT,
                    "tool_call_id": "fixture-capability",
                    "research_session_id": "fixture-session",
                    "round": 1,
                    "quality": "accepted",
                    "freshness_status": "fresh",
                    "conflict_group": None,
                }
            )
        )
    with database.session() as session:
        preparation.begin_immediate(session)
        ActionRepository(session).register_opportunity(opportunity)
        for item in evidence:
            values = item.model_dump(mode="python")
            values["source_url"] = str(item.source_url)
            session.add(
                ResearchEvidenceRecord(
                    **values,
                    run_id="run-1",
                    capability_id="synthetic-component-fixture",
                    accepted_at=AT,
                )
            )
    template = json.loads(
        (Path(__file__).parent / "fixtures/native_action_legacy_v1.json").read_text(
            encoding="utf-8",
        )
    )
    result = json.loads(template["cases"]["tool"]["result_json"])
    result.update(
        request_id=body["request_id"],
        research_session_id="fixture-session",
        evidence_candidates=[item.model_dump(mode="json") for item in evidence],
        horizons=body["synthesis"]["research_candidate_v1"]["horizons"],
        causal_case=None,
    )
    ref = parse_graph_final_ref(
        {
            "kind": "graph_final_projection",
            "row": body["selected_native_observation_ref"],
        }
    )
    observed = ObservedExecutionRef.model_validate_json(
        _json(body["selected_native_observation_ref"])
    )
    native = SimpleNamespace(
        synthesis_json=_json(body["synthesis"]),
        producer_generation=body["generation"],
        task_request_hash=body["request_hash"],
        execution_request_hash="e" * 64,
        raw_context_hash=body["context_hash"],
        producer_cutoff_at=AT,
        selection_witness_json=_json(body["selection_witness"]),
        raw_candidate_hash=canonical_hash(body["synthesis"]),
        projected_research_candidate_hash=canonical_hash(
            body["synthesis"]["research_candidate_v1"]
        ),
        review_gate_status="matched",
        review_effect_ref=observed,
        execution_mode="replay",
        producer_input_evidence_json="[]",
    )
    trusted = SimpleNamespace(
        run_id="run-1",
        request_id=body["request_id"],
        scope_id=opportunity.scope_id,
        action_status="native_candidate",
        native_action=native,
        decision_cutoff_at=AT,
        selected_observation_ref=observed,
        selected_result_json=_json(result),
    )
    calls = []

    def fixture_reader(session, **identities):
        assert not session.new and not session.dirty
        assert session.connection().connection.driver_connection.in_transaction
        calls.append(identities)
        return trusted

    # Native authority is explicitly replaced here; real reader integration is
    # the separate native chain test, never inferred from this fixture.
    monkeypatch.setattr(publication, "read_trusted_research_in_session", fixture_reader)
    guard = SimpleNamespace(root=tmp_path, valid=True)
    guard.matches = lambda: guard.valid
    monkeypatch.setattr(_ReadSourceGuard, "capture", classmethod(lambda _cls: guard))
    monkeypatch.setattr(
        preparation._ConsumerSourceGuard,
        "capture",
        classmethod(
            lambda _cls, *_args: guard,
        ),
    )
    clock = [NOW]
    service = publication.ActionPublicationService(database, clock=lambda: clock[0])
    args = dict(
        run_id="run-1",
        event_id="event-1",
        artifact_id="artifact-1",
        reference=ref,
        opportunity_id=opportunity.id,
    )
    yield SimpleNamespace(
        database=database,
        service=service,
        args=args,
        trusted=trusted,
        native=native,
        body=body,
        opportunity=opportunity,
        clock=clock,
        calls=calls,
        guard=guard,
    )
    database.engine.dispose()


def _counts(context):
    with context.database.session() as session:
        return tuple(
            session.query(model).count()
            for model in (
                RunRoleOutputRecord,
                DecisionSnapshotRecord,
                ActionPlanRevisionRecord,
                ActionPlanHeadRecord,
                OutboxRecord,
                ActionCommandReceiptRecord,
            )
        )


def _assert_empty(context):
    assert _counts(context) == (0, 0, 0, 0, 0, 0)
    with context.database.session() as session:
        assert ActionRepository(session).get_opportunity(context.opportunity.id).status == "open"


def test_all_branches_sidecar_outbox_and_receipt_are_one_atomic_publication(context):
    receipt = context.service.publish(**context.args)
    assert _counts(context) == (1, 1, 3, 3, 3, 1)
    assert [b["position_context"] for b in receipt.response["branches"]] == [
        "flat",
        "long",
        "unknown",
    ]
    with context.database.session() as session:
        repository = ActionRepository(session)
        assert repository.get_opportunity(context.opportunity.id).status == "decided"
        snapshot = repository.get_snapshot(receipt.response["decision_id"])
        assert snapshot.probability.value is None
        revisions = [
            repository.get_revision(b["revision_id"]) for b in receipt.response["branches"]
        ]
        assert [r.advice_qualification for r in revisions] == [
            "research_trial",
            "research_trial",
            "blocked",
        ]
        sidecar = session.get(RunRoleOutputRecord, receipt.response["sidecar_ref"])
        assert hashlib.sha256(sidecar.payload_json.encode()).hexdigest() == sidecar.payload_hash
        saved = json.loads(sidecar.payload_json)
        assert saved["synthesis"]["action_intents"][1]["probability"]["value"] == 0.61
        assert saved["synthesis"]["action_intents"][0]["probability"]["value"] is None
        for branch in receipt.response["branches"]:
            row = session.get(OutboxRecord, branch["outbox_id"])
            assert row.subject_kind == "action_revision" and row.subject_id == branch["revision_id"]
            assert row.sent_at is None and row.attempts == 0
        assert repository.get_head(revisions[2].plan_id).state == "proposed"


def test_retry_returns_first_receipt_after_new_head_and_expiry_without_reauth(context):
    first = context.service.publish(**context.args)
    branch = first.response["branches"][0]
    commands = ActionPlanService(context.database, clock=lambda: NOW + timedelta(minutes=1))
    commands.record_command(
        owner_id="owner",
        request_id="pause",
        plan_id=branch["plan_id"],
        expected_revision_id=branch["revision_id"],
        expected_state_version=1,
        operation="pause",
    )
    context.clock[0] += timedelta(days=2)
    context.guard.valid = False
    assert context.service.publish(**context.args) == first
    assert len(context.calls) == 1
    assert _counts(context) == (1, 1, 3, 3, 3, 2)


def test_same_run_opportunity_with_changed_identity_conflicts(context):
    context.service.publish(**context.args)
    with pytest.raises(ValueError, match="action_publication_key_conflict"):
        context.service.publish(**{**context.args, "artifact_id": "different"})
    assert _counts(context) == (1, 1, 3, 3, 3, 1)


@pytest.mark.parametrize(
    "failure",
    ["snapshot", "second_branch", "second_outbox", "receipt", "after_verify", "opportunity"],
)
def test_failure_at_each_stage_rolls_back_every_publication_row(context, monkeypatch, failure):
    owner, method = {
        "snapshot": (ActionRepository, "append_snapshot"),
        "second_branch": (ActionRepository, "create_plan"),
        "second_outbox": (publication.OutboxService, "enqueue_subject"),
        "receipt": (ActionRepository, "append_command_receipt"),
        "after_verify": (preparation.PreparedOriginals, "verify_after_writes"),
        "opportunity": (ActionRepository, "set_opportunity_status"),
    }[failure]
    original = getattr(owner, method)
    calls = [0]

    def fail(*args, **kwargs):
        calls[0] += 1
        if calls[0] == (2 if failure.startswith("second") else 1):
            raise RuntimeError("injected_publication_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, fail)
    with pytest.raises(RuntimeError, match="injected_publication_failure"):
        context.service.publish(**context.args)
    _assert_empty(context)


@pytest.mark.parametrize("kind", ["evidence", "source", "expiry"])
def test_changed_original_or_source_or_expiry_between_read_and_writer_is_rejected(
    context, monkeypatch, kind
):
    begin = publication.begin_immediate

    def changed(session):
        if kind == "evidence":
            with context.database.session() as separate:
                separate.execute(
                    update(ResearchEvidenceRecord)
                    .where(
                        ResearchEvidenceRecord.evidence_id == "evidence-1",
                    )
                    .values(excerpt="Changed after trusted read")
                )
        elif kind == "source":
            context.guard.valid = False
        else:
            context.clock[0] += timedelta(days=1)
        begin(session)

    monkeypatch.setattr(publication, "begin_immediate", changed)
    with pytest.raises(
        ValueError,
        match={
            "evidence": "original_preparation_readset_changed",
            "source": "original_preparation_source_changed",
            "expiry": "action_publication_expired_during_preparation",
        }[kind],
    ):
        context.service.publish(**context.args)
    _assert_empty(context)


@pytest.mark.parametrize(
    "change,error",
    [
        ("legacy", "action_native_typed_synthesis_unavailable"),
        ("unreviewed", "action_native_independent_review_required"),
        ("scope", "action_publication_scope_mismatch"),
        ("hash", "action_evidence_content_hash_mismatch"),
        ("future", "research_pit_violation"),
    ],
)
def test_ineligible_or_corrupt_originals_do_not_publish(context, change, error):
    if change == "legacy":
        context.trusted.action_status = "unsupported"
    elif change == "unreviewed":
        context.native.review_gate_status = "unreviewed"
    elif change == "scope":
        context.trusted.scope_id = "other"
    else:
        result = json.loads(context.trusted.selected_result_json)
        with context.database.session() as session:
            row = session.get(ResearchEvidenceRecord, "evidence-1")
            if change == "hash":
                row.content_hash = "1" * 64
                result["evidence_candidates"][0]["content_hash"] = row.content_hash
            else:
                row.received_at = NOW
                result["evidence_candidates"][0]["received_at"] = NOW.isoformat()
        context.trusted.selected_result_json = _json(result)
    with pytest.raises(ValueError, match=error):
        context.service.publish(**context.args)
    _assert_empty(context)


def test_missing_claim_blocks_relevant_branch_and_preserves_reduction(context):
    source = json.loads(context.native.synthesis_json)
    source["claim_signal_refs"] = ["missing-claim"]
    source["action_intents"][0]["claim_refs"] = ["missing-claim"]
    context.native.synthesis_json = _json(source)
    receipt = context.service.publish(**context.args)
    with context.database.session() as session:
        revisions = [
            ActionRepository(session).get_revision(b["revision_id"])
            for b in receipt.response["branches"]
        ]
        assert revisions[0].advice_qualification == "blocked"
        assert revisions[1].advice_qualification == "research_trial"
        assert "外部观点" in revisions[0].reason


def test_sidecar_immutable_conflict_preserves_original_and_no_product_rows(context, monkeypatch):
    identity = (
        "action_synthesis_"
        + canonical_hash(
            [
                "run-1",
                context.opportunity.scope_id,
                context.opportunity.horizon,
            ]
        )[:40]
    )
    with context.database.session() as session:
        session.add(
            RunRoleOutputRecord(
                output_id=identity,
                run_id="run-1",
                role="research_action_synthesis_24h",
                schema_version="other",
                payload_json="{}",
                payload_hash=canonical_hash({}),
                created_at=AT,
            )
        )
    with pytest.raises(ValueError, match="action_sidecar_immutable_conflict"):
        context.service.publish(**context.args)
    assert _counts(context) == (1, 0, 0, 0, 0, 0)
    with context.database.session() as session:
        assert session.get(RunRoleOutputRecord, identity).payload_json == "{}"


def test_replay_source_cannot_enter_prospective_cohort(context):
    from packages.kernel.decision_hub_kernel.persistence.action_records import (
        DecisionOpportunityRecord,
    )

    with context.database.session() as session:
        row = session.get(DecisionOpportunityRecord, context.opportunity.id)
        row.cohort = "prospective"
    with pytest.raises(ValueError, match="action_prospective_live_source_required"):
        context.service.publish(**context.args)
    _assert_empty(context)


def test_no_public_verified_or_dto_bypass(context):
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        context.service.publish(**context.args, verified=True)
    _assert_empty(context)


def test_existing_report_outbox_attempts_are_untouched(context):
    with context.database.session() as session:
        row = OutboxRecord(
            artifact_id="old-artifact",
            subject_kind="report",
            subject_id="old-artifact",
            channel="local",
            dedupe_key="historical-report",
            attempts=4,
            claim_generation=7,
            attempt_history_json='[{"event":"old"}]',
            dispatch_status="failed",
        )
        session.add(row)
        session.flush()
        row_id = row.id
    context.service.publish(**context.args)
    with context.database.session() as session:
        row = session.get(OutboxRecord, row_id)
        assert (row.attempts, row.claim_generation, row.attempt_history_json) == (
            4,
            7,
            '[{"event":"old"}]',
        )
        assert (
            session.scalar(
                select(OutboxRecord.id).where(OutboxRecord.dedupe_key == "historical-report")
            )
            == row_id
        )


def test_producer_request_input_evidence_is_resolved_without_latest_run_fallback(context):
    result = json.loads(context.trusted.selected_result_json)
    first = result["evidence_candidates"].pop(0)
    context.trusted.selected_result_json = _json(result)
    first = {
        key: value for key, value in first.items() if key in ResearchInputEvidence.model_fields
    }
    first["excerpt"] = "Isolated test"
    context.native.producer_input_evidence_json = _json([first])
    receipt = context.service.publish(**context.args)
    with context.database.session() as session:
        revision = ActionRepository(session).get_revision(
            receipt.response["branches"][0]["revision_id"]
        )
        assert revision.advice_qualification == "research_trial"


def test_conflicting_request_input_and_result_original_is_rejected(context):
    first = json.loads(context.trusted.selected_result_json)["evidence_candidates"][0]
    first = {
        key: value for key, value in first.items() if key in ResearchInputEvidence.model_fields
    }
    first["content_hash"] = "a" * 64
    context.native.producer_input_evidence_json = _json([first])
    with pytest.raises(ValueError, match="action_evidence_duplicate_conflict"):
        context.service.publish(**context.args)
    _assert_empty(context)


def test_same_assertion_with_different_source_label_is_not_an_independent_counter(context):
    result = json.loads(context.trusted.selected_result_json)
    support, counter = result["evidence_candidates"][:2]
    counter["excerpt"] = support["excerpt"]
    counter["content_hash"] = research_evidence_content_hash(
        requirement_id=counter["requirement_id"],
        kind=counter["kind"],
        authority=counter["authority"],
        source_id=counter["source_id"],
        source_url=counter["source_url"],
        published_at=AT,
        excerpt=counter["excerpt"],
        structured_payload_ref=counter["structured_payload_ref"],
    )
    assert counter["content_hash"] != support["content_hash"]
    context.trusted.selected_result_json = _json(result)
    with context.database.session() as session:
        row = session.get(ResearchEvidenceRecord, counter["evidence_id"])
        row.content_hash, row.excerpt = counter["content_hash"], counter["excerpt"]
    receipt = context.service.publish(**context.args)
    with context.database.session() as session:
        repository = ActionRepository(session)
        flat, long, _ = receipt.response["branches"]
        assert repository.get_revision(flat["revision_id"]).advice_qualification == "blocked"
        assert repository.get_revision(long["revision_id"]).advice_qualification == "research_trial"


@pytest.mark.parametrize("referenced", [False, True])
def test_unverified_compiled_input_is_not_candidate_and_does_not_veto_unrelated_branch(
    context, referenced
):
    initial = ResearchInputEvidence.model_validate(
        {
            "evidence_id": "compiled-spot-context",
            "kind": "document",
            "authority": "unverified",
            "source_id": "compiled-spot-context",
            "source_url": None,
            "published_at": None,
            "observed_at": AT,
            "received_at": AT,
            "content_hash": canonical_hash({"fixture": True}),
            "excerpt": "Synthetic compiled context; not an EvidenceCandidate",
        }
    )
    context.native.producer_input_evidence_json = _json([initial.model_dump(mode="json")])
    if referenced:
        source = json.loads(context.native.synthesis_json)
        source["action_intents"][0]["evidence_refs"].append(initial.evidence_id)
        context.native.synthesis_json = _json(source)
    receipt = context.service.publish(**context.args)
    with context.database.session() as session:
        revisions = [
            ActionRepository(session).get_revision(branch["revision_id"])
            for branch in receipt.response["branches"]
        ]
        assert revisions[0].advice_qualification == ("blocked" if referenced else "research_trial")
        assert revisions[1].advice_qualification == "research_trial"
    assert receipt.response["source_gaps"] == (
        [
            {
                "ref": initial.evidence_id,
                "reason": "action_input_evidence_kind_unsupported",
            }
        ]
        if referenced
        else []
    )


@pytest.mark.parametrize("corrupt", [None, "hash", "url", "time"])
def test_acquisition_receipt_hash_source_and_time_are_rechecked(context, corrupt):
    from packages.kernel.decision_hub_kernel.application.source_acquisition import (
        _record_to_receipt,
    )

    result = json.loads(context.trusted.selected_result_json)
    result["evidence_candidates"][0]["acquisition_receipt_id"] = "acq-test"
    context.trusted.selected_result_json = _json(result)
    with context.database.session() as session:
        evidence = session.get(ResearchEvidenceRecord, "evidence-1")
        evidence.acquisition_receipt_id = "acq-test"
        row = SourceAcquisitionRecord(
            receipt_id="acq-test",
            source_id=evidence.source_id,
            url=evidence.source_url if corrupt != "url" else "https://wrong.example/item",
            access_mode="http",
            status="succeeded",
            status_code=200,
            observed_at=AT,
            received_at=AT if corrupt != "time" else NOW,
            created_at=AT,
            artifacts_json=_json([{"kind": "dom", "path": "test-only", "content_hash": "a" * 64}]),
        )
        body = _record_to_receipt(row).model_dump(mode="json")
        for field in ("request_started_at", "request_completed_at", "received_at"):
            if body.get(field) is None:
                body.pop(field, None)
        body.pop("received_at", None)
        row.receipt_hash = canonical_hash(body) if corrupt != "hash" else "0" * 64
        session.add(row)
    if corrupt is None:
        assert context.service.publish(**context.args).response["status"] == "published"
    else:
        code = {"hash": "hash_mismatch", "url": "source_mismatch", "time": "time_invalid"}[corrupt]
        with pytest.raises(ValueError, match="action_acquisition_" + code):
            context.service.publish(**context.args)
        _assert_empty(context)


def test_writer_time_is_used_after_prepare_delay(context, monkeypatch):
    begin = publication.begin_immediate
    writer_at = NOW + timedelta(minutes=7)

    def delayed(session):
        context.clock[0] = writer_at
        begin(session)

    monkeypatch.setattr(publication, "begin_immediate", delayed)
    receipt = context.service.publish(**context.args)
    assert receipt.accepted_at == writer_at
    with context.database.session() as session:
        repo = ActionRepository(session)
        assert repo.get_snapshot(receipt.response["decision_id"]).known_at == writer_at
        for branch in receipt.response["branches"]:
            assert repo.get_revision(branch["revision_id"]).published_at == writer_at
            message = json.loads(session.get(OutboxRecord, branch["outbox_id"]).payload_json)
            assert message["created_at"] == writer_at.isoformat().replace("+00:00", "Z")


def test_two_simultaneous_requests_return_same_original_receipt(context, monkeypatch):
    original_prepare = publication.prepare_originals
    barrier = Barrier(2)

    def prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        barrier.wait(timeout=10)
        return prepared

    monkeypatch.setattr(publication, "prepare_originals", prepare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(context.service.publish, **context.args) for _ in range(2)]
        first, second = [future.result(timeout=20) for future in futures]
    assert first == second
    assert _counts(context) == (1, 1, 3, 3, 3, 1)


def test_accidental_original_mutation_in_writer_rolls_back_input_and_outputs(context, monkeypatch):
    original_store = publication._store_sidecar

    def mutate_original(session, prepared, at):
        original_store(session, prepared, at)
        session.execute(
            update(ResearchEvidenceRecord)
            .where(
                ResearchEvidenceRecord.evidence_id == "evidence-1",
            )
            .values(excerpt="accidental same-transaction change")
        )

    monkeypatch.setattr(publication, "_store_sidecar", mutate_original)
    with pytest.raises(ValueError, match="original_preparation_original_changed"):
        context.service.publish(**context.args)
    _assert_empty(context)
    with context.database.session() as session:
        assert (
            session.get(ResearchEvidenceRecord, "evidence-1").excerpt == "Isolated test evidence 0"
        )


@pytest.mark.parametrize("changed", [False, True])
def test_document_byte_read_is_rechecked_before_writer_publication(
    context, monkeypatch, tmp_path, changed
):
    from packages.kernel.decision_hub_kernel.application import research_signal
    from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

    store = DocumentBundleStore(tmp_path / "documents")
    reference, digest = store.write_original(b"isolated attributed opinion", suffix=".txt")
    context.service.document_store = store
    source = json.loads(context.native.synthesis_json)
    source["claim_signal_refs"] = ["claim-fixture"]
    source["action_intents"][0]["claim_refs"] = ["claim-fixture"]
    context.native.synthesis_json = _json(source)

    def named_claim_fixture(_session, *, store, **identities):
        assert identities["claim_id"] == "claim-fixture"
        assert hashlib.sha256(store.read(reference)).hexdigest() == digest
        return SimpleNamespace(
            payload_hash="d" * 64,
            signal=SimpleNamespace(
                content_hash=digest,
                source=SimpleNamespace(
                    first_seen_at=AT,
                    canonical_url="https://opinion.example/item",
                    syndication_cluster=None,
                    source_id="opinion-source",
                ),
            ),
        )

    monkeypatch.setattr(research_signal, "resolve_claim_signal_in_session", named_claim_fixture)
    if changed:
        begin = publication.begin_immediate

        def changed_bytes(session):
            store.path_for(reference).write_bytes(b"changed after preparation")
            begin(session)

        monkeypatch.setattr(publication, "begin_immediate", changed_bytes)
        with pytest.raises(ValueError, match="document_bundle_hash_mismatch"):
            context.service.publish(**context.args)
        _assert_empty(context)
    else:
        receipt = context.service.publish(**context.args)
        with context.database.session() as session:
            revision = ActionRepository(session).get_revision(
                receipt.response["branches"][0]["revision_id"]
            )
            assert revision.advice_qualification == "research_trial"
