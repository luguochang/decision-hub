"""Loaded verifier boundary and genuine durable rechecks on isolated SQLite.

These controls never certify the ordinary daily source. The first executes a
real copied verifier module, then changes an actual source byte under its root
without reloading it. The remaining cases use discover/claim/build/finish.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event

from packages.evals import assessment_basis as module
from packages.evals.assessment_basis import (
    LOADED_VERIFIER_VERSION,
    SCHEMA,
    build_assessment_basis,
    loaded_assessment_manifest_hash,
)
from packages.kernel.decision_hub_kernel.application.analytical_assessment import (
    AnalyticalAssessmentService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, as_utc
from tests.evals.test_assessment_basis import NOW, ordinary
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert ordinary is not None and forbid_external_connections is not None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _original(database: Database) -> str:
    with database.session() as session:
        row = session.get(Outlook, "basis-daily")
        assert row is not None
        return _json({
            column.name: str(getattr(row, column.name))
            for column in Outlook.__table__.columns
        })


def _isolated_loaded_module(tmp_path: Path) -> tuple[ModuleType, Path]:
    """Execute exact new verifier bytes against an explicit test-only source root.

    Small seed files isolate the release-file boundary; all SQLite assessment
    work still uses the genuine assessor and keeps the daily verdict unverified.
    This is not a full code-object or source certification fixture.
    """
    root = tmp_path / "verifier-root"
    for relative in module._SOURCE_DIRS:  # pyright: ignore[reportPrivateUsage]
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "boundary_seed.py").write_bytes(b"BOUNDARY_SEED = 1\n")
    gateway_ref = "packages/workbench_adapters/durable_research_gateway.py"
    gateway = root / gateway_ref
    gateway.parent.mkdir(parents=True)
    gateway.write_bytes((Path(module.__file__).resolve().parents[2] / gateway_ref).read_bytes())
    path = root / "packages/evals/assessment_basis.py"
    body = Path(module.__file__).read_bytes()
    path.write_bytes(body)
    name = "_assessment_loaded_verifier_boundary_control"
    loaded = ModuleType(name)
    loaded.__file__ = str(path)
    sys.modules[name] = loaded
    try:
        exec(compile(body, str(path), "exec"), loaded.__dict__)
    finally:
        sys.modules.pop(name, None)
    return loaded, root / "packs/boundary_seed.py"


def test_actual_loaded_verifier_rejects_new_disk_bytes_before_assessment_queries(
    ordinary: Database, tmp_path: Path,
) -> None:
    before = _original(ordinary)
    loaded, changed_source = _isolated_loaded_module(tmp_path)
    frozen = loaded.loaded_assessment_manifest_hash()
    assessed, basis = loaded.build_assessment_basis(
        ordinary, "basis-daily", assessed_at=NOW, archive=None,
    )
    assert basis["loaded_verifier"] == {
        "schema_version": LOADED_VERIFIER_VERSION, "manifest_hash": frozen,
    }
    assert assessed.source_status == "unverifiable" and not assessed.directional_usable
    cached = SimpleNamespace(
        outlook_id=assessed.outlook_id, status="completed", evaluated_at=assessed.assessed_at,
        next_attempt_at=NOW + timedelta(hours=6), verifier_version=frozen,
        basis_json=_json(basis), basis_hash=_hash(_json(basis)),
        assessment_json=_json(assessed.model_dump(mode="json")),
        assessment_hash=_hash(_json(assessed.model_dump(mode="json"))),
    )

    def read_loaded():
        with ordinary.session() as session:
            connection = session.connection()
            connection.exec_driver_sql("BEGIN")
            connection.info["assessment_replay_authorizer_owned"] = True
            try:
                return loaded.read_valid_assessment(session, cached, now=NOW, archive=None)
            finally:
                connection.info.pop("assessment_replay_authorizer_owned", None)

    assert read_loaded() == assessed
    still_loaded = loaded.build_assessment_basis
    changed_source.write_bytes(b"BOUNDARY_SEED = 2\n")
    assert loaded.assessment_manifest_hash() != frozen
    assert loaded.build_assessment_basis is still_loaded
    statements: list[str] = []

    def count(
        _connection: object, _cursor: object, statement: str,
        _parameters: object, _context: object, _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(ordinary.engine, "before_cursor_execute", count)
    try:
        with pytest.raises(ValueError, match="^assessment_loaded_verifier_source_changed$"):
            loaded.build_assessment_basis(
                ordinary, "basis-daily", assessed_at=NOW, archive=None,
            )
    finally:
        event.remove(ordinary.engine, "before_cursor_execute", count)
    assert statements == []
    assert read_loaded() is None
    assert _original(ordinary) == before


@pytest.mark.parametrize("old_body", ["same_manifest_v2", "malformed"])
async def test_future_completed_old_basis_is_genuinely_rechecked_once(
    ordinary: Database, old_body: str,
) -> None:
    original = _original(ordinary)
    assessment, current_basis = build_assessment_basis(
        ordinary, "basis-daily", assessed_at=NOW, archive=None,
    )
    manifest = loaded_assessment_manifest_hash()
    old_basis = dict(current_basis)
    old_basis["schema_version"] = "analytical-assessment-basis.v2"
    old_basis.pop("loaded_verifier")
    basis_json = _json(old_basis) if old_body == "same_manifest_v2" else "{broken-json"
    assessment_json = _json(assessment.model_dump(mode="json"))
    # Explicitly stale derived output: the original Outlook is never modified,
    # and this receipt is never asserted to authenticate any research source.
    with ordinary.session() as session:
        session.add(Job(
            outlook_id="basis-daily", status="completed", attempt=1,
            next_attempt_at=NOW + timedelta(hours=6), evaluated_at=NOW,
            verifier_version=manifest, basis_json=basis_json,
            basis_hash=_hash(basis_json), assessment_json=assessment_json,
            assessment_hash=_hash(assessment_json),
        ))
    service = AnalyticalAssessmentService(ordinary, clock=lambda: NOW)
    assert service.discover(NOW, manifest) == 0
    with ordinary.session() as session:
        queued = session.get(Job, "basis-daily")
        assert queued is not None and queued.status == "pending"
        assert queued.basis_json == basis_json and queued.assessment_json == assessment_json
        assert queued.attempt == 1 and as_utc(queued.next_attempt_at) == NOW
    # The public batch uses the real claim + builder + final fenced writer.
    assert await service.process_due(limit=1) == 1
    with ordinary.session() as session:
        finished = session.get(Job, "basis-daily")
        assert finished is not None and finished.status == "completed"
        assert finished.attempt == 2 and finished.lease_owner is None
        assert finished.basis_json is not None and finished.assessment_json is not None
        fresh_basis = json.loads(finished.basis_json)
        assert fresh_basis["schema_version"] == SCHEMA
        assert fresh_basis["loaded_verifier"] == {
            "schema_version": LOADED_VERIFIER_VERSION, "manifest_hash": manifest,
        }
        assert json.loads(finished.assessment_json)["source_status"] == "unverifiable"
        assert json.loads(finished.assessment_json)["label_status"] == "not_due"
    assert await service.process_due(limit=1) == 0
    with ordinary.session() as session:
        unchanged = session.get(Job, "basis-daily")
        assert unchanged is not None and unchanged.attempt == 2
    assert _original(ordinary) == original
