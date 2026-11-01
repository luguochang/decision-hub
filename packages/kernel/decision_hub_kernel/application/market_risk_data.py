"""Independent acquisition cycle and read-only, committed risk snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)

from ..persistence.db import Database, as_utc, utcnow
from ..persistence.sqlite_busy import is_sqlite_initial_begin_busy
from ..persistence.risk_records import MarketPathAcquisitionRecord
from ..ports.market_component_consumer import MarketComponentConsumer
from ..ports.market_path import (
    MarketPathScope,
    MarketPricePath,
    aware_utc,
    canonical_hash,
    path_hashes,
    spot_scope,
)
from ..ports.risk_monitor import RiskMonitorInput
from .component_consumption_binding import component_consumption_reference_in_session
from .fact_lineage import FactLineageStore
from .market_features import MarketFeatureSnapshot, build_market_features
from .market_path_store import MarketPathStore, archive_feature
from .risk_monitor import canonical_json


class _MarketInitialBeginPending(RuntimeError):
    def __init__(self, original_error: Exception):
        super().__init__("market_acquisition_initial_begin_busy")
        self.original_error = original_error


async def _begin_market_acquisition(
    session: Session, *, deadline_at: float, clock: Callable[[], datetime],
    lease_expires_at: datetime | None = None,
) -> None:
    """Yield only before an untouched initial BEGIN; never replay work or commit."""
    loop = asyncio.get_running_loop()
    if not isinstance(deadline_at, (int, float)) or not math.isfinite(deadline_at):
        raise ValueError("market_acquisition_admission_deadline_invalid")
    lease = aware_utc(lease_expires_at) if lease_expires_at is not None else None

    def checkpoint() -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError()
        if loop.time() >= deadline_at:
            raise TimeoutError("market_acquisition_channel_deadline_elapsed")
        if lease is not None and aware_utc(clock()) >= lease:
            raise TimeoutError("market_acquisition_lease_expired")

    checkpoint()
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection) or native.in_transaction:
        raise ValueError("market_acquisition_fresh_transaction_required")
    if session.new or session.dirty or session.deleted or session.get_nested_transaction():
        raise ValueError("market_acquisition_fresh_session_required")
    changes, transaction = native.total_changes, session.get_transaction()

    def initial_unchanged() -> None:
        if (session.connection() is not connection
            or connection.connection.driver_connection is not native
            or session.get_transaction() is not transaction
            or native.in_transaction or native.total_changes != changes
            or session.new or session.dirty or session.deleted or session.get_nested_transaction()):
            raise ValueError("market_acquisition_initial_state_changed")

    row = native.execute("PRAGMA busy_timeout").fetchone()
    if row is None or len(row) != 1 or type(row[0]) is not int or not 0 <= row[0] <= 2_147_483_647:
        raise ValueError("market_acquisition_busy_timeout_invalid")
    original_timeout, failure = row[0], None
    try:
        for attempt in range(16):
            checkpoint()
            initial_unchanged()
            remaining_ms = (deadline_at - loop.time()) * 1000
            if lease is not None:
                remaining_ms = min(remaining_ms, (lease - aware_utc(clock())).total_seconds() * 1000)
            if remaining_ms < 1:
                raise TimeoutError("market_acquisition_admission_deadline_elapsed")
            native.execute(f"PRAGMA busy_timeout={min(100, int(remaining_ms))}")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except Exception as error:
                if not is_sqlite_initial_begin_busy(error):
                    raise
                initial_unchanged()
                checkpoint()
                if attempt == 15:
                    raise _MarketInitialBeginPending(error) from error
                # Deliver queued cancellation/timers before another native wait.
                await asyncio.sleep(0)
            else:
                if (session.connection() is not connection
                    or connection.connection.driver_connection is not native
                    or session.get_transaction() is not transaction
                    or not native.in_transaction or native.total_changes != changes
                    or session.new or session.dirty or session.deleted or session.get_nested_transaction()):
                    raise ValueError("market_acquisition_initial_state_changed")
                checkpoint()
                return
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            native.execute(f"PRAGMA busy_timeout={original_timeout}")
            if native.execute("PRAGMA busy_timeout").fetchone() != (original_timeout,):
                raise ValueError("market_acquisition_busy_timeout_restore_failed")
        except Exception as restore_error:
            try:
                connection.invalidate()
            except Exception:
                restore_error.add_note("market_acquisition_connection_invalidation_failed")
            if failure is not None:
                failure.add_note("market_acquisition_busy_timeout_restore_failed")
                raise failure from restore_error
            raise ValueError("market_acquisition_busy_timeout_restore_failed") from restore_error


class MarketRiskDataService:
    def __init__(
        self,
        database: Database,
        raw_archive: RawBodyArchive,
        feature_root: Path,
        *,
        adapter=None,
        scopes: tuple[MarketPathScope, ...] | None = None,
        clock: Callable[[], datetime] = utcnow,
        enabled: bool = False,
        poll_seconds: int = 60,
        stale_after_seconds: int = 180,
        overlap_minutes: int = 2,
        lease_seconds: int = 180,
        component_consumer: MarketComponentConsumer | None = None,
    ) -> None:
        if (
            poll_seconds < 60
            or poll_seconds % 60
            or not 1 <= overlap_minutes <= 30
            or lease_seconds < 90
        ):
            raise ValueError("market_acquisition_config_invalid")
        if stale_after_seconds < 60:
            raise ValueError("market_acquisition_stale_config_invalid")
        self.database, self.raw_archive, self.feature_root = database, raw_archive, feature_root
        self.component_consumer = component_consumer
        self.adapter, self.clock, self.enabled = adapter or OKXMarketPathAdapter(), clock, enabled
        self.poll_seconds, self.stale_after_seconds = poll_seconds, stale_after_seconds
        self.overlap_minutes, self.lease_seconds = overlap_minutes, lease_seconds
        supported = (
            scopes if scopes is not None else (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))
        )
        self.scopes = {scope.scope_id: scope for scope in supported}
        if len(self.scopes) != len(supported):
            raise ValueError("market_acquisition_duplicate_scope")
        if any(scope != spot_scope(scope.instrument) for scope in supported):
            raise ValueError("market_acquisition_scope_not_enabled")
        self.store = MarketPathStore(database, raw_archive, clock=clock)
        self.lineage = FactLineageStore(database)

    async def _write(self, session, *, deadline_at=None, lease_expires_at=None):
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            loop = asyncio.get_running_loop()
            await _begin_market_acquisition(
                session, deadline_at=deadline_at if deadline_at is not None else loop.time() + 90,
                clock=self.clock, lease_expires_at=lease_expires_at,
            )

    async def _claim(self, scope: MarketPathScope, *, deadline_at=None, slot_at=None) -> MarketPathAcquisitionRecord | None:
        now = aware_utc(self.clock())
        slot = aware_utc(slot_at) if slot_at is not None else now.replace(second=0, microsecond=0)
        if slot > now or slot.second != 0 or slot.microsecond != 0:
            raise ValueError("market_acquisition_batch_slot_invalid")
        # Include the minute that just closed; provider confirm=1 remains mandatory.
        # Do not bake an extra minute of latency into every monitoring cycle.
        end = slot
        with self.database.session() as session:
            await self._write(session, deadline_at=deadline_at)
            latest = (
                session.query(MarketPathAcquisitionRecord)
                .filter_by(scope_id=scope.scope_id)
                .order_by(MarketPathAcquisitionRecord.slot_at.desc())
                .first()
            )
            if latest is not None:
                if latest.status == "running":
                    if as_utc(latest.lease_expires_at) > now:
                        return None
                    # A killed cycle remains auditable and cannot be called success.
                    self._finish_in_session(latest, now, error="market_acquisition_lease_expired")
                if as_utc(latest.next_due_at) > now or as_utc(latest.slot_at) >= slot:
                    return None
            previous = (
                session.query(MarketPathAcquisitionRecord)
                .filter_by(scope_id=scope.scope_id, status="succeeded")
                .order_by(MarketPathAcquisitionRecord.known_at.desc())
                .first()
            )
            start = end - timedelta(hours=24)
            if previous is not None:
                start = max(
                    start, as_utc(previous.window_end_at) - timedelta(minutes=self.overlap_minutes)
                )
            if start >= end:
                return None
            identity = "macq_" + uuid.uuid4().hex
            receipt = {
                "schema_version": "market-acquisition.v1",
                "scope": scope.model_dump(mode="json"),
                "status": "running",
            }
            row = MarketPathAcquisitionRecord(
                acquisition_id=identity,
                scope_id=scope.scope_id,
                slot_at=slot,
                window_start_at=start,
                window_end_at=end,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                next_due_at=slot + timedelta(seconds=self.poll_seconds),
                status="running",
                consecutive_errors=latest.consecutive_errors if latest is not None else 0,
                health_state=latest.health_state if latest is not None else "active",
                receipt_json=canonical_json(receipt),
                receipt_hash=canonical_hash(receipt),
            )
            session.add(row)
            return row

    def _finish_in_session(
        self, row, now, *, error=None, feature=None, feature_ref=None, feature_hash=None, extra=None
    ):
        row.status = "failed" if error else "succeeded"
        row.completed_at = row.known_at = now
        row.consecutive_errors = row.consecutive_errors + 1 if error else 0
        row.health_state = "degraded" if row.consecutive_errors >= 2 else "active"
        row.error_code = error
        row.feature_ref, row.feature_hash = feature_ref, feature_hash
        payload = {
            "schema_version": "market-acquisition.v1",
            "scope_id": row.scope_id,
            "acquisition_id": row.acquisition_id,
            "status": row.status,
            "started_at": as_utc(row.started_at).isoformat(),
            "known_at": now.isoformat(),
            "window_start_at": as_utc(row.window_start_at).isoformat(),
            "window_end_at": as_utc(row.window_end_at).isoformat(),
            "health_state": row.health_state,
            "consecutive_errors": row.consecutive_errors,
            "error_code": error,
            "feature_ref": feature_ref,
            "feature_hash": feature_hash,
            "manifest_hash": feature.manifest_hash if feature is not None else None,
            **(extra or {}),
        }
        row.receipt_json, row.receipt_hash = canonical_json(payload), canonical_hash(payload)

    async def _refresh(self, scope, claim, *, deadline_at=None) -> bool:
        batch = CaptureBatch(
            claim.acquisition_id,
            self.adapter.adapter_version,
            self.raw_archive,
            self.lineage.record_capture,
        )
        feature = ref = digest = None
        extra, error, pending, primary_failure = {}, None, None, None
        try:
            with capture_scope(batch):
                path = await asyncio.wait_for(
                    self.adapter.path(
                        scope=scope,
                        start_at=as_utc(claim.window_start_at),
                        end_at=as_utc(claim.window_end_at),
                    ),
                    timeout=40,
                )
            component_refs = []
            if self.component_consumer is not None:
                from .market_component_functions import (
                    PathComponentInput,
                    normalize_market_path_component,
                )

                with self.database.session() as session:
                    await self._write(session, deadline_at=deadline_at, lease_expires_at=as_utc(claim.lease_expires_at))
                    known_at = aware_utc(self.clock())
                    payload = PathComponentInput(path=path).model_dump(mode="json")
                    selected, receipt = self.component_consumer(
                        session, kind="source_parser", component_ref="okx.spot_path.v1",
                        scope_id=scope.scope_id, request_id=f"source:{claim.acquisition_id}",
                        known_at=known_at, payload=payload,
                        fallback=normalize_market_path_component,
                    )
                    path = MarketPricePath.model_validate(selected)
                    if path_hashes(scope, path.candles) != (
                        path.semantic_hash, path.manifest_hash,
                    ):
                        raise ValueError("market_component_path_hash_invalid")
                    component_refs.append(component_consumption_reference_in_session(
                        session, receipt, kind="source_parser", component_ref="okx.spot_path.v1",
                        scope_id=scope.scope_id, request_id=f"source:{claim.acquisition_id}",
                        known_at=known_at, payload=payload, value=selected,
                    ))
            if (
                path.scope != scope
                or path.window_start_at != as_utc(claim.window_start_at)
                or path.window_end_at != as_utc(claim.window_end_at)
            ):
                raise ValueError("market_acquisition_path_basis_mismatch")
            extra = {
                "capture_ids": [item["capture_id"] for item in batch.captures],
                "page_count": path.page_count,
                "bar_count": len(path.candles),
                "path_semantic_hash": path.semantic_hash,
                "path_manifest_hash": path.manifest_hash,
                "component_consumptions": component_refs,
            }
            extra["added_bars"] = self.store.ingest(path)
            if path.status != "complete":
                raise ValueError("market_acquisition_path_incomplete")
            computed_at = aware_utc(self.clock())
            end = as_utc(claim.window_end_at)
            bars = self.store.load(
                scope, start_at=end - timedelta(minutes=1471), end_at=end, as_of=computed_at
            )
            if self.component_consumer is None:
                feature = build_market_features(
                    scope, bars, computed_at, stale_after_seconds=self.stale_after_seconds
                )
            else:
                from .market_component_functions import (
                    FeatureComponentInput,
                    build_market_feature_component,
                )

                with self.database.session() as session:
                    await self._write(session, deadline_at=deadline_at, lease_expires_at=as_utc(claim.lease_expires_at))
                    payload = FeatureComponentInput(
                        scope=scope, candles=bars, as_of=computed_at,
                        stale_after_seconds=self.stale_after_seconds,
                    ).model_dump(mode="json")
                    selected, receipt = self.component_consumer(
                        session, kind="feature", component_ref="spot.features.v1",
                        scope_id=scope.scope_id, request_id=f"feature:{claim.acquisition_id}",
                        known_at=computed_at,
                        payload=payload,
                        fallback=build_market_feature_component,
                    )
                    feature = MarketFeatureSnapshot.model_validate(selected)
                    if feature.scope != scope or feature.as_of != computed_at:
                        raise ValueError("market_component_feature_basis_invalid")
                    basis = build_market_features(
                        scope, bars, computed_at, stale_after_seconds=self.stale_after_seconds,
                    )
                    for field in ("semantic_hash", "manifest_hash", "data_as_of", "known_at",
                                  "latest_close_at", "last_price", "status"):
                        if getattr(feature, field) != getattr(basis, field):
                            raise ValueError("market_component_feature_basis_invalid")
                    if any(value is not None and not value.is_finite()
                           for value in (feature.return_30m, feature.return_24h,
                                         feature.realized_vol_30m, feature.volume_change_30m,
                                         feature.spread_fraction)):
                        raise ValueError("market_component_feature_value_invalid")
                    component_refs.append(component_consumption_reference_in_session(
                        session, receipt, kind="feature", component_ref="spot.features.v1",
                        scope_id=scope.scope_id, request_id=f"feature:{claim.acquisition_id}",
                        known_at=computed_at, payload=payload, value=selected,
                    ))
            ref, digest = archive_feature(
                canonical_json(feature.model_dump(mode="json")), self.feature_root
            )
        except _MarketInitialBeginPending as exc:
            pending = primary_failure = exc
            error = "market_acquisition_initial_begin_busy"
            extra["capture_ids"] = [item["capture_id"] for item in batch.captures]
        except asyncio.CancelledError as cancelled:
            try:
                await self._finish(
                    claim.acquisition_id, deadline_at=deadline_at,
                    error="market_acquisition_cancelled",
                    extra={"capture_ids": [item["capture_id"] for item in batch.captures]},
                )
            except BaseException as cleanup_error:
                cancelled.add_note("market_acquisition_cancelled_receipt_unconfirmed")
                raise cancelled from cleanup_error
            raise
        except Exception as exc:
            primary_failure = exc
            # Exception text can contain URLs or paths. Persist only owned bounded codes.
            code = getattr(exc, "code", None) or str(exc)
            error = (
                code
                if isinstance(code, str)
                and code.startswith(("market_", "raw_capture_", "component_"))
                and len(code) <= 128
                and all(ch.isalnum() or ch == "_" for ch in code)
                else "market_acquisition_timeout"
                if isinstance(exc, TimeoutError)
                else "market_acquisition_failed"
            )
            extra["capture_ids"] = [item["capture_id"] for item in batch.captures]
        try:
            finished = await self._finish(
                claim.acquisition_id, deadline_at=deadline_at,
                error=error, feature=feature, feature_ref=ref,
                feature_hash=digest, extra=extra,
            )
        except BaseException as finish_pending:
            if primary_failure is not None and primary_failure is not finish_pending:
                existing_cause = finish_pending.__cause__
                if (existing_cause is not None
                        and existing_cause is not finish_pending
                        and existing_cause is not primary_failure):
                    raise finish_pending from BaseExceptionGroup(
                        "market_acquisition_finish_and_primary_failed",
                        [existing_cause, primary_failure],
                    )
                raise finish_pending from primary_failure
            raise
        if pending is not None:
            raise pending
        return finished

    async def _finish(self, acquisition_id, *, deadline_at=None, **details) -> bool:
        with self.database.session() as session:
            await self._write(session, deadline_at=deadline_at)
            now = aware_utc(self.clock())
            row = session.get(MarketPathAcquisitionRecord, acquisition_id)
            if row is None or row.status != "running":
                return False
            if now >= as_utc(row.lease_expires_at):
                details = {"error": "market_acquisition_lease_expired"}
            self._finish_in_session(row, now, **details)
            return row.status == "succeeded"

    async def refresh_due(self, *, deadline_at=None) -> int:
        if not self.enabled:
            return 0
        loop = asyncio.get_running_loop()
        stop = deadline_at if deadline_at is not None else loop.time() + 90
        slot = aware_utc(self.clock()).replace(second=0, microsecond=0)
        successful, pendings = 0, []
        for scope in self.scopes.values():
            if loop.time() >= stop:
                raise TimeoutError("market_acquisition_channel_deadline_elapsed")
            try:
                claim = await self._claim(scope, deadline_at=stop, slot_at=slot)
                if claim is not None:
                    successful += int(await self._refresh(scope, claim, deadline_at=stop))
            except _MarketInitialBeginPending as pending:
                pendings.append(pending)
        if pendings:
            pending = pendings[0]
            cause = pending.__cause__
            if isinstance(cause, BaseExceptionGroup):
                other_causes = tuple(
                    item for item in cause.exceptions
                    if item is not pending.original_error
                )
                cause = (cause.derive(other_causes) if len(other_causes) > 1
                         else other_causes[0] if other_causes else None)
            # Preserve genuine SQLite BEGIN diagnostics, even if another scope
            # succeeded. A contended all-zero batch is never channel recovery.
            if cause is not None and cause is not pending.original_error:
                raise pending.original_error from cause
            raise pending.original_error
        return successful

    def _empty(self, scope_id, clock, state, reason, receipt=None):
        payload = {
            "scope_id": scope_id,
            "as_of": clock.isoformat(),
            "state": state,
            "reason": reason,
            "receipt": receipt,
        }
        digest = canonical_hash(payload)
        return RiskMonitorInput(
            scope_id=scope_id,
            as_of=clock,
            data_as_of=None,
            window_seconds=1800,
            close_return=None,
            data_state=state,
            reason=reason,
            input_ref="risk-data://unavailable/" + digest,
            input_hash=digest,
            manifest_hash=digest,
        )

    async def snapshot(self, scope_id: str, *, as_of: datetime) -> RiskMonitorInput:
        clock = aware_utc(as_of)
        if not self.enabled:
            return self._empty(scope_id, clock, "unknown", "risk_data_disabled")
        scope = self.scopes.get(scope_id)
        if scope is None:
            return self._empty(scope_id, clock, "error", "risk_data_scope_not_enabled")
        with self.database.session() as session:
            query = session.query(MarketPathAcquisitionRecord).filter(
                MarketPathAcquisitionRecord.scope_id == scope_id,
                MarketPathAcquisitionRecord.known_at <= clock,
            )
            latest = query.order_by(
                MarketPathAcquisitionRecord.known_at.desc(),
                MarketPathAcquisitionRecord.slot_at.desc(),
            ).first()
            success = (
                query.filter_by(status="succeeded")
                .order_by(
                    MarketPathAcquisitionRecord.known_at.desc(),
                    MarketPathAcquisitionRecord.slot_at.desc(),
                )
                .first()
            )
        if latest is not None and latest.health_state == "degraded":
            return self._empty(
                scope_id,
                clock,
                "error",
                latest.error_code or "market_acquisition_degraded",
                latest.receipt_hash,
            )
        if success is None:
            return self._empty(scope_id, clock, "unknown", "no_committed_market_snapshot")
        try:
            receipt = json.loads(success.receipt_json)
            if (
                canonical_hash(receipt) != success.receipt_hash
                or not success.feature_ref
                or not success.feature_hash
            ):
                raise ValueError("market_acquisition_receipt_corrupt")
            path = Path(success.feature_ref).resolve()
            if not path.is_relative_to(self.feature_root.resolve()):
                raise ValueError("market_feature_ref_invalid")
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != success.feature_hash:
                raise ValueError("market_feature_archive_corrupt")
            feature = MarketFeatureSnapshot.model_validate_json(body)
            if (
                feature.scope != scope
                or feature.as_of > clock
                or feature.known_at is None
                or feature.known_at > clock
                or feature.latest_close_at is None
            ):
                raise ValueError("market_feature_basis_corrupt")
            bars = self.store.load(
                scope,
                start_at=as_utc(success.window_end_at) - timedelta(minutes=1471),
                end_at=as_utc(success.window_end_at),
                as_of=feature.as_of,
            )
            expected = build_market_features(
                scope, bars, feature.as_of, stale_after_seconds=self.stale_after_seconds
            )
            if (
                expected.model_dump(mode="json") != feature.model_dump(mode="json")
                or receipt.get("manifest_hash") != feature.manifest_hash
            ):
                raise ValueError("market_feature_receipt_mismatch")
        except Exception as exc:
            reason = (
                str(exc)
                if str(exc).startswith(("market_", "raw_capture_")) and len(str(exc)) <= 128
                else "market_snapshot_original_unavailable"
            )
            return self._empty(scope_id, clock, "error", reason, success.receipt_hash)
        stale = (clock - feature.latest_close_at).total_seconds() > self.stale_after_seconds
        state = "stale" if stale else feature.status
        reason = (
            "latest_closed_bar_stale"
            if stale
            else next(
                (
                    item
                    for item in feature.reasons
                    if item != "trade_candles_do_not_supply_bid_ask_spread"
                ),
                None,
            )
        )
        return RiskMonitorInput(
            scope_id=scope_id,
            as_of=clock,
            data_as_of=feature.latest_close_at,
            window_seconds=1800,
            close_return=feature.return_30m if state == "fresh" else None,
            data_state=state,
            reason=reason,
            input_ref=success.feature_ref,
            input_hash=success.feature_hash,
            manifest_hash=feature.manifest_hash,
        )


# Explicit name for composition; both entry points share one durable service.
RiskSnapshotProvider = MarketRiskDataService
