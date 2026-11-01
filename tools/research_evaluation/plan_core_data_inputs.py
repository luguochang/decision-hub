from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.provider_adapters.research import CryptoMacroFactPack
from tools.research_evaluation.build_core_replay_dataset import (
    KALSHI_EVENTS,
    TARGET_POLICIES,
    requested_data_slots,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"
EVALUATIONS = PACK_ROOT / "evaluations"


def _dataset_root(name: str) -> Path:
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("core_data_input_dataset_invalid")
    root = EVALUATIONS / name
    if not root.is_dir():
        raise ValueError("core_data_input_dataset_missing")
    return root


def plan(source_name: str, *, source_timezone: str = "America/Chicago") -> dict[str, object]:
    """Create a deterministic procurement contract from a frozen source split.

    This reads only case manifests. It does not contact providers and does not
    alter any evaluation directory, so it can be run before licensed files are
    available.
    """

    source_root = _dataset_root(source_name)
    try:
        ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("core_data_input_timezone_invalid") from exc
    manifest_path = source_root / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        source_manifest = cast(
            dict[str, Any],
            json.loads(manifest_bytes),
        )
    except (OSError, ValueError) as exc:
        raise ValueError("core_data_input_manifest_invalid") from exc

    fact_pack = CryptoMacroFactPack.from_pack(PACK_ROOT)
    cases: list[dict[str, object]] = []
    by_requirement: dict[str, dict[str, int]] = {}
    for fixture_ref in source_manifest.get("fixture_refs", []):
        reference = Path(str(fixture_ref))
        if reference.is_absolute() or ".." in reference.parts:
            raise ValueError("core_data_input_fixture_ref_invalid")
        try:
            case = cast(
                dict[str, Any],
                json.loads((source_root / reference).read_text(encoding="utf-8")),
            )
            cutoff_at = datetime.fromisoformat(str(case["cutoff_at"])).astimezone(UTC)
            event_at = cutoff_at - timedelta(minutes=5)
            event_family = str(case["event_family"])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("core_data_input_case_invalid") from exc

        policies = {
            item.requirement_id: item
            for item in fact_pack.contract_requirements_for_event(event_family)
        }
        requirements: dict[str, dict[str, object]] = {}
        for requirement_id in TARGET_POLICIES:
            importance = str(policies[requirement_id].importance)
            requirements[requirement_id] = {
                "importance": importance,
                "hard_data_required": importance == "hard",
            }
            counts = by_requirement.setdefault(requirement_id, {"cases": 0, "hard_cases": 0})
            counts["cases"] += 1
            if importance == "hard":
                counts["hard_cases"] += 1

        case_id = str(case["case_id"])
        event_ticker = KALSHI_EVENTS.get(case_id)
        expectation_sources: list[dict[str, str]] = []
        if event_ticker is not None:
            expectation_sources.append(
                {"provider": "kalshi", "event_ticker": event_ticker, "role": "preferred"}
            )
        expectation_sources.append(
            {
                "provider": "portara_us2y",
                "instrument": "US2Y",
                "role": "fallback",
            }
        )
        cases.append(
            {
                "case_id": case_id,
                "event_family": event_family,
                "cutoff_at": cutoff_at.isoformat(),
                "event_at": event_at.isoformat(),
                "requirements": requirements,
                "expectation_sources": expectation_sources,
                "requested_slots": requested_data_slots(event_at),
            }
        )

    return {
        "schema_version": "core-data-input-plan.v1",
        "source_dataset": str(source_manifest.get("dataset_id", source_name)),
        "source_split": str(source_manifest.get("split", "unknown")),
        "source_manifest_hash": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_fixture_hashes": dict(
            cast(dict[str, str], source_manifest.get("fixture_hashes", {}))
        ),
        "source_timezone": source_timezone,
        "delivery_contract": {
            "macro": {
                "US2Y": "owner-authorized Portara/CQG USY02Y ASCII",
                "DXY": "owner-authorized Portara/CQG DX/DXA ASCII",
            },
            "cross_asset": {"ES": "owner-authorized Portara/CQG EP ASCII"},
            "columns": ["Date", "Time", "Open", "High", "Low", "Close", "Volume"],
            "maximum_event_bar_lag_seconds": 90,
            "maximum_expectation_baseline_lag_seconds": 259200,
        },
        "summary": {
            "case_count": len(cases),
            "by_requirement": by_requirement,
            "hard_data_requirement_case_count": sum(
                int(item["hard_cases"]) for item in by_requirement.values() if item["hard_cases"]
            ),
        },
        "cases": cases,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-timezone", default="America/Chicago")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = plan(args.source, source_timezone=args.source_timezone)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
