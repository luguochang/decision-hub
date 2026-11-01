from __future__ import annotations

from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import FactEnvelope
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from tools.dsh_native_acceptance import (
    ScenarioResult,
    ScenarioRuntime,
    assert_scenario,
    find_session_log,
    replay_rounds,
    research_archive,
)


def _result(**overrides: object) -> ScenarioResult:
    values: dict[str, object] = {
        "scenario": "success",
        "run_id": "run-test",
        "dsh_session_id": "dsh_" + "a" * 64,
        "run_status": "completed",
        "gate_status": "publish",
        "coverage_status": "sufficient",
        "hard_coverage_ratio": 1.0,
        "stop_reason": "sufficient",
        "evidence_count": 9,
        "tool_failure_count": 0,
        "session_log": "/tmp/session.jsonl.zstd",
        "session_log_sha256": "b" * 64,
        "output_dir": "/tmp/acceptance",
    }
    values.update(overrides)
    return ScenarioResult(**values)  # type: ignore[arg-type]


def test_compressed_official_session_log_is_discovered_without_rewriting(tmp_path: Path) -> None:
    session_id = "dsh_" + "a" * 64
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    expected = session_dir / "session.jsonl.zstd"
    expected.write_bytes(b"official-compressed-session")

    assert find_session_log(tmp_path, session_id) == expected


def test_session_log_discovery_fails_closed_on_duplicate_history(tmp_path: Path) -> None:
    session_id = "dsh_" + "a" * 64
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    (session_dir / "session.jsonl").write_text("{}\n")
    (session_dir / "session.jsonl.zstd").write_bytes(b"compressed")

    with pytest.raises(RuntimeError, match="expected one DSH JSONL"):
        find_session_log(tmp_path, session_id)


def test_acceptance_summary_rejects_a_false_success() -> None:
    assert_scenario(_result())

    with pytest.raises(AssertionError):
        assert_scenario(_result(gate_status="reject"))


def test_success_fixture_contains_all_required_authority_classes() -> None:
    archive = research_archive("success")
    queries = archive["queries"]
    assert isinstance(queries, list)
    candidates = [candidate for query in queries for candidate in query["evidence_candidates"]]

    assert len(candidates) == 9
    assert {candidate["authority"] for candidate in candidates} == {
        "official",
        "exchange",
        "verified_web",
    }


def test_success_fixture_facts_use_canonical_payload_and_instance_hashes() -> None:
    archive = research_archive("success")
    for query in archive["queries"]:
        for raw_fact in query.get("facts", []):
            fact = FactEnvelope.model_validate(raw_fact)
            payload_hash = research_fact_payload_hash(
                requirement_id=fact.requirement_id,
                metric_family=fact.metric_family,
                field=fact.field,
                instrument=fact.instrument,
                venue=fact.venue,
                value=fact.value,
                unit=fact.unit,
                window_start_at=fact.window_start_at,
                window_end_at=fact.window_end_at,
                event_offset=fact.event_offset,
                published_at=fact.published_at,
                source_id=fact.source_id,
                independence_group=fact.independence_group,
                delay_class=fact.delay_class,
                payload_schema_ref=fact.payload_schema_ref,
                attributes=fact.attributes,
            )
            assert fact.payload_hash == payload_hash
            assert fact.fact_id == research_fact_instance_id(
                evidence_id=fact.evidence_id, payload_hash=payload_hash
            )


def test_worker_composition_is_explicitly_dsh_web_replay(tmp_path: Path) -> None:
    runtime = ScenarioRuntime("success", tmp_path)

    env = runtime.worker_env()

    assert env["DECISION_HUB_RESEARCH_RUNTIME"] == "dsh-web"
    assert env["DECISION_HUB_RESEARCH_EXECUTION_MODE"] == "replay"
    assert env["DECISION_HUB_RESEARCH_CAPABILITIES"] == "replay.research"


def test_acceptance_workspace_uses_the_product_name(tmp_path: Path) -> None:
    runtime = ScenarioRuntime("success", tmp_path)

    assert runtime.workspace.name == "Crypto Macro Trader"


def test_partial_failure_fixture_records_two_evidence_rounds() -> None:
    assert replay_rounds("partial_failure") == (
        ("event_identity", "policy_or_data_delta"),
        ("expectation_pricing",),
    )
