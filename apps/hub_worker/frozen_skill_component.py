"""Staged exact-version feature execution. No active pointer or qualification grant."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from apps.hub_worker.market_components import (
    ComponentConsumerReceipt,
    ComponentSelection,
    MarketComponents,
    _json,  # pyright: ignore[reportPrivateUsage] -- reuse existing JSON contract; staged public seam
)
from packages.kernel.decision_hub_kernel.application.component_registry import (
    _read,  # pyright: ignore[reportPrivateUsage] -- same original verifier, no copied authentication
)
from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    FeatureComponentInput,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.market_features import MarketFeatureSnapshot
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

REASON = "frozen_skill_component_unqualified"


def _ref(value: object, id_field: str) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {id_field, "payload_hash"}
        or not isinstance(value[id_field], str)
        or not value[id_field]
        or not isinstance(value["payload_hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["payload_hash"]) is None
    ):
        raise ValueError("skill_component_reference_invalid")
    return dict(value)


class FrozenSkillComponentConsumer:
    """Composition-owned port; selected node service supplies authenticated originals."""

    def __init__(self, components: MarketComponents):
        self.components = components

    def _snapshot(self, session: Session) -> tuple[Connection, sqlite3.Connection]:
        bound = session.get_bind()
        if getattr(bound, "engine", bound) is not self.components.registry.database.engine:
            raise ValueError("skill_component_database_mismatch")
        # Reuse the physical transaction guard; expose it publicly during production integration.
        return self.components._snapshot(session)  # pyright: ignore[reportPrivateUsage]

    def _intent(
        self,
        *,
        scope_id: object,
        request_id: object,
        known_at: object,
        cutoff_at: object,
        payload: object,
    ):
        if (
            not isinstance(known_at, datetime)
            or not isinstance(cutoff_at, datetime)
            or not isinstance(scope_id, str)
            or not scope_id
            or not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 210
        ):
            raise ValueError("skill_component_intent_invalid")
        at, cutoff = aware_utc(known_at), aware_utc(cutoff_at)
        if cutoff > at:
            raise ValueError("skill_component_cutoff_after_known_at")
        original_input = _json(payload)
        typed = FeatureComponentInput.model_validate(original_input)
        if typed.scope.scope_id != scope_id or typed.as_of != cutoff:
            raise ValueError("skill_component_input_identity_mismatch")
        if any(
            bar.scope_id != scope_id
            or bar.venue != typed.scope.venue
            or bar.instrument != typed.scope.instrument
            or bar.price_type != typed.scope.price_type
            or bar.interval_seconds != typed.scope.interval_seconds
            or not bar.closed
            or bar.known_at > cutoff
            or bar.close_at > cutoff
            for bar in typed.candles
        ):
            raise ValueError("skill_component_input_outside_cutoff_or_scope")
        key = "skill-component-consume:" + request_id
        audit_id = "cconsume_" + canonical_hash([key])[:40]
        return at, cutoff, original_input, key, audit_id

    def _selection(
        self,
        session: Session,
        manifest_ref: object,
        scope_id: str,
        cutoff: datetime,
    ) -> ComponentSelection:
        ref = _ref(manifest_ref, "version_id")
        row = session.get(MarketComponentVersionRecord, ref["version_id"], populate_existing=True)
        if row is None:
            raise ValueError("component_original_not_found")
        manifest = _read(row, ComponentManifest)
        if manifest.payload_hash != ref["payload_hash"]:
            raise ValueError("skill_component_manifest_hash_mismatch")
        if (
            manifest.kind != "feature"
            or manifest.component_ref != "spot.features.v1"
            or manifest.scope_id != scope_id
            or manifest.input_contract != "market-feature-component-input.v1"
            or manifest.output_contract != "market-feature-snapshot.v1"
        ):
            raise ValueError("skill_component_contract_unsupported")
        if as_utc(row.created_at) > cutoff:
            raise ValueError("skill_component_registered_after_cutoff")
        installed = self.components.installed.get(manifest.version_id)
        if installed is None:
            raise ValueError("skill_component_version_not_installed")
        if installed.manifest(parent_version_id=manifest.parent_version_id) != manifest:
            raise ValueError("skill_component_installed_manifest_mismatch")
        return ComponentSelection(
            kind=manifest.kind,
            component_ref=manifest.component_ref,
            scope_id=scope_id,
            cutoff_at=cutoff,
            status="selected",
            reason_code=REASON,
            manifest=manifest,
            manifest_hash=manifest.payload_hash,
            original_readset_hash=canonical_hash(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "registered_at": as_utc(row.created_at).isoformat(),
                }
            ),
        )

    @staticmethod
    def _unchanged(
        session: Session,
        connection: Connection,
        native: sqlite3.Connection,
        count: int,
    ) -> None:
        if (
            session.connection() is not connection
            or not native.in_transaction
            or native.total_changes != count
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_consumer_changed_snapshot")

    def _read_audit(
        self,
        session: Session,
        *,
        audit_ref: object,
        selected: ComponentSelection,
        at: datetime,
        original_input: object,
        key: str,
        audit_id: str,
    ) -> ComponentConsumerReceipt:
        ref = _ref(audit_ref, "audit_id")
        row = session.get(MarketComponentAuditRecord, ref["audit_id"], populate_existing=True)
        if row is None:
            raise ValueError("skill_component_audit_not_found")
        value = ComponentConsumerReceipt.model_validate_json(row.payload_json)
        body_hash = canonical_hash(value.model_dump(mode="json"))
        if selected.manifest is None:
            raise ValueError("skill_component_manifest_missing")
        expected_baseline = {
            "kind": REASON,
            "manifest_ref": {
                "version_id": selected.manifest.version_id,
                "payload_hash": selected.manifest_hash,
            },
        }
        if (
            row.audit_id != audit_id
            or ref["audit_id"] != audit_id
            or value.audit_id != audit_id
            or row.request_id != key
            or value.request_id != key
            or row.payload_hash != body_hash
            or ref["payload_hash"] != body_hash
            or row.pointer_id != canonical_hash(["feature", "spot.features.v1", selected.scope_id])
            or as_utc(row.created_at) != at
            or value.known_at != at
            or value.selection != selected
            or value.state != "baseline_not_registry_approved"
            or value.baseline_identity != expected_baseline
            or value.input_hash != canonical_hash(original_input)
            or value.output_hash != canonical_hash(_json(value.output))
        ):
            raise ValueError("skill_component_audit_original_invalid")
        output = MarketFeatureSnapshot.model_validate(value.output)
        if output.scope.scope_id != selected.scope_id or output.as_of != selected.cutoff_at:
            raise ValueError("skill_component_output_identity_mismatch")
        return value

    def read_in_session(
        self,
        session: Session,
        *,
        audit_ref: object,
        manifest_ref: object,
        scope_id: str,
        request_id: str,
        known_at: datetime,
        cutoff_at: datetime,
        payload: object,
    ) -> ComponentConsumerReceipt:
        connection, native = self._snapshot(session)
        count = native.total_changes
        at, cutoff, original_input, key, audit_id = self._intent(
            scope_id=scope_id,
            request_id=request_id,
            known_at=known_at,
            cutoff_at=cutoff_at,
            payload=payload,
        )
        selected = self._selection(session, manifest_ref, scope_id, cutoff)
        value = self._read_audit(
            session,
            audit_ref=audit_ref,
            selected=selected,
            at=at,
            original_input=original_input,
            key=key,
            audit_id=audit_id,
        )
        self._unchanged(session, connection, native, count)
        return value

    def execute_in_session(
        self,
        session: Session,
        *,
        manifest_ref: object,
        scope_id: str,
        request_id: str,
        known_at: datetime,
        cutoff_at: datetime,
        payload: object,
    ) -> tuple[object, dict[str, str]]:
        connection, native = self._snapshot(session)
        count = native.total_changes
        at, cutoff, original_input, key, audit_id = self._intent(
            scope_id=scope_id,
            request_id=request_id,
            known_at=known_at,
            cutoff_at=cutoff_at,
            payload=payload,
        )
        selected = self._selection(session, manifest_ref, scope_id, cutoff)
        previous = session.scalar(
            select(MarketComponentAuditRecord)
            .where(
                MarketComponentAuditRecord.request_id == key,
            )
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            ref = {"audit_id": previous.audit_id, "payload_hash": previous.payload_hash}
            value = self._read_audit(
                session,
                audit_ref=ref,
                selected=selected,
                at=at,
                original_input=original_input,
                key=key,
                audit_id=audit_id,
            )
            self._unchanged(session, connection, native, count)
            return _json(value.output), ref
        if selected.manifest is None:
            raise ValueError("skill_component_manifest_missing")
        installed = self.components.installed[selected.manifest.version_id]
        output = _json(installed.callback(_json(original_input), _json(dict(installed.parameters))))
        typed = MarketFeatureSnapshot.model_validate(output)
        if typed.scope.scope_id != scope_id or typed.as_of != cutoff:
            raise ValueError("skill_component_output_identity_mismatch")
        installed.verify()
        self._unchanged(session, connection, native, count)
        if self._selection(session, manifest_ref, scope_id, cutoff) != selected:
            raise ValueError("component_consumer_original_changed")
        value = ComponentConsumerReceipt(
            request_id=key,
            audit_id=audit_id,
            known_at=at,
            state="baseline_not_registry_approved",
            selection=selected,
            input_hash=canonical_hash(original_input),
            output_hash=canonical_hash(output),
            output=output,
            baseline_identity={
                "kind": REASON,
                "manifest_ref": {
                    "version_id": selected.manifest.version_id,
                    "payload_hash": selected.manifest_hash,
                },
            },
        )
        body_hash = canonical_hash(value.model_dump(mode="json"))
        session.add(
            MarketComponentAuditRecord(
                audit_id=audit_id,
                request_id=key,
                pointer_id=canonical_hash(["feature", "spot.features.v1", scope_id]),
                created_at=at,
                payload_json=value.model_dump_json(),
                payload_hash=body_hash,
            )
        )
        session.flush()
        ref = {"audit_id": audit_id, "payload_hash": body_hash}
        saved = self._read_audit(
            session,
            audit_ref=ref,
            selected=selected,
            at=at,
            original_input=original_input,
            key=key,
            audit_id=audit_id,
        )
        if saved != value or self._selection(session, manifest_ref, scope_id, cutoff) != selected:
            raise ValueError("component_consumption_writer_changed")
        return _json(output), ref
