"""Internal perpetual paper ledger; immutable revisions and a shared account CAS head."""

from sqlalchemy import DDL, CheckConstraint, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PerpetualPaperRevisionRecord(Base):
    __tablename__ = "perpetual_paper_revisions"
    __table_args__ = (
        UniqueConstraint("account_key", "state_version", name="uq_perp_paper_revision"),
        CheckConstraint("state_version >= 1", name="ck_perp_paper_revision_version"),
    )
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(128))
    state_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class PerpetualPaperHeadRecord(Base):
    __tablename__ = "perpetual_paper_heads"
    __table_args__ = (
        UniqueConstraint("owner_id", "account_id", name="uq_perp_paper_owner_account"),
        CheckConstraint("state_version >= 1", name="ck_perp_paper_head_version"),
    )
    account_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(128))
    current_revision_id: Mapped[str] = mapped_column(String(128))
    state_version: Mapped[int] = mapped_column(Integer)


class PerpetualPaperCapacityRecord(Base):
    __tablename__ = "perpetual_paper_quote_capacity"
    account_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    quote_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_side: Mapped[str] = mapped_column(String(4), primary_key=True)
    consumed_contracts: Mapped[str] = mapped_column(String(80))
    state_version: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("order_side IN ('buy','sell')", name="ck_perp_paper_capacity_side"),
        CheckConstraint("state_version >= 1", name="ck_perp_paper_capacity_version"),
    )


_table = PerpetualPaperRevisionRecord.__table__
event.listen(
    _table,
    "after_create",
    DDL(
        "CREATE TRIGGER trg_perpetual_paper_revisions_immutable_insert "
        "BEFORE INSERT ON perpetual_paper_revisions WHEN EXISTS ("
        "SELECT 1 FROM perpetual_paper_revisions WHERE revision_id = NEW.revision_id OR "
        "(account_key = NEW.account_key AND state_version = NEW.state_version)) "
        "BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: perpetual_paper_revisions'); END"
    ).execute_if(dialect="sqlite"),
)
for _operation in ("UPDATE", "DELETE"):
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER trg_perpetual_paper_revisions_immutable_{_operation.lower()} "
            f"BEFORE {_operation} ON perpetual_paper_revisions "
            "BEGIN SELECT RAISE(ABORT, 'perpetual_paper_revisions_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )
