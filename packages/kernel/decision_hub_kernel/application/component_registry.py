"""Independent shadow/review/CAS and rollback, preserving every previous output."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select, update

from ..persistence.db import Database, as_utc, utcnow
from ..persistence.market_case_records import (
    MarketComponentAuditRecord,
    MarketComponentPointerRecord,
    MarketComponentReviewRecord,
    MarketComponentShadowRecord,
    MarketComponentVersionRecord,
)
from ..ports.component_registry import (
    ComponentManifest,
    ComponentOriginalReader,
    ComponentPointer,
    ComponentQualification,
    ComponentReview,
    ShadowTrial,
)
from ..ports.market_path import aware_utc, canonical_hash


def _body(model):
    return model.model_dump(mode="json")


def _read(row, model):
    if row is None:
        raise ValueError("component_original_not_found")
    value = model.model_validate_json(row.payload_json)
    if canonical_hash(_body(value)) != row.payload_hash:
        raise ValueError("component_original_hash_invalid")
    for key in (
        "version_id",
        "trial_id",
        "review_id",
        "request_id",
        "kind",
        "component_ref",
        "scope_id",
    ):
        if hasattr(row, key) and hasattr(value, key) and getattr(row, key) != getattr(value, key):
            raise ValueError("component_original_identity_invalid")
    for key in ("registered_at", "reviewed_at"):
        if (
            hasattr(row, key)
            and hasattr(value, key)
            and as_utc(getattr(row, key)) != getattr(value, key)
        ):
            raise ValueError("component_original_time_invalid")
    return value


class ComponentRegistry:
    def __init__(
        self,
        database: Database,
        *,
        original_reader: ComponentOriginalReader | None = None,
        clock=utcnow,
    ):
        self.database, self.original_reader, self.clock = database, original_reader, clock

    def register(self, manifest: ComponentManifest) -> ComponentManifest:
        # Repeated startup registration only authenticates immutable content;
        # it must remain available while another connection owns the writer.
        with self.database.session() as session:
            old = session.get(MarketComponentVersionRecord, manifest.version_id)
            if old:
                if _read(old, ComponentManifest) != manifest:
                    raise ValueError("component_version_reused")
                return manifest

        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = session.get(MarketComponentVersionRecord, manifest.version_id)
            if old:
                if _read(old, ComponentManifest) != manifest:
                    raise ValueError("component_version_reused")
                return manifest
            if manifest.parent_version_id:
                parent = _read(
                    session.get(MarketComponentVersionRecord, manifest.parent_version_id),
                    ComponentManifest,
                )
                if self._key(parent) != self._key(manifest):
                    raise ValueError("component_parent_scope_mismatch")
                if (parent.input_contract, parent.output_contract) != (
                    manifest.input_contract,
                    manifest.output_contract,
                ):
                    raise ValueError("component_contract_break_requires_new_component")
            session.add(
                MarketComponentVersionRecord(
                    version_id=manifest.version_id,
                    kind=manifest.kind,
                    component_ref=manifest.component_ref,
                    scope_id=manifest.scope_id,
                    created_at=now,
                    payload_json=manifest.model_dump_json(),
                    payload_hash=manifest.payload_hash,
                )
            )
            return manifest

    def shadow(
        self,
        trial_id: str,
        *,
        version_id: str,
        protocol_hash: str,
        baseline_version_id: str | None,
        start_at: datetime,
        end_at: datetime,
    ) -> ShadowTrial:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            manifest = _read(
                session.get(MarketComponentVersionRecord, version_id), ComponentManifest
            )
            if baseline_version_id:
                baseline = _read(
                    session.get(MarketComponentVersionRecord, baseline_version_id),
                    ComponentManifest,
                )
                if self._key(baseline) != self._key(manifest):
                    raise ValueError("component_shadow_baseline_mismatch")
            old = session.get(MarketComponentShadowRecord, trial_id)
            if old:
                trial = _read(old, ShadowTrial)
                if (
                    trial.version_id,
                    trial.protocol_hash,
                    trial.baseline_version_id,
                    trial.start_at,
                    trial.end_at,
                ) != (
                    version_id,
                    protocol_hash,
                    baseline_version_id,
                    aware_utc(start_at),
                    aware_utc(end_at),
                ):
                    raise ValueError("component_trial_reused")
                return trial
            trial = ShadowTrial(
                trial_id=trial_id,
                version_id=version_id,
                manifest_hash=manifest.payload_hash,
                protocol_hash=protocol_hash,
                baseline_version_id=baseline_version_id,
                registered_at=now,
                start_at=aware_utc(start_at),
                end_at=aware_utc(end_at),
            )
            session.add(
                MarketComponentShadowRecord(
                    trial_id=trial_id,
                    version_id=version_id,
                    registered_at=now,
                    payload_json=trial.model_dump_json(),
                    payload_hash=canonical_hash(_body(trial)),
                )
            )
            return trial

    def _qualification(self, manifest, trial, now) -> ComponentQualification:
        if self.original_reader is None:
            return ComponentQualification(
                trial_id=trial.trial_id,
                manifest_hash=manifest.payload_hash,
                protocol_hash=trial.protocol_hash,
                original_refs=(),
                original_hashes=(),
                known_at=now,
                status="unavailable",
                reasons=("component_original_reader_unavailable",),
            )
        result = self.original_reader.verify(manifest, trial)
        if (
            result.trial_id != trial.trial_id
            or result.manifest_hash != manifest.payload_hash
            or result.protocol_hash != trial.protocol_hash
            or result.known_at > now
        ):
            raise ValueError("component_qualification_binding_invalid")
        if result.status == "passed" and (now < trial.end_at or result.known_at < trial.end_at):
            raise ValueError("component_shadow_not_mature")
        return result

    def review(self, request_id: str, *, trial_id: str, reviewer_ref: str) -> ComponentReview:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = session.scalar(
                select(MarketComponentReviewRecord).where(
                    MarketComponentReviewRecord.request_id == request_id
                )
            )
            if old:
                value = _read(old, ComponentReview)
                if value.trial_id != trial_id or value.reviewer_ref != reviewer_ref:
                    raise ValueError("component_review_request_reused")
                return value
            trial = _read(session.get(MarketComponentShadowRecord, trial_id), ShadowTrial)
            manifest = _read(
                session.get(MarketComponentVersionRecord, trial.version_id), ComponentManifest
            )
            if trial.manifest_hash != manifest.payload_hash:
                raise ValueError("component_shadow_manifest_changed")
            qualification = self._qualification(manifest, trial, now)
            review = ComponentReview(
                review_id="crev_" + canonical_hash({"request_id": request_id})[:40],
                request_id=request_id,
                version_id=manifest.version_id,
                trial_id=trial_id,
                reviewer_ref=reviewer_ref,
                reviewed_at=now,
                qualification=qualification,
                decision="approved" if qualification.status == "passed" else "not_admitted",
            )
            session.add(
                MarketComponentReviewRecord(
                    review_id=review.review_id,
                    request_id=request_id,
                    version_id=manifest.version_id,
                    trial_id=trial_id,
                    reviewed_at=now,
                    payload_json=review.model_dump_json(),
                    payload_hash=canonical_hash(_body(review)),
                )
            )
            return review

    @staticmethod
    def _key(manifest):
        return manifest.kind, manifest.component_ref, manifest.scope_id

    @staticmethod
    def _pointer(row) -> ComponentPointer:
        return ComponentPointer(
            kind=row.kind,
            component_ref=row.component_ref,
            scope_id=row.scope_id,
            version_id=row.version_id,
            generation=row.generation,
            changed_at=as_utc(row.changed_at),
            audit_id=row.audit_id,
        )

    def activate(
        self, request_id: str, *, review_id: str, expected_generation: int
    ) -> ComponentPointer:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            review = _read(session.get(MarketComponentReviewRecord, review_id), ComponentReview)
            manifest = _read(
                session.get(MarketComponentVersionRecord, review.version_id), ComponentManifest
            )
            old = session.scalar(
                select(MarketComponentAuditRecord).where(
                    MarketComponentAuditRecord.request_id == request_id
                )
            )
            intent = {
                "operation": "activate",
                "review_id": review_id,
                "expected_generation": expected_generation,
            }
            if old:
                return self._audit_replay(old, intent)
            if review.decision != "approved":
                raise ValueError("component_review_not_admitted")
            trial = _read(session.get(MarketComponentShadowRecord, review.trial_id), ShadowTrial)
            fresh = self._qualification(manifest, trial, now)
            if fresh != review.qualification or fresh.status != "passed":
                raise ValueError("component_review_original_changed")
            return self._cas(session, request_id, manifest, expected_generation, now, intent)

    def rollback(
        self,
        request_id: str,
        *,
        kind: str,
        component_ref: str,
        scope_id: str,
        expected_generation: int,
    ) -> ComponentPointer:
        now = aware_utc(self.clock())
        pointer_id = canonical_hash([kind, component_ref, scope_id])
        intent = {
            "operation": "rollback",
            "kind": kind,
            "component_ref": component_ref,
            "scope_id": scope_id,
            "expected_generation": expected_generation,
        }
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = session.scalar(
                select(MarketComponentAuditRecord).where(
                    MarketComponentAuditRecord.request_id == request_id
                )
            )
            if old:
                return self._audit_replay(old, intent)
            pointer = session.get(MarketComponentPointerRecord, pointer_id)
            if pointer is None:
                raise ValueError("component_pointer_missing")
            audit = session.get(MarketComponentAuditRecord, pointer.audit_id)
            body = self._audit_body(audit)
            if body["after"] != _body(self._pointer(pointer)) or body["before"] is None:
                raise ValueError("component_rollback_previous_missing")
            manifest = _read(
                session.get(MarketComponentVersionRecord, body["before"]["version_id"]),
                ComponentManifest,
            )
            if self._key(manifest) != (kind, component_ref, scope_id):
                raise ValueError("component_rollback_scope_mismatch")
            return self._cas(session, request_id, manifest, expected_generation, now, intent)

    @staticmethod
    def _audit_body(row):
        if row is None:
            raise ValueError("component_audit_missing")
        body = json.loads(row.payload_json)
        after = ComponentPointer.model_validate(body["after"])
        if (
            canonical_hash(body) != row.payload_hash
            or after.audit_id != row.audit_id
            or body["request_id"] != row.request_id
            or after.changed_at != as_utc(row.created_at)
            or canonical_hash([after.kind, after.component_ref, after.scope_id]) != row.pointer_id
        ):
            raise ValueError("component_audit_invalid")
        return body

    @classmethod
    def _audit_replay(cls, row, intent):
        body = cls._audit_body(row)
        if body["intent"] != intent:
            raise ValueError("component_pointer_request_reused")
        return ComponentPointer.model_validate(body["after"])

    def _cas(self, session, request_id, manifest, expected_generation, now, intent):
        if expected_generation < 0:
            raise ValueError("component_generation_invalid")
        pointer_id = canonical_hash(list(self._key(manifest)))
        row = session.get(MarketComponentPointerRecord, pointer_id)
        if (row.generation if row else 0) != expected_generation:
            raise ValueError("component_pointer_conflict")
        before = _body(self._pointer(row)) if row else None
        if (
            row is not None
            and self._audit_body(session.get(MarketComponentAuditRecord, row.audit_id))["after"]
            != before
        ):
            raise ValueError("component_pointer_audit_mismatch")
        audit_id = "caudit_" + canonical_hash({"request_id": request_id})[:40]
        after = ComponentPointer(
            kind=manifest.kind,
            component_ref=manifest.component_ref,
            scope_id=manifest.scope_id,
            version_id=manifest.version_id,
            generation=expected_generation + 1,
            changed_at=now,
            audit_id=audit_id,
        )
        if row is None:
            session.add(
                MarketComponentPointerRecord(
                    pointer_id=pointer_id,
                    **{key: getattr(after, key) for key in type(after).model_fields},
                )
            )
        else:
            changed = session.execute(
                update(MarketComponentPointerRecord)
                .where(
                    MarketComponentPointerRecord.pointer_id == pointer_id,
                    MarketComponentPointerRecord.generation == expected_generation,
                )
                .values(
                    version_id=manifest.version_id,
                    generation=after.generation,
                    changed_at=now,
                    audit_id=audit_id,
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise ValueError("component_pointer_conflict")
        body = {
            "schema_version": "market-component-pointer-audit.v1",
            "request_id": request_id,
            "intent": intent,
            "before": before,
            "after": _body(after),
            "trading_authority": False,
        }
        session.add(
            MarketComponentAuditRecord(
                audit_id=audit_id,
                request_id=request_id,
                pointer_id=pointer_id,
                created_at=now,
                payload_json=json.dumps(body, sort_keys=True),
                payload_hash=canonical_hash(body),
            )
        )
        session.flush()
        fresh = session.get(MarketComponentPointerRecord, pointer_id, populate_existing=True)
        if self._pointer(fresh) != after:
            raise ValueError("component_pointer_writer_changed")
        if (
            self._audit_body(
                session.get(MarketComponentAuditRecord, audit_id, populate_existing=True)
            )
            != body
            or _read(
                session.get(
                    MarketComponentVersionRecord, manifest.version_id, populate_existing=True
                ),
                ComponentManifest,
            )
            != manifest
        ):
            raise ValueError("component_pointer_original_changed")
        return after

    def current(self, *, kind: str, component_ref: str, scope_id: str) -> ComponentPointer | None:
        with self.database.session() as session:
            row = session.get(
                MarketComponentPointerRecord, canonical_hash([kind, component_ref, scope_id])
            )
            if row is None:
                return None
            if self._audit_body(session.get(MarketComponentAuditRecord, row.audit_id))[
                "after"
            ] != _body(self._pointer(row)):
                raise ValueError("component_pointer_audit_mismatch")
            _read(session.get(MarketComponentVersionRecord, row.version_id), ComponentManifest)
            return self._pointer(row)
