"""Durable automatic research status and owner-triggered current-asset refresh."""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException

from packages.contracts_py.decision_hub_contracts.generated.risk_research_request import (
    RiskResearchCommand,
    RiskResearchOwnerReceipt,
    RiskResearchRequest,
    RiskResearchStatus,
)
from packages.kernel.decision_hub_kernel.application.risk_alert_recheck import (
    RiskAlertRecheckService,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.kernel.decision_hub_kernel.ports.risk_alert_recheck import (
    RiskAlertRecheckCommand,
    RiskAlertRecheckReceipt,
    RiskAlertRecheckState,
)


def risk_research_router(
    service: RiskResearchAdmissionService,
    require_owner: Callable[[str | None], str],
    *,
    clock: Callable[[], datetime] = utcnow,
) -> APIRouter:
    router = APIRouter(tags=["market-risk-research"])
    scopes = {spot_scope(x).scope_id: x for x in ("BTC-USDT", "ETH-USDT")}
    rechecks = RiskAlertRecheckService(service)

    def recheck_call(operation):
        try:
            return operation()
        except KeyError as error:
            raise HTTPException(404, "risk_alert_not_found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/v1/risk/alerts/{alert_id}/research-recheck", response_model=RiskAlertRecheckState)
    def recheck_conditions(alert_id: str):
        return recheck_call(lambda: rechecks.conditions(alert_id))

    @router.post(
        "/v1/risk/alerts/{alert_id}/research-recheck", response_model=RiskAlertRecheckReceipt
    )
    def recheck_request(
        alert_id: str,
        command: RiskAlertRecheckCommand,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        owner = require_owner(owner_id)
        if idempotency_key != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        return recheck_call(lambda: rechecks.accept(alert_id, owner, command))

    @router.get(
        "/v1/risk/alerts/{alert_id}/research-recheck/commands/{request_id}",
        response_model=RiskAlertRecheckReceipt,
    )
    def recheck_receipt(
        alert_id: str,
        request_id: str,
        expected_head_hash: str,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
    ):
        owner = require_owner(owner_id)
        command = recheck_call(
            lambda: RiskAlertRecheckCommand(
                request_id=request_id, expected_head_hash=expected_head_hash
            )
        )
        result = recheck_call(lambda: rechecks.get_receipt(alert_id, owner, command))
        if result is None:
            raise HTTPException(404, "risk_alert_recheck_receipt_not_found")
        return result

    def instrument(scope_id):
        if scope_id not in scopes:
            raise HTTPException(404, "risk_scope_not_found")
        return scopes[scope_id]

    @router.get("/v1/risk/scopes/{scope_id}/research", response_model=RiskResearchStatus)
    def status(scope_id: str):
        instrument(scope_id)
        return RiskResearchStatus.model_validate(
            {
                "schema_version": "risk-research-status.v1",
                "scope_id": scope_id,
                "enabled": service.enabled,
                "as_of": clock(),
                "request": service.latest(scope_id),
            }
        )

    @router.post("/v1/risk/scopes/{scope_id}/research", response_model=RiskResearchRequest)
    def request(
        scope_id: str,
        command: RiskResearchCommand,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        owner = require_owner(owner_id)
        if idempotency_key != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return service.stage(
                instrument(scope_id),
                request_id=command.request_id,
                source_ref=f"owner-command:{command.request_id}",
                source={"owner_id": owner},
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get(
        "/v1/risk/scopes/{scope_id}/research/commands/{command_id}",
        response_model=RiskResearchOwnerReceipt,
    )
    def owner_receipt(
        scope_id: str, command_id: str, owner_id: str | None = Header(None, alias="X-Owner-Id")
    ):
        instrument(scope_id)
        owner = require_owner(owner_id)
        if not command_id or len(command_id) > 128:
            raise HTTPException(404, "risk_research_owner_receipt_not_found")
        try:
            result = service.owner_command_status(scope_id, owner, command_id)
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(409, "risk_research_owner_receipt_invalid") from error
        if result is None:
            raise HTTPException(404, "risk_research_owner_receipt_not_found")
        return result

    return router
