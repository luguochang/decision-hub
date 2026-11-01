"""Immutable owner-bound Saved confirmation authorization journal."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from ..persistence.db import Database, ProductCommandRecord, as_utc, utcnow
from ..ports.research_saved_confirmation import (
    SavedConfirmationCommandV1, SavedConfirmationReceiptV1,
    SavedConfirmationReplyV1, SavedConfirmationStatusV1,
)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class ResearchSavedConfirmationCommandService:
    KIND = "saved_confirmation"
    RECEIPT_BOUND = 64 * 1024

    def __init__(self, database: Database, lifecycle, *, clock: Callable[[], datetime] = utcnow):
        self.database, self.lifecycle, self.clock = database, lifecycle, clock

    @classmethod
    def _fingerprint(cls, owner_id, run_id, command, owner_native_sha256):
        return hashlib.sha256(_canonical([
            cls.KIND, owner_id, run_id, command.model_dump(mode="json"), owner_native_sha256,
        ]).encode("utf-8")).hexdigest()

    @classmethod
    def validated_receipt(cls, record, *, run_id, owner_id=None, command=None):
        """Validate immutable journal bytes; owner_id=None is for the internal worker."""
        if (record.kind != cls.KIND or record.target_id != run_id
                or owner_id is not None and record.owner_id != owner_id
                or type(record.result_json) is not str
                or not isinstance(record.created_at, datetime)
                or not 0 < len(record.result_json.encode("utf-8")) <= cls.RECEIPT_BOUND):
            raise ValueError("saved_confirmation_request_reused")
        receipt = SavedConfirmationReceiptV1.model_validate_json(record.result_json)
        if (receipt.run_id != run_id or receipt.owner_id != record.owner_id
                or receipt.request_id != record.request_id
                or receipt.payload_hash != record.payload_hash
                or receipt.payload_hash != cls._fingerprint(
                    receipt.owner_id, run_id, receipt.command, receipt.owner_native_sha256)
                or receipt.recorded_at != as_utc(record.created_at)
                or _canonical(receipt.model_dump(mode="json")) != record.result_json
                or command is not None and receipt.command != command):
            raise ValueError("saved_confirmation_receipt_invalid")
        return receipt

    def _owner_native_sha256(self, session, run_id, owner_id):
        proof = self.lifecycle.saved_confirmation_owner_proof(session, run_id, owner_id)
        if not isinstance(proof, (dict, tuple, list)) or not proof:
            raise PermissionError("saved_confirmation_normal_owner_required")
        return hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()

    def status(self, run_id: str, *, owner_id: str) -> SavedConfirmationStatusV1:
        result = SavedConfirmationStatusV1.model_validate(
            self.lifecycle.saved_confirmation_status(run_id, owner_id))
        if result.run_id != run_id:
            raise ValueError("saved_confirmation_status_identity_changed")
        return result

    def _receipt_snapshot(self, session, run_id, request_id, owner_id):
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        owner_hash = self._owner_native_sha256(session, run_id, owner_id)
        record = session.get(ProductCommandRecord, request_id, populate_existing=True)
        receipt = None if record is None else self.validated_receipt(
            record, run_id=run_id, owner_id=owner_id)
        if receipt is not None and receipt.owner_native_sha256 != owner_hash:
            raise PermissionError("saved_confirmation_owner_scope_changed")
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        return receipt, owner_hash

    def receipt(self, run_id: str, request_id: str, *, owner_id: str,
                missing_ok: bool = False) -> SavedConfirmationReceiptV1 | None:
        with self.lifecycle._ro() as session:
            entry = self._receipt_snapshot(session, run_id, request_id, owner_id)
        with self.lifecycle._ro() as session:
            exit = self._receipt_snapshot(session, run_id, request_id, owner_id)
        if entry != exit:
            raise ValueError("saved_confirmation_receipt_changed")
        if exit[0] is None and not missing_ok:
            raise KeyError(request_id)
        return exit[0]

    def apply(self, run_id: str, command: SavedConfirmationCommandV1, *,
              owner_id: str) -> SavedConfirmationReceiptV1:
        if type(command) is not SavedConfirmationCommandV1 or command.owner_id != owner_id:
            raise PermissionError("saved_confirmation_normal_owner_required")
        if SavedConfirmationCommandV1.model_validate_json(command.model_dump_json()) != command:
            raise ValueError("saved_confirmation_command_invalid")
        prior = self.receipt(run_id, command.request_id, owner_id=owner_id, missing_ok=True)
        if prior is not None:
            if prior.command != command:
                raise ValueError("saved_confirmation_request_reused")
            return prior
        with self.lifecycle._ro() as session:
            owner_hash = self._owner_native_sha256(session, run_id, owner_id)
        digest = self._fingerprint(owner_id, run_id, command, owner_hash)

        def journal_writer(session, reply, *, write):
            actual_owner_hash = self._owner_native_sha256(session, run_id, owner_id)
            if actual_owner_hash != owner_hash:
                raise PermissionError("saved_confirmation_owner_scope_changed")
            result = SavedConfirmationReplyV1.model_validate(reply)
            record = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if write:
                if record is not None:
                    raise ValueError("saved_confirmation_request_reused")
                now = self.clock()
                receipt = SavedConfirmationReceiptV1(
                    schema_version="research-saved-confirmation-receipt.v1",
                    request_id=command.request_id, owner_id=owner_id, run_id=run_id,
                    command=command, result=result, status="accepted", payload_hash=digest,
                    owner_native_sha256=owner_hash, recorded_at=now)
                raw = _canonical(receipt.model_dump(mode="json"))
                if len(raw.encode("utf-8")) > self.RECEIPT_BOUND:
                    raise ValueError("saved_confirmation_receipt_bound_exceeded")
                session.add(ProductCommandRecord(
                    request_id=command.request_id, owner_id=owner_id, target_id=run_id,
                    kind=self.KIND, payload_hash=digest, result_json=raw, created_at=now))
            else:
                if record is None:
                    raise ValueError("saved_confirmation_receipt_missing")
                receipt = self.validated_receipt(
                    record, run_id=run_id, owner_id=owner_id, command=command)
                if receipt.owner_native_sha256 != owner_hash or receipt.result != result:
                    raise ValueError("saved_confirmation_receipt_invalid")

        # Unknown SQL/COMMIT and its durable fence propagate unchanged. Only a
        # read of the retained request may reconcile that outcome; no retry here.
        try:
            reply = SavedConfirmationReplyV1.model_validate(
                self.lifecycle.authorize_saved_confirmation(
                    run_id, command, owner_id, journal_writer=journal_writer))
        except (PermissionError, ValueError) as error:
            if not (
                type(error) is PermissionError
                and error.args == ("publication_saved_confirmation_authorization_CAS_lost",)
                or type(error) is ValueError
                and error.args == ("publication_saved_confirmation_exact_terminal_required",)
            ):
                raise
            prior = self.receipt(run_id, command.request_id, owner_id=owner_id, missing_ok=True)
            if prior is None or prior.command != command:
                raise
            return prior
        receipt = self.receipt(run_id, command.request_id, owner_id=owner_id)
        if receipt is None or receipt.command != command or receipt.result != reply:
            raise ValueError("saved_confirmation_receipt_invalid")
        return receipt
