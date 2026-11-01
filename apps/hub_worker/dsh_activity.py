"""Collect current Host activity only for an existing automatic admission need."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRequest,
    DshHostActivitySnapshot,
    activity_targets_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.orchestration.owned_work import HeavyWorkAdmission
from packages.runtime_adapters.dsh_runtime.activity_transport import (
    DshActivityTransportConfig,
    activity_channel_url,
    validate_activity_scope,
)
from packages.runtime_adapters.dsh_runtime.web_host_client import (
    DshActivityHostClient,
    DshWebHostClient,
)

log = logging.getLogger(__name__)
ACTIVITY_TIME_BUDGET_SECONDS = 8.0


class DshActivityCollector:
    def __init__(
        self, database: Database, host: DshActivityHostClient, config: DshActivityTransportConfig,
        *, sender: httpx.AsyncClient | None = None, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database, self.host, self.config = database, host, config
        if activity_channel_url(host.config.base_url) != activity_channel_url(config.host.base_url):
            raise ValueError("dsh_activity_host_configuration_mismatch")
        self.clock = clock or (lambda: datetime.now(UTC))
        self._stage = "targets"
        self._inflight: asyncio.Task[int] | None = None
        self._targets_work = HeavyWorkAdmission()
        self._closed = False
        self._target_count = 0
        self._last_target_hash: str | None = None
        self._owns_sender = sender is None
        self.sender = sender or httpx.AsyncClient(timeout=8, trust_env=False)

    def _targets(self) -> DshHostActivityRequest | None:
        with self.database.session() as session:
            return activity_targets_in_session(session, now=self.clock(), limit=64)

    async def collect_once(self) -> int:
        if self._closed:
            return 0
        task = self._inflight
        if task is None:
            task = asyncio.create_task(self._collect_owned(), name="dsh-activity-owned-observation")
            self._inflight = task
        try:
            # Eight seconds observes one existing operation; it never cancels
            # healthy Host work or replays a pending POST/API ingestion.
            return await asyncio.wait_for(asyncio.shield(task), ACTIVITY_TIME_BUDGET_SECONDS)
        except TimeoutError:
            if task.done():
                # A real operation TimeoutError must retain its failure path.
                return task.result()
            log.info(
                "dsh_activity_observation_waiting stage=%s targets=%s",
                self._stage, self._target_count,
            )
            return 0
        finally:
            if task.done() and self._inflight is task:
                self._inflight = None

    async def _collect_owned(self) -> int:
        self._stage = "targets"
        request = await self._targets_work.run_thread(self._targets)
        if request is None or not request.records:
            return 0
        # This operation owns these exact targets. New targets belong only to a
        # subsequent fresh task; a pending snapshot never claims their proof.
        raw_targets = request.model_dump_json()
        request = DshHostActivityRequest.model_validate_json(raw_targets)
        target_hash = hashlib.sha256(raw_targets.encode()).hexdigest()
        if self._last_target_hash is not None and target_hash != self._last_target_hash:
            log.info("dsh_activity_targets_changed targets=%s", len(request.records))
        self._last_target_hash = target_hash
        self._target_count = len(request.records)
        try:
            return await self._collect_request(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "dsh_activity_observation_pending stage=%s targets=%s error_class=%s",
                self._stage, len(request.records), type(exc).__name__,
            )
            # Only the API's own authenticated GET may invalidate its current
            # head. This carries no caller-asserted scope or inactive record.
            try:
                async with asyncio.timeout(4):
                    url = (
                        f"{activity_channel_url(self.config.hub_base_url)}"
                        "/v1/dsh/activity-scope-probe"
                    )
                    await self.sender.post(
                        url,
                        headers={"X-Decision-Hub-Bridge-Key": self.config.callback_key},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            raise

    async def _collect_request(self, request: DshHostActivityRequest) -> int:
        # All remote calls happen after the read Session closes. API ingestion
        # performs the second physical Link/lease check in its own writer.
        original_targets = request.model_dump_json()
        self._stage = "host_scope_before"
        before = validate_activity_scope(
            await self.host.activity_scope(), self.config, now=self.clock(),
        )
        self._stage = "host_snapshot"
        if request.model_dump_json() != original_targets:
            raise ValueError("dsh_activity_request_targets_changed")
        response = await self.host.activity_snapshot(request)
        self._stage = "snapshot_validation"
        source_url = f"{self.config.host.base_url.rstrip('/')}/decision-hub/v1/current-activity"
        if response.url != source_url:
            raise ValueError("dsh_activity_channel_changed")
        snapshot = DshHostActivitySnapshot.model_validate_json(response.body)
        self._stage = "host_scope_after"
        after = validate_activity_scope(
            await self.host.activity_scope(), self.config, now=self.clock(),
        )
        self._stage = "snapshot_binding"
        now = self.clock()
        if before.scope != snapshot.scope or after.scope != snapshot.scope:
            raise ValueError("dsh_activity_epoch_changed")
        if not before.observed_at <= snapshot.observed_at <= after.observed_at:
            raise ValueError("dsh_activity_snapshot_time_invalid")
        if not snapshot.observed_at <= now < snapshot.expires_at:
            raise ValueError("dsh_activity_snapshot_expired")
        if (snapshot.expires_at - snapshot.observed_at).total_seconds() > 30:
            raise ValueError("dsh_activity_snapshot_ttl_invalid")
        if len(snapshot.records) != len(request.records):
            raise ValueError("dsh_activity_snapshot_targets_changed")
        expected = {record.run_id: record for record in request.records}
        seen: set[str] = set()
        for record in snapshot.records:
            original = expected.get(record.run_id)
            if original is None or record.run_id in seen or any(
                getattr(record, key) != getattr(original, key)
                for key in ("run_id", "study_id", "dsh_session_id", "generation",
                            "request_hash", "link_fingerprint")
            ):
                raise ValueError("dsh_activity_snapshot_binding_changed")
            seen.add(record.run_id)
        if request.model_dump_json() != original_targets:
            raise ValueError("dsh_activity_request_targets_changed")
        url = f"{activity_channel_url(self.config.hub_base_url)}/v1/dsh/activity-snapshots"
        self._stage = "api_ingestion"
        receipt = await self.sender.post(
            url, content=response.body,
            headers={"Content-Type": "application/json",
                     "X-Decision-Hub-Bridge-Key": self.config.callback_key},
        )
        self._stage = "api_receipt"
        if receipt.status_code != 200 or str(receipt.url) != url:
            raise ValueError("dsh_activity_ingestion_unconfirmed")
        value = receipt.json().get("recorded_count")
        if type(value) is not int or value < 0:
            raise ValueError("dsh_activity_ingestion_receipt_invalid")
        self._stage = "recorded"
        return value

    async def run_forever(self, *, interval_seconds: float = 10) -> None:
        while True:
            try:
                await self.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Remote failures were logged at their exact bounded stage.
                # Target selection errors happen before the official channel.
                if self._stage == "targets":
                    log.warning(
                        "dsh_activity_observation_pending stage=targets error_class=%s",
                        type(exc).__name__,
                    )
            await asyncio.sleep(interval_seconds)

    async def close(self) -> None:
        self._closed = True
        task = self._inflight
        cancelled = False
        if task is not None:
            task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if not task.done():
                        cancelled = True
                except BaseException:
                    if not task.done():
                        raise
                    break
            try:
                task.result()
            except BaseException:
                pass
            self._inflight = None
        if self._owns_sender:
            await self.sender.aclose()
        if cancelled:
            raise asyncio.CancelledError


def build_dsh_activity_collector(
    database: Database, runtime: object,
) -> DshActivityCollector | None:
    client = getattr(runtime, "client", None)
    if not isinstance(client, DshWebHostClient):
        return None
    try:
        config = DshActivityTransportConfig.from_env()
        return DshActivityCollector(database, client, config)
    except Exception:
        log.warning("dsh_activity_observation_pending error_class=configuration_unavailable")
        return None
