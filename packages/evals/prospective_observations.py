"""Bridge frozen live outlooks to auditable observational evaluation inputs.

Selection never looks at labels. This is not a preregistered candidate comparison:
it cannot grant promotion, turn neutral views into directions, or replay an LLM
after the outcome and call that prospective execution.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import AwareDatetime, BaseModel
from sqlalchemy.orm import defer

from packages.kernel.decision_hub_kernel.application.fact_lineage import digest
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord,
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    FactVersionRecord,
    PublicationDecisionRecord,
    RawCaptureRecord,
    RunInputRefRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

SELECTION_VERSION = "first-record-study-method-family-version-horizon.v2"


class ProspectiveInputSummary(BaseModel):
    observed_at: AwareDatetime
    selection_version: str = SELECTION_VERSION
    outlooks: int
    eligible: int
    selected: int
    repeated: int
    excluded: int
    distinct_studies: int
    recorded_completed: int
    completed: int
    pending: int
    retry: int
    unknown: int
    directional_completed: int
    neutral_completed: int
    next_due_at: AwareDatetime | None
    first_records: int = 0
    first_record_excluded: int = 0
    repeated_ineligible: int = 0


def _selection_now(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("prospective_selection_clock_timezone_required")
    return as_utc(now)


def _selection_key(
    outlook: AnalyticalOutlookRecord,
) -> tuple[str, str, str, str, str, str] | None:
    if not outlook.study_id:
        return None
    return (
        outlook.market, outlook.instrument, outlook.method_family, outlook.method_version,
        outlook.horizon, outlook.study_id,
    )


def first_record_outlooks(
    outlooks: Iterable[AnalyticalOutlookRecord], *, now: datetime,
) -> tuple[AnalyticalOutlookRecord, ...]:
    """Fix sample positions before eligibility, actions, statuses or labels.

    Callers must supply the complete original candidate set at the cutoff,
    before applying presentation filters. This selects persisted rows only;
    it does not authenticate original inputs or establish a stable recipe.
    """
    current = _selection_now(now)
    available = sorted(
        (row for row in outlooks
         if row.cohort == "prospective" and as_utc(row.recorded_at) <= current),
        key=lambda row: (as_utc(row.recorded_at), row.outlook_id),
    )
    seen: set[tuple[str, str, str, str, str, str]] = set()
    first: list[AnalyticalOutlookRecord] = []
    for row in available:
        key = _selection_key(row)
        if key is not None and key not in seen:
            seen.add(key)
            first.append(row)
    return tuple(first)


def _input_problem(outlook, now):
    if outlook.eligibility != "eligible":
        return "outlook_excluded"
    if not outlook.study_id:
        return "study_identity_missing"
    if (
        as_utc(outlook.recorded_at) > as_utc(outlook.window_start_at)
        or outlook.data_as_of is None
        or as_utc(outlook.data_as_of) > as_utc(outlook.window_start_at)
        or as_utc(outlook.window_start_at) >= as_utc(outlook.window_end_at)
        or as_utc(outlook.window_start_at) > now
    ):
        return "outlook_not_frozen_before_window"
    if outlook.action not in {"long", "short", "neutral"}:
        return "outlook_action_not_evaluable"
    return None


def select_inputs(session, now):
    now = _selection_now(now)
    rows = session.query(AnalyticalOutlookRecord, AnalyticalObservationRecord).outerjoin(
        AnalyticalObservationRecord,
        AnalyticalObservationRecord.outlook_id == AnalyticalOutlookRecord.outlook_id,
    ).options(
        defer(AnalyticalOutlookRecord.raw_payload_json),
        defer(AnalyticalOutlookRecord.evidence_refs_json),
        defer(AnalyticalOutlookRecord.fact_version_refs_json),
        defer(AnalyticalOutlookRecord.trigger),
        defer(AnalyticalOutlookRecord.invalidation),
        defer(AnalyticalObservationRecord.window_json),
    ).filter(
        AnalyticalOutlookRecord.cohort == "prospective",
        AnalyticalOutlookRecord.recorded_at <= now,
    ).order_by(AnalyticalOutlookRecord.recorded_at, AnalyticalOutlookRecord.outlook_id).all()
    first = first_record_outlooks((outlook for outlook, _ in rows), now=now)
    first_ids = {_selection_key(outlook): outlook.outlook_id for outlook in first}
    selections, rejected = [], []
    eligible, first_excluded, repeated_ineligible = 0, 0, 0
    for outlook, observation in rows:
        problem = _input_problem(outlook, now)
        eligible += problem is None
        key = _selection_key(outlook)
        first_id = first_ids.get(key) if key is not None else None
        reason = problem
        if first_id is not None and first_id != outlook.outlook_id:
            reason = "repeat_study_method_horizon"
            repeated_ineligible += problem is not None
        elif first_id is not None and problem is not None:
            first_excluded += 1
        if reason:
            rejected.append({"outlook_id": outlook.outlook_id, "reason": reason,
                             "first_outlook_id": first_id, "input_problem": problem})
        else:
            selections.append((outlook, observation))
    counts = Counter(reason["reason"] for reason in rejected)
    states = Counter(o.status if o else "unknown" for _, o in selections)
    completed = [(o, r) for o, r in selections if r and r.status == "completed"]
    due = [as_utc(o.window_end_at) for o, r in selections
           if r and r.status in {"pending", "running", "retry"}]
    summary = ProspectiveInputSummary(
        observed_at=now, outlooks=len(rows), eligible=eligible, selected=len(selections),
        repeated=counts["repeat_study_method_horizon"],
        excluded=len(rejected) - counts["repeat_study_method_horizon"],
        distinct_studies=len({o.study_id for o, _ in selections}),
        recorded_completed=sum(r is not None and r.status == "completed" for _, r in rows),
        completed=states["completed"], pending=states["pending"] + states["running"],
        retry=states["retry"], unknown=sum(v for k, v in states.items()
                                         if k not in {"completed", "pending", "running", "retry"}),
        directional_completed=sum(o.action in {"long", "short"} for o, _ in completed),
        neutral_completed=sum(o.action == "neutral" for o, _ in completed),
        next_due_at=min(due, default=None),
        first_records=len(first), first_record_excluded=first_excluded,
        repeated_ineligible=repeated_ineligible,
    )
    return summary, selections, rejected


def prospective_summary(database, now):
    with database.session() as session:
        return select_inputs(session, now)[0]


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _frozen_input(session, outlook):
    publication = session.get(PublicationDecisionRecord, outlook.artifact_id)
    _require(publication and publication.run_id == outlook.run_id
             and publication.result_hash == outlook.result_hash, "publication_lineage_mismatch")
    version_ids = json.loads(outlook.fact_version_refs_json)
    _require(bool(version_ids), "versioned_inputs_missing")
    versions = []
    boundary = as_utc(outlook.window_start_at)
    for identity in version_ids:
        version = session.get(FactVersionRecord, identity)
        links = session.query(RunInputRefRecord).filter_by(
            run_id=outlook.run_id, fact_version_id=identity,
        ).all()
        _require(version and links, "fact_input_lineage_missing")
        _require(as_utc(version.known_at) <= boundary and as_utc(version.observed_at) <= boundary
                 and all(as_utc(ref.adopted_at) <= boundary
                         and as_utc(ref.as_of) <= boundary for ref in links),
                 "fact_input_after_freeze")
        versions.append({"fact_version_id": identity, "payload": json.loads(version.payload_json),
                         "known_at": as_utc(version.known_at).isoformat(),
                         "parsed_version_id": version.parsed_version_id})
    return {
        **{key: getattr(outlook, key) for key in (
            "outlook_id", "run_id", "study_id", "artifact_id", "market", "instrument",
            "method_family", "method_version", "horizon", "action", "subjective_probability",
            "probability_status", "result_hash", "research_quality",
        )},
        **{key: as_utc(getattr(outlook, key)).isoformat() for key in (
            "data_as_of", "recorded_at", "window_start_at", "window_end_at",
        )},
        "payload": json.loads(outlook.raw_payload_json), "facts": versions,
        "evidence_refs": json.loads(outlook.evidence_refs_json),
    }


def verify_recorded_label(session, outlook, observation, archive, now):
    _require(observation and observation.status == "completed", "observation_not_completed")
    end = as_utc(outlook.window_end_at)
    _require(end <= now and observation.evaluated_at
             and end <= as_utc(observation.evaluated_at) <= now, "label_timestamp_invalid")
    _require(observation.benchmark == "close_1m", "unsupported_benchmark")
    attempt = session.get(AnalyticalObservationAttemptRecord,
                          (outlook.outlook_id, observation.attempt))
    _require(attempt and attempt.status == "completed", "completed_attempt_missing")
    context = f"analytical-observation:{outlook.outlook_id}:{observation.attempt}"
    window = json.loads(observation.window_json)
    market_return, proofs = verify_archived_window(
        session, context_id=context, instrument=outlook.instrument,
        start_at=as_utc(outlook.window_start_at), end_at=end, window=window,
        evaluated_at=as_utc(observation.evaluated_at), now=now, archive=archive,
    )
    directional = market_return if outlook.action == "long" else (
        -market_return if outlook.action == "short" else None)
    _require(math.isclose(market_return, observation.market_return_pct, abs_tol=1e-10),
             "market_return_mismatch")
    _require(observation.directional_return_pct == directional
             and observation.direction_correct == (directional > 0 if directional is not None
                                                   else None), "direction_label_mismatch")
    _require(observation.quality_status == "estimated", "close_1m_quality_mismatch")
    return {"evaluated_at": as_utc(observation.evaluated_at).isoformat(),
            "market_return_pct": market_return, "directional_return_pct": directional,
            "direction_correct": directional > 0 if directional is not None else None,
            "quality_status": "estimated", "benchmark": "close_1m", "captures": proofs}


def verify_archived_candles(session, *, context_id, instrument, start_at, end_at,
                            evaluated_at, now, archive):
    """Reconstruct confirmed closes from HTTP receipts, without inventing quote times."""
    _require(start_at < end_at <= evaluated_at <= now, "label_timestamp_invalid")
    captures = session.query(RawCaptureRecord).filter_by(context_id=context_id).all()
    prices, proofs = [], []
    for side, boundary in (("entry", start_at), ("exit", end_at)):
        matches = [c for c in captures if parse_qs(urlsplit(c.url).query).get("after")
                   == [str(int(boundary.timestamp() * 1000))]]
        _require(len(matches) == 1, "candle_capture_ambiguous")
        capture = matches[0]
        url = urlsplit(capture.url)
        query = parse_qs(url.query)
        _require(url.hostname == "www.okx.com" and url.path == "/api/v5/market/history-candles"
                 and query.get("instId") == [instrument] and query.get("bar") == ["1m"],
                 "unsupported_candle_source")
        _require(capture.status_code == 200 and capture.body_ref, "capture_missing_or_failed")
        body = archive.read(capture.body_ref)
        _require(hashlib.sha256(body).hexdigest() == capture.body_hash, "archive_hash_mismatch")
        payload = json.loads(body)
        _require(str(payload.get("code")) == "0" and len(payload.get("data", [])) == 1,
                 "candle_response_invalid")
        candle = payload["data"][0]
        _require(len(candle) == 9 and str(candle[8]) == "1", "candle_not_closed")
        stamp = (datetime.fromtimestamp(int(candle[0]) / 1000, boundary.tzinfo)
                 + timedelta(minutes=1))
        price = float(candle[4])
        _require(math.isfinite(price) and price > 0 and boundary <= stamp
                 < boundary + timedelta(minutes=1), "candle_time_or_price_invalid")
        _require(max(stamp, end_at) <= as_utc(capture.completed_at) <= evaluated_at,
                 "saved_window_mismatch")
        prices.append(price)
        proofs.append({"side": side, "capture_id": capture.capture_id,
                       "body_ref": capture.body_ref, "body_hash": capture.body_hash,
                       "source_url": capture.url, "body_text": body.decode("utf-8"),
                       "price": price, "observed_at": stamp.isoformat()})
    market_return = (prices[1] / prices[0] - 1) * 100
    return market_return, proofs


def verify_archived_window(session, *, context_id, instrument, start_at, end_at, window,
                           evaluated_at, now, archive):
    """Verify saved quote timestamps as well as the underlying HTTP receipts."""
    market_return, proofs = verify_archived_candles(
        session, context_id=context_id, instrument=instrument, start_at=start_at,
        end_at=end_at, evaluated_at=evaluated_at, now=now, archive=archive,
    )
    for proof in proofs:
        quote = window[proof["side"]]
        observed = datetime.fromisoformat(quote["observed_at"].replace("Z", "+00:00"))
        received = datetime.fromisoformat(quote["received_at"].replace("Z", "+00:00"))
        _require(datetime.fromisoformat(proof["observed_at"]) == observed
                 and observed <= received <= evaluated_at
                 and proof["price"] == quote["last"]
                 and quote["instrument"] == instrument, "saved_window_mismatch")
    return market_return, proofs


def _validation_code(exc):
    message = str(exc)
    return message if isinstance(exc, ValueError) and (
        message.replace("_", "").isalnum() and len(message) < 80
    ) else "archive_or_input_invalid"


def _audit_case(session, outlook, observation, archive, now, *, include_bodies=False):
    from packages.evals.input_receipts import verify_input_receipts

    case = {"outlook_id": outlook.outlook_id, "input": None, "label": None,
            "input_receipts": [], "input_status": "invalid", "input_error": None,
            "label_status": "pending", "label_error": None, "status": "pending",
            "error_code": None}
    try:
        frozen = _frozen_input(session, outlook)
        case["input"] = frozen
        data = {**frozen, "cutoff_at": frozen["recorded_at"],
                "facts": sorted(frozen["facts"], key=lambda f: f["fact_version_id"])}
        case["input_receipts"] = verify_input_receipts(session, data, archive)
        if include_bodies:
            for receipt in case["input_receipts"]:
                body = archive.read(receipt["body_ref"])
                _require(hashlib.sha256(body).hexdigest() == receipt["body_hash"],
                         "archive_hash_mismatch")
                receipt["body_base64"] = base64.b64encode(body).decode("ascii")
        case["input_status"] = "verified"
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        case["input_error"] = _validation_code(exc)
        case["input_receipts"] = []
    if observation and observation.status == "completed":
        try:
            case["label"] = verify_recorded_label(session, outlook, observation, archive, now)
            case["label_status"] = "verified"
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            case["label_status"], case["label_error"] = "invalid", _validation_code(exc)
    else:
        case["label_status"] = observation.status if observation else "missing_observation"
    if case["input_status"] != "verified" or case["label_status"] == "invalid":
        case["status"] = "invalid"
        case["error_code"] = case["input_error"] or case["label_error"]
    else:
        case["status"] = case["label_status"]
    return case


def _audit_counts(cases):
    usable = [c for c in cases if c["status"] == "verified"]
    return {
        "verified_inputs": sum(c["input_status"] == "verified" for c in cases),
        "invalid_inputs": sum(c["input_status"] == "invalid" for c in cases),
        "verified_labels": sum(c["label_status"] == "verified" for c in cases),
        "invalid_labels": sum(c["label_status"] == "invalid" for c in cases),
        "pending_labels": sum(c["label_status"] not in {"verified", "invalid"} for c in cases),
        "usable_observations": len(usable),
        "directional_usable": sum(c["input"]["action"] in {"long", "short"} for c in usable),
        "neutral_usable": sum(c["input"]["action"] == "neutral" for c in usable),
        "invalid_cases": sum(c["status"] == "invalid" for c in cases),
    }


def audit_observation_inputs(database, data_root: Path, *, now):
    current = _selection_now(now)
    archive = RawBodyArchive(data_root / "raw-captures")
    with database.session() as session:
        if session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        summary, selected, rejected = select_inputs(session, current)
        cases = [_audit_case(session, o, r, archive, current) for o, r in selected]
    issues = Counter(c[k] for c in cases for k in ("input_error", "label_error") if c[k])
    return {"schema_version": "observation-input-audit.v1", "checked_at": now.isoformat(),
            "selection": summary.model_dump(mode="json"), **_audit_counts(cases),
            "issues": dict(issues), "promotion_eligible": False,
            "cases": [{k: c[k] for k in (
                "outlook_id", "input_status", "input_error", "label_status", "label_error",
            )} for c in cases]}


def export_prospective_inputs(database, data_root: Path, output: Path, *, now):
    current = _selection_now(now)
    _require(not output.exists(), "new_output_directory_required")
    archive = RawBodyArchive(data_root / "raw-captures")
    cases = []
    with database.session() as session:
        # sqlite's legacy driver does not open a read transaction for SELECT.
        # Hold one WAL snapshot across selection, lineage, and label reads.
        if session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        summary, selected, rejected = select_inputs(session, current)
        cases = [_audit_case(session, o, r, archive, current, include_bodies=True)
                 for o, r in selected]
    manifest = {"schema_version": "prospective-observation-dataset.v2",
                "purpose": "observational_only", "promotion_eligible": False,
                "limitations": ["selection_not_preregistered_for_historical_rows",
                                "study_identity_not_proof_of_event_independence",
                                "method_version_not_bound_to_candidate_recipe",
                                "paired_baseline_candidate_predictions_missing",
                                "minute_close_not_execution_pnl"],
                "summary": summary.model_dump(mode="json"), "rejected": rejected,
                "cases": [], **_audit_counts(cases)}
    output.mkdir(parents=True, exist_ok=False)
    for index, case in enumerate(cases):
        body = (json.dumps(case, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        name = f"case-{index:05}.json"
        (output / name).write_bytes(body)
        manifest["cases"].append({"path": name, "sha256": hashlib.sha256(body).hexdigest(),
                                  "outlook_id": case["outlook_id"], "status": case["status"]})
    manifest["content_hash"] = digest(manifest)
    (output / "manifest.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
    )
    return manifest


def load_prospective_inputs(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    content_hash = manifest.pop("content_hash", None)
    _require(content_hash == digest(manifest), "dataset_manifest_hash_mismatch")
    _require(manifest.get("schema_version") in {"prospective-observation-dataset.v1",
                                                  "prospective-observation-dataset.v2"}
             and manifest.get("purpose") == "observational_only"
             and manifest.get("promotion_eligible") is False, "dataset_purpose_invalid")
    cases = []
    for entry in manifest["cases"]:
        path = Path(entry["path"])
        _require(not path.is_absolute() and len(path.parts) == 1
                 and path.name.startswith("case-") and path.suffix == ".json",
                 "dataset_case_path_invalid")
        body = (directory / path).read_bytes()
        _require(hashlib.sha256(body).hexdigest() == entry["sha256"], "dataset_case_hash_mismatch")
        case = json.loads(body)
        _require(case["outlook_id"] == entry["outlook_id"]
                 and case["status"] == entry["status"], "dataset_case_identity_mismatch")
        for capture in (case.get("label") or {}).get("captures", []):
            _require(hashlib.sha256(capture["body_text"].encode("utf-8")).hexdigest()
                     == capture["body_hash"], "dataset_archive_hash_mismatch")
        for receipt in case.get("input_receipts", []):
            try:
                body = base64.b64decode(receipt["body_base64"], validate=True)
            except (ValueError, KeyError) as exc:
                raise ValueError("dataset_input_archive_invalid") from exc
            _require(hashlib.sha256(body).hexdigest() == receipt["body_hash"],
                     "dataset_input_archive_hash_mismatch")
        cases.append(case)
    if manifest["schema_version"] == "prospective-observation-dataset.v2":
        _require(all(manifest.get(k) == v for k, v in _audit_counts(cases).items()),
                 "dataset_count_mismatch")
    manifest["content_hash"] = content_hash
    return manifest, cases
