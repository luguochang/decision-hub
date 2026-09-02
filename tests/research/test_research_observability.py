# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest
from sqlalchemy.orm import Session

from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    EvidenceCandidate,
    ResearchRunCommand,
    ResearchSessionResult,
    ResearchTraceEvent,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    ResearchCommandRecord,
    ResearchResultRecord,
    ResearchTraceRecord,
)
from packages.provider_adapters.research import CryptoMacroFactPack
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    FakeResearchRuntime,
    ResearchRequestFactory,
    _admitted_run,
    _candidate,
    _result,
)


def _trace(run_id: str, *, event_type: str, summary: str) -> ResearchTraceEvent:
    return ResearchTraceEvent.model_validate(
        {
            "schema_version": "research-trace-event.v1",
            "run_id": run_id,
            "research_session_id": f"product:{run_id}",
            "sequence_no": 0,
            "event_type": event_type,
            "occurred_at": NOW.isoformat(),
            "stage": "planning",
            "summary": summary,
            "reference_type": "run",
            "reference_id": run_id,
            "status": "running",
            "error_code": None,
        }
    )


def test_normalized_trace_is_idempotent_and_allocates_run_sequence(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    service = ResearchObservabilityService(database, clock=lambda: NOW)

    first = service.append_trace(_trace(run_id, event_type="session_started", summary="Started"))
    duplicate = service.append_trace(
        _trace(run_id, event_type="session_started", summary="Started")
    )
    second = service.append_trace(_trace(run_id, event_type="plan_created", summary="Plan ready"))

    assert first.sequence_no == 1
    assert duplicate.sequence_no == 1
    assert second.sequence_no == 2
    assert [item.sequence_no for item in service.list_trace(run_id)] == [1, 2]
    assert [item.sequence_no for item in service.list_trace(run_id, after=1)] == [2]
    with database.session() as session:
        assert session.query(ResearchTraceRecord).filter_by(run_id=run_id).count() == 2


def test_trace_error_provenance_survives_durable_projection(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    service = ResearchObservabilityService(database, clock=lambda: NOW)
    event = _trace(run_id, event_type="tool_failed", summary="Search failed")
    event = event.model_copy(
        update={
            "status": "failed",
            "error_code": "research_capability_timeout",
            "error": ErrorProvenance(
                error_code="research_capability_timeout",
                origin="transport",
                cause_code="deadline",
                capability_id="market.cross_asset",
                tool_call_id="call-1",
                retryable=True,
                deadline_ms=20_000,
            ),
        }
    )

    service.append_trace(event)
    persisted = service.list_trace(run_id)[0]

    assert persisted.error is not None
    assert persisted.error.origin == "transport"
    assert persisted.error.capability_id == "market.cross_asset"


def test_research_result_is_committed_with_ledger_and_query_view(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request)
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    asyncio.run(
        observability.emit(_trace(run_id, event_type="session_started", summary="Started"))
    )

    first_artifact, _ = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )
    second_artifact, _ = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )

    assert second_artifact == first_artifact
    with database.session() as session:
        assert session.query(ResearchResultRecord).filter_by(run_id=run_id).count() == 1
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert detail is not None
    assert detail.run.runtime_id == result.runtime_id
    assert detail.run.current_round == 1
    assert detail.run.latest_sequence_no == 1
    assert detail.trigger_snapshot is not None
    assert detail.decision_snapshot is None
    assert detail.rounds == result.rounds
    assert detail.causal_case == result.causal_case
    assert {item.horizon for item in detail.horizons} == {"30m", "24h", "72h"}
    assert detail.scheduled_recheck_at == NOW + timedelta(minutes=10)


