"""Persist owner-scoped settings and pending business intents atomically."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1,
    DecisionSettingsCurrentViewV1,
    DecisionSettingsPartitionV1,
    DecisionSettingsRevisionV1,
    DecisionSettingsSaveReceiptV1,
)

from ..persistence.db import Database, ProductCommandRecord, as_utc
from ..persistence.decision_settings_records import (
    DecisionSettingsHeadRecord as Head,
)
from ..persistence.decision_settings_records import (
    DecisionSettingsRevisionRecord as Revision,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.decision_settings import BindingReader, PendingSettings
from ..ports.market_path import aware_utc, canonical_hash

SAVE = "decision_settings_save"


def _text(value: str, *, maximum: int = 128) -> str:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError("decision_settings_identity_invalid")
    return value


def _owner(value: str) -> str:
    if _text(value).startswith("system:"):
        raise ValueError("decision_settings_owner_invalid")
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dump(value: BaseModel) -> dict:
    return value.model_dump(mode="json")


def _seal[M: BaseModel](model: type[M], value: dict) -> M:
    # Hash exactly the generated model's JSON representation (not a datetime
    # representation that changes from +00:00 to Z during parse/serialize).
    body = _dump(model.model_validate({**value, "payload_hash": "0" * 64}))
    body["payload_hash"] = canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
    return model.model_validate(body)


def _valid_seal(model: BaseModel) -> bool:
    value = _dump(model)
    return value["payload_hash"] == canonical_hash(
        {k: v for k, v in value.items() if k != "payload_hash"}
    )


def declared_account_id(owner_id: str, allocation_request_id: str) -> str:
    """Stable identity only. Does not authenticate an account or declare funds."""
    return "declared_" + canonical_hash([_owner(owner_id), _text(allocation_request_id)])


def settings_key(owner_id: str, partition: DecisionSettingsPartitionV1) -> str:
    p = _dump(partition)
    if p["account_kind"] not in {"user_declared", "paper"}:
        raise ValueError("decision_settings_account_kind_invalid")
    _text(p["account_id"])
    _text(p["instrument_id"], maximum=192)
    _text(p["profile_ref"], maximum=256)
    return "dsettings_" + canonical_hash([_owner(owner_id), p])


def _request_key(owner_id: str, request_id: str) -> str:
    return "settings_" + canonical_hash([_owner(owner_id), _text(request_id)])


def _partition_columns(owner_id: str, partition: DecisionSettingsPartitionV1) -> dict:
    return {"owner_id": owner_id, **_dump(partition)}


def _revision_id(key: str, number: int, command_hash: str, parent_hash: str | None) -> str:
    return "settingsrev_" + canonical_hash([key, number, command_hash, parent_hash])


class DecisionSettingsService:
    def __init__(
        self,
        database: Database,
        *,
        now: Callable[[], datetime],
        binding_reader: BindingReader,
    ) -> None:
        # No permissive default: real adapter must authenticate the immutable
        # instrument/profile plus account ownership/source in a read snapshot.
        if not callable(binding_reader):
            raise ValueError("decision_settings_binding_reader_required")
        self.database, self._now, self._binding_reader = database, now, binding_reader

    @staticmethod
    def _revision(session: Session, revision_id: str) -> DecisionSettingsRevisionV1:
        current_id = revision_id
        first = None
        child = None
        seen: set[str] = set()
        while True:
            if current_id in seen:
                raise ValueError("decision_settings_revision_cycle")
            seen.add(current_id)
            row = session.get(Revision, current_id, populate_existing=True)
            if row is None:
                raise ValueError("decision_settings_revision_missing")
            value = DecisionSettingsRevisionV1.model_validate_json(row.payload_json)
            p = _dump(value.partition)
            parent_hash = value.parent.hash if value.parent else None
            if (
                not _valid_seal(value)
                or _canonical(_dump(value)) != row.payload_json
                or value.revision_id != row.revision_id
                or value.payload_hash != row.payload_hash
                or settings_key(value.owner_id, value.partition) != row.settings_key
                or value.settings_key != row.settings_key
                or value.owner_id != row.owner_id
                or any(p[name] != getattr(row, name) for name in p)
                or value.revision_no != row.revision_no
                or parent_hash != row.parent_hash
                or aware_utc(value.created_at) != as_utc(row.created_at)
                or value.revision_id
                != _revision_id(
                    value.settings_key,
                    value.revision_no,
                    value.command_hash,
                    parent_hash,
                )
                or (value.operation == "remove") != (value.values is None)
                or (value.revision_no == 1) != (value.parent is None)
            ):
                raise ValueError("decision_settings_revision_original_invalid")
            if child is not None and (
                child.parent.ref != value.revision_id
                or child.parent.hash != value.payload_hash
                or child.settings_key != value.settings_key
                or child.revision_no != value.revision_no + 1
            ):
                raise ValueError("decision_settings_parent_original_invalid")
            first = first or value
            if value.parent is None:
                return first
            current_id, child = value.parent.ref, value

    @classmethod
    def _head_revision(cls, session: Session, head: Head) -> DecisionSettingsRevisionV1:
        value = cls._revision(session, head.current_revision_id)
        p = _partition_columns(value.owner_id, value.partition)
        if (
            head.settings_key != value.settings_key
            or head.state_version != value.revision_no
            or any(getattr(head, name) != item for name, item in p.items())
            or head.scan_generation < 1
            or (
                bool(head.reevaluation_pending)
                and (
                    head.pending_revision_id != value.revision_id
                    or head.pending_state_version != head.state_version
                    or head.next_due_at is None
                )
            )
            or (
                not head.reevaluation_pending
                and (
                    head.pending_revision_id is not None
                    or head.pending_state_version is not None
                    or head.next_due_at is not None
                )
            )
        ):
            raise ValueError("decision_settings_head_original_invalid")
        return value

    def _receipt(self, session: Session, owner_id: str, request_id: str):
        row = session.get(
            ProductCommandRecord, _request_key(owner_id, request_id), populate_existing=True
        )
        if row is None:
            raise ValueError("decision_settings_command_missing")
        value = DecisionSettingsSaveReceiptV1.model_validate_json(row.result_json)
        revision = self._revision(session, value.revision.ref)
        if (
            row.kind != SAVE
            or row.owner_id != owner_id
            or value.owner_id != owner_id
            or value.request_id != request_id
            or row.target_id != revision.revision_id
            or row.payload_hash != value.command_hash
            or revision.command_hash != value.command_hash
            or value.revision.hash != revision.payload_hash
            or value.settings_key != revision.settings_key
            or value.owner_id != revision.owner_id
            or value.state_version != revision.revision_no
            or not _valid_seal(value)
            or _canonical(_dump(value)) != row.result_json
            or aware_utc(value.accepted_at) != as_utc(row.created_at)
            or aware_utc(value.accepted_at) != aware_utc(revision.created_at)
        ):
            raise ValueError("decision_settings_command_original_invalid")
        # Deliberately no current head equality: an old command is still the
        # original command after newer revisions or a tombstone were committed.
        return value

    def _replay(
        self, session: Session, owner: str, command: DecisionSettingsCommandV1, digest: str
    ):
        if session.get(ProductCommandRecord, _request_key(owner, command.request_id)) is None:
            return None
        old = self._receipt(session, owner, command.request_id)
        if old.command_hash != digest:
            raise ValueError("decision_settings_request_conflict")
        return old

    def receipt(self, owner_id: str, request_id: str) -> DecisionSettingsSaveReceiptV1:
        with self.database.session() as session:
            return self._receipt(session, _owner(owner_id), request_id)

    def save(
        self, owner_id: str, command: DecisionSettingsCommandV1
    ) -> DecisionSettingsSaveReceiptV1:
        owner = _owner(owner_id)
        key = settings_key(owner, command.partition)
        request_key = _request_key(owner, command.request_id)
        digest = canonical_hash(_dump(command))
        if (command.operation == "remove") != (command.values is None):
            raise ValueError("decision_settings_operation_values_invalid")
        with self.database.session() as session:
            old = self._replay(session, owner, command, digest)
            if old is not None:
                return old

        # Reparse constructed/copied model instances at the trusted service boundary.
        command = DecisionSettingsCommandV1.model_validate(_dump(command))
        if command.values is not None and command.values.account_snapshot.owner_id != owner:
            raise PermissionError("decision_settings_account_owner_mismatch")
        prepared = prepare_originals(
            self.database,
            lambda session: self._binding_reader(session, owner, command),
        )
        with self.database.session() as session:
            begin_immediate(session)
            old = self._replay(session, owner, command, digest)
            if old is not None:
                return old
            prepared.verify(session)
            head = session.get(Head, key, populate_existing=True)
            parent = self._head_revision(session, head) if head is not None else None
            actual_version = head.state_version if head is not None else 0
            if actual_version != command.expected_state_version:
                raise ValueError("decision_settings_state_conflict")
            if head is None and command.operation == "remove":
                raise ValueError("decision_settings_remove_missing")
            number = actual_version + 1
            now = aware_utc(self._now())
            parent_ref = (
                {"ref": parent.revision_id, "hash": parent.payload_hash} if parent else None
            )
            value = _seal(
                DecisionSettingsRevisionV1,
                {
                    "schema_version": "decision-settings-revision.v1",
                    "revision_id": _revision_id(
                        key, number, digest, parent.payload_hash if parent else None
                    ),
                    "settings_key": key,
                    "owner_id": owner,
                    "partition": _dump(command.partition),
                    "revision_no": number,
                    "parent": parent_ref,
                    "command_hash": digest,
                    "operation": command.operation,
                    "values": _dump(command.values) if command.values is not None else None,
                    "created_at": now,
                },
            )
            session.add(
                Revision(
                    revision_id=value.revision_id,
                    settings_key=key,
                    **_partition_columns(owner, command.partition),
                    revision_no=number,
                    parent_hash=parent.payload_hash if parent else None,
                    payload_json=_canonical(_dump(value)),
                    payload_hash=value.payload_hash,
                    created_at=now,
                )
            )
            # Explicit flush is also the rollback test's fault boundary. There
            # is no COMMIT until the Database.session context exits successfully.
            session.flush()
            changed = {
                "current_revision_id": value.revision_id,
                "state_version": number,
                "reevaluation_pending": 1,
                "pending_revision_id": value.revision_id,
                "pending_state_version": number,
                "scan_after_plan_id": None,
                "next_due_at": now,
                "scan_generation": head.scan_generation + 1 if head else 1,
            }
            if head is None:
                session.add(
                    Head(
                        settings_key=key, **_partition_columns(owner, command.partition), **changed
                    )
                )
            else:
                changed_count = session.execute(
                    update(Head)
                    .where(
                        Head.settings_key == key,
                        Head.state_version == actual_version,
                        Head.current_revision_id == parent.revision_id,
                    )
                    .values(**changed),
                    execution_options={"synchronize_session": False},
                ).rowcount
                if changed_count != 1:
                    raise ValueError("decision_settings_state_conflict")
            receipt = _seal(
                DecisionSettingsSaveReceiptV1,
                {
                    "schema_version": "decision-settings-save-receipt.v1",
                    "owner_id": owner,
                    "request_id": command.request_id,
                    "settings_key": key,
                    "command_hash": digest,
                    "revision": {"ref": value.revision_id, "hash": value.payload_hash},
                    "state_version": number,
                    "settings_saved": True,
                    "reevaluation_state": "pending",
                    "accepted_at": now,
                },
            )
            session.add(
                ProductCommandRecord(
                    request_id=request_key,
                    owner_id=owner,
                    target_id=value.revision_id,
                    kind=SAVE,
                    payload_hash=digest,
                    result_json=_canonical(_dump(receipt)),
                    created_at=now,
                )
            )
            session.flush()
            return self._receipt(session, owner, command.request_id)

    def current(
        self, owner_id: str, partition: DecisionSettingsPartitionV1
    ) -> DecisionSettingsCurrentViewV1 | None:
        with self.database.session() as session:
            return self.current_in_session(session, owner_id, partition)

    @classmethod
    def current_in_session(
        cls,
        session: Session,
        owner_id: str,
        partition: DecisionSettingsPartitionV1,
    ) -> DecisionSettingsCurrentViewV1 | None:
        """Authenticate current settings inside the caller's existing read snapshot."""
        key = settings_key(owner_id, partition)
        head = session.get(Head, key, populate_existing=True)
        if head is None:
            return None
        revision = cls._head_revision(session, head)
        return DecisionSettingsCurrentViewV1.model_validate(
            {
                "schema_version": "decision-settings-current-view.v1",
                "revision": _dump(revision),
                "state_version": head.state_version,
                "reevaluation_intent_state": "pending"
                if head.reevaluation_pending
                else "discovery_complete",
                "scan_generation": head.scan_generation,
                "scan_after_plan_id": head.scan_after_plan_id,
            }
        )

    def pending(self, limit: int = 2) -> tuple[PendingSettings, ...]:
        if not 1 <= limit <= 16:
            raise ValueError("decision_settings_pending_limit_invalid")
        now = aware_utc(self._now())
        with self.database.session() as session:
            heads = session.scalars(
                select(Head)
                .where(
                    Head.reevaluation_pending == 1,
                    Head.next_due_at <= now,
                )
                .order_by(Head.next_due_at, Head.settings_key)
                .limit(limit)
            ).all()
            result = []
            for head in heads:
                revision = self._head_revision(session, head)
                result.append(
                    PendingSettings(
                        head.settings_key,
                        revision.revision_id,
                        revision.payload_hash,
                        head.state_version,
                        head.scan_generation,
                        head.scan_after_plan_id,
                    )
                )
            return tuple(result)


# There is deliberately no consume()/mark_success() placeholder. Those require
# authenticated V2 action bindings, a real V2 review/derivation writer, and atomic
# per-target effect + dispatch receipt + pending cursor CAS (see README).
