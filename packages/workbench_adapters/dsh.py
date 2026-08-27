from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from packages.contracts_py.decision_hub_contracts.models import CapabilityManifest
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.workbench import (
    CapabilityCall,
    CapabilityExecutor,
    CapabilityResult,
)
from packages.runtime_adapters.errors import map_runtime_error


class DshCapabilityAdapter:
    """Maps audited DSH metadata into Core intake without executing the plugin."""

    def __init__(
        self,
        workbench: WorkbenchAssetService,
        executors: Mapping[str, CapabilityExecutor] | None = None,
        output_schemas: Mapping[str, Mapping[str, object]] | None = None,
        *,
        owner_id: str = "owner",
    ) -> None:
        self.workbench = workbench
        self.executors = dict(executors or {})
        self.output_schemas = dict(output_schemas or {})
        self.owner_id = owner_id
        for schema in self.output_schemas.values():
            Draft202012Validator.check_schema(schema)

    def discover(self, manifest: CapabilityManifest) -> CapabilityManifest:
        discovered = manifest.model_copy(update={"status": "discovered"})
        return self.workbench.register_capability(discovered)

    def audit(self, capability_id: str, *, owner_id: str) -> CapabilityManifest:
        self._require_owner(owner_id)
        return self.workbench.set_capability_status(capability_id, "audited")

    def enable(
        self, capability_id: str, *, owner_id: str, shadow: bool = False
    ) -> CapabilityManifest:
        self._require_owner(owner_id)
        if capability_id not in self.executors:
            raise ValueError("capability_executor_not_registered")
        manifest = next(
            (
                item
                for item in self.workbench.list_capabilities()
                if item.capability_id == capability_id
            ),
            None,
        )
        if manifest is None:
            raise ValueError("capability_not_found")
        if manifest.status != "audited":
            raise PermissionError("capability_audit_required")
        if manifest.output_schema_ref not in self.output_schemas:
            raise ValueError("capability_output_schema_not_registered")
        return self.workbench.set_capability_status(
            capability_id, "shadow" if shadow else "enabled"
        )

    async def execute(self, call: CapabilityCall) -> CapabilityResult:
        try:
            manifest = self.workbench.require_enabled(call.capability_id)
        except PermissionError as exc:
            raise AgentExecutionError("tool_denied", "capability is not enabled") from exc
        executor = self.executors.get(call.capability_id)
        if executor is None:
            raise AgentExecutionError("tool_denied", "capability executor is not registered")
        schema = self.output_schemas.get(manifest.output_schema_ref)
        if schema is None:
            raise AgentExecutionError(
                "configuration_invalid", "capability output schema is not registered"
            )
        now = datetime.now(UTC)
        timeout = min((call.deadline_at - now).total_seconds(), manifest.timeout_seconds)
        if timeout <= 0:
            raise AgentExecutionError(
                "provider_timeout", "capability deadline exceeded", retryable=True
            )
        try:
            value = executor(call)
            if inspect.isawaitable(value):
                output = await asyncio.wait_for(value, timeout=timeout)
            else:
                output = value
        except TimeoutError as exc:
            raise AgentExecutionError(
                "provider_timeout", "capability deadline exceeded", retryable=True
            ) from exc
        except PermissionError as exc:
            raise AgentExecutionError("tool_denied", "capability execution denied") from exc
        except BaseException as exc:
            raise map_runtime_error(exc, provider_id=manifest.provider) from exc
        raw_output = cast(object, output)
        if not isinstance(raw_output, Mapping) or not all(
            isinstance(key, str) for key in raw_output
        ):
            raise AgentExecutionError(
                "structured_output_invalid", "capability output is not an object"
            )
        normalized = dict(raw_output)
        try:
            Draft202012Validator(schema).validate(normalized)
        except JsonSchemaValidationError as exc:
            raise AgentExecutionError(
                "structured_output_invalid",
                "capability output does not match its declared schema",
            ) from exc
        finished = datetime.now(UTC)
        return CapabilityResult(
            capability_id=manifest.capability_id,
            output=normalized,
            started_at=now,
            finished_at=finished,
            status="shadow" if manifest.status == "shadow" else "succeeded",
        )

    def _require_owner(self, owner_id: str) -> None:
        if not owner_id or owner_id != self.owner_id:
            raise PermissionError("owner_identity_mismatch")
