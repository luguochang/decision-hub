"""One archived market window scores both frozen predictions, without trading authority."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from packages.evals.prospective_observations import verify_archived_window
from packages.evals.prospective_pairs import HORIZONS
from packages.evals.prospective_trials import TrialRequest
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProspectiveLabelAttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProspectiveLabelRecord as Label,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProspectivePairRecord as Pair,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc, utcnow
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash
from packages.workbench_adapters.source_capture import observation_capture_context


def score_pair(view, trial, market_return, *, evaluated_at, captures):
    request = TrialRequest.model_validate(trial.definition["request"])
    start = datetime.fromisoformat(view["deadline_at"])
    end = datetime.fromisoformat(view["window_end_at"])
    data = view["input"]
    if (
        view["exclusion_reason"]
        or view["trial_id"] != trial.trial_id
        or start != datetime.fromisoformat(data["window_start_at"])
        or end != datetime.fromisoformat(data["window_end_at"])
        or end - start != HORIZONS[request.horizon]
        or end > evaluated_at
        or data["market"] != request.market
        or data["instrument"] != request.instrument
    ):
        raise ValueError("pair_label_scope_or_window_invalid")
    scores = {}
    for arm in view["arms"]:
        name = arm["arm"]
        if arm["recipe_hash"] != trial.definition[name]["recipe_hash"]:
            raise ValueError("pair_label_recipe_mismatch")
        score = {
            "status": arm["status"],
            "result_hash": arm["result_hash"],
            "direction": None,
            "directional_return_pct": None,
            "direction_correct": None,
        }
        if arm["status"] == "succeeded":
            began = datetime.fromisoformat(arm["started_at"])
            finished = datetime.fromisoformat(arm["finished_at"])
            if not datetime.fromisoformat(view["admitted_at"]) <= began <= finished < start:
                raise ValueError("pair_prediction_after_cutoff")
            direction = arm["result"]["payload"]["direction"]
            directional = (
                market_return
                if direction == "long"
                else -market_return
                if direction == "short"
                else None
            )
            score.update(
                direction=direction,
                directional_return_pct=directional,
                direction_correct=directional > 0 if directional is not None else None,
            )
        scores[name] = score
    left, right = (scores[name]["direction_correct"] for name in ("baseline", "candidate"))
    return {
        "schema_version": "prospective-pair-label.v1",
        "pair_id": view["pair_id"],
        "trial_id": trial.trial_id,
        "trial_definition_hash": trial.definition_hash,
        "input_hash": view["input_hash"],
        "evaluated_at": evaluated_at.isoformat(),
        "window_start_at": start.isoformat(),
        "window_end_at": end.isoformat(),
        "market_return_pct": market_return,
        "scores": scores,
        "paired_correctness_delta": int(right) - int(left)
        if left is not None and right is not None
        else None,
        "benchmark": "close_1m",
        "quality_status": "estimated",
        "captures": captures,
        "promotion_eligible": False,
        "limitations": [
            "minute_close_not_execution_pnl",
            "probabilities_uncalibrated",
            "single_pair_not_independent_event_proof",
        ],
    }


class ProspectiveLabelService:
    def __init__(self, database, pairs, market, data_root, *, clock=utcnow):
        self.database, self.pairs, self.market, self.clock = database, pairs, market, clock
        self.archive = RawBodyArchive(data_root / "raw-captures")
        self.capture_context = observation_capture_context(database, data_root)

    def register_missing(self, trial_id, *, limit=100):
        with self.database.session() as session:
            rows = (
                session.query(Pair)
                .outerjoin(Label, Label.pair_id == Pair.pair_id)
                .filter(
                    Pair.trial_id == trial_id,
                    Pair.exclusion_reason.is_(None),
                    Label.pair_id.is_(None),
                )
                .order_by(Pair.window_end_at, Pair.pair_id)
                .limit(limit)
                .all()
            )
            for row in rows:
                try:
                    with session.begin_nested():
                        session.add(
                            Label(
                                pair_id=row.pair_id,
                                status="pending",
                                attempt=0,
                                next_attempt_at=as_utc(row.window_end_at) + timedelta(minutes=1),
                            )
                        )
                        session.flush()
                except IntegrityError:
                    pass

    def recover_expired(self):
        now = as_utc(self.clock())
        with self.database.session() as session:
            for row in (
                session.query(Label)
                .filter(
                    Label.status == "running",
                    Label.lease_expires_at <= now,
                )
                .all()
            ):
                changed = (
                    session.query(Label)
                    .filter_by(
                        pair_id=row.pair_id,
                        attempt=row.attempt,
                        status="running",
                        lease_owner=row.lease_owner,
                    )
                    .filter(Label.lease_expires_at <= now)
                    .update(
                        {
                            "status": "retry",
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "next_attempt_at": now,
                            "error_code": "pair_label_interrupted",
                        },
                        synchronize_session=False,
                    )
                )
                if changed:
                    attempt = session.get(Attempt, (row.pair_id, row.attempt))
                    attempt.status, attempt.finished_at = "interrupted", now
                    attempt.error_code = "pair_label_interrupted"

    def claim(self, pair_id):
        now, owner = as_utc(self.clock()), uuid.uuid4().hex
        with self.database.session() as session:
            row = session.get(Label, pair_id)
            if row is None:
                return None
            attempt = row.attempt + 1
            changed = (
                session.query(Label)
                .filter(
                    Label.pair_id == pair_id,
                    Label.attempt == row.attempt,
                    Label.status.in_(["pending", "retry"]),
                    Label.next_attempt_at <= now,
                )
                .update(
                    {
                        "status": "running",
                        "attempt": attempt,
                        "lease_owner": owner,
                        "lease_expires_at": now + timedelta(seconds=90),
                        "error_code": None,
                    },
                    synchronize_session=False,
                )
            )
            if not changed:
                return None
            session.add(Attempt(pair_id=pair_id, attempt=attempt, status="running", started_at=now))
            return owner, attempt

    def finish(self, pair_id, owner, attempt, *, result=None, error=None):
        now = as_utc(self.clock())
        with self.database.session() as session:
            changed = (
                session.query(Label)
                .filter_by(
                    pair_id=pair_id,
                    lease_owner=owner,
                    attempt=attempt,
                    status="running",
                )
                .filter(Label.lease_expires_at > now)
                .update(
                    {
                        "status": "completed" if result else "retry",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "result_json": canonical_recipe(result) if result else None,
                        "result_hash": recipe_hash(result) if result else None,
                        "evaluated_at": now if result else None,
                        "error_code": error,
                        "next_attempt_at": now
                        + timedelta(seconds=min(21600, 60 * 2 ** min(attempt - 1, 9))),
                    },
                    synchronize_session=False,
                )
            )
            if changed:
                record = session.get(Attempt, (pair_id, attempt))
                record.status = "completed" if result else "failed"
                record.finished_at, record.error_code = now, error
            return bool(changed)

    async def process_due(self, trial_id, *, limit=10):
        self.pairs.recover_expired(trial_id)
        self.register_missing(trial_id)
        self.recover_expired()
        now = as_utc(self.clock())
        with self.database.session() as session:
            identities = [
                r[0]
                for r in session.query(Label.pair_id)
                .join(
                    Pair,
                    Pair.pair_id == Label.pair_id,
                )
                .filter(
                    Pair.trial_id == trial_id,
                    Pair.window_end_at <= now,
                    Label.status.in_(["pending", "retry"]),
                    Label.next_attempt_at <= now,
                )
                .order_by(
                    Label.next_attempt_at,
                    Label.pair_id,
                )
                .limit(limit)
            ]
        completed = 0
        for identity in identities:
            claim = self.claim(identity)
            if claim is None:
                continue
            owner, attempt = claim
            try:
                view = self.pairs.get(identity)
                trial = self.pairs.trials.get(trial_id)
                start, end = (
                    datetime.fromisoformat(view[k]) for k in ("deadline_at", "window_end_at")
                )
                context = f"paired:{identity}:{attempt}"
                with self.capture_context(context):
                    async with asyncio.timeout(25):
                        window = await self.market.window(view["input"]["instrument"], start, end)
                if window is None:
                    raise ValueError("pair_market_window_missing")
                evaluated = as_utc(self.clock())
                with self.database.session() as session:
                    change, captures = verify_archived_window(
                        session,
                        context_id=f"analytical-observation:{context}",
                        instrument=view["input"]["instrument"],
                        start_at=start,
                        end_at=end,
                        window=window.model_dump(mode="json"),
                        evaluated_at=evaluated,
                        now=as_utc(self.clock()),
                        archive=self.archive,
                    )
                result = score_pair(view, trial, change, evaluated_at=evaluated, captures=captures)
                completed += int(self.finish(identity, owner, attempt, result=result))
            except asyncio.CancelledError:
                self.finish(identity, owner, attempt, error="pair_label_cancelled")
                raise
            except Exception as exc:
                code = str(exc)
                safe = (
                    code
                    if isinstance(exc, ValueError)
                    and code
                    in {
                        "pair_market_window_missing",
                        "archive_hash_mismatch",
                        "candle_capture_ambiguous",
                        "candle_not_closed",
                        "candle_time_or_price_invalid",
                        "saved_window_mismatch",
                        "pair_label_scope_or_window_invalid",
                        "pair_label_recipe_mismatch",
                        "pair_prediction_after_cutoff",
                        "pair_input_corrupt",
                        "pair_result_corrupt",
                    }
                    else "pair_label_collection_failed"
                )
                self.finish(identity, owner, attempt, error=safe)
        return completed

    def get(self, pair_id):
        with self.database.session() as session:
            row = session.get(Label, pair_id)
            if row is None:
                return None
            result = json.loads(row.result_json) if row.result_json else None
            if result is not None and recipe_hash(result) != row.result_hash:
                raise ValueError("pair_label_corrupt")
            if result is not None:
                pair = self.pairs.get(pair_id)
                trial = self.pairs.trials.get(pair["trial_id"])
                if (
                    result["pair_id"] != pair_id
                    or result["trial_id"] != pair["trial_id"]
                    or result["input_hash"] != pair["input_hash"]
                    or result["trial_definition_hash"] != trial.definition_hash
                    or any(
                        result["scores"][arm["arm"]]["result_hash"] != arm["result_hash"]
                        or result["scores"][arm["arm"]]["status"] != arm["status"]
                        for arm in pair["arms"]
                    )
                ):
                    raise ValueError("pair_label_binding_mismatch")
            return {
                "pair_id": pair_id,
                "status": row.status,
                "attempt": row.attempt,
                "next_attempt_at": as_utc(row.next_attempt_at).isoformat(),
                "error_code": row.error_code,
                "result": result,
                "result_hash": row.result_hash,
            }
