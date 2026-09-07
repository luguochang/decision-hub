from __future__ import annotations

import hmac
import json
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityGateway

RESEARCH_CAPABILITY_HTTP_PATH = "/decision-hub/v1/research-capabilities/execute"
_BRIDGE_KEY_HEADER = "x-decision-hub-bridge-key"
_MAX_HTTP_BODY_BYTES = 256_000


def build_research_mcp_server(
    gateway: ResearchCapabilityGateway,
    *,
    bridge_key: str | None = None,
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
            query = ResearchCapabilityQuery.model_validate(json.loads(raw_body))
        except (ValidationError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return _http_error(
                "research_capability_query_invalid",
                status_code=422,
                cause_code="canonical_validation_failed",
            )
        try:
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
