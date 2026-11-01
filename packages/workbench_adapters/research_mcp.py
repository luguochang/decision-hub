from __future__ import annotations

import asyncio
import hmac
import json
import logging
import traceback
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    ResearchCapabilityInvocation,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityGateway

RESEARCH_CAPABILITY_HTTP_PATH = "/decision-hub/v1/research-capabilities/execute"
RESEARCH_CATALOG_HTTP_PATH = "/decision-hub/v1/research-capabilities/catalog"
RESEARCH_INVOCATION_HTTP_PATH = "/decision-hub/v1/research-capabilities/invoke-live"
_BRIDGE_KEY_HEADER = "x-decision-hub-bridge-key"
_MAX_HTTP_BODY_BYTES = 256_000
_LOGGER = logging.getLogger(__name__)


def _baseline_failure_diagnostic(error: Exception, identity) -> dict:
    """Keep failure locations and native codes; never SQL, messages or locals."""
    chain = []
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        frames = [{"module": frame.f_globals.get("__name__", "unknown"),
                   "function": frame.f_code.co_name, "line": line}
                  for frame, line in traceback.walk_tb(current.__traceback__)]
        item = {"class": type(current).__name__, "module": type(current).__module__,
                "frames": frames[-12:]}
        code = getattr(current, "sqlite_errorcode", None)
        name = getattr(current, "sqlite_errorname", None)
        if type(code) is int:
            item["sqlite_errorcode"] = code
        if isinstance(name, str) and len(name) <= 64:
            item["sqlite_errorname"] = name
        chain.append(item)
        original = getattr(current, "orig", None)
        current = (original if isinstance(original, BaseException)
                   else current.__cause__ or current.__context__)
    return {"schema_version": "research-baseline-failure-diagnostic.v1",
            "run_id": identity.run_id, "research_session_id": identity.research_session_id,
            "generation": identity.generation, "exception_chain": chain}


