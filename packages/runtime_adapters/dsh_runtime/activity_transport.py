"""Configured, independently authenticated current Host scope binding."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityScope,
    DshHostActivityScopeView,
)
from packages.runtime_adapters.dsh_runtime.web_host_client import (
    DshActivityHttpResponse,
    DshWebHostConfig,
)


def activity_channel_url(value: str) -> str:
    url = urlsplit(value.strip())
    if (
        url.scheme not in {"http", "https"} or not url.hostname
        or url.username or url.password or url.query or url.fragment
    ):
        raise ValueError("dsh_activity_channel_invalid")
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", ""))


def _canonical_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").lower()


@dataclass(frozen=True)
class DshActivityTransportConfig:
    host: DshWebHostConfig
    hub_base_url: str
    home: Path
    workspace: Path
    callback_key: str = field(repr=False)
    workspace_id: str | None = None

    @classmethod
    def from_env(cls) -> DshActivityTransportConfig:
        hub = os.getenv("DECISION_HUB_API_URL", "").strip()
        home = os.getenv("DECISION_HUB_DSH_HOME", "").strip()
        workspace = os.getenv("DECISION_HUB_DSH_WORKSPACE", "").strip()
        callback = os.getenv("DECISION_HUB_DSH_CALLBACK_SECRET", "").strip()
        if not all((hub, home, workspace, callback)):
            raise ValueError("dsh_activity_configuration_missing")
        return cls(
            DshWebHostConfig.from_env(), activity_channel_url(hub), Path(home), Path(workspace),
            callback, os.getenv("DECISION_HUB_DSH_WORKSPACE_ID", "").strip() or None,
        )

    def validate_scope(self, scope: DshHostActivityScope) -> None:
        if not self.home.is_dir() or not self.workspace.is_dir():
            raise ValueError("dsh_activity_namespace_unavailable")
        expected_cwd = hashlib.sha256(_canonical_path(self.workspace).encode()).hexdigest()
        if (
            activity_channel_url(scope.hub_base_url) != activity_channel_url(self.hub_base_url)
            or scope.workspace_cwd_hash != expected_cwd
            or (self.workspace_id is not None and scope.workspace_id != self.workspace_id)
        ):
            raise ValueError("dsh_activity_scope_configuration_mismatch")

    def fingerprint(self, scope: DshHostActivityScope) -> str:
        self.validate_scope(scope)
        # Scope comes from a separate authenticated GET, never the POST claim.
        # Its boot identity is stable; GET observation time is not.
        body = {
            "schema_version": "dsh-activity-transport.v1",
            "host_base_url": activity_channel_url(self.host.base_url),
            "hub_base_url": activity_channel_url(self.hub_base_url),
            "home": _canonical_path(self.home),
            "workspace": _canonical_path(self.workspace),
            "scope": scope.model_dump(mode="json"),
        }
        return hashlib.sha256(json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()


def validate_activity_scope(
    response: DshActivityHttpResponse, config: DshActivityTransportConfig, *, now: datetime,
) -> DshHostActivityScopeView:
    expected_url = f"{config.host.base_url.rstrip('/')}/decision-hub/v1/current-activity"
    if response.url != expected_url:
        raise ValueError("dsh_activity_channel_changed")
    view = DshHostActivityScopeView.model_validate_json(response.body)
    config.validate_scope(view.scope)
    if not view.scope.started_at <= view.observed_at <= response.received_at <= now:
        raise ValueError("dsh_activity_scope_time_invalid")
    if (now - view.observed_at).total_seconds() > 30:
        raise ValueError("dsh_activity_scope_expired")
    return view
