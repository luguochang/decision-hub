from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunInputRefRecord,
    RunRecord,
    StudyRecord,
    as_utc,
    utcnow,
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def run_scope(session: Session, run_id: str) -> tuple[str, str]:
    run = session.get(RunRecord, run_id)
    study = session.get(StudyRecord, run.study_id) if run and run.study_id else None
    return (study.market, study.instrument) if study else ("unknown", "unknown")


class FactLineageStore:
    def __init__(self, database: Database):
        self.database = database

    def record_capture(self, entry: dict) -> None:
        with self.database.session() as session:
            session.add(RawCaptureRecord(**entry))

    def bind_parsed(
        self,
        session,
        *,
        evidence_ids,
        captures=(),
        parser_version="unknown",
        reused=None,
        legacy=False,
    ):
        """The complete call input set is retained, without inventing sentence citations."""
        for evidence_id in evidence_ids:
            if session.get(EvidenceParsedLinkRecord, evidence_id):
                continue
            row = session.get(ResearchEvidenceRecord, evidence_id)
            if row is None:
                raise ValueError("parsed_evidence_missing")
            parsed_id = (reused or {}).get(evidence_id)
            if parsed_id:
                origin = session.get(ParsedVersionRecord, parsed_id)
                if origin is None or origin.content_hash != row.content_hash:
                    raise ValueError("parsed_reuse_identity_mismatch")
            else:
                good = [
                    c
                    for c in captures
                    if c.get("body_ref")
                    and c.get("status_code") is not None
                    and 200 <= c["status_code"] < 300
                ]
                # Content-addressed version identity; repeated fetch receipt IDs
                # may differ but all original receipts remain separately queryable.
                parsed_id = (
                    "parsed_"
                    + digest(
                        [
                            row.content_hash,
                            parser_version,
                            row.parser_ref,
                            sorted({c["body_hash"] for c in good}),
                            row.content_ref,
                            row.acquisition_receipt_id,
                        ]
                    )[:48]
                )
                if session.get(ParsedVersionRecord, parsed_id) is None:
                    session.add(
                        ParsedVersionRecord(
                            parsed_version_id=parsed_id,
                            original_evidence_id=evidence_id,
                            content_hash=row.content_hash,
                            parser_version=f"{parser_version}:{row.parser_ref or 'adapter'}"[:256],
                            capture_ids_json=json.dumps([c["capture_id"] for c in good]),
                            content_ref=row.content_ref,
                            acquisition_receipt_id=row.acquisition_receipt_id,
                            archive_status=(
                                "captured"
                                if good
                                else "reference_only"
                                if row.content_ref or row.acquisition_receipt_id
                                else "legacy_unarchived"
                                if legacy
                                else "unarchived"
                            ),
                            recorded_at=utcnow(),
                        )
                    )
                    session.flush()
            session.add(
                EvidenceParsedLinkRecord(evidence_id=evidence_id, parsed_version_id=parsed_id)
            )
        session.flush()

    def bind_facts(self, session, *, facts, cutoff_at, reused=None, legacy=False):
        # Import locally: the fact store invokes this projection after validation.
        from packages.kernel.decision_hub_kernel.application.fact_store import _fact_model

        for fact in facts:
            if session.get(RunInputRefRecord, fact.fact_id):
                continue
            evidence = session.get(ResearchEvidenceRecord, fact.evidence_id)
            if evidence is None or evidence.run_id != fact.run_id:
                raise ValueError("fact_lineage_evidence_mismatch")
            self.bind_parsed(session, evidence_ids=[evidence.evidence_id], legacy=legacy)
            link = session.get(EvidenceParsedLinkRecord, evidence.evidence_id)
            market, instrument = run_scope(session, fact.run_id)
            model = _fact_model(fact)
            payload = model.model_dump(mode="json")
            # Slot identity includes valid window, source and unit. A revision
            # cannot silently replace a different instrument/window/provider.
            semantic_key = digest(
                [
                    market,
                    instrument,
                    fact.metric_family,
                    fact.field,
                    fact.instrument,
                    fact.venue,
                    fact.unit,
                    fact.source_id,
                    payload["window_start_at"],
                    payload["window_end_at"],
                    fact.event_offset,
                ]
            )
            origin_id = (reused or {}).get(fact.fact_id)
            version_id = (
                origin_id
                or "fv_"
                + digest(
                    [
                        semantic_key,
                        fact.payload_hash,
                        link.parsed_version_id,
                        payload["observed_at"],
                    ]
                )[:48]
            )
            version = session.get(FactVersionRecord, version_id)
            if origin_id and (
                version is None
                or version.semantic_key != semantic_key
                or json.loads(version.payload_json)["payload_hash"] != fact.payload_hash
            ):
                raise ValueError("fact_reuse_identity_mismatch")
            if version is None:
                version = FactVersionRecord(
                    fact_version_id=version_id,
                    semantic_key=semantic_key,
                    market=market,
                    scope_instrument=instrument,
                    field=fact.field,
                    source_id=fact.source_id,
                    original_fact_id=fact.fact_id,
                    parsed_version_id=link.parsed_version_id,
                    payload_json=json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    known_at=max(
                        as_utc(fact.received_at),
                        as_utc(fact.accepted_at),
                        as_utc(evidence.received_at),
                        as_utc(evidence.accepted_at),
                    ),
                    observed_at=fact.observed_at,
                    recorded_at=utcnow(),
                )
                session.add(version)
                session.flush()
            session.add(
                RunInputRefRecord(
                    fact_id=fact.fact_id,
                    run_id=fact.run_id,
                    fact_version_id=version_id,
                    use_mode="reused" if origin_id else "legacy" if legacy else "fresh",
                    as_of=cutoff_at,
                    adopted_at=fact.accepted_at,
                )
            )
        session.flush()

    def backfill(self) -> dict[str, int]:
        with self.database.session() as session:
            # Stable chronological order makes earliest actually accepted version
            # the origin. Never touch original Evidence or Fact records.
            rows = (
                session.query(ResearchFactRecord)
                .order_by(ResearchFactRecord.accepted_at, ResearchFactRecord.fact_id)
                .all()
            )
            for row in rows:
                self.bind_facts(session, facts=[row], cutoff_at=row.accepted_at, legacy=True)
            for evidence in session.query(ResearchEvidenceRecord).all():
                self.bind_parsed(session, evidence_ids=[evidence.evidence_id], legacy=True)
            return {
                "facts": len(rows),
                "versions": session.query(FactVersionRecord).count(),
                "refs": session.query(RunInputRefRecord).count(),
                "parsed": session.query(ParsedVersionRecord).count(),
            }

    def list_versions(
        self,
        *,
        as_of: datetime,
        market=None,
        instrument=None,
        field=None,
        source_id=None,
        fact_instrument=None,
        metric_family=None,
        semantic_key=None,
        limit=50,
        offset=0,
    ):
        if as_of.tzinfo is None or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("fact_query_invalid")
        with self.database.session() as session:
            query = session.query(FactVersionRecord).filter(
                FactVersionRecord.known_at <= as_of, FactVersionRecord.observed_at <= as_of
            )
            for column, value in (
                (FactVersionRecord.market, market),
                (FactVersionRecord.scope_instrument, instrument),
                (FactVersionRecord.field, field),
                (FactVersionRecord.source_id, source_id),
                (
                    func.json_extract(FactVersionRecord.payload_json, "$.instrument"),
                    fact_instrument,
                ),
                (
                    func.json_extract(FactVersionRecord.payload_json, "$.metric_family"),
                    metric_family,
                ),
                (FactVersionRecord.semantic_key, semantic_key),
            ):
                if value is not None:
                    query = query.filter(column == value)
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            rows = (
                query.order_by(FactVersionRecord.known_at.desc(), FactVersionRecord.fact_version_id)
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "as_of": as_of,
                "items": [self._view(session, row) for row in rows],
            }

    def filter_options(self, *, as_of: datetime):
        """Read actual PIT dimensions, independently of list paging and Run scope.

        The product database is SQLite. Extract the original fact instrument and
        metric family, not the enclosing research scope instrument.
        """
        if as_of.tzinfo is None:
            raise ValueError("fact_query_invalid")
        instrument = func.json_extract(FactVersionRecord.payload_json, "$.instrument")
        family = func.json_extract(FactVersionRecord.payload_json, "$.metric_family")
        with self.database.session() as session:
            query = session.query(FactVersionRecord).filter(
                FactVersionRecord.known_at <= as_of, FactVersionRecord.observed_at <= as_of
            )
            return {
                "as_of": as_of,
                "fields": [row[0] for row in query.with_entities(
                    FactVersionRecord.field
                ).distinct().order_by(FactVersionRecord.field).all()],
                "sources": [row[0] for row in query.with_entities(
                    FactVersionRecord.source_id
                ).distinct().order_by(FactVersionRecord.source_id).all()],
                "instruments": [
                    {"instrument": row[0], "metric_family": row[1]}
                    for row in query.with_entities(instrument, family).filter(
                        instrument.isnot(None), instrument != ""
                    ).distinct().order_by(instrument, family).all()
                ],
            }

    def get_version(self, version_id):
        with self.database.session() as session:
            row = session.get(FactVersionRecord, version_id)
            if row is None:
                raise KeyError(version_id)
            return self._view(session, row, include_refs=True)

    def _view(self, session, row, include_refs=False):
        parsed = session.get(ParsedVersionRecord, row.parsed_version_id)
        evidence = (
            session.get(ResearchEvidenceRecord, parsed.original_evidence_id) if parsed else None
        )
        captures = (
            [session.get(RawCaptureRecord, id) for id in json.loads(parsed.capture_ids_json)]
            if parsed
            else []
        )
        result = {
            "fact_version_id": row.fact_version_id,
            "semantic_key": row.semantic_key,
            "market": row.market,
            "scope_instrument": row.scope_instrument,
            "known_at": as_utc(row.known_at),
            "recorded_at": as_utc(row.recorded_at),
            "fact": json.loads(row.payload_json),
            "parsed_version_id": row.parsed_version_id,
            "archive_status": parsed.archive_status if parsed else "missing",
            "parser_version": parsed.parser_version if parsed else "unknown",
            "content_ref": parsed.content_ref if parsed else None,
            "structured_payload_ref": evidence.structured_payload_ref if evidence else None,
            "acquisition_receipt_id": parsed.acquisition_receipt_id if parsed else None,
            "raw_captures": [
                {
                    "capture_id": c.capture_id,
                    "url": c.url,
                    "body_ref": c.body_ref,
                    "body_hash": c.body_hash,
                    "status_code": c.status_code,
                    "byte_count": c.byte_count,
                    "media_type": c.media_type,
                    "started_at": as_utc(c.started_at),
                    "completed_at": as_utc(c.completed_at),
                }
                for c in captures
                if c
            ],
        }
        if include_refs:
            refs = (
                session.query(RunInputRefRecord)
                .filter_by(fact_version_id=row.fact_version_id)
                .order_by(RunInputRefRecord.adopted_at)
                .all()
            )
            result["run_inputs"] = [
                {
                    "run_id": ref.run_id,
                    "fact_id": ref.fact_id,
                    "use_mode": ref.use_mode,
                    "as_of": as_utc(ref.as_of),
                    "adopted_at": as_utc(ref.adopted_at),
                }
                for ref in refs
            ]
        return result