def build_research_mcp_server(
    gateway: ResearchCapabilityGateway,
    *,
    bridge_key: str | None = None,
    query_catalog: dict | None = None,
    baseline_service=None,
) -> MCPServer:
    """Expose only the canonical read-only research capability boundary."""

    server = MCPServer(
        name="decision-hub-research-capabilities",
        version="research-capability.v1",
        instructions=(
            "Use the single canonical capability tool for evidence acquisition. "
            "It is read-only, bounded and fail-closed; denied capabilities must remain gaps. "
            "Use one call per requirement and a unique request_id per attempt. A failed call "
            "does not cancel successful evidence and does not authorize changing the PIT cutoff."
        ),
    )

    @server.tool(
        name="research_capability_execute",
        description=(
            "Execute one audited Decision Hub research capability and return canonical "
            "EvidenceCandidate records. For official documents provide target_url; for market "
            "snapshots provide the manifest-approved symbols and fields. Retry only when the "
            "structured error is retryable, never by changing permissions or PIT cutoff."
        ),
        structured_output=True,
    )
    async def research_capability_execute(
        request_id: str,
        capability_id: str,
        requirement_id: str,
        query: str,
        research_session_id: str,
        round: int,
        mode: Literal["live", "replay"],
        observed_at: str,
        cutoff_at: str,
        target_url: str | None = None,
        symbols: list[str] | None = None,
        fields: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        max_results: int = 10,
        max_cost_usd: float | None = None,
        event_id: str | None = None,
        event_at: str | None = None,
        window_start_at: str | None = None,
        window_end_at: str | None = None,
        requested_event_offsets: list[str] | None = None,
        evidence_ref: str | None = None,
        content_ref: str | None = None,
    ) -> ResearchCapabilityResult:
        try:
            request = ResearchCapabilityQuery.model_validate(
                {
                    "schema_version": "research-capability-query.v1",
                    "request_id": request_id,
                    "capability_id": capability_id,
                    "requirement_id": requirement_id,
                    "query": query,
                    "target_url": target_url,
                    "symbols": symbols or [],
                    "fields": fields or [],
                    "allowed_domains": allowed_domains or [],
                    "max_results": max_results,
                    "max_cost_usd": max_cost_usd,
                    "research_session_id": research_session_id,
                    "round": round,
                    "mode": mode,
                    "observed_at": observed_at,
                    "cutoff_at": cutoff_at,
                    "event_id": event_id,
                    "event_at": event_at,
                    "window_start_at": window_start_at,
                    "window_end_at": window_end_at,
                    "requested_event_offsets": requested_event_offsets or [],
                    "evidence_ref": evidence_ref,
                    "content_ref": content_ref,
                }
            )
            return await gateway.execute(request)
        except (ResearchCapabilityError, ValueError) as exc:
            code = getattr(exc, "error_code", str(exc))
            if isinstance(exc, ResearchCapabilityError):
                payload = json.dumps(
                    exc.provenance().model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                raise ToolError(f"{code} provenance={payload}") from exc
            raise ToolError(str(code)) from exc

    @server.custom_route(RESEARCH_INVOCATION_HTTP_PATH, methods=["POST"])
    @server.custom_route(RESEARCH_CAPABILITY_HTTP_PATH, methods=["POST"])
    async def research_capability_http(request: Request) -> JSONResponse:
        if not bridge_key:
            return _http_error(
                "research_tool_auth_unconfigured",
                status_code=503,
                cause_code="bridge_key_missing",
                retryable=True,
            )
        supplied = request.headers.get(_BRIDGE_KEY_HEADER, "")
        if not supplied or not hmac.compare_digest(supplied, bridge_key):
            return _http_error(
                "research_tool_unauthorized",
                status_code=401,
                cause_code="bridge_key_mismatch",
            )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_HTTP_BODY_BYTES:
                    return _http_error(
                        "research_capability_query_too_large",
                        status_code=413,
                        cause_code="body_limit_exceeded",
                    )
            except ValueError:
                return _http_error(
                    "research_capability_query_invalid",
                    status_code=400,
                    cause_code="content_length_invalid",
                )
        try:
            raw_body = await request.body()
            if len(raw_body) > _MAX_HTTP_BODY_BYTES:
                return _http_error(
                    "research_capability_query_too_large",
                    status_code=413,
                    cause_code="body_limit_exceeded",
                )
            managed = request.url.path == RESEARCH_INVOCATION_HTTP_PATH
            contract = ResearchCapabilityInvocation if managed else ResearchCapabilityQuery
            query = contract.model_validate(json.loads(raw_body))
        except (ValidationError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return _http_error(
                "research_capability_query_invalid",
                status_code=422,
                cause_code="canonical_validation_failed",
            )
        try:
            if managed:
                invoke = getattr(gateway, "invoke_live", None)
                if invoke is None:
                    return _http_error(
                        "research_invocation_unavailable", status_code=503,
                        cause_code="durable_gateway_required",
                    )
                result = await invoke(query)
            else:
                result = await gateway.execute(query)
        except ResearchCapabilityError as exc:
            return JSONResponse(
                exc.provenance().model_dump(mode="json"),
                status_code=503 if exc.retryable else 409,
            )
        except Exception:
            return _http_error(
                "research_capability_failed",
                status_code=503,
                cause_code="gateway_unhandled_error",
                retryable=True,
            )
        return JSONResponse(result.model_dump(mode="json"), status_code=200)

    @server.custom_route("/decision-hub/v1/research-capabilities/baseline", methods=["POST"])
    async def research_baseline_http(request: Request) -> JSONResponse:
        # Internal Host pre-step only; deliberately not registered as a model Tool.
        supplied = request.headers.get(_BRIDGE_KEY_HEADER, "")
        if not bridge_key or not supplied or not hmac.compare_digest(supplied, bridge_key):
            return _http_error("research_tool_unauthorized", status_code=401,
                               cause_code="bridge_key_mismatch")
        if baseline_service is None:
            return _http_error("research_baseline_unavailable", status_code=503,
                               cause_code="durable_baseline_required")
        from packages.kernel.decision_hub_kernel.ports.research_baseline import BaselineRequest
        try:
            raw = await request.body()
            if len(raw) > 4096:
                return _http_error("research_baseline_request_too_large", status_code=413,
                                   cause_code="identity_body_limit")
            identity = BaselineRequest.model_validate_json(raw)
        except (ValidationError, ValueError):
            return _http_error("research_baseline_request_invalid", status_code=422,
                               cause_code="canonical_validation_failed")
        try:
            result = await baseline_service.deliver(identity)
        except ValueError as exc:
            return _http_error("research_baseline_failed", status_code=409,
                               cause_code=str(exc)[:256])
        except Exception as exc:
            try:
                _LOGGER.error("research_baseline_operation_failed %s",
                              json.dumps(_baseline_failure_diagnostic(exc, identity),
                                         sort_keys=True, separators=(",", ":")))
            except Exception:
                # Diagnostics must not replace the existing failure response.
                pass
            return _http_error("research_baseline_unavailable", status_code=503,
                               cause_code="baseline_operation_unavailable", retryable=True)
        return JSONResponse(result.model_dump(mode="json"))

    @server.custom_route(RESEARCH_CATALOG_HTTP_PATH, methods=["GET"])
    async def research_catalog_http(request: Request) -> JSONResponse:
        supplied = request.headers.get(_BRIDGE_KEY_HEADER, "")
        if not bridge_key or not supplied or not hmac.compare_digest(supplied, bridge_key):
            return _http_error(
                "research_tool_unauthorized", status_code=401, cause_code="bridge_key_mismatch"
            )
        if query_catalog is None:
            return _http_error(
                "research_catalog_unavailable", status_code=503, cause_code="catalog_not_configured"
            )
        session_id = request.query_params.get("research_session_id")
        if session_id is not None:
            scope_reader = getattr(gateway, "scoped_query_catalog", None)
            if scope_reader is None:
                return _http_error("research_catalog_scope_unavailable", status_code=503,
                                   cause_code="scope_reader_unavailable")
            try:
                if not session_id or len(session_id) > 128:
                    raise ValueError("invalid_session_id")
                scoped = await asyncio.to_thread(scope_reader, query_catalog, session_id)
            except (ValueError, KeyError, TypeError):
                return _http_error("research_catalog_scope_invalid", status_code=409,
                                   cause_code="frozen_scope_not_verified")
            return JSONResponse(scoped)
        return JSONResponse(query_catalog)

    return server


def _http_error(
    error_code: str,
    *,
    status_code: int,
    cause_code: str,
    retryable: bool = False,
) -> JSONResponse:
    provenance = ErrorProvenance(
        error_code=error_code,
        origin="gateway",
        cause_code=cause_code,
        capability_id=None,
        tool_call_id=None,
        retryable=retryable,
        deadline_ms=None,
    )
    return JSONResponse(provenance.model_dump(mode="json"), status_code=status_code)
