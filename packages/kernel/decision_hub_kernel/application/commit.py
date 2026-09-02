from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import cast

from packages.contracts_py.decision_hub_contracts.models import (
    Direction,
    Forecast,
    GateStatus,
    HorizonDecision,
    ResearchSessionResult,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.decision.gate import GateResult, evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ForecastRecord,
    GateDecisionRecord,
    OutboxRecord,
    RunEventRecord,
    RunRecord,
    utcnow,
)


class CommitDecisionService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock
        self.research_observability = ResearchObservabilityService(database, clock=clock)

    def commit(
        self,
        run_id: str,
        event_id: str,
        candidate: dict[str, object],
        gate: GateResult,
        *,
        horizons: list[HorizonDecision] | None = None,
        research_result: ResearchSessionResult | None = None,
    ) -> str:
        with self.database.session() as session:
            existing_run = session.get(RunRecord, run_id)
            if not existing_run:
                raise KeyError(run_id)
            if existing_run.artifact_id:
                if research_result is not None:
                    self.research_observability.save_result_in_session(
                        session, run_id, research_result
                    )
                return existing_run.artifact_id
        artifact_id = f"art_{uuid.uuid4().hex}"
        now = self.clock()
        publishable_research = _research_result_is_publishable(research_result)
        effective_horizons = (
            [
                _fail_closed_horizon(item, research_result.stop_reason.code)
                for item in horizons or []
            ]
            if research_result is not None and not publishable_research
            else list(horizons or [])
        )
        forecasts: list[Forecast] = []
        horizon_by_name = {item.horizon: item for item in effective_horizons}
        for horizon in ("30m", "24h", "72h"):
            selected = horizon_by_name.get(horizon)
            forecasts.append(
                Forecast(
                    forecast_id=f"fc_{uuid.uuid4().hex}",
                    artifact_id=artifact_id,
                    instrument="BTC-USDT-SWAP",
                    horizon=horizon,
                    direction=Direction(
                        selected.action
                        if selected is not None
                        else str(candidate.get("direction", "no_trade"))
                    ),
                    probability=(
                        selected.subjective_probability
                        if selected is not None
                        else float(str(candidate.get("probability", 0.5)))
                    ),
                    trigger=(
                        selected.trigger
                        if selected is not None
                        else str(candidate.get("trigger", ""))
                    ),
                    invalidation=(
                        selected.invalidation
                        if selected is not None
                        else str(candidate.get("invalidation", ""))
                    ),
                    expires_at=(
                        selected.expires_at
                        if selected is not None
                        else now
                        + timedelta(minutes={"30m": 30, "24h": 1440, "72h": 4320}[horizon])
                    ),
                )
            )
        payload = {
            "facts": candidate.get("facts", []),
            "inferences": candidate.get("inferences", []),
            "counter_thesis": candidate.get("counter_thesis", ""),
            "uncertainty": candidate.get("uncertainty", []),
            "transmission_chain": candidate.get("transmission_chain", []),
            "citations": candidate.get("citations", []),
        }
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            if run.status == RunStatus.cancelled.value:
                raise PermissionError("research_run_cancelled")
            if run.artifact_id:
                if research_result is not None:
                    self.research_observability.save_result_in_session(
                        session, run_id, research_result
                    )
                return run.artifact_id
            if research_result is not None:
                self.research_observability.save_result_in_session(
                    session, run_id, research_result
                )
            session.add(
                ArtifactRecord(
                    artifact_id=artifact_id,
                    run_id=run_id,
                    event_id=event_id,
                    gate_status=gate.status.value,
                    headline=str(candidate.get("headline", "Decision candidate")),
                    summary=str(candidate.get("summary", "")),
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=now,
                )
            )
            for forecast in forecasts:
                session.add(
                    ForecastRecord(
                        forecast_id=forecast.forecast_id,
                        artifact_id=artifact_id,
                        instrument=forecast.instrument,
                        horizon=forecast.horizon,
                        direction=forecast.direction.value,
                        probability=forecast.probability,
                        trigger=forecast.trigger,
                        invalidation=forecast.invalidation,
                        expires_at=forecast.expires_at,
                    )
                )
            for decision in gate.decisions:
                session.add(GateDecisionRecord(run_id=run_id, artifact_id=artifact_id, **decision))
            run.artifact_id = artifact_id
            run.status = (
                RunStatus.completed.value
                if gate.status == GateStatus.publish
                else RunStatus.degraded.value
            )
            run.updated_at = now
            run.finished_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence_no=4,
                    event_type="decision.committed",
                    occurred_at=now,
                    payload_json=json.dumps({"artifact_id": artifact_id}),
                )
            )
            if gate.status.value in {"publish", "degraded"}:
                session.add(
                    OutboxRecord(
                        artifact_id=artifact_id,
                        channel="local",
                        dedupe_key=f"artifact:{artifact_id}:local",
                    )
                )
            recheck_at = _next_recheck_at(horizons or (), completed_at=now)
            if recheck_at is not None:
                recheck_key = f"research-recheck:{run_id}"
                existing_recheck = (
                    session.query(RunRecord).filter_by(idempotency_key=recheck_key).first()
                )
                if existing_recheck is None:
                    recheck_run_id = f"run_{uuid.uuid4().hex}"
                    session.add(
                        RunRecord(
                            run_id=recheck_run_id,
                            event_id=event_id,
                            idempotency_key=recheck_key,
                            status=RunStatus.admitted.value,
                            strategy_version="research.v1",
                            runtime_version="pending",
                            admission_origin="scheduled_recheck",
                            priority=10,
                            available_at=recheck_at,
                            parent_run_id=run_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.add(
                        RunEventRecord(
                            run_id=run_id,
                            sequence_no=5,
                            event_type="research.recheck.scheduled",
                            occurred_at=now,
                            payload_json=json.dumps(
                                {
                                    "run_id": recheck_run_id,
                                    "available_at": recheck_at.isoformat(),
                                }
                            ),
                        )
                    )
        return artifact_id

    def commit_research_result(
        self,
        run_id: str,
        event_id: str,
        result: ResearchSessionResult,
    ) -> tuple[str, GateResult]:
        """Project a validated agentic result into the existing decision ledger."""

        main_links = result.causal_case.main_chain if result.causal_case else []
        opposite_links = result.causal_case.opposite_chain if result.causal_case else []
        facts = [item.statement for item in main_links if item.claim_type == "fact"]
        inferences = [item.statement for item in main_links if item.claim_type != "fact"]
        citations = list(
            dict.fromkeys(
                ref
                for item in [*main_links, *opposite_links]
                for ref in item.evidence_refs
            )
        )
        first = next((item for item in result.horizons if item.horizon == "30m"), None)
        candidate: dict[str, object] = {
            "headline": (
                result.causal_case.thesis
                if result.causal_case
                else "Agentic research result"
            ),
            "summary": result.stop_reason.detail,
            "facts": facts,
            "inferences": inferences,
            "counter_thesis": "；".join(item.statement for item in opposite_links),
            "uncertainty": [
                f"{item.requirement_id}: {item.reason_code}"
                for item in result.final_coverage.gaps
            ],
            "transmission_chain": [item.statement for item in main_links],
            "citations": citations,
            "direction": first.action if first is not None else "no_trade",
            "probability": first.subjective_probability if first is not None else 0.5,
            "trigger": first.trigger if first is not None else "等待充分度与市场确认",
            "invalidation": (
                first.invalidation
                if first is not None
                else "研究证据不足或核心假设失效"
            ),
        }
        publishable_research = _research_result_is_publishable(result)
        if not publishable_research:
            candidate["direction"] = "no_trade"
            candidate["probability"] = 0.5
            candidate["uncertainty"] = [
                *cast(list[str], candidate["uncertainty"]),
                f"research_sufficiency:{result.stop_reason.code}",
            ]
        gate = evaluate_gate(candidate)
        artifact_id = self.commit(
            run_id,
            event_id,
            candidate,
            gate,
            horizons=list(result.horizons),
            research_result=result,
        )
        return artifact_id, gate


def _research_result_is_publishable(result: ResearchSessionResult | None) -> bool:
    if result is None:
        return True
    return (
        result.status == "completed"
        and result.final_coverage.status == "sufficient"
        and result.stop_reason.code == "sufficient"
        and not result.final_coverage.gaps
        and not result.stop_reason.remaining_hard_gaps
    )


def _fail_closed_horizon(
    horizon: HorizonDecision,
    stop_code: str,
) -> HorizonDecision:
    return horizon.model_copy(
        update={
            "action": Direction.no_trade,
            "subjective_probability": 0.5,
            "probability_status": "uncalibrated",
            "confidence_cap_reason": (
                f"Directional output suppressed because research stopped at {stop_code}."
            ),
        }
    )


def _next_recheck_at(
    horizons: Sequence[HorizonDecision], *, completed_at: datetime
) -> datetime | None:
    candidates = [
        item.next_review_at
        for item in horizons
        if completed_at < item.next_review_at <= item.expires_at
    ]
    return min(candidates, default=None)
