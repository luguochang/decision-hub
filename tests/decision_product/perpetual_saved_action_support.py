"""Explicit UNIT seams only; none of these fixtures authenticates a saved report."""

from __future__ import annotations

import importlib
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import JsonValue, TypeAdapter

PREFIX = "packages.kernel.decision_hub_kernel.application."
OBJECT = TypeAdapter(dict[str, JsonValue])


def _install_unit_seams(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    admission: dict[str, JsonValue],
    wire: dict[str, JsonValue],
    artifact: dict[str, JsonValue],
    at: datetime,
) -> None:
    def identity(*_args: object) -> object:
        return admission, {"ref": "unit-admission", "hash": "a" * 64}

    def empty(*_args: object, **_kwargs: object) -> object:
        return {}

    def declared(_text: object) -> None:
        return None

    def final(_ref: object) -> object:
        return SimpleNamespace(row=SimpleNamespace(recorded_at=at))

    def saved(*_args: object, **_kwargs: object) -> object:
        return wire["artifact_id"], None

    def artifact_body(_artifact: object) -> object:
        return artifact

    monkeypatch.setattr(module, "read_perpetual_admission_identity_in_session", identity)
    monkeypatch.setattr(
        module, "ResearchSessionResult", SimpleNamespace(model_validate_json=declared)
    )
    monkeypatch.setattr(module, "parse_graph_final_ref", final)
    monkeypatch.setattr(module, "_probability_definitions_view", empty)
    commit = importlib.import_module(PREFIX + "commit")
    monkeypatch.setattr(commit, "_read_publication_source_in_session", saved)
    monkeypatch.setattr(commit, "_artifact_body", artifact_body)
    monkeypatch.setattr(
        importlib.import_module(PREFIX + "perpetual_research_reader"),
        "read_perpetual_probability_definitions_in_session",
        empty,
    )


def text_value(value: object) -> str:
    assert isinstance(value, str)
    return value


class UnitSession:
    def __init__(self, module: ModuleType, run: object, row: object) -> None:
        self.module, self.run, self.row = module, run, row

    def connection(self) -> UnitSession:
        return self

    def exec_driver_sql(self, _sql: str) -> None:
        return None

    def get(self, kind: object, _key: object, **_kwargs: object) -> object:
        if kind is self.module.RunRecord:
            return self.run
        if kind is self.module.ResearchResultRecord:
            return SimpleNamespace(payload_json="{}")
        return SimpleNamespace() if kind is self.module.ArtifactRecord else None

    def scalar(self, _query: object) -> object:
        return self.row


class UnitDatabase:
    def __init__(self, session: UnitSession) -> None:
        self.value = session

    @contextmanager
    def session(self) -> Generator[UnitSession]:
        yield self.value


def unit_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, UnitDatabase, dict[str, JsonValue]]:
    module = importlib.import_module(PREFIX + "perpetual_saved_report")
    wire = OBJECT.validate_json(
        (
            Path(__file__).parents[2] / "artifacts/d2-perpetual-saved-report/"
            "available-actual-v5-example.json"
        ).read_text("utf-8")
    )
    source = OBJECT.validate_json(text_value(wire["source_json"]))
    artifact = OBJECT.validate_json(text_value(wire["artifact_json"]))
    packet = OBJECT.validate_python(
        OBJECT.validate_python(source["candidate"])["perpetual_synthesis"]
    )
    primary = OBJECT.validate_python(packet["synthesis"])
    primary = OBJECT.validate_python(primary.get("primary_v2", primary))
    at = datetime.fromisoformat(text_value(wire["published_at"]))
    run = module.RunRecord(
        run_id=wire["run_id"],
        event_id=wire["event_id"],
        artifact_id=wire["artifact_id"],
        finished_at=at,
    )
    row = module.RunRoleOutputRecord(
        run_id=wire["run_id"],
        payload_json=wire["source_json"],
        payload_hash=wire["source_sha256"],
        schema_version=module.SOURCE_SCHEMA,
        created_at=at,
        output_id=OBJECT.validate_python(wire["publication_ref"])["output_id"],
        role=module.SOURCE_ROLE,
    )
    admission = {
        "owner_id": wire["owner_id"],
        "opportunity_id": primary["opportunity_id"],
        "context": OBJECT.validate_python(primary["candidate_set"])["context"],
        "settings_revision": {},
    }

    _install_unit_seams(module, monkeypatch, admission, wire, artifact, at)
    return module, UnitDatabase(UnitSession(module, run, row)), wire
