"""Pure declaration checks; no clock sampling, SQL provenance, or permission.

Integer UTC microseconds preserve the new sidecar's precision. An inherited
raw timestamp can have additional zero digits; nonzero discarded digits are
never silently truncated. Typed datetime values cannot attest earlier raw text.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

SELECTION_MAX_BYTES = 16_777_216
EXECUTION_LINK_MAX_BYTES = 16_384
MAX_CONSIDERED_INPUTS = 2050
_DAY_US = 86_400_000_000
_UTC_LIMIT_US = date(9999, 12, 31).toordinal() * _DAY_US
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})"
)


class ContextSelectionSemanticError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parts(value: object) -> tuple[int, int, str]:
    if not isinstance(value, str) or (match := _TIMESTAMP.fullmatch(value)) is None:
        raise ContextSelectionSemanticError("context_selection_invalid_timestamp")
    year, month, day, hour, minute, second = map(int, match.group(1, 2, 3, 4, 5, 6))
    try:
        ordinal = date(year, month, day).toordinal() - 1
    except ValueError as exc:
        raise ContextSelectionSemanticError("context_selection_invalid_timestamp") from exc
    if hour > 23 or minute > 59 or second > 59:
        raise ContextSelectionSemanticError("context_selection_invalid_timestamp")
    offset = match.group(8)
    offset_minutes = 0
    if offset != "Z":
        offset_hour, offset_minute = int(offset[1:3]), int(offset[4:6])
        if offset_hour > 23 or offset_minute > 59:
            raise ContextSelectionSemanticError("context_selection_invalid_timestamp")
        offset_minutes = (offset_hour * 60 + offset_minute) * (1 if offset[0] == "+" else -1)
    seconds = ordinal * 86_400 + hour * 3600 + minute * 60 + second - offset_minutes * 60
    fraction = match.group(7) or ""
    whole = seconds * 1_000_000 + int((fraction[:6] + "000000")[:6])
    if not 0 <= whole < _UTC_LIMIT_US:
        raise ContextSelectionSemanticError("context_selection_invalid_timestamp")
    return whole, len(fraction), fraction


def timestamp_shape_valid(value: object) -> bool:
    """Gregorian/RFC3339 shape, leaving inherited precision to the semantic gate."""
    try:
        _parts(value)
    except ContextSelectionSemanticError:
        return False
    return True


def parse_rfc3339_microseconds(value: object, *, inherited: bool = False) -> int:
    whole, precision, fraction = _parts(value)
    if precision > 6:
        if not inherited:
            raise ContextSelectionSemanticError("context_selection_invalid_timestamp")
        if any(character != "0" for character in fraction[6:]):
            raise ContextSelectionSemanticError("context_selection_time_precision_unrepresentable")
    return whole


def context_selection_time_issue_codes(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Cross-field declaration relations, without authenticating their source."""
    issues: list[str] = []

    def issue(code: str) -> None:
        if code not in issues:
            issues.append(code)

    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            issue("context_selection_non_finite_number")
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)

    def instant(raw: object, *, inherited: bool = False) -> int | None:
        try:
            return parse_rfc3339_microseconds(raw, inherited=inherited)
        except ContextSelectionSemanticError as exc:
            issue(exc.code)
            return None

    schema = value.get("schema_version")
    recorded = instant(value.get("recorded_at"))
    if schema == "research-context-selection.v1":
        pit = instant(value.get("original_pit_cutoff_at"))
        effective = instant(value.get("effective_eligibility_cutoff_at"))
        decision = instant(value.get("decision_cutoff_before_selection"))
        request = value.get("projected_request", {})
        raw_pit = instant(request.get("pit_cutoff_at"), inherited=True)
        if pit is not None and raw_pit is not None and pit != raw_pit:
            issue("context_selection_original_pit_mismatch")
        if value.get("execution_mode") == "live":
            clock = instant(value.get("live_selection_clock_at"))
            if pit is not None and clock is not None and effective != min(pit, clock):
                issue("context_selection_effective_cutoff_mismatch")
            if recorded is not None and clock is not None and recorded < clock:
                issue("context_selection_recorded_before_selection_clock")
        elif value.get("execution_mode") == "replay":
            if value.get("live_selection_clock_at") is not None:
                issue("context_selection_replay_clock_not_null")
            if pit is not None and effective != pit:
                issue("context_selection_effective_cutoff_mismatch")
        if value.get("decision_cutoff_basis") == "replay_original_pit" and decision != pit:
            issue("context_selection_decision_cutoff_mismatch")
        for witness in value.get("considered_inputs", []):
            instant(witness.get("input", {}).get("received_at"), inherited=True)
            row = witness.get("row")
            if row is not None:
                instant(row.get("received_at"), inherited=True)
        for evidence in request.get("input_evidence", []):
            received = instant(evidence.get("received_at"), inherited=True)
            if received is not None and effective is not None and received > effective:
                issue("context_selection_projected_received_after_cutoff")
        if any(
            request.get(key) != value.get(target)
            for key, target in (
                ("run_id", "run_id"),
                ("event_id", "event_id"),
                ("current_round", "graph_round"),
                ("execution_mode", "execution_mode"),
            )
        ) or value.get("task_anchor", {}).get("run_id") != value.get("run_id"):
            issue("context_selection_declared_identity_mismatch")
    elif schema == "research-context-selection-execution-link.v1":
        selection_time = instant(value.get("selection_row_identity", {}).get("recorded_at"))
        if recorded is not None and selection_time is not None and selection_time > recorded:
            issue("context_selection_link_before_selection")
        for key in ("execution_row_identity", "prompt_integrity_row_identity"):
            row = value.get(key, {})
            row_time = instant(row.get("recorded_at"))
            if recorded is not None and row_time is not None and row_time != recorded:
                issue("context_selection_execution_time_mismatch")
            if row.get("run_id") != value.get("run_id"):
                issue("context_selection_declared_identity_mismatch")
        if value.get("selection_row_identity", {}).get("run_id") != value.get("run_id"):
            issue("context_selection_declared_identity_mismatch")
        if value.get("turn_kind") == "research_round" and (
            value.get("selection_full_request_hash") != value.get("actual_execution_full_hash")
        ):
            issue("context_selection_normal_full_hash_mismatch")
    return tuple(issues)


def utf8_payload_size(serialized: str, *, limit: int | None = None) -> int:
    """Count this exact JSON text, rejecting surrogate replacement or truncation."""
    size = 0
    try:
        for start in range(0, len(serialized), 16_384):
            size += len(serialized[start : start + 16_384].encode("utf-8", errors="strict"))
            if limit is not None and size > limit:
                raise ContextSelectionSemanticError("context_selection_payload_too_large")
    except UnicodeEncodeError as exc:
        raise ContextSelectionSemanticError("context_selection_invalid_unicode") from exc
    return size


def canonical_payload_size(value: object, *, limit: int) -> int:
    """Incremental canonical encoding; cap is no bound on the input object's peak."""
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    size = 0
    try:
        for fragment in encoder.iterencode(value):
            size += utf8_payload_size(fragment)
            if size > limit:
                raise ContextSelectionSemanticError("context_selection_payload_too_large")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContextSelectionSemanticError):
            raise
        raise ContextSelectionSemanticError("context_selection_payload_not_json") from exc
    return size
