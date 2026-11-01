"""Versioned historical-data delivery; selection is not a model-use receipt."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from candidate_perpetual_forecast_support import ROOT, load
from test_perpetual_forecast_scoring_store import service_module


def learning_module() -> Any:
    service_module()
    return load(
        "packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_inputs.py"
    )


def test_v2_sample_gets_explicit_selection_v3_without_claiming_consumption(monkeypatch: Any):
    module = learning_module()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    admission = {"context": {"horizon": "24h"}, "admitted_at": now.isoformat()}

    def identity(*_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return admission, {}

    monkeypatch.setattr(module, "read_perpetual_admission_identity_in_session", identity)
    evidence = SimpleNamespace(
        evidence_id="original-ref",
        content_hash="a" * 64,
        source_id="perpetual_forecast_experience.v2",
    )

    def dump(**_: Any) -> dict[str, str]:
        return {"run_id": "next-run"}

    request = SimpleNamespace(
        run_id="next-run",
        request_id="next-request",
        input_evidence=[evidence],
        model_dump=dump,
    )
    rows = []
    module.freeze_experience_selection_in_session(
        SimpleNamespace(add=rows.append), request, [evidence], recorded_at=now
    )
    assert len(rows) == 1
    row = rows[0]
    body = json.loads(row.payload_json)
    assert row.role == "perpetual_experience_task_selection_v3"
    assert body["schema_version"] == "perpetual-experience-task-selection.v3"
    assert body["strategy"] == "perpetual-history-inputs.v3"
    assert body["selected"] == [
        {"ref": "original-ref", "hash": "a" * 64, "kind": "forecast_terminal_direction_v2"}
    ]
    assert body["skill_consumed"] is False and body["provider_input_proven"] is False


def test_original_worker_uses_versioned_facade_at_both_prepare_and_freeze():
    learning_module()
    source = (ROOT / "apps/hub_worker/research.py").read_text(encoding="utf-8")
    assert "perpetual_forecast_scoring_inputs.prepare_perpetual_experience_inputs(" in source
    assert "perpetual_forecast_scoring_inputs.freeze_experience_selection_in_session(" in source


def test_invalid_newest_selection_cannot_fall_back_to_older_version():
    learning_module()
    module = load("packages/kernel/decision_hub_kernel/application/perpetual_experience_use.py")
    row = SimpleNamespace(payload_json="{}")
    visits = []

    class Session:
        def get(self, _: Any, address: Any, **kwargs: Any):
            visits.append(address)
            return row if "selection_v3_" in address else None

    request = SimpleNamespace(run_id="next-run", input_evidence=[])
    with pytest.raises((ValueError, KeyError, AttributeError)):
        module._read_selection(Session(), request)
    assert len(visits) == 1 and "selection_v3_" in visits[0]


def test_scoring_software_pins_include_actual_shared_truth_codec_primitives():
    module = service_module()
    assert "ports/perpetual_close.py" in module._software()
    assert module.METHOD["truth_adapter"] == "okx-perp-forecast-truth.v1"


def test_normal_use_queue_selects_new_v3_delivery_and_matches_its_receipt():
    from contextlib import contextmanager

    from test_perpetual_forecast_scoring_runtime import runtime_module

    module = runtime_module()
    statements = []

    def empty() -> list[Any]:
        return []

    class Session:
        def scalars(self, statement: Any):
            statements.append(statement.compile().params)
            return SimpleNamespace(all=empty)

    @contextmanager
    def session():
        yield Session()

    database = SimpleNamespace(session=session)
    runtime = module.PerpetualForecastRuntime(
        database, SimpleNamespace(database=database), scoring_version="v2"
    )
    assert runtime.process_uses() == 0
    values = repr(statements)
    assert "perpetual_experience_task_selection_v3" in values
    assert "perpetual_experience_provider_use_v3:" in values


def test_legacy_due_queue_cannot_create_a_second_version_after_v2_scoring():
    """Queue-filter-only in-memory rows; no claim of authenticated business originals."""
    from test_perpetual_forecast_scoring_runtime import runtime_module

    module = runtime_module()
    from packages.kernel.decision_hub_kernel.persistence.db import (
        Database,
        RunRecord,
        RunRoleOutputRecord,
    )

    at = datetime(2026, 1, 2, tzinfo=UTC)
    database = Database("sqlite:///:memory:")
    database.create_all()
    try:
        with database.session() as session:
            session.add(
                RunRecord(
                    run_id="filter-only",
                    event_id="filter-only",
                    status="completed",
                    created_at=at,
                    updated_at=at,
                )
            )
            session.add(
                RunRoleOutputRecord(
                    output_id="target-filter-only",
                    run_id="filter-only",
                    role="perpetual_publication_original:target:filter-only",
                    schema_version="publication-bound-target.v1",
                    payload_json='{"horizon":"4h","window_end":"2026-01-01T04:00:00Z"}',
                    payload_hash="a" * 64,
                    created_at=at,
                )
            )
            session.add(
                RunRoleOutputRecord(
                    output_id="score-filter-only",
                    run_id="filter-only",
                    role="perpetual_forecast_experience_v2",
                    schema_version="perpetual-forecast-experience.v2",
                    payload_json="{}",
                    payload_hash="b" * 64,
                    created_at=at,
                )
            )
        runtime = module.PerpetualForecastRuntime(
            database, SimpleNamespace(database=database), clock=lambda: at
        )
        assert runtime._due_runs() == ()
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("mutation", ["uses", "skill_consumed", "row_run", "row_clock", None])
def test_v3_prior_replays_original_facts_at_its_original_recorded_time(
    monkeypatch: Any,
    mutation: Any,
):
    """Pure in-memory prior-branch counterexample; first derive is a controlled seam."""
    from contextlib import contextmanager
    from copy import deepcopy
    from datetime import timedelta

    module = load("packages/kernel/decision_hub_kernel/application/perpetual_experience_use.py")
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import canonical
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

    at = datetime(2026, 1, 1, tzinfo=UTC)
    reference = ObservedExecutionRef(
        output_id="memory-emission",
        role="provider-emission",
        schema_version="research-provider-tool-emission.v1",
        payload_hash="a" * 64,
        recorded_at=at,
    )
    digest = canonical_hash(reference.model_dump(mode="json"))
    original: dict[str, Any] = dict(
        schema_version="perpetual-experience-provider-use.v3",
        run_id="memory-run",
        provider_emission_ref=reference.model_dump(mode="json"),
        recorded_at=at.isoformat(),
        uses=[dict(status="not_referenced", consumed_as_historical_data=False)],
        skill_consumed=False,
        financial_qualified=False,
    )
    body = deepcopy(original)
    if mutation == "uses":
        body["uses"][0].update(status="referenced", consumed_as_historical_data=True)
    if mutation == "skill_consumed":
        body["skill_consumed"] = True
    row = RunRoleOutputRecord(
        output_id="perp_experience_use_v3_" + digest,
        run_id="wrong-run" if mutation == "row_run" else "memory-run",
        role="perpetual_experience_provider_use_v3:" + digest,
        schema_version=body["schema_version"],
        payload_json=canonical(body),
        payload_hash=canonical_hash(body),
        created_at=at + timedelta(seconds=1) if mutation == "row_clock" else at,
    )
    calls = []
    state: dict[str, Any] = dict(live=0, snapshot=False, prepared=0)

    def derive(session: Any, ref: Any, *, cutoff_at: Any) -> Any:
        assert ref == reference
        if not state["snapshot"]:
            raise ValueError("research_provider_physical_snapshot_required")
        calls.append(cutoff_at)
        return original

    monkeypatch.setattr(module, "derive_experience_use_in_session", derive)

    class Session:
        def get(self, kind: Any, address: Any, **kwargs: Any) -> Any:
            return row if address == row.output_id else None

    @contextmanager
    def session():
        state["live"] += 1
        try:
            yield Session()
        finally:
            state["live"] -= 1

    def prepare(database: Any, read: Any, **options: Any) -> Any:
        assert state["live"] == 0
        assert options["link_activity_observations"] and options["link_tool_counter_observations"]
        state["prepared"] += 1
        state["snapshot"] = True
        try:
            return SimpleNamespace(value=read(Session()))
        finally:
            state["snapshot"] = False

    monkeypatch.setattr(module, "prepare_originals", prepare)

    database = SimpleNamespace(session=session)
    if mutation is None:
        assert (
            module.record_experience_use(database, reference, clock=lambda: at + timedelta(days=1))
            == original
        )
        assert calls == [at]
    else:
        with pytest.raises(ValueError, match="perpetual_experience_use_original_invalid"):
            module.record_experience_use(database, reference, clock=lambda: at + timedelta(days=1))
    assert state["prepared"] == 1


def test_v3_concurrent_existing_receipt_returns_authenticated_stored_original(monkeypatch: Any):
    """Pure two-session race: prepare at B cannot claim an unstored B receipt over A."""
    from contextlib import contextmanager
    from datetime import timedelta

    module = load("packages/kernel/decision_hub_kernel/application/perpetual_experience_use.py")
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import canonical
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

    at = datetime(2026, 1, 1, tzinfo=UTC)
    reference = ObservedExecutionRef(
        output_id="race-emission",
        role="provider-emission",
        schema_version="research-provider-tool-emission.v1",
        payload_hash="a" * 64,
        recorded_at=at,
    )
    digest = canonical_hash(reference.model_dump(mode="json"))
    stored = dict(
        schema_version="perpetual-experience-provider-use.v3",
        run_id="race-run",
        provider_emission_ref=reference.model_dump(mode="json"),
        recorded_at=at.isoformat(),
        uses=[],
        skill_consumed=False,
        financial_qualified=False,
    )
    row = RunRoleOutputRecord(
        output_id="perp_experience_use_v3_" + digest,
        run_id="race-run",
        role="perpetual_experience_provider_use_v3:" + digest,
        schema_version=stored["schema_version"],
        payload_json=canonical(stored),
        payload_hash=canonical_hash(stored),
        created_at=at,
    )
    visits, replayed = [], []
    state: dict[str, Any] = dict(live=0, snapshot=False, prepared=0)

    class Session:
        def __init__(self, number: int):
            self.number = number

        def get(self, kind: Any, address: Any, **kwargs: Any) -> Any:
            return row if self.number >= 2 and address == row.output_id else None

        def add(self, value: Any) -> None:
            raise AssertionError("existing receipt must not be rewritten")

    @contextmanager
    def session():
        visits.append(1)
        state["live"] += 1
        try:
            yield Session(len(visits))
        finally:
            state["live"] -= 1

    def no_op(*_: Any) -> None:
        pass

    def derive(session: Any, ref: Any, *, cutoff_at: Any) -> Any:
        if not state["snapshot"]:
            raise ValueError("research_provider_physical_snapshot_required")
        replayed.append(cutoff_at)
        return stored

    prepared = SimpleNamespace(
        value={**stored, "recorded_at": (at + timedelta(seconds=1)).isoformat()},
        verify=no_op,
        verify_after_writes=no_op,
    )

    def prepare(database: Any, read: Any, **options: Any) -> Any:
        assert state["live"] == 0
        state["prepared"] += 1
        if state["prepared"] == 1:
            return prepared
        assert options["link_activity_observations"] and options["link_tool_counter_observations"]
        state["snapshot"] = True
        try:
            return SimpleNamespace(value=read(Session(3)))
        finally:
            state["snapshot"] = False

    monkeypatch.setattr(module, "prepare_originals", prepare)
    monkeypatch.setattr(module, "begin_immediate", no_op)
    monkeypatch.setattr(module, "derive_experience_use_in_session", derive)
    result = module.record_experience_use(
        SimpleNamespace(session=session), reference, clock=lambda: at + timedelta(seconds=1)
    )
    assert result == stored and replayed == [at]
