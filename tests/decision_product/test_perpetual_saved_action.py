"""Real temporary publication originals plus one explicitly seamed report wiring test."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import delete, event
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
    DerivedPerpetualPublication,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from tests.decision_product.perpetual_saved_action_support import text_value as _text
from tests.decision_product.perpetual_saved_action_support import unit_reader as _unit_reader

PREFIX = "packages.kernel.decision_hub_kernel.application."
OBJECT = TypeAdapter(dict[str, JsonValue])
action = importlib.import_module("tests.decision_product.test_perpetual_action_publication")
persisted_original: Callable[..., object] = importlib.import_module(
    "tests.decision_product.test_okx_perpetual_original"
).persisted_original
binding_case: Callable[..., object] = importlib.import_module(
    "tests.decision_product.test_perpetual_research_binding"
).binding_case


def test_original_fixture_includes_publication_bundle_schema(
    persisted_original: tuple[Database, object, object, object],
) -> None:
    database, _, _, _ = persisted_original
    with database.engine.connect() as connection:
        names = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).scalars().all()
    assert "research_publication_bundle_jobs" in names


def test_report_wiring_keeps_unpublished_action_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit wiring only: signed-source/DB readers are seams, not authenticators."""
    module, database, wire = _unit_reader(monkeypatch)
    result = OBJECT.validate_python(
        module.PerpetualSavedReportQuery(database, now=lambda: datetime.now(UTC)).read(
            wire["owner_id"], wire["run_id"]
        )
    )
    assert result["action_publication"] == {
        "schema_version": "perpetual-saved-action.v1",
        "status": "unavailable",
        "reason_code": "action_publication_not_saved",
    }
    assert result["source_json"] == wire["source_json"]
    assert result["artifact_json"] == wire["artifact_json"]
    assert result["financial_qualified"] is result["execution_authorized"] is False


@dataclass(frozen=True)
class ProjectionCase:
    database: Database
    run_id: str
    originals: dict[str, dict[str, JsonValue]]
    assigned: datetime


@pytest.fixture
def publication(binding_case: dict[str, object]) -> ProjectionCase:
    """Real SQL/archived raw sources and pure derive; no live/native qualification claim."""
    value: object = importlib.import_module(
        "tests.decision_product.test_perpetual_advice_query"
    ).persisted_publication(binding_case)
    assert isinstance(value, DerivedPerpetualPublication)
    database = binding_case["db"]
    assert isinstance(database, Database)
    return ProjectionCase(
        database,
        _text(binding_case["run_id"]),
        {item.role: OBJECT.validate_json(item.body_json) for item in value.originals},
        value.bound.target.assigned_at,
    )


def _arguments(case: ProjectionCase, session: Session) -> dict[str, object]:
    identity: object = importlib.import_module(
        PREFIX + "perpetual_research_binding"
    ).read_perpetual_admission_identity_in_session(session, case.run_id)
    admission, reference = TypeAdapter(
        tuple[dict[str, JsonValue], dict[str, JsonValue]]
    ).validate_python(identity)
    derivation = case.originals["derivation"]
    synthesis = case.originals["native_synthesis"]
    return dict(
        run=session.get(RunRecord, case.run_id),
        admission=admission,
        admission_ref=reference,
        report_binding=dict(
            mapped_ref=derivation["mapped_original"],
            provider_emission_ref=derivation["provider_original"],
            synthesis_ref=derivation["native_synthesis_ref"],
        ),
        report_synthesis=synthesis,
        report_review_request=case.originals["review_request"],
        report_published_at=case.assigned - timedelta(seconds=1),
        read_at=case.assigned + timedelta(days=1),
    )


def _read(case: ProjectionCase, **changes: object) -> dict[str, JsonValue]:
    module = importlib.import_module(PREFIX + "perpetual_saved_action")
    with case.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        arguments = _arguments(case, session)
        arguments.update(changes)
        return OBJECT.validate_python(
            module.read_saved_perpetual_action_in_session(session, **arguments).model_dump(
                mode="json"
            )
        )


