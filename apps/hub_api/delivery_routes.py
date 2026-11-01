import hashlib
import json
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ResearchInputEvidence,
    ResearchRunCommandReceipt,
    RevisionFeedbackEvaluation,
    RevisionFeedbackEvaluationBasis,
)
from packages.contracts_py.decision_hub_contracts.generated.product_delivery import (
    ReadReceiptCommand,
    ReadReceiptView,
    ReportDeliveryPage,
    StudyTrackingCommand,
    StudyTrackingReceipt,
)
from packages.contracts_py.decision_hub_contracts.generated.read_command_receipt import (
    ReadCommandReceipt,
)
from packages.contracts_py.decision_hub_contracts.generated.research_market import (
    ResearchMarketCatalog,
)
from packages.kernel.decision_hub_kernel.application.market_scope import research_market_catalog
from packages.kernel.decision_hub_kernel.application.product_delivery import ProductDeliveryService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.research_submission import (
    owner_root_creation_receipt_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.application.revision_feedback_evaluation import (
    RevisionFeedbackEvaluationError,
    RevisionFeedbackEvaluationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ProductCommandRecord,
    ResearchCommandRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_initial_begin_busy

type FeedbackEvaluationRead = RevisionFeedbackEvaluation | RevisionFeedbackEvaluationBasis | None


class OriginalInputRequestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    output_id: str
    role: Literal["research_task_request"]
    schema_version: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    recorded_at: AwareDatetime


class OriginalInputReceiptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["research-original-input-receipt.v1"]
    owner_id: str
    run_id: str
    origin: Literal["authorized_original_input"]
    public_evidence_accepted: Literal[False]
    input: ResearchInputEvidence
    excerpt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_receipt: OriginalInputRequestReceipt


def _original_input_receipt_in_session(
    session: Session, run_id: str, evidence_id: str, owner: str
) -> OriginalInputReceiptView:
    # Existing child authorization is unchanged. Only a strict original v2
    # root creation receipt can establish ownership for a new manual root.
    creation = _linked_command_receipt_in_session(session, run_id, owner)
    root_creation = None
    if creation is None:
        root_creation = owner_root_creation_receipt_in_session(session, run_id, owner)
        if root_creation is None:
            raise PermissionError("research_original_input_owner_binding_unavailable")
    if evidence_id.startswith("library:"):
        raise PermissionError("research_original_input_library_receipt_required")
    run = session.get(RunRecord, run_id)
    assert run is not None
    saved = read_task_request(session, run)
    if saved is None:
        raise ValueError("research_original_input_request_missing")
    request, request_hash, _source = saved
    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.role == "research_task_request",
    ))
    if row is None:
        raise ValueError("research_original_input_request_missing")
    if root_creation is not None and root_creation.created_at > as_utc(row.created_at):
        raise ValueError("research_original_input_request_invalid")
    body = json.loads(row.payload_json)
    # Library inputs keep their original private-document permission path.
    # This generic reader never turns library content into a public fallback.
    if any(item.get("evidence_id") == evidence_id for item in body.get("library_inputs", [])):
        raise PermissionError("research_original_input_library_receipt_required")
    if (row.payload_hash != hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
            or body["request_hash"] != request_hash):
        raise ValueError("research_original_input_request_invalid")
    items = [item for item in request.input_evidence if item.evidence_id == evidence_id]
    if len(items) != 1:
        raise KeyError("research_original_input_not_found")
    item = items[0]
    return OriginalInputReceiptView(
        schema_version="research-original-input-receipt.v1", owner_id=owner,
        run_id=run_id, origin="authorized_original_input", public_evidence_accepted=False,
        input=ResearchInputEvidence.model_validate(item.model_dump(mode="json")),
        excerpt_sha256=hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest(),
        request_hash=request_hash,
        request_receipt=OriginalInputRequestReceipt(
            output_id=row.output_id, role="research_task_request",
            schema_version=row.schema_version,
            payload_hash=row.payload_hash, recorded_at=as_utc(row.created_at),
        ),
    )


