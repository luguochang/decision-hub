"""Shared pre-dispatch and post-execution verification of frozen research input archives."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.fact_lineage import run_scope
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    PublicationDecisionRecord,
    RawCaptureRecord,
    RunInputRefRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive


class _Archives:
    def __init__(self, roots):
        self.archives = [RawBodyArchive(Path(root) / "raw-captures") for root in roots]

    def read(self, ref):
        for archive in self.archives:
            try:
                return archive.read(ref)
            except FileNotFoundError:
                continue
        raise ValueError("prospective_raw_archive_missing")


def verify_input_receipts(session, data, archive, *, request=None):
    from packages.evals.prospective_observations import _frozen_input, _require

    cutoff = datetime.fromisoformat(data["cutoff_at"])
    if request is not None:
        _require(request.starts_at <= cutoff < request.ends_at, "pair_input_outside_trial")
    outlook = session.get(AnalyticalOutlookRecord, data["outlook_id"])
    _require(outlook is not None, "pair_source_missing")
    frozen = _frozen_input(session, outlook)
    publication = session.get(PublicationDecisionRecord, outlook.artifact_id)
    _require(publication and as_utc(publication.decided_at) <= cutoff,
             "source_publication_after_cutoff")
    market = request.market if request is not None else outlook.market
    instrument = request.instrument if request is not None else outlook.instrument
    source_market, source_instrument = run_scope(session, outlook.run_id)
    # A daily spot outlook can be derived inside a perpetual research topic.
    # Preserve both scopes: every fact belongs to the original study, and the
    # audited daily rule's input instrument must match its prediction target.
    daily_target = (outlook.method_family == "daily_rule"
                    and outlook.method_version == "daily-trend-v0.1")
    _require(
        sorted(frozen["facts"], key=lambda f: f["fact_version_id"]) == data["facts"]
        and frozen["recorded_at"] == data["cutoff_at"]
        and all(
            frozen[k] == data[k] for k in ("study_id", "run_id", "market", "instrument", "horizon")
        ),
        "pair_frozen_input_mismatch",
    )
    captures = {}
    for fact in data["facts"]:
        version = session.get(FactVersionRecord, fact["fact_version_id"])
        parsed = session.get(ParsedVersionRecord, fact["parsed_version_id"])
        refs = (
            session.query(RunInputRefRecord)
            .filter_by(
                run_id=data["run_id"],
                fact_version_id=fact["fact_version_id"],
            )
            .all()
        )
        _require(
            version is not None and version.market == market
            and (version.scope_instrument in {instrument, "*"} if not daily_target else (
                source_market == market and version.scope_instrument == source_instrument
                and json.loads(version.payload_json).get("instrument") == instrument
            ))
            and all(
                as_utc(t) <= cutoff
                for t in (
                    version.known_at,
                    version.observed_at,
                    version.recorded_at,
                )
            )
            and refs
            and all(as_utc(r.adopted_at) <= cutoff and as_utc(r.as_of) <= cutoff for r in refs),
            "pair_fact_after_cutoff_or_scope_mismatch",
        )
        _require(
            parsed and as_utc(parsed.recorded_at) <= cutoff, "pair_parsed_archive_missing_or_late"
        )
        ids = json.loads(parsed.capture_ids_json)
        _require(bool(ids), "pair_input_capture_missing")
        for identity in ids:
            if identity in captures:
                continue
            row = session.get(RawCaptureRecord, identity)
            _require(
                row
                and row.status_code is not None and 200 <= row.status_code < 300
                and row.body_ref
                and row.body_hash
                and as_utc(row.completed_at) <= cutoff
                and as_utc(row.recorded_at) <= cutoff,
                "pair_input_capture_missing_or_late",
            )
            body = archive.read(row.body_ref)
            _require(hashlib.sha256(body).hexdigest() == row.body_hash, "archive_hash_mismatch")
            captures[identity] = {
                "capture_id": identity,
                "body_ref": row.body_ref,
                "body_hash": row.body_hash,
                "status_code": row.status_code,
                "completed_at": as_utc(row.completed_at).isoformat(),
            }
    # Linked captures attest storage and timing, not correctness of a parser's interpretation.
    return list(captures.values())