def test_partial_progress_remains_visible_without_final_result(tmp_path: Path) -> None:
    """A failed model step must not hide completed capabilities or earlier failures."""

    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    candidate = EvidenceCandidate.model_validate(_candidate(request)).model_copy(
        update={
            "tool_call_id": "call-official",
            "research_session_id": "dsh-session-partial",
        }
    )
    requirements = {
        item.requirement_id: item
        for item in CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements()
    }
    ResearchEvidenceService(database).accept_candidates(
        run_id=run_id,
        capability_id="official.macro",
        candidates=[candidate],
        requirements=requirements,
        cutoff_at=NOW,
    )
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    observability.append_trace(
        _trace(run_id, event_type="tool_started", summary="Market capability started").model_copy(
            update={
                "research_session_id": "dsh-session-partial",
                "reference_type": "capability_call",
                "reference_id": "call-market",
            }
        )
    )
    failed = _trace(
        run_id, event_type="tool_failed", summary="Market capability failed"
    ).model_copy(
        update={
            "research_session_id": "dsh-session-partial",
            "reference_type": "capability_call",
            "reference_id": "call-market",
            "status": "failed",
            "error_code": "research_capability_timeout",
            "error": ErrorProvenance(
                error_code="research_capability_timeout",
                origin="transport",
                cause_code="deadline",
                capability_id="market.cross_asset",
                tool_call_id="call-market",
                retryable=True,
                deadline_ms=20_000,
            ),
        }
    )
    observability.append_trace(failed)
    observability.append_trace(
        _trace(run_id, event_type="tool_started", summary="Official capability started").model_copy(
            update={
                "research_session_id": "dsh-session-partial",
                "reference_type": "capability_call",
                "reference_id": "call-official",
            }
        )
    )
    observability.append_trace(
        _trace(
            run_id, event_type="tool_completed", summary="A later capability completed"
        ).model_copy(
            update={
                "research_session_id": "dsh-session-partial",
                "reference_type": "capability_call",
                "reference_id": "call-official",
                "status": "succeeded",
            }
        )
    )
    observability.append_trace(
        _trace(
            run_id,
            event_type="tool_started",
            summary="The research session called decision_hub_research.",
        ).model_copy(
            update={
                "research_session_id": "dsh-session-partial",
                "reference_type": "dsh_tool_call",
                "reference_id": "call-native-wrapper",
            }
        )
    )
    observability.append_trace(
        _trace(
            run_id,
            event_type="tool_failed",
            summary="The decision_hub_research call failed.",
        ).model_copy(
            update={
                "research_session_id": "dsh-session-partial",
                "reference_type": "dsh_tool_call",
                "reference_id": "call-native-wrapper",
                "status": "failed",
                "error_code": "dsh_tool_failed",
                "error": ErrorProvenance(
                    error_code="dsh_tool_failed",
                    origin="mcp",
                    cause_code="decision_hub_research",
                    capability_id=None,
                    tool_call_id="call-native-wrapper",
                    retryable=False,
                    deadline_ms=None,
                ),
            }
        )
    )

    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert detail is not None
    assert detail.total_tool_calls == 2
    assert len(detail.evidence) == 1
    assert detail.run.current_round == 1
    assert detail.run.runtime_id == "dsh"
    assert detail.run.coverage is not None
    assert detail.run.coverage.gaps
    assert "policy_or_data_delta" in {
        item.requirement_id for item in detail.run.coverage.gaps
    }
    assert len(detail.rounds) == 1
    assert {
        item.capability_id for item in detail.rounds[0].tool_invocations
    } == {"market.cross_asset", "official.macro"}
    assert detail.run.failure is not None
    assert detail.run.failure.error_code == "research_capability_timeout"

    business = ResearchQueryService(database, pack_root=PACK_ROOT).business_status(run_id)
    assert business is not None
    assert business.coverage_status == "insufficient"
    assert business.hard_coverage_ratio == pytest.approx(1 / 6)
    assert [item.error_code for item in business.failures] == ["research_capability_timeout"]
    assert business.failures[0].origin == "transport"


def test_failed_synthesis_projects_durable_round_and_real_hard_gaps(tmp_path: Path) -> None:
    """A rejected synthesis must not erase the trusted work that preceded it."""

    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    candidate = EvidenceCandidate.model_validate(_candidate(request)).model_copy(
        update={
            "tool_call_id": "call-official",
            "research_session_id": "dsh-session-failed-synthesis",
        }
    )
    requirements = {
        item.requirement_id: item
        for item in CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements()
    }
    ResearchEvidenceService(database).accept_candidates(
        run_id=run_id,
        capability_id="official.macro",
        candidates=[candidate],
        requirements=requirements,
        cutoff_at=NOW,
    )
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    observability.append_trace(
        _trace(run_id, event_type="tool_started", summary="Official capability started").model_copy(
            update={
                "research_session_id": "dsh-session-failed-synthesis",
                "reference_type": "capability_call",
                "reference_id": "call-official",
            }
        )
    )
    observability.append_trace(
        _trace(
            run_id,
            event_type="tool_completed",
            summary="Official capability retained one trusted Evidence item",
        ).model_copy(
            update={
                "research_session_id": "dsh-session-failed-synthesis",
                "reference_type": "capability_call",
                "reference_id": "call-official",
                "status": "succeeded",
            }
        )
    )
    failure = ErrorProvenance(
        error_code="dsh_evidence_unattested",
        origin="orchestration",
        cause_code="synthesis_attestation",
        capability_id=None,
        tool_call_id=None,
        retryable=False,
        deadline_ms=None,
    )
    observability.append_trace(
        _trace(
            run_id,
            event_type="session_stopped",
            summary="Synthesis was rejected; trusted Evidence was retained.",
        ).model_copy(
            update={
                "research_session_id": "dsh-session-failed-synthesis",
                "stage": "done",
                "status": "failed",
                "error_code": failure.error_code,
                "error": failure,
            }
        )
    )
    RunService(database, clock=lambda: NOW).set_status(
        run_id,
        RunStatus.failed,
        error_code="dsh_evidence_unattested",
    )

    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert detail is not None
    assert detail.run.runtime_id == "dsh"
    assert detail.run.current_round == 1
    assert detail.total_tool_calls == 1
    assert len(detail.evidence) == 1
    assert len(detail.rounds) == 1
    assert len(detail.rounds[0].tool_invocations) == 1
    assert detail.rounds[0].tool_invocations[0].capability_id == "official.macro"
    assert detail.causal_case is None
    assert detail.horizons == []
    assert detail.run.stop_reason is not None
    assert detail.run.stop_reason.code == "critical_data_unavailable"
    assert detail.run.coverage is not None
    expected_hard_gaps = {
        item.requirement_id
        for item in detail.run.coverage.gaps
        if item.importance == "hard"
    }
    assert set(detail.run.stop_reason.remaining_hard_gaps) == expected_hard_gaps
    assert expected_hard_gaps
    assert detail.run.failure == failure


