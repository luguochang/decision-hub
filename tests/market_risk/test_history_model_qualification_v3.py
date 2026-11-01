"""Six private mechanical groups. Synthetic deep issuer; no real model/finance P."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import zipfile
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import (
    history_forecast_dispatch41 as dispatch_module,
)
from packages.kernel.decision_hub_kernel.application import history_forecast_transfer39 as transfer
from packages.kernel.decision_hub_kernel.application import history_model_freeze_v3 as freeze
from packages.kernel.decision_hub_kernel.application import (
    history_model_qualification_v3 as qualification,
)
from packages.kernel.decision_hub_kernel.application import (
    history_qualified_forecast_v3 as forecast,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import Base, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as ForecastRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as ModelRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_model_qualification_v3_records import (
    HistoryModelQualificationAcceptanceV3Record as EventRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_model_qualification_v3_records import (
    HistoryModelQualificationV3Record as QualificationRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_model_qualification_v3_records import (
    HistoryQualifiedForecastAcceptanceV3Record as AckRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_model_qualification_v3_records import (
    HistoryQualifiedForecastBindingV3Record as BindingRow,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_observed_training import seal
from tests.market_risk.test_history_outcome_forecasts_v2 import save as save40
from tests.market_risk.test_history_outcome_forecasts_v2 import setup as setup40


def refresh_guards(monkeypatch):
    # Only these new41 modules have their explicit test issuer substituted.
    for module in (freeze, qualification, forecast):
        monkeypatch.setattr(
            module,
            "LOADED_METHODS",
            tuple(
                (cls, name, getattr(cls, name), getattr(cls, name).__code__)
                for cls, name, _function, _code in module.LOADED_METHODS
            ),
        )


def setup41(tmp_path, monkeypatch, *, publish=True, second_model=False):
    item = setup40(tmp_path, monkeypatch, count=1, second_model=second_model)
    env = item.env
    Base.metadata.create_all(
        env.db.engine,
        tables=[record.__table__ for record in (QualificationRow, EventRow, BindingRow, AckRow)],
    )
    with env.db.engine.begin() as connection:
        for identifier, (model, _ref, _digest) in item.registry.items():
            connection.execute(
                ModelRow.__table__.update()
                .where(ModelRow.model_id == identifier)
                .values(
                    request_id=model.request_id,
                    plan_id=model.plan_id,
                    known_at=model.model_known_at,
                    recorded_at=model.recorded_at,
                )
            )
    calls = []

    def fake_issue(self, session, identifier):
        calls.append(
            (identifier, id(session), id(session.connection().connection.driver_connection))
        )
        model, ref, digest = item.registry[identifier]
        authority = transfer.TransferAuthority39(self.forecasts.writer)
        keys = [
            ("market_history_v2_models", identifier),
            ("market_history_v2_originals", "synthetic-dataset"),
            ("market_history_v2_jobs", "synthetic-job"),
            ("market_history_v2_attempts", "synthetic-attempt"),
        ]
        for record, table in (
            (MarketCandleRecord, "market_candles"),
            (RawCaptureRecord, "raw_captures"),
        ):
            key = tuple(record.__table__.primary_key)[0]
            keys.extend((table, identity) for identity in session.scalars(select(key)))
        rows = transfer.capture_rows(session, (), tuple(keys))
        sources = self.forecasts._sources()
        files = transfer.capture_files(
            (
                (ref, digest),
                (env.dataset_ref, env.dataset_hash),
                (model.source_original_ref, model.source_original_hash),
                (str(env.originals.raw.root / env.raw_hash[:2] / env.raw_hash), env.raw_hash),
                *((str(env.originals.source_root / path), sha) for path, sha in sources),
            )
        )
        body = encoded(model.model_dump(mode="json"))
        manifest = hashlib.sha256(body).hexdigest()
        proof = transfer.ModelProof39(
            body,
            rows,
            transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS)),
            files,
            sources,
            authority.context(session),
            env.originals.now().isoformat(),
            authority,
            threading.get_ident(),
            manifest,
            hmac.digest(authority.key, manifest.encode(), "sha256"),
        )
        authority._issued, authority._members = proof, authority.members(proof)
        return authority, proof, model

    monkeypatch.setattr(freeze.HistoryModelFreezeV3Publisher, "_issue_model", fake_issue)
    refresh_guards(monkeypatch)
    grants = {model.scope_id: identifier for identifier, (model, _, _) in item.registry.items()}
    service = qualification.HistoryModelQualificationV3Service(
        item.dispatch, publication_grants=grants, serving_grants={}
    )
    prepared = service.prepare(model_ids=tuple(item.registry), request_id="private-qualification41")
    values = service.publish(prepared) if publish else ()
    serving = {value.scope_id: value.qualification_id for value in values}
    active = qualification.HistoryModelQualificationV3Service(
        item.dispatch, publication_grants=grants, serving_grants=serving
    )
    writer = forecast.HistoryQualifiedForecastV3Service(active)
    # Keep the same pinned clock function/code; advance its private fixture clock
    # after actual qualification confirmation, before freezing the next T.
    env.originals.clock.__closure__[0].cell_contents = env.cutoff + timedelta(minutes=1)
    return SimpleNamespace(
        item=item,
        env=env,
        service=service,
        prepared=prepared,
        values=values,
        active=active,
        writer=writer,
        dispatch=dispatch_module.HistoryForecastDispatch41Service(writer),
        calls=calls,
    )


def prepared_forecast(item, request="private-forecast41"):
    proof = item.writer.authenticate_model(item.env.scope.scope_id)
    with item.env.commands._read_session() as session:
        prepared = item.writer.prepare(
            session,
            scope_id=item.env.scope.scope_id,
            request_id=request,
            cutoff_at=item.env.originals.now().replace(second=0, microsecond=0),
            proof=proof,
        )
    return proof, prepared


def test_freeze_complete_typed_selected_snapshot_and_whole_originals_once_two_models(
    tmp_path, monkeypatch
):
    item = setup41(tmp_path, monkeypatch, second_model=True)
    assert len(item.calls) == 2 and len({entry[0] for entry in item.calls}) == 2
    assert len({entry[1:] for entry in item.calls}) == 1
    value = item.values[0]
    bundle = freeze.FrozenModelBundleV3(
        item.active.root,
        value.bundle_ref,
        value.bundle_hash,
        value.manifest_hash,
        checkpoint=lambda: None,
    )
    assert len(bundle.manifest["models"]) == 2
    assert bundle.manifest["native_format"] == "sqlite-native-unaffinitized.v1"
    with item.env.commands._read_session() as live, bundle.snapshot_session() as frozen:
        for table, expected_schema in bundle.manifest["schemas"]:
            rows = frozen.connection().exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1').all()
            keys = bundle.manifest["row_keys"].get(table, [])
            assert len(rows) == len(keys)
            if not keys:
                continue
            original = (
                live.connection()
                .exec_driver_sql(
                    f'SELECT * FROM "{table}" WHERE "{expected_schema[0][1]}" IN '
                    f"({','.join('?' for _ in keys)}) ORDER BY 1",
                    tuple(keys),
                )
                .all()
            )
            assert transfer.native_equal39(
                tuple(tuple(row) for row in rows), tuple(tuple(row) for row in original)
            )
    for logical, sha, size, _member in bundle.manifest["files"]:
        body = bundle.read(logical, sha)
        assert len(body) == size and hashlib.sha256(body).hexdigest() == sha
    with zipfile.ZipFile(value.bundle_ref) as archive:
        if bundle.manifest["schema_version"] == "history-model-frozen-bundle.v4":
            assert bundle.manifest["transport_limits"] == freeze._transport_limits46()
            assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())
        else:
            assert bundle.manifest["schema_version"] == "history-model-frozen-bundle.v3"
            assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    assert all(value.financial_qualified is False for value in item.values)


def test_qualification_firstsave_content_grant_bundle_schema_owner_and_typed_proof_reject(
    tmp_path, monkeypatch
):
    item = setup41(tmp_path, monkeypatch)
    assert item.service.publish(item.prepared) == item.values
    with item.env.commands._read_session() as session:
        value, event_value, _bundle, _model = item.active.read(
            session, item.values[0].qualification_id
        )
        assert event_value.state == "confirmed" and value.state == "prepared"
    proof, prepared = prepared_forecast(item)
    rows = proof.rows
    object.__setattr__(
        proof, "rows", (*rows[:-1], (rows[-1][0], rows[-1][1], (*rows[-1][2][:-1], 1.0)))
    )
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="proof_changed"),
    ):
        item.writer._authenticate(session, proof)
    object.__setattr__(proof, "rows", rows)
    captures = prepared.capture_candidates
    capture = captures[0]
    byte_count = capture[6]
    assert type(byte_count) is int
    replacement = (*capture[:6], float(byte_count), *capture[7:])
    assert replacement == capture  # ordinary tuple equality incorrectly accepts this
    object.__setattr__(prepared, "capture_candidates", (replacement, *captures[1:]))
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="immutable_proof_changed"),
    ):
        item.writer._verify_small(session, prepared)
    object.__setattr__(prepared, "capture_candidates", captures)
    with item.env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE market_history_qualified_forecast_bindings_v3 ADD COLUMN extra TEXT"
        )
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="schema_changed"),
    ):
        item.writer._verify_small(session, prepared)
    target = Path(item.values[0].bundle_ref)
    original = target.read_bytes()
    target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="full_bundle_hash_changed"),
    ):
        item.active.read(session, item.values[0].qualification_id)
    target.write_bytes(original)
    with pytest.raises(ValueError, match="server_grants_invalid"):
        qualification.HistoryModelQualificationV3Service(
            item.item.dispatch,
            publication_grants={},
            serving_grants={"other": value.qualification_id},
        )


def test_online_no_historical_deep_or_bundle_read_under_writer_actual_private_sqlite_ack(
    tmp_path, monkeypatch
):
    item = setup41(tmp_path, monkeypatch)
    proof, prepared = prepared_forecast(item)
    writer_io, writer_sql, depth = [], [], [False]

    def observe(connection, cursor, statement, parameters, context, executemany):
        upper = statement.strip().upper()
        if upper.startswith("BEGIN IMMEDIATE"):
            depth[0] = True
        if depth[0]:
            writer_sql.append(upper)

    def profile(frame, event_name, arg):
        if (
            event_name == "call"
            and depth[0]
            and frame.f_code
            in (
                freeze._fingerprint.__code__,
                freeze.FrozenModelBundleV3.verify.__code__,
                freeze.FrozenModelBundleV3.model.__code__,
                item.env.models._derive.__func__.__code__,
                item.env.models.read.__func__.__code__,
                transfer.verify_files.__code__,
            )
        ):
            writer_io.append(frame.f_code.co_name)

    import sys

    event.listen(item.env.db.engine, "before_cursor_execute", observe)
    event.listen(item.env.db.engine, "commit", lambda connection: depth.__setitem__(0, False))
    old_profile = sys.getprofile()
    sys.setprofile(profile)
    try:
        started = perf_counter()
        value = item.writer.save_prepared(prepared)
        elapsed = perf_counter() - started
    finally:
        sys.setprofile(old_profile)
        event.remove(item.env.db.engine, "before_cursor_execute", observe)
    assert len(item.calls) == 1 and writer_io == []
    assert not any(
        "FROM MARKET_HISTORY_V2_ATTEMPTS" in sql or "FROM MARKET_HISTORY_V2_JOBS" in sql
        for sql in writer_sql
    )
    assert item.writer.last_phase_timing["writer_hold"] >= 0
    with item.env.commands._read_session() as session:
        assert item.dispatch.read(session, value.forecast_id) == value
        ack = item.writer._ack(session, value.forecast_id)
        assert ack.confirmed_at >= value.recorded_at
    assert item.writer.producer_once(value.scope_id, value.request_id) == value
    # Consistent row+archive+binding+ACK alteration must still fail actual math.
    with item.env.commands._read_session() as session:
        binding = item.writer._binding(session, value.forecast_id)
        ack = item.writer._ack(session, value.forecast_id)
    changed_frequency = value.frequencies[0].model_copy(
        update={"baseline_long_probability": value.frequencies[0].baseline_long_probability / 2}
    )
    forged = seal(
        forecast.HistoryDatasetProspectiveForecastV2,
        **{
            **value.model_dump(exclude={"payload_hash", "frequencies"}),
            "frequencies": (changed_frequency, value.frequencies[1]),
        },
    )
    changed_binding = seal(
        forecast.HistoryQualifiedForecastBindingV3,
        **{
            **binding.model_dump(exclude={"payload_hash", "forecast_hash"}),
            "forecast_hash": forged.payload_hash,
        },
    )
    changed_ack = seal(
        forecast.HistoryQualifiedForecastAcceptanceV3,
        **{
            **ack.model_dump(exclude={"payload_hash", "forecast_hash", "binding_hash"}),
            "forecast_hash": forged.payload_hash,
            "binding_hash": changed_binding.payload_hash,
        },
    )
    with item.env.db.engine.begin() as connection:
        save40(connection, item.env.service, forged)
        for record, dto in ((BindingRow, changed_binding), (AckRow, changed_ack)):
            ref, digest = item.active.archive.put(dto.model_dump(mode="json"))
            connection.execute(
                record.__table__.update()
                .where(record.forecast_id == value.forecast_id)
                .values(
                    **{
                        name: getattr(dto, name)
                        for name in record.__table__.columns.keys()
                        if name
                        not in ("original_ref", "original_hash", "payload_json", "payload_hash")
                    },
                    original_ref=ref,
                    original_hash=digest,
                    payload_json=encoded(dto.model_dump(mode="json")).decode(),
                    payload_hash=dto.payload_hash,
                )
            )
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="inputs_or_numerics_changed"),
    ):
        item.dispatch.read(session, value.forecast_id)
    print(
        {
            "scope": "private-synthetic-model-only",
            "online_seconds": elapsed,
            "phases": dict(item.writer.last_phase_timing),
            "actual87_model": "UNKNOWN",
        }
    )


def test_current_input_change_known_rollback_and_ordinary_binding_coexists(tmp_path, monkeypatch):
    item = setup41(tmp_path, monkeypatch)
    _proof, prepared = prepared_forecast(item)
    with item.env.db.engine.begin() as connection:
        connection.execute(RawCaptureRecord.__table__.update().values(adapter_version="changed"))
    with pytest.raises(ValueError, match="current_input_native_CAS_changed"):
        item.writer.save_prepared(prepared)
    assert not item.writer._fence_path(item.env.scope.scope_id).exists()
    with item.env.commands._read_session() as session:
        assert session.get(ForecastRow, prepared.value.forecast_id) is None
    # Ordinary model-binding-sized private transaction is never given a long
    # historical audit lock by qualification/current-input preparation.
    with item.env.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ordinary_binding41 (id TEXT PRIMARY KEY, model_id TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ordinary_binding41 VALUES (?,?)", ("one", item.item.model.model_id)
        )
        assert (
            connection.exec_driver_sql("SELECT model_id FROM ordinary_binding41").scalar_one()
            == item.item.model.model_id
        )


def test_unknown_commit_and_committed_byte_change_keep_fence_zero_replay(tmp_path, monkeypatch):
    item = setup41(tmp_path, monkeypatch)
    _proof, prepared = prepared_forecast(item)
    original_commit = Session.commit

    def uncertain_commit(session):
        if session.get(BindingRow, prepared.value.forecast_id) is not None:
            original_commit(session)
            raise sqlite3.OperationalError("private commit ACK lost")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", uncertain_commit)
    with pytest.raises(forecast.HistoryForecastCommitUnconfirmed39, match="commit_unconfirmed"):
        item.writer.save_prepared(prepared)
    assert item.writer._fence_path(item.env.scope.scope_id).exists()
    with item.env.commands._read_session() as session:
        assert session.get(ForecastRow, prepared.value.forecast_id) is not None
        assert session.get(AckRow, prepared.value.forecast_id) is None
        with pytest.raises(ValueError, match="reconciliation"):
            item.dispatch.read(session, prepared.value.forecast_id)
    with pytest.raises(ValueError, match="reconciliation"):
        item.writer.producer_once(item.env.scope.scope_id)
    monkeypatch.setattr(Session, "commit", original_commit)
    other = setup41(tmp_path / "second", monkeypatch)
    _proof, prepared = prepared_forecast(other)

    def changed_commit(session):
        is_forecast = session.get(BindingRow, prepared.value.forecast_id) is not None
        original_commit(session)
        if is_forecast:
            path = Path(other.values[0].bundle_ref)
            body = path.read_bytes()
            path.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))

    monkeypatch.setattr(Session, "commit", changed_commit)
    with pytest.raises(forecast.HistoryDatasetForecastCommittedReadbackError):
        other.writer.save_prepared(prepared)
    assert other.writer._fence_path(other.env.scope.scope_id).exists()


def test_finite_legacy_vectors_unknown_reject_revocation_and_clock_bounds(tmp_path, monkeypatch):
    item = setup41(tmp_path, monkeypatch)
    legacy = item.item.values[0]
    reads = []

    def fake_legacy(self, session, identifier):
        reads.append(identifier)
        return legacy

    monkeypatch.setattr(
        dispatch_module.HistoryForecastDispatch41Service, "_read_legacy", fake_legacy
    )
    monkeypatch.setattr(
        dispatch_module,
        "LOADED_METHODS",
        tuple(
            (name, function, function.__code__)
            for name, function in dispatch_module.HistoryForecastDispatch41Service.__dict__.items()
            if callable(function) and hasattr(function, "__code__")
        ),
    )
    with item.env.commands._read_session() as session:
        assert item.dispatch.read(session, legacy.forecast_id) == legacy
    assert reads == [legacy.forecast_id]
    release39 = seal(
        forecast.HistoryDatasetProspectiveForecastV2,
        **{
            **legacy.model_dump(exclude={"payload_hash", "source_files"}),
            "source_files": item.item.dispatch._sources(),
        },
    )
    with item.env.db.engine.begin() as connection:
        save40(connection, item.env.service, release39)
    with item.env.commands._read_session() as session:
        assert item.dispatch.read(session, legacy.forecast_id) == legacy
    unknown = seal(
        forecast.HistoryDatasetProspectiveForecastV2,
        **{
            **legacy.model_dump(exclude={"payload_hash", "source_files"}),
            "source_files": ((*legacy.source_files[:-1], (legacy.source_files[-1][0], "0" * 64))),
        },
    )
    with item.env.db.engine.begin() as connection:
        save40(connection, item.env.service, unknown)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="unknown_or_mixed"),
    ):
        item.dispatch.read(session, legacy.forecast_id)
    proof, _prepared = prepared_forecast(item)
    with (
        item.env.commands._read_session() as session,
        pytest.raises(ValueError, match="internal_T"),
    ):
        item.writer.prepare(
            session,
            scope_id=item.env.scope.scope_id,
            request_id="future-T",
            cutoff_at=item.env.originals.now().replace(second=0, microsecond=0)
            + timedelta(minutes=1),
            proof=proof,
        )
    event_value = item.active.append_state(
        item.values[0].qualification_id,
        request_id="revoke-private",
        state="revoked",
        reason_code="private_control",
    )
    assert event_value.version == 2 and event_value.state == "revoked"
    with pytest.raises(ValueError, match="not_confirmed"):
        item.writer.authenticate_model(item.env.scope.scope_id)
    with pytest.raises(ValueError, match="freeze_request_invalid"):
        item.service.prepare(
            model_ids=(item.item.model.model_id,), request_id="over-budget", budget_seconds=181
        )
