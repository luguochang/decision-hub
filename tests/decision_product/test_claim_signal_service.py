"""Opinion snapshots in the existing library; synthetic data, no model/network."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_signal import (
    claim_use_evidence_in_session,
    freeze_claim_signal_in_session,
    read_claim_signal_in_session,
    resolve_claim_signal_in_session,
)
from packages.kernel.decision_hub_kernel.application.source_acquisition import (
    _canonical_json,
    _record_to_receipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalystClaimRecord,
    Database,
    ResearchDocumentBlockRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    ResearchEvidenceRecord,
    ResearchRunScopeRecord,
    RunRecord,
    RunRoleOutputRecord,
    SourceAcquisitionRecord,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ClaimSourceContext
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore
from tests.decision_product.test_research_signal_contracts import library, source

AT = datetime(2026, 10, 13, 0, tzinfo=UTC)
CUTOFF = AT + timedelta(minutes=5)


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'claims.sqlite3'}")
    db.create_all()
    store = DocumentBundleStore(tmp_path / "documents")
    original = b"Synthetic quote. It is an attributed forecast, not a market fact."
    ref, digest = store.write_original(original, suffix=".txt")
    _, version, _ = library()
    with db.session() as session:
        session.add(RunRecord(run_id="run-1", event_id="event-1", created_at=AT, updated_at=AT))
        session.add(
            ResearchRunScopeRecord(
                run_id="run-1",
                research_family="crypto_macro",
                domain_pack_ref="crypto_macro.v1",
                instrument_scope_json='["BTC"]',
                library_context_mode="selected",
                selected_version_ids_json='["docver_a"]',
                allow_remote_library_content=False,
                input_receipts_json="[]",
                created_at=AT,
            )
        )
        session.add(
            ResearchDocumentRecord(
                document_id="doc_a",
                title="Synthetic source",
                source_type="public_web",
                source_profile_id="source-news",
                author="Example Author",
                market_scopes_json='["crypto_macro"]',
                instrument_scopes_json='["BTC"]',
                privacy_mode="remote_allowed",
                visibility="shared",
                created_at=AT,
                latest_version_id="docver_future",
            )
        )
        session.add(
            ResearchDocumentVersionRecord(
                version_id="docver_a",
                document_id="doc_a",
                original_file_ref=ref,
                original_file_hash=digest,
                status="ready",
                correction_status="not_requested",
                published_at=None,
                observed_at=AT,
                received_at=AT,
                created_at=AT,
                block_count=1,
                claim_count=1,
                quality_json=version.quality.model_dump_json(),
            )
        )
        session.add(
            ResearchDocumentBlockRecord(
                block_id="block-1",
                version_id="docver_a",
                block_type="paragraph",
                page_no=1,
                content=original.decode(),
                content_hash=digest,
                locator="document://docver_a/page/1/block/1",
                confidence=1,
                has_numeric_content=False,
                needs_review=False,
            )
        )
        session.add(
            AnalystClaimRecord(
                claim_id="claim_a",
                version_id="docver_a",
                claim_type="forecast",
                stance="mixed",
                claim_text="Synthetic proposition",
                supporting_quote="Synthetic quote",
                locator_refs_json='["document://docver_a/page/1/block/1"]',
                market_scope="crypto_macro",
                instrument_scope_json='["BTC"]',
                horizon=None,
                conditions_json="[]",
                invalidation_conditions_json="[]",
                status="candidate",
                attribution_confidence="unknown",
                requires_independent_evidence=True,
                created_at=AT,
            )
        )
    yield db, store
    db.engine.dispose()


def freeze(context, **changes):
    db, store = context
    with db.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        return freeze_claim_signal_in_session(
            session,
            **{
                "run_id": "run-1",
                "document_id": "doc_a",
                "version_id": "docver_a",
                "claim_id": "claim_a",
                "source": ClaimSourceContext(**source()),
                "cutoff": CUTOFF,
                "store": store,
                **changes,
            },
        )


def read(context, frozen, **changes):
    db, store = context
    with db.session() as session:
        return read_claim_signal_in_session(
            session,
            **{
                "run_id": "run-1",
                "output_id": frozen.output_id,
                "payload_hash": frozen.payload_hash,
                "cutoff": CUTOFF,
                "store": store,
                **changes,
            },
        )


def test_freeze_replay_exact_version_unknown_time_and_restart(context):
    frozen = freeze(context)
    assert freeze(context) == frozen
    assert read(context, frozen) == frozen
    assert frozen.signal.version_id == "docver_a"
    assert frozen.signal.source.publication.published_at is None
    assert frozen.signal.horizon is None
    assert frozen.source_attestation == "declared"
    assert frozen.signal.requires_independent_evidence is True
    with context[0].session() as session:
        assert len(session.scalars(select(RunRoleOutputRecord)).all()) == 1


def test_writer_rollback_leaves_no_snapshot(context):
    db, store = context
    with pytest.raises(RuntimeError):
        with db.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            freeze_claim_signal_in_session(
                session,
                run_id="run-1",
                document_id="doc_a",
                version_id="docver_a",
                claim_id="claim_a",
                source=ClaimSourceContext(**source()),
                cutoff=CUTOFF,
                store=store,
            )
            raise RuntimeError("rollback")
    with db.session() as session:
        assert session.scalar(select(RunRoleOutputRecord)) is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"document_id": "doc_other"}, "document_version_mismatch"),
        ({"version_id": "docver_other"}, "document_version_mismatch"),
        ({"cutoff": AT - timedelta(seconds=1)}, "after_cutoff"),
        ({"cutoff": CUTOFF.replace(tzinfo=None)}, "timezone_required"),
    ],
)
def test_wrong_identity_or_cutoff_rejected(context, change, reason):
    with pytest.raises(ValueError, match=reason):
        freeze(context, **change)


@pytest.mark.parametrize(
    "record,field,value,reason",
    [
        (ResearchRunScopeRecord, "selected_version_ids_json", "[]", "not_selected"),
        (ResearchDocumentRecord, "privacy_mode", "local_only", "consent_required"),
        (ResearchDocumentRecord, "source_profile_id", "another", "source_mismatch"),
        (
            ResearchDocumentVersionRecord,
            "received_at",
            CUTOFF + timedelta(seconds=1),
            "after_cutoff",
        ),
        (AnalystClaimRecord, "created_at", CUTOFF + timedelta(seconds=1), "after_cutoff"),
        (AnalystClaimRecord, "reviewed_at", CUTOFF + timedelta(seconds=1), "after_cutoff"),
        (AnalystClaimRecord, "status", "rejected", "not_eligible"),
        (AnalystClaimRecord, "locator_refs_json", '["document://another/page/1"]', "locator"),
        (AnalystClaimRecord, "supporting_quote", "Not present in the original block", "quote"),
        (ResearchDocumentBlockRecord, "content", "Changed without hash", "block_hash"),
    ],
)
def test_source_and_version_admission_rejections(context, record, field, value, reason):
    with context[0].session() as session:
        row = session.scalar(select(record))
        setattr(row, field, value)
    with pytest.raises(ValueError, match=reason):
        freeze(context)


def test_metadata_change_conflicts_instead_of_rewriting(context):
    original = freeze(context)
    changed = source()
    changed["language"] = "zh"
    with pytest.raises(ValueError, match="immutable"):
        freeze(context, source=ClaimSourceContext(**changed))
    assert read(context, original) == original


@pytest.mark.parametrize("mutation", ["row_payload", "row_hash", "claim", "file", "locator"])
def test_read_rechecks_role_and_library_originals(context, mutation):
    frozen = freeze(context)
    db, store = context
    if mutation == "file":
        store.path_for(frozen.signal.content_ref).write_bytes(b"changed original")
    else:
        with db.session() as session:
            if mutation == "row_payload":
                session.get(RunRoleOutputRecord, frozen.output_id).payload_json = "{}"
            elif mutation == "row_hash":
                session.get(RunRoleOutputRecord, frozen.output_id).payload_hash = "0" * 64
            elif mutation == "claim":
                session.get(AnalystClaimRecord, "claim_a").claim_text = "Rewritten claim"
            else:
                session.get(ResearchDocumentBlockRecord, "block-1").locator = "document://wrong"
    with pytest.raises(ValueError):
        read(context, frozen)


def test_later_mutable_review_does_not_rewrite_frozen_history(context):
    frozen = freeze(context)
    with context[0].session() as session:
        row = session.get(AnalystClaimRecord, "claim_a")
        row.status, row.reviewed_at = "superseded", CUTOFF + timedelta(days=1)
    assert read(context, frozen).signal.review_status == "candidate"


def acquisition(context):
    db, store = context
    with db.session() as session:
        version = session.get(ResearchDocumentVersionRecord, "docver_a")
        row = SourceAcquisitionRecord(
            receipt_id="acq-1",
            source_id="source-news",
            url="https://example.org/article",
            access_mode="http",
            status="succeeded",
            observed_at=AT,
            received_at=AT,
            status_code=200,
            artifacts_json=json.dumps(
                [{"kind": "dom", "path": "unused", "content_hash": version.original_file_hash}]
            ),
            created_at=AT,
            receipt_hash="",
        )
        receipt = _record_to_receipt(row).model_dump(mode="json")
        for key in ("request_started_at", "request_completed_at", "received_at"):
            if receipt.get(key) is None:
                receipt.pop(key, None)
        receipt.pop("received_at", None)
        row.receipt_hash = hashlib.sha256(_canonical_json(receipt).encode()).hexdigest()
        session.add(row)


def test_successful_matching_acquisition_is_distinct_from_declared(context):
    acquisition(context)
    frozen = freeze(context, acquisition_receipt_id="acq-1")
    assert frozen.source_attestation == "acquisition_verified"
    assert read(context, frozen) == frozen
    with context[0].session() as session:
        session.get(SourceAcquisitionRecord, "acq-1").url = "https://wrong.test/article"
    with pytest.raises(ValueError, match="acquisition"):
        read(context, frozen)


def evidence(context, identity="evidence-1", **changes):
    with context[0].session() as session:
        row = ResearchEvidenceRecord(
            **{
                "evidence_id": identity,
                "run_id": "run-1",
                "capability_id": "official-data",
                "requirement_id": "requirement-1",
                "kind": "official",
                "authority": "official",
                "source_id": "official-primary",
                "source_url": "https://official.test/data",
                "observed_at": AT,
                "received_at": AT,
                "content_hash": "a" * 64,
                "excerpt": "Independent source observation",
                "source_content_hash": "b" * 64,
                "research_session_id": "session-1",
                "round": 1,
                "quality": "accepted",
                "freshness_status": "fresh",
                "accepted_at": AT,
                **changes,
            }
        )
        row.content_hash = research_evidence_content_hash(
            requirement_id=row.requirement_id,
            kind=row.kind,
            authority=row.authority,
            source_id=row.source_id,
            source_url=row.source_url,
            published_at=row.published_at,
            excerpt=row.excerpt,
            structured_payload_ref=row.structured_payload_ref,
        )
        session.add(row)


def links(context, frozen, support=("evidence-1",), counter=()):
    with context[0].session() as session:
        return claim_use_evidence_in_session(
            session,
            frozen=frozen,
            support_refs=support,
            counter_refs=counter,
            cutoff=CUTOFF,
            store=context[1],
        )


def test_evidence_association_requires_admitted_cross_source_inputs_not_author_quotes(context):
    frozen = freeze(context)
    evidence(context)
    result = links(context, frozen)
    assert result.support_refs == ("evidence-1",)
    assert result.counter_refs == ()
    assert result.claim_truth == "not_assessed"
    assert result.independent_source_count == 1
    assert "counterevidence_missing" in result.gaps
    with pytest.raises(ValueError, match="not_admitted"):
        links(context, frozen, support=tuple(frozen.signal.quoted_evidence_refs))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"run_id": "other-run"}, "not_admitted"),
        ({"quality": "rejected"}, "not_admitted"),
        ({"received_at": CUTOFF + timedelta(seconds=1)}, "after_cutoff"),
        ({"source_id": "source-news"}, "not_independent"),
        ({"source_url": "https://example.org/another-article"}, "not_independent"),
    ],
)
def test_invalid_or_same_source_evidence_cannot_corroborate(context, changes, reason):
    frozen = freeze(context)
    evidence(context, **changes)
    with pytest.raises(ValueError, match=reason):
        links(context, frozen)


def test_shared_original_hash_is_not_independent_and_duplicate_sources_count_once(context):
    frozen = freeze(context)
    evidence(context, source_content_hash=frozen.signal.content_hash)
    with pytest.raises(ValueError, match="not_independent"):
        links(context, frozen)
    with context[0].session() as session:
        session.get(ResearchEvidenceRecord, "evidence-1").source_content_hash = "b" * 64
    evidence(context, identity="evidence-2")
    result = links(context, frozen, support=("evidence-1", "evidence-2"))
    assert result.independent_source_count == 1


def test_promotion_has_no_fact_admission_and_missing_links_are_explicit(context):
    raw = source()
    raw["content_kind"] = "promotion"
    frozen = freeze(context, source=ClaimSourceContext(**raw))
    result = links(context, frozen, support=())
    assert result.claim_truth == "not_assessed"
    assert "promotion_not_fact" in result.gaps
    assert "independent_support_missing" in result.gaps


def test_existing_claim_id_resolves_exact_snapshot_and_missing_is_local(context):
    frozen = freeze(context)
    with context[0].session() as session:
        assert (
            resolve_claim_signal_in_session(
                session, run_id="run-1", claim_id="claim_a", cutoff=CUTOFF, store=context[1]
            )
            == frozen
        )
        assert (
            resolve_claim_signal_in_session(
                session, run_id="run-1", claim_id="claim_absent", cutoff=CUTOFF, store=context[1]
            )
            is None
        )


def test_known_syndication_is_not_a_second_independent_source(context):
    raw = source()
    raw["syndication_cluster"] = "original-story-1"
    frozen = freeze(context, source=ClaimSourceContext(**raw))
    with context[0].session() as session:
        # A second real library identity, preserving each original's own IDs.
        document = session.get(ResearchDocumentRecord, "doc_a")
        version = session.get(ResearchDocumentVersionRecord, "docver_a")
        claim = session.get(AnalystClaimRecord, "claim_a")
        block = session.get(ResearchDocumentBlockRecord, "block-1")

        def clone(row, **updates):
            return type(row)(
                **{**{col.key: getattr(row, col.key) for col in row.__table__.columns}, **updates}
            )

        session.add(clone(document, document_id="doc_b", source_profile_id="copy-source"))
        session.add(clone(version, version_id="docver_b", document_id="doc_b"))
        session.add(
            clone(
                claim,
                claim_id="claim_b",
                version_id="docver_b",
                locator_refs_json='["document://docver_b/block/1"]',
            )
        )
        session.add(
            clone(
                block,
                block_id="block-2",
                version_id="docver_b",
                locator="document://docver_b/block/1",
            )
        )
        session.get(
            ResearchRunScopeRecord, "run-1"
        ).selected_version_ids_json = '["docver_a","docver_b"]'
    raw.update(source_id="copy-source", canonical_url="https://copy.test/article")
    freeze(
        context,
        document_id="doc_b",
        version_id="docver_b",
        claim_id="claim_b",
        source=ClaimSourceContext(**raw),
    )
    evidence(context, source_id="copy-source", source_url="https://copy.test/article")
    with pytest.raises(ValueError, match="not_independent"):
        links(context, frozen)


def test_interval_publication_is_not_backfilled_as_exact_or_rejected_for_day_end(context):
    raw = source()
    raw["publication"].update(
        precision="interval",
        window_start=AT.isoformat(),
        window_end=(AT + timedelta(days=1)).isoformat(),
    )
    frozen = freeze(context, source=ClaimSourceContext(**raw))
    assert read(context, frozen).signal.source.publication.precision == "interval"
    assert frozen.signal.source.publication.published_at is None


def test_evidence_body_changed_without_hash_rejected(context):
    frozen = freeze(context)
    evidence(context)
    with context[0].session() as session:
        session.get(ResearchEvidenceRecord, "evidence-1").excerpt = "Forged evidence"
    with pytest.raises(ValueError, match="evidence_hash_mismatch"):
        links(context, frozen)


def test_acquisition_server_received_time_is_bound_even_though_receipt_hash_excludes_it(context):
    acquisition(context)
    frozen = freeze(context, acquisition_receipt_id="acq-1")
    with context[0].session() as session:
        session.get(SourceAcquisitionRecord, "acq-1").received_at = AT + timedelta(seconds=1)
    with pytest.raises(ValueError, match="source_attestation_changed"):
        read(context, frozen)
