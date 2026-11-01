"""Local market risk read projections and idempotent owner commands."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from threading import Lock
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import AwareDatetime

from packages.contracts_py.decision_hub_contracts.generated.market_risk_view import (
    RiskAlertView,
    RiskMonitorCommand,
    RiskMonitorCommandReceipt,
    RiskReceiptCommand,
    RiskReceiptView,
    RiskScopesView,
    RiskSummaryView,
    RiskTimelineView,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.query_views.market_risk import MarketRiskQuery
from packages.kernel.decision_hub_kernel.application.market_path_read_v2 import MarketPathReaderV2
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.kernel.decision_hub_kernel.persistence.operation_phases import (
    OperationPhases, PhaseObservation,
)


def _summary_diagnostics(request: Request) -> OperationPhases | None:
    """Call-local bounded metadata; no request input selects diagnostic identity."""
    request_id = request.scope.get("decision_hub_request_id")
    if not isinstance(request_id, str) or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
        return None
    lock = Lock()
    observations: list[dict] = []
    dropped, finalized = 0, False
    flags = {"response_start_observed": False, "headers_send_returned": False,
             "final_body_send_returned": False, "send_error_observed": False}

    def append(entry: dict) -> None:
        nonlocal dropped
        if len(observations) < 64:
            observations.append(entry)
        else:
            dropped += 1

    def phase_sink(observation: PhaseObservation) -> None:
        with lock:
            if finalized:
                return
            append({"domain": "query_span", "phase": observation.phase,
                    "state": observation.state, "observed_at_utc": observation.observed_at,
                    "elapsed_ms": observation.elapsed_ms, "error_type": observation.error_type,
                    "sqlite_error_code": observation.sqlite_error_code})

    def response_observer(phase: str, state: str, *, observed_at_utc: str,
                          request_elapsed_ms: float, status_code: int | None,
                          error_type: str | None) -> None:
        nonlocal finalized
        if phase not in {"risk_response_start", "risk_headers_sent", "risk_final_body_sent",
                         "risk_send_error", "risk_cleanup_error", "risk_asgi_finally"}:
            return
        packet = None
        with lock:
            if finalized:
                return
            append({"domain": "ASGI_request_elapsed", "phase": phase, "state": state,
                    "observed_at_utc": observed_at_utc, "elapsed_ms": request_elapsed_ms,
                    "status_code": status_code, "error_type": error_type})
            if phase == "risk_response_start":
                flags["response_start_observed"] = True
            elif phase == "risk_headers_sent":
                flags["headers_send_returned"] = True
            elif phase == "risk_final_body_sent":
                flags["final_body_send_returned"] = True
            elif phase == "risk_send_error":
                flags["send_error_observed"] = True
            elif phase == "risk_asgi_finally":
                finalized = True
                started = {x["phase"] for x in observations
                           if x["domain"] == "query_span" and x["state"] == "started"}
                ended = {x["phase"] for x in observations if x["domain"] == "query_span"
                         and x["state"] in {"finished", "error"}}
                packet = {"schema_version": "risk-summary-request-timing.v1",
                          "request_id": request_id, "operation_id": request_id,
                          "process_id": os.getpid(), "observed_at_utc": observed_at_utc,
                          "request_elapsed_ms": request_elapsed_ms, "status_code": status_code,
                          "asgi_finally_state": state, "error_type": error_type, **flags,
                          "query_spans_closed": started <= ended and dropped == 0,
                          "observations_dropped": dropped, "observations": list(observations),
                          "authority": "diagnostic_only_not_permission_or_acceptance"}
        if packet is not None:
            logging.getLogger("uvicorn.error").info(
                "risk_summary_request_timing %s", json.dumps(packet, separators=(",", ":")))

    request.scope["decision_hub_risk_summary_response_observer"] = response_observer
    return OperationPhases(phase_sink, operation_id=request_id)


def risk_router(
    database: Database,
    data_root: Path,
    require_owner: Callable[[str | None], str],
    *,
    enabled: bool,
    clock: Callable[[], datetime] = utcnow,
) -> APIRouter:
    router = APIRouter(tags=["market-risk"])
    summary_reader = MarketPathReaderV2(database, RawBodyArchive(data_root / "raw-captures"))
    query = MarketRiskQuery(database, data_root, enabled=enabled, clock=clock, summary_reader=summary_reader)
    monitors = RiskMonitorService(database, clock=clock)

    def call[T, **P](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "risk_object_not_found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/v1/risk/scopes", response_model=RiskScopesView)
    def scopes():
        return query.catalog()

    @router.get("/v1/risk/scopes/{scope_id}/summary", response_model=RiskSummaryView)
    def summary(scope_id: str, request: Request):
        return call(query.summary, scope_id, diagnostics=_summary_diagnostics(request))

    @router.get("/v1/risk/scopes/{scope_id}/timeline", response_model=RiskTimelineView)
    def timeline(scope_id: str, start: AwareDatetime, end: AwareDatetime):
        return call(query.timeline, scope_id, start=start, end=end)

    @router.get("/v1/risk/alerts/{alert_id}", response_model=RiskAlertView)
    def alert(alert_id: str):
        return call(query.alert, alert_id)

    def receipt(
        alert_id: str,
        command: RiskReceiptCommand,
        kind: Literal["display", "ack"],
        owner_id: str | None,
    ) -> RiskReceiptView:
        require_owner(owner_id)
        result = call(
            monitors.record_receipt,
            alert_id,
            kind,
            request_id=command.request_id,
            client_time=command.client_time,
        )
        return RiskReceiptView.model_validate(
            {
                "schema_version": "market-risk-receipt.v1",
                "alert_id": alert_id,
                "request_id": command.request_id,
                "status": "recorded",
                "recorded_at": result["received_at"],
            }
        )

    @router.post("/v1/risk/alerts/{alert_id}/display-receipt", response_model=RiskReceiptView)
    def display(
        alert_id: str,
        command: RiskReceiptCommand,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
    ):
        return receipt(alert_id, command, "display", owner_id)

    @router.post("/v1/risk/alerts/{alert_id}/ack", response_model=RiskReceiptView)
    def acknowledge(
        alert_id: str,
        command: RiskReceiptCommand,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
    ):
        return receipt(alert_id, command, "ack", owner_id)

    @router.post("/v1/risk/monitors/{plan_id}/command", response_model=RiskMonitorCommandReceipt)
    def monitor_command(
        plan_id: str,
        command: RiskMonitorCommand,
        owner_id: str | None = Header(None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        if command.action == "resume" and not enabled:
            raise HTTPException(409, "risk_monitor_not_enabled")
        call(monitors.command, plan_id, command.action, request_id=command.request_id)
        return RiskMonitorCommandReceipt(
            schema_version="market-risk-monitor-command-receipt.v1",
            plan_id=plan_id,
            request_id=command.request_id,
            action=command.action,
            status="applied",
            monitor=call(query.monitor, plan_id),
        )

    return router