def test_durable_worker_persists_product_trace_result_and_decision_snapshot(
    tmp_path: Path,
) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    worker = DurableResearchWorker(database, FakeResearchRuntime(), pack_root=PACK_ROOT)

    report = asyncio.run(worker.tick())

    assert report is not None and report.artifact_id is not None
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert detail is not None
    assert detail.trigger_snapshot is not None
    assert detail.decision_snapshot is not None
    assert detail.run.latest_sequence_no == len(detail.trace)
    event_types = {item.event_type for item in detail.trace}
    assert {
        "session_started",
        "plan_created",
        "coverage_assessed",
        "evidence_accepted",
        "round_completed",
        "synthesis_started",
        "session_stopped",
    } <= event_types
    with database.session() as session:
        assert session.query(ResearchResultRecord).filter_by(run_id=run_id).count() == 1


def test_research_result_and_artifact_roll_back_as_one_transaction(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    commit = CommitDecisionService(database, clock=lambda: NOW)
    save = commit.research_observability.save_result_in_session

    def fail_after_result(
        session: Session,
        saved_run_id: str,
        result: ResearchSessionResult,
    ) -> NoReturn:
        save(session, saved_run_id, result)
        raise RuntimeError("inject_transaction_failure")

    commit.research_observability.save_result_in_session = fail_after_result  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="inject_transaction_failure"):
        commit.commit_research_result(run_id, event_id, _result(request))
    with database.session() as session:
        assert session.query(ResearchResultRecord).count() == 0
        assert session.query(ArtifactRecord).count() == 0


def test_owner_commands_are_idempotent_append_only_and_cancel_blocks_commit(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    commands = ResearchCommandService(database, clock=lambda: NOW)
    cancel = ResearchRunCommand(
        schema_version="research-run-command.v1",
        request_id="cancel-1",
        command="cancel",
        reason="Owner stopped an obsolete research task.",
    )

    accepted = commands.apply(run_id, cancel)
    duplicate = commands.apply(run_id, cancel)

    assert accepted.status == "accepted"
    assert duplicate.status == "already_applied"
    assert database.get_run_record(run_id).status == "cancelled"  # type: ignore[union-attr]
    with database.session() as session:
        assert session.query(ResearchCommandRecord).count() == 1

    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    with pytest.raises(PermissionError, match="research_run_cancelled"):
        CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
            run_id, event_id, _result(request)
        )
    with database.session() as session:
        assert session.query(ResearchResultRecord).count() == 0

    retry = commands.apply(
        run_id,
        ResearchRunCommand(
            schema_version="research-run-command.v1",
            request_id="retry-1",
            command="retry",
            reason="Retry after correcting the upstream capability.",
        ),
    )
    assert retry.status == "accepted"
    assert retry.target_run_id is not None
    child = database.get_run_record(retry.target_run_id)
    assert child is not None
    assert child.parent_run_id == run_id
    assert child.strategy_version == "research.v1"


def test_command_request_id_cannot_be_reused_for_different_intent(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    commands = ResearchCommandService(database, clock=lambda: datetime.now(UTC))
    commands.apply(
        run_id,
        ResearchRunCommand(
            schema_version="research-run-command.v1",
            request_id="owner-command-1",
            command="feedback",
            reason="Useful causal chain.",
        ),
    )

    with pytest.raises(ValueError, match="research_command_request_reused"):
        commands.apply(
            run_id,
            ResearchRunCommand(
                schema_version="research-run-command.v1",
                request_id="owner-command-1",
                command="feedback",
                reason="A different payload must not replace history.",
            ),
        )
