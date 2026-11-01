"""Read only the quality bound to an actual report publication."""

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from packages.kernel.decision_hub_kernel.application.research_quality_view import (
    ResearchQualityQuery,
    ResearchQualityViewV1,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.kernel.decision_hub_kernel.ports.research_publication_bundle import (
    FrozenPublicationBundleV1,
)
from packages.kernel.decision_hub_kernel.ports.research_report_snapshot import (
    ConfirmedReportSnapshotV1,
)
from packages.kernel.decision_hub_kernel.ports.research_report_recovery import (
    ReportRecoveryStatusV1, ReportRecoveryCommandV1, ReportRecoveryReceiptV1,
)
from packages.kernel.decision_hub_kernel.ports.research_saved_confirmation import (
    SavedConfirmationStatusV1, SavedConfirmationCommandV1, SavedConfirmationReceiptV1,
)
from packages.kernel.decision_hub_kernel.application.research_saved_confirmation_commands import ResearchSavedConfirmationCommandService
from packages.kernel.decision_hub_kernel.application.research_report_recovery_commands import ReportRecoveryCommandService
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleCommitUnconfirmed, BundleWriterBeginBusy,
)


from packages.kernel.decision_hub_kernel.application.research_publication_jobs48 import _ReportRecoveryObservations74


class _PublicationLifecycle(Protocol):
    def status(self, run_id: str) -> dict[str, object]: ...
    def known_cancel_recovery_status(self, run_id: str) -> dict[str, object]: ...
    def audit_original(self, run_id: str, *, as_of: datetime) -> ResearchQualityViewV1: ...

    def read_with_head(
        self, run_id: str,
    ) -> tuple[FrozenPublicationBundleV1 | None, dict[str, object]]: ...

    def read_confirmed_report_snapshot(
        self, run_id: str,
    ) -> tuple[FrozenPublicationBundleV1 | None, dict[str, object] | None]: ...


