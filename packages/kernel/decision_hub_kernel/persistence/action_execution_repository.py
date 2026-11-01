"""Typed same-session W6 repository draft. No commit, IO or execution authority."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionEpisodeV1,
    ActionExecutionIntentV1,
    ActionExecutionProtocolV1,
    ActionExecutionReceiptV1,
    ActionFillV1,
    ActionFillV2,
    ActionOutcomeV2,
    ActionPaperCheckpointV1,
    ActionPaperPortfolioIdentityV1,
)
from packages.kernel.decision_hub_kernel.decision.action_paper_accounting import Account
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionEpisodeRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionProtocolRecord,
    ExecutionReceiptRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_paper_records import (
    AccountHead,
    Checkpoint,
    PortfolioIdentity,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import ActionOutcomeRecord
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("execution_decimal_finite_required")
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"


def seal[M: BaseModel](cls: type[M], body: dict) -> M:
    parsed = cls.model_validate(body | {"payload_hash": "0" * 64})
    value = parsed.model_dump(mode="json", by_alias=True)
    value["payload_hash"] = canonical_hash({k: v for k, v in value.items() if k != "payload_hash"})
    return cls.model_validate(value)


def check[M: BaseModel](cls: type[M], raw: str, digest: str) -> M:
    payload = json.loads(raw)
    parsed = cls.model_validate(payload)
    if (
        canonical(payload) != raw
        or payload.get("payload_hash") != digest
        or canonical_hash({k: v for k, v in payload.items() if k != "payload_hash"}) != digest
    ):
        raise ValueError("execution_original_hash_mismatch")
    return parsed


def check_fill(raw: str, digest: str) -> ActionFillV1 | ActionFillV2:
    kind = json.loads(raw).get("schema_version")
    cls = {"action-fill.v1": ActionFillV1, "action-fill.v2": ActionFillV2}.get(kind)
    if cls is None:
        raise ValueError("execution_fill_version_unsupported")
    return check(cls, raw, digest)


def encoded(value: BaseModel) -> str:
    return canonical(value.model_dump(mode="json", by_alias=True))


def identity_for(
    protocol: ActionExecutionProtocolV1,
    *,
    scope_id: str,
    horizon: str,
    scenario: str,
    method_ref: str,
    channel: str,
    delay_seconds: int,
    owner_id: str | None,
) -> ActionPaperPortfolioIdentityV1:
    methods = {item.ref: item.hash for item in protocol.methods}
    targets = {item.horizon: item for item in protocol.targets}
    if (
        scope_id not in protocol.scopes
        or horizon not in targets
        or method_ref not in methods
        or channel not in protocol.channels
        or delay_seconds not in protocol.delays
    ):
        raise ValueError("execution_dimension_not_registered")
    # Keep the reviewed ledger identity's exact business projection/key names.
    identity = PortfolioIdentity(
        protocol.payload_hash,
        protocol.trial_id,
        scope_id,
        scenario,
        method_ref,
        channel,
        delay_seconds,
        horizon,
        targets[horizon].target_spec_hash,
        methods[method_ref],
        owner_id,
    )
    return ActionPaperPortfolioIdentityV1.model_validate(
        dict(
            schema_version="action-paper-portfolio-identity.v1",
            id=identity.id,
            identity_hash=canonical_hash(asdict(identity)),
            protocol_ref=protocol.id,
            protocol_hash=protocol.payload_hash,
            trial_id=protocol.trial_id,
            scope_id=scope_id,
            horizon=horizon,
            target_spec_hash=identity.target_spec_hash,
            scenario=scenario,
            method_ref=method_ref,
            method_hash=identity.method_hash,
            channel=channel,
            delay_seconds=delay_seconds,
            owner_id=owner_id,
        )
    )


def account_values(account: Account) -> dict[str, str]:
    return {key: number(value) for key, value in asdict(account).items() if key != "scope"}


def account_for(checkpoint: ActionPaperCheckpointV1) -> Account:
    return Account(
        checkpoint.portfolio_identity.scope_id,
        **{k: Decimal(v) for k, v in checkpoint.account_after.model_dump().items()},
    )


def episode_ref(value: ActionExecutionEpisodeV1) -> str:
    return f"{value.id}:v{value.version}"


class ExecutionRepository:
    def __init__(self, session: Session):
        self.session = session

    def _writer(self):
        connection = self.session.connection()
        if connection.dialect.name == "sqlite" and not getattr(
            connection.connection.driver_connection, "in_transaction", False
        ):
            raise ValueError("execution_explicit_writer_required")

    def protocol(self, protocol_id: str) -> ActionExecutionProtocolV1:
        row = self.session.get(ExecutionProtocolRecord, protocol_id, populate_existing=True)
        if row is None:
            raise ValueError("execution_protocol_missing")
        if json.loads(row.payload_json).get("schema_version") != "action-execution-protocol.v1":
            raise ValueError("execution_protocol_version_requires_explicit_reader")
        value = check(ActionExecutionProtocolV1, row.payload_json, row.payload_hash)
        if (value.id, value.trial_id, value.registered_at, value.T0, value.T1) != (
            row.protocol_id,
            row.trial_id,
            as_utc(row.registered_at),
            as_utc(row.start_at),
            as_utc(row.end_at),
        ):
            raise ValueError("execution_protocol_projection_mismatch")
        return value

    def perpetual_protocol(self, protocol_id: str):
        from ..ports.perpetual_execution_protocol import PerpetualPaperExecutionProtocol

        row = self.session.get(ExecutionProtocolRecord, protocol_id, populate_existing=True)
        if row is None:
            raise ValueError("execution_protocol_missing")
        if json.loads(row.payload_json).get("schema_version") != "perp-quote-paper-protocol.v1":
            raise ValueError("execution_protocol_version_requires_explicit_reader")
        value = check(PerpetualPaperExecutionProtocol, row.payload_json, row.payload_hash)
        if (value.id, value.trial_id, value.registered_at, value.T0, value.T1) != (
            row.protocol_id,
            row.trial_id,
            as_utc(row.registered_at),
            as_utc(row.start_at),
            as_utc(row.end_at),
        ):
            raise ValueError("execution_protocol_projection_mismatch")
        return value

    def register_perpetual_protocol(self, value, *, now: datetime):
        from ..ports.perpetual_execution_protocol import PerpetualPaperExecutionProtocol

        self._writer()
        value = PerpetualPaperExecutionProtocol.model_validate(value)
        check(PerpetualPaperExecutionProtocol, encoded(value), value.payload_hash)
        old = self.session.get(ExecutionProtocolRecord, value.id, populate_existing=True)
        if old is not None:
            if self.perpetual_protocol(value.id) != value:
                raise ValueError("execution_protocol_conflict")
            return value
        if not as_utc(now) <= value.registered_at <= value.T0:
            raise ValueError("execution_protocol_prospective_required")
        self.session.add(
            ExecutionProtocolRecord(
                protocol_id=value.id,
                trial_id=value.trial_id,
                registered_at=value.registered_at,
                start_at=value.T0,
                end_at=value.T1,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return value

    def perpetual_manual_protocol(self, protocol_id: str):
        from ..ports.perpetual_execution_protocol import PerpetualManualPaperProtocol

        row = self.session.get(ExecutionProtocolRecord, protocol_id, populate_existing=True)
        if row is None:
            raise ValueError("execution_protocol_missing")
        if json.loads(row.payload_json).get("schema_version") != "perp-manual-paper-protocol.v1":
            raise ValueError("execution_protocol_version_requires_explicit_reader")
        value = check(PerpetualManualPaperProtocol, row.payload_json, row.payload_hash)
        if (value.id, value.trial_id, value.registered_at, value.T0, value.T1) != (
            row.protocol_id, row.trial_id, as_utc(row.registered_at),
            as_utc(row.start_at), as_utc(row.end_at),
        ):
            raise ValueError("execution_protocol_projection_mismatch")
        return value

    def register_perpetual_manual_protocol(self, value, *, now: datetime):
        from ..ports.perpetual_execution_protocol import PerpetualManualPaperProtocol

        self._writer()
        value = PerpetualManualPaperProtocol.model_validate(value)
        check(PerpetualManualPaperProtocol, encoded(value), value.payload_hash)
        if self.session.get(ExecutionProtocolRecord, value.id, populate_existing=True):
            if self.perpetual_manual_protocol(value.id) != value:
                raise ValueError("execution_protocol_conflict")
            return value
        if not as_utc(now) <= value.registered_at <= value.T0:
            raise ValueError("execution_protocol_prospective_required")
        self.session.add(ExecutionProtocolRecord(
            protocol_id=value.id, trial_id=value.trial_id, registered_at=value.registered_at,
            start_at=value.T0, end_at=value.T1, payload_json=encoded(value),
            payload_hash=value.payload_hash,
        ))
        self.session.flush()
        return value

    def register_protocol(self, value: ActionExecutionProtocolV1, *, now: datetime):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        old = self.session.get(ExecutionProtocolRecord, value.id)
        if old:
            if self.protocol(value.id) != value:
                raise ValueError("execution_protocol_conflict")
            return value
        if not now <= value.registered_at <= value.T0 or len(value.scopes) != 1:
            raise ValueError("execution_protocol_prospective_single_scope_required")
        self.session.add(
            ExecutionProtocolRecord(
                protocol_id=value.id,
                trial_id=value.trial_id,
                registered_at=value.registered_at,
                start_at=value.T0,
                end_at=value.T1,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return value

    def intent(self, intent_id: str) -> ActionExecutionIntentV1:
        row = self.session.get(ExecutionIntentRecord, intent_id, populate_existing=True)
        if row is None:
            raise ValueError("execution_intent_missing")
        value = check(ActionExecutionIntentV1, row.payload_json, row.payload_hash)
        if (
            value.id,
            value.portfolio_id,
            value.event_key,
            value.plan_id,
            value.revision_id,
            value.phase,
            value.eligible_at,
            value.quote_deadline,
        ) != (
            row.intent_id,
            row.portfolio_id,
            row.event_key,
            row.plan_id,
            row.revision_id,
            row.phase,
            as_utc(row.eligible_at),
            as_utc(row.quote_deadline),
        ):
            raise ValueError("execution_intent_projection_mismatch")
        identity = self.account(value.portfolio_id).portfolio_identity
        if (
            identity.protocol_ref != row.protocol_id
            or identity.scope_id != row.scope_id
            or identity.identity_hash != value.identity_hash
        ):
            raise ValueError("execution_intent_identity_mismatch")
        return value

    def intent_by_key(self, portfolio_id: str, event_key: str) -> ActionExecutionIntentV1 | None:
        row = self.session.scalar(
            select(ExecutionIntentRecord).filter_by(portfolio_id=portfolio_id, event_key=event_key)
        )
        return self.intent(row.intent_id) if row else None

    def append_intent(self, value: ActionExecutionIntentV1):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        old = self.intent_by_key(value.portfolio_id, value.event_key)
        if old:
            if old != value:
                raise ValueError("execution_intent_conflict")
            return old
        identity = self.account(value.portfolio_id).portfolio_identity
        self.session.add(
            ExecutionIntentRecord(
                intent_id=value.id,
                portfolio_id=value.portfolio_id,
                protocol_id=identity.protocol_ref,
                scope_id=identity.scope_id,
                event_key=value.event_key,
                plan_id=value.plan_id,
                revision_id=value.revision_id,
                phase=value.phase,
                eligible_at=value.eligible_at,
                quote_deadline=value.quote_deadline,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return value

    def checkpoint(self, reference: str, digest: str | None = None) -> ActionPaperCheckpointV1:
        # Stored hash is the immutable PK; IDs remain independently checked.
        row = (
            self.session.get(Checkpoint, digest, populate_existing=True)
            if digest
            else self.session.scalar(
                select(Checkpoint).where(Checkpoint.checkpoint_hash == reference)
            )
        )
        if row is None:
            raise ValueError("execution_checkpoint_missing")
        value = check(ActionPaperCheckpointV1, row.payload_json, row.checkpoint_hash)
        if reference not in (value.id, value.payload_hash) or (
            value.portfolio_id,
            value.version,
            value.execution_key,
            value.request_hash,
            value.previous_hash,
        ) != (row.account_id, row.version, row.execution_key, row.request_hash, row.previous_hash):
            raise ValueError("execution_checkpoint_projection_mismatch")
        protocol = self.protocol(value.portfolio_identity.protocol_ref)
        expected = identity_for(
            protocol,
            **{
                k: getattr(value.portfolio_identity, k)
                for k in (
                    "scope_id",
                    "horizon",
                    "scenario",
                    "method_ref",
                    "channel",
                    "delay_seconds",
                    "owner_id",
                )
            },
        )
        if expected != value.portfolio_identity:
            raise ValueError("execution_portfolio_identity_mismatch")
        return value

    def account(self, portfolio_id: str) -> ActionPaperCheckpointV1:
        head = self.session.get(AccountHead, portfolio_id, populate_existing=True)
        if head is None:
            raise ValueError("execution_portfolio_missing")
        value = self.checkpoint(head.checkpoint_hash)
        if value.version != head.version or head.identity_json != encoded(value.portfolio_identity):
            raise ValueError("execution_account_head_mismatch")
        return value

    def append_checkpoint(self, value: ActionPaperCheckpointV1):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        head = self.session.get(AccountHead, value.portfolio_id, populate_existing=True)
        if value.version == 1:
            if head:
                raise ValueError("execution_portfolio_already_registered")
            self.session.add(
                AccountHead(
                    account_id=value.portfolio_id,
                    identity_json=encoded(value.portfolio_identity),
                    version=1,
                    checkpoint_hash=value.payload_hash,
                )
            )
        else:
            previous = self.checkpoint(value.previous_ref, value.previous_hash)
            if (
                previous.portfolio_identity != value.portfolio_identity
                or previous.version + 1 != value.version
                or value.account_before_hash
                != canonical_hash(previous.account_after.model_dump(mode="json"))
            ):
                raise ValueError("execution_checkpoint_parent_mismatch")
            changed = self.session.execute(
                update(AccountHead)
                .where(
                    AccountHead.account_id == value.portfolio_id,
                    AccountHead.version == previous.version,
                    AccountHead.checkpoint_hash == previous.payload_hash,
                )
                .values(version=value.version, checkpoint_hash=value.payload_hash)
                .execution_options(synchronize_session=False)
            )
            if not isinstance(changed, CursorResult) or changed.rowcount != 1:
                raise ValueError("execution_account_cas_lost")
        self.session.add(
            Checkpoint(
                checkpoint_hash=value.payload_hash,
                account_id=value.portfolio_id,
                version=value.version,
                execution_key=value.execution_key,
                request_hash=value.request_hash,
                previous_hash=value.previous_hash,
                payload_json=encoded(value),
            )
        )
        self.session.flush()

    def episode(self, reference: str, digest: str) -> ActionExecutionEpisodeV1:
        identity, version = reference.rsplit(":v", 1)
        row = self.session.get(
            ExecutionEpisodeRecord, (identity, int(version)), populate_existing=True
        )
        if row is None or row.payload_hash != digest:
            raise ValueError("execution_episode_missing_or_changed")
        value = check(ActionExecutionEpisodeV1, row.payload_json, row.payload_hash)
        if (value.id, value.version, value.portfolio_id) != (
            row.episode_id,
            row.version,
            row.portfolio_id,
        ):
            raise ValueError("execution_episode_projection_mismatch")
        for checkpoint in value.checkpoint_refs:
            prior = self.checkpoint(checkpoint.ref, checkpoint.hash)
            if prior.portfolio_id != value.portfolio_id or prior.created_at > value.recorded_at:
                raise ValueError("execution_episode_checkpoint_mismatch")
        return value

    def open_episode(self, portfolio_id: str) -> ActionExecutionEpisodeV1 | None:
        row = self.session.get(ExecutionEpisodeHeadRecord, portfolio_id, populate_existing=True)
        if row is None:
            return None
        value = self.episode(f"{row.episode_id}:v{row.version}", row.payload_hash)
        if row.active != (
            value.status in {"open", "partially_closed"}
        ) or row.remaining_nonzero != (Decimal(value.remaining_quantity) > 0):
            raise ValueError("execution_episode_head_mismatch")
        return value if row.active else None

    def first_episode(self, episode_id: str) -> ActionExecutionEpisodeV1:
        row = self.session.get(ExecutionEpisodeRecord, (episode_id, 1), populate_existing=True)
        if row is None:
            raise ValueError("execution_episode_initial_original_missing")
        return self.episode(f"{episode_id}:v1", row.payload_hash)

    def append_episode(self, value: ActionExecutionEpisodeV1, *, next_due_at: datetime | None):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        old = self.session.get(
            ExecutionEpisodeHeadRecord, value.portfolio_id, populate_existing=True
        )
        if value.version == 1:
            if old and old.active:
                raise ValueError("execution_open_episode_conflict")
        elif old is None or (old.episode_id, old.version, old.payload_hash) != (
            value.id,
            value.version - 1,
            value.previous_hash,
        ):
            raise ValueError("execution_episode_cas_lost")
        self.session.add(
            ExecutionEpisodeRecord(
                episode_id=value.id,
                version=value.version,
                portfolio_id=value.portfolio_id,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        values = dict(
            episode_id=value.id,
            version=value.version,
            payload_hash=value.payload_hash,
            scope_id=self.account(value.portfolio_id).portfolio_identity.scope_id,
            active=value.status in {"open", "partially_closed"},
            remaining_nonzero=Decimal(value.remaining_quantity) > 0,
            next_due_at=next_due_at,
        )
        if value.version == 1:
            values.update(
                quote_cursor_available_at=None,
                quote_cursor_received_at=None,
                quote_cursor_capture_id=None,
            )
        if old:
            changed = self.session.execute(
                update(ExecutionEpisodeHeadRecord)
                .where(
                    ExecutionEpisodeHeadRecord.portfolio_id == value.portfolio_id,
                    ExecutionEpisodeHeadRecord.version == old.version,
                    ExecutionEpisodeHeadRecord.payload_hash == old.payload_hash,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if not isinstance(changed, CursorResult) or changed.rowcount != 1:
                raise ValueError("execution_episode_cas_lost")
        else:
            self.session.add(ExecutionEpisodeHeadRecord(portfolio_id=value.portfolio_id, **values))
        self.session.flush()

    def append_fill(self, value: ActionFillV1 | ActionFillV2):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        self.session.add(
            ExecutionFillRecord(
                fill_id=value.id,
                intent_id=value.intent_ref.removeprefix("intent:"),
                portfolio_id=value.portfolio_id,
                episode_id=value.episode_id,
                executed_at=value.executed_at,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()

    def episode_fills(
        self, episode_id: str, *, through: datetime
    ) -> tuple[ActionFillV1 | ActionFillV2, ...]:
        rows = self.session.scalars(
            select(ExecutionFillRecord)
            .where(
                ExecutionFillRecord.episode_id == episode_id,
                ExecutionFillRecord.executed_at <= through,
            )
            .order_by(ExecutionFillRecord.executed_at, ExecutionFillRecord.fill_id)
            .limit(129)
        ).all()
        if len(rows) > 128:
            raise ValueError("execution_episode_fill_read_budget_exceeded")
        result = []
        for row in rows:
            value = check_fill(row.payload_json, row.payload_hash)
            if (
                value.id,
                value.episode_id,
                value.portfolio_id,
                value.executed_at,
                value.intent_ref,
            ) != (
                row.fill_id,
                row.episode_id,
                row.portfolio_id,
                as_utc(row.executed_at),
                "intent:" + row.intent_id,
            ):
                raise ValueError("execution_fill_projection_mismatch")
            result.append(value)
        return tuple(result)

    def latest_outcome(
        self, plan_id: str, revision_id: str, identity_hash: str
    ) -> ActionOutcomeV2 | None:
        row = self.session.scalar(
            select(ActionOutcomeRecord)
            .filter_by(
                plan_id=plan_id, revision_id=revision_id, protocol_hash=identity_hash, mode="paper"
            )
            .order_by(ActionOutcomeRecord.version.desc())
            .limit(1)
        )
        return check(ActionOutcomeV2, row.payload_json, row.payload_hash) if row else None

    def append_outcome(self, value: ActionOutcomeV2):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        self.session.add(
            ActionOutcomeRecord(
                outcome_id=value.id,
                opportunity_id=value.opportunity_id,
                plan_id=value.plan_id,
                revision_id=value.revision_id,
                mode=value.mode,
                protocol_hash=value.protocol_hash,
                version=value.version,
                status=value.status,
                due_at=value.due_at,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()

    def result(self, intent_id: str) -> ActionExecutionReceiptV1 | None:
        row = self.session.scalar(select(ExecutionReceiptRecord).filter_by(intent_id=intent_id))
        if row is None:
            return None
        value = check(ActionExecutionReceiptV1, row.payload_json, row.payload_hash)
        intent = self.intent(intent_id)
        if (
            value.id,
            value.intent_ref,
            value.intent_hash,
            value.portfolio_id,
            value.status,
            value.decided_at,
        ) != (
            row.receipt_id,
            "intent:" + intent.id,
            intent.payload_hash,
            row.portfolio_id,
            row.status,
            as_utc(row.decided_at),
        ):
            raise ValueError("execution_result_intent_mismatch")
        if value.outcome_ref:
            outcome = self.session.get(
                ActionOutcomeRecord, value.outcome_ref, populate_existing=True
            )
            if outcome is None or outcome.payload_hash != value.outcome_hash:
                raise ValueError("execution_result_outcome_missing")
            actual = check(ActionOutcomeV2, outcome.payload_json, outcome.payload_hash)
            if (
                actual.execution_ref != value.intent_ref
                or actual.execution_hash != intent.payload_hash
                or actual.execution_status != value.status
                or actual.portfolio_id != value.portfolio_id
            ):
                raise ValueError("execution_result_outcome_mismatch")
        if value.checkpoint_ref:
            checkpoint = self.checkpoint(value.checkpoint_ref, value.checkpoint_hash)
            if (
                checkpoint.intent_ref != value.intent_ref
                or checkpoint.intent_hash != intent.payload_hash
                or checkpoint.episode_ref != value.episode_ref
                or checkpoint.episode_hash != value.episode_hash
                or checkpoint.fill_ref != value.fill_ref
                or checkpoint.fill_hash != value.fill_hash
            ):
                raise ValueError("execution_result_checkpoint_mismatch")
        if value.episode_ref:
            self.episode(value.episode_ref, value.episode_hash)
        if value.fill_ref:
            fill = self.session.get(ExecutionFillRecord, value.fill_ref, populate_existing=True)
            if fill is None or fill.payload_hash != value.fill_hash:
                raise ValueError("execution_result_fill_missing")
            actual = check_fill(fill.payload_json, fill.payload_hash)
            if (
                actual.intent_ref != value.intent_ref
                or actual.intent_hash != intent.payload_hash
                or actual.portfolio_id != value.portfolio_id
            ):
                raise ValueError("execution_result_fill_mismatch")
        return value

    def append_result(self, value: ActionExecutionReceiptV1):
        self._writer()
        check(type(value), encoded(value), value.payload_hash)
        self.session.add(
            ExecutionReceiptRecord(
                receipt_id=value.id,
                intent_id=value.intent_ref.removeprefix("intent:"),
                portfolio_id=value.portfolio_id,
                status=value.status,
                decided_at=value.decided_at,
                payload_json=encoded(value),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()
        return self.result(value.intent_ref.removeprefix("intent:"))