def _linked_command_receipt_in_session(
    session: Session, run_id: str, owner: str
) -> ResearchRunCommandReceipt | None:
    """Discover only an original owner-bound receipt that created this child."""
    if owner == ResearchCommandService.LEGACY_INTERNAL_OWNER:
        raise ValueError("research_command_legacy_owner_unknown")
    run = session.get(RunRecord, run_id)
    if run is None or run.parent_run_id is None:
        return None
    rows = session.execute(
        select(ResearchCommandRecord, ProductCommandRecord)
        .join(
            ProductCommandRecord,
            ProductCommandRecord.request_id == ResearchCommandRecord.request_id,
        )
        .where(
            ResearchCommandRecord.target_run_id == run_id,
            ProductCommandRecord.owner_id == owner,
            ProductCommandRecord.kind == ResearchCommandService.KIND,
        )
    ).all()
    if not rows:
        # A missing original owner binding cannot be supplied by the current header.
        return None
    source = session.get(RunRecord, run.parent_run_id)
    if (
        source is None
        or source.strategy_version != "research.v1"
        or run.strategy_version != "research.v1"
        or run.admission_origin != "manual"
        or not run.study_id
        or source.study_id != run.study_id
        or source.event_id != run.event_id
    ):
        raise ValueError("research_command_linked_receipt_invalid")
    receipts: list[ResearchRunCommandReceipt] = []
    for record, original in rows:
        # This original verifier checks payload hash, stored owner, time, request
        # and deterministic child identity without changing the original bytes.
        receipt = ResearchCommandService._receipt_view(original)  # pyright: ignore[reportPrivateUsage]
        if (
            original.owner_id != owner
            or receipt.owner_id != owner
            or receipt.result.status != "accepted"
            or receipt.command.command not in {"retry", "recheck"}
            or receipt.result.target_run_id != run_id
            or receipt.source_run_id != run.parent_run_id
            or record.run_id != run.parent_run_id
            or record.status != "accepted"
            or record.command != receipt.command.command
            or record.reason != receipt.command.reason
            or receipt.result.created_at != as_utc(record.created_at)
            or receipt.result.created_at != as_utc(run.created_at)
            or run.idempotency_key != f"research-command:{receipt.request_id}"
        ):
            raise ValueError("research_command_linked_receipt_invalid")
        receipts.append(receipt)
    if len(receipts) != 1:
        raise ValueError("research_command_linked_receipt_ambiguous")
    return receipts[0]


