"""Real library ingestion and Factory PIT boundaries; no model or result is made."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ResearchRequest
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate, SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchDocumentCorrectionRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    SnapshotRecord,
    as_utc,
)
from tests.evolution.test_research_worker import PACK_ROOT
from tests.research.test_full_method_v2_bundle import (
    CAPABILITIES,
    _library,  # pyright: ignore[reportPrivateUsage]
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    _ready_version,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,  # pyright: ignore[reportUnusedImport]  # noqa: F401
)


@pytest.mark.parametrize("prepared", [True, False], ids=["prepared", "legacy"])
@pytest.mark.parametrize("order", ["before-trigger", "after-trigger"])
def test_replay_never_selects_library_received_after_original_frozen_pit(
    tmp_path: Path, prepared: bool, order: str
) -> None:
    _case(tmp_path, prepared=prepared, order=order, execution_mode="replay")


@pytest.mark.parametrize("prepared", [True, False], ids=["prepared", "legacy"])
def test_live_library_after_trigger_but_before_effective_pit_is_eligible(
    tmp_path: Path, prepared: bool
) -> None:
    _case(tmp_path, prepared=prepared, order="after-trigger", execution_mode="live")


def _case(
    directory: Path, *, prepared: bool, order: str, execution_mode: Literal["live", "replay"]
) -> None:
    database = Database(f"sqlite+pysqlite:///{(directory / 'library-pit.sqlite3').as_posix()}")
    database.create_all()
    try:
        library = _library(database, directory)
        version_id = None
        if order == "before-trigger":
            version_id = _ready_version(library, "Actually received before original trigger")
        # This is an actual local admission clock sampled after the preceding
        # operations, not externally verified publication time.
        received = datetime.now(UTC)
        event_id, _, admitted = AdmissionService(database, clock=lambda: received).admit(
            ObservationCreate(
                text="The official event confirms BTC macro transmission risks.",
                source_id="library-pit-controlled-transcript",
                source_type=SourceType.transcript,
                observed_at=received - timedelta(seconds=1),
                published_at=received - timedelta(seconds=2),
                language="en",
                event_hint="central_bank_speech",
            )
        )
        assert admitted
        run_id, created = RunService(database, clock=lambda: received).create(
            event_id, strategy_version="research.v1"
        )
        assert created
        snapshot_id, _ = SnapshotService(database).freeze_for_run(run_id, event_id)
        if order == "after-trigger":
            version_id = _ready_version(library, "Actually received after original trigger")
        assert version_id is not None
        library.select_context(
            ResearchRequest(
                schema_version="research-request.v1",
                research_family="crypto_macro",
                instrument_scope=["BTC-USDT-SWAP"],
                focus="Original PIT control question",
                library_context_mode="selected",
                selected_version_ids=[version_id],
                allow_remote_library_content=True,
            ),
            run_id=run_id,
        )
        now = datetime.now(UTC)
        pack_root = PACK_ROOT
        refs = None
        if prepared:
            pack_root, manifest = _prepared_pack(directory)
            refs = PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            )
        request = ResearchRequestFactory(
            database,
            pack_root=pack_root,
            allowed_capabilities=CAPABILITIES,
            prepared_policy_refs=refs,
            execution_mode=execution_mode,
            clock=lambda: now,
        ).build(event_id, run_id)
        # Legacy workers freeze this complete Task before execution. Call the
        # real seam but make no Executor/Host request or successful result.
        ResearchTaskService(database).freeze(request)
        with database.session() as session:
            version = session.get(ResearchDocumentVersionRecord, version_id)
            snapshot = session.get(SnapshotRecord, snapshot_id)
            scope = session.get(ResearchRunScopeRecord, run_id)
            correction = (
                session.query(ResearchDocumentCorrectionRecord)
                .filter_by(version_id=version_id)
                .one()
            )
            assert version is not None and snapshot is not None and scope is not None
            assert version.parse_receipt_ref is not None
            document_received = as_utc(version.received_at)
            trigger_cutoff = as_utc(snapshot.cutoff_at)
            assert document_received is not None and trigger_cutoff is not None
            times = {
                "published_at": version.published_at.isoformat() if version.published_at else None,
                "observed_at_sql": version.observed_at.isoformat(),
                "received_at_sql": version.received_at.isoformat(),
                "version_created_at_sql": version.created_at.isoformat(),
                "correction_created_at_sql": correction.created_at.isoformat(),
                "correction_accepted_at_sql": (
                    correction.accepted_at.isoformat() if correction.accepted_at else None
                ),
                "parse_receipt": json.loads(library.store.read(version.parse_receipt_ref)),
                "scope_created_at_sql": scope.created_at.isoformat(),
                "scope_input_receipts_json": scope.input_receipts_json,
                "scope_selected_version_ids_json": scope.selected_version_ids_json,
                "scope_allow_remote_library_content": scope.allow_remote_library_content,
                "version_status": version.status,
                "original_file_hash": version.original_file_hash,
            }
        selected = [
            item
            for item in request.input_evidence
            if item.source_id == f"research-library:{version_id}"
        ]
        observation = {
            "prepared": prepared,
            "execution_mode": execution_mode,
            "order": order,
            "source_times": times,
            "original_trigger_cutoff": trigger_cutoff.isoformat(),
            "effective_request_pit_cutoff": request.pit_cutoff_at.isoformat(),
            "factory_read_at": now.isoformat(),
            "selected_library_count": len(selected),
            "selected_library": [item.model_dump(mode="json") for item in selected],
            "request": request.model_dump(mode="json"),
            "rows": _rows(database, run_id),
            "version_time_rows_edited": False,
            "library_clock_overridden": False,
            "external_publication_verified": False,
        }
        (directory / "observation.json").write_text(
            json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"LIBRARY_PIT_OBSERVATION {prepared=} {execution_mode=} {order=} {len(selected)=}")
        assert version.published_at is None
        if order == "after-trigger":
            assert document_received > trigger_cutoff
        else:
            assert document_received <= trigger_cutoff
        assert all(item.received_at == document_received for item in selected)
        assert all(item.published_at is None for item in selected)
        assert json.loads(scope.selected_version_ids_json) == [version_id]
        assert scope.allow_remote_library_content
        assert version.status == "ready" and correction.accepted_at is not None
        assert request.context_manifest is not None
        receipts = json.loads(scope.input_receipts_json)
        assert {item["evidence_id"] for item in receipts} == {
            item.evidence_id for item in selected
        }
        if execution_mode == "replay":
            assert request.pit_cutoff_at == trigger_cutoff
            if order == "after-trigger":
                assert not selected, (
                    "Library received after frozen replay PIT entered complete Task"
                )
                assert any(
                    item.reason == "received_after_cutoff"
                    and item.evidence_ref.startswith(f"library:{version_id}:")
                    for item in request.context_manifest.excluded_inputs
                )
            else:
                assert selected
        else:
            assert trigger_cutoff < document_received <= request.pit_cutoff_at
            assert selected
    finally:
        database.engine.dispose()
