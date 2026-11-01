"""Server-built first-seed input from actual closed MarketPath originals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    FeatureComponentInput,
)

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, spot_scope

from .research_skill_package import encoded, sha


@dataclass(frozen=True)
class SkillMarketInput:
    workflow_input_json: bytes
    originals: tuple[tuple[str, str], ...]
    known_through: object


def read_market_input_in_session(store, session, *, scope_id, cutoff_at):
    """Reuse production bar/capture/hash/PIT authentication in the same snapshot.

    Cutoff is authenticated Gateway completed_at, not current time or a future
    Task deadline. The 25-hour closed path leaves return_24h genuinely unknown
    when its original history is missing. Never synthesize or fill missing bars.
    """
    if scope_id not in {"crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m"}:
        raise ValueError("skill_market_scope_unsupported")
    scope, cutoff = spot_scope(scope_id.split(":")[3]), aware_utc(cutoff_at)
    originals = []
    bars = store.load_in_session(
        session,
        scope,
        start_at=cutoff - timedelta(hours=25),
        end_at=cutoff,
        as_of=cutoff,
        on_original=lambda ref, digest: originals.append((ref, digest)),
    )
    if not bars:
        raise ValueError("skill_market_closed_path_unavailable")
    payload = FeatureComponentInput(
        scope=scope, candles=bars, as_of=cutoff, stale_after_seconds=180
    )
    return SkillMarketInput(
        encoded({"feature_input": payload.model_dump(mode="json")}),
        tuple(sorted(set(originals))),
        max(bar.known_at for bar in bars),
    )


def verify_market_input_bytes(store, material):
    for ref, digest in material.originals:
        if sha(store.archive.read(ref)) != digest:
            raise ValueError("skill_market_original_changed")