def _latest_own_command_receipt_in_session(
    session: Session, run_id: str, owner: str
) -> ResearchRunCommandReceipt | None:
    """Read the latest persisted own operation for display, never input consent."""
    if owner == ResearchCommandService.LEGACY_INTERNAL_OWNER:
        raise ValueError("research_command_legacy_owner_unknown")
    run = session.get(RunRecord, run_id)
    if run is None or run.strategy_version != "research.v1":
        return None
    row = session.execute(
        select(ResearchCommandRecord, ProductCommandRecord)
        .join(
            ProductCommandRecord,
            ProductCommandRecord.request_id == ResearchCommandRecord.request_id,
        )
        .where(
            ResearchCommandRecord.run_id == run_id,
            ProductCommandRecord.owner_id == owner,
        )
        .order_by(
            ResearchCommandRecord.created_at.desc(),
            ResearchCommandRecord.request_id.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    record, original = row
    receipt = ResearchCommandService._receipt_view(original)  # pyright: ignore[reportPrivateUsage]
    if (
        original.owner_id != owner
        or original.target_id != run_id
        or original.kind != ResearchCommandService.KIND
        or receipt.owner_id != owner
        or receipt.source_run_id != run_id
        or record.request_id != receipt.request_id
        or record.run_id != run_id
        or record.command != receipt.command.command
        or record.reason != receipt.command.reason
        or record.status != receipt.result.status
        or record.target_run_id != receipt.result.target_run_id
        or as_utc(record.created_at) != receipt.result.created_at
    ):
        raise ValueError("research_command_own_receipt_invalid")
    return receipt


def _display_command_receipt_in_session(
    session: Session, run_id: str, owner: str
) -> ResearchRunCommandReceipt | None:
    own = _latest_own_command_receipt_in_session(session, run_id, owner)
    # An invalid own original raises above; it must not become incoming success.
    return own if own is not None else _linked_command_receipt_in_session(session, run_id, owner)


def delivery_router(
    database: Database, require_owner: Callable[[str | None], str]
) -> APIRouter:
    router = APIRouter(tags=["product-delivery"])
    service = ProductDeliveryService(database)
    feedback_evaluations = RevisionFeedbackEvaluationService(database)

    @router.get(
        "/v1/research/runs/{run_id}/input-receipts/{evidence_id}",
        response_model=OriginalInputReceiptView,
    )
    def original_input_receipt(
        run_id: str, evidence_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> OriginalInputReceiptView:
        owner = require_owner(x_owner_id)
        try:
            with database.session() as session:
                return _original_input_receipt_in_session(session, run_id, evidence_id, owner)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "research_original_input_not_found") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(409, "research_original_input_receipt_invalid") from exc

    @router.get(
        "/v1/research/runs/{run_id}/linked-command-receipt",
        response_model=ResearchRunCommandReceipt | None,
    )
    def linked_command_receipt(
        run_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchRunCommandReceipt | None:
        owner = require_owner(x_owner_id)
        try:
            with database.session() as session:
                return _display_command_receipt_in_session(session, run_id, owner)
        except ValueError as exc:
            detail = (
                str(exc)
                if str(exc).startswith("research_command_")
                else "research_command_linked_receipt_invalid"
            )
            raise HTTPException(409, detail) from exc
        except (TypeError, KeyError) as exc:
            raise HTTPException(409, "research_command_linked_receipt_invalid") from exc

    @router.get("/v1/product/research-scopes", response_model=ResearchMarketCatalog)
    def scopes():
        return research_market_catalog()

    @router.get("/v1/product/reports", response_model=ReportDeliveryPage)
    def reports(
        limit: int = Query(12, ge=1, le=100),
        offset: int = Query(0, ge=0),
        unread_only: bool = False,
        run_id: str | None = None,
        x_owner_id: str | None = Header(None, max_length=128),
    ):
        owner = require_owner(x_owner_id)
        return service.list_reports(
            owner, limit=limit, offset=offset, unread_only=unread_only, run_id=run_id
        )

    def execute[ReceiptT: ReadReceiptView | StudyTrackingReceipt](
        command: ReadReceiptCommand | StudyTrackingCommand,
        owner: str | None,
        key: str | None,
        callback: Callable[[str], ReceiptT],
    ) -> ReceiptT:
        owner = require_owner(owner)
        if key != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return callback(owner)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/v1/product/reports/{revision_id}/read", response_model=ReadReceiptView)
    def mark_read(
        revision_id: str,
        payload: ReadReceiptCommand,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ):
        try:
            return execute(
                payload,
                x_owner_id,
                idempotency_key,
                lambda owner: service.mark_read(revision_id, owner, payload),
            )
        except OperationalError as exc:
            if not is_sqlite_initial_begin_busy(exc):
                raise
            raise HTTPException(
                status_code=503, detail="product_read_command_storage_busy",
                headers={"Retry-After": "1"},
            ) from exc

    @router.get(
        "/v1/product/reports/{revision_id}/read/commands/{request_id}",
        response_model=ReadCommandReceipt | None,
    )
    def read_command_receipt(
        revision_id: str,
        request_id: str,
        read: bool = Query(...),
        x_owner_id: str | None = Header(None, max_length=128),
    ):
        owner = require_owner(x_owner_id)
        try:
            command = ReadReceiptCommand(request_id=request_id, read=read)
            return service.get_read_command_receipt(revision_id, owner, command)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/v1/studies/{study_id}/tracking-commands/{request_id}",
        response_model=StudyTrackingReceipt,
    )
    def tracking_command_receipt(
        study_id: str,
        request_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> StudyTrackingReceipt:
        owner = require_owner(x_owner_id)
        try:
            result = service.get_tracking_command_receipt(study_id, owner, request_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "study_tracking_command_not_found")
        return result

    @router.post("/v1/studies/{study_id}/tracking", response_model=StudyTrackingReceipt)
    def tracking(
        study_id: str,
        payload: StudyTrackingCommand,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ):
        return execute(
            payload,
            x_owner_id,
            idempotency_key,
            lambda owner: service.track(study_id, owner, payload),
        )

    def read_feedback_binding[ReceiptT: FeedbackEvaluationRead](
        callback: Callable[[], ReceiptT],
    ) -> ReceiptT:
        try:
            return callback()
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except RevisionFeedbackEvaluationError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/v1/product/reports/{revision_id}/feedback-evaluation/requests",
        response_model=RevisionFeedbackEvaluation,
    )
    def feedback_evaluation_request(
        revision_id: str,
        request_id: str = Query(..., min_length=1, max_length=256),
        x_owner_id: str | None = Header(None, max_length=128),
    ):
        owner = require_owner(x_owner_id)
        return read_feedback_binding(
            lambda: feedback_evaluations.get(revision_id, request_id, owner)
        )

    @router.get(
        "/v1/product/reports/{revision_id}/feedback-evaluation",
        response_model=RevisionFeedbackEvaluation | None,
    )
    def feedback_evaluation_latest(
        revision_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ):
        owner = require_owner(x_owner_id)
        return read_feedback_binding(lambda: feedback_evaluations.latest(revision_id, owner))

    @router.get(
        "/v1/product/reports/{revision_id}/feedback-evaluation/basis",
        response_model=RevisionFeedbackEvaluationBasis,
    )
    def feedback_evaluation_basis(
        revision_id: str,
        request_id: str = Query(..., min_length=1, max_length=256),
        x_owner_id: str | None = Header(None, max_length=128),
    ):
        owner = require_owner(x_owner_id)
        return read_feedback_binding(
            lambda: feedback_evaluations.basis(revision_id, request_id, owner)
        )

    return router
