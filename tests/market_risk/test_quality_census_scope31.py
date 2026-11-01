"""One draft joint: real complete provider/lifecycle originals, private SQLite.

The quality publication/Graph projection is an explicit seam. The real quality
entry, its random marker, original usage auth and all provider writers execute.
This does not authenticate a publication, financial report, old Run or latency.
NOT EXECUTED when prepared; Root owns the finite execution window.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    ResearchSynthesisCandidate,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)
from packages.kernel.decision_hub_kernel.application import research_provider_usage as usage
from packages.kernel.decision_hub_kernel.application import research_quality_view as quality
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.native_conversation_census import (
    NativeConversationCensusService,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    read_mapped_observation,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from tests.dsh_native.test_model_dispatch_v2 import _payload
from tests.dsh_native.test_native_conversation_census import census, controls
from tests.dsh_native.test_native_conversation_census import usage_active as usage_fixture
from tests.dsh_native.test_provider_input_receipts import _mapped, finish
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


@pytest.fixture
def originals(tmp_path: Any, request: Any):
    maker = usage_fixture.__wrapped__(tmp_path, request)
    prepared, payload = next(maker)
    try:
        provider = ProviderInputReceiptService(prepared.database)
        root_input = provider.record_input(payload)
        provider.record_finish(finish(payload, root_input))
        child = "scope-independent-repair-child"
        ModelDispatchService(prepared.database).bind(_payload(prepared).model_copy(update={
            "execution_session_id": child, "parent_session_id": payload.root_session_id,
        }))
        previous = None
        for index in range(2):
            child_input = payload.model_copy(update={
                "execution_session_id": child, "parent_session_id": payload.root_session_id,
                "dispatch_id": f"scope-child:{index}", "previous_input_ref": previous,
            })
            saved = provider.record_input(child_input)
            provider.record_finish(finish(child_input, saved))
            previous = ResearchProviderInputRef(output_id=saved.reference.output_id,
                                               payload_hash=saved.reference.payload_hash)
        NativeConversationCensusService(prepared.database).record(census(payload, child=child))
        candidate = ResearchSynthesisCandidate.model_validate(dict(
            schema_version="research-synthesis-candidate.v1",
            request_id=prepared.request.request_id,
            causal_case=_causal_case(thesis="Controlled independent usage.",
                                    unresolved_questions=[]), horizons=_horizons(),
        ))
        mapped_ref = _mapped(prepared, candidate, {"candidate": candidate.model_dump(mode="json")},
                             preceding_events=controls(payload.root_session_id))
        with prepared.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            mapped = read_mapped_observation(session, mapped_ref)
            reply = read_transport_observation(session, mapped.mapping_input_ref).reply
        DshSessionLinkService(prepared.database).complete(DshSessionCompletion(
            schema_version="dsh-session-completion.v1", run_id=payload.run_id,
            dsh_session_id=payload.root_session_id, generation=1, terminal_status="completed",
            last_seq=reply.last_seq, trace_ref=reply.trace_ref,
            result_ref=f"hub://runs/{payload.run_id}/result", result_hash=reply.result_hash,
            completed_at=reply.finished_at, error=None,
        ), authenticated_host=True)
        yield prepared, mapped_ref, utcnow(), root_input.reference
    finally:
        maker.close()


def test_quality_entry_real_census_first_hit_exit_and_mutation_closed(
    originals: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    prepared, mapped, at, input_ref = originals
    with prepared.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        expected = usage.read_provider_run_usage_in_session(session, mapped_ref=mapped, as_of=at)
    assert expected.coverage_status == "complete" and expected.expected_dispatches == 3
    assert expected.root_dispatches == 1 and expected.child_dispatches == 2
    assert expected.total_tokens == 30 and expected.cost_usd is None
    assert not expected.financial_qualified and not expected.budget_qualified

    mode, samples = "positive", []
    calls = {"original": 0, "fresh_witness": 0}
    selected_codes = {
        usage._read_provider_run_usage_original_in_session.__wrapped__.__code__: "original",
        usage._fresh_usage_witness.__code__: "fresh_witness",
    }

    def count(frame: Any, event: str, _arg: Any) -> None:
        if event == "call" and frame.f_code in selected_codes:
            calls[selected_codes[frame.f_code]] += 1

    def publication_seam(session: Any, run_id: str, *, as_of: Any, consume: Any) -> Any:
        # Only the Graph/publication projection is a seam. No usage reader,
        # terminal, native census, model binding or provider original is mocked.
        assert run_id == prepared.request.run_id and as_of == at
        for index in range(6):
            if index == 1 and mode == "raw_transaction":
                native = session.connection().connection.driver_connection
                native.execute("COMMIT")
                native.execute("BEGIN")
            if index == 1 and mode == "source":
                source = tmp_path / "usage-original-source.py"
                source.write_bytes(b"changed original bytes")
                monkeypatch.setattr(usage, "__file__", str(source))
            samples.append(usage.read_provider_run_usage_in_session(
                session, mapped_ref=mapped, as_of=at
            ))
        usage.verify_provider_usage_census_in_session(session, expected)
        if mode == "exit_material":
            scope = usage._USAGE_READ_SCOPE.get()
            key = next(iter(scope.values))
            material = json.loads(scope.values[key])
            material["witness"]["prompts"][0][3] = "altered-prompt-id"
            scope.values[key] = observation_json(material)
        view = quality.ResearchQualityViewV1(run_id=run_id, as_of=as_of,
            status="unavailable", reason_code="controlled_publication_projection_seam")
        return consume(view, mapped)

    monkeypatch.setattr(quality, "_read_published_research_quality_in_session", publication_seam)
    prior_profile = sys.getprofile()
    sys.setprofile(count)
    try:
        with prepared.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            native = session.connection().connection.driver_connection
            before = native.total_changes
            result = quality.consume_published_research_originals_in_session(
                session, prepared.request.run_id, as_of=at,
                consume=lambda _view, _mapped: samples[-1],
            )
            assert result == expected and all(item == expected for item in samples)
            assert samples[0] is not samples[1]
            assert native.total_changes == before and not (session.new or session.dirty)
        assert calls == {"original": 2, "fresh_witness": 8}
    finally:
        sys.setprofile(prior_profile)
    assert usage._USAGE_READ_SCOPE.get() is None
    positive_calls = dict(calls)

    original_source = usage.__file__
    for mode in ("raw_transaction", "source", "exit_material"):
        samples.clear()
        try:
            with prepared.database.session() as session:
                session.connection().exec_driver_sql("BEGIN")
                with pytest.raises(ValueError, match="research_usage_(?:read_scope_changed|frozen_computation_changed)"):
                    quality.consume_published_research_originals_in_session(
                        session, prepared.request.run_id, as_of=at,
                        consume=lambda _view, _mapped: samples[-1],
                    )
            if mode in {"raw_transaction", "source"}:
                assert len(samples) == 1  # No reused value escapes the broken boundary.
            assert usage._USAGE_READ_SCOPE.get() is None
        finally:
            monkeypatch.setattr(usage, "__file__", original_source)

    mode = "auth_fallback"
    samples.clear(); calls.update(original=0, fresh_witness=0)
    sys.setprofile(count)
    try:
        with prepared.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            native = session.connection().connection.driver_connection
            def authorizer(action: int, *_args: Any) -> int:
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_SAVEPOINT else sqlite3.SQLITE_OK
            native.set_authorizer(authorizer)
            try:
                assert quality.consume_published_research_originals_in_session(
                    session, prepared.request.run_id, as_of=at,
                    consume=lambda _view, _mapped: samples[-1],
                ) == expected
                assert usage._USAGE_READ_SCOPE.get() is None
            finally:
                native.set_authorizer(None)
        assert calls["original"] == 7 and calls["fresh_witness"] == 0
    finally:
        sys.setprofile(prior_profile)

    # Real persisted row-byte alteration, before a new clean read, remains an
    # original SHA refusal. Neither parsing memo nor stale metadata repairs it.
    with prepared.database.session() as session:
        row = session.get(RunRoleOutputRecord, input_ref.output_id)
        original_payload = row.payload_json
        row.payload_json = original_payload + " "
    try:
        with prepared.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with pytest.raises(ValueError, match="research_provider_original_hash_invalid"):
                with usage.provider_usage_read_scope(session):
                    usage.read_provider_run_usage_in_session(session, mapped_ref=mapped, as_of=at)
    finally:
        with prepared.database.session() as session:
            session.get(RunRoleOutputRecord, input_ref.output_id).payload_json = original_payload
    assert usage._USAGE_READ_SCOPE.get() is None
    (tmp_path / "census-scope-proof.json").write_text(json.dumps({
        "expected_census_hash": expected.census_hash, "positive_counts": positive_calls,
        "original_payload_sha": hashlib.sha256(original_payload.encode()).hexdigest(),
        "quality_publication_projection": "explicit seam; not full publication authentication",
        "performance": "not measured; original call counts only",
    }), encoding="utf-8")
