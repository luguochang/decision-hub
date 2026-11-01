"""Strict wire shape for original-command receipts, including old results."""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import ReadCommandReceipt


def receipt() -> dict[str, Any]:
    return {
        "schema_version": "read-command-receipt.v1",
        "request_id": "original-command",
        "revision_id": "original-revision",
        "owner_id": "原用户",
        "command": {"request_id": "original-command", "read": True},
        "result": {
            "revision_id": "original-revision", "owner_id": "原用户",
            "read_at": "2026-10-03T04:00:00.123456Z",
            "updated_at": "2026-10-03T04:00:01.234567Z",
        },
        "payload_hash": "a" * 64,
        "confirmation_basis": "stored_command_hash",
    }


def test_original_result_wire_values_are_preserved_with_unicode_identity():
    value = receipt()
    parsed = ReadCommandReceipt.model_validate(value)
    assert parsed.model_dump(mode="json") == value
    assert parsed.result.read_at is not None
    assert parsed.result.read_at.microsecond == 123456


@pytest.mark.parametrize(
    "missing", ["schema_version", "confirmation_basis", "request_id", "result"]
)
def test_required_confirmation_fields_cannot_be_defaulted(missing: str):
    value = receipt()
    value.pop(missing)
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)


@pytest.mark.parametrize("bad", [1, "true", None])
def test_command_boolean_is_not_coerced(bad: Any):
    value = receipt()
    value["command"]["read"] = bad
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)


@pytest.mark.parametrize("bad", [1760000000, "2026-10-03T04:00:00", "2026-02-30T04:00:00Z"])
def test_original_result_requires_an_aware_real_wire_datetime(bad: Any):
    value = receipt()
    value["result"]["updated_at"] = bad
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)


@pytest.mark.parametrize("bad", ["a" * 63, "a" * 65, "A" * 64, "a" * 64 + "\n"])
def test_hash_is_exact_lowercase_sha256(bad: str):
    value = receipt()
    value["payload_hash"] = bad
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)


@pytest.mark.parametrize("location", ["root", "command", "result"])
def test_receipt_does_not_accept_undeclared_fields(location: str):
    value = receipt()
    target = value if location == "root" else value[location]
    target["unrecorded"] = "not original"
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)


def test_unicode_identifier_limits_count_characters():
    value = receipt()
    value["owner_id"] = value["result"]["owner_id"] = "😀" * 128
    assert ReadCommandReceipt.model_validate(value).owner_id == "😀" * 128
    too_long = deepcopy(value)
    too_long["owner_id"] += "😀"
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(too_long)


def test_native_aware_values_are_supported_without_accepting_naive_values():
    value = receipt()
    value["result"]["updated_at"] = datetime(2026, 10, 3, 4, tzinfo=UTC)
    offset = ReadCommandReceipt.model_validate(value).result.updated_at.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    value["result"]["updated_at"] = datetime(2026, 10, 3, 4)
    with pytest.raises(ValidationError):
        ReadCommandReceipt.model_validate(value)
