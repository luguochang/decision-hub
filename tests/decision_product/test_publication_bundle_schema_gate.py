"""Real SQLite normal enqueue boundary, not Graph/review financial authority."""

import json
from datetime import UTC, datetime
from hashlib import sha256

from packages.kernel.decision_hub_kernel.application.commit import (
    PUBLICATION_SOURCE_ROLE,
    PUBLICATION_SOURCE_SCHEMA_V4,
    PUBLICATION_SOURCE_SCHEMA_V5,
)
from packages.kernel.decision_hub_kernel.application.research_publication_jobs48 import (
    enqueue_in_publication_transaction,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord,
)


def test_normal_producer_commits_v5_report_without_legacy_job_and_preserves_v4(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "producer.sqlite3").as_posix())
    db.create_all()
    at = datetime(2026, 10, 19, tzinfo=UTC)
    try:
        for name, schema, selection in [
            ("v5", PUBLICATION_SOURCE_SCHEMA_V5, "v3"),
            ("v4_older", PUBLICATION_SOURCE_SCHEMA_V4, "v2"),
            ("v4_current", PUBLICATION_SOURCE_SCHEMA_V4, "v3"),
        ]:
            artifact_id = "artifact_" + name
            with db.session() as session:
                begin_immediate(session)
                session.add(
                    RunRecord(
                        run_id=name,
                        event_id="event",
                        status="degraded",
                        artifact_id=artifact_id,
                        finished_at=at,
                        created_at=at,
                        updated_at=at,
                    )
                )
                session.add(
                    ArtifactRecord(
                        artifact_id=artifact_id,
                        run_id=name,
                        event_id="event",
                        gate_status="degraded",
                        headline="Explicit producer boundary fixture",
                        summary="No Graph or financial qualification claim",
                        payload_json="{}",
                        created_at=at,
                    )
                )
                bodies = [
                    (
                        PUBLICATION_SOURCE_ROLE,
                        schema,
                        dict(
                            schema_version=schema,
                            run_id=name,
                            event_id="event",
                            artifact_id=artifact_id,
                        ),
                    ),
                    (
                        "graph_recipe_selection",
                        "research-graph-recipe-selection." + selection,
                        dict(
                            schema_version="research-graph-recipe-selection." + selection,
                            run_id=name,
                        ),
                    ),
                ]
                for role, version, body in bodies:
                    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
                    session.add(
                        RunRoleOutputRecord(
                            output_id=name + "_" + role,
                            run_id=name,
                            role=role,
                            schema_version=version,
                            payload_json=raw,
                            payload_hash=sha256(raw.encode()).hexdigest(),
                            created_at=at,
                        )
                    )
                result = enqueue_in_publication_transaction(session, name, now=at)
                assert (result is None) is (schema == PUBLICATION_SOURCE_SCHEMA_V5)
                assert enqueue_in_publication_transaction(session, name, now=at) == result
            with db.session() as session:
                assert session.get(ArtifactRecord, artifact_id) is not None
                assert session.get(RunRecord, name).status == "degraded"
                job = session.get(PublicationBundleJobRecord, name)
                if schema == PUBLICATION_SOURCE_SCHEMA_V5:
                    assert job is None
                else:
                    assert (
                        job.state == "queued"
                        and job.publication_schema == PUBLICATION_SOURCE_SCHEMA_V4
                    )
        print("V5 report rows committed/no legacy job; V4 selection v2+v3 queued and idempotent")
    finally:
        db.engine.dispose()
