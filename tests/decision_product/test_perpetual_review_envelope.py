"""The actual BTC16 approved audit envelope is not one Provider argument block."""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "artifacts/d2-btc16-review-envelope/inputs/native-original.json"
INPUT_SHA = "3359b4f75497c82b276c5177e521e5cacd5a78044972da89675d638c4ed5d547"


def native_original():
    from packages.kernel.decision_hub_kernel.ports.research_observation import (
        ResearchPerpetualSynthesisObservation,
    )

    raw = INPUT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == INPUT_SHA
    return ResearchPerpetualSynthesisObservation.model_validate_json(raw)


def test_actual_approved_btc16_review_originals_pass_with_complete_child_audit():
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _review_originals,
    )

    native = native_original()
    assert len(native.review_wrapper_json.encode()) == 2_592_456
    before = hashlib.sha256(native.review_wrapper_json.encode()).hexdigest()
    originals = _review_originals(native)
    assert len(originals.synthesis_json.encode()) == 24_613
    assert len(originals.review_request_json.encode()) == 26_571
    assert json.loads(originals.authorization_json)["verdict"] == "approved"
    assert json.loads(originals.review_originals_json)["run_id"] == (
        "run_a9e70b8ddad248798d8bb333159dd69b"
    )
    wrapper = json.loads(native.review_wrapper_json)
    assert len(wrapper["child_events_json"].encode()) == 1_523_024
    assert len(json.loads(wrapper["child_events_json"])) == 2_256
    assert hashlib.sha256(native.review_wrapper_json.encode()).hexdigest() == before


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('{"x":1,"x":2}', "duplicate_json_key"),
        ('{"x":NaN}', "nonfinite_json"),
        ("[" * 65 + "0" + "]" * 65, "json_complexity"),
        (json.dumps([0] * 100_001), "json_complexity"),
        ('{"schema_version":"decision-hub-frozen-review-result.v3"}', "envelope_invalid"),
    ],
    ids=("duplicate", "nonfinite", "depth", "nodes", "wrong_version"),
)
def test_review_envelope_retains_strict_structural_rejections(text, reason):
    from packages.kernel.decision_hub_kernel.application.perpetual_review_envelope import (
        read_frozen_review_envelope_v4,
    )

    with pytest.raises(ValueError, match=reason):
        read_frozen_review_envelope_v4(text)


def test_review_envelope_preserves_child_hash_and_generic_callback_budgets():
    from packages.kernel.decision_hub_kernel.application.perpetual_review_envelope import (
        read_frozen_review_envelope_v4,
    )
    from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
        strict_input_json,
        strict_json,
    )

    native = native_original()
    with pytest.raises(ValueError, match="research_provider_bytes_exceeded"):
        strict_json(native.review_wrapper_json)
    assert strict_input_json('"' + "x" * 1_000_001 + '"') == "x" * 1_000_001
    with pytest.raises(ValueError, match="research_provider_bytes_exceeded"):
        strict_input_json('"' + "x" * (8 * 1024 * 1024) + '"')
    body = json.loads(native.review_wrapper_json)
    body["child_events_json"] += " "
    with pytest.raises(ValueError, match="envelope_digest_mismatch"):
        read_frozen_review_envelope_v4(json.dumps(body))


def test_review_envelope_rejects_oversized_child_and_candidate_without_truncation():
    from packages.kernel.decision_hub_kernel.application.perpetual_review_envelope import (
        read_frozen_review_envelope_v4,
    )

    with pytest.raises(ValueError, match="envelope_bytes_exceeded"):
        read_frozen_review_envelope_v4('"' + "x" * (64 * 1024 * 1024) + '"')
    body = json.loads(native_original().review_wrapper_json)
    body["candidate_json"] = '"' + "x" * 1_000_001 + '"'
    body["candidate_sha256"] = hashlib.sha256(body["candidate_json"].encode()).hexdigest()
    with pytest.raises(ValueError, match="envelope_subbody_bytes_exceeded"):
        read_frozen_review_envelope_v4(json.dumps(body))
