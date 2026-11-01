"""Whole native transcript limits do not relax individual callback limits."""

import json

import pytest

from packages.kernel.decision_hub_kernel.application import research_transport_json as transport
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import strict_json
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    MAX_OBSERVATION_BYTES,
)


def test_complete_transcript_above_callback_limit_uses_observation_limit():
    text = json.dumps([{"payload": "x" * 1_100_000}])
    assert transport.MAX_TRANSPORT_JSON_BYTES == MAX_OBSERVATION_BYTES
    assert transport.read_transport_events_json(text) == [{"payload": "x" * 1_100_000}]
    with pytest.raises(ValueError, match="research_provider_bytes_exceeded"):
        strict_json(text)


@pytest.mark.parametrize(
    "text,code",
    [
        ('[{"seq":1,"seq":2}]', "duplicate_json_key"),
        ('[{"x":NaN}]', "nonfinite_json"),
        ('[{"x":Infinity}]', "nonfinite_json"),
        ('[{"x":1e999}]', "nonfinite_json"),
        ('[{"x":"\\ud800"}]', "json_invalid"),
        ("[", "json_invalid"),
        ("{}", "events_invalid"),
        ("[null]", "events_invalid"),
        ('[{"x":' + "[" * 65 + "0" + "]" * 65 + "}]", "json_complexity_exceeded"),
        (json.dumps([{"x": [0] * 100_000}]), "json_complexity_exceeded"),
    ],
    ids=[
        "duplicate",
        "nan",
        "infinity",
        "overflow",
        "utf8",
        "syntax",
        "object",
        "null",
        "depth",
        "count",
    ],
)
def test_transport_keeps_strict_json_controls(text, code):
    with pytest.raises(ValueError, match="research_transport_" + code):
        transport.read_transport_events_json(text)


def test_transport_still_has_a_finite_byte_limit(monkeypatch):
    monkeypatch.setattr(transport, "MAX_TRANSPORT_JSON_BYTES", 32)
    with pytest.raises(ValueError, match="research_transport_bytes_exceeded"):
        transport.read_transport_events_json(json.dumps([{"payload": "x" * 33}]))
