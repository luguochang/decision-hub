"""Bounded normal-worker maturity/intake and data-use observations on existing originals."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    capture_scope,
)

from ..persistence.db import RawCaptureRecord, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_close import VERSION, millis, public_url
from ..ports.perpetual_forecast_experience import ROLE as EXPERIENCE_ROLE
from ..ports.research_observation import ObservedExecutionRef
from .action_research_integration import RETRY_SECONDS
from .perpetual_experience_use import ROLE as USE_ROLE
from .perpetual_experience_use import SCHEMA as USE_SCHEMA
from .perpetual_experience_use import record_experience_use
from .perpetual_forecast_experience import PerpetualForecastExperienceService, _publication
from .perpetual_research_binding import canonical

ROLE = "perpetual_forecast_due_observation_v1"
SCHEMA = "perpetual-forecast-due-observation.v1"


class PerpetualForecastRuntime:
    def __init__(
        self, database, source, *, clock=utcnow, collection_enabled=True, http_transport=None,
        scoring_version: str = "v1",
    ):
        if source.database is not database:
            raise ValueError("perpetual_forecast_runtime_database_mismatch")
        if scoring_version not in ("v1", "v2"):
            raise ValueError("perpetual_forecast_runtime_scoring_version_invalid")
        self.database, self.source, self.clock = database, source, clock
        self.scoring_version = scoring_version
        self.due_role = ROLE if scoring_version == "v1" else "perpetual_forecast_due_observation_v2"
        self.due_schema = (
            SCHEMA if scoring_version == "v1" else "perpetual-forecast-due-observation.v2"
        )
        self.publication_reader: Any = _publication
        self.collection_enabled, self.http_transport = collection_enabled, http_transport
        self.experiences = PerpetualForecastExperienceService(database, source, clock=clock)
        if scoring_version == "v2":
            from .perpetual_forecast_scoring import PerpetualForecastScoringService
            from .perpetual_forecast_scoring import (
                _publication as scoring_publication,  # pyright: ignore[reportPrivateUsage]
            )
            self.experiences = PerpetualForecastScoringService(database, source, clock=clock)
            self.publication_reader = scoring_publication

    def _last(self, session, run_id):
        row = session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.startswith(self.due_role + ":"),
            )
            .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id.desc())
            .execution_options(populate_existing=True)
            .limit(1)
        ).first()
        if row is None:
            return None
        body = json.loads(row.payload_json)
        if (
            row.payload_hash != canonical_hash(body)
            or row.payload_json != canonical(body)
            or row.schema_version != self.due_schema
            or body["run_id"] != run_id
            or as_utc(row.created_at) != datetime.fromisoformat(body["recorded_at"])
        ):
            raise ValueError("perpetual_forecast_due_original_invalid")
        return body

    def _append(self, session, body):
        digest = canonical_hash(body)
        phase = "1" if body["status"] == "collecting" else "2"
        prefix = "perp_due_" if self.scoring_version == "v1" else "perp_due_v2_"
        address = f"{prefix}{body['attempt']:06d}_{phase}_" + digest
        session.add(
            RunRoleOutputRecord(
                output_id=address,
                run_id=body["run_id"],
                role=self.due_role + ":" + digest,
                schema_version=self.due_schema,
                payload_json=canonical(body),
                payload_hash=digest,
                created_at=datetime.fromisoformat(body["recorded_at"]),
            )
        )
        session.flush()

    def _due_runs(self):
        target = aliased(RunRoleOutputRecord)
        experience = aliased(RunRoleOutputRecord)
        observation = aliased(RunRoleOutputRecord)
        latest = (
            select(observation.payload_json)
            .where(observation.run_id == target.run_id,
                   observation.role.startswith(self.due_role + ":"))
            .order_by(observation.created_at.desc(), observation.output_id.desc())
            .limit(1)
            .scalar_subquery()
        )
        now = aware_utc(self.clock())
        with self.database.session() as session:
            return tuple(
                session.scalars(
                    select(target.run_id)
                    .join(RunRecord, RunRecord.run_id == target.run_id)
                    .where(
                        target.schema_version == "publication-bound-target.v1",
                        target.role.startswith("perpetual_publication_original:"),
                        RunRecord.status.in_(("completed", "degraded")),
                        func.json_extract(target.payload_json, "$.horizon").in_(
                            ("4h",) if self.scoring_version == "v1" else ("4h", "24h", "72h")),
                        func.julianday(func.json_extract(target.payload_json, "$.window_end"))
                        <= func.julianday(now.isoformat()),
                        ~exists(
                            select(experience.output_id).where(
                                experience.run_id == target.run_id,
                                experience.role.in_(
                                    (EXPERIENCE_ROLE, "perpetual_forecast_experience_v2")
                                ),
                            )
                        ),
                        or_(latest.is_(None), func.json_extract(latest, "$.status") != "blocked"),
                        or_(
                            latest.is_(None),
                            func.julianday(func.json_extract(latest, "$.next_attempt_at"))
                            <= func.julianday(now.isoformat()),
                        ),
                    )
                    .order_by(target.created_at, target.output_id)
                    .limit(1)
                ).all()
            )

    def _claim(self, run_id):
        at = aware_utc(self.clock())
        def read_publication(session: Any) -> Any:
            return self.publication_reader(session, run_id, at)
        try:
            prepared = prepare_originals(self.database, read_publication)
        except ValueError as error:
            # A historical learning source must not stop unrelated ordinary
            # admission. Preserve its exact reason without creating a label.
            reason = str(error)
            with self.database.session() as session:
                begin_immediate(session)
                last = self._last(session, run_id)
                if last is None or datetime.fromisoformat(last["next_attempt_at"]) <= at:
                    self._append(
                        session,
                        dict(
                            schema_version=self.due_schema,
                            run_id=run_id,
                            attempt=last["attempt"] + 1 if last else 1,
                            status="blocked"
                            if reason
                            in {
                                "perpetual_experience_target_policy_unsupported",
                                "perpetual_experience_only_4h_supported",
                                "perpetual_scoring_target_policy_unsupported",
                            }
                            else "pending",
                            target_ref=None,
                            capture_ids=[],
                            reason=reason,
                            label_created=False,
                            recorded_at=at.isoformat(),
                            next_attempt_at=(at + timedelta(seconds=RETRY_SECONDS)).isoformat(),
                        ),
                    )
            return None
        admission, publication, _command = prepared.value
        target = publication["target"]
        if datetime.fromisoformat(target["window_end"].replace("Z", "+00:00")) > at:
            return None
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            last = self._last(session, run_id)
            if last is not None and (
                last["status"] == "blocked" or datetime.fromisoformat(last["next_attempt_at"]) > at
            ):
                return None
            # Expired collecting attempt resumes its captured bytes after a
            # crash; a completed failed attempt gets a separately traced retry.
            attempt = (
                last["attempt"]
                if last and last["status"] == "collecting"
                else (last["attempt"] + 1 if last else 1)
            )
            body = dict(
                schema_version=self.due_schema,
                run_id=run_id,
                attempt=attempt,
                status="collecting",
                target_ref=publication["snapshot"]["bindings"]["final_target"],
                instrument_id=admission["partition"]["instrument_id"],
                target_end=target["window_end"],
                capture_ids=[],
                reason="target_mature_source_capture_pending",
                recorded_at=at.isoformat(),
                next_attempt_at=(at + timedelta(seconds=RETRY_SECONDS)).isoformat(),
                retry_policy="existing_action_research_RETRY_SECONDS",
                label_created=False,
            )
            if self.scoring_version == "v2":
                body.update(target_start=target["window_start"], horizon=target["horizon"])
            self._append(session, body)
            prepared.verify_after_writes(session)
        return body

    async def _capture(self, claim):
        if self.scoring_version == "v2":
            from .perpetual_forecast_truth_capture import capture_truth
            return await capture_truth(self, claim)
        inst = claim["instrument_id"].removeprefix("okx:")
        acquisition = canonical_hash([claim["run_id"], claim["target_ref"], claim["attempt"]])[:32]
        context = f"{VERSION}:{inst}:{acquisition}:base:0"
        with self.database.session() as session:
            prior = session.scalars(
                select(RawCaptureRecord.capture_id)
                .where(RawCaptureRecord.context_id == context)
                .order_by(RawCaptureRecord.recorded_at, RawCaptureRecord.capture_id)
            ).all()
        if prior:
            if len(prior) != 1:
                raise ValueError("perpetual_forecast_capture_context_ambiguous")
            return tuple(prior)
        end = datetime.fromisoformat(claim["target_end"].replace("Z", "+00:00"))
        batch = CaptureBatch(
            context, VERSION, self.source.archive, self.source.lineage.record_capture
        )
        async with CapturingAsyncClient(
            timeout=15, follow_redirects=False, transport=self.http_transport
        ) as client:
            with capture_scope(batch):
                try:
                    await client.get(public_url(inst, millis(end)))
                except httpx.HTTPError:
                    # The existing capture hook persists failed HTTP/timeout
                    # metadata too. Keep its exact ID in the pending observation.
                    if not batch.captures:
                        raise
        if len(batch.captures) != 1:
            raise ValueError("perpetual_forecast_one_capture_required")
        return (batch.captures[0]["capture_id"],)

    async def process_due(self):
        if not self.collection_enabled:
            return 0
        count = 0
        for run_id in self._due_runs():
            claim = await asyncio.to_thread(self._claim, run_id)
            if claim is None:
                continue
            ids, reason, status, result = (), None, "recorded", None
            try:
                ids = await self._capture(claim)
                result = await asyncio.to_thread(
                    self.experiences.record, run_id=run_id, capture_ids=ids
                )
            except (ValueError, httpx.HTTPError, OSError) as error:
                ids = getattr(error, "capture_ids", ids)
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                # Missing/unconfirmed minutes, temporary HTTP and source format
                # responses remain pending. Only an unsupported frozen policy
                # is an operator-visible stable block, never a false label.
                status = (
                    "blocked"
                    if reason
                    in {
                        "perpetual_experience_target_policy_unsupported",
                        "perpetual_experience_only_4h_supported",
                        "perpetual_scoring_target_policy_unsupported",
                    }
                    else "pending"
                )
            at = aware_utc(self.clock())
            final = {
                **claim,
                "status": status,
                "reason": reason,
                "capture_ids": list(ids),
                "recorded_at": at.isoformat(),
                "next_attempt_at": (at + timedelta(seconds=RETRY_SECONDS)).isoformat(),
                "label_created": result is not None,
                "experience_ref": result.ref.model_dump(mode="json") if result else None,
            }
            with self.database.session() as session:
                begin_immediate(session)
                latest = self._last(session, run_id)
                if latest != claim:
                    raise ValueError("perpetual_forecast_due_claim_changed")
                self._append(session, final)
            count += 1
        return count

    def process_uses(self):
        emission = aliased(RunRoleOutputRecord)
        selected = aliased(RunRoleOutputRecord)
        used = aliased(RunRoleOutputRecord)
        selected_v3 = exists(select(selected.output_id).where(
            selected.run_id == emission.run_id,
            selected.role == "perpetual_experience_task_selection_v3").correlate(emission))
        selected_v2 = exists(
            select(selected.output_id)
            .where(
                selected.run_id == emission.run_id,
                selected.role == "perpetual_experience_task_selection_v2",
            )
            .correlate(emission)
        )
        with self.database.session() as session:
            rows = session.scalars(
                select(emission)
                .where(
                    emission.schema_version == "research-provider-tool-emission.v1",
                    func.json_extract(emission.payload_json, "$.submit.tool_name")
                    == "decision_hub_synthesis_submit",
                    or_(
                        func.json_extract(
                            func.json_extract(emission.payload_json, "$.submit.raw_arguments_json"),
                            "$.action",
                        ).is_(None),
                        func.json_extract(
                            func.json_extract(emission.payload_json, "$.submit.raw_arguments_json"),
                            "$.action",
                        )
                        != "finalize",
                    ),
                    func.json_extract(emission.payload_json, "$.submit.execution_session_id")
                    == func.json_extract(emission.payload_json, "$.submit.root_session_id"),
                    exists(
                        select(selected.output_id).where(
                            selected.run_id == emission.run_id,
                            selected.role.in_(
                                (
                                    "perpetual_experience_task_selection_v1",
                                    "perpetual_experience_task_selection_v2",
                                    "perpetual_experience_task_selection_v3",
                                )
                            ),
                            func.json_array_length(
                                func.json_extract(selected.payload_json, "$.selected")
                            )
                            > 0,
                        )
                    ),
                    ~exists(
                        select(used.output_id).where(
                            used.run_id == emission.run_id,
                            or_(
                                and_(selected_v3,
                                     used.role.startswith("perpetual_experience_provider_use_v3:")),
                                and_(
                                    ~selected_v3, selected_v2,
                                    used.role.startswith("perpetual_experience_provider_use_v2:"),
                                ),
                                and_(~selected_v3, ~selected_v2,
                                     used.role.startswith(USE_ROLE + ":")),
                            ),
                            func.json_extract(
                                used.payload_json, "$.provider_emission_ref.output_id"
                            )
                            == emission.output_id,
                        )
                    ),
                )
                .order_by(emission.created_at, emission.output_id)
                .limit(1)
            ).all()
            references = tuple(
                ObservedExecutionRef(
                    output_id=row.output_id,
                    role=row.role,
                    schema_version=row.schema_version,
                    payload_hash=row.payload_hash,
                    recorded_at=as_utc(row.created_at),
                )
                for row in rows
            )
        count = 0
        for reference in references:
            try:
                result = record_experience_use(self.database, reference, clock=self.clock)
                reason = "unsupported_or_non_authoring_emission" if result is None else None
            except (ValueError, KeyError, TypeError, OSError) as error:
                result, reason = None, str(error)
            if result is None:
                # A malformed/rejected model attempt must not starve later valid
                # emissions or block ordinary research. This is a diagnostic,
                # explicitly not a consumption receipt.
                at = aware_utc(self.clock())
                body = dict(
                    schema_version=USE_SCHEMA,
                    status="unavailable",
                    reason=reason,
                    run_id=reference.output_id,
                    provider_emission_ref=reference.model_dump(mode="json"),
                    uses=[],
                    skill_consumed=False,
                    financial_qualified=False,
                    recorded_at=at.isoformat(),
                )
                output_id = "perp_experience_use_" + canonical_hash(
                    reference.model_dump(mode="json")
                )
                with self.database.session() as session:
                    begin_immediate(session)
                    emission_row = session.get(
                        RunRoleOutputRecord, reference.output_id, populate_existing=True
                    )
                    if emission_row is None or emission_row.payload_hash != reference.payload_hash:
                        raise ValueError("perpetual_experience_diagnostic_source_changed")
                    body["run_id"] = emission_row.run_id
                    v3 = session.get(RunRoleOutputRecord,
                        "perp_experience_selection_v3_" + canonical_hash(emission_row.run_id),
                        populate_existing=True) is not None
                    v2 = (
                        session.get(
                            RunRoleOutputRecord,
                            "perp_experience_selection_v2_" + canonical_hash(emission_row.run_id),
                            populate_existing=True,
                        )
                        is not None
                    )
                    version = "v3" if v3 else "v2" if v2 else "v1"
                    use_role = "perpetual_experience_provider_use_" + version
                    use_schema = "perpetual-experience-provider-use." + version
                    if version != "v1":
                        body["schema_version"] = use_schema
                        output_id = "perp_experience_use_" + version + "_" + canonical_hash(
                            reference.model_dump(mode="json")
                        )
                    if session.get(RunRoleOutputRecord, output_id, populate_existing=True) is None:
                        session.add(
                            RunRoleOutputRecord(
                                output_id=output_id,
                                run_id=emission_row.run_id,
                                role=use_role
                                + ":"
                                + canonical_hash(reference.model_dump(mode="json")),
                                schema_version=use_schema,
                                payload_json=canonical(body),
                                payload_hash=canonical_hash(body),
                                created_at=at,
                            )
                        )
            count += 1
        return count

    async def tick(self):
        labels = await self.process_due()
        return labels + await asyncio.to_thread(self.process_uses)
