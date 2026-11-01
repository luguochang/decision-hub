"""Private SQL consumer mechanics. Synthetic review evidence is not market qualification."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from apps.hub_worker.market_components import InstalledComponent, MarketComponents
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentQualification
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"


def installed_one(value, parameters):
    return {"value": value["value"] * parameters["factor"], "version": "one"}


def installed_two(value, parameters):
    return {"value": value["value"] * parameters["factor"], "version": "two"}


def baseline(value):
    return {"value": value["value"], "version": "published-baseline"}


class FixtureMechanicalReader:
    """Explicit synthetic receipt, solely to reach CAS/consumer mechanics."""

    available = True

    def verify(self, manifest, trial):
        return ComponentQualification(
            trial_id=trial.trial_id,
            manifest_hash=manifest.payload_hash,
            protocol_hash=trial.protocol_hash,
            original_refs=("fixture://component-mechanics-not-financial",),
            original_hashes=(canonical_hash({"mechanics_only": True}),),
            known_at=trial.end_at,
            status="passed" if self.available else "unavailable",
            reasons=() if self.available else ("fixture_original_unavailable",),
            sample_qualified=self.available,
            cost_verified=self.available,
        )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "bootstrap"))

    def private(_dialect, _record, args, _kwargs):
        assert (
            Path(str(args[0]).removeprefix("file:").split("?")[0])
            .resolve()
            .is_relative_to(tmp_path.resolve())
        )

    event.listen(Engine, "do_connect", private)
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'consumers.sqlite3'}")
    db.create_all()
    now = [NOW - timedelta(days=2)]
    reader = FixtureMechanicalReader()
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    resource = tmp_path / "installed-resource.json"
    resource.write_text('{"installed":true}')
    one = InstalledComponent(
        "feature",
        "fixed-test-feature",
        SCOPE,
        "one",
        "input.v1",
        "output.v1",
        installed_one,
        {"consumer": Path(__file__)},
        {"parameters": resource},
        {"factor": 2},
    )
    two = InstalledComponent(
        "feature",
        "fixed-test-feature",
        SCOPE,
        "two",
        "input.v1",
        "output.v1",
        installed_two,
        {"consumer": Path(__file__)},
        {"parameters": resource},
        {"factor": 3},
    )
    consumers = MarketComponents(registry, (one, two))
    consumers.register_installed()
    for component in (one, two):
        registry.shadow(
            "trial-" + component.version_id,
            version_id=component.version_id,
            protocol_hash="a" * 64,
            baseline_version_id=None,
            start_at=NOW - timedelta(days=1),
            end_at=NOW - timedelta(hours=1),
        )
    now[0] = NOW
    try:
        yield db, registry, consumers, reader, now, resource
    finally:
        db.engine.dispose()
        event.remove(Engine, "do_connect", private)


def approved(registry, version, generation):
    review = registry.review(
        "review-" + version,
        trial_id="trial-" + version,
        reviewer_ref="fixture://explicit-mechanical-review",
    )
    return registry.activate(
        "activate-" + version, review_id=review.review_id, expected_generation=generation
    )


def consume(db, consumers, request, *, cutoff=NOW, payload=None):
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return consumers(
            session,
            kind="feature",
            component_ref="fixed-test-feature",
            scope_id=SCOPE,
            payload=payload or {"value": 4},
            fallback=baseline,
            request_id=request,
            known_at=NOW,
            cutoff_at=cutoff,
        )


def test_actual_consumption_baseline_upgrade_rollback_and_original_receipts(runtime):
    db, registry, consumers, _, _, _ = runtime
    initial, missing = consume(db, consumers, "original-baseline")
    assert (
        initial["version"] == "published-baseline"
        and missing["state"] == "baseline_not_registry_approved"
    )
    assert not missing["probability_qualified"] and not missing["trading_authority"]
    approved(registry, "one", 0)
    one, first = consume(db, consumers, "first")
    first_body = canonical_hash(first)
    assert one == {"value": 8, "version": "one"} and first["state"] == "selected"
    approved(registry, "two", 1)
    two, second = consume(db, consumers, "second")
    assert two == {"value": 12, "version": "two"}
    assert second["selection"]["pointer"]["generation"] == 2
    # Existing consumption request returns its immutable original, not current pointer state.
    assert canonical_hash(consume(db, consumers, "first")[1]) == first_body
    registry.rollback(
        "rollback",
        kind="feature",
        component_ref="fixed-test-feature",
        scope_id=SCOPE,
        expected_generation=2,
    )
    reverted, final = consume(db, consumers, "reverted")
    assert reverted == one and final["selection"]["pointer"]["generation"] == 3
    assert (
        final["selection"]["original_readset_hash"] != first["selection"]["original_readset_hash"]
    )
    assert (
        registry.current(
            kind="feature", component_ref="fixed-test-feature", scope_id=SCOPE
        ).generation
        == 3
    )
    with db.session() as session:
        rows = session.scalars(
            select(MarketComponentAuditRecord).where(
                MarketComponentAuditRecord.request_id.like("component-consume:%")
            )
        ).all()
        assert len(rows) == 4 and all(row.audit_id.startswith("cconsume_") for row in rows)


def test_bad_or_unavailable_pointer_never_falls_back_and_dirty_scope_is_rejected(runtime):
    db, registry, consumers, reader, _, resource = runtime
    approved(registry, "one", 0)
    with pytest.raises(ValueError, match="component_pointer_after_cutoff"):
        consume(db, consumers, "past", cutoff=NOW - timedelta(seconds=1))
    reader.available = False
    with pytest.raises(ValueError, match="component_original_qualification_unavailable"):
        consume(db, consumers, "expired-proof")
    reader.available = True
    resource.write_text('{"installed":false}')
    with pytest.raises(ValueError, match="installed_identity_changed"):
        consume(db, consumers, "changed-file")
    resource.write_text('{"installed":true}')
    with db.session() as session:
        with pytest.raises(ValueError, match="physical_snapshot"):
            consumers.freeze_in_session(
                session,
                kind="feature",
                component_ref="fixed-test-feature",
                scope_id=SCOPE,
                cutoff_at=NOW,
            )
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        session.add(
            MarketComponentAuditRecord(
                audit_id="dirty",
                request_id="dirty",
                pointer_id="a" * 64,
                created_at=NOW,
                payload_json="{}",
                payload_hash="b" * 64,
            )
        )
        with pytest.raises(ValueError, match="physical_snapshot"):
            consumers.freeze_in_session(
                session,
                kind="feature",
                component_ref="fixed-test-feature",
                scope_id=SCOPE,
                cutoff_at=NOW,
            )
        session.rollback()
    with db.session() as session:
        assert (
            session.scalar(
                select(MarketComponentAuditRecord).where(
                    MarketComponentAuditRecord.request_id.like("component-consume:%")
                )
            )
            is None
        )


def test_actual_audit_writer_mutation_rolls_back_consumption(runtime):
    db, registry, consumers, _, _, _ = runtime
    approved(registry, "one", 0)
    target = ["pointer"]

    def tamper(session, _context):
        if any(
            isinstance(row, MarketComponentAuditRecord) and row.audit_id.startswith("cconsume_")
            for row in session.new
        ):
            statement = (
                "UPDATE market_component_pointers SET generation=generation+1"
                if target[0] == "pointer"
                else "UPDATE market_component_audits SET pointer_id='changed' "
                "WHERE audit_id LIKE 'cconsume_%'"
            )
            session.connection().exec_driver_sql(statement)

    from sqlalchemy.orm import Session

    event.listen(Session, "after_flush", tamper)
    try:
        with pytest.raises(ValueError, match="pointer_audit_mismatch"):
            consume(db, consumers, "writer-poison")
        target[0] = "audit"
        with pytest.raises(ValueError, match="consumption_writer_changed"):
            consume(db, consumers, "audit-poison")
    finally:
        event.remove(Session, "after_flush", tamper)
    assert (
        registry.current(
            kind="feature", component_ref="fixed-test-feature", scope_id=SCOPE
        ).generation
        == 1
    )
    with db.session() as session:
        assert (
            session.scalar(
                select(MarketComponentAuditRecord).where(
                    MarketComponentAuditRecord.request_id.in_(
                        ("component-consume:writer-poison", "component-consume:audit-poison")
                    )
                )
            )
            is None
        )
