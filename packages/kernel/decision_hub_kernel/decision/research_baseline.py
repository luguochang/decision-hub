"""Unique bounded acquisition plan, derived from frozen server inputs, never LLM text."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityInvocation,
    ResearchSessionRequest,
)

from ..application.research_method import canonical_recipe
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_baseline import BaselinePlan, BaselineSlot
from ..ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF
from .research_loop import research_acquisition_close_at

BASELINE_PROFILE = "decision-research.web.baseline.v1"
PROTOCOL = {
    "schema_version": "research-baseline-protocol.v1",
    "profile_ref": BASELINE_PROFILE,
    "waves": [[0, 1], [2], [3, 4]],
    "max_parallel": 2,
    "attempts_per_slot": 1,
    "historical_days": 90,
    "minimum_daily_bars": 60,
    "quality": "existing_assess_evidence_sufficiency",
    "origin": "server_baseline",
    "skill_execution_verified": False,
    "model_understanding_verified": False,
}
PROTOCOL_HASH = canonical_hash(PROTOCOL)
DAILY_FIELDS = (
    "period_close",
    "sma_20",
    "sma_60",
    "return_20_pct",
    "realized_vol_20_pct",
    "volume_ratio_20",
    "rolling_high_20",
    "rolling_low_20",
    "row_count",
)


def baseline_profile_for(request: ResearchSessionRequest, original: str) -> str:
    hard = {
        item.requirement_id for item in request.evidence_requirements if item.importance == "hard"
    }
    return (
        BASELINE_PROFILE
        if (
            {"crypto_spot_confirmation", "market_regime_context"} <= hard
            and request.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF
            and request.execution_mode == "live"
            and request.research_mode == "current_decision"
        )
        else original
    )


def _route_available(
    catalog: Mapping[str, Any], capability: str, symbols: list[str], fields: list[str]
) -> bool:
    for entry in catalog.get("capabilities", []):
        if entry.get("capability_id") != capability:
            continue
        for route in entry.get("routes", []):
            if "live" not in route.get("modes", []) or route.get("requires_event_window"):
                continue
            if route.get("symbols") is None or not set(symbols) <= set(route["symbols"]):
                continue
            symbol_fields = route.get("symbol_fields")
            available = (
                set.intersection(*(set(symbol_fields.get(symbol, [])) for symbol in symbols))
                if symbol_fields is not None
                else set(route.get("fields") or [])
            )
            if set(fields) <= available:
                return True
    return False


def compile_baseline_plan(
    *,
    request: ResearchSessionRequest,
    session_id: str,
    generation: int,
    request_hash: str,
    execution_request_hash: str,
    method_hash: str,
    profile_ref: str,
    instrument: str,
    catalog: Mapping[str, Any],
    planned_at: datetime,
) -> BaselinePlan:
    """Pure compiler; the application must authenticate every argument first."""
    now = aware_utc(planned_at)
    if (
        profile_ref != BASELINE_PROFILE
        or generation != 1
        or request.current_round != 1
        or request.execution_mode != "live"
        or request.research_mode != "current_decision"
        or request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
        or instrument not in {"BTC-USDT", "ETH-USDT"}
    ):
        raise ValueError("research_baseline_method_not_opted_in")
    if request.execution_budget.max_tool_calls < 5:
        raise ValueError("research_baseline_original_budget_insufficient")
    close = research_acquisition_close_at(request)
    if now >= close:
        raise ValueError("research_baseline_acquisition_closed")
    requirements = {item.requirement_id: item for item in request.evidence_requirements}
    if any(
        requirements.get(key) is None or requirements[key].importance != "hard"
        for key in ("crypto_spot_confirmation", "market_regime_context")
    ):
        raise ValueError("research_baseline_hard_policy_missing")
    daily_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    specs = (
        (
            "hard",
            "spot_market_facts",
            "crypto_spot_confirmation",
            [instrument],
            ["spot_price", "spot_volume", "return_30m", "return_24h", "realized_vol_30m", "path"],
        ),
        (
            "hard",
            "market.crypto_derivatives",
            "crypto_spot_confirmation",
            [instrument],
            ["spot_price", "spot_volume"],
        ),
        (
            "hard",
            "data.historical",
            "market_regime_context",
            [f"crypto.{instrument[:3].lower()}_usdt.ohlcv_1d"],
            list(DAILY_FIELDS),
        ),
        (
            "soft",
            "market.cross_asset",
            "macro_transmission",
            ["US10Y", "DXY"],
            ["level", "change_5d"],
        ),
        (
            "soft",
            "market.crypto_derivatives",
            "derivatives_crowding",
            [instrument + "-SWAP"],
            ["funding_rate", "open_interest", "open_interest_delta", "basis"],
        ),
    )
    binding = {
        "protocol_hash": PROTOCOL_HASH,
        "run_id": request.run_id,
        "generation": generation,
        "request_hash": request_hash,
        "execution_request_hash": execution_request_hash,
        "method_hash": method_hash,
        "catalog_hash": canonical_hash(catalog),
        "planned_at": now.isoformat(),
    }
    slots = []
    for ordinal, (phase, capability, requirement_id, symbols, fields) in enumerate(specs):
        requirement = requirements.get(requirement_id)
        missing = None
        if (
            requirement is None
            or capability not in request.allowed_capabilities
            or capability
            not in (*requirement.preferred_capabilities, *requirement.allowed_fallbacks)
        ):
            missing = "research_baseline_frozen_capability_unavailable"
        elif not _route_available(catalog, capability, symbols, fields):
            missing = "research_baseline_catalog_route_unavailable"
        invocation = ResearchCapabilityInvocation(
            schema_version="research-capability-invocation.v1",
            request_id="baseline_" + canonical_hash([binding, ordinal]),
            capability_id=capability,
            requirement_id=requirement_id,
            query=f"Frozen baseline {ordinal}: {requirement_id}; server-origin, no trade authority",
            target_url=None,
            symbols=symbols,
            fields=fields,
            allowed_domains=[],
            max_results=10,
            max_cost_usd=None,
            research_session_id=session_id,
            window_start_at=daily_end - timedelta(days=90) if ordinal == 2 else None,
            window_end_at=daily_end if ordinal == 2 else None,
        )
        slots.append(
            BaselineSlot(
                ordinal=ordinal, phase=phase, invocation=invocation, unavailable_reason=missing
            )
        )
    body = {
        "schema_version": "research-baseline-plan.v1",
        **binding,
        "research_session_id": session_id,
        "catalog_json": canonical_recipe(catalog),
        "instrument": instrument,
        "acquisition_close_at": close.isoformat(),
        "deadline_at": request.deadline_at.isoformat(),
        "max_parallel": 2,
        "slots": [slot.model_dump(mode="json") for slot in slots],
    }
    return BaselinePlan.model_validate({**body, "plan_hash": canonical_hash(body)})


async def run_baseline_waves(
    plan: BaselinePlan,
    invoke: Callable[[BaselineSlot], Awaitable[Any]],
    checkpoint: Callable[[], None],
) -> tuple[Any, ...]:
    """Join every in-flight owner before cancellation escapes; no detached writers."""
    output = []
    for ordinals in PROTOCOL["waves"]:
        checkpoint()
        tasks = [asyncio.create_task(invoke(plan.slots[index])) for index in ordinals]
        joined = asyncio.gather(*tasks, return_exceptions=True)
        try:
            values = await asyncio.shield(joined)
        except asyncio.CancelledError:
            # Gateway owns its original bounded operation. Await terminal cleanup
            # rather than releasing ownership while a background admission writes.
            await asyncio.shield(joined)
            raise
        error = next((value for value in values if isinstance(value, BaseException)), None)
        if error is not None:
            raise error
        output.extend(values)
    return tuple(output)
