"""One bounded coupling control; qualification history is synthetic, not finance P."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from packages.kernel.decision_hub_kernel.application import history_outcome_qualified41 as bridge
from packages.kernel.decision_hub_kernel.application import history_outcome_v2 as outcome
from packages.kernel.decision_hub_kernel.persistence.db import Base
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record,
    HistoryOutcomeOpportunityV2Record,
    HistoryOutcomeProtocolV2Record,
    HistoryOutcomePublicationV2Record,
    HistoryOutcomeScoreV2Record,
)
from tests.market_risk.test_history_model_qualification_v3 import setup41


def refresh_bridge_guards(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "LOADED_METHODS",
        tuple(
            (cls, name, getattr(cls, name), getattr(cls, name).__code__)
            for cls, name, _fn, _code in bridge.LOADED_METHODS
        ),
    )


def test_two_actual_qualified_models_two_forecasts_same_ro_main_sidecar_and_full_sha_drift(
    tmp_path, monkeypatch
):
    item = setup41(tmp_path, monkeypatch, second_model=True)
    values = []
    for model, _ref, _digest in item.item.registry.values():
        proof = item.writer.authenticate_model(model.scope_id)
        with item.env.commands._read_session() as session:
            prepared = item.writer.prepare(
                session,
                scope_id=model.scope_id,
                request_id="qualified-batch:" + model.model_id,
                cutoff_at=item.env.originals.now().replace(second=0, microsecond=0),
                proof=proof,
            )
        values.append(item.writer.save_prepared(prepared))
    ids = tuple(value.forecast_id for value in values)
    assert len(ids) == 2 and len({value.model_hash for value in values}) == 2
    calls, sql, archives = [], [], []
    original_issue = bridge.HistoryOutcomeQualified41Authenticator._issue_model

    def count_actual_issue(self, session, scope, check):
        calls.append((scope, id(session), id(session.connection().connection.driver_connection)))
        return original_issue(self, session, scope, check)

    monkeypatch.setattr(
        bridge.HistoryOutcomeQualified41Authenticator, "_issue_model", count_actual_issue
    )
    refresh_bridge_guards(monkeypatch)
    reader = bridge.HistoryOutcomeQualified41Authenticator(item.dispatch)

    def observe_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        sql.append(statement)

    put_codes = {
        type(archive).put.__code__
        for archive in (
            item.env.originals.archive,
            item.active.archive,
            item.writer.archive,
        )
    }

    def observe_archive(frame, action, _argument):
        if action == "call" and frame.f_code in put_codes:
            archives.append("put")

    previous_profile = sys.getprofile()
    event.listen(item.env.db.engine, "before_cursor_execute", observe_sql)
    sys.setprofile(observe_archive)
    try:
        with item.env.commands._read_session() as session:
            native = session.connection().connection.driver_connection
            original_changes = native.total_changes
            assert reader.read_many(session, ids) == tuple(values)
            assert native.total_changes == original_changes
    finally:
        sys.setprofile(previous_profile)
        event.remove(item.env.db.engine, "before_cursor_execute", observe_sql)
    assert len(calls) == 2 and len({call[0] for call in calls}) == 2
    assert len({call[1:] for call in calls}) == 1
    assert not item.writer._proofs and archives == []
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "COMMIT"))
        for statement in sql
    )
    assert not any('FROM "market_history_v2_attempts"' in statement for statement in sql)

    # Real main40r2 composition uses actual39 resources plus its exact41
    # sidecar. The hourly census deliberately has missing publications, so this
    # control establishes no 1440-label or paired financial result.
    Base.metadata.create_all(
        item.env.db.engine,
        tables=[
            record.__table__
            for record in (
                HistoryOutcomeProtocolV2Record,
                HistoryOutcomeOpportunityV2Record,
                HistoryOutcomePublicationV2Record,
                HistoryOutcomeObservationV2Record,
                HistoryOutcomeScoreV2Record,
            )
        ],
    )
    service = outcome.HistoryOutcomeV2Service(
        item.item.dispatch,
        qualified_forecasts=item.dispatch,
        hourly=SimpleNamespace(_read_slot=lambda _session, _identifier: None),
    )
    calls.clear()
    protocol = service.register("qualified-main40r2")
    assert len(calls) == 4  # Two exact models, independent registration confirmation.
    assert len({call[2] for call in calls[:2]}) == 1
    assert calls[0][2] != calls[2][2]
    with service.commands._read_session() as session:
        assert service._read_forecasts(session, ids, check=lambda: None) == tuple(values)
        opportunities = session.scalars(
            select(HistoryOutcomeOpportunityV2Record).order_by(
                HistoryOutcomeOpportunityV2Record.hour_at,
                HistoryOutcomeOpportunityV2Record.scope_id,
            )
        ).all()
        assert len(opportunities) == 48
    item.env.originals.clock.__closure__[0].cell_contents = protocol.first_hour + timedelta(
        minutes=6
    )
    for opportunity in opportunities[:2]:
        with service.commands._read_session() as session:
            packet = service.prepare_publication(session, opportunity.opportunity_id)
        service.save(packet)
    with service.commands._read_session() as session:
        packet = service.prepare_score(session, protocol.protocol_id, "qualified-low-paired")
    score = service.save(packet)[0]
    with service.commands._read_session() as session:
        assert service.read_score(session, score.score_id) == score
    assert score.expected_total == 48 and score.paired_count == 0
    assert score.publication_counts["missing"] == 2 and not score.financial_qualified

    # A whole raw original is changed after both current windows have been
    # reconstructed. Complete request exit SHA must reject the entire result.
    target = item.env.originals.raw.root / item.env.raw_hash[:2] / item.env.raw_hash
    before = Path(target).read_bytes()
    original_verify = bridge.HistoryOutcomeQualified41Authenticator._verify_forecast
    verified = []

    def mutate_after_last(self, *args, **kwargs):
        result = original_verify(self, *args, **kwargs)
        verified.append(1)
        if len(verified) == 2:
            Path(target).write_bytes(before + b" ")
        return result

    monkeypatch.setattr(
        bridge.HistoryOutcomeQualified41Authenticator, "_verify_forecast", mutate_after_last
    )
    refresh_bridge_guards(monkeypatch)
    with service.commands._read_session() as session:
        with pytest.raises(ValueError, match="original_bytes_changed"):
            reader.read_many(session, ids)
    assert not item.writer._proofs
