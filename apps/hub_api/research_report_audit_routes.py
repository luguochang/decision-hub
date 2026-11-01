"""Explicit durable audits; GET never starts or repeats the full reader."""
from contextlib import asynccontextmanager
from fastapi import APIRouter, Header, HTTPException
from packages.kernel.decision_hub_kernel.application.research_report_audit_jobs import ReportAuditManager, ReportAuditCommitUnknown
from packages.kernel.decision_hub_kernel.ports.research_report_audit import (
    ReportAuditContextV1, ReportAuditRequestV1, ReportAuditReceiptV1,
    ReportAuditStateV1, ReportAuditResultReplyV1,
)

def research_report_audit_router(database, *, lifecycle, clock):
    manager = ReportAuditManager(database, lifecycle, clock=clock)

    @asynccontextmanager
    async def lifespan(_app):
        await manager.start()
        try:
            yield
        finally:
            await manager.close()

    router = APIRouter(tags=["research-report-audit"], lifespan=lifespan)

    def owner(value):
        # Request namespace only. Fresh ordinary report permission is required
        # independently on every acceptance, native read and result/status GET.
        if not value or value != value.strip():
            raise HTTPException(403, "owner_identity_required")
        return value

    def perform(action):
        try:
            return action()
        except KeyError as error:
            raise HTTPException(404, "research_report_audit_not_found") from error
        except PermissionError as error:
            raise HTTPException(403, "research_report_audit_read_permission_denied") from error
        except ReportAuditCommitUnknown as error:
            raise HTTPException(409, "research_report_audit_commit_unknown") from error
        except (ValueError, TypeError, OSError) as error:
            raise HTTPException(409, "research_report_audit_verification_failed") from error

    @router.get("/v1/research/runs/{run_id}/report/audits/context", response_model=ReportAuditContextV1)
    def context(run_id: str):
        return perform(lambda: manager.context(run_id))

    @router.post("/v1/research/runs/{run_id}/report/audits", response_model=ReportAuditReceiptV1, status_code=202)
    def accept(run_id: str, payload: ReportAuditRequestV1,
               owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
        actual_owner = owner(owner_id)
        if idempotency_key != payload.request_key:
            raise HTTPException(400, "Idempotency-Key must match request_key")
        return perform(lambda: manager.accept(run_id, payload, owner_id=actual_owner))

    @router.get("/v1/research/runs/{run_id}/report/audits/{request_key}", response_model=ReportAuditStateV1)
    def status(run_id: str, request_key: str,
               owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128)):
        actual_owner = owner(owner_id)
        return perform(lambda: manager.status(run_id, request_key, owner_id=actual_owner))

    @router.get("/v1/research/runs/{run_id}/report/audits/{request_key}/result", response_model=ReportAuditResultReplyV1)
    def result(run_id: str, request_key: str,
               owner_id: str | None = Header(default=None, alias="X-Owner-Id", max_length=128)):
        actual_owner = owner(owner_id)
        return perform(lambda: manager.result(run_id, request_key, owner_id=actual_owner))

    return router
