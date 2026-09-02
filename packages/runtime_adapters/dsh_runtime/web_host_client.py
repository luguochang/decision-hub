from __future__ import annotations

import os
from typing import Protocol, TypeVar

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

T = TypeVar("T", bound=BaseModel)


class DshWebHostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1, pattern=r"^https?://")
    bridge_key: str = Field(min_length=1, repr=False)
    request_timeout_seconds: float = Field(default=15, gt=0, le=120)

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
        )


class DshHostClient(Protocol):
    async def readiness(self) -> DshHostReadiness: ...
    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted: ...
    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus: ...
    async def result(self, payload: DshSessionSubmit) -> DshSessionResult: ...
    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus: ...
    async def close(self) -> None: ...


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
        )

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        return await self._request(
            "GET",
            f"/decision-hub/v1/runs/{payload.run_id}",
            DshSessionStatus,
        )

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        return await self._request(
            "GET",
            f"/decision-hub/v1/runs/{payload.run_id}/result",
            DshSessionResult,
        )

    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        return await self._request(
            "POST",
            f"/decision-hub/v1/runs/{payload.run_id}/cancel",
            DshSessionStatus,
            json={"reason": reason, "generation": payload.generation},
        )

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
    ) -> T:
        try:
            response = await self._client.request(
                method,
                f"{str(self.config.base_url).rstrip('/')}{path}",
                json=json,
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
                deadline_ms=round(self.config.request_timeout_seconds * 1000),
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
            raise _http_error(response.status_code)
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


def _http_error(status_code: int) -> AgentExecutionError:
    if status_code in {401, 403}:
        return AgentExecutionError(
            "dsh_host_auth_failed",
            "DSH Host rejected bridge authentication",
            provider_id="dsh-web",
            origin="configuration",
            cause_code=f"http_{status_code}",
        )
    if status_code == 404:
        return AgentExecutionError(
            "dsh_session_not_found",
            "DSH Host has not materialized the requested session",
            retryable=True,
            provider_id="dsh-web",
            origin="dsh",
            cause_code="http_404",
        )
    if status_code in {408, 504}:
        return AgentExecutionError(
            "provider_timeout",
            "DSH Host request exceeded its bounded timeout",
            retryable=True,
            provider_id="dsh-web",
            origin="transport",
            cause_code=f"http_{status_code}",
        )
    if status_code >= 500:
        return AgentExecutionError(
            "dsh_host_unavailable",
            "DSH Host returned a server error",
            retryable=True,
            provider_id="dsh-web",
            origin="dsh",
            cause_code=f"http_{status_code}",
        )
    return AgentExecutionError(
        "dsh_host_conflict",
        "DSH Host rejected the canonical request",
        provider_id="dsh-web",
        origin="dsh",
        cause_code=f"http_{status_code}",
    )
