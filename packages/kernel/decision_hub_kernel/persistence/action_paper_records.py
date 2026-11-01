"""Internal append-only paper-account repository; it grants no execution authority.

The caller owns one BEGIN IMMEDIATE transaction and authenticates action, quote,
intent and current risk before append. Tests use a private DB only. These models
are drafts pending a generated execution contract and real migration rehearsal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from sqlalchemy import DDL, CheckConstraint, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from packages.kernel.decision_hub_kernel.persistence.db import Base

DraftBase = Base


class AccountHead(DraftBase):
    __tablename__ = "action_paper_accounts"
    __table_args__ = (CheckConstraint("version >= 1"),)
    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    identity_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    checkpoint_hash: Mapped[str] = mapped_column(String(64))


class Checkpoint(DraftBase):
    __tablename__ = "action_paper_checkpoints"
    __table_args__ = (
        UniqueConstraint("account_id", "version"),
        UniqueConstraint("account_id", "execution_key"),
        CheckConstraint("version >= 1"),
    )
    checkpoint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    execution_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)


for operation in ("UPDATE", "DELETE"):
    event.listen(
        Checkpoint.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER paper_checkpoint_no_{operation.lower()} "
            f"BEFORE {operation} ON action_paper_checkpoints BEGIN "
            "SELECT RAISE(ABORT, 'paper_checkpoint_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )

event.listen(
    Checkpoint.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER paper_checkpoint_no_replace BEFORE INSERT "
        "ON action_paper_checkpoints WHEN EXISTS (SELECT 1 FROM action_paper_checkpoints "
        "WHERE checkpoint_hash=NEW.checkpoint_hash OR "
        "(account_id=NEW.account_id AND (version=NEW.version OR "
        "execution_key=NEW.execution_key))) BEGIN "
        "SELECT RAISE(ABORT, 'paper_checkpoint_immutable'); END"
    ).execute_if(dialect="sqlite"),
)


def body(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(body(value).encode()).hexdigest()


def text(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip() != value
        or len(value) > 128
    ):
        raise ValueError("paper_ledger_identity_invalid")
    return value


@dataclass(frozen=True)
class PortfolioIdentity:
    protocol_hash: str
    trial_id: str
    scope: str
    scenario: str
    method_ref: str
    channel: str
    delay_seconds: int
    horizon: str
    target_spec_hash: str
    method_hash: str
    owner_id: str | None = None

    def __post_init__(self):
        for value in (self.trial_id, self.scope, self.method_ref):
            text(value)
        for name, value in (
            ("protocol", self.protocol_hash),
            ("target_spec", self.target_spec_hash),
            ("method", self.method_hash),
        ):
            if len(value) != 64 or set(value) - set("0123456789abcdef"):
                raise ValueError(f"paper_ledger_{name}_hash_invalid")
        if self.scenario not in ("cash", "existing_long"):
            raise ValueError("paper_ledger_scenario_invalid")
        if self.channel not in ("shadow_engine", "pc_visible"):
            raise ValueError("paper_ledger_channel_invalid")
        if type(self.delay_seconds) is not int or self.delay_seconds not in (0, 60, 300):
            raise ValueError("paper_ledger_delay_invalid")
        if self.horizon not in ("30m", "24h", "72h"):
            raise ValueError("paper_ledger_horizon_invalid")
        if self.channel == "shadow_engine" and self.owner_id is not None:
            raise ValueError("paper_ledger_shadow_owner_forbidden")
        if self.channel == "pc_visible":
            if self.owner_id is None:
                raise ValueError("paper_ledger_pc_owner_required")
            text(self.owner_id)

    @property
    def id(self) -> str:
        return "paper_" + digest(asdict(self))
