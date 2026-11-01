"""Read declared numeric event actuals from successful poll/capture originals.

The frozen rule is reviewed parsing data. It cannot declare a source verified,
invent actuals from an agenda, or turn another event family into CPI metrics.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioRealizationV1,
)

from ..decision.action_comparison import _number, _utc, content_hash
from ..persistence.action_quote_repository import RawArchive, _capture
from ..persistence.db import RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.exact_decimal_text import number
from ..ports.market_path import aware_utc
from ..ports.sources import SourceContentSnapshot

HOSTS = {
    "cpi": {"api.bls.gov", "www.bls.gov", "bls.gov"},
    "nfp": {"api.bls.gov", "www.bls.gov", "bls.gov"},
    "employment": {"api.bls.gov", "www.bls.gov", "bls.gov"},
    "pce": {"apps.bea.gov", "www.bea.gov", "bea.gov"},
    "fomc": {"www.federalreserve.gov", "federalreserve.gov"},
}


def _wire(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("event_actual_duplicate_json_key")
        result[key] = value
    return result


def _path(body, path):
    if not isinstance(path, list) or not 1 <= len(path) <= 16:
        raise ValueError("event_actual_path_invalid")
    value = body
    for key in path:
        if not isinstance(key, str) or not isinstance(value, dict) or key not in value:
            raise ValueError("event_actual_declared_field_unavailable")
        value = value[key]
    return value


def _rule(plan, rule):
    event = plan["event"]
    if (
        rule.get("schema_version") != "event-scenario-observation-rule.v1"
        or rule.get("adapter") != "captured_numeric_json.v1"
    ):
        raise ValueError("event_actual_adapter_unsupported")
    if any(rule.get(key) != event[key] for key in ("event_key", "family", "stage_id")):
        raise ValueError("event_actual_rule_event_identity")
    url = urlsplit(rule["source_url"])
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.fragment
        or url.port not in {None, 443}
        or url.hostname not in HOSTS.get(event["family"], set())
    ):
        raise ValueError("event_actual_official_source_required")
    metrics = rule.get("metrics")
    if not isinstance(metrics, list) or not 1 <= len(metrics) <= 32:
        raise ValueError("event_actual_metric_rule_invalid")
    identities = [
        tuple(row[key] for key in ("metric_id", "unit", "reference_period", "stage_id"))
        for row in metrics
    ]
    if len(set(identities)) != len(identities) or any(
        row["stage_id"] != event["stage_id"] for row in metrics
    ):
        raise ValueError("event_actual_metric_rule_identity")


def source_original(plan, capture, attempt):
    return dict(
        observation_rule_ref=plan["event"]["observation_rule_ref"], capture=capture, attempt=attempt
    )


def parse_captured_event_scenario_realization(plan, rule, capture, attempt, raw, *, cutoff_at):
    """Pure parse after caller supplies real captured bytes and native metadata."""
    _rule(plan, rule)
    cutoff = _utc(cutoff_at) if isinstance(cutoff_at, str) else aware_utc(cutoff_at)
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = SourceContentSnapshot.model_validate_json(attempt["content_snapshot_json"] or "{}")
    if (
        attempt["source_id"] != rule["source_id"]
        or attempt["status"] != "succeeded"
        or attempt["received_count"] < 1
        or capture["context_id"] != "source:" + attempt["attempt_id"]
        or capture["url"] != rule["source_url"]
        or capture["adapter_version"] != rule["adapter_version"]
        or capture["status_code"] != 200
        or capture["error_code"] is not None
        or capture["byte_count"] != len(raw)
        or capture["body_hash"] != digest
        or capture["body_ref"] != "raw://sha256/" + digest
        or snapshot.http_status != 200
        or snapshot.content_hash != digest
        or snapshot.parser_version != rule["adapter_version"]
        or snapshot.parse_status != "valid"
        or snapshot.parsed_count is None
        or snapshot.parsed_count < len(rule["metrics"])
    ):
        raise ValueError("event_actual_native_source_binding_invalid")
    lower, start, received, recorded, known = (
        _utc(value)
        for value in (
            attempt["started_at"],
            capture["started_at"],
            capture["completed_at"],
            capture["recorded_at"],
            attempt["completed_at"],
        )
    )
    if not lower <= start <= received <= recorded <= known <= cutoff:
        raise ValueError("event_actual_native_pit_invalid")
    body = json.loads(
        raw,
        object_pairs_hook=_unique,
        parse_float=Decimal,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("event_actual_nonfinite")),
    )
    event = plan["event"]
    if any(
        _path(body, rule[key + "_path"]) != event[key]
        for key in ("event_key", "family", "stage_id")
    ):
        raise ValueError("event_actual_payload_event_identity")
    release_text, public_text = (
        _path(body, rule[key + "_path"]) for key in ("released_at", "public_at")
    )
    if not _utc(release_text) <= _utc(public_text) <= received:
        raise ValueError("event_actual_public_clock_invalid")
    metrics = []
    for row in rule["metrics"]:
        if (
            _path(body, row["unit_path"]) != row["unit"]
            or _path(body, row["reference_period_path"]) != row["reference_period"]
        ):
            raise ValueError("event_actual_metric_units_or_period_mismatch")
        value = _path(body, row["value_path"])
        if type(value) is int:
            value = str(value)
        elif isinstance(value, Decimal):
            value = number(value)
        value = number(_number(value))
        metrics.append(
            {
                **{key: row[key] for key in ("metric_id", "unit", "reference_period", "stage_id")},
                "value": value,
            }
        )
    original = source_original(plan, capture, attempt)
    reference = dict(
        ref="event-realization-source:" + content_hash(original), hash=content_hash(original)
    )
    result = dict(
        schema_version="event-scenario-realization.v1",
        event=event,
        released_at=release_text,
        public_at=public_text,
        received_at=capture["completed_at"],
        known_at=attempt["completed_at"],
        metrics=metrics,
        source_ref=reference,
    )
    EventScenarioRealizationV1.model_validate(result)
    return result


def read_event_scenario_actual_inputs_in_session(
    session: Session, *, plan: dict[str, Any], originals: dict[str, Any],
    cutoff_at: datetime, archive: RawArchive,
) -> dict[str, Any] | None:
    """Dispatch only an exact reviewed adapter, authenticating its native originals."""
    reference = plan["event"]["observation_rule_ref"]
    rule = originals.get(reference["ref"])
    if rule is None or content_hash(rule) != reference["hash"]:
        raise ValueError("event_actual_observation_rule_original_missing")
    if rule.get("adapter") == "bea_pce_release_selector.v1":
        from .event_scenario_pce_prospective import read_pce_prospective_actual_inputs_in_session

        return read_pce_prospective_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    if rule.get("adapter") == "bea_pce_release_document.v1":
        from .event_scenario_pce_realization import read_pce_event_scenario_actual_inputs_in_session

        return read_pce_event_scenario_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    if rule.get("adapter") == "bls_employment_release_selector.v1":
        from .event_scenario_employment_prospective import (
            read_employment_prospective_actual_inputs_in_session,
        )

        return read_employment_prospective_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    if rule.get("adapter") == "bls_employment_release_document.v1":
        from .event_scenario_employment_realization import (
            read_employment_event_scenario_actual_inputs_in_session,
        )

        return read_employment_event_scenario_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    if rule.get("adapter") == "fed_fomc_statement_selector.v1":
        from .event_scenario_fomc_realization import (
            read_fomc_event_scenario_actual_inputs_in_session,
        )

        return read_fomc_event_scenario_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    if rule.get("adapter") == "bls_cpi_release_document.v1":
        from .event_scenario_cpi_realization import read_cpi_event_scenario_actual_inputs_in_session

        return read_cpi_event_scenario_actual_inputs_in_session(
            session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
        )
    _rule(plan, rule)
    cutoff = aware_utc(cutoff_at)
    attempt = session.scalar(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == rule["source_id"],
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(
            SourcePollAttemptRecord.completed_at.desc(), SourcePollAttemptRecord.attempt_id.desc()
        )
        .limit(1)
    )
    if attempt is None:
        return None
    rows = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + attempt.attempt_id,
            RawCaptureRecord.url == rule["source_url"],
            RawCaptureRecord.adapter_version == rule["adapter_version"],
        )
    ).all()
    if len(rows) != 1:
        raise ValueError("event_actual_exact_capture_required")
    capture = _capture(rows[0]).payload()
    poll = {
        key: getattr(attempt, key)
        for key in ("attempt_id", "source_id", "status", "received_count", "content_snapshot_json")
    }
    poll.update(
        started_at=_wire(as_utc(attempt.started_at)),
        completed_at=_wire(as_utc(attempt.completed_at)),
    )
    raw = archive.read(capture["body_ref"])
    realization = parse_captured_event_scenario_realization(
        plan, rule, capture, poll, raw, cutoff_at=cutoff
    )
    return dict(realization=realization, source_evidence=source_original(plan, capture, poll))


def read_event_scenario_realization_in_session(
    session: Session, *, plan: dict[str, Any], originals: dict[str, Any],
    cutoff_at: datetime, archive: RawArchive,
) -> dict[str, Any] | None:
    value = read_event_scenario_actual_inputs_in_session(
        session, plan=plan, originals=originals, cutoff_at=cutoff_at, archive=archive
    )
    return None if value is None else value["realization"]
