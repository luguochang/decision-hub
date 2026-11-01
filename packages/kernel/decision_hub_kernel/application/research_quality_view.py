"""Read the quality actually bound to a publication, never the latest sidecar."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Literal, cast
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decision.research_quality import QualityDecisionV1
from ..persistence.db import Database, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.run_spot_admission import RunSpotAdmissionRecord
from ..ports.market_path import aware_utc
from ..ports.provider_input import ProviderConversationUsageCensusV1
from ..ports.research_observation import ObservedExecutionRef
from .commit import (
    PUBLICATION_SOURCE_ROLE,
    PUBLICATION_SOURCE_SCHEMA_V4,
    _read_publication_source_in_session,  # pyright: ignore[reportPrivateUsage]
)
from .research_graph_binding import (
    parse_graph_final_ref,
    publication_final_read_scope,
    read_graph_final_in_session,
)
from .research_graph_dispatch import read_graph_recipe_selection_for_run_in_session
from .research_graph_recipe_v3 import FrozenGraphRecipeSelectionV3
from .research_provider_usage import provider_usage_read_scope
from .research_quality_sidecar import read_quality_sidecar_in_session
from .research_recipe_read_scope import recipe_original_read_scope
from .research_review import _decode_review_row  # pyright: ignore[reportPrivateUsage]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreparedQualityBudgetViewV1(_View):
    status: Literal["prepared_limits_only"] = "prepared_limits_only"
    max_tool_calls: int = Field(ge=0)
    max_subagents: int = Field(ge=0)
    max_evidence_rounds: int = Field(ge=0)
    max_estimated_cost_usd: float = Field(ge=0, allow_inf_nan=False)
    deadline_at: AwareDatetime
    actual_total_tokens: None = None
    actual_estimated_cost_usd: None = None


class ResearchQualityViewV1(_View):
    schema_version: Literal["research-quality-view.v1"] = "research-quality-view.v1"
    run_id: str
    as_of: AwareDatetime
    status: Literal["available", "unavailable"]
    reason_code: str | None = None
    gate_status: Literal["publish", "degraded", "research_only", "reject"] | None = None
    published_at: AwareDatetime | None = None
    publication_ref: ObservedExecutionRef | None = None
    graph_final_ref: ObservedExecutionRef | None = None
    recipe_selection_ref: ObservedExecutionRef | None = None
    quality_sidecar_id: str | None = None
    decision: QualityDecisionV1 | None = None
    budget: PreparedQualityBudgetViewV1 | None = None
    usage: ProviderConversationUsageCensusV1 | None = None
    unknown_receipt_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def publication_boundary(self) -> ResearchQualityViewV1:
        fields = (
            self.gate_status,
            self.published_at,
            self.publication_ref,
            self.graph_final_ref,
            self.recipe_selection_ref,
            self.quality_sidecar_id,
            self.decision,
            self.budget,
        )
        if self.status == "available":
            if any(value is None for value in fields) or self.reason_code is not None:
                raise ValueError("research_quality_available_publication_required")
            assert self.decision is not None and self.published_at is not None
            if self.decision.run_id != self.run_id or self.published_at > self.as_of:
                raise ValueError("research_quality_publication_identity_invalid")
        elif any(value is not None for value in fields) or not self.reason_code:
            raise ValueError("research_quality_unpublished_decision_forbidden")
        if self.usage is not None and (
            self.status != "available" or self.usage.run_id != self.run_id
            or self.published_at is None or self.usage.as_of > self.published_at
        ):
            raise ValueError("research_quality_usage_publication_invalid")
        return self


def read_published_research_quality_in_session(
    session: Session, run_id: str, *, as_of: datetime
) -> ResearchQualityViewV1:
    return consume_published_research_originals_in_session(
        session, run_id, as_of=as_of, consume=lambda quality, _mapped: quality
    )


def consume_published_research_originals_in_session[R](
    session: Session,
    run_id: str,
    *,
    as_of: datetime,
    consume: Callable[[ResearchQualityViewV1, ObservedExecutionRef | None], R],
) -> R:
    """Consume an authenticated publication locator inside its source fence.

    The locator comes from the actual fully verified final, never the naked
    publication row or latest output. No reusable proof object is returned;
    every invocation establishes its own physical read and fresh source checks.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    transaction, nested = session.get_transaction(), session.get_nested_transaction()
    thread_id = threading.get_ident()
    changes = native.total_changes if isinstance(native, sqlite3.Connection) else -1

    def require_snapshot(
        error_code: str = "research_quality_consumer_snapshot_changed",
    ) -> None:
        # Check SQLAlchemy identities first: rollback may already have closed
        # the original native connection. A changed boundary cannot return a
        # value or become a generic closed-driver error from the outer fence.
        try:
            valid = (
                threading.get_ident() == thread_id
                and session.get_transaction() is transaction
                and session.get_nested_transaction() is nested
                and not connection.closed
                and session.connection() is connection
                and connection.connection.driver_connection is native
                and isinstance(native, sqlite3.Connection)
                and native.in_transaction
                and native.total_changes == changes
                and not (session.new or session.dirty or session.deleted)
            )
        except sqlite3.ProgrammingError:
            valid = False
        if not valid:
            raise ValueError(error_code)

    def consume_original(quality: ResearchQualityViewV1, mapped: ObservedExecutionRef | None) -> R:
        require_snapshot()
        value = consume(quality, mapped)
        require_snapshot()
        return value

    require_snapshot("research_quality_clean_read_snapshot_required")

    def read_original(*, reuse_recipe: bool) -> R:
        with publication_final_read_scope(session):
            if reuse_recipe:
                with recipe_original_read_scope(session):
                    with provider_usage_read_scope(session):
                        return _read_published_research_quality_in_session(
                            session, run_id, as_of=as_of, consume=consume_original
                        )
            return _read_published_research_quality_in_session(
                session, run_id, as_of=as_of, consume=consume_original
            )

    # A native COMMIT/BEGIN can retain the ORM transaction and connection
    # identities. This marker must survive the entire synchronous reused read.
    marker = "quality_read_" + uuid4().hex
    driver = cast(sqlite3.Connection, native)
    try:
        driver.execute(f"SAVEPOINT {marker}")
    except sqlite3.DatabaseError as exc:
        if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_AUTH:
            raise
        # Preserve a caller's stricter readonly authorizer. It gets the original
        # full publication reader, without the added recipe reuse.
        return read_original(reuse_recipe=False)
    try:
        result = read_original(reuse_recipe=True)
        require_snapshot()
    except BaseException:
        try:
            driver.execute(f"RELEASE SAVEPOINT {marker}")
        except sqlite3.Error:
            pass
        raise
    try:
        driver.execute(f"RELEASE SAVEPOINT {marker}")
    except sqlite3.Error as exc:
        raise ValueError("research_quality_consumer_snapshot_changed") from exc
    return result


