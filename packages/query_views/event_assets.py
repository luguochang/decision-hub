from sqlalchemy import select, union

from packages.contracts_py.decision_hub_contracts.generated.event_asset import EventAssetView
from packages.kernel.decision_hub_kernel.persistence.db import (
    EventOccurrenceRecord,
    EventWatchRecord,
    EventWindowSampleRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    RunRecord,
    as_utc,
)


def event_assets(
    database, macro_event_id, *, vintage_offset=0, sample_offset=0, run_offset=0, limit=12
):
    with database.session() as session:
        event = session.get(MacroEventRecord, macro_event_id)
        if event is None:
            raise KeyError("macro_event_not_found")
        occurrence = session.get(EventOccurrenceRecord, event.event_id)
        vintage_query = session.query(MacroEventVintageRecord).filter_by(
            macro_event_id=macro_event_id
        )
        event_ids = union(
            select(MacroEventRecord.event_id).where(
                MacroEventRecord.macro_event_id == macro_event_id
            ),
            select(MacroEventVintageRecord.event_id).where(
                MacroEventVintageRecord.macro_event_id == macro_event_id
            ),
            select(EventOccurrenceRecord.event_id).where(
                EventOccurrenceRecord.occurrence_id == occurrence.occurrence_id
            )
            if occurrence
            else select(EventOccurrenceRecord.event_id).where(False),
        )
        sample_query = (
            session.query(EventWindowSampleRecord, EventWatchRecord)
            .join(EventWatchRecord, EventWatchRecord.watch_id == EventWindowSampleRecord.watch_id)
            .filter(EventWindowSampleRecord.event_id.in_(event_ids))
        )
        run_query = session.query(RunRecord).filter(RunRecord.event_id.in_(event_ids))
        vintage_total, sample_total, run_total = (
            vintage_query.count(),
            sample_query.count(),
            run_query.count(),
        )
        vintages = (
            vintage_query.order_by(
                MacroEventVintageRecord.created_at.desc(), MacroEventVintageRecord.vintage_id
            )
            .offset(vintage_offset)
            .limit(limit)
        )
        samples = (
            sample_query.order_by(
                EventWindowSampleRecord.target_at.desc(), EventWindowSampleRecord.sample_id
            )
            .offset(sample_offset)
            .limit(limit)
        )
        runs = (
            run_query.order_by(RunRecord.created_at.desc(), RunRecord.run_id)
            .offset(run_offset)
            .limit(limit)
        )
        return EventAssetView(
            macro_event_id=macro_event_id,
            occurrence_id=occurrence.occurrence_id if occurrence else None,
            vintages=[
                dict(
                    vintage_id=r.vintage_id,
                    status=r.status,
                    time_precision=r.time_precision,
                    scheduled_at=as_utc(r.scheduled_at) if r.time_precision == "exact" else None,
                    scheduled_date=r.scheduled_date,
                    observed_at=as_utc(r.observed_at),
                    recorded_at=as_utc(r.created_at),
                    source_timezone=r.source_timezone,
                    prior_value=r.prior_value,
                    consensus_value=r.consensus_value,
                    actual_value=r.actual_value,
                    payload_hash=r.payload_hash,
                )
                for r in vintages
            ],
            vintage_total=vintage_total,
            samples=[
                dict(
                    sample_id=r.sample_id,
                    watch_id=r.watch_id,
                    offset=r.offset,
                    target_at=as_utc(r.target_at),
                    watch_scheduled_at=as_utc(w.scheduled_at),
                    watch_status=w.status,
                    status=r.status,
                    observed_at=as_utc(r.observed_at),
                    received_at=as_utc(r.received_at),
                    provider_id=r.provider_id,
                    error_code=r.error_code,
                    payload_ref=r.payload_ref,
                )
                for r, w in samples
            ],
            sample_total=sample_total,
            runs=[
                dict(
                    run_id=r.run_id,
                    study_id=r.study_id,
                    status=r.status,
                    strategy_version=r.strategy_version,
                    created_at=as_utc(r.created_at),
                    artifact_id=r.artifact_id,
                )
                for r in runs
            ],
            run_total=run_total,
        )
