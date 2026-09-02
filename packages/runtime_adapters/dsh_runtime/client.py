from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .profile import DEFAULT_PROFILE_PATH

SDK_VERSION = "0.1.1rc1"


class DshRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sdk_version: str = SDK_VERSION
    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    profile_path: Path = DEFAULT_PROFILE_PATH
    workspace: Path = Path(".")
    session_root: Path = Path("data/dsh-sessions")
    max_tokens: int = Field(default=8192, ge=256, le=65536)
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    shutdown_timeout_seconds: float = Field(default=2, gt=0, le=30)
    base_url: str | None = None
    research_mcp_url: AnyHttpUrl | None = None

    @classmethod
    def from_env(cls) -> DshRuntimeConfig:
        return cls(
            provider=os.getenv("DECISION_HUB_DSH_PROVIDER", "deepseek-official"),
            model=os.getenv("DECISION_HUB_DSH_MODEL", "deepseek-v4-flash"),
            profile_path=Path(os.getenv("DECISION_HUB_DSH_PROFILE", str(DEFAULT_PROFILE_PATH))),
            workspace=Path(os.getenv("DECISION_HUB_DSH_WORKSPACE", ".")),
            session_root=Path(os.getenv("DECISION_HUB_DSH_SESSION_ROOT", "data/dsh-sessions")),
            max_tokens=int(os.getenv("DECISION_HUB_DSH_MAX_TOKENS", "8192")),
            request_timeout_seconds=float(
                os.getenv("DECISION_HUB_DSH_REQUEST_TIMEOUT_SECONDS", "60")
            ),
            shutdown_timeout_seconds=float(
                os.getenv("DECISION_HUB_DSH_SHUTDOWN_TIMEOUT_SECONDS", "2")
            ),
            base_url=_first_nonempty(
                "DECISION_HUB_DSH_BASE_URL",
                "DEEPSEEK_BASE_URL",
                "SUB2API_BASE_URL",
                "OPENAI_BASE_URL",
            ),
            research_mcp_url=_optional_http_url(
                os.getenv("DECISION_HUB_RESEARCH_MCP_URL")
            ),
        )


@dataclass(frozen=True)
class DshSdkNotification:
    method: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class DshSdkRun:
    session_id: str
    final_response: str
    finish_reason: str | None
    events: tuple[Mapping[str, object], ...]
    notifications: tuple[DshSdkNotification, ...]
    session_root: str | None


class DshHarnessHandle(Protocol):
    def start(self) -> None: ...

    def close(self) -> None: ...

    def run(
        self,
        input: str,
        *,
        session_id: str,
        on_notification: Callable[[object], None] | None = None,
    ) -> object: ...


DshHarnessFactory = Callable[[DshRuntimeConfig], DshHarnessHandle]


class DshSdkClient:
    def __init__(
        self,
        config: DshRuntimeConfig,
        factory: DshHarnessFactory | None = None,
    ) -> None:
        self.config = config
        self._factory = factory or _default_factory
        self._handle: DshHarnessHandle | None = None

    async def start(self) -> None:
        handle = self._ensure_handle()
        await asyncio.to_thread(handle.start)

    async def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            await asyncio.to_thread(handle.close)

    async def run(self, prompt: str, *, session_id: str) -> DshSdkRun:
        handle = self._ensure_handle()
        raw = await asyncio.to_thread(handle.run, prompt, session_id=session_id)
        normalized = _normalize_run(raw)
        if normalized.session_id != session_id:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "DSH returned a different session identity than the requested session",
            )
        return normalized

    def _ensure_handle(self) -> DshHarnessHandle:
        if self._handle is None:
            self._handle = self._factory(self.config)
        return self._handle


def _default_factory(config: DshRuntimeConfig) -> DshHarnessHandle:
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise AgentExecutionError(
            "dsh_sdk_unavailable",
            "install the project dsh extra before enabling the DSH runtime",
        ) from exc

    secret = _first_nonempty(
        "DECISION_HUB_DSH_API_KEY",
        "DEEPSEEK_API_KEY",
        "SUB2API_API_KEY",
        "OPENAI_API_KEY",
    )
    if secret is None:
        raise AgentExecutionError(
            "configuration_invalid",
            "DSH runtime requires a provider API key in the process environment",
        )

    config.session_root.mkdir(parents=True, exist_ok=True)
    return cast(
        DshHarnessHandle,
        DeepSeekHarness(
            provider=config.provider,
            model=config.model,
            max_tokens=config.max_tokens,
            cwd=str(config.workspace.resolve()),
            session_root=str(config.session_root.resolve()),
            cordis=str(config.profile_path.resolve()),
            request_timeout_seconds=config.request_timeout_seconds,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
            base_url=config.base_url,
            api_key=secret,
            env=(
                {"DECISION_HUB_RESEARCH_MCP_URL": str(config.research_mcp_url)}
                if config.research_mcp_url is not None
                else {}
            ),
        ),
    )


def _normalize_run(raw: object) -> DshSdkRun:
    session_id = getattr(raw, "session_id", None)
    final_response = getattr(raw, "final_response", None)
    finish_reason = getattr(raw, "finish_reason", None)
    events = getattr(raw, "events", None)
    notifications = getattr(raw, "notifications", None)
    session_root = getattr(raw, "session_root", None)
    if not isinstance(session_id, str) or not isinstance(final_response, str):
        raise AgentExecutionError(
            "dsh_protocol_invalid", "DSH run result is missing identity or text"
        )
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise AgentExecutionError("dsh_protocol_invalid", "DSH finish reason must be a string")
    if not isinstance(events, list) or not isinstance(notifications, list):
        raise AgentExecutionError("dsh_protocol_invalid", "DSH run result is missing event lists")

    normalized_events: list[Mapping[str, object]] = []
    for event in events:
        if isinstance(event, Mapping) and all(isinstance(key, str) for key in event):
            normalized_events.append(cast(Mapping[str, object], event))

    normalized_notifications: list[DshSdkNotification] = []
    for notification in notifications:
        method = getattr(notification, "method", None)
        payload = getattr(notification, "payload", None)
        if isinstance(method, str) and isinstance(payload, Mapping):
            normalized_notifications.append(
                DshSdkNotification(method=method, payload=cast(Mapping[str, object], payload))
            )

    return DshSdkRun(
        session_id=session_id,
        final_response=final_response,
        finish_reason=finish_reason,
        events=tuple(normalized_events),
        notifications=tuple(normalized_notifications),
        session_root=session_root if isinstance(session_root, str) else None,
    )


def _first_nonempty(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _optional_http_url(value: str | None) -> AnyHttpUrl | None:
    return TypeAdapter(AnyHttpUrl).validate_python(value) if value else None
