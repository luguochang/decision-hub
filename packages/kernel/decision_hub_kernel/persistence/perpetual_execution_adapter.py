"""Manual-perp payload versions in the existing execution records, no cash ledger."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select, update

from ..ports.perpetual_manual_execution import ManualExecutionArtifact, ManualExecutionIntent
from .action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionEpisodeRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionReceiptRecord,
)
from .action_execution_repository import canonical, check, encoded, seal
from .action_records import ActionOutcomeRecord
from .db import as_utc


class PerpetualExecutionAdapter:
    def __init__(self, session):
        self.session = session

    def intent(self, identity):
        row = self.session.get(ExecutionIntentRecord, identity, populate_existing=True)
        if row is None:
            raise ValueError("perp_manual_intent_missing")
        value = check(ManualExecutionIntent, row.payload_json, row.payload_hash)
        if (
            row.intent_id,
            row.portfolio_id,
            row.protocol_id,
            row.scope_id,
            row.event_key,
            row.plan_id,
            row.revision_id,
            row.phase,
            as_utc(row.eligible_at),
            as_utc(row.quote_deadline),
        ) != (
            value.id,
            value.account_key,
            value.protocol_ref.ref,
            value.scope_id,
            value.event_key,
            value.plan_id,
            value.revision.ref,
            value.phase,
            value.eligible_at,
            value.quote_deadline,
        ):
            raise ValueError("perp_manual_intent_projection_invalid")
        return value

    def require_idle(self, account_key):
        pending = self.session.scalar(
            select(ExecutionIntentRecord.intent_id)
            .outerjoin(
                ExecutionReceiptRecord,
                ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
            )
            .where(
                ExecutionIntentRecord.portfolio_id == account_key,
                ExecutionReceiptRecord.intent_id.is_(None),
            )
            .limit(1)
        )
        head = self.session.get(ExecutionEpisodeHeadRecord, account_key, populate_existing=True)
        if pending or (head is not None and head.active):
            raise ValueError("perp_manual_account_execution_already_active")

    def append_intent(self, value):
        from .action_execution_repository import ExecutionRepository

        ExecutionRepository(self.session)._writer()
        check(ManualExecutionIntent, encoded(value), value.payload_hash)
        old = self.session.get(ExecutionIntentRecord, value.id, populate_existing=True)
        if old:
            if self.intent(value.id) != value:
                raise ValueError("perp_manual_intent_conflict")
            return value
        self.session.add(
            ExecutionIntentRecord(
                intent_id=value.id,
                portfolio_id=value.account_key,
                protocol_id=value.protocol_ref.ref,
                scope_id=value.scope_id,
                event_key=value.event_key,
                plan_id=value.plan_id,
                revision_id=value.revision.ref,
                phase=value.phase,
                eligible_at=value.eligible_at,
                quote_deadline=value.quote_deadline,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return value

    @staticmethod
    def artifact(row, schema):
        value = check(ManualExecutionArtifact, row.payload_json, row.payload_hash)
        body = json.loads(value.body_json)
        if value.schema_version != schema or canonical(body) != value.body_json:
            raise ValueError("perp_manual_artifact_version_or_body_invalid")
        return value, body

    def result(self, intent):
        row = self.session.scalar(
            select(ExecutionReceiptRecord).where(ExecutionReceiptRecord.intent_id == intent.id)
        )
        if row is None:
            return None
        value, body = self.artifact(row, "perpetual-manual-execution-receipt.v1")
        if (
            (row.receipt_id, row.intent_id, row.portfolio_id, row.status, as_utc(row.decided_at))
            != (
                value.id,
                intent.id,
                intent.account_key,
                body["status"],
                as_utc(__import__("datetime").datetime.fromisoformat(body["decided_at"])),
            )
            or value.intent_id != intent.id
            or value.account_key != intent.account_key
        ):
            raise ValueError("perp_manual_result_projection_invalid")
        return body

    def append_result(self, intent, body, now):
        value = seal(
            ManualExecutionArtifact,
            dict(
                schema_version="perpetual-manual-execution-receipt.v1",
                id="perp_result_" + intent.id,
                account_key=intent.account_key,
                intent_id=intent.id,
                body_json=canonical(body),
            ),
        )
        self.session.add(
            ExecutionReceiptRecord(
                receipt_id=value.id,
                intent_id=intent.id,
                portfolio_id=intent.account_key,
                status=body["status"],
                decided_at=now,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return body

    def append_fill(self, intent, body, now):
        value = seal(
            ManualExecutionArtifact,
            dict(
                schema_version="perpetual-manual-fill.v1",
                id=body["id"],
                account_key=intent.account_key,
                intent_id=intent.id,
                body_json=canonical(body),
            ),
        )
        self.session.add(
            ExecutionFillRecord(
                fill_id=value.id,
                intent_id=intent.id,
                portfolio_id=intent.account_key,
                episode_id=body["episode_id"],
                executed_at=now,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return {"ref": value.id, "hash": value.payload_hash}

    def episode(self, account_key):
        # Mutable index: exact query before the writer, own version CAS after.
        head = self.session.execute(
            select(*ExecutionEpisodeHeadRecord.__table__.columns).where(
                ExecutionEpisodeHeadRecord.portfolio_id == account_key
            )
        ).one_or_none()
        if head is None:
            return None
        row = self.session.get(
            ExecutionEpisodeRecord, (head.episode_id, head.version), populate_existing=True
        )
        if row is None:
            raise ValueError("perp_manual_episode_original_missing")
        value, body = self.artifact(row, "perpetual-manual-episode.v1")
        if (
            head.payload_hash != value.payload_hash
            or row.portfolio_id != account_key
            or body["episode_id"] != head.episode_id
            or body["version"] != head.version
            or value.account_key != account_key
            or head.active != (body["remaining_contracts"] != "0")
            or head.remaining_nonzero != head.active
        ):
            raise ValueError("perp_manual_episode_projection_invalid")
        return body, {"ref": value.id, "hash": value.payload_hash}

    def append_episode(self, intent, body, *, now, expected_version):
        value = seal(
            ManualExecutionArtifact,
            dict(
                schema_version="perpetual-manual-episode.v1",
                id=body["episode_id"] + ":" + str(body["version"]),
                account_key=intent.account_key,
                intent_id=intent.id,
                body_json=canonical(body),
            ),
        )
        self.session.add(
            ExecutionEpisodeRecord(
                episode_id=body["episode_id"],
                version=body["version"],
                portfolio_id=intent.account_key,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        active = body["remaining_contracts"] != "0"
        funding_pending = not active and body.get("funding_status") in {
            "pending_coverage",
            "pending_source_retry",
        }
        retry_at = body.get("funding_next_retry_at")
        if retry_at is not None:
            retry_at = as_utc(datetime.fromisoformat(retry_at))
        values = dict(
            episode_id=body["episode_id"],
            version=body["version"],
            payload_hash=value.payload_hash,
            scope_id=intent.scope_id,
            active=active,
            remaining_nonzero=active,
            next_due_at=now if active else (retry_at or now) if funding_pending else None,
            quote_cursor_available_at=None,
            quote_cursor_received_at=None,
            quote_cursor_capture_id=None,
        )
        if expected_version == 0:
            if self.session.get(
                ExecutionEpisodeHeadRecord, intent.account_key, populate_existing=True
            ):
                raise ValueError("perp_manual_episode_account_conflict")
            self.session.add(ExecutionEpisodeHeadRecord(portfolio_id=intent.account_key, **values))
        elif (
            self.session.execute(
                update(ExecutionEpisodeHeadRecord)
                .where(
                    ExecutionEpisodeHeadRecord.portfolio_id == intent.account_key,
                    ExecutionEpisodeHeadRecord.version == expected_version,
                )
                .values(**values),
                execution_options={"synchronize_session": False},
            ).rowcount
            != 1
        ):
            raise ValueError("perp_manual_episode_cas_conflict")
        self.session.flush()
        return {"ref": value.id, "hash": value.payload_hash}

    def append_outcome(self, intent, body, now):
        value = seal(
            ManualExecutionArtifact,
            dict(
                schema_version="perpetual-manual-outcome.v1",
                id=body["id"],
                account_key=intent.account_key,
                intent_id=intent.id,
                body_json=canonical(body),
            ),
        )
        self.session.add(
            ActionOutcomeRecord(
                outcome_id=value.id,
                opportunity_id=body["opportunity_id"],
                plan_id=intent.plan_id,
                revision_id=intent.revision.ref,
                mode="paper",
                protocol_hash=intent.protocol_ref.hash,
                version=body.get("version", 1),
                status=body["status"],
                due_at=now,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
