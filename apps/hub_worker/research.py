from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict

from packages.contracts_py.decision_hub_contracts import (
    DomainPackManifest,
    ErrorProvenance,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.provider_adapters.research import CryptoMacroFactPack


class ResearchWorkerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    run_id: str | None = None
    artifact_id: str | None = None
    error_code: str | None = None


class ResearchRequestFactory:
    """Build typed requests from the immutable trigger snapshot and Domain Pack."""

    def __init__(
        self,
        database: Database,
        *,
        pack_root: Path,
        allowed_capabilities: Iterable[str] = ("replay.research",),
        execution_mode: Literal["live", "replay"] = "live",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.pack_root = pack_root
        self.allowed_capabilities = tuple(dict.fromkeys(allowed_capabilities))
        self.execution_mode = execution_mode
        self.clock = clock
        self.fact_pack = CryptoMacroFactPack.from_pack(pack_root)
        self.event_watches = EventWatchService(database, clock=clock)

    def build(self, event_id: str, run_id: str) -> ResearchSessionRequest:
        snapshot_id, _ = SnapshotService(self.database).freeze_for_run(run_id, event_id)
        rows = self.database.get_snapshot_evidence(snapshot_id)
        if not rows:
            raise ValueError("research_trigger_evidence_missing")
        pack = DomainPackManifest.model_validate(
            yaml.safe_load((self.pack_root / "pack.yaml").read_text(encoding="utf-8"))
        )
        requirements = list(self.fact_pack.all_contract_requirements())
        input_evidence = [_input_evidence(row) for row in rows]
        now = self.clock().astimezone(UTC)
        deadline_at = now + timedelta(seconds=pack.execution_budget.total_deadline_seconds)
        # Replay must retain the historical information boundary. Using the
        # worker's deadline as PIT would make minute-level market evidence look
        # stale merely because the replay budget is longer than one minute.
        pit_cutoff_at = (
            max(item.received_at for item in input_evidence)
            if self.execution_mode == "replay"
            else deadline_at
        )
        event_watch = self.event_watches.get_watch_by_event_id(event_id)
        event_window_samples = (
            list(self.event_watches.list_event_samples(event_id))
            if event_watch is not None
            else []
        )
        return ResearchSessionRequest(
            schema_version="research-session-request.v1",
            request_id=f"research-request:{run_id}",
            run_id=run_id,
            event_id=event_id,
            trigger_snapshot_id=snapshot_id,
            domain_pack_ref=f"{pack.pack_id}.v{pack.version.split('.')[0]}",
            role_profile_ref="crypto_macro.manager.v1",
            execution_mode=cast(Literal["live", "replay"], self.execution_mode),
            pit_cutoff_at=pit_cutoff_at,
            current_round=1,
            evidence_refs=[item.evidence_id for item in input_evidence],
            input_evidence=input_evidence,
            evidence_requirements=requirements,
            target_gaps=[],
            event_watch=event_watch,
            event_window_samples=event_window_samples,
            allowed_capabilities=list(self.allowed_capabilities),
            execution_budget=pack.execution_budget,
            deadline_at=deadline_at,
            output_schema_ref="agentic_research.schema.yaml#/$defs/research_session_result",
            repair_instructions=None,
        )


class DurableResearchWorker:
    """Claim admitted Runs, execute the agentic graph, and project one ledger result."""

    def __init__(
        self,
        database: Database,
        runtime: ResearchHarnessRuntime,
        *,
        pack_root: Path,
        checkpoint_path: Path | None = None,
        worker_id: str = "research-worker",
        strategy_version: str = "research.v1",
        allowed_capabilities: Iterable[str] = ("replay.research",),
        execution_mode: Literal["live", "replay"] = "live",
        lease_seconds: int = 240,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.runs = RunService(database, clock=clock)
        self.runtime = runtime
        self.worker_id = worker_id
        self.strategy_version = strategy_version
        self.lease_seconds = lease_seconds
        self.factory = ResearchRequestFactory(
            database,
            pack_root=pack_root,
            allowed_capabilities=allowed_capabilities,
            execution_mode=execution_mode,
            clock=clock,
        )
        self.executor = LangGraphResearchExecutor(
            database,
            runtime,
            checkpoint_path=checkpoint_path,
        )
        self.commit = CommitDecisionService(database, clock=clock)
        self.observability = ResearchObservabilityService(database, clock=clock)
        self.value_evaluations = ResearchValueEvaluationService(
            database, pack_root=pack_root, clock=clock
        )
        self.clock = clock

    async def tick(self) -> ResearchWorkerReport | None:
        claim = self.runs.claim_next(
            strategy_version=self.strategy_version,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return None
        event_id, run_id = claim.event_id, claim.run_id
        try:
            if claim.recovered and await self.executor.has_checkpoint(run_id):
                state = await self.executor.resume(run_id)
            else:
                request = self.factory.build(event_id, run_id)
                state = await self.executor.execute(request)
            final = ResearchSessionResult.model_validate(state["final_result"])
            artifact_id, gate = self.commit.commit_research_result(run_id, event_id, final)
            self._record_completed(run_id, state, gate.status.value, artifact_id)
            self._evaluate_terminal_safely(run_id)
            return ResearchWorkerReport(
                status=gate.status.value,
                run_id=run_id,
                artifact_id=artifact_id,
            )
        except Exception as exc:
            error_code = getattr(exc, "error_code", "research_worker_failed")
            current = self.database.get_run_record(run_id)
            if current is not None and current.status == RunStatus.cancelled.value:
                return ResearchWorkerReport(
                    status="cancelled",
                    run_id=run_id,
                    error_code=current.error_code or "cancelled_by_owner",
                )
            self.runs.set_status(run_id, RunStatus.failed, error_code=error_code)
            self.observability.append_trace(
                ResearchTraceEvent.model_validate(
                    {
                        "schema_version": "research-trace-event.v1",
                        "run_id": run_id,
                        "research_session_id": f"product:{run_id}",
                        "sequence_no": 0,
                        "event_type": "session_stopped",
                        "occurred_at": self.clock(),
                        "stage": "done",
                        "summary": "Research execution stopped without a committable result.",
                        "reference_type": "run",
                        "reference_id": run_id,
                        "status": "failed",
                        "error_code": error_code,
                        "error": _error_provenance(exc),
                    }
                )
            )
            self.database.record_run_event(
                run_id,
                self._next_event_sequence(run_id),
                "research.agentic.failed",
                {"error_code": error_code},
            )
            self._evaluate_terminal_safely(run_id)
            return ResearchWorkerReport(status="failed", run_id=run_id, error_code=error_code)

    def _evaluate_terminal_safely(self, run_id: str) -> bool:
        """Keep derived evaluation failures outside the committed Run outcome.

        Research value evaluation is an append-only projection. It may be retried
        independently, but it must never turn an already committed Artifact or a
        correctly classified runtime failure into a different business outcome.
        """

        try:
            self.value_evaluations.evaluate_current(run_id)
            return True
        except Exception as exc:
            self.database.record_run_event(
                run_id,
                self._next_event_sequence(run_id),
                "research.value_evaluation.failed",
                {
                    "error_code": "research_value_evaluation_failed",
                    "cause_code": str(getattr(exc, "error_code", type(exc).__name__)),
                    "retryable": True,
                },
            )
            return False

    def _record_completed(
        self,
        run_id: str,
        state: dict[str, object],
        gate_status: str,
        artifact_id: str,
    ) -> None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            final = ResearchSessionResult.model_validate(state["final_result"])
            run.runtime_version = final.runtime_version
            run.latency_ms = round(
                (final.finished_at - final.started_at).total_seconds() * 1000
            )
            run.cost_usd = final.estimated_cost_usd
            run.updated_at = self.clock()
        self.database.record_run_event(
            run_id,
            self._next_event_sequence(run_id),
            "research.agentic.completed",
            {
                "artifact_id": artifact_id,
                "gate_status": gate_status,
                "decision_snapshot_id": state.get("decision_snapshot_id"),
                "runtime_id": final.runtime_id,
                "runtime_version": final.runtime_version,
                "research_session_id": final.research_session_id,
                "rounds": len(final.rounds),
                "tool_calls": final.total_tool_calls,
                "subagents": final.total_subagents,
            },
        )

    def _next_event_sequence(self, run_id: str) -> int:
        timeline = self.database.get_timeline(run_id)
        return max((item.sequence_no for item in timeline), default=0) + 1


def _input_evidence(row: dict[str, Any]) -> ResearchInputEvidence:
    source_type = str(row.get("source_type", "document"))
    kind = cast(
        Literal["official", "market", "web", "transcript", "document"],
        {
        "transcript": "transcript",
        "official_feed": "official",
        "web": "web",
        "document": "document",
        }.get(source_type, "document"),
    )
    authority = "official" if kind == "official" else "unverified"
    # ResearchInputEvidence is a bounded prompt projection. The immutable
    # Observation/Snapshot retains the full source text and content hash.
    excerpt = str(row.get("text", ""))[:4000]
    return ResearchInputEvidence(
        evidence_id=str(row["evidence_id"]),
        kind=kind,
        authority=authority,
        source_id=str(row["source_id"]),
        source_url=row.get("source_url"),
        published_at=_parse_optional_datetime(row.get("published_at")),
        observed_at=_parse_datetime(row["observed_at"]),
        received_at=_parse_datetime(row["received_at"]),
        content_hash=str(row["content_hash"]),
        excerpt=excerpt,
    )


def _error_provenance(error: BaseException) -> ErrorProvenance:
    provenance = getattr(error, "provenance", None)
    if callable(provenance):
        value = provenance()
        if isinstance(value, ErrorProvenance):
            return value
    return ErrorProvenance(
        error_code=str(getattr(error, "error_code", "research_worker_failed")),
        origin=str(getattr(error, "origin", "orchestration")),  # type: ignore[arg-type]
        cause_code=getattr(error, "cause_code", None),
        capability_id=getattr(error, "capability_id", None),
        tool_call_id=getattr(error, "tool_call_id", None),
        retryable=bool(getattr(error, "retryable", False)),
        deadline_ms=getattr(error, "deadline_ms", None),
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError("research_timestamp_invalid")
    normalized = as_utc(parsed)
    if normalized is None:
        raise ValueError("research_timestamp_invalid")
    return normalized


def _parse_optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _parse_datetime(value)
