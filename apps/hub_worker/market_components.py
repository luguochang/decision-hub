"""Installed, bounded component consumers. Registry data never imports executable code.

Composition must inject installed functions at the five real consumer boundaries.
This module selects and executes those functions; it neither bootstraps approval
nor equates a component review with probability/trading qualification.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import CodeType, MappingProxyType
from typing import Literal

from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.component_registry import (
    ComponentRegistry,
    _read,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
    MarketComponentPointerRecord,
    MarketComponentReviewRecord,
    MarketComponentShadowRecord,
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import (
    ComponentKind,
    ComponentManifest,
    ComponentPointer,
    ComponentReview,
    ShadowTrial,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    aware_utc,
    canonical_hash,
)


def _json(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    # Produce an independent canonical JSON value; reject NaN and non-DTO objects.
    return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))


def _file_hashes(files: Mapping[str, Path]) -> dict[str, str]:
    if not files or any(not key or not path.is_file() for key, path in files.items()):
        raise ValueError("component_installed_files_missing")
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()}


@dataclass(frozen=True)
class InstalledComponent:
    """Trusted in-process declaration, created by composition, never a request DTO.

    A callback is a pure parser/feature/model/config/rule function. Network/model
    acquisition must occur outside this SQL snapshot and supplies its own DTO.
    Resource files may be empty; contracts and fixed parameters remain hashed.
    """

    kind: ComponentKind
    component_ref: str
    scope_id: str
    version_id: str
    input_contract: str
    output_contract: str
    callback: Callable
    code_files: Mapping[str, Path]
    resource_files: Mapping[str, Path] = field(default_factory=dict)
    parameters: Mapping = field(default_factory=dict)
    _code: CodeType = field(init=False, repr=False)
    _code_hash: str = field(init=False, repr=False)
    _resource_hash: str = field(init=False, repr=False)
    _parameters_json: str = field(init=False, repr=False)

    def __post_init__(self):
        if not inspect.isfunction(self.callback):
            raise ValueError("component_installed_function_required")
        files = MappingProxyType(
            {key: Path(path).resolve() for key, path in self.code_files.items()}
        )
        resources = MappingProxyType(
            {key: Path(path).resolve() for key, path in self.resource_files.items()}
        )
        actual_file = Path(inspect.getsourcefile(self.callback) or "").resolve()
        if actual_file not in files.values():
            raise ValueError("component_callable_source_unbound")
        params = json.dumps(_json(dict(self.parameters)), sort_keys=True, allow_nan=False)
        object.__setattr__(self, "code_files", files)
        object.__setattr__(self, "resource_files", resources)
        object.__setattr__(self, "parameters", MappingProxyType(json.loads(params)))
        object.__setattr__(self, "_parameters_json", params)
        object.__setattr__(self, "_code", self.callback.__code__)
        object.__setattr__(self, "_code_hash", self._current_code_hash())
        object.__setattr__(self, "_resource_hash", self._current_resource_hash())

    def _current_code_hash(self):
        return canonical_hash(
            {
                "files": _file_hashes(self.code_files),
                "function": self.callback.__qualname__,
                "source": hashlib.sha256(inspect.getsource(self.callback).encode()).hexdigest(),
            }
        )

    def _current_resource_hash(self):
        return canonical_hash(
            {
                "files": _file_hashes(self.resource_files) if self.resource_files else {},
                "parameters": json.loads(self._parameters_json),
                "input_contract": self.input_contract,
                "output_contract": self.output_contract,
            }
        )

    def verify(self):
        if (
            self.callback.__code__ is not self._code
            or self._current_code_hash() != self._code_hash
            or self._current_resource_hash() != self._resource_hash
            or _json(dict(self.parameters)) != json.loads(self._parameters_json)
        ):
            raise ValueError("component_installed_identity_changed")

    def manifest(self, *, parent_version_id: str | None = None) -> ComponentManifest:
        self.verify()
        return ComponentManifest(
            version_id=self.version_id,
            kind=self.kind,
            component_ref=self.component_ref,
            scope_id=self.scope_id,
            parent_version_id=parent_version_id,
            input_contract=self.input_contract,
            output_contract=self.output_contract,
            code_hash=self._code_hash,
            resource_hash=self._resource_hash,
            parameters=json.loads(self._parameters_json),
        )


class ComponentSelection(FrozenContract):
    schema_version: Literal["market-component-selection.v1"] = "market-component-selection.v1"
    kind: ComponentKind
    component_ref: str
    scope_id: str
    cutoff_at: AwareDatetime
    status: Literal["selected", "unavailable"]
    reason_code: str | None = None
    pointer: ComponentPointer | None = None
    manifest: ComponentManifest | None = None
    manifest_hash: str | None = None
    original_readset_hash: str | None = None
    # Installation/approval is distinct from a qualified financial model.
    trading_authority: Literal[False] = False


class ComponentConsumption(FrozenContract):
    schema_version: Literal["market-component-consumption.v1"] = "market-component-consumption.v1"
    selection: ComponentSelection
    input_hash: str
    output_hash: str | None = None
    output: object = None
    probability_qualified: Literal[False] = False


class ComponentConsumerReceipt(FrozenContract):
    schema_version: Literal["market-component-consumption.v1"] = "market-component-consumption.v1"
    request_id: str
    audit_id: str
    known_at: AwareDatetime
    state: Literal["selected", "baseline_not_registry_approved"]
    selection: ComponentSelection
    input_hash: str
    output_hash: str
    output: object
    baseline_identity: dict | None = None
    probability_qualified: Literal[False] = False
    trading_authority: Literal[False] = False


class MarketComponents:
    def __init__(self, registry: ComponentRegistry, installed: tuple[InstalledComponent, ...]):
        self.registry = registry
        self.installed = MappingProxyType({item.version_id: item for item in installed})
        if len(self.installed) != len(installed):
            raise ValueError("component_installed_version_duplicate")

    def register_installed(self) -> tuple[ComponentManifest, ...]:
        """Explicit registration only. No shadow, review or pointer promotion."""
        return tuple(self.registry.register(item.manifest()) for item in self.installed.values())

    @staticmethod
    def _snapshot(session: Session):
        connection = session.connection()
        native = connection.connection.driver_connection
        if (
            connection.dialect.name != "sqlite"
            or not isinstance(native, sqlite3.Connection)
            or not native.in_transaction
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_clean_physical_snapshot_required")
        return connection, native

    def _selection(self, session, *, kind, component_ref, scope_id, cutoff_at):
        cutoff = aware_utc(cutoff_at)
        key = canonical_hash([kind, component_ref, scope_id])
        row = session.get(MarketComponentPointerRecord, key, populate_existing=True)
        base = dict(kind=kind, component_ref=component_ref, scope_id=scope_id, cutoff_at=cutoff)
        if row is None:
            return ComponentSelection(
                **base, status="unavailable", reason_code="component_pointer_missing"
            )
        pointer = self.registry._pointer(row)
        if (pointer.kind, pointer.component_ref, pointer.scope_id) != (
            kind,
            component_ref,
            scope_id,
        ):
            raise ValueError("component_pointer_scope_invalid")
        audit = session.get(MarketComponentAuditRecord, pointer.audit_id, populate_existing=True)
        body = self.registry._audit_body(audit)
        if body["after"] != pointer.model_dump(mode="json"):
            raise ValueError("component_pointer_audit_mismatch")
        version = session.get(
            MarketComponentVersionRecord, pointer.version_id, populate_existing=True
        )
        manifest = _read(version, ComponentManifest)
        if (manifest.kind, manifest.component_ref, manifest.scope_id) != (
            kind,
            component_ref,
            scope_id,
        ):
            raise ValueError("component_manifest_scope_invalid")
        if pointer.changed_at > cutoff or as_utc(version.created_at) > cutoff:
            return ComponentSelection(
                **base, status="unavailable", reason_code="component_pointer_after_cutoff"
            )
        installed = self.installed.get(manifest.version_id)
        if installed is None:
            return ComponentSelection(
                **base, status="unavailable", reason_code="component_version_not_installed"
            )
        expected = installed.manifest(parent_version_id=manifest.parent_version_id)
        if expected != manifest:
            raise ValueError("component_manifest_installed_mismatch")
        # Follow the exact pointer audit ancestry, including rollback. A current
        # pointer alone is not evidence that its original review is still valid.
        lineage, cursor = [], body
        for _ in range(64):
            lineage.append(cursor)
            intent = cursor["intent"]
            if (
                cursor["after"]["version_id"] == manifest.version_id
                and intent["operation"] == "activate"
            ):
                review_row = session.get(
                    MarketComponentReviewRecord, intent["review_id"], populate_existing=True
                )
                review = _read(review_row, ComponentReview)
                trial_row = session.get(
                    MarketComponentShadowRecord, review.trial_id, populate_existing=True
                )
                trial = _read(trial_row, ShadowTrial)
                if (
                    review.version_id != manifest.version_id
                    or trial.version_id != manifest.version_id
                    or trial.manifest_hash != manifest.payload_hash
                    or review.decision != "approved"
                    or review.reviewed_at > cutoff
                    or trial.registered_at > cutoff
                ):
                    raise ValueError("component_consumer_review_invalid")
                qualification = self.registry._qualification(manifest, trial, cutoff)
                if qualification != review.qualification or qualification.status != "passed":
                    return ComponentSelection(
                        **base,
                        status="unavailable",
                        reason_code="component_original_qualification_unavailable",
                    )
                break
            previous = cursor.get("before")
            if previous is None:
                return ComponentSelection(
                    **base,
                    status="unavailable",
                    reason_code="component_original_review_unavailable",
                )
            previous_row = session.get(
                MarketComponentAuditRecord, previous["audit_id"], populate_existing=True
            )
            cursor = self.registry._audit_body(previous_row)
            if cursor["after"] != previous:
                raise ValueError("component_pointer_ancestry_invalid")
        else:
            raise ValueError("component_pointer_ancestry_limit")
        readset = {
            "pointer": pointer.model_dump(mode="json"),
            "audit": body,
            "manifest": manifest.model_dump(mode="json"),
            "manifest_created_at": as_utc(version.created_at).isoformat(),
            "audit_ancestry": lineage,
            "review": review.model_dump(mode="json"),
            "trial": trial.model_dump(mode="json"),
            "qualification": qualification.model_dump(mode="json"),
        }
        return ComponentSelection(
            **base,
            status="selected",
            pointer=pointer,
            manifest=manifest,
            manifest_hash=manifest.payload_hash,
            original_readset_hash=canonical_hash(readset),
        )

    def freeze_in_session(
        self,
        session: Session,
        *,
        kind: ComponentKind,
        component_ref: str,
        scope_id: str,
        cutoff_at: datetime,
    ) -> ComponentSelection:
        """Caller may persist this exact receipt in its existing frozen bundle.

        No latest/default substitution, no historical pointer reconstruction,
        no new Session/BEGIN/authorizer. Old frozen output is never rewritten.
        """
        self._snapshot(session)
        with session.no_autoflush:
            return self._selection(
                session,
                kind=kind,
                component_ref=component_ref,
                scope_id=scope_id,
                cutoff_at=cutoff_at,
            )

    def consume_in_session(
        self,
        session: Session,
        *,
        kind: ComponentKind,
        component_ref: str,
        scope_id: str,
        cutoff_at: datetime,
        value: object,
    ) -> ComponentConsumption:
        connection, native = self._snapshot(session)
        count = native.total_changes
        selected = self.freeze_in_session(
            session, kind=kind, component_ref=component_ref, scope_id=scope_id, cutoff_at=cutoff_at
        )
        original_input = _json(value)
        if selected.status != "selected":
            return ComponentConsumption(
                selection=selected, input_hash=canonical_hash(original_input)
            )
        installed = self.installed[selected.manifest.version_id]
        # Supply a detached JSON input and immutable declared parameters.
        output = _json(
            installed.callback(_json(original_input), json.loads(installed._parameters_json))
        )
        installed.verify()
        if (
            session.connection() is not connection
            or not native.in_transaction
            or native.total_changes != count
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_consumer_changed_snapshot")
        again = self.freeze_in_session(
            session, kind=kind, component_ref=component_ref, scope_id=scope_id, cutoff_at=cutoff_at
        )
        if again != selected:
            raise ValueError("component_consumer_original_changed")
        return ComponentConsumption(
            selection=selected,
            input_hash=canonical_hash(original_input),
            output=output,
            output_hash=canonical_hash(output),
        )

    def __call__(
        self,
        session: Session,
        *,
        kind: ComponentKind,
        component_ref: str,
        scope_id: str,
        payload: object,
        fallback: Callable,
        request_id: str,
        known_at: datetime,
        cutoff_at: datetime | None = None,
    ) -> tuple[object, dict]:
        """Same-writer consumer hook. Preserve the established baseline only
        when no component pointer exists; never silently downgrade a bad pointer.
        """
        connection, native = self._snapshot(session)
        at, cutoff = aware_utc(known_at), aware_utc(cutoff_at or known_at)
        if cutoff > at or not request_id or len(request_id) > 230:
            raise ValueError("component_consumer_intent_invalid")
        key = "component-consume:" + request_id
        audit_id = "cconsume_" + canonical_hash([key])[:40]
        input_hash = canonical_hash(_json(payload))
        previous = session.scalar(
            select(MarketComponentAuditRecord)
            .where(MarketComponentAuditRecord.request_id == key)
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            value = ComponentConsumerReceipt.model_validate_json(previous.payload_json)
            if (
                previous.audit_id != audit_id
                or value.audit_id != audit_id
                or value.request_id != key
                or previous.payload_hash != canonical_hash(value.model_dump(mode="json"))
                or previous.pointer_id != canonical_hash([kind, component_ref, scope_id])
                or as_utc(previous.created_at) != value.known_at
                or (
                    value.selection.kind,
                    value.selection.component_ref,
                    value.selection.scope_id,
                    value.selection.cutoff_at,
                    value.input_hash,
                )
                != (kind, component_ref, scope_id, cutoff, input_hash)
                or value.output_hash != canonical_hash(_json(value.output))
            ):
                raise ValueError("component_consumption_original_invalid")
            return value.output, value.model_dump(mode="json")
        count = native.total_changes
        consumed = self.consume_in_session(
            session,
            kind=kind,
            component_ref=component_ref,
            scope_id=scope_id,
            cutoff_at=cutoff,
            value=payload,
        )
        baseline_identity = None
        if consumed.selection.status == "selected":
            output, state = consumed.output, "selected"
        elif consumed.selection.reason_code == "component_pointer_missing":
            if not inspect.isfunction(fallback):
                raise ValueError("component_baseline_function_required")
            code = fallback.__code__
            source = Path(inspect.getsourcefile(fallback) or "")
            baseline_identity = {
                "function": fallback.__qualname__,
                "source_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "function_hash": hashlib.sha256(inspect.getsource(fallback).encode()).hexdigest(),
            }
            output, state = _json(fallback(_json(payload))), "baseline_not_registry_approved"
            if (
                fallback.__code__ is not code
                or hashlib.sha256(source.read_bytes()).hexdigest()
                != baseline_identity["source_hash"]
            ):
                raise ValueError("component_baseline_identity_changed")
        else:
            raise ValueError(
                "component_consumer_unavailable:" + str(consumed.selection.reason_code)
            )
        if (
            session.connection() is not connection
            or not native.in_transaction
            or native.total_changes != count
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_consumer_changed_snapshot")
        if (
            self.freeze_in_session(
                session, kind=kind, component_ref=component_ref, scope_id=scope_id, cutoff_at=cutoff
            )
            != consumed.selection
        ):
            raise ValueError("component_consumer_original_changed")
        value = ComponentConsumerReceipt(
            request_id=key,
            audit_id=audit_id,
            known_at=at,
            state=state,
            selection=consumed.selection,
            input_hash=input_hash,
            output_hash=canonical_hash(_json(output)),
            output=output,
            baseline_identity=baseline_identity,
        )
        session.add(
            MarketComponentAuditRecord(
                audit_id=audit_id,
                request_id=key,
                pointer_id=canonical_hash([kind, component_ref, scope_id]),
                created_at=at,
                payload_json=value.model_dump_json(),
                payload_hash=canonical_hash(value.model_dump(mode="json")),
            )
        )
        session.flush()
        saved = session.get(MarketComponentAuditRecord, audit_id, populate_existing=True)
        if (
            ComponentConsumerReceipt.model_validate_json(saved.payload_json) != value
            or saved.audit_id != audit_id
            or saved.request_id != key
            or saved.pointer_id != canonical_hash([kind, component_ref, scope_id])
            or as_utc(saved.created_at) != at
            or saved.payload_hash != canonical_hash(value.model_dump(mode="json"))
            or self.freeze_in_session(
                session, kind=kind, component_ref=component_ref, scope_id=scope_id, cutoff_at=cutoff
            )
            != consumed.selection
        ):
            raise ValueError("component_consumption_writer_changed")
        return output, value.model_dump(mode="json")
