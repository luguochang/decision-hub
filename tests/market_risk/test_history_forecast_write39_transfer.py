"""Six finite mechanical controls on real private SQLite, with synthetic proofs.

These do not fit or authenticate an actual stored model, call GET/HTTP/production,
or claim probability, full-model integration, formal performance or finance P.
The issuer is seeded only inside this test to isolate complete tuple/SHA transfer.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from apps.hub_api.history_owner_authority import HistoryOwnerAuthority
from apps.hub_worker.composition import build_history_observed_worker
from packages.kernel.decision_hub_kernel.application import history_forecast_transfer39 as transfer
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
    forecast_id,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_model_v2 import (
    HistoryDatasetModelV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_write39 import (
    HistoryForecastCommitUnconfirmed39,
    HistoryForecastWrite39Service,
    PreparedForecast39,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    Database,
    RawCaptureRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as Model,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord as Original,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2AttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_dataset_forecast_v2 import (
    HistoryDatasetProspectiveForecastV2,
    HistoryDatasetRiskFrequencyV2,
)
from packages.kernel.decision_hub_kernel.ports.history_observed_training import seal
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    db = Database("sqlite+pysqlite:///" + (tmp_path / "private.sqlite3").as_posix())
    tables = (Forecast, Model, Original, Attempt, Job, MarketCandleRecord, RawCaptureRecord)
    Base.metadata.create_all(db.engine, tables=[row.__table__ for row in tables])
    commands = build_history_observed_worker(db, owner_id="mechanical39")
    cutoff = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    originals = HistoryDatasetV2Service(
        commands, tmp_path, fixture_mode=True, clock=lambda: cutoff + timedelta(seconds=5)
    )
    models = HistoryDatasetModelV2Service(originals)
    scope, model_id = spot_scope("BTC-USDT"), "hfit2_" + "c" * 48
    service = HistoryForecastWrite39Service(
        models, owner_claim="mechanical39", grants={scope.scope_id: model_id}
    )
    stamps = [int((cutoff - timedelta(minutes=i)).timestamp() * 1000) for i in range(32, 1, -1)]
    raw_rows = [[str(stamp), "100", "103", "97", "100", "1", "100", "100", "1"] for stamp in stamps]
    raw = encoded({"code": "0", "data": raw_rows})
    digest, ref = originals.raw.put(raw)
    bars = tuple(
        MarketCandle(
            scope_id=scope.scope_id,
            venue=scope.venue,
            instrument=scope.instrument,
            open_at=datetime.fromtimestamp(stamp / 1000, UTC),
            close_at=datetime.fromtimestamp(stamp / 1000, UTC) + timedelta(minutes=1),
            open=Decimal(100),
            high=Decimal(103),
            low=Decimal(97),
            close=Decimal(100),
            volume_base=Decimal(1),
            volume_quote=Decimal(100),
            received_at=cutoff - timedelta(seconds=3),
            accepted_at=cutoff - timedelta(seconds=1),
            capture_ref=ref,
            capture_hash=digest,
            row_index=index,
        )
        for index, stamp in enumerate(stamps)
    )
    model_ref, model_hash = models.archive.put({"fixture": "synthetic transfer only"})
    dataset_ref, dataset_hash = originals.archive.put({"fixture": "synthetic transfer only"})
    with db.engine.begin() as connection:
        connection.execute(
            Model.__table__.insert(),
            dict(
                model_id=model_id,
                owner_claim="mechanical39",
                request_id="synthetic",
                scope_id=scope.scope_id,
                plan_id="synthetic-plan",
                dataset_id="synthetic-dataset",
                payload_json="{}",
                payload_hash=model_hash,
                original_ref=model_ref,
                original_hash=model_hash,
                known_at=cutoff - timedelta(days=1),
                recorded_at=cutoff - timedelta(days=1),
            ),
        )
        connection.execute(
            Original.__table__.insert(),
            dict(
                original_id="synthetic-dataset",
                kind="dataset",
                owner_claim="mechanical39",
                request_id="synthetic",
                plan_id="synthetic-plan",
                payload_json="{}",
                payload_hash=dataset_hash,
                original_ref=dataset_ref,
                original_hash=dataset_hash,
                recorded_at=cutoff - timedelta(days=2),
            ),
        )
        connection.execute(
            Job.__table__.insert(),
            dict(
                job_id="synthetic-job",
                epoch_id="epoch",
                plan_id="synthetic-plan",
                scope_id=scope.scope_id,
                start_at=cutoff - timedelta(days=1),
                end_at=cutoff,
                cursor="before",
                max_pages=2,
                page_count=0,
                attempts=0,
                retry_used=0,
                generation=0,
                state="pending",
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=cutoff,
                error_code=None,
            ),
        )
        connection.execute(
            Attempt.__table__.insert(),
            dict(
                attempt_id="synthetic-attempt",
                epoch_id="epoch",
                job_id="synthetic-job",
                generation=0,
                cursor="before",
                reserved_at=cutoff,
                state="completed",
                capture_id="synthetic-capture",
                result_json="{}",
                result_hash="0" * 64,
                fixture_mode=True,
            ),
        )
        connection.execute(
            RawCaptureRecord.__table__.insert(),
            dict(
                capture_id="synthetic-capture",
                context_id="synthetic",
                url="https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1m&limit=300&after="
                + str(int(cutoff.timestamp() * 1000)),
                status_code=200,
                body_hash=digest,
                body_ref=ref,
                byte_count=len(raw),
                media_type="application/json",
                adapter_version="synthetic-mechanical",
                error_code=None,
                started_at=cutoff - timedelta(seconds=4),
                completed_at=cutoff - timedelta(seconds=3),
                recorded_at=cutoff - timedelta(seconds=2),
            ),
        )
        connection.execute(
            MarketCandleRecord.__table__.insert(),
            [
                dict(
                    candle_id="candle_"
                    + canonical_hash([bar.scope_id, canonical_hash(bar.semantic_payload())])[:48],
                    scope_id=bar.scope_id,
                    venue=bar.venue,
                    instrument=bar.instrument,
                    price_type=bar.price_type,
                    interval_seconds=60,
                    open_at=bar.open_at,
                    close_at=bar.close_at,
                    received_at=bar.received_at,
                    known_at=bar.known_at,
                    revision_hash=canonical_hash(bar.semantic_payload()),
                    candle_json=bar.model_dump_json(),
                    capture_ref=ref,
                    capture_hash=digest,
                )
                for bar in bars
            ],
        )
    env = SimpleNamespace(
        db=db,
        commands=commands,
        originals=originals,
        models=models,
        service=service,
        cutoff=cutoff,
        scope=scope,
        bars=bars,
        raw=raw,
        raw_ref=ref,
        raw_hash=digest,
        model_ref=model_ref,
        model_hash=model_hash,
        dataset_ref=dataset_ref,
        dataset_hash=dataset_hash,
        directory=tmp_path,
    )
    return env


def prepared(env, request="synthetic-transfer", service=None):
    service = service or env.service
    authority = transfer.TransferAuthority39(service)
    sources = service._sources()
    feature = {
        "fixture": "synthetic transfer only",
        "selected": [bar.model_dump(mode="json") for bar in env.bars],
    }
    source = {
        "schema_version": "history-v2-forecast-source-original.v1",
        "files": [
            {
                "path": path,
                "sha256": digest,
                "bytes_base64": __import__("base64")
                .b64encode((env.originals.source_root / path).read_bytes())
                .decode(),
            }
            for path, digest in sources
        ],
    }
    feature_ref, feature_hash = service.archive.put(feature)
    source_ref, source_hash = service.archive.put(source)
    files = transfer.capture_files(
        (
            (env.model_ref, env.model_hash),
            (env.dataset_ref, env.dataset_hash),
            (str(env.originals.raw.root / env.raw_hash[:2] / env.raw_hash), env.raw_hash),
            *((str(env.originals.source_root / path), digest) for path, digest in sources),
            (feature_ref, feature_hash),
            (source_ref, source_hash),
        )
    )
    with env.commands._read_session() as session:
        rows = transfer.capture_rows(
            session,
            (),
            (
                ("market_history_v2_models", "hfit2_" + "c" * 48),
                ("market_history_v2_originals", "synthetic-dataset"),
                ("market_history_v2_jobs", "synthetic-job"),
                ("market_history_v2_attempts", "synthetic-attempt"),
            ),
        )
        candidates = transfer.feature_candidates(session, env.scope.scope_id, env.cutoff)
        feature_rows = transfer.capture_rows(
            session,
            (),
            tuple(("market_candles", row[0]) for row in candidates)
            + (("raw_captures", "synthetic-capture"),),
        )
        captures = service._capture_candidates(session, ((env.raw_ref, env.raw_hash),))
        proof = transfer.ModelProof39(
            b"synthetic-not-a-model",
            rows,
            transfer.capture_schemas39(
                session, tuple(group.table for group in (*rows, *feature_rows))
            ),
            files,
            sources,
            authority.context(session),
            env.cutoff.isoformat(),
            authority,
            threading.get_ident(),
            "a" * 64,
            hmac.digest(authority.key, ("a" * 64).encode(), "sha256"),
        )
        # Explicit test-only seeding, not an accepted serialized proof.
        authority._issued, authority._members = proof, authority.members(proof)
    frequencies = tuple(
        HistoryDatasetRiskFrequencyV2(
            threshold=threshold,
            baseline_n=60,
            conditional_n=30,
            baseline_long_probability=Decimal("0.5"),
            baseline_short_probability=Decimal("0.5"),
            conditional_long_probability=Decimal("0.5"),
            conditional_short_probability=Decimal("0.5"),
        )
        for threshold in ("0.02", "0.05")
    )
    now = env.originals.now()
    value = seal(
        HistoryDatasetProspectiveForecastV2,
        forecast_id=forecast_id("mechanical39", request),
        owner_claim="mechanical39",
        request_id=request,
        scope_id=env.scope.scope_id,
        model_id="hfit2_" + "c" * 48,
        model_hash=env.model_hash,
        model_known_at=env.cutoff - timedelta(days=1),
        model_authenticated_at=now,
        model_original_ref=env.model_ref,
        model_original_hash=env.model_hash,
        cutoff_at=env.cutoff,
        latest_close_at=env.bars[-1].close_at,
        feature_known_at=max(bar.known_at for bar in env.bars),
        selected_closes=env.bars,
        feature_original_ref=feature_ref,
        feature_original_hash=feature_hash,
        original_readset=(
            ("archive", env.model_ref, env.model_hash),
            ("raw", env.raw_ref, env.raw_hash),
        ),
        realized_vol_30m=Decimal(0),
        selected_group="low",
        frequencies=frequencies,
        source_files=sources,
        source_original_ref=source_ref,
        source_original_hash=source_hash,
        produced_at=now,
        recorded_at=now,
        fixture_mode=True,
    )
    candidate = PreparedForecast39(
        value,
        proof,
        encoded(feature),
        encoded(source),
        candidates,
        captures,
        feature_rows,
        files,
        service,
        threading.get_ident(),
        b"",
    )
    return replace(candidate, authentication=service._auth39(candidate))


def count(env):
    with env.commands._read_session() as session:
        return session.scalar(select(func.count()).select_from(Forecast))


def test_positive_native_transfer_and_measured_private_writer(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    forbidden = {env.models.read.__func__.__code__, env.models._derive.__func__.__code__}
    calls = []
    binding = {"started": threading.Event(), "errors": [], "begin_wait": None}
    Base.metadata.create_all(env.db.engine, tables=[RunRoleOutputRecord.__table__])

    def ordinary_binding_storage_writer():
        # Actual model-dispatch persistence table and fresh writer boundary.
        # Payload is synthetic: this control claims SQLite coexistence only.
        try:
            binding["started"].set()
            began = perf_counter()
            with env.db.session() as session:
                begin_immediate(session)
                binding["begin_wait"] = perf_counter() - began
                session.add(
                    RunRoleOutputRecord(
                        output_id="mechanical_binding39",
                        run_id="synthetic-run",
                        role="research_model_dispatch",
                        payload_json="{}",
                        payload_hash=hashlib.sha256(b"{}").hexdigest(),
                        schema_version="dsh-model-dispatch-binding.v1",
                        created_at=env.cutoff,
                    )
                )
                session.flush()
        except Exception as error:
            binding["errors"].append(error)

    peer = threading.Thread(target=ordinary_binding_storage_writer)
    launch = [False]

    def launch_during_forecast_lock(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            threading.get_ident() == item.thread_id
            and statement.startswith("SAVEPOINT")
            and not launch[0]
        ):
            launch[0] = True
            peer.start()
            assert binding["started"].wait(0.5)

    event.listen(env.db.engine, "after_cursor_execute", launch_during_forecast_lock)

    def profile(frame, kind, _arg):
        if kind == "call" and frame.f_code in forbidden:
            calls.append(frame.f_code.co_name)
            raise AssertionError("writer/proof readback entered deep model authentication")

    sys.setprofile(profile)
    try:
        saved = env.service.save_prepared(item)
    finally:
        sys.setprofile(None)
        event.remove(env.db.engine, "after_cursor_execute", launch_during_forecast_lock)
    peer.join(timeout=5)
    assert launch[0] and not peer.is_alive() and not binding["errors"]
    assert binding["begin_wait"] > 0
    assert not calls and count(env) == 1 and saved == item.value
    phases = dict(env.service.last_phase_timing)
    assert phases["writer_hold"] > 0 and phases["commit"] > 0
    assert phases["writer_wait"] >= 0 and phases["file_bytes_per_pass"] > len(env.raw)
    # Real independent connection writes during native RO and after COMMIT.
    with env.commands._read_session() as session:
        transfer.verify_rows(session, item.proof.rows)
        with env.db.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE market_history_v2_jobs SET cursor='progress' WHERE job_id='synthetic-job'"
            )
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE market_history_v2_jobs SET page_count=1 WHERE job_id='synthetic-job'"
        )
    print("SYNTHETIC_MECHANICAL_PHASES39", json_for(phases))
    print(
        "SYNTHETIC_BINDING_SQLITE_COEXISTENCE39",
        json_for({"begin_wait": binding["begin_wait"], "SQLITE_BUSY": False}),
    )


def json_for(value):
    return __import__("json").dumps(value, sort_keys=True)


def test_issuer_thread_hmac_and_grant_changes_zero_insert(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    for invalid in (
        replace(item, issuer=object()),
        replace(item, thread_id=-1),
        replace(item, authentication=b"bad"),
        replace(item, proof=replace(item.proof, model_bytes=b"forged")),
    ):
        with pytest.raises(ValueError):
            with env.commands._write_session() as session:
                env.service.record(session, invalid)
    env.service.owner_claim = "other"
    with pytest.raises(ValueError):
        with env.commands._write_session() as session:
            env.service.record(session, item)
    assert count(env) == 0


def test_complete_rows_raw_archive_revision_and_normal_job_progress(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE market_history_v2_jobs SET cursor='normal',page_count=1 "
            "WHERE job_id='synthetic-job'"
        )
    with env.commands._read_session() as session:
        env.service._verify39(session, item)
    for path in (Path(env.model_ref), env.originals.raw.root / env.raw_hash[:2] / env.raw_hash):
        old = path.read_bytes()
        path.write_bytes(old + b" ")
        try:
            with pytest.raises(ValueError, match="original_bytes_changed"):
                with env.commands._write_session() as session:
                    env.service.record(session, item)
        finally:
            path.write_bytes(old)
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE market_history_v2_attempts SET state='changed' "
            "WHERE attempt_id='synthetic-attempt'"
        )
    with pytest.raises(ValueError, match="original_metadata_changed"):
        with env.commands._write_session() as session:
            env.service.record(session, item)
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE market_history_v2_attempts SET state='completed' "
            "WHERE attempt_id='synthetic-attempt'"
        )
        connection.exec_driver_sql(
            "INSERT INTO market_candles SELECT 'new-known-revision',scope_id,venue,instrument,"
            "price_type,interval_seconds,open_at,close_at,received_at,known_at,'new-semantic',"
            "candle_json,capture_ref,capture_hash FROM market_candles LIMIT 1"
        )
    with pytest.raises(ValueError, match="feature_selection_closure_changed"):
        with env.commands._write_session() as session:
            env.service.record(session, item)
    assert count(env) == 0


def test_first_save_request_conflict_and_cancel_before_commit(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    saved = env.service.save_prepared(item)
    assert env.service.save_prepared(item) == saved and count(env) == 1
    other = replace(
        item,
        value=seal(
            HistoryDatasetProspectiveForecastV2,
            **{**item.value.model_dump(exclude={"payload_hash"}), "model_id": "hfit2_" + "d" * 48},
        ),
    )
    other = replace(other, authentication=env.service._auth39(other))
    with pytest.raises(ValueError, match="request_conflict"):
        with env.commands._write_session() as session:
            env.service.record(session, other)
    cancelled = prepared(env, "cancelled")
    checks = [0]

    def check():
        checks[0] += 1
        if checks[0] == 2:
            raise ValueError("synthetic cancellation before COMMIT")

    with pytest.raises(ValueError, match="cancellation"):
        env.service.save_prepared(cancelled, check=check)
    assert count(env) == 1 and not env.service._fence_path(env.scope.scope_id).exists()
    # Consistent row/archive payload alteration must fail the proof reader even
    # though record's immutable fastpath returns that altered first object.
    altered_frequency = saved.frequencies[0].model_copy(
        update={"baseline_long_probability": Decimal("0.75")}
    )
    altered = seal(
        HistoryDatasetProspectiveForecastV2,
        **{
            **saved.model_dump(exclude={"payload_hash"}),
            "frequencies": (altered_frequency, saved.frequencies[1]),
        },
    )
    ref, digest = env.service.archive.put(altered.model_dump(mode="json"))
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE market_history_v2_forecasts SET payload_json=?,payload_hash=?,"
            "original_ref=?,original_hash=? WHERE forecast_id=?",
            (
                encoded(altered.model_dump(mode="json")).decode(),
                altered.payload_hash,
                ref,
                digest,
                altered.forecast_id,
            ),
        )
    with pytest.raises(HistoryDatasetForecastCommittedReadbackError):
        env.service.save_prepared(item)
    assert count(env) == 1 and env.service._fence_path(env.scope.scope_id).exists()


@pytest.mark.parametrize("failure", ("ack", "readback", "close"))
def test_actual_commit_ack_loss_single_row_persistent_fence_no_replay(
    tmp_path, monkeypatch, failure
):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    session_class = env.db.factory.class_

    def ack_lost(_session):
        if failure == "ack":
            raise RuntimeError("synthetic ACK loss after actual SQLite COMMIT")
        if failure == "close":
            actual_close = _session.close

            def fail_close():
                actual_close()
                raise RuntimeError("synthetic close failure after known COMMIT")

            _session.close = fail_close
            return
        # COMMIT returned normally; a lost final archive is a known committed
        # readback failure and retains the same persistent scope fence.
        with env.db.engine.connect() as connection:
            ref = connection.exec_driver_sql(
                "SELECT original_ref FROM market_history_v2_forecasts"
            ).scalar_one()
        Path(ref).unlink()

    event.listen(session_class, "after_commit", ack_lost)
    try:
        error_type = (
            HistoryForecastCommitUnconfirmed39
            if failure == "ack"
            else HistoryDatasetForecastCommittedReadbackError
        )
        with pytest.raises(error_type):
            env.service.save_prepared(item)
    finally:
        event.remove(session_class, "after_commit", ack_lost)
    assert count(env) == 1 and env.service._fence_path(env.scope.scope_id).exists()
    reopened = HistoryForecastWrite39Service(
        env.models, owner_claim="mechanical39", grants=dict(env.service.grants)
    )
    with pytest.raises(HistoryForecastCommitUnconfirmed39, match="fence"):
        reopened.save_prepared(prepared(env, service=reopened))
    assert count(env) == 1


def test_dispatch_unknown_vector_rejects_without_archive_code_execution(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    unknown = replace(
        item,
        value=seal(
            HistoryDatasetProspectiveForecastV2,
            **{
                **item.value.model_dump(exclude={"payload_hash"}),
                "source_files": (("unknown", "0" * 64),),
            },
        ),
    )
    ref, digest = env.service.archive.put(unknown.value.model_dump(mode="json"))
    value = unknown.value
    with env.db.engine.begin() as connection:
        connection.execute(
            Forecast.__table__.insert(),
            dict(
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
            ),
        )
    with pytest.raises(ValueError, match="unknown_or_mixed"):
        with env.commands._read_session() as session:
            HistoryForecastDispatch39Service(env.service).read(session, value.forecast_id)
    assert count(env) == 1


def test_proposed_root_gate_exact_dispatch_owner_and_originals(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    path = Path(__file__).resolve().parents[2] / (
        "artifacts/market-risk-implementation-20261007/B2/forecast-write39r2/"
        "proposed-root/apps/hub_api/history_product_routes.py"
    )
    name = "apps.hub_api.private_proposed_history_product_routes39"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    authority = HistoryOwnerAuthority(
        signing_key=b"s" * 32,
        pairing_sha256=hashlib.sha256(b"private-mechanical39-token").digest(),
        owner_identity="mechanical39",
        origin="http://127.0.0.1:8894",
    )
    dispatch = HistoryForecastDispatch39Service(env.service)
    boot = module.HistoryProductBootstrap(
        authority, env.originals, forecasts=dispatch, allow_producer=True
    )
    assert boot.forecasts is dispatch
    with pytest.raises(ValueError, match="producer_owner_or_resource"):
        module.HistoryProductBootstrap(
            authority,
            env.originals,
            forecasts=SimpleNamespace(owner_claim="mechanical39", models=env.models),
            allow_producer=True,
        )
    wrong_owner = HistoryForecastDispatch39Service(
        HistoryForecastWrite39Service(
            env.models, owner_claim="wrong-owner", grants=dict(env.service.grants)
        )
    )
    with pytest.raises(ValueError, match="producer_owner_or_resource"):
        module.HistoryProductBootstrap(
            authority, env.originals, forecasts=wrong_owner, allow_producer=True
        )
    other_dir = tmp_path / "wrong-resource"
    other_dir.mkdir()
    other = HistoryDatasetV2Service(env.commands, other_dir, fixture_mode=True)
    wrong_resource = HistoryForecastDispatch39Service(
        HistoryForecastWrite39Service(
            HistoryDatasetModelV2Service(other),
            owner_claim="mechanical39",
            grants=dict(env.service.grants),
        )
    )
    with pytest.raises(ValueError, match="producer_owner_or_resource"):
        module.HistoryProductBootstrap(
            authority, env.originals, forecasts=wrong_resource, allow_producer=True
        )


def test_schema_added_column_after_proof_zero_insert(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    with env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE market_candles ADD COLUMN unexpected_metadata TEXT "
            "NOT NULL DEFAULT 'changed-after-proof'"
        )
    with pytest.raises(ValueError, match="complete_metadata_schema_changed"):
        with env.commands._write_session() as session:
            env.service.record(session, item)
    assert count(env) == 0


def test_equivalent_native_integer_real_and_issued_leaf_mutation_zero_insert(tmp_path, monkeypatch):
    env = fixture(tmp_path, monkeypatch)
    item = prepared(env)
    group = next(group for group in item.proof.rows if group.table == "market_history_v2_jobs")
    original_rows = group.rows
    with env.commands._read_session() as session:
        real = (
            session.connection()
            .exec_driver_sql(
                "SELECT CAST(max_pages AS REAL) FROM market_history_v2_jobs "
                "WHERE job_id='synthetic-job'"
            )
            .scalar_one()
        )
        changed = list(original_rows[0])
        changed[6] = real
        changed_rows = (tuple(changed),)
        assert type(real) is float and type(original_rows[0][6]) is int
        assert original_rows == changed_rows  # The superseded tuple== gate admitted this.
        assert not transfer.native_equal39(original_rows, changed_rows)
        with pytest.raises(ValueError, match="original_metadata_changed"):
            transfer.verify_rows(session, (transfer.NativeRows39(group.table, changed_rows),))
    # Even deliberate frozen-dataclass mutation cannot change the retained
    # immutable native leaf snapshot used by the exact internal issuer.
    object.__setattr__(group, "rows", changed_rows)
    with pytest.raises(ValueError, match="model_proof_changed"):
        with env.commands._write_session() as session:
            env.service.record(session, item)
    assert count(env) == 0
