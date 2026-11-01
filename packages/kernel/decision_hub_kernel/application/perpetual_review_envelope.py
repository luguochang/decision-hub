"""Typed V4 native review audit envelope, bounded by its existing transport.

This is not a Provider callback argument or a report body. Parsing grants no
origin or authorization: the native mapper and original reader still verify
the independent child, immutable references, packet and financial scope.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, TypedDict, cast

from .provider_input_receipts import MAX_BYTES, _strict_json
from .research_transport_json import MAX_TRANSPORT_JSON_BYTES


class FrozenReviewBindingV4(TypedDict):
    runId: str
    requestHash: str
    sessionId: str
    requestId: str
    generation: int


class FrozenReviewEnvelopeV4(TypedDict):
    schema_version: Literal["decision-hub-frozen-review-result.v4"]
    binding: FrozenReviewBindingV4
    freeze_call_id: str
    freeze_seq: int
    candidate_json: str
    candidate_sha256: str
    review_request_json: str
    review_request_sha256: str
    actual_prompt: str
    actual_prompt_sha256: str
    child_session_id: str
    child_parent_session_id: str
    child_events_json: str
    child_events_sha256: str
    original_output: dict[str, Any]


_FIELDS = frozenset(FrozenReviewEnvelopeV4.__annotations__)
_BINDING_FIELDS = frozenset(FrozenReviewBindingV4.__annotations__)
_TEXTS = (
    "schema_version",
    "freeze_call_id",
    "candidate_json",
    "candidate_sha256",
    "review_request_json",
    "review_request_sha256",
    "actual_prompt",
    "actual_prompt_sha256",
    "child_session_id",
    "child_parent_session_id",
    "child_events_json",
    "child_events_sha256",
)


def read_frozen_review_envelope_v4(text: str) -> FrozenReviewEnvelopeV4:
    """Keep complete raw bodies and strict JSON guards inside the 64 MiB carrier."""
    if not isinstance(text, str):
        raise ValueError("perpetual_native_review_envelope_invalid")
    if len(text.encode("utf-8")) > MAX_TRANSPORT_JSON_BYTES:
        raise ValueError("perpetual_native_review_envelope_bytes_exceeded")
    # Identical duplicate/nonfinite/UTF-8/depth64/node100k guards to existing
    # Provider JSON, with the already established whole native carrier budget.
    value = _strict_json(text, max_bytes=MAX_TRANSPORT_JSON_BYTES)
    if (
        not isinstance(value, dict)
        or set(value) != _FIELDS
        or value.get("schema_version") != "decision-hub-frozen-review-result.v4"
        or any(not isinstance(value.get(key), str) or not value[key] for key in _TEXTS)
        or type(value.get("freeze_seq")) is not int
        or value["freeze_seq"] < 0
        or not isinstance(value.get("original_output"), dict)
    ):
        raise ValueError("perpetual_native_review_envelope_invalid")
    binding = value["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding) != _BINDING_FIELDS
        or any(
            not isinstance(binding.get(key), str) or not binding[key]
            for key in ("runId", "requestHash", "sessionId", "requestId")
        )
        or re.fullmatch(r"[a-f0-9]{64}", binding["requestHash"]) is None
        or type(binding.get("generation")) is not int
        or binding["generation"] < 1
    ):
        raise ValueError("perpetual_native_review_envelope_binding_invalid")
    for field in ("candidate", "review_request", "actual_prompt", "child_events"):
        text_key = field if field == "actual_prompt" else field + "_json"
        digest_key = field + "_sha256"
        raw = value[text_key].encode("utf-8")
        # The producer/mapper's separate candidate, packet and prompt bounds
        # stay intact. Only the complete child audit uses the transport budget.
        if field != "child_events" and len(raw) > MAX_BYTES:
            raise ValueError("perpetual_native_review_envelope_subbody_bytes_exceeded")
        if (
            re.fullmatch(r"[a-f0-9]{64}", value[digest_key]) is None
            or hashlib.sha256(raw).hexdigest() != value[digest_key]
        ):
            raise ValueError("perpetual_native_review_envelope_digest_mismatch")
    return cast(FrozenReviewEnvelopeV4, value)
