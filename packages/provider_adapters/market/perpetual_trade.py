"""Explicit SWAP trade observations through the original public capture/decoder."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import Any, Protocol, cast

from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal, decode_json
from packages.kernel.decision_hub_kernel.ports.okx_perpetual_original import (
    Kind,
    decode_public_original,
)
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE, RawBodyArchive


class PublicOriginalCollector(Protocol):
    async def __call__(
        self,
        *,
        inst_id: str,
        kind: Kind,
        archive: RawBodyArchive,
        persist: Callable[[dict[str, Any]], None],
        capture_sink: list[dict[str, Any]] | None,
    ) -> dict[str, Any]: ...


class PublicCaptureContext(Protocol):
    archive: RawBodyArchive
    persist: Callable[[dict[str, Any]], None]
    captures: list[dict[str, Any]]


async def collect_perpetual_trade(
    inst_id: str,
    *,
    collector: PublicOriginalCollector,
) -> dict[str, Any]:
    parent = cast(PublicCaptureContext | None, CURRENT_CAPTURE.get())
    if parent is None:
        raise ValueError("perp_research_capture_context_required")
    entry = await collector(
        inst_id=inst_id,
        kind="ticker",
        archive=parent.archive,
        persist=parent.persist,
        capture_sink=parent.captures,
    )
    capture = CaptureOriginal(
        **{field.name: entry[field.name] for field in fields(CaptureOriginal)}
    )
    if capture.body_ref is None:
        raise ValueError("perp_research_raw_original_missing")
    raw = parent.archive.read(capture.body_ref)
    original = decode_public_original("ticker", inst_id, capture, raw)
    row = decode_json(raw)["data"][0]
    return {
        "code": "0",
        "data": [
            dict(
                instType="SWAP",
                instId=inst_id,
                ts=row["ts"],
                last=original.fields["last"],
                bidPx=original.fields["bid"],
                askPx=original.fields["ask"],
            )
        ],
        "perpetual_original": dict(
            capture_id=capture.capture_id,
            source_ref=original.source_ref.model_dump(mode="json"),
            body_ref=capture.body_ref,
            body_hash=capture.body_hash,
            instrument_id="okx:" + inst_id,
            price_basis="trade",
            provider_observed_at=(
                original.observed_at.isoformat() if original.observed_at is not None else None
            ),
            received_at=capture.completed_at.isoformat(),
            recorded_at=capture.recorded_at.isoformat(),
            executable_quote=False,
        ),
    }
