"""Bounded batch controls: synthetic deep issuer, real private SQLite/originals.

The fake issuer is explicitly installed only in this test module. No actual
stored-model deep authentication, fit, Root database, HTTP, or finance P claim.
Sealed39 classes/functions/source are never patched by these controls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from packages.kernel.decision_hub_kernel.application import history_forecast_transfer39 as transfer
from packages.kernel.decision_hub_kernel.application import history_outcome_forecasts_v2 as batch
from packages.kernel.decision_hub_kernel.application import (
    history_outcome_originals_v2 as originals,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import forecast_id
from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_write39 import (
    HistoryForecastWrite39Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import (
    encoded,
    snapshot_identity,
)
from packages.kernel.decision_hub_kernel.persistence.db import Base, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as Model,
)
from packages.kernel.decision_hub_kernel.persistence.history_observed_records import (
    HistoryObservedAcquisitionJobRecord,
    HistoryObservedTrainingRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_dataset_forecast_v2 import (
    HistoryDatasetProspectiveForecastV2,
)
from packages.kernel.decision_hub_kernel.ports.history_dataset_model_v2 import (
    HistoryDatasetFitModelV2,
)
from packages.kernel.decision_hub_kernel.ports.history_observed_training import seal
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope
from packages.kernel.decision_hub_kernel.ports.path_forecast import EmpiricalDistribution
from tests.market_risk.test_history_forecast_write39_transfer import fixture


def bind_test_callable(monkeypatch, cls, name, function):
    """Explicit fake boundary on new batch module only; not a production grant."""
    monkeypatch.setattr(cls, name, function)
    monkeypatch.setattr(
        batch,
        "LOADED_METHODS",
        tuple(
            (owner, label, getattr(owner, label), getattr(owner, label).__code__)
            for owner in (batch._RequestModels, batch.HistoryOutcomeForecastsV2Authenticator)
            for label in owner.__dict__
            if callable(getattr(owner, label)) and hasattr(getattr(owner, label), "__code__")
        ),
    )


def setup(tmp_path, monkeypatch, *, count=6, second_model=False):
    env = fixture(tmp_path, monkeypatch)
    Base.metadata.create_all(
        env.db.engine,
        tables=[
            HistoryObservedTrainingRecord.__table__,
            HistoryObservedAcquisitionJobRecord.__table__,
        ],
    )

    def distribution(n):
        return EmpiricalDistribution(
            n=n,
            long_mae_sorted=(Decimal("0.01"),) * n,
            short_mae_sorted=(Decimal("0.02"),) * n,
        )

    model_source_ref, model_source_hash = env.models.archive.put(
        {"fixture": "synthetic model source"}
    )
    model_known = env.cutoff - timedelta(days=1)
    model = seal(
        HistoryDatasetFitModelV2,
        model_id="hfit2_" + "c" * 48,
        owner_claim="mechanical39",
        request_id="synthetic-model",
        plan_id="synthetic-plan",
        dataset_id="synthetic-dataset",
        dataset_hash=env.dataset_hash,
        scope_id=env.scope.scope_id,
        cutoffs=tuple(env.cutoff - timedelta(days=100 - index) for index in range(90)),
        data_information_asof=model_known - timedelta(hours=4),
        dataset_recorded_at=model_known - timedelta(hours=3),
        fit_started_at=model_known - timedelta(hours=2),
        fit_completed_at=model_known - timedelta(hours=1),
        model_known_at=model_known,
        recorded_at=model_known,
        baseline=distribution(60),
        conditional_low=distribution(30),
        conditional_high=distribution(30),
        training_vol_median=Decimal(0),
        windows=tuple({"synthetic": index} for index in range(90)),
        source_files=env.models.sources(),
        source_original_ref=model_source_ref,
        source_original_hash=model_source_hash,
        dataset_original_ref=env.dataset_ref,
        dataset_original_hash=env.dataset_hash,
    )
    model_ref, model_hash = env.models.archive.put(model.model_dump(mode="json"))
    with env.db.engine.begin() as connection:
        connection.execute(
            Model.__table__.update().values(
                payload_json=encoded(model.model_dump(mode="json")).decode(),
                payload_hash=model.payload_hash,
                original_ref=model_ref,
                original_hash=model_hash,
            )
        )
    env.model_ref, env.model_hash = model_ref, model_hash
    registry = {model.model_id: (model, model_ref, model_hash)}
    if second_model:
        eth_scope = spot_scope("ETH-USDT")
        eth_model = seal(
            HistoryDatasetFitModelV2,
            **{
                **model.model_dump(exclude={"payload_hash"}),
                "model_id": "hfit2_" + "e" * 48,
                "request_id": "synthetic-model-ETH",
                "scope_id": eth_scope.scope_id,
                "training_vol_median": Decimal("0.1"),
            },
        )
        eth_ref, eth_hash = env.models.archive.put(eth_model.model_dump(mode="json"))
        registry[eth_model.model_id] = eth_model, eth_ref, eth_hash
        with env.db.engine.begin() as connection:
            row = dict(connection.execute(select(Model.__table__)).mappings().one())
            row.update(
                model_id=eth_model.model_id,
                request_id=eth_model.request_id,
                scope_id=eth_model.scope_id,
                payload_hash=eth_model.payload_hash,
                payload_json=encoded(eth_model.model_dump(mode="json")).decode(),
                original_ref=eth_ref,
                original_hash=eth_hash,
            )
            connection.execute(Model.__table__.insert(), row)
            capture = dict(connection.execute(select(RawCaptureRecord.__table__)).mappings().one())
            capture.update(
                capture_id="synthetic-capture-ETH",
                url=capture["url"].replace("BTC-USDT", "ETH-USDT"),
            )
            connection.execute(RawCaptureRecord.__table__.insert(), capture)
            for bar in env.bars:
                eth = bar.model_copy(
                    update={"scope_id": eth_scope.scope_id, "instrument": eth_scope.instrument}
                )
                revision = canonical_hash(eth.semantic_payload())
                connection.execute(
                    MarketCandleRecord.__table__.insert(),
                    dict(
                        candle_id="candle_" + canonical_hash([eth.scope_id, revision])[:48],
                        scope_id=eth.scope_id,
                        venue=eth.venue,
                        instrument=eth.instrument,
                        price_type=eth.price_type,
                        interval_seconds=60,
                        open_at=eth.open_at,
                        close_at=eth.close_at,
                        received_at=eth.received_at,
                        known_at=eth.known_at,
                        revision_hash=revision,
                        candle_json=eth.model_dump_json(),
                        capture_ref=eth.capture_ref,
                        capture_hash=eth.capture_hash,
                    ),
                )
        env.service = HistoryForecastWrite39Service(
            env.models,
            owner_claim="mechanical39",
            grants={env.scope.scope_id: model.model_id, eth_scope.scope_id: eth_model.model_id},
        )
    dispatch = HistoryForecastDispatch39Service(env.service)
    features = {}
    for identifier, (candidate, _ref, _digest) in registry.items():
        with env.commands._read_session() as session:
            selected, feature = env.service._feature(session, candidate.scope_id, env.cutoff)
        feature_ref, feature_hash = env.service.archive.put(feature)
        rv, group, frequencies = env.service._numerics(candidate, selected)
        features[identifier] = (selected, feature_ref, feature_hash, rv, group, frequencies)
    values = []
    with env.db.engine.begin() as connection:
        for index in range(count):
            current_model = eth_model if second_model and index >= count - 2 else model
            current_ref, current_hash = registry[current_model.model_id][1:]
            selected, feature_ref, feature_hash, rv, group, frequencies = features[
                current_model.model_id
            ]
            sources = dispatch.legacy._sources() if index % 2 == 0 else dispatch._sources()
            source = {
                "schema_version": "history-v2-forecast-source-original.v1",
                "files": [
                    dict(
                        path=path,
                        sha256=digest,
                        bytes_base64=base64.b64encode(
                            (env.originals.source_root / path).read_bytes()
                        ).decode(),
                    )
                    for path, digest in sources
                ],
            }
            source_ref, source_hash = env.service.archive.put(source)
            request = "batch-synthetic-" + str(index)
            value = seal(
                HistoryDatasetProspectiveForecastV2,
                forecast_id=forecast_id("mechanical39", request),
                owner_claim="mechanical39",
                request_id=request,
                scope_id=current_model.scope_id,
                model_id=current_model.model_id,
                model_hash=current_model.payload_hash,
                model_known_at=current_model.model_known_at,
                model_authenticated_at=env.originals.now(),
                model_original_ref=current_ref,
                model_original_hash=current_hash,
                cutoff_at=env.cutoff,
                latest_close_at=selected[-1].close_at,
                feature_known_at=max(bar.known_at for bar in selected),
                selected_closes=selected,
                feature_original_ref=feature_ref,
                feature_original_hash=feature_hash,
                original_readset=tuple(
                    sorted(
                        {
                            ("archive", current_ref, current_hash),
                            *(("raw", bar.capture_ref, bar.capture_hash) for bar in selected),
                        }
                    )
                ),
                realized_vol_30m=rv,
                selected_group=group,
                frequencies=frequencies,
                source_files=sources,
                source_original_ref=source_ref,
                source_original_hash=source_hash,
                produced_at=env.originals.now(),
                recorded_at=env.originals.now(),
                fixture_mode=True,
            )
            save(connection, env.service, value)
            values.append(value)
    calls = []

    def fake_issue(self, session, identifier):
        calls.append(
            (identifier, id(session), id(session.connection().connection.driver_connection))
        )
        authority = transfer.TransferAuthority39(self.forecasts.writer)
        issued_model, issued_ref, issued_hash = registry[identifier]
        sources = self.forecasts._sources()
        rows = transfer.capture_rows(
            session,
            (),
            (
                ("market_history_v2_models", issued_model.model_id),
                ("market_history_v2_originals", "synthetic-dataset"),
                ("market_history_v2_jobs", "synthetic-job"),
                ("market_history_v2_attempts", "synthetic-attempt"),
            ),
        )
        files = transfer.capture_files(
            (
                (issued_ref, issued_hash),
                (env.dataset_ref, env.dataset_hash),
                (model_source_ref, model_source_hash),
                (str(env.originals.raw.root / env.raw_hash[:2] / env.raw_hash), env.raw_hash),
                *((str(env.originals.source_root / path), digest) for path, digest in sources),
            )
        )
        body = encoded(issued_model.model_dump(mode="json"))
        manifest = hashlib.sha256(body).hexdigest()
        proof = transfer.ModelProof39(
            body,
            rows,
            transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS)),
            files,
            sources,
            authority.context(session),
            env.cutoff.isoformat(),
            authority,
            threading.get_ident(),
            manifest,
            hmac.digest(authority.key, manifest.encode(), "sha256"),
        )
        authority._issued, authority._members = proof, authority.members(proof)
        return authority, proof, issued_model

    bind_test_callable(
        monkeypatch, batch.HistoryOutcomeForecastsV2Authenticator, "_issue_model", fake_issue
    )
    return SimpleNamespace(
        env=env,
        dispatch=dispatch,
        reader=batch.HistoryOutcomeForecastsV2Authenticator(dispatch),
        model=model,
        registry=registry,
        calls=calls,
        values=tuple(values),
        ids=tuple(value.forecast_id for value in values),
        feature=feature,
    )


def save(connection, service, value):
    ref, digest = service.archive.put(value.model_dump(mode="json"))
    data = dict(
        forecast_id=value.forecast_id,
        owner_claim=value.owner_claim,
        request_id=value.request_id,
        scope_id=value.scope_id,
        model_id=value.model_id,
        model_hash=value.model_hash,
        cutoff_at=value.cutoff_at,
        payload_json=encoded(value.model_dump(mode="json")).decode(),
        payload_hash=value.payload_hash,
        original_ref=ref,
        original_hash=digest,
        produced_at=value.produced_at,
        recorded_at=value.recorded_at,
    )
    connection.execute(Forecast.__table__.delete().where(Forecast.forecast_id == value.forecast_id))
    connection.execute(Forecast.__table__.insert(), data)


def test_batch_shares_two_fake_deep_and_same_physical_snapshot_no_writes(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch, count=48, second_model=True)
    mutations, archive_puts, capture_queries = [], [], []

    def sql_observer(connection, cursor, statement, parameters, context, executemany):
        if statement.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}:
            mutations.append(statement)
        if "FROM RAW_CAPTURES" in statement.upper():
            capture_queries.append(statement)

    def profile(frame, event_name, arg):
        if event_name == "call" and frame.f_code is item.env.service.archive.put.__func__.__code__:
            archive_puts.append(frame.f_code.co_name)

    import sys

    event.listen(item.env.db.engine, "before_cursor_execute", sql_observer)
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        with item.env.commands._read_session() as session:
            physical = id(session.connection().connection.driver_connection)
            assert item.reader.read_many(session, item.ids) == item.values
            assert item.calls == [
                (identifier, id(session), physical) for identifier in item.registry
            ]
            assert session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() == 1
        with item.env.commands._read_session() as session:
            assert item.reader.read_many(session, item.ids[:2]) == item.values[:2]
        assert len(item.calls) == 3  # second request's BTC authenticates again; no TTL reuse
        assert len({entry[0] for entry in item.calls}) == 2
        assert len({value.payload_hash for value, _, _ in item.registry.values()}) == 2
        assert len({ref for _, ref, _ in item.registry.values()}) == 2
        assert mutations == [] and archive_puts == []
        assert len(capture_queries) == 4  # one entry/exit pool scan in each request
        assert all("BODY_REF IN" in statement.upper() for statement in capture_queries)
    finally:
        sys.setprofile(previous)
        event.remove(item.env.db.engine, "before_cursor_execute", sql_observer)


def test_batch_unknown_source_and_consistent_saved_numerics_forgery_rejected(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch)
    first = item.values[0]
    frequency = first.frequencies[0].model_copy(
        update={"baseline_long_probability": Decimal("0.4")}
    )
    forged = seal(
        HistoryDatasetProspectiveForecastV2,
        **{
            **first.model_dump(exclude={"payload_hash"}),
            "frequencies": (frequency, first.frequencies[1]),
        },
    )
    with item.env.db.engine.begin() as connection:
        save(connection, item.env.service, forged)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="actual_probability_changed"),
    ):
        item.reader.read_many(session, item.ids)
    unknown = seal(
        HistoryDatasetProspectiveForecastV2,
        **{
            **first.model_dump(exclude={"payload_hash"}),
            "source_files": (("unknown.py", "a" * 64),),
        },
    )
    with item.env.db.engine.begin() as connection:
        save(connection, item.env.service, unknown)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="unknown_or_mixed"),
    ):
        item.reader.read_many(session, item.ids)


def test_batch_full_raw_and_archive_sha_at_exit_rejected(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch, count=2)
    original = batch.HistoryOutcomeForecastsV2Authenticator._verify_forecast
    target = item.env.originals.raw.root / item.env.raw_hash[:2] / item.env.raw_hash
    raw = target.read_bytes()

    def changed(self, *args):
        result = original(self, *args)
        target.write_bytes(raw + b" ")
        return result

    bind_test_callable(
        monkeypatch, batch.HistoryOutcomeForecastsV2Authenticator, "_verify_forecast", changed
    )
    with item.env.commands._read_session() as session, pytest.raises(ValueError, match="raw|bytes"):
        item.reader.read_many(session, item.ids)
    target.write_bytes(raw)
    source = Path(item.values[0].source_original_ref)
    source_body = source.read_bytes()

    def archive_changed(self, *args):
        result = original(self, *args)
        source.write_bytes(source_body + b" ")
        return result

    bind_test_callable(
        monkeypatch,
        batch.HistoryOutcomeForecastsV2Authenticator,
        "_verify_forecast",
        archive_changed,
    )
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="archive_hash|bytes"),
    ):
        item.reader.read_many(session, item.ids)


def test_batch_snapshot_replacement_and_issuer_leaf_rejected(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch, count=1)
    original = batch._RequestModels.issue

    def corrupt_leaf(self, entries):
        original(self, entries)
        group = self.entries[0].proof.rows[0]
        object.__setattr__(group, "rows", tuple(tuple(row) + ("forged",) for row in group.rows))

    bind_test_callable(monkeypatch, batch._RequestModels, "issue", corrupt_leaf)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="request_cache_changed"),
    ):
        item.reader.read_many(session, item.ids)
    bind_test_callable(monkeypatch, batch._RequestModels, "issue", original)
    verify = batch.HistoryOutcomeForecastsV2Authenticator._verify_forecast

    def replaced(self, session, *args):
        result = verify(self, session, *args)
        native = session.connection().connection.driver_connection
        native.commit()
        native.execute("BEGIN")
        return result

    bind_test_callable(
        monkeypatch, batch.HistoryOutcomeForecastsV2Authenticator, "_verify_forecast", replaced
    )
    with pytest.raises(ValueError, match="physical_snapshot_replaced"):
        with item.env.commands._read_session() as session:
            item.reader.read_many(session, item.ids)


def test_batch_schema_native_type_context_and_budget_gates(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch, count=1)
    with item.env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE market_history_v2_forecasts ADD COLUMN ignored TEXT"
        )
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="complete_forecast_schema_changed"),
    ):
        item.reader.read_many(session, item.ids)
    assert item.calls == []
    # Direct issuer tests exercise immutable native leaf/type and context binding.
    with item.env.commands._read_session() as session:
        sources = item.reader._sources()
        boundary = snapshot_identity(session)
        cache = batch._RequestModels(item.reader, session, boundary, sources)
        authority, proof, _ = item.reader._issue_model(session, item.model.model_id)
        entry = batch._ModelEntry(
            (item.model.model_id, item.model.payload_hash, item.env.model_ref, item.env.model_hash),
            proof.model_bytes,
            authority,
            proof,
        )
        cache.issue((entry,))
        cache.authenticate()
        changed = replace(
            entry,
            exact_model=(
                item.model.model_id,
                item.model.payload_hash,
                item.env.model_ref,
                "b" * 64,
            ),
        )
        cache.entries = (changed,)
        with pytest.raises(ValueError, match="request_cache_changed"):
            cache.authenticate()
        cache.entries = (entry,)
        cache.thread_id = -1
        with pytest.raises(ValueError, match="request_cache_changed"):
            cache.authenticate()
        cache.close()
        with pytest.raises(ValueError, match="request_cache_changed"):
            cache.authenticate()
    assert not transfer.native_equal39((1,), (1.0,))
    with (
        item.env.commands._read_session() as session,
        pytest.raises(batch.HistoryOutcomeForecastBudgetV2Error, match="budget_reached"),
    ):
        item.reader.read_many(session, item.ids, budget_seconds=0.00000001)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="budget_invalid"),
    ):
        item.reader.read_many(session, item.ids, budget_seconds=121)


def test_capture_cache_window_equivalence_actual_known_and_immutable_leaf(tmp_path, monkeypatch):
    item = setup(tmp_path, monkeypatch, count=1)
    env = item.env
    known = env.cutoff + timedelta(minutes=10)
    # Two future closes, no complete-label or financial claim. This tests the
    # independent known=C predicate, not a feature's known=T predicate.
    bars = tuple(
        env.bars[-1].model_copy(
            update={
                "open_at": env.cutoff + timedelta(minutes=index),
                "close_at": env.cutoff + timedelta(minutes=index + 1),
                "received_at": known - timedelta(seconds=3),
                "accepted_at": known - timedelta(seconds=1),
                "row_index": index,
            }
        )
        for index in range(2)
    )
    body = encoded(
        {
            "code": "0",
            "data": [
                [
                    str(int(bar.open_at.timestamp() * 1000)),
                    "100",
                    "103",
                    "97",
                    "100",
                    "1",
                    "100",
                    "100",
                    "1",
                ]
                for bar in bars
            ],
        }
    )
    digest, ref = env.originals.raw.put(body)
    bars = tuple(
        bar.model_copy(update={"capture_ref": ref, "capture_hash": digest}) for bar in bars
    )
    with env.db.engine.begin() as connection:
        capture = dict(connection.execute(select(RawCaptureRecord.__table__)).mappings().one())
        capture.update(
            capture_id="future-label-known-C",
            body_ref=ref,
            body_hash=digest,
            byte_count=len(body),
            started_at=known - timedelta(seconds=4),
            completed_at=known - timedelta(seconds=3),
            recorded_at=known - timedelta(seconds=2),
            url=capture["url"].rsplit("after=", 1)[0]
            + "after="
            + str(int((env.cutoff + timedelta(minutes=3)).timestamp() * 1000)),
        )
        connection.execute(RawCaptureRecord.__table__.insert(), capture)
        for bar in bars:
            revision = canonical_hash(bar.semantic_payload())
            connection.execute(
                MarketCandleRecord.__table__.insert(),
                dict(
                    candle_id="candle_" + canonical_hash([bar.scope_id, revision])[:48],
                    scope_id=bar.scope_id,
                    venue=bar.venue,
                    instrument=bar.instrument,
                    price_type=bar.price_type,
                    interval_seconds=60,
                    open_at=bar.open_at,
                    close_at=bar.close_at,
                    received_at=bar.received_at,
                    known_at=bar.known_at,
                    revision_hash=revision,
                    candle_json=bar.model_dump_json(),
                    capture_ref=bar.capture_ref,
                    capture_hash=bar.capture_hash,
                ),
            )
    with env.commands._read_session() as session:
        expected = env.service.reader.load(session, env.scope, env.cutoff, known)
        authority = originals.HistoryOutcomeCaptureAuthorityV2(item.dispatch)
        cache = authority.issue(session, tuple(sorted((env.raw_ref, ref))), checkpoint=lambda: None)
        actual = originals.load_window(session, env.scope, env.cutoff, known, cache)
        assert actual[0] == expected[0] and encoded(actual[1]) == encoded(expected[1])
        assert len(actual[0]) == 33
        assert (
            len(originals.load_window(session, env.scope, env.cutoff, env.cutoff, cache)[0]) == 31
        )
        assert item.reader.read_many(session, item.ids, capture_cache=cache) == item.values
        # read_many keeps the caller's cache alive for subsequent label windows.
        assert len(originals.load_window(session, env.scope, env.cutoff, known, cache)[0]) == 33
        before = cache.captures
        changed = list(before[0])
        changed[6] = float(changed[6])  # same value, distinct native scalar type
        object.__setattr__(cache, "captures", (tuple(changed), *before[1:]))
        with pytest.raises(ValueError, match="capture_cache_changed"):
            originals.load_window(session, env.scope, env.cutoff, known, cache)
        object.__setattr__(cache, "captures", before)
        authority.close(session, cache)
        with pytest.raises(ValueError, match="capture_cache_changed"):
            originals.load_window(session, env.scope, env.cutoff, known, cache)
