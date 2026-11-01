"""Immutable storage for policy assessments in the caller's SQLite transaction.

Storage validates declarations and content identity. It does not attest handler
execution or authorize publication. The checker and Commit must independently
load original inputs and execute the registered rules in this same transaction.
Existing v1 ledger helpers and receipts are left intact.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyCheckReceipt,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)

PolicyPhase = Literal[
    "pre_submission", "evidence_admission", "finalization", "commit", "evaluation",
]
SCHEMA = "research-policy-check.v1"


class ResearchPolicyReceiptError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


def _model(receipt: object) -> ResearchPolicyCheckReceipt:
    # Revalidate even model_construct() values and nested lists modified after
    # construction. A Python model instance is not a trusted producer token.
    if not isinstance(receipt, ResearchPolicyCheckReceipt):
        raise ResearchPolicyReceiptError("policy_receipt_contract_invalid")
    try:
        return ResearchPolicyCheckReceipt.model_validate_json(receipt.model_dump_json())
    except (ValueError, TypeError) as exc:
        raise ResearchPolicyReceiptError("policy_receipt_contract_invalid") from exc


def policy_receipt_identity_hash(receipt: ResearchPolicyCheckReceipt) -> str:
    """Bind the complete assessment input identity, not its claimed outcomes."""
    value = _model(receipt)
    reference = as_utc(value.reference_at)
    assert reference is not None
    return recipe_hash({
        "schema_version": value.schema_version,
        "run_id": value.run_id, "event_id": value.event_id, "phase": value.phase,
        "binding_hash": value.binding.binding_hash, "method_hash": value.method_hash,
        "trigger_snapshot_id": value.trigger_snapshot_id,
        "trigger_snapshot_hash": value.trigger_snapshot_hash,
        "decision_snapshot_id": value.decision_snapshot_id,
        "decision_snapshot_hash": value.decision_snapshot_hash,
        "reference_at": reference.isoformat(),
        "result_content_hash": value.result_content_hash,
        "result_payload_hash": value.result_payload_hash,
        "input_set_hash": value.input_set_hash,
        "checker_version": value.checker_version,
        "checker_code_hash": value.checker_code_hash,
    })


def policy_receipt_role(phase: PolicyPhase, full_identity_hash: str) -> str:
    return f"policy_check:{phase}:{full_identity_hash[:32]}"


def policy_receipt_id(full_identity_hash: str) -> str:
    return "policy_check_" + full_identity_hash


def _encoded(receipt: ResearchPolicyCheckReceipt) -> tuple[ResearchPolicyCheckReceipt, str]:
    value = _model(receipt)
    binding = value.binding.model_dump(mode="json", exclude={"binding_hash"})
    reference, assessed = as_utc(value.reference_at), as_utc(value.assessed_at)
    assert reference is not None and assessed is not None
    if (
        value.binding.run_id != value.run_id or value.binding.event_id != value.event_id
        or value.binding.binding_hash != recipe_hash(binding)
        or value.checker_version != value.binding.checker_version
        or value.checker_code_hash != value.binding.checker_code_hash
        or (value.decision_snapshot_id is None) != (value.decision_snapshot_hash is None)
        or (value.result_content_hash is None) != (value.result_payload_hash is None)
        or reference > assessed
    ):
        raise ResearchPolicyReceiptError("policy_receipt_binding_invalid")
    identities = [check.rule_id for check in value.checks]
    if len(identities) != len(set(identities)):
        raise ResearchPolicyReceiptError("policy_receipt_duplicate_rule")
    blocking = [check for check in value.checks
                if check.blocking_for and check.applicability != "not_applicable"]
    if value.all_applicable_blocking_rules_passed and (
        not blocking or any(check.status != "pass" or check.applicability != "applicable"
                            for check in blocking)
    ):
        raise ResearchPolicyReceiptError("policy_receipt_claimed_pass_inconsistent")
    for check in value.checks:
        observed = as_utc(check.observed_at)
        if (
            as_utc(check.reference_at) != reference or observed is None or observed > assessed
            or (check.applicability != "applicable" and check.status != "not_available")
        ):
            raise ResearchPolicyReceiptError("policy_receipt_check_invalid")
    if (
        value.full_identity_hash != policy_receipt_identity_hash(value)
        or value.receipt_id != policy_receipt_id(value.full_identity_hash)
    ):
        raise ResearchPolicyReceiptError("policy_receipt_identity_invalid")
    body = value.model_dump(mode="json")
    body["reference_at"], body["assessed_at"] = reference.isoformat(), assessed.isoformat()
    for check, item in zip(value.checks, body["checks"], strict=True):
        item["reference_at"] = reference.isoformat()
        observed = as_utc(check.observed_at)
        assert observed is not None
        item["observed_at"] = observed.isoformat()
    encoded = canonical_recipe(body)
    return ResearchPolicyCheckReceipt.model_validate_json(encoded), encoded


def _read(row: RunRoleOutputRecord) -> ResearchPolicyCheckReceipt:
    try:
        value, encoded = _encoded(ResearchPolicyCheckReceipt.model_validate_json(row.payload_json))
        if (
            row.schema_version != SCHEMA or row.run_id != value.run_id
            or row.output_id != value.receipt_id
            or row.role != policy_receipt_role(value.phase, value.full_identity_hash)
            or row.payload_json != encoded
            or row.payload_hash != hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            or as_utc(row.created_at) != as_utc(value.assessed_at)
        ):
            raise ValueError("policy_receipt_row_mismatch")
        return value
    except (ValueError, TypeError) as exc:
        raise ResearchPolicyReceiptError("policy_receipt_saved_invalid") from exc


def read_policy_check_receipt(
    session: Session, receipt_id: str,
) -> ResearchPolicyCheckReceipt | None:
    """Read current SQL declarations; this is not a pass verifier.

    A caller may retain ORM objects across transactions. Always refresh from the
    database so an old identity-map object cannot hide a changed stored row.
    """
    row = session.get(RunRoleOutputRecord, receipt_id, populate_existing=True)
    return _read(row) if row is not None else None


def store_policy_check_receipt(
    session: Session, receipt: ResearchPolicyCheckReceipt,
) -> ResearchPolicyCheckReceipt:
    """Compare or insert; never begin, commit or roll back a caller transaction.

    The caller must take BEGIN IMMEDIATE before reading the assessment inputs.
    This guard rejects SQLAlchemy's implicit read-only autobegin. SQLite cannot
    expose which BEGIN mode was used, so the caller still owns the lock mode.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection)
        or not native.in_transaction
    ):
        raise ResearchPolicyReceiptError("policy_receipt_caller_write_transaction_required")
    value, encoded = _encoded(receipt)
    run = session.get(RunRecord, value.run_id, populate_existing=True)
    if run is None or run.event_id != value.event_id:
        raise ResearchPolicyReceiptError("policy_receipt_run_identity_mismatch")
    role = policy_receipt_role(value.phase, value.full_identity_hash)
    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == value.run_id, RunRoleOutputRecord.role == role,
    ).execution_options(populate_existing=True))
    if row is not None:
        saved = _read(row)
        if saved != value or row.payload_json != encoded:
            raise ResearchPolicyReceiptError("policy_receipt_content_conflict")
        return saved
    if session.get(RunRoleOutputRecord, value.receipt_id, populate_existing=True) is not None:
        raise ResearchPolicyReceiptError("policy_receipt_output_id_collision")
    session.add(RunRoleOutputRecord(
        output_id=value.receipt_id, run_id=value.run_id, role=role,
        schema_version=SCHEMA, payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        created_at=value.assessed_at,
    ))
    session.flush()
    return value
