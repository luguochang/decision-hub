"""Finite private SQLite comparison against frozen formal18 summary source.

Transport and prices are explicitly constructed controls; no provider or HTTP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from packages.kernel.decision_hub_kernel.application import market_features
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.decision import path_risk
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.query_views.market_risk import MarketRiskQuery
from tests.market_risk.test_market_data import SCOPE
from tests.market_risk.test_market_data import runtime as market_fixture

ROOT = Path(__file__).resolve().parents[2]
BEFORE = ROOT / (
    "artifacts/market-risk-implementation-20261007/B2/summary-metrics19/before/"
    "packages/query_views/market_risk.py"
)
BEFORE_SHA = "1419ad8a1e8be59e2f546de68414bb7f995a8af976b92c1887cd2a81d8a14fa8"


def before_query():
    raw = BEFORE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BEFORE_SHA
    module = ModuleType("formal18_risk_summary_control")
    exec(compile(raw, str(BEFORE), "exec"), module.__dict__)
    return module.MarketRiskQuery


@pytest.fixture
def originals(tmp_path, monkeypatch):
    for database, existing, transport, now in market_fixture.__wrapped__(tmp_path, monkeypatch):
        service = MarketRiskDataService(
            database,
            existing.raw_archive,
            existing.feature_root,
            adapter=existing.adapter,
            scopes=(SCOPE, spot_scope("ETH-USDT")),
            clock=lambda now=now: now[0],
            enabled=True,
        )
        assert asyncio.run(service.refresh_due()) == 2
        # Produce the additional observed endpoints, retaining real accepted
        # clocks and immutable private originals. This is not historical PIT.
        now[0] += timedelta(minutes=31)
        transport.bump = Decimal("2")
        assert asyncio.run(service.refresh_due()) == 2
        # The current incremental adapter needs one extra page per asset.
        assert len(transport.calls) == 12
        yield database, service, now


def query_pair(originals):
    database, service, now = originals
    old = before_query()(
        database, service.raw_archive.root.parent, enabled=True, clock=lambda: now[0]
    )
    new = MarketRiskQuery(
        database, service.raw_archive.root.parent, enabled=True, clock=lambda: now[0]
    )
    # Both readers use the same actual private original store and SHA checks.
    old.store = new.store = service.store
    return old, new


def test_complete_projection_matches_formal18_without_unused_aggregate_hashes(
    originals, monkeypatch, request
):
    old, new = query_pair(originals)
    _, _, now = originals
    counts = {"old": 0, "new": 0}
    phase = ["old"]
    original_hashes = path_risk.path_hashes

    def counted(*args, **kwargs):
        counts[phase[0]] += 1
        return original_hashes(*args, **kwargs)

    monkeypatch.setattr(market_features, "path_hashes", counted)
    monkeypatch.setattr(path_risk, "path_hashes", counted)
    for stale in (False, True):
        if stale:
            now[0] += timedelta(minutes=4)
        for scope in (SCOPE, spot_scope("ETH-USDT")):
            phase[0] = "old"
            previous = old.summary(scope.scope_id)
            phase[0] = "new"
            current = new.summary(scope.scope_id)
            assert current.model_dump_json() == previous.model_dump_json()
            assert current.quote is not None and current.quote.change_24h_pct == 2
            assert current.window is not None and current.window.samples == 1440
            assert current.window.max_adverse_move_pct is not None
            assert current.data_status == ("stale" if stale else "fresh")
    assert counts == {"old": 8, "new": 0}
    request.node.user_properties.append(("full_path_hash_calls", json.dumps(counts)))


@pytest.mark.parametrize("mutation", ["missing_bar", "missing_anchor", "future", "scope", "tamper"])
def test_missing_future_scope_and_fresh_tamper_remain_closed(originals, mutation):
    database, service, now = originals
    old, new = query_pair(originals)
    end = now[0].replace(second=0, microsecond=0)
    # Warm the pure parse hints before destructive private controls.
    assert new.summary(SCOPE.scope_id).data_status == "fresh"
    if mutation in {"missing_bar", "missing_anchor"}:
        at = end - (
            timedelta(hours=24) if mutation == "missing_anchor" else timedelta(minutes=10)
        )
        with database.session() as session:
            session.query(MarketCandleRecord).filter(
                MarketCandleRecord.scope_id == SCOPE.scope_id,
                MarketCandleRecord.open_at == at,
            ).delete()
    elif mutation in {"future", "scope"}:
        with database.session() as session:
            row = (
                session.query(MarketCandleRecord)
                .filter(
                    MarketCandleRecord.scope_id == SCOPE.scope_id,
                    MarketCandleRecord.open_at == end - timedelta(minutes=1),
                )
                .one()
            )
            if mutation == "scope":
                row.instrument = "ETH-USDT"
            else:
                known = now[0] + timedelta(minutes=2)
                row.known_at = known
                body = json.loads(row.candle_json)
                body["accepted_at"] = known.isoformat()
                row.candle_json = json.dumps(body)
    else:
        with database.session() as session:
            row = (
                session.query(MarketCandleRecord)
                .filter(
                    MarketCandleRecord.scope_id == SCOPE.scope_id,
                    MarketCandleRecord.open_at == end - timedelta(minutes=1),
                )
                .one()
            )
            digest = row.capture_hash
        path = service.raw_archive.root / digest[:2] / digest
        raw, stat = path.read_bytes(), path.stat()
        changed = raw.replace(b'"102"', b'"103"', 1)
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    current, previous = new.summary(SCOPE.scope_id), old.summary(SCOPE.scope_id)
    assert current.model_dump_json() == previous.model_dump_json()
    if mutation in {"missing_bar", "missing_anchor"}:
        assert current.window is not None and current.window.samples == 1439
        assert current.window.max_adverse_move_pct is None
        if mutation == "missing_bar":
            assert current.data_status == "missing"  # The last 31 closes are incomplete.
    elif mutation == "future":
        assert current.quote is not None and current.quote.quoted_at == end - timedelta(minutes=1)
        assert current.quote.known_at <= now[0]
    else:
        assert current.data_status == "invalid"
        assert current.quote is None and current.window is None
