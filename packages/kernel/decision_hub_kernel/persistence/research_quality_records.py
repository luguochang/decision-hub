"""Immutable per-round / final V3 quality sidecars; additive to existing Run outputs."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ResearchQualitySidecarRecord(Base):
    __tablename__ = "research_quality_sidecars"
    __table_args__ = (
        UniqueConstraint("run_id", "logical_round", "phase", name="uq_research_quality_phase"),
        Index("ix_research_quality_run", "run_id", "created_at"),
    )

    sidecar_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128))
    logical_round: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(16))
    source_result_hash: Mapped[str] = mapped_column(String(64))
    projected_result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_context_hash: Mapped[str] = mapped_column(String(64))
    quality_decision_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    publication_mode: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