def test_real_originals_keep_exact_receipt_and_expired_comparison(
    publication: ProjectionCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("historical projection consulted current state")

    monkeypatch.setattr(
        importlib.import_module(PREFIX + "perpetual_condition_monitor"),
        "condition_new_exposure_blocked_in_session",
        forbidden,
    )
    monkeypatch.setattr(
        importlib.import_module(PREFIX + "event_scenario_publication"),
        "read_published_event_scenario_plan_in_session",
        forbidden,
    )
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: object,
    ) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(publication.database.engine, "before_cursor_execute", record)
    try:
        result = _read(publication)
        assert result["status"] == "available"
        assert result["comparison"] == publication.originals["comparison"]
        assert result["derivation"] == publication.originals["derivation"]
        receipt = OBJECT.validate_python(result["command_receipt"])
        assert result["accepted_at"] == receipt["accepted_at"] == publication.assigned.isoformat()
        assert result["financial_qualified"] is result["execution_authorized"] is False
        assert _read(publication) == result
        (tmp_path / "pure-persisted-saved-action.json").write_text(
            json.dumps(result, indent=2), "utf-8"
        )
    finally:
        event.remove(publication.database.engine, "before_cursor_execute", record)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}


@pytest.mark.parametrize(
    "change", ["synthesis_ref", "mapped_ref", "admission_ref", "published_at", "read_at"]
)
def test_cross_report_or_bad_publication_clock_is_rejected(
    publication: ProjectionCase, change: str
) -> None:
    with publication.database.session() as session:
        arguments = _arguments(publication, session)
    if change == "synthesis_ref":
        binding = OBJECT.validate_python(arguments["report_binding"])
        original = OBJECT.validate_python(binding["synthesis_ref"])
        binding["synthesis_ref"] = {"ref": "wrong", "hash": original["hash"]}
        arguments["report_binding"] = binding
    elif change == "mapped_ref":
        binding = OBJECT.validate_python(arguments["report_binding"])
        mapped = OBJECT.validate_python(binding["mapped_ref"])
        mapped["output_id"] = "foreign-mapped-output"
        binding["mapped_ref"] = mapped
        arguments["report_binding"] = binding
    elif change == "admission_ref":
        arguments["admission_ref"] = {"ref": "wrong-admission", "hash": "b" * 64}
    elif change == "published_at":
        arguments["report_published_at"] = publication.assigned + timedelta(seconds=1)
    else:
        arguments["read_at"] = publication.assigned - timedelta(seconds=1)
    arguments.pop("run")
    with pytest.raises(ValueError):
        _read(publication, **arguments)


def test_present_but_missing_comparison_is_never_unavailable(publication: ProjectionCase) -> None:
    comparison = publication.originals["comparison"]
    with publication.database.session() as session:
        session.execute(
            delete(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == publication.run_id,
                RunRoleOutputRecord.payload_hash
                == hashlib.sha256(
                    json.dumps(
                        comparison,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
            )
        )
    with pytest.raises(ValueError, match="original_unavailable"):
        _read(publication)


def test_lawful_native_report_projects_its_own_action(
    binding_case: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Existing authenticated synthetic native pipeline; no real Provider/Host."""
    native = importlib.import_module("tests.decision_product.test_perpetual_native_graph")

    def authenticate(prepared: object) -> None:
        action.authenticate_provider(prepared, monkeypatch)

    value: object = native.build_perpetual_native_graph_case(
        binding_case,
        tmp_path,
        monkeypatch,
        packet_builder=action.lawful_packet_for,
        before_graph=authenticate,
        phased=True,
    )
    path = tmp_path / "lawful-native-saved-action-report.json"
    receipt, _advice = action.assert_actual_report_then_unique_perpetual_action(
        value,
        monkeypatch,
        saved_report_example_path=path,
    )
    wire = OBJECT.validate_json(path.read_text("utf-8"))
    projection = OBJECT.validate_python(wire["action_publication"])
    assert projection["status"] == "available"
    assert projection["command_receipt"] == receipt.wire()
    assert projection["financial_qualified"] is projection["execution_authorized"] is False
