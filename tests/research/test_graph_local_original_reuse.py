"""Local reuse bookkeeping only; the genuine joint gate proves authentication.

The controlled mapped observations deliberately fail the existing mapping gate.
Reader doubles here never claim authenticated source, publication or permission.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel
from sqlalchemy import Table, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchNativeObservationContext,
)


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "controlled_original"
    identity: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str]


@pytest.fixture
def session() -> Generator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _Base.metadata.create_all(engine)
    try:
        with Session(engine) as value:
            value.connection().exec_driver_sql("BEGIN")
            yield value
            value.rollback()
    finally:
        engine.dispose()


def test_local_boundary_requires_physical_snapshot_and_expires(session: Session) -> None:
    with binding._local_original_read(session) as boundary:  # pyright: ignore[reportPrivateUsage]
        assert boundary.unchanged()
        assert session.scalars(select(_Row)).all() == []
        assert boundary.unchanged()
    assert not boundary.unchanged()
    session.rollback()
    with binding._local_original_read(session) as fresh:  # pyright: ignore[reportPrivateUsage]
        assert not fresh.unchanged()


@pytest.mark.parametrize("mutation", ["new", "dirty", "deleted", "write", "flush", "transaction"])
def test_local_boundary_rejects_mutation(session: Session, mutation: str) -> None:
    row = _Row(identity=1, value="original")
    session.add(row)
    session.flush()
    with binding._local_original_read(session) as boundary:  # pyright: ignore[reportPrivateUsage]
        assert boundary.unchanged()
        if mutation == "new":
            session.add(_Row(identity=2, value="new"))
        elif mutation == "dirty":
            row.value = "changed"
        elif mutation == "deleted":
            session.delete(row)
        elif mutation == "write":
            session.connection().exec_driver_sql("UPDATE controlled_original SET value='changed'")
        elif mutation == "flush":
            row.value = "changed"
            session.flush()
            assert not session.dirty
        else:
            session.rollback()
            session.connection().exec_driver_sql("BEGIN")
        assert not boundary.unchanged()


def test_unchanged_original_snapshot_never_authorizes_an_expired_lease(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This exact real permission predicate is called on mapped reuse and return.
    now = datetime(2026, 10, 4, tzinfo=UTC)
    table = RunRecord.__table__
    assert isinstance(table, Table)
    table.create(session.connection())
    row = RunRecord(
        run_id="controlled-run",
        event_id="controlled-event",
        status="running",
        created_at=now,
        updated_at=now,
        lease_owner="controlled-owner",
        lease_expires_at=now + timedelta(seconds=1),
    )
    session.add(row)
    session.flush()
    monkeypatch.setattr(binding, "utcnow", lambda: now)
    with binding._local_original_read(session) as boundary:  # pyright: ignore[reportPrivateUsage]
        assert binding._permission(session, row.run_id, row.lease_owner) is row  # pyright: ignore[reportPrivateUsage]
        assert boundary.unchanged()
        monkeypatch.setattr(binding, "utcnow", lambda: now + timedelta(seconds=1))
        assert boundary.unchanged()
        with pytest.raises(PermissionError, match="^research_graph_writer_permission_lost$"):
            binding._permission(session, row.run_id, row.lease_owner)  # pyright: ignore[reportPrivateUsage]


class _MutablePart(BaseModel):
    value: int = 1


def _context() -> ResearchNativeObservationContext:
    return ResearchNativeObservationContext(
        run_id="controlled-run",
        event_id="controlled-event",
        snapshot_id=None,
        request_id="controlled-request",
        dsh_session_id="controlled-session",
        generation=1,
        original_task_request_hash="1" * 64,
        bundle_hash="2" * 64,
        method_hash="3" * 64,
        execution_request_hash="4" * 64,
        first_linked_request_hash="5" * 64,
        prompt_hash="6" * 64,
        rows=(
            ObservedExecutionRef(
                output_id="controlled-output",
                role="controlled-role",
                schema_version="controlled.v1",
                payload_hash="7" * 64,
                recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        ),
    )


@pytest.mark.parametrize(
    "change", ["none", "context", "row_ref", "prepared", "write", "no_snapshot"]
)
def test_mapped_reuse_is_exact_local_and_still_runs_mapping_gate(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    context = _context()
    parts = [_MutablePart() for _ in range(5)]
    # These doubles test only reuse classification. They cannot pass _mapped.
    saved = cast(
        FrozenExecutionPreparation,
        SimpleNamespace(
            method=parts[0],
            link=parts[1],
            prompt=parts[2],
            execution=parts[3],
            integrity=parts[4],
        ),
    )
    counts = {"deep": 0, "fresh_context": 0, "transport": 0}

    def deep(*_args: Any, **_kwargs: Any) -> FrozenExecutionPreparation:
        counts["deep"] += 1
        return saved

    def fresh(*_args: Any, **_kwargs: Any) -> None:
        counts["fresh_context"] += 1

    altered = context
    if change == "context":
        altered = context.model_copy(update={"prompt_hash": "8" * 64})
    elif change == "row_ref":
        altered = context.model_copy(
            update={"rows": (context.rows[0].model_copy(update={"payload_hash": "8" * 64}),)}
        )
    observed = SimpleNamespace(
        context=context,
        mapping_input_ref=context.rows[0],
        latest_transport_ref=context.rows[0],
        mapping_input_generation=1,
        latest_transport_generation=1,
        result=SimpleNamespace(),
        result_content_hash="not-a-verdict",
    )

    def transport(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        counts["transport"] += 1
        if counts["transport"] == 1:
            if change == "prepared":
                parts[0].value = 2
            elif change == "write":
                session.connection().exec_driver_sql("DELETE FROM controlled_original")
        # Wrong phase intentionally exercises the original rejection after reuse.
        return SimpleNamespace(
            context=altered if counts["transport"] % 2 else context,
            phase="accepted",
            reply=None,
        )

    monkeypatch.setattr(binding, "_context_valid", deep)
    monkeypatch.setattr(binding, "_context_binding_valid", fresh)
    def mapped(*_args: Any) -> SimpleNamespace:
        return observed

    monkeypatch.setattr(binding, "read_mapped_observation", mapped)
    monkeypatch.setattr(binding, "read_transport_observation", transport)
    if change == "no_snapshot":
        session.rollback()
    with pytest.raises(ValueError, match="^research_graph_mapping_binding_invalid$"):
        binding._mapped(session, context.rows[0], active=False)  # pyright: ignore[reportPrivateUsage]
    expected = 1 if change == "none" else 2 if change in {"context", "row_ref"} else 3
    assert counts["deep"] == expected
    assert counts["fresh_context"] == 3 - expected
    assert counts["transport"] == 2
    if change == "none":
        # A later call must do its own real deep validation, even in this Session.
        with pytest.raises(ValueError, match="^research_graph_mapping_binding_invalid$"):
            binding._mapped(session, context.rows[0], active=False)  # pyright: ignore[reportPrivateUsage]
        assert counts["deep"] == 2
        assert counts["fresh_context"] == 4
