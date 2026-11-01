"""One bounded worker channel for restart-safe fit/publish/label/finalize jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, case, or_

from ..decision.path_forecast import fit_path_forecast
from ..persistence.db import as_utc
from ..persistence.path_forecast_records import RiskPathJobRecord, RiskShadowTrialRecord
from ..ports.path_forecast import parse_path_parameters
from .risk_path_ledger import RiskPathLedger, scope_from_protocol, writer


@dataclass(frozen=True)
class PathShadowClaim:
    job_id: str
    trial_id: str
    kind: str
    scope_id: str
    cutoff_at: datetime
    owner: str
    generation: int
    expires_at: datetime
    protocol: dict


class PathShadowJobService:
    def __init__(
        self,
        ledger: RiskPathLedger,
        *,
        owner_id: str,
        lease_seconds: int = 120,
    ) -> None:
        if not owner_id or len(owner_id) > 128 or not 10 <= lease_seconds <= 600:
            raise ValueError("path_shadow_lease_configuration_invalid")
        self.ledger, self.owner_id, self.lease_seconds = ledger, owner_id, lease_seconds

    def claim(self, job_id: str | None = None) -> PathShadowClaim | None:
        # At most 256 already-expired rows are retired in one bounded selection.
        for _ in range(256):
            with self.ledger.database.session() as session:
                writer(session)
                now = self.ledger.now()
                query = (
                    session.query(RiskPathJobRecord)
                    .join(
                        RiskShadowTrialRecord,
                        RiskShadowTrialRecord.trial_id == RiskPathJobRecord.trial_id,
                    )
                    .filter(
                        RiskShadowTrialRecord.state.not_in(("completed", "failed")),
                        or_(
                            RiskShadowTrialRecord.state != "paused",
                            RiskPathJobRecord.kind == "finalize",
                        ),
                        RiskPathJobRecord.next_attempt_at <= now,
                        or_(
                            RiskPathJobRecord.state == "pending",
                            and_(
                                RiskPathJobRecord.state == "running",
                                RiskPathJobRecord.lease_expires_at <= now,
                            ),
                        ),
                    )
                )
                if job_id is not None:
                    query = query.filter(RiskPathJobRecord.job_id == job_id)
                row = query.order_by(
                    case(
                        (RiskPathJobRecord.kind == "finalize", 0),
                        (RiskPathJobRecord.kind == "fit", 1),
                        (RiskPathJobRecord.kind == "forecast", 2),
                        else_=3,
                    ),
                    RiskPathJobRecord.due_at,
                    RiskPathJobRecord.job_id,
                ).first()
                if row is None:
                    return None
                trial = session.get(RiskShadowTrialRecord, row.trial_id)
                protocol = self.ledger.protocol(trial)
                if row.kind != "finalize" and (
                    now > as_utc(row.deadline_at)
                    or (row.kind == "label" and now == as_utc(row.deadline_at))
                ):
                    row.state = "missed" if row.kind == "forecast" else "expired"
                    row.error_code, row.completed_at = "fixed_job_deadline_elapsed", now
                    row.lease_generation += 1
                    row.lease_owner = row.lease_expires_at = None
                    if job_id is not None:
                        return None
                    continue
                row.state, row.lease_owner = "running", self.owner_id
                row.lease_generation += 1
                row.attempts += 1
                row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                trial.state = (
                    "draining"
                    if now > as_utc(trial.last_cutoff_at) + timedelta(minutes=1)
                    else "running"
                )
                return PathShadowClaim(
                    row.job_id,
                    row.trial_id,
                    row.kind,
                    row.scope_id,
                    as_utc(row.cutoff_at),
                    self.owner_id,
                    row.lease_generation,
                    row.lease_expires_at,
                    protocol,
                )
        return None

    def execute(self, claim: PathShadowClaim) -> bool:
        if claim.kind == "finalize":
            return self.ledger.finalize(claim)
        scope = scope_from_protocol(claim.protocol, claim.scope_id)
        if claim.kind == "fit":
            read_at = min(self.ledger.now(), claim.cutoff_at)
            windows = self.ledger.inputs.training_windows(scope=scope, known_by=read_at)
            model = fit_path_forecast(
                scope,
                windows,
                training_cutoff_at=claim.cutoff_at,
                parameters=parse_path_parameters(claim.protocol["parameters"]),
            )
            return self.ledger.save_fit(claim, model, read_as_of=read_at)
        if claim.kind == "forecast":
            model = self.ledger.fitted_model(claim.trial_id, claim.scope_id)
            if model is None:
                raise ValueError("path_shadow_frozen_fit_unavailable")
            manifest = self.ledger.inputs.feature_manifest(scope, claim.cutoff_at)
            return self.ledger.publish_pair(claim, model, manifest)
        if claim.kind == "label":
            receipt = self.ledger.inputs.observe(scope, claim.cutoff_at)
            return self.ledger.save_observation(claim, receipt)
        raise ValueError("path_shadow_job_kind_invalid")

    def fail(self, claim: PathShadowClaim, error_code: str) -> bool:
        with self.ledger.database.session() as session:
            writer(session)
            now = self.ledger.now()
            row = self.ledger.owns(session, claim, now)
            if row is None:
                return False
            row.error_code = error_code[:128]
            row.lease_owner = row.lease_expires_at = None
            if row.kind in {"fit", "finalize"} and row.attempts >= 3:
                row.state, row.completed_at = "failed", now
                if row.kind == "finalize":
                    trial = session.get(RiskShadowTrialRecord, row.trial_id)
                    trial.state, trial.finished_at, trial.conclusion = (
                        "failed",
                        now,
                        "ledger_failure",
                    )
            elif row.kind != "finalize" and now >= as_utc(row.deadline_at):
                row.state = "missed" if row.kind == "forecast" else "expired"
                row.completed_at = now
            else:
                row.state = "pending"
                delay = timedelta(seconds=5 if row.kind == "forecast" else 300)
                row.next_attempt_at = (
                    now + delay
                    if row.kind == "finalize"
                    else min(now + delay, as_utc(row.deadline_at))
                )
        return True

    def process_due(self, *, limit: int = 4) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("path_shadow_process_limit_invalid")
        processed = 0
        for _ in range(limit):
            claim = self.claim()
            if claim is None:
                break
            try:
                self.execute(claim)
            except Exception as exc:
                code = (
                    str(exc)
                    if isinstance(exc, ValueError) and str(exc).isidentifier()
                    else (type(exc).__name__)
                )
                self.fail(claim, code)
            processed += 1
        return processed
