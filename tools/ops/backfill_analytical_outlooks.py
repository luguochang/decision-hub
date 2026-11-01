"""Offline 0051 migration; project legacy judgments only into an excluded retrospective cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

from pydantic import RootModel

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application.analytical_outlook import freeze_outlooks
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ForecastRecord,
    GateDecisionRecord,
    PublicationDecisionRecord,
    ResearchResultRecord,
    RunRecord,
    as_utc,
    utcnow,
)
from tools.ops.backfill_studies import immutable_digests
from tools.ops.database import backup, integrity


def project(database):
    with database.session() as session:
        artifacts = (
            session.query(ArtifactRecord)
            .outerjoin(
                PublicationDecisionRecord,
                PublicationDecisionRecord.artifact_id == ArtifactRecord.artifact_id,
            )
            .filter(PublicationDecisionRecord.artifact_id.is_(None))
            .order_by(ArtifactRecord.created_at)
            .all()
        )
        for artifact in artifacts:
            run = session.get(RunRecord, artifact.run_id)
            stored = session.get(ResearchResultRecord, run.run_id)
            raw = ResearchSessionResult.model_validate_json(stored.payload_json) if stored else None
            result = (
                raw.model_copy(
                    update={
                        "horizons": [
                            h.model_copy(
                                update={
                                    "probability_status": "uncalibrated"
                                    if h.subjective_probability is not None
                                    else "unavailable",
                                }
                            )
                            for h in raw.horizons
                        ]
                    }
                )
                if raw
                else None
            )
            published = [
                RootModel[dict](
                    {
                        "horizon": f.horizon,
                        "action": f.direction,
                        "subjective_probability": f.probability,
                        "trigger": f.trigger,
                        "invalidation": f.invalidation,
                        "expires_at": as_utc(f.expires_at).isoformat(),
                    }
                )
                for f in session.query(ForecastRecord).filter_by(artifact_id=artifact.artifact_id)
            ]
            decisions = [
                {
                    key: getattr(row, key)
                    for key in ("rule_id", "status", "reason_code", "input_hash")
                }
                for row in session.query(GateDecisionRecord).filter_by(
                    artifact_id=artifact.artifact_id
                )
            ]
            freeze_outlooks(
                session,
                run=run,
                artifact=artifact,
                result=result,
                raw_result=raw,
                gate=GateResult(GateStatus(artifact.gate_status), decisions),
                published_horizons=published,
                now=as_utc(artifact.created_at),
                retrospective=True,
            )
        session.flush()
        return {
            "publications": session.query(PublicationDecisionRecord).count(),
            "outlooks": session.query(AnalyticalOutlookRecord).count(),
            "retrospective": session.query(AnalyticalOutlookRecord)
            .filter_by(cohort="retrospective")
            .count(),
            "prospective": session.query(AnalyticalOutlookRecord)
            .filter_by(cohort="prospective")
            .count(),
        }


def digests(path):
    result = immutable_digests(path)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for table, key in (
            ("outcomes", "outcome_id"),
            ("fact_versions", "fact_version_id"),
            ("run_input_refs", "run_id,fact_id"),
            ("raw_captures", "capture_id"),
        ):
            rows = db.execute(f"select * from {table} order by {key}").fetchall()
            result[table] = {
                "count": len(rows),
                "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
            }
    return result


def apply(path, backup_path, *, rehearsal):
    if not path.is_file() or backup_path.exists() or path.resolve() == backup_path.resolve():
        raise ValueError("database_or_new_backup_path_invalid")
    if (
        rehearsal
        and path.resolve() == Path("data/core-live-20260913/db/decision_hub.sqlite3").resolve()
    ):
        raise ValueError("rehearsal_must_not_use_formal_database")
    if not rehearsal:
        for port in (8047, 3097, 8037, 8038):
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise ValueError(f"stop_managed_product_before_migration:{port}")
    backup(path, backup_path)
    if not integrity(backup_path):
        raise ValueError("backup_integrity_failed")
    before = digests(path)
    db = Database(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    db.initialize()
    first, second = project(db), project(db)
    db.engine.dispose()
    if first != second or before != digests(path) or not integrity(path):
        raise ValueError("outlook_projection_verification_failed")
    return dict(
        status="passed",
        at=utcnow().isoformat(),
        scope="rehearsal" if rehearsal else "formal",
        projection_counts=first,
        old_records=before,
        repeated_projection_unchanged=True,
        database=str(path.resolve()),
        backup=str(backup_path.resolve()),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rehearsal", action="store_true")
    args = parser.parse_args()
    result = apply(args.database, args.backup, rehearsal=args.rehearsal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({"status": result["status"], "projection_counts": result["projection_counts"]})
    )


if __name__ == "__main__":
    main()
