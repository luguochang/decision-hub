"""Saved V1/V2 configuration projections use one borrowed SQLite session."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchVersionLineage,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ROLE, ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_value_basis import SavedCoverageBasis
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.query_views.research.configuration import SavedConfiguration, saved_configuration
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    _admitted_run,  # pyright: ignore[reportPrivateUsage] -- real isolated admission fixture
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ConfigurationCase:
    database: Database
    request: ResearchSessionRequest
    pack_root: Path


@pytest.fixture
def prepared_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Generator[ConfigurationCase]:
    monkeypatch.setenv("LLM_ENABLED", "0")
    monkeypatch.setenv("DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root = tmp_path / "prepared-pack"
        shutil.copytree(ROOT / "packs/crypto_macro_prepared/1.1.0", root)
        manifest = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
        factory = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode="replay",
            clock=lambda: NOW,
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        yield ConfigurationCase(database, factory.build(event_id, run_id), root)
    finally:
        database.engine.dispose()


def _physical_rows(database: Database) -> dict[str, list[list[object]]]:
    path = database.engine.url.database
    assert path is not None and path != ":memory:"
    with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return {
            table: [list(row) for row in connection.execute(
                'SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid'
            )]
            for table in tables
        }


def _project(case: ConfigurationCase) -> SavedConfiguration:
    with case.database.session() as session:
        run = session.get(RunRecord, case.request.run_id)
        assert run is not None
        return saved_configuration(
            session, run, SavedCoverageBasis("saved_request", request=case.request),
            pack_service=ResearchPackService(case.database),
        )


def test_real_prepared_manifest_version_survives_today_pack_edits(
    prepared_case: ConfigurationCase,
) -> None:
    case = prepared_case
    before = _physical_rows(case.database)
    view = _project(case)
    assert view["pack_basis"] == "saved_pack"
    assert view["domain_pack_version"] == "1.1.0"
    assert view["domain_pack_ref"] == "crypto_macro.v1"
    assert view["gate_policy_ref"] == "gates/prepared.yaml"
    assert view["pack_receipt_ref"]
    # The path is a frozen declaration, not proof that a checker executed.
    assert "gate_policy_ref" in view["unverified_fields"]
    assert view["capability_versions"] == [] and view["source_registry_ref"] is None
    typed = ResearchVersionLineage(
        **view, runtime_id="controlled-runtime", runtime_version="controlled-v1",
    )
    assert typed.domain_pack_version == "1.1.0"
    manifest_path = case.pack_root / "pack.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "9.0.0"
    manifest["gate_policy_ref"] = "today-gate"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert _project(case) == view
    assert _physical_rows(case.database) == before


@pytest.mark.parametrize("damage", ["hash", "unknown_schema", "missing_task", "missing_pack"])
def test_corrupt_or_partial_prepared_receipts_remain_invalid_and_unchanged(
    prepared_case: ConfigurationCase, damage: str,
) -> None:
    case = prepared_case
    with case.database.session() as session:
        role = "research_task_request" if damage == "missing_task" else ROLE
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=case.request.run_id, role=role,
        ).one()
        if damage in {"missing_task", "missing_pack"}:
            session.delete(row)
        elif damage == "unknown_schema":
            row.schema_version = "research-request-pack.unknown"
        else:
            body = json.loads(row.payload_json)
            body["definition"]["manifest"]["version"] = "9.0.0"
            row.payload_json = json.dumps(body)
    before = _physical_rows(case.database)
    view = _project(case)
    assert view["pack_basis"] == "invalid"
    assert view["domain_pack_version"] is None and view["gate_policy_ref"] is None
    assert view["pack_receipt_ref"] is None
    assert view["domain_pack_ref"] == case.request.domain_pack_ref
    assert view["request_basis"] == "saved_request"
    assert _physical_rows(case.database) == before


def test_original_v1_saved_configuration_remains_read_only(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
            event_id, run_id,
        )
        before = _physical_rows(database)
        view = _project(ConfigurationCase(database, request, PACK_ROOT))
        assert view["pack_basis"] == "saved_pack"
        assert view["domain_pack_version"] == "1.0.0"
        assert view["pack_receipt_ref"]
        assert "gate_policy_ref" in view["unverified_fields"]
        assert _physical_rows(database) == before
    finally:
        database.engine.dispose()


def test_genuinely_absent_historical_receipt_is_unavailable(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    try:
        before = _physical_rows(database)
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            view = saved_configuration(
                session, run, SavedCoverageBasis("unavailable"),
                pack_service=ResearchPackService(database),
            )
        assert view["pack_basis"] == "unavailable"
        assert view["domain_pack_version"] is None and view["gate_policy_ref"] is None
        assert view["domain_pack_ref"] is None and view["pack_receipt_ref"] is None
        assert _physical_rows(database) == before
    finally:
        database.engine.dispose()
