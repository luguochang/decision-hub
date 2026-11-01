"""Strict Selection reference codec. Declarations confer no original/lease authority."""

from __future__ import annotations

import json

from .research_context_selection import ContextSelectionReference as LegacyReference
from .research_context_selection_v4 import ContextSelectionReference as QualityReference

type ContextSelectionReference = LegacyReference | QualityReference


def parse_context_selection_reference(payload_json: str) -> ContextSelectionReference:
    body = json.loads(payload_json)
    if not isinstance(body, dict):
        raise ValueError("research_context_reference_invalid")
    if body.get("schema_version") == "research-context-selection.v4":
        return QualityReference.model_validate_json(payload_json)
    return LegacyReference.model_validate_json(payload_json)
