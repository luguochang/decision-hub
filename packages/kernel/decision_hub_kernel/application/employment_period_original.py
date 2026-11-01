"""Read-only next-release evidence derived from native BLS originals at the agenda cutoff."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.db import RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.employment_source_rules import METRICS, parse_employment_next_release
from ..ports.market_path import aware_utc, canonical_hash
from .event_scenario_employment_realization import read_employment_edition_original_in_session


def _utc(value: datetime | str | None) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    result = as_utc(value)
    if result is None:
        raise ValueError("employment_period_source_clock_missing")
    return result


def _wire(value: datetime | str) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _proof(
    session: Session,
    article: RawCaptureRecord,
    parent: SourcePollAttemptRecord,
    archive: RawBodyArchive,
    at: datetime,
) -> dict[str, Any] | None:
    if article.body_ref is None:
        raise ValueError("employment_release_capture_original_invalid")
    raw = archive.read(article.body_ref)
    statement = parse_employment_next_release(raw, source_url=article.url)
    rule = dict(
        source_url=article.url,
        reference_period=statement.previous_period,
        vintage_policy="current_reference_month_first_estimate",
        metrics=[
            dict(
                metric_id=name,
                unit=unit,
                reference_period=statement.previous_period,
                stage_id="release",
            )
            for name, unit in METRICS
        ],
    )
    native = read_employment_edition_original_in_session(
        session, article, parent, rule, archive, at, statement.previous_released_at
    )
    if native is None:
        return None
    return dict(
        statement={
            **asdict(statement),
            "previous_released_at": statement.previous_released_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "scheduled_at": statement.scheduled_at.isoformat().replace("+00:00", "Z"),
        },
        native_source_evidence=native[1],
        declaration_kind="derived_from_native_publisher_bytes_not_reviewed_or_persisted_rule",
    )


def _eligible(session: Session, archive: RawBodyArchive, at: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(RawCaptureRecord, SourcePollAttemptRecord)
        .join(
            SourcePollAttemptRecord,
            RawCaptureRecord.context_id == "source:" + SourcePollAttemptRecord.attempt_id,
        )
        .where(
            SourcePollAttemptRecord.source_id == "bls-releases",
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= at,
            RawCaptureRecord.url.like("https://%bls.gov/news.release/archives/empsit_%.htm"),
        )
    )
    values = []
    for article, parent in rows:
        try:
            value = _proof(session, article, parent, archive, at)
        except (ValueError, OSError, KeyError, TypeError, UnicodeError):
            continue
        if value is not None:
            values.append(value)
    return values


def _chosen(values: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any] | None:
    if not values:
        return None
    latest = max(value["statement"]["previous_released_at"] for value in values)
    values = [value for value in values if value["statement"]["previous_released_at"] == latest]
    identities = {
        canonical_hash(
            dict(statement=value["statement"], edition=value["native_source_evidence"]["edition"])
        )
        for value in values
    }
    if len(identities) != 1 or values[0]["statement"]["scheduled_at"] != _wire(
        entry["scheduled_at"]
    ):
        return None
    return min(values, key=lambda value: value["native_source_evidence"]["attempt"]["completed_at"])


def _attach(entry: dict[str, Any], chosen: dict[str, Any]) -> dict[str, Any]:
    statement = chosen["statement"]
    proof = dict(
        schema_version="nfp-next-release-original.v1",
        event_key=entry["event_key"],
        calendar_ref=entry["source_ref"],
        reference_period=statement["reference_period"],
        scheduled_at=statement["scheduled_at"],
        reference_period_basis="explicit_prior_official_next_release_statement",
        **chosen,
    )
    digest = canonical_hash(proof)
    original = json.loads(entry["original_json"])
    original["employment_next_release_original"] = proof
    known = max(
        _utc(entry["known_at"]), _utc(chosen["native_source_evidence"]["attempt"]["completed_at"])
    )
    raw_refs = list(entry["raw_capture_refs"])
    for name in ("rss_capture", "article_capture"):
        capture = chosen["native_source_evidence"][name]
        reference = dict(ref=capture["body_ref"], hash=capture["body_hash"])
        if reference not in raw_refs:
            raw_refs.append(reference)
    return dict(
        entry,
        original_json=json.dumps(original, sort_keys=True, separators=(",", ":")),
        known_at=known.isoformat().replace("+00:00", "Z"),
        raw_capture_refs=raw_refs,
        dependency_refs=[
            *entry["dependency_refs"],
            dict(ref="derived:nfp-next-release:" + digest, hash=digest),
        ],
    )


def bind_employment_period_in_session(
    session: Session, entry: dict[str, Any], *, archive: RawBodyArchive, as_of: datetime
) -> dict[str, Any]:
    """No complete proof means the exact old agenda entry, including clocks, is retained."""
    at = aware_utc(as_of)
    if entry["family"] != "nfp" or entry["time_precision"] != "exact":
        return entry
    chosen = _chosen(_eligible(session, archive, at), entry)
    return entry if chosen is None else _attach(entry, chosen)
