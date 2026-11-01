"""Trusted original-reading component protocols; no user-supplied qualification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy.orm import Session

from .component_registry import ComponentKind, ComponentManifest, ShadowTrial
from .market_path import FrozenContract, aware_utc

LOCAL_EXECUTION_POLICY = {
    "version": "installed-pure-local-execution-cost.v1",
    "maximum_elapsed_ns": 2_000_000_000,
    "maximum_thread_cpu_ns": 1_000_000_000,
    "external_boundary": "network-and-process-denied-installed-pure-callback.v1",
    "qualification": "component_local_execution_only_not_whole_financial_pipeline_cost",
}


class ComponentExecutionUsage(FrozenContract):
    schema_version: Literal["market-component-local-execution-usage.v1"] = (
        "market-component-local-execution-usage.v1"
    )
    started_at: AwareDatetime
    completed_at: AwareDatetime
    elapsed_ns: int = Field(ge=0)
    thread_cpu_ns: int = Field(ge=0)
    external_calls: Literal[0]
    boundary: Literal["network-and-process-denied-installed-pure-callback.v1"]
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def ordered(self):
        if self.completed_at < self.started_at:
            raise ValueError("component_execution_clock_invalid")
        return self


def verify_local_execution_cost(captures: tuple[dict, ...]) -> tuple[bool, tuple[str, ...]]:
    """Authenticate measured callback costs; no token/USD inference for the wider pipeline."""
    if not captures:
        return False, ("component_local_execution_captures_missing",)
    reasons = []
    for capture in captures:
        if capture.get("schema_version") != "market-component-shadow-execution.v2":
            reasons.append("component_local_execution_usage_originals_missing")
            continue
        usage = ComponentExecutionUsage.model_validate(capture["execution_usage"])
        if (
            usage.manifest_hash != capture["manifest_hash"]
            or usage.input_hash != capture["input_hash"]
            or usage.output_hash != capture["output_hash"]
            or usage.completed_at != aware_utc(datetime.fromisoformat(capture["known_at"]))
        ):
            raise ValueError("component_local_execution_usage_binding_invalid")
        if (
            usage.elapsed_ns > LOCAL_EXECUTION_POLICY["maximum_elapsed_ns"]
            or usage.thread_cpu_ns > LOCAL_EXECUTION_POLICY["maximum_thread_cpu_ns"]
        ):
            reasons.append("component_local_execution_cost_exceeds_frozen_limit")
    return not reasons, tuple(dict.fromkeys(reasons))


class ComponentOriginalRef(FrozenContract):
    original_ref: str = Field(min_length=1, max_length=512)
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ComponentProtocolResult(FrozenContract):
    protocol_id: str = Field(min_length=1, max_length=128)
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["passed", "failed", "insufficient", "unavailable"]
    reasons: tuple[str, ...]
    original_refs: tuple[ComponentOriginalRef, ...]
    known_at: AwareDatetime
    independent_blocks: int = Field(default=0, ge=0)
    sample_qualified: bool = False
    cost_verified: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def qualification(self):
        if len({r.original_ref for r in self.original_refs}) != len(self.original_refs):
            raise ValueError("component_protocol_original_ref_duplicate")
        if self.status == "passed" and (
            self.reasons
            or not self.original_refs
            or not self.sample_qualified
            or not self.cost_verified
        ):
            raise ValueError("component_protocol_qualification_incomplete")
        return self


@dataclass(frozen=True)
class ComponentProtocolContext:
    """In-process composition ports, never deserialized from a request or model."""

    raw_archive: Any
    path_ledger: Any | None = None
    installed: Any = None


class ComponentProtocolHandler(Protocol):
    kind: ComponentKind
    protocol_id: str
    protocol_hash: str

    def evaluate(
        self,
        session: Session,
        *,
        manifest: ComponentManifest,
        trial: ShadowTrial,
        captures: tuple[dict, ...],
        original_refs: tuple[ComponentOriginalRef, ...],
        as_of: datetime,
        context: ComponentProtocolContext,
    ) -> ComponentProtocolResult: ...
