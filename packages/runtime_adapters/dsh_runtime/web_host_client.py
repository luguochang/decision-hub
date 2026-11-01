from __future__ import annotations

import json as json_module
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

if TYPE_CHECKING:
    from .terminal_queue_recovery import TerminalQueueAuthority, TerminalQueueReceipt

T = TypeVar("T", bound=BaseModel)


class DshWebHostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1, pattern=r"^https?://")
    bridge_key: str = Field(min_length=1, repr=False)
    request_timeout_seconds: float = Field(default=15, gt=0, le=120)
    # Session operations may wait for the Host's bounded 30 second SDK
    # inspection.  Keep their HTTP envelope independently configurable and
    # slightly larger than that Host operation; a short readiness timeout must
    # not turn a still-running native session into a failed product Run.
    operation_timeout_seconds: float = Field(default=35, gt=0, le=600)

    @classmethod
    def from_env(cls) -> DshWebHostConfig:
        base_url = os.getenv("DECISION_HUB_DSH_WEB_URL")
        bridge_key = os.getenv("DECISION_HUB_DSH_HOST_SECRET")
        if not base_url or not bridge_key:
            raise AgentExecutionError(
                "configuration_invalid",
                "DSH Web runtime requires host URL and bridge secret",
                origin="configuration",
                cause_code="dsh_web_configuration_missing",
            )
        return cls(
            base_url=base_url,
            bridge_key=bridge_key,
            request_timeout_seconds=float(
                os.getenv("DECISION_HUB_DSH_HOST_TIMEOUT_SECONDS", "15")
            ),
            operation_timeout_seconds=float(
                os.getenv("DECISION_HUB_DSH_HOST_OPERATION_TIMEOUT_SECONDS", "35")
            ),
        )


class DshHostClient(Protocol):
    async def readiness(self) -> DshHostReadiness: ...
    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted: ...
    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus: ...
    async def result(self, payload: DshSessionSubmit) -> DshSessionResult: ...
    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class DshRecoveryBinding:
    """Verified frozen correlation proof, deliberately not a new submission."""
    run_id: str
    dsh_session_id: str
    generation: int
    request_hash: str
    task_request_hash: str
    method_hash: str
    prompt_hash: str
    first_prompt_hash: str
    first_execution_request_hash: str
    execution_request_hash: str
    deadline_at: datetime
    max_tool_calls: int
    model_step_timeout_ms: int


@runtime_checkable
class DshRecoveryHostClient(Protocol):
    """Optional recovery capability; existing SDK/Fake clients stay compatible."""
    async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus: ...
    async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult: ...
    async def recovery_cancel(
        self, binding: DshRecoveryBinding, reason: str,
    ) -> DshSessionStatus: ...


@dataclass(frozen=True)
class DshActivityHttpResponse:
    """The original authenticated Host response, before any local projection."""

    url: str
    body: bytes
    received_at: datetime


class DshActivityHostClient(Protocol):
    config: DshWebHostConfig

    async def activity_scope(self) -> DshActivityHttpResponse: ...
    async def activity_snapshot(self, payload: BaseModel) -> DshActivityHttpResponse: ...


