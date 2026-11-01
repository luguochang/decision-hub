"""Source-authenticated event batches feeding the existing perpetual Run lane."""

from datetime import timedelta

from sqlalchemy import select

from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.perpetual_research_schedule_records import (
    PerpetualResearchScheduleRecord as Schedule,
)
from ..persistence.risk_research_records import (
    RiskResearchRequestRecord as AdmissionRequest,
)
from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_research_schedule import (
    EVENT_PROTOCOL,
    PerpetualEventMotiveV1,
    PerpetualEventResearchTriggerV1,
    PerpetualEventTriggerItemV1,
)
from .perpetual_research_binding import _settings_original, canonical
from .perpetual_research_horizon import (
    PERPETUAL_RESEARCH_RUNTIME_SECONDS,
    perpetual_horizon_seconds,
)
from .perpetual_research_schedule import (
    _current_receipt,
    read_automatic_source_in_session,
)


def _event_identity(value):
    event_ref = value["event_ref"] if "event_ref" in value else value["source_ref"]
    return canonical(
        {
            "event_ref": event_ref,
            "event_key": value["event_key"],
            "stage_id": value["stage_id"],
        }
    )


class PerpetualEventResearchService:
    """Discover new agenda originals; model execution remains in admission/Run."""

    def __init__(self, database, entry, *, now):
        self.database, self.entry, self.now = database, entry, now
        if entry.database is not database:
            raise ValueError("perpetual_event_research_database_mismatch")
        if entry.total_deadline_seconds != PERPETUAL_RESEARCH_RUNTIME_SECONDS:
            raise ValueError("perpetual_event_research_runtime_policy_unsupported")

    def discover(self):
        at = aware_utc(self.now())
        with self.database.session() as session:
            schedule_ids = list(
                session.scalars(
                    select(Schedule.schedule_id)
                    .where(Schedule.state == "active")
                    .order_by(Schedule.schedule_id)
                    .limit(8)
                )
            )
        created = 0
        for schedule_id in schedule_ids:

            def prepare(session, schedule_id=schedule_id):
                from .perpetual_event_agenda import collect_event_agenda_in_session

                row = session.get(Schedule, schedule_id, populate_existing=True)
                if row is None or row.state != "active":
                    raise ValueError("perpetual_event_schedule_inactive")
                receipt = _current_receipt(session, row)
                saved = _settings_original(session, row.owner_id, receipt.command.partition)
                agenda = collect_event_agenda_in_session(
                    session,
                    archive=self.entry.binding.source.archive,
                    as_of=at,
                    instrument_refs=[saved.revision.values.instrument],
                    window_end=at
                    + timedelta(seconds=perpetual_horizon_seconds(row.horizon)),
                )
                return {
                    "schedule_id": schedule_id,
                    "schedule_ref": receipt.revision.model_dump(mode="json"),
                    "agenda": agenda.model_dump(mode="json"),
                }

            try:
                prepared = prepare_originals(self.database, prepare)
            except ValueError:
                continue
            created += self._stage(prepared, at)
        return created

    def _stage(self, prepared, at):
        with self.database.session() as session:
            begin_immediate(session)
            row = session.get(
                Schedule, prepared.value["schedule_id"], populate_existing=True
            )
            if row is None or row.state != "active":
                return 0
            current = _current_receipt(session, row)
            if current.revision.model_dump(mode="json") != prepared.value["schedule_ref"]:
                return 0
            prepared.verify(session)
            entries = sorted(prepared.value["agenda"]["entries"], key=_event_identity)
            if not entries:
                return 0
            existing = self._authenticated_existing(session, current, entries, at)
            pending = [entry for entry in entries if _event_identity(entry) not in existing]
            if not pending:
                return 0
            trigger_events = sorted(
                (
                    PerpetualEventTriggerItemV1(
                        event_ref=entry["source_ref"],
                        event_key=entry["event_key"],
                        stage_id=entry["stage_id"],
                    )
                    for entry in pending
                ),
                key=lambda item: canonical(item.model_dump(mode="json")),
            )
            source = PerpetualEventResearchTriggerV1(
                schema_version="perpetual-research-event-trigger.v1",
                schedule_id=row.schedule_id,
                schedule_ref=current.revision,
                owner_id=row.owner_id,
                partition=current.command.partition,
                horizon=row.horizon,
                scheduled_at=at,
                deadline_at=at + timedelta(seconds=PERPETUAL_RESEARCH_RUNTIME_SECONDS),
                recorded_at=at,
                trigger_events=trigger_events,
            )
            body = source.model_dump(mode="json")
            key = "perpetual_event:" + canonical_hash(
                [body["schedule_ref"], body["trigger_events"]]
            )
            scope = (
                "crypto:perp:okx:"
                + source.partition.instrument_id.removeprefix("okx:")
                + ":trade:1m"
            )
            request_id = "riskresearch_" + canonical_hash([scope, key])[:40]
            if session.get(Trigger, key) is not None or session.get(
                AdmissionRequest, request_id
            ) is not None:
                proof = read_automatic_source_in_session(
                    session,
                    request_id,
                    None,
                    None,
                    at=at,
                    require_active=False,
                    original_only=True,
                )
                if proof["source"] != body:
                    raise ValueError("perpetual_event_request_conflict")
                return 0
            session.add(
                AdmissionRequest(
                    request_id=request_id,
                    protocol_version=EVENT_PROTOCOL,
                    scope_id=scope,
                    instrument=source.partition.instrument_id.removeprefix("okx:"),
                    status="pending_admission",
                    created_at=at,
                    updated_at=at,
                    available_at=at,
                    attempts=0,
                    lease_generation=0,
                )
            )
            session.add(
                Trigger(
                    trigger_key=key,
                    request_id=request_id,
                    kind="perpetual_event",
                    scope_id=scope,
                    source_ref=row.schedule_id,
                    payload_json=canonical(body),
                    payload_hash=canonical_hash(body),
                    recorded_at=at,
                )
            )
            for entry in pending:
                motive = PerpetualEventMotiveV1(
                    schema_version="perpetual-event-motive.v1",
                    schedule_ref=current.revision,
                    owner_id=row.owner_id,
                    partition=current.command.partition,
                    horizon=row.horizon,
                    scheduled_at=source.scheduled_at,
                    deadline_at=source.deadline_at,
                    event_ref=entry["source_ref"],
                    source_id=entry["source_id"],
                    event_key=entry["event_key"],
                    family=entry["family"],
                    stage_id=entry["stage_id"],
                    known_at=entry["known_at"],
                    recorded_at=at,
                )
                motive_body = motive.model_dump(mode="json")
                identity = "perpetual_event_motive:" + canonical_hash(
                    [request_id, motive_body["event_ref"], motive.stage_id]
                )
                session.add(
                    Trigger(
                        trigger_key=identity,
                        request_id=request_id,
                        kind="perpetual_event_motive",
                        scope_id=scope,
                        source_ref=motive.event_ref.ref,
                        payload_json=canonical(motive_body),
                        payload_hash=canonical_hash(motive_body),
                        recorded_at=at,
                    )
                )
            return 1

    @staticmethod
    def _authenticated_existing(session, current, entries, at):
        refs = sorted({entry["source_ref"]["ref"] for entry in entries})
        linked = session.scalars(
            select(Trigger).where(
                Trigger.kind == "perpetual_event_motive", Trigger.source_ref.in_(refs)
            )
        ).all()
        request_ids = sorted({trigger.request_id for trigger in linked})
        result = set()
        expected_schedule = current.revision.model_dump(mode="json")
        for request_id in request_ids:
            proof = read_automatic_source_in_session(
                session,
                request_id,
                None,
                None,
                at=at,
                require_active=False,
                original_only=True,
            )
            if proof["schedule_ref"] != expected_schedule:
                continue
            result.update(_event_identity(value) for value in proof.get("event_motives", ()))
        return result
