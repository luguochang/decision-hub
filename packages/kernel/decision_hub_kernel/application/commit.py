from __future__ import annotations

import json
import uuid
from datetime import timedelta

from packages.contracts_py.decision_hub_contracts.models import (
    Direction,
    Forecast,
    GateStatus,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
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
    def __init__(self, database: Database) -> None:
        self.database = database

    def commit(
        self, run_id: str, event_id: str, candidate: dict[str, object], gate: GateResult
    ) -> str:
        artifact_id = f"art_{uuid.uuid4().hex}"
        now = utcnow()
        forecasts: list[Forecast] = []
        for horizon in ("30m", "24h", "72h"):
            forecasts.append(
                Forecast(
                    forecast_id=f"fc_{uuid.uuid4().hex}",
                    artifact_id=artifact_id,
                    instrument="BTC-USDT-SWAP",
                    horizon=horizon,
                    direction=Direction(str(candidate.get("direction", "no_trade"))),
                    probability=float(str(candidate.get("probability", 0.5))),
                    trigger=str(candidate.get("trigger", "")),
                    invalidation=str(candidate.get("invalidation", "")),
                    expires_at=now
                    + timedelta(minutes={"30m": 30, "24h": 1440, "72h": 4320}[horizon]),
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
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            run.artifact_id = artifact_id
            run.status = (
                RunStatus.completed.value
                if gate.status == GateStatus.publish
                else RunStatus.degraded.value
            )
            run.updated_at = now
            run.finished_at = now
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
        return artifact_id