class DshWebHostClient:
    """Typed client for the public Decision Hub Host Plugin contract."""

    def __init__(
        self,
        config: DshWebHostConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=str(config.base_url).rstrip("/"),
            timeout=config.request_timeout_seconds,
            # DSH Web is an explicit loopback/local Host boundary. Do not let
            # inherited HTTP(S)_PROXY settings reroute bridge traffic and turn
            # a healthy local Host into an opaque proxy 502.
            trust_env=False,
        )

    async def readiness(self) -> DshHostReadiness:
        return await self._request(
            "GET",
            "/decision-hub/v1/readiness",
            DshHostReadiness,
            accepted_statuses=frozenset({503}),
        )

    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
        return await self._request(
            "PUT",
            f"/decision-hub/v1/runs/{payload.run_id}",
            DshSessionAccepted,
            json=payload.model_dump(mode="json"),
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        return await self._request(
            "GET",
            f"/decision-hub/v1/runs/{payload.run_id}",
            DshSessionStatus,
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        return await self._request(
            "GET",
            f"/decision-hub/v1/runs/{payload.run_id}/result",
            DshSessionResult,
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        return await self._request(
            "POST",
            f"/decision-hub/v1/runs/{payload.run_id}/cancel",
            DshSessionStatus,
            json={"reason": reason, "generation": payload.generation},
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        return await self._request(
            "GET", f"/decision-hub/v1/runs/{binding.run_id}", DshSessionStatus,
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
        return await self._request(
            "GET", f"/decision-hub/v1/runs/{binding.run_id}/result", DshSessionResult,
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def recovery_cancel(
        self, binding: DshRecoveryBinding, reason: str,
    ) -> DshSessionStatus:
        return await self._request(
            "POST", f"/decision-hub/v1/runs/{binding.run_id}/cancel", DshSessionStatus,
            json={"reason": reason, "generation": binding.generation},
            timeout_seconds=self.config.operation_timeout_seconds,
        )

    async def terminal_quiesce(self, authority: TerminalQueueAuthority) -> TerminalQueueReceipt:
        from .terminal_queue_recovery import TerminalQueueReceipt
        return await self._request(
            "POST", f"/decision-hub/v1/runs/{authority.run_id}/quiesce", TerminalQueueReceipt,
            json={"generation": authority.generation},
        )

    async def before_failed_terminal_quiesce(
        self, authority: TerminalQueueAuthority,
    ) -> TerminalQueueReceipt:
        from .terminal_queue_recovery import TerminalQueueReceipt
        if authority.purpose != "before_failed_terminal":
            raise ValueError("terminal_queue_failed_invocation_purpose_invalid")
        return await self._request(
            "POST", f"/decision-hub/v1/runs/{authority.run_id}/quiesce", TerminalQueueReceipt,
            json={
                "generation": authority.generation, "purpose": "before_failed_terminal",
                "authority": authority.model_dump(mode="json"),
            },
        )

    async def activity_scope(self) -> DshActivityHttpResponse:
        return await self._activity_request("GET")

    async def activity_snapshot(self, payload: BaseModel) -> DshActivityHttpResponse:
        # The Host activity operation itself remains 30s. Allow that existing
        # operation plus a 5s local response margin, only for this read/fence POST.
        # Scope, research, model and cancellation RPC budgets remain unchanged.
        return await self._activity_request(
            "POST", json=payload.model_dump(mode="json"), timeout=35.0,
        )

    async def _activity_request(
        self, method: str, *, json: dict[str, object] | None = None, timeout: float | None = None,
    ) -> DshActivityHttpResponse:
        # This capability only reads activity and installs the Host's explicit
        # lineage fence. It never invokes submit/result/cancel or model research.
        url = f"{self.config.base_url.rstrip('/')}/decision-hub/v1/current-activity"
        try:
            options = {} if timeout is None else {"timeout": timeout}
            response = await self._client.request(
                method, url, json=json,
                headers={"X-Decision-Hub-Host-Key": self.config.bridge_key},
                **options,
            )
        except httpx.TimeoutException as exc:
            raise AgentExecutionError(
                "provider_timeout", "DSH activity read exceeded its bounded timeout",
                retryable=True, provider_id="dsh-web", origin="transport",
                cause_code="dsh_activity_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentExecutionError(
                "dsh_host_unavailable", "DSH activity transport is unavailable",
                retryable=True, provider_id="dsh-web", origin="transport",
                cause_code="dsh_activity_transport_unavailable",
            ) from exc
        if response.status_code != 200:
            raise _http_error(
                response.status_code,
                _safe_host_error(response, None, url),
            )
        if str(response.url) != url:
            raise AgentExecutionError(
                "dsh_protocol_invalid", "DSH activity response changed its configured channel",
                provider_id="dsh-web", origin="dsh", cause_code="dsh_activity_channel_changed",
            )
        if len(response.content) > 2 * 1024 * 1024:
            raise AgentExecutionError(
                "dsh_protocol_invalid", "DSH activity response exceeded its capture bound",
                provider_id="dsh-web", origin="dsh", cause_code="dsh_activity_response_too_large",
            )
        return DshActivityHttpResponse(url, response.content, datetime.now(UTC))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        json: dict[str, object] | None = None,
        accepted_statuses: frozenset[int] = frozenset(),
        timeout_seconds: float | None = None,
    ) -> T:
        request_url = f"{str(self.config.base_url).rstrip('/')}{path}"
        effective_timeout = (
            self.config.request_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            response = await self._client.request(
                method,
                request_url,
                json=json,
                timeout=effective_timeout,
                headers={
                    "X-Decision-Hub-Host-Key": self.config.bridge_key
                },
            )
        except httpx.TimeoutException as exc:
            raise AgentExecutionError(
                "provider_timeout",
                "DSH Host request exceeded its bounded timeout",
                retryable=True,
                provider_id="dsh-web",
                origin="transport",
                cause_code="dsh_host_timeout",
                deadline_ms=round(effective_timeout * 1000),
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentExecutionError(
                "dsh_host_unavailable",
                "DSH Host transport is unavailable",
                retryable=True,
                provider_id="dsh-web",
                origin="transport",
                cause_code=type(exc).__name__.lower(),
            ) from exc
        if response.status_code >= 400 and response.status_code not in accepted_statuses:
            diagnostic = _safe_host_error(
                response,
                json if method == "PUT" and model is DshSessionAccepted else None,
                request_url,
            )
            raise _http_error(response.status_code, diagnostic)
        try:
            return model.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "DSH Host returned an invalid canonical payload",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="canonical_validation_failed",
            ) from exc


_SUBMIT_STAGES = frozenset({
    "host_submit_inspect", "host_submit_workspace", "host_submit_create",
    "host_submit_accepted", "host_submit_prompt_fetch", "host_submit_link_fetch",
    "host_submit_original_prompt_fetch", "host_submit_prompt",
})
_SUBMIT_ERROR_STATUSES = {
    "host_internal_error": frozenset({500}),
    "host_deadline_elapsed": frozenset({408}),
    "host_session_identity_changed": frozenset({502}),
    "host_session_workspace_conflict": frozenset({409}),
    "host_workspace_resolution_failed": frozenset({503}),
    "host_workspace_not_registered": frozenset({503}),
    "host_prompt_conflict": frozenset({409}),
    "host_activity_lineage_fenced": frozenset({409}),
    "host_activity_lineage_invalid": frozenset({409}),
    "host_callback_unreachable": frozenset({503}),
    "host_hub_response_invalid": frozenset({502}),
    "dsh_host_read_transport_timeout": frozenset({502}),
    "dsh_host_read_deadline_elapsed": frozenset({502}),
    "dsh_host_read_cancelled": frozenset({502}),
    "host_contract_invalid": frozenset({422}),
    **{f"host_hub_http_{status}": frozenset({503 if status >= 500 else 502})
       for status in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504)},
}


def _safe_host_error(
    response: httpx.Response, expected: dict[str, object] | None, expected_url: str,
) -> tuple[str, bool, str | None] | None:
    """Parse only the Host's closed error envelope.

    Every bridge route uses the same authenticated envelope.  Preserving its
    code keeps ``host_run_not_found``, ``host_route_not_found`` and native
    session loss distinct.  A submit diagnostic is accepted only when every
    tuple field matches the exact request that produced the response.
    """
    if len(response.content) > 2048:
        return None
    if (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return None
    if str(response.url) != expected_url:
        return None
    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_submit_error_field")
            result[key] = value
        return result
    try:
        body = json_module.loads(response.content, object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if type(body) is not dict or set(body) != {"error"}:
        return None
    error = body["error"]
    if type(error) is not dict or set(error) not in (
        {"code", "message", "retryable"},
        {"code", "message", "retryable", "submit"},
    ):
        return None
    code = error["code"]
    if (
        type(code) is not str
        or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None
        or error["message"] != code
        or type(error["retryable"]) is not bool
    ):
        return None
    if "submit" not in error:
        return code, error["retryable"], None
    if expected is None or response.status_code not in _SUBMIT_ERROR_STATUSES.get(code, ()):
        return None
    submit = error["submit"]
    fields = {"schema_version", "stage_code", "run_id", "dsh_session_id",
              "generation", "request_hash", "request_id"}
    if type(submit) is not dict or set(submit) != fields:
        return None
    if submit["schema_version"] != "dsh-submit-error.v1":
        return None
    stage = submit["stage_code"]
    if type(stage) is not str or stage not in _SUBMIT_STAGES:
        return None
    bindings = {
        "run_id": "run_id", "dsh_session_id": "deterministic_session_id",
        "request_id": "deterministic_request_id", "request_hash": "request_hash",
        "generation": "generation",
    }
    for key, expected_key in bindings.items():
        value = submit[key]
        native_type = int if key == "generation" else str
        if type(value) is not native_type or type(expected.get(expected_key)) is not native_type:
            return None
        if value != expected[expected_key]:
            return None
    return code, error["retryable"], stage


def _http_error(
    status_code: int, safe_host_error: tuple[str, bool, str | None] | None = None,
) -> AgentExecutionError:
    def message(base: str) -> str:
        if safe_host_error is None:
            return base
        code, _retryable, stage = safe_host_error
        suffix = f"; stage={stage}" if stage is not None else ""
        return f"{base} [Host code={code}{suffix}]"

    if safe_host_error is not None:
        code, retryable, _stage = safe_host_error
        return AgentExecutionError(
            code,
            message("DSH Host rejected the canonical request"),
            retryable=retryable,
            provider_id="dsh-web",
            origin="configuration" if status_code in {401, 403} else "dsh",
            cause_code=code,
        )

    if status_code in {401, 403}:
        return AgentExecutionError(
            "dsh_host_auth_failed",
            message("DSH Host rejected bridge authentication"),
            provider_id="dsh-web",
            origin="configuration",
            cause_code=f"http_{status_code}",
        )
    if status_code == 404:
        return AgentExecutionError(
            "dsh_session_not_found",
            message("DSH Host has not materialized the requested session"),
            retryable=True,
            provider_id="dsh-web",
            origin="dsh",
            cause_code="http_404",
        )
    if status_code in {408, 504}:
        return AgentExecutionError(
            "provider_timeout",
            message("DSH Host request exceeded its bounded timeout"),
            retryable=True,
            provider_id="dsh-web",
            origin="transport",
            cause_code=f"http_{status_code}",
        )
    if status_code >= 500:
        return AgentExecutionError(
            "dsh_host_unavailable",
            message("DSH Host returned a server error"),
            retryable=True,
            provider_id="dsh-web",
            origin="dsh",
            cause_code=f"http_{status_code}",
        )
    return AgentExecutionError(
        "dsh_host_conflict",
        message("DSH Host rejected the canonical request"),
        provider_id="dsh-web",
        origin="dsh",
        cause_code=f"http_{status_code}",
    )
