"""Offline captured-source parser arithmetic; no live source or review authority."""

import hashlib
import importlib
import json

import pytest


def fixture():
    event = dict(
        event_key="offline-pce-2026-01",
        family="pce",
        stage_id="release",
        scheduled_at="2026-01-01T00:01:00Z",
        schedule_ref={"ref": "offline:schedule", "hash": "1" * 64},
        event_definition_ref={"ref": "offline:event", "hash": "2" * 64},
        observation_rule_ref={"ref": "offline:rule", "hash": "3" * 64},
        partition_ref={"ref": "offline:partition", "hash": "4" * 64},
    )
    metric = dict(
        metric_id="core_pce_mom", unit="percent", reference_period="2025-12", stage_id="release"
    )
    rule = dict(
        schema_version="event-scenario-observation-rule.v1",
        adapter="captured_numeric_json.v1",
        source_id="offline-bea-pce",
        adapter_version="offline-bea-release.v1",
        source_url="https://www.bea.gov/offline-controlled-fixture.json",
        event_key=event["event_key"],
        family="pce",
        stage_id="release",
        event_key_path=["event_key"],
        family_path=["family"],
        stage_id_path=["stage_id"],
        released_at_path=["released_at"],
        public_at_path=["public_at"],
        metrics=[
            {
                **metric,
                "value_path": ["value"],
                "unit_path": ["unit"],
                "reference_period_path": ["reference_period"],
            }
        ],
    )
    body = {
        **metric,
        "event_key": event["event_key"],
        "family": "pce",
        "released_at": event["scheduled_at"],
        "public_at": event["scheduled_at"],
        "value": "0.20",
    }
    raw = json.dumps(body).encode()
    digest = hashlib.sha256(raw).hexdigest()
    capture = dict(
        capture_id="offline-capture",
        context_id="source:offline-attempt",
        url=rule["source_url"],
        adapter_version=rule["adapter_version"],
        status_code=200,
        error_code=None,
        byte_count=len(raw),
        body_hash=digest,
        body_ref="raw://sha256/" + digest,
        started_at="2026-01-01T00:01:00Z",
        completed_at="2026-01-01T00:01:01Z",
        recorded_at="2026-01-01T00:01:01Z",
    )
    attempt = dict(
        attempt_id="offline-attempt",
        source_id=rule["source_id"],
        status="succeeded",
        started_at="2026-01-01T00:01:00Z",
        completed_at="2026-01-01T00:01:02Z",
        received_count=1,
        content_snapshot_json=json.dumps(
            dict(
                http_status=200,
                parser_version=rule["adapter_version"],
                content_hash=digest,
                parse_status="valid",
                parsed_count=1,
            )
        ),
    )
    return {"event": event}, rule, capture, attempt, raw


def parse(*args):
    module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.event_scenario_realization"
    )
    return module.parse_captured_event_scenario_realization(*args, cutoff_at="2026-01-01T00:02:00Z")


def test_captured_actual_preserves_family_metric_units_period_and_first_known_clock():
    plan, rule, capture, attempt, raw = fixture()
    value = parse(plan, rule, capture, attempt, raw)
    assert value["event"] == plan["event"]
    assert value["metrics"] == [
        dict(
            metric_id="core_pce_mom",
            unit="percent",
            reference_period="2025-12",
            stage_id="release",
            value="0.2",
        )
    ]
    assert value["known_at"] == "2026-01-01T00:01:02Z"


@pytest.mark.parametrize(
    "mutation", ["source_host", "capture_hash", "future_known", "wrong_period", "wrong_unit"]
)
def test_declared_numeric_source_rejects_changed_identity_or_unknown_future(mutation):
    plan, rule, capture, attempt, raw = fixture()
    if mutation == "source_host":
        rule["source_url"] = capture["url"] = "https://untrusted.invalid/release"
    elif mutation == "capture_hash":
        capture["body_hash"] = "0" * 64
    elif mutation == "future_known":
        attempt["completed_at"] = "2026-01-01T00:02:01Z"
    else:
        edited = json.loads(raw)
        edited["reference_period" if mutation == "wrong_period" else "unit"] = "other"
        raw = json.dumps(edited).encode()
        digest = hashlib.sha256(raw).hexdigest()
        capture.update(byte_count=len(raw), body_hash=digest, body_ref="raw://sha256/" + digest)
        snapshot = json.loads(attempt["content_snapshot_json"])
        snapshot["content_hash"] = digest
        attempt["content_snapshot_json"] = json.dumps(snapshot)
    with pytest.raises(ValueError):
        parse(plan, rule, capture, attempt, raw)