def research_quality_router(
    database: Database, *, publication_lifecycle: _PublicationLifecycle,
    clock: Callable[[], datetime] = utcnow,
) -> APIRouter:
    router = APIRouter(tags=["research-quality"])
    from apps.hub_api.research_report_audit_routes import research_report_audit_router
    router.include_router(research_report_audit_router(
        database, lifecycle=publication_lifecycle, clock=clock))
    query = ResearchQualityQuery(database)
    recovery_commands = ReportRecoveryCommandService(database, publication_lifecycle, clock=clock)

    saved_confirmation_commands = ResearchSavedConfirmationCommandService(database, publication_lifecycle, clock=clock)
    from packages.kernel.decision_hub_kernel.application.research_report_recovery_commands import PreparationAuthorizationRecoveryCommandService
    from packages.kernel.decision_hub_kernel.ports.research_report_recovery import (
        PreparationRecoveryStatusV2,PreparationRecoveryCommandV2,PreparationRecoveryReceiptV2)
    preparation_recovery_commands=PreparationAuthorizationRecoveryCommandService(database,publication_lifecycle,clock=clock)

    def read_quality(
        run_id: str,
    ) -> tuple[ResearchQualityViewV1, dict[str, object], FrozenPublicationBundleV1 | None]:
        try:
            bundle, head = publication_lifecycle.read_with_head(run_id)
            if head["state"] == "confirmed":
                if bundle is None or bundle.run_id != run_id:
                    raise ValueError("research_quality_confirmed_bundle_missing")
                return bundle.quality(read_at=clock()), head, bundle
            if head["state"] == "legacy_full_original":
                if bundle is not None:
                    raise ValueError("research_quality_legacy_bundle_forbidden")
                view = query.read(run_id, as_of=clock())
                if publication_lifecycle.status(run_id) != head:
                    raise ValueError("research_quality_legacy_serving_head_changed")
                return view, head, None
            # An existing durable job always owns this read path. Unknown or
            # revoked preparation never grants a fallback to the full reader.
            raise HTTPException(409, "research_quality_bundle_not_confirmed", headers={
                "X-Decision-Hub-Report-Read-Mode": str(head["state"]),
                "X-Decision-Hub-Report-State-Version": str(head["state_version"]),
            })
        except KeyError as exc:
            raise HTTPException(404, "research_quality_run_not_found") from exc
        except (ValueError, PermissionError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_quality_original_verification_failed") from exc

    def serving_headers(head: dict[str, object]) -> dict[str, str]:
        return {
            "X-Decision-Hub-Report-Read-Mode": (
                "confirmed_bundle" if head["state"] == "confirmed" else "legacy_full_original"
            ),
            "X-Decision-Hub-Report-State-Version": str(head["state_version"]),
        }

    @router.get("/v1/research/runs/{run_id}/report/status")
    def report_status(run_id: str) -> dict[str, object]:
        try:
            return publication_lifecycle.status(run_id)
        except KeyError as exc:
            raise HTTPException(404, "research_quality_run_not_found") from exc
        except (ValueError, PermissionError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_quality_original_verification_failed") from exc

    @router.get("/v1/research/runs/{run_id}/report/recovery/status", response_model=ReportRecoveryStatusV1)
    def recovery_status(run_id: str) -> ReportRecoveryStatusV1:
        """Explain same-Run recovery requirements without granting a command."""
        try:
            value=ReportRecoveryStatusV1.model_validate(publication_lifecycle.known_cancel_recovery_status(run_id))
            if value.run_id!=run_id:
                raise ValueError("research_report_recovery_identity_changed")
            return value
        except KeyError as exc:
            raise HTTPException(404, "research_quality_run_not_found") from exc
        except (ValueError, PermissionError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_report_recovery_status_unavailable") from exc


    def recovery_owner(owner_id: str | None) -> str:
        # Same header requirement as main.require_owner; header alone grants nothing.
        if not owner_id:
            raise HTTPException(403, "owner_identity_required")
        return owner_id

    @router.post("/v1/research/runs/{run_id}/report/recovery/commands",
                 response_model=ReportRecoveryReceiptV1)
    def recover_report(run_id: str, payload: ReportRecoveryCommandV1,
                       owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128),
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
        observation = _ReportRecoveryObservations74(run_id, payload.expected_request_id,
            payload.expected_generation, payload.expected_state_version,
            scope="route", request_id=payload.request_id)
        observation_error = None
        try:
            owner = recovery_owner(owner_id)
            if not idempotency_key or idempotency_key != payload.request_id:
                raise HTTPException(400, "Idempotency-Key must match request_id")
            try:
                return recovery_commands.apply(run_id, payload, owner_id=owner)
            except KeyError as exc:
                raise HTTPException(404, "research_report_recovery_not_found") from exc
            except PermissionError as exc:
                raise HTTPException(403, "research_report_recovery_owner_verification_failed") from exc
            except (BundleCommitUnconfirmed, BundleWriterBeginBusy) as exc:
                raise HTTPException(409, "research_report_recovery_unconfirmed_read_receipt") from exc
            except (ValueError, TypeError, OSError) as exc:
                raise HTTPException(409, "research_report_recovery_verification_failed") from exc
        except BaseException as error:
            observation_error = error
            raise
        finally:
            observation.terminal(
                observation_error.__cause__ if type(observation_error) is HTTPException else observation_error,
                transport_error=type(observation_error) is HTTPException)

    @router.get("/v1/research/runs/{run_id}/report/recovery/commands/{request_id}",
                response_model=ReportRecoveryReceiptV1)
    def recovery_receipt(run_id: str, request_id: str,
                         owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128)):
        owner = recovery_owner(owner_id)
        try:
            return recovery_commands.receipt(run_id, request_id, owner_id=owner)
        except KeyError as exc:
            raise HTTPException(404, "research_report_recovery_receipt_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(403, "research_report_recovery_owner_verification_failed") from exc
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_report_recovery_receipt_unconfirmed") from exc

    @router.get("/v1/research/runs/{run_id}/report/saved-confirmation/status", response_model=SavedConfirmationStatusV1)
    def saved_confirmation_status(run_id: str,
            owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128)):
        owner = recovery_owner(owner_id)
        try:
            return saved_confirmation_commands.status(run_id, owner_id=owner)
        except KeyError as exc:
            raise HTTPException(404, "research_saved_confirmation_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(403, "research_saved_confirmation_owner_verification_failed") from exc
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_saved_confirmation_status_unavailable") from exc

    @router.post("/v1/research/runs/{run_id}/report/saved-confirmation/commands", response_model=SavedConfirmationReceiptV1)
    def authorize_saved_confirmation(run_id: str, payload: SavedConfirmationCommandV1,
            owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
        owner = recovery_owner(owner_id)
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(400, "Idempotency-Key must match request_id")
        try:
            return saved_confirmation_commands.apply(run_id, payload, owner_id=owner)
        except KeyError as exc:
            raise HTTPException(404, "research_saved_confirmation_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(403, "research_saved_confirmation_owner_verification_failed") from exc
        except (BundleCommitUnconfirmed, BundleWriterBeginBusy) as exc:
            raise HTTPException(409, "research_saved_confirmation_unconfirmed_read_receipt") from exc
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_saved_confirmation_verification_failed") from exc

    @router.get("/v1/research/runs/{run_id}/report/saved-confirmation/commands/{request_id}", response_model=SavedConfirmationReceiptV1)
    def saved_confirmation_receipt(run_id: str, request_id: str,
            owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128)):
        owner = recovery_owner(owner_id)
        try:
            return saved_confirmation_commands.receipt(run_id, request_id, owner_id=owner)
        except KeyError as exc:
            raise HTTPException(404, "research_saved_confirmation_receipt_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(403, "research_saved_confirmation_owner_verification_failed") from exc
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_saved_confirmation_receipt_unconfirmed") from exc

    @router.get("/v1/research/runs/{run_id}/report/preparation-recovery/status",response_model=PreparationRecoveryStatusV2)
    def preparation_recovery_status(run_id:str,
            owner_id:str|None=Header(default=None,alias="X-Owner-Id",max_length=128)):
        owner=recovery_owner(owner_id)
        try:return preparation_recovery_commands.status(run_id,owner_id=owner)
        except KeyError as error:raise HTTPException(404,"research_preparation_recovery_not_found") from error
        except PermissionError as error:raise HTTPException(403,"research_preparation_recovery_owner_verification_failed") from error
        except (ValueError,TypeError,OSError) as error:raise HTTPException(409,"research_preparation_recovery_status_unavailable") from error

    @router.post("/v1/research/runs/{run_id}/report/preparation-recovery/commands",response_model=PreparationRecoveryReceiptV2)
    def authorize_preparation_recovery(run_id:str,payload:PreparationRecoveryCommandV2,
            owner_id:str|None=Header(default=None,alias="X-Owner-Id",max_length=128),
            idempotency_key:str|None=Header(default=None,alias="Idempotency-Key",max_length=128)):
        owner=recovery_owner(owner_id)
        if not idempotency_key or idempotency_key!=payload.request_id:
            raise HTTPException(400,"Idempotency-Key must match request_id")
        try:return preparation_recovery_commands.apply(run_id,payload,owner_id=owner)
        except KeyError as error:raise HTTPException(404,"research_preparation_recovery_not_found") from error
        except PermissionError as error:raise HTTPException(403,"research_preparation_recovery_owner_verification_failed") from error
        except (BundleCommitUnconfirmed,BundleWriterBeginBusy) as error:raise HTTPException(409,"research_preparation_recovery_unconfirmed_read_receipt") from error
        except (ValueError,TypeError,OSError) as error:raise HTTPException(409,"research_preparation_recovery_verification_failed") from error

    @router.get("/v1/research/runs/{run_id}/report/preparation-recovery/commands/{request_id}",response_model=PreparationRecoveryReceiptV2)
    def preparation_recovery_receipt(run_id:str,request_id:str,
            owner_id:str|None=Header(default=None,alias="X-Owner-Id",max_length=128)):
        owner=recovery_owner(owner_id)
        try:return preparation_recovery_commands.receipt(run_id,request_id,owner_id=owner)
        except KeyError as error:raise HTTPException(404,"research_preparation_recovery_receipt_not_found") from error
        except PermissionError as error:raise HTTPException(403,"research_preparation_recovery_owner_verification_failed") from error
        except (ValueError,TypeError,OSError) as error:raise HTTPException(409,"research_preparation_recovery_receipt_unconfirmed") from error

    @router.post("/v1/research/runs/{run_id}/report/audit", response_model=ResearchQualityViewV1)
    def audit_report(run_id: str, response: Response) -> ResearchQualityViewV1:
        """Explicit expensive RO original reader; never part of fast GET.

        Current serving permission is checked before and after. A saved bundle
        is compared with every public original field, except this read's clock.
        A legacy original has no saved bundle comparison. Neither path claims
        frozen replay, provider execution, or a new publication confirmation.
        """
        try:
            bundle, head = publication_lifecycle.read_with_head(run_id)
            if head["state"] not in {"confirmed", "legacy_full_original"}:
                raise HTTPException(409, "research_quality_bundle_not_confirmed", headers={
                    "X-Decision-Hub-Report-Read-Mode": str(head["state"]),
                    "X-Decision-Hub-Report-State-Version": str(head["state_version"]),
                })
            view = publication_lifecycle.audit_original(run_id, as_of=clock())
            if view.status != "available" or view.decision is None:
                raise HTTPException(404, "research_quality_report_not_published")
            if head["state"] == "confirmed":
                if bundle is None or bundle.run_id != run_id:
                    raise ValueError("research_quality_confirmed_bundle_missing")
                saved = bundle.quality(read_at=clock())
                if saved.model_dump(mode="json", exclude={"as_of"}) != view.model_dump(mode="json", exclude={"as_of"}):
                    raise ValueError("research_quality_full_audit_changed")
                comparison = "saved_bundle_full_quality"
            else:
                if bundle is not None:
                    raise ValueError("research_quality_legacy_bundle_forbidden")
                comparison = "legacy_original_only"
            if publication_lifecycle.status(run_id) != head:
                raise ValueError("research_quality_audit_serving_head_changed")
            response.headers.update({
                "X-Decision-Hub-Report-Read-Mode": "full_original_audit",
                "X-Decision-Hub-Report-State-Version": str(head["state_version"]),
                "X-Decision-Hub-Report-Audit-Comparison": comparison,
            })
            return view
        except KeyError as exc:
            raise HTTPException(404, "research_quality_run_not_found") from exc
        except (ValueError, PermissionError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_quality_full_audit_verification_failed") from exc

    @router.get("/v1/research/runs/{run_id}/quality", response_model=ResearchQualityViewV1)
    def quality(run_id: str, response: Response) -> ResearchQualityViewV1:
        view, head, _bundle = read_quality(run_id)
        response.headers.update(serving_headers(head))
        return view

    @router.get("/v1/research/runs/{run_id}/report/download")
    def download_report(run_id: str) -> Response:
        view, head, bundle = read_quality(run_id)
        if view.status != "available" or view.decision is None:
            raise HTTPException(404, "research_quality_report_not_published")
        content = bundle.report() if bundle is not None else view.decision.full_report_json.encode("utf-8")
        report_hash = view.decision.full_report_hash
        if hashlib.sha256(content).hexdigest() != report_hash:
            raise HTTPException(409, "research_quality_original_verification_failed")
        safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)[:128] or "report"
        return Response(
            content=content,
            media_type="application/json",
            headers={
                **serving_headers(head),
                "Content-Disposition": f'attachment; filename="research-{safe_run_id}.json"',
                "ETag": f'"{report_hash}"',
            },
        )

    def confirmed_snapshot(run_id: str) -> tuple[ConfirmedReportSnapshotV1, bytes]:
        try:
            bundle, head = publication_lifecycle.read_confirmed_report_snapshot(run_id)
            if bundle is None or head is None:
                # Legacy reports retain their existing full-reader path. A GET
                # never backfills a snapshot or manufactures a new ACK.
                raise HTTPException(404, "research_report_snapshot_not_available")
            if head["state"] != "confirmed_report_snapshot":
                raise ValueError("report_snapshot_read_scope_changed")
            view = bundle.quality(read_at=clock())
            if view.status != "available" or view.decision is None:
                raise ValueError("report_snapshot_quality_not_available")
            value = ConfirmedReportSnapshotV1.model_validate({
                "run_id": run_id, "state_version": head["state_version"],
                "source_bundle_sha256": head["bundle_sha256"],
                "published_at": view.published_at, "as_of": view.as_of,
                "report_sha256": bundle.report_sha256, "report_bytes": bundle.report_bytes,
                "quality_json": view.model_dump_json(),
            })
            return value, bundle.report()
        except KeyError as exc:
            raise HTTPException(404, "research_quality_run_not_found") from exc
        except (ValueError, PermissionError, TypeError, OSError) as exc:
            raise HTTPException(409, "research_report_snapshot_verification_failed") from exc

    def snapshot_headers(value: ConfirmedReportSnapshotV1) -> dict[str, str]:
        return {
            "X-Decision-Hub-Report-Read-Mode": value.read_scope,
            "X-Decision-Hub-Report-State-Version": str(value.state_version),
            "X-Decision-Hub-Report-Material-Audit": value.material_audit,
        }

    @router.get("/v1/research/runs/{run_id}/report/snapshot",
                response_model=ConfirmedReportSnapshotV1)
    def report_snapshot(run_id: str, response: Response) -> ConfirmedReportSnapshotV1:
        value, _content = confirmed_snapshot(run_id)
        response.headers.update(snapshot_headers(value))
        return value

    @router.get("/v1/research/runs/{run_id}/report/snapshot/download")
    def download_snapshot(run_id: str) -> Response:
        value, content = confirmed_snapshot(run_id)
        safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)[:128] or "report"
        return Response(content=content, media_type="application/json", headers={
            **snapshot_headers(value),
            "Content-Disposition": f'attachment; filename="research-{safe_run_id}.json"',
            "ETag": f'"{value.report_sha256}"',
        })

    return router
