"""Bounded capture of actual report revisions and later mature observation receipts."""

from __future__ import annotations

import asyncio

from sqlalchemy import and_, func, or_, select

from packages.kernel.decision_hub_kernel.application.market_case import MarketCaseService
from packages.kernel.decision_hub_kernel.application.risk_monitor import canonical_json
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    Database,
    ReportRevisionRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import MarketCaseRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.provider_adapters.research.raw_capture import RawBodyArchive


class MarketMemoryTick:
    def __init__(self, database: Database, archive: RawBodyArchive, *, clock=utcnow):
        self.database, self.clock = database, clock
        self.cases = MarketCaseService(database, archive, clock=clock)

    def process(self, *, limit: int = 2) -> int:
        now = self.clock()
        with self.database.session() as session:
            captured_at = (
                select(func.max(MarketCaseRecord.created_at))
                .where(
                    MarketCaseRecord.outlook_id == AnalyticalOutlookRecord.outlook_id,
                )
                .correlate(AnalyticalOutlookRecord)
                .scalar_subquery()
            )
            failed_at = (
                select(func.max(RunRoleOutputRecord.created_at))
                .where(
                    RunRoleOutputRecord.run_id == AnalyticalOutlookRecord.run_id,
                    RunRoleOutputRecord.role.like("mcase_capture_failure_%"),
                )
                .correlate(AnalyticalOutlookRecord)
                .scalar_subquery()
            )
            rows = session.execute(
                select(
                    ReportRevisionRecord.revision_id,
                    AnalyticalOutlookRecord.outlook_id,
                    AnalyticalOutlookRecord.run_id,
                    AnalyticalObservationRecord.evaluated_at,
                )
                .join(
                    AnalyticalOutlookRecord,
                    AnalyticalOutlookRecord.artifact_id == ReportRevisionRecord.artifact_id,
                )
                .outerjoin(
                    AnalyticalObservationRecord,
                    and_(
                        AnalyticalObservationRecord.outlook_id == AnalyticalOutlookRecord.outlook_id,
                        AnalyticalObservationRecord.status == "completed",
                        AnalyticalObservationRecord.evaluated_at <= now,
                        AnalyticalOutlookRecord.window_end_at <= now,
                    ),
                )
                .where(
                    AnalyticalOutlookRecord.instrument.in_(("BTC-USDT", "ETH-USDT")),
                    AnalyticalOutlookRecord.market == "crypto",
                    AnalyticalOutlookRecord.horizon == "24h",
                    ReportRevisionRecord.recorded_at <= now,
                    or_(
                        captured_at.is_(None),
                        AnalyticalObservationRecord.evaluated_at > captured_at,
                    ),
                    or_(failed_at.is_(None), AnalyticalObservationRecord.evaluated_at > failed_at),
                )
                .order_by(ReportRevisionRecord.recorded_at)
                .limit(max(0, min(limit, 4)))
            ).all()
        completed = 0
        for revision, outlook, run_id, evaluated_at in rows:
            version = as_utc(evaluated_at).isoformat() if evaluated_at else "initial"
            request_id = "auto-case:" + canonical_hash([revision, outlook, version])
            try:
                self.cases.capture(request_id, revision_id=revision, outlook_id=outlook)
            except ValueError as error:
                # A poison original cannot block every future capture. Preserve
                # one failure for this exact input version; never declare a Case.
                output_id = "mcase_fail_" + canonical_hash([request_id])[:40]
                payload = {
                    "schema_version": "market-case-capture-failure.v1",
                    "request_id": request_id,
                    "revision_id": revision,
                    "outlook_id": outlook,
                    "error_code": str(error)[:160],
                    "recorded_at": now.isoformat(),
                }
                with self.database.session() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    if session.get(RunRoleOutputRecord, output_id) is None:
                        encoded = canonical_json(payload)
                        import hashlib

                        session.add(
                            RunRoleOutputRecord(
                                output_id=output_id,
                                run_id=run_id,
                                role="mcase_capture_failure_" + canonical_hash([request_id])[:32],
                                schema_version=payload["schema_version"],
                                payload_json=encoded,
                                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                                created_at=now,
                            )
                        )
            else:
                completed += 1
        return completed

    async def tick(self) -> int:
        task = asyncio.create_task(asyncio.to_thread(self.process, limit=2))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise
