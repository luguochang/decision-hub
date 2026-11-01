"""Offline B4 mechanisms. Fixture component evidence is not native/financial qualification."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, select

from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.market_case import (
    MarketCaseService,
    case_context_reference,
)
from packages.kernel.decision_hub_kernel.application.market_extension_registry import (
    MarketExtensionRegistry,
    input_route_manifest,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Base,
    Database,
    FactVersionRecord,
    FeedbackRecord,
    ReportRevisionRecord,
    RunRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketCaseRecord,
    MarketComponentAuditRecord,
    MarketComponentPointerRecord,
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import (
    ComponentManifest,
    ComponentQualification,
)
from packages.kernel.decision_hub_kernel.ports.market_extension import (
    ExtensionOriginals,
    GoldMarketSpec,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.provider_adapters.research.raw_capture import RawBodyArchive

NOW = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)
H = "a" * 64


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'b4.sqlite3'}")
    database.create_all()
    yield database
    database.engine.dispose()


def seed_case(db, *, identity="one", feedback=False, known_at=NOW - timedelta(days=1)):
    start = NOW - timedelta(days=2)
    with db.session() as session:
        session.add(
            StudyRecord(
                study_id="study-" + identity,
                scope_key=identity,
                title="原研究题",
                market="crypto",
                instrument="BTC-USDT",
                horizon_scope="24h",
                question_key="q",
                identity_basis="fixture",
                status="active",
                created_at=start,
                updated_at=start,
            )
        )
        session.add(
            RunRecord(
                run_id="run-" + identity,
                event_id="event-" + identity,
                study_id="study-" + identity,
                status="degraded",
                strategy_version="research.v1",
                runtime_version="fixture",
                created_at=start,
                updated_at=start,
                finished_at=start,
                artifact_id="art-" + identity,
            )
        )
        session.add(
            ArtifactRecord(
                artifact_id="art-" + identity,
                run_id="run-" + identity,
                event_id="event-" + identity,
                gate_status="research_only",
                headline="原报告",
                summary="原摘要",
                payload_json="{}",
                created_at=start,
            )
        )
        session.add(
            ReportRevisionRecord(
                revision_id="rev-" + identity,
                study_id="study-" + identity,
                source_run_id="run-" + identity,
                artifact_id="art-" + identity,
                previous_revision_id=None,
                strategy_version="research.v1",
                input_fingerprint=None,
                publication_status="research_only",
                change_json="{}",
                identity_basis="fixture",
                created_at=start,
                recorded_at=start,
            )
        )
        session.add(
            FactVersionRecord(
                fact_version_id="fv-" + identity,
                semantic_key=identity,
                market="crypto",
                scope_instrument="BTC-USDT",
                field="price",
                source_id="fixture",
                original_fact_id="f-" + identity,
                parsed_version_id="p-" + identity,
                payload_json="{}",
                observed_at=start,
                recorded_at=start,
                known_at=known_at,
            )
        )
        session.add(
            AnalyticalOutlookRecord(
                outlook_id="out-" + identity,
                run_id="run-" + identity,
                artifact_id="art-" + identity,
                study_id="study-" + identity,
                market="crypto",
                instrument="BTC-USDT",
                method_family="ai_synthesis",
                method_version="fixture",
                horizon="24h",
                action="long",
                subjective_probability=0.6,
                probability_status="uncalibrated",
                data_as_of=start,
                window_start_at=start,
                window_end_at=start + timedelta(days=1),
                result_hash=H,
                evidence_refs_json="[]",
                fact_version_refs_json=json.dumps(["fv-" + identity]),
                raw_payload_json="{}",
                trigger="原确认",
                invalidation="原失效",
                cohort="prospective",
                eligibility="eligible",
                exclusion_reasons_json="[]",
                research_quality="advisory_only",
                recorded_at=start,
            )
        )
        # The status/number alone is not a verified archived label.
        session.add(
            AnalyticalObservationRecord(
                outlook_id="out-" + identity,
                status="completed",
                attempt=1,
                next_attempt_at=NOW,
                evaluated_at=NOW - timedelta(hours=1),
                direction_correct=1,
                directional_return_pct=2.0,
                quality_status="verified",
                window_json="{}",
            )
        )
        if feedback:
            session.add(
                FeedbackRecord(
                    feedback_id="fb-" + identity,
                    request_id="fbr-" + identity,
                    target_type="report_revision",
                    target_id="rev-" + identity,
                    created_by="owner",
                    verdict="useful",
                    notes="原用户意见不是成熟价格标签",
                    created_at=NOW,
                )
            )


def test_case_real_assessment_never_promotes_verified_flags_and_keeps_feedback(db, tmp_path):
    seed_case(db, feedback=True)
    service = MarketCaseService(db, RawBodyArchive(tmp_path / "archive"), clock=lambda: NOW)
    value = service.capture(
        "capture-one", revision_id="rev-one", outlook_id="out-one", feedback_id="fb-one"
    )
    assert value.classification == "product_feedback" and not value.eligible
    assert value.label_known_at is None and value.direction_correct is None
    assert value.available_at == NOW and value.original_cutoff_at == NOW - timedelta(days=2)
    assert value.feedback["notes"] == "原用户意见不是成熟价格标签"
    assert (
        value.assessment["source_status"] != "verified"
        and value.assessment["label_status"] == "invalid"
    )
    assert value.assessment_basis["read_queries"] and value.source_snapshot["facts"]["fv-one"]
    assert (
        service.capture(
            "capture-one", revision_id="rev-one", outlook_id="out-one", feedback_id="fb-one"
        )
        == value
    )
    with pytest.raises(ValueError, match="request_reused"):
        service.capture("capture-one", revision_id="other", outlook_id="out-one")
    reference = case_context_reference(value)
    assert reference.context_only and reference.ref == "market_case:" + value.case_id
    assert reference.payload_hash == value.payload_hash


def test_case_future_firstknown_pit_memory_off_relations_and_original_tamper(db, tmp_path):
    seed_case(db, identity="early")
    seed_case(db, identity="future", known_at=NOW + timedelta(microseconds=1))
    service = MarketCaseService(db, RawBodyArchive(tmp_path / "archive"), clock=lambda: NOW)
    early = service.capture("early", revision_id="rev-early", outlook_id="out-early")
    future = service.capture("future", revision_id="rev-future", outlook_id="out-future")
    assert future.available_at == NOW + timedelta(microseconds=1) and not future.eligible
    assert "fact_known_after_capture" in future.exclusion_reasons
    with pytest.raises(ValueError, match="relation_scope"):
        service.relate(
            "future-relation",
            left_case_id=early.case_id,
            right_case_id=future.case_id,
            kind="contradicts",
            reason="不可将未来已知案例倒填当前",
        )
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        before_changes = native.total_changes
        before_tx = session.connection().get_transaction()
        assert len(service.list_in_session(session, cutoff_at=NOW)) == 1
        answer = service.retrieve_in_session(
            session, scope=early.scope, cutoff_at=NOW, query_terms=("price",)
        )
        assert not answer.items and answer.excluded == (
            (early.case_id, "original_case_ineligible"),
        )
        assert (
            service.retrieve_in_session(
                session, scope=early.scope, cutoff_at=NOW, memory_enabled=False
            ).total_scanned
            == 0
        )
        assert (
            native.total_changes == before_changes
            and session.connection().get_transaction() is before_tx
        )
    with db.session() as session:
        row = session.get(MarketCaseRecord, early.case_id)
        row.payload_hash = "f" * 64
    with pytest.raises(ValueError, match="original_invalid"):
        service.read(early.case_id)


def test_case_original_after_flush_change_rolls_back_entire_case(db, tmp_path):
    seed_case(db)
    original = {}
    with db.session() as session:
        original["action"] = session.get(AnalyticalOutlookRecord, "out-one").action

    def tamper(session, _context):
        if any(isinstance(row, MarketCaseRecord) for row in session.new):
            session.connection().exec_driver_sql(
                "UPDATE analytical_outlooks SET action='short' WHERE outlook_id='out-one'"
            )

    event.listen(db.factory.class_, "after_flush", tamper)
    try:
        with pytest.raises(ValueError, match="original_readset_changed"):
            MarketCaseService(db, RawBodyArchive(tmp_path / "archive"), clock=lambda: NOW).capture(
                "rollback", revision_id="rev-one", outlook_id="out-one"
            )
    finally:
        event.remove(db.factory.class_, "after_flush", tamper)
    with db.session() as session:
        assert session.get(AnalyticalOutlookRecord, "out-one").action == original["action"]
        assert session.scalar(select(MarketCaseRecord)) is None


def test_borrowed_case_reader_preserves_external_authorizer_and_dirty_uow(db, tmp_path):
    seed_case(db)
    service = MarketCaseService(db, RawBodyArchive(tmp_path / "archive"), clock=lambda: NOW)
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        actions = []

        def read_only(action, *_args):
            actions.append(action)
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE else sqlite3.SQLITE_OK

        native.set_authorizer(read_only)
        try:
            assert service.list_in_session(session, cutoff_at=NOW) == ()
            with pytest.raises(sqlite3.DatabaseError):
                native.execute("UPDATE studies SET status='paused'")
            assert sqlite3.SQLITE_UPDATE in actions
        finally:
            native.set_authorizer(None)  # Test owns this callback, service never replaces it.
        session.get(StudyRecord, "study-one").status = "paused"
        with pytest.raises(ValueError, match="clean_reader"):
            service.list_in_session(session, cutoff_at=NOW)
        assert session.dirty and native.in_transaction


def test_case_contradiction_is_append_only_hash_bound_and_cutoff_visible(db, tmp_path):
    seed_case(db, identity="left")
    seed_case(db, identity="right")
    service = MarketCaseService(db, RawBodyArchive(tmp_path / "archive"), clock=lambda: NOW)
    left = service.capture("left", revision_id="rev-left", outlook_id="out-left")
    right = service.capture("right", revision_id="rev-right", outlook_id="out-right")
    relation = service.relate(
        "contradiction",
        left_case_id=left.case_id,
        right_case_id=right.case_id,
        kind="contradicts",
        reason="同市场窗口下反方条件不同；不覆盖任何原报告",
    )
    assert (
        relation.left_payload_hash == left.payload_hash
        and relation.right_payload_hash == right.payload_hash
    )
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        assert (
            service.relations_in_session(
                session, case_id=left.case_id, cutoff_at=NOW - timedelta(microseconds=1)
            )
            == ()
        )
        assert service.relations_in_session(session, case_id=left.case_id, cutoff_at=NOW) == (
            relation,
        )
    with pytest.raises(ValueError, match="relation_request_reused"):
        service.relate(
            "contradiction",
            left_case_id=left.case_id,
            right_case_id=right.case_id,
            kind="supersedes",
            reason="不得覆盖原冲突",
        )
    assert service.read(left.case_id) == left and service.read(right.case_id) == right


class FixtureComponentReader:
    """Local saved fixture originals exercise registry mechanisms, not market merit."""

    def __init__(self, directory, now):
        self.directory, self.now = directory, now

    def verify(self, manifest, trial):
        path = self.directory / (trial.trial_id + ".json")
        body = json.loads(path.read_text(encoding="utf-8"))
        assert (
            body["manifest_hash"] == manifest.payload_hash
            and body["protocol_hash"] == trial.protocol_hash
        )
        return ComponentQualification(
            trial_id=trial.trial_id,
            manifest_hash=manifest.payload_hash,
            protocol_hash=trial.protocol_hash,
            original_refs=("fixture://" + path.name,),
            original_hashes=(canonical_hash(body),),
            known_at=self.now,
            status="passed",
            reasons=(),
            sample_qualified=True,
            cost_verified=True,
            independent_blocks=100,
        )


def component(version, *, kind="model", parent=None):
    return ComponentManifest(
        version_id=version,
        kind=kind,
        component_ref="BASE_EMPIRICAL_V1",
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        parent_version_id=parent,
        input_contract="frozen-feature.v1",
        output_contract="path-forecast.v1",
        code_hash=H,
        resource_hash="b" * 64,
        parameters={"fixture": True},
    )


def test_component_no_reader_backdate_and_contract_break_are_closed(db):
    service = ComponentRegistry(db, clock=lambda: NOW)
    service.register(component("v1"))
    with pytest.raises(ValueError, match="backdated"):
        service.shadow(
            "bad",
            version_id="v1",
            protocol_hash=H,
            baseline_version_id=None,
            start_at=NOW - timedelta(microseconds=1),
            end_at=NOW + timedelta(days=1),
        )
    trial = service.shadow(
        "trial",
        version_id="v1",
        protocol_hash=H,
        baseline_version_id=None,
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
    )
    review = service.review("review", trial_id=trial.trial_id, reviewer_ref="ops")
    assert review.decision == "not_admitted" and review.qualification.status == "unavailable"
    with pytest.raises(ValueError, match="not_admitted"):
        service.activate("activate", review_id=review.review_id, expected_generation=0)
    with pytest.raises(ValueError, match="contract_break"):
        service.register(
            component("v2", parent="v1").model_copy(update={"output_contract": "other.v2"})
        )


def test_independent_component_cas_rollback_audits_and_fresh_original_recheck(db, tmp_path):
    clock = [NOW]
    reader = FixtureComponentReader(tmp_path, NOW + timedelta(days=1))
    service = ComponentRegistry(db, original_reader=reader, clock=lambda: clock[0])
    reviews = {}
    for manifest in (
        component("v1"),
        component("v2", parent="v1"),
        component("parser-v1", kind="source_parser"),
    ):
        service.register(manifest)
        trial = service.shadow(
            "trial-" + manifest.version_id,
            version_id=manifest.version_id,
            protocol_hash=H,
            baseline_version_id=manifest.parent_version_id,
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
        )
        (tmp_path / (trial.trial_id + ".json")).write_text(
            json.dumps(
                {
                    "manifest_hash": manifest.payload_hash,
                    "protocol_hash": H,
                    "synthetic_fixture_not_native": True,
                }
            ),
            encoding="utf-8",
        )
    clock[0] = NOW + timedelta(days=1)
    for version in ("v1", "v2", "parser-v1"):
        reviews[version] = service.review(
            "review-" + version, trial_id="trial-" + version, reviewer_ref="fixture-ops"
        )
    first = service.activate("act-v1", review_id=reviews["v1"].review_id, expected_generation=0)
    parser = service.activate(
        "act-parser", review_id=reviews["parser-v1"].review_id, expected_generation=0
    )
    assert (
        service.activate("act-v1", review_id=reviews["v1"].review_id, expected_generation=0)
        == first
    )
    with pytest.raises(ValueError, match="pointer_conflict"):
        service.activate("wrong-gen", review_id=reviews["v2"].review_id, expected_generation=0)
    second = service.activate("act-v2", review_id=reviews["v2"].review_id, expected_generation=1)
    rolled = service.rollback(
        "rollback",
        kind=second.kind,
        component_ref=second.component_ref,
        scope_id=second.scope_id,
        expected_generation=2,
    )
    assert rolled.version_id == "v1" and rolled.generation == 3
    assert (
        service.current(
            kind=parser.kind, component_ref=parser.component_ref, scope_id=parser.scope_id
        )
        == parser
    )
    with db.session() as session:
        assert len(session.scalars(select(MarketComponentAuditRecord)).all()) == 4
        assert len(session.scalars(select(MarketComponentVersionRecord)).all()) == 3
        assert len(session.scalars(select(MarketComponentPointerRecord)).all()) == 2
    path = tmp_path / "trial-v2.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["changed"] = True
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="original_changed"):
        service.activate("fresh-reject", review_id=reviews["v2"].review_id, expected_generation=3)
    assert (
        service.current(kind=first.kind, component_ref=first.component_ref, scope_id=first.scope_id)
        == rolled
    )


def test_component_after_flush_tampered_audit_rolls_back_pointer(db, tmp_path):
    clock = [NOW]
    reader = FixtureComponentReader(tmp_path, NOW + timedelta(days=1))
    service = ComponentRegistry(db, original_reader=reader, clock=lambda: clock[0])
    manifest = component("rollback-audit")
    service.register(manifest)
    trial = service.shadow(
        "rollback-trial",
        version_id=manifest.version_id,
        protocol_hash=H,
        baseline_version_id=None,
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
    )
    (tmp_path / (trial.trial_id + ".json")).write_text(
        json.dumps(
            {
                "manifest_hash": manifest.payload_hash,
                "protocol_hash": H,
                "synthetic_fixture_not_native": True,
            }
        ),
        encoding="utf-8",
    )
    clock[0] = NOW + timedelta(days=1)
    review = service.review("rollback-review", trial_id=trial.trial_id, reviewer_ref="fixture-ops")

    def corrupt(session, _context):
        if any(isinstance(row, MarketComponentAuditRecord) for row in session.new):
            session.connection().exec_driver_sql(
                "UPDATE market_component_audits SET payload_hash=?", ("f" * 64,)
            )

    event.listen(db.factory.class_, "after_flush", corrupt)
    try:
        with pytest.raises(ValueError, match="audit_invalid"):
            service.activate(
                "rollback-activation", review_id=review.review_id, expected_generation=0
            )
    finally:
        event.remove(db.factory.class_, "after_flush", corrupt)
    assert (
        service.current(
            kind=manifest.kind, component_ref=manifest.component_ref, scope_id=manifest.scope_id
        )
        is None
    )
    with db.session() as session:
        assert session.scalar(select(MarketComponentAuditRecord)) is None


def gold(**overrides):
    body = dict(
        scope_id="gold:spot:unselected:XAU-USD:trade:1m",
        instrument="XAU-USD",
        instrument_kind="spot",
        price_basis="trade",
        timezone="UTC",
    )
    body.update(overrides)
    return GoldMarketSpec(**body)


def test_gold_gc_is_not_xau_provider_calendar_labels_must_be_real(db):
    with pytest.raises(ValueError, match="spot_contract"):
        gold(instrument="GC=F")
    with pytest.raises(ValueError, match="future_contract"):
        gold(instrument_kind="future", instrument="XAU-USD")
    registry = MarketExtensionRegistry(db, clock=lambda: NOW)
    identity = registry.register("gold", gold())
    result = registry.assess(identity)
    assert result.state == "not_admitted"
    assert set(result.reasons) == {
        "gold_provider_not_selected",
        "gold_calendar_not_verified",
        "market_extension_original_reader_unavailable",
    }
    assert not result.trading_authority and not result.probability_calibrated
    assert registry.assess(identity) == result  # same instant is idempotent, no audit deletion
    assert registry.status(identity) == result


def test_three_routes_missing_publictime_oi_consensus_and_independence_never_pass(db):
    class CSVOnlyReader:
        def verify(self, manifest):
            return ExtensionOriginals(
                manifest_hash=manifest.payload_hash,
                scope_id=manifest.scope_id,
                instrument=manifest.instrument,
                known_at=NOW,
                original_refs=("fixture://csv-date-only",),
                original_hashes=(H,),
                fields=("series_value", "observation_date"),
                rights_verified=True,
                quality_verified=True,
                independent_day_blocks=200,
                distinct_event_blocks=100,
            )

    registry = MarketExtensionRegistry(db, original_reader=CSVOnlyReader(), clock=lambda: NOW)
    for route in ("MACRO_STATE_V1", "CROWDING_V1", "EVENT_SURPRISE_V1"):
        manifest = input_route_manifest(
            route,
            scope_id="BTC-spot",
            instrument="BTC-USDT",
            protocol_hash=H,
            matching_version="day-shock-nonoverlap.v1",
        )
        result = registry.assess(registry.register(route, manifest))
        assert result.state == ("descriptive_only" if route == "MACRO_STATE_V1" else "not_admitted")
        assert (
            "source_public_time_unknown" in result.reasons
            and "f5_independent_sample_qualification_missing" in result.reasons
        )
        assert not result.probability_calibrated and not result.trading_authority
        if route == "CROWDING_V1":
            assert "required_field_missing:open_interest" in result.reasons
        if route == "EVENT_SURPRISE_V1":
            assert "required_field_missing:consensus_value" in result.reasons

    class WrongAsset(CSVOnlyReader):
        def verify(self, manifest):
            return super().verify(manifest).model_copy(update={"instrument": "ETH-USDT"})

    bad = MarketExtensionRegistry(db, original_reader=WrongAsset(), clock=lambda: NOW)
    with pytest.raises(ValueError, match="binding_invalid"):
        bad.assess(
            registry.register(
                "wrong-asset",
                input_route_manifest(
                    "CROWDING_V1",
                    scope_id="BTC-spot",
                    instrument="BTC-USDT",
                    protocol_hash=H,
                    matching_version="matching.v1",
                ),
            )
        )


def test_0079_additive_migration_matches_all_new_orm_indexes_and_keeps_old_rows(tmp_path):
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0079_market_case_and_components.py"
    )
    spec = importlib.util.spec_from_file_location("b4_migration_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0078_path_forecast_ledger"
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    new_names = {
        table.name
        for table in Base.metadata.tables.values()
        if table.name.startswith("market_case")
        or table.name.startswith("market_component")
        or table.name.startswith("market_extension")
    }
    assert len(new_names) == 9
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE old_original(id TEXT PRIMARY KEY, payload TEXT)")
        connection.exec_driver_sql("INSERT INTO old_original VALUES('original','unchanged')")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        view = inspect(connection)
        for name in new_names:
            table = Base.metadata.tables[name]
            assert {col["name"] for col in view.get_columns(name)} == set(table.columns.keys())
            actual = {
                item["name"]: (tuple(item["column_names"]), item["unique"])
                for item in view.get_indexes(name)
            }
            assert actual == {
                index.name: (tuple(col.name for col in index.columns), index.unique)
                for index in table.indexes
            }
        assert connection.exec_driver_sql("SELECT * FROM old_original").all() == [
            ("original", "unchanged")
        ]
        with pytest.raises(RuntimeError, match="downgrade_forbidden"):
            module.downgrade()
    engine.dispose()