def _read_published_research_quality_in_session[R](
    session: Session,
    run_id: str,
    *,
    as_of: datetime,
    consume: Callable[[ResearchQualityViewV1, ObservedExecutionRef | None], R],
) -> R:
    """Borrow a clean physical read snapshot. No sidecar is regraded or persisted.

    A V3 publication must retain the exact graph/selection/sidecar and all
    publication original proofs. Missing or corrupt originals raise an error.
    Earlier Runs explicitly lack this new quality contract.
    """
    at = aware_utc(as_of)
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
        or session.new
        or session.dirty
        or session.deleted
    ):
        raise ValueError("research_quality_clean_read_snapshot_required")
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise KeyError(run_id)
    source_rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
            )
            .execution_options(populate_existing=True)
        )
    )
    if len(source_rows) > 1:
        raise ValueError("research_quality_publication_ambiguous")
    selection_schema = session.scalar(
        select(RunRoleOutputRecord.schema_version).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "graph_recipe_selection",
        )
    )
    # Missing a new selection original cannot turn an authenticated spot Run
    # into an older Run. This is classification only, never scope permission.
    new_quality_expected = selection_schema == "research-graph-recipe-selection.v3" or (
        session.get(RunSpotAdmissionRecord, run_id) is not None
    )

    def unavailable(reason: str) -> R:
        return consume(
            ResearchQualityViewV1(
                run_id=run_id, as_of=at, status="unavailable", reason_code=reason
            ),
            None,
        )

    if not source_rows:
        if new_quality_expected and run.artifact_id:
            raise ValueError("research_quality_publication_original_missing")
        return unavailable(
            "research_quality_publication_pending"
            if new_quality_expected
            else "research_quality_legacy_unavailable"
        )
    source = source_rows[0]
    if hashlib.sha256(source.payload_json.encode()).hexdigest() != source.payload_hash:
        raise ValueError("research_quality_publication_original_changed")
    body = json.loads(source.payload_json)
    published_at = as_utc(cast(datetime | None, source.created_at))
    if (
        not isinstance(body, dict)
        or body.get("schema_version") != source.schema_version
        or body.get("run_id") != run_id
        or body.get("event_id") != run.event_id
        or published_at is None
    ):
        raise ValueError("research_quality_publication_original_invalid")
    if source.schema_version != PUBLICATION_SOURCE_SCHEMA_V4:
        if new_quality_expected:
            raise ValueError("research_quality_publication_original_missing")
        return unavailable("research_quality_legacy_unavailable")
    if published_at > at:
        return unavailable("research_quality_publication_pending")
    from .research_graph_history_versions import (
        RetainedPublicationQualityRead,
        consume_retained_publication_quality_in_session,
    )

    def retained_original(original: RetainedPublicationQualityRead) -> R:
        quality = ResearchQualityViewV1.model_validate_json(original.view_json)
        mapped = (
            ObservedExecutionRef.model_validate_json(original.selected_observation_ref_json)
            if original.selected_observation_ref_json is not None
            else None
        )
        return consume(quality, mapped)

    retained, consumed = consume_retained_publication_quality_in_session(
        session, run_id, as_of=at, consume=retained_original
    )
    if retained:
        return cast(R, consumed)
    reference = parse_graph_final_ref(body.get("graph_final_ref"))
    original = read_graph_final_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=None,
        require_current_permission=False,
    )
    reviews = list(
        session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "independent_reviewer",
            )
        )
    )
    if len(reviews) != 1:
        raise ValueError("research_quality_publication_review_original_missing")
    declared = original.result.model_copy(update={"reviews": [_decode_review_row(reviews[0])]})
    _read_publication_source_in_session(
        session,
        run=run,
        reference=reference,
        declared=declared,
        expected_lease_owner=None,
        candidate=None,
        gate=None,
    )
    selection = read_graph_recipe_selection_for_run_in_session(session, run_id)
    if not isinstance(selection, FrozenGraphRecipeSelectionV3):
        raise ValueError("research_quality_publication_selection_mixed")
    quality_binding = body["quality_binding"]
    sidecar_id = quality_binding["quality_sidecar_id"]
    sidecar = read_quality_sidecar_in_session(session, sidecar_id, selection=selection.receipt)
    if sidecar.phase != "final" or sidecar.recorded_at > published_at:
        raise ValueError("research_quality_publication_sidecar_clock_invalid")
    runtime = json.loads(sidecar.prepared_runtime_json)
    budget = runtime["prepared_budget"]
    usage = (
        ProviderConversationUsageCensusV1.model_validate_json(
            json.dumps(runtime["authenticated_conversation_usage"], ensure_ascii=False)
        )
        if "authenticated_conversation_usage" in runtime else None
    )
    quality = ResearchQualityViewV1.model_validate(
        {
            "run_id": run_id,
            "as_of": at,
            "status": "available",
            "gate_status": body["gate"]["status"],
            "published_at": published_at,
            "publication_ref": {
                "output_id": source.output_id,
                "role": source.role,
                "schema_version": source.schema_version,
                "payload_hash": source.payload_hash,
                "recorded_at": published_at,
            },
            "graph_final_ref": reference.row,
            "recipe_selection_ref": selection.reference,
            "quality_sidecar_id": sidecar_id,
            "decision": sidecar.quality_decision,
            "budget": {
                "max_tool_calls": budget["max_tool_calls"],
                "max_subagents": budget["max_subagents"],
                "max_evidence_rounds": budget["max_evidence_rounds"],
                "max_estimated_cost_usd": budget["max_estimated_cost_usd"],
                "deadline_at": runtime["deadline_at"],
            },
            "unknown_receipt_codes": sidecar.unknown_receipt_codes,
            "usage": usage,
        }
    )
    return consume(quality, original.receipt.selected_synthesis_observation_ref)


class ResearchQualityQuery:
    def __init__(self, database: Database):
        self.database = database

    def read(self, run_id: str, *, as_of: datetime | None = None) -> ResearchQualityViewV1:
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            return read_published_research_quality_in_session(
                session, run_id, as_of=as_of or utcnow()
            )
