from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import json
import statistics
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from packages.evals.research_dataset import load_research_evaluation_dataset

ROOT = Path(__file__).resolve().parents[2]
EVALUATIONS = ROOT / "data" / "decision-hub" / "evaluations"
DEFAULT_OUTPUT_DIR = EVALUATIONS / "directional-value-audit-20260915"
DATASETS = {
    "dev": ROOT / "packs/crypto_macro/evaluations/r2r_dev_core_v3/manifest.json",
    "holdout": ROOT / "packs/crypto_macro/evaluations/r2r_holdout_core_v3/manifest.json",
}
COMPARISONS = {
    "dev": EVALUATIONS / "g1-c3-dev-20260913/comparison.json",
    "holdout": EVALUATIONS / "g1-c3-holdout-20260913/comparison.json",
}
HORIZON_DELTAS = {
    "30m": timedelta(minutes=30),
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
}
THRESHOLDS_BPS = (0, 10, 25)
PRIMARY_COST_BPS = 10


@dataclass(frozen=True)
class Candle:
    close_at: datetime
    close: Decimal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit frozen Decision Hub outputs against post-cutoff BTC outcomes."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--refresh", action="store_true", help="Redownload Binance Vision archives."
    )
    return parser.parse_args()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "decision-hub-directional-value-audit/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"outcome_download_http_{response.status}")
        return response.read()


def _archive_for_day(
    day: date, cache_dir: Path, *, refresh: bool
) -> tuple[list[Candle], dict[str, Any]]:
    name = f"BTCUSDT-1m-{day.isoformat()}.zip"
    url = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{name}"
    archive_path = cache_dir / name
    checksum_path = cache_dir / f"{name}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if refresh or not archive_path.exists():
        archive_path.write_bytes(_download(url))
    if refresh or not checksum_path.exists():
        checksum_path.write_bytes(_download(f"{url}.CHECKSUM"))
    payload = archive_path.read_bytes()
    digest = _sha256(payload)
    checksum_text = checksum_path.read_text(encoding="utf-8").strip()
    official_digest = checksum_text.split()[0].lower()
    if digest != official_digest:
        raise RuntimeError(f"binance_vision_checksum_mismatch:{name}")

    candles: list[Candle] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [item for item in archive.namelist() if item.lower().endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected_binance_archive_members:{name}")
        with archive.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                if len(row) < 7 or not row[0].isdigit():
                    continue
                close_timestamp = int(row[6])
                if close_timestamp > 10_000_000_000_000:
                    close_timestamp //= 1000
                candles.append(
                    Candle(
                        close_at=datetime.fromtimestamp(close_timestamp / 1000, UTC),
                        close=Decimal(row[4]),
                    )
                )
    if not candles:
        raise RuntimeError(f"empty_binance_archive:{name}")
    return candles, {
        "url": url,
        "sha256": digest,
        "official_checksum": official_digest,
        "bytes": len(payload),
        "rows": len(candles),
    }


def _dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=index) for index in range((end - start).days + 1)]


def _price_at_or_before(candles: list[Candle], target: datetime) -> tuple[Decimal, datetime]:
    timestamps = [item.close_at for item in candles]
    index = bisect.bisect_right(timestamps, target) - 1
    if index < 0:
        raise RuntimeError(f"outcome_price_missing:{target.isoformat()}")
    selected = candles[index]
    age = (target - selected.close_at).total_seconds()
    if age < 0 or age > 61:
        raise RuntimeError(f"outcome_price_stale:{target.isoformat()}:{age}")
    return selected.close, selected.close_at


def _event_momentum(case: Any) -> tuple[str, float]:
    prices: dict[str, Decimal] = {}
    for fixture in case.archived_capability_fixtures:
        for fact in fixture.facts or []:
            if (
                fact.metric_family == "crypto.spot"
                and fact.field == "price"
                and fact.event_offset in {"t-5m", "t+1m"}
            ):
                prices[fact.event_offset] = Decimal(fact.value)
    if set(prices) != {"t-5m", "t+1m"}:
        raise RuntimeError(f"event_momentum_inputs_missing:{case.case_id}")
    move = float(prices["t+1m"] / prices["t-5m"] - Decimal(1))
    return ("long" if move >= 0 else "short"), move


def _realized_direction(return_ratio: float, threshold_bps: int) -> str:
    threshold = threshold_bps / 10_000
    if return_ratio > threshold:
        return "long"
    if return_ratio < -threshold:
        return "short"
    return "neutral"


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directional = [item for item in rows if item["action"] in {"long", "short"}]
    action_counts = Counter(item["action"] for item in rows)
    signed_returns = [
        item["return_ratio"] * (1 if item["action"] == "long" else -1)
        for item in directional
    ]
    calibrated = [
        item for item in directional if item.get("probability_status") == "calibrated"
    ]
    accuracy_by_threshold = {
        f"{threshold}bps": (
            sum(
                item["action"]
                == _realized_direction(item["return_ratio"], threshold)
                for item in directional
            )
            / len(directional)
            if directional
            else None
        )
        for threshold in THRESHOLDS_BPS
    }
    brier_scores: list[float] = []
    if directional and len(calibrated) == len(directional):
        for item in directional:
            target = float(
                item["action"]
                == _realized_direction(item["return_ratio"], PRIMARY_COST_BPS)
            )
            brier_scores.append((float(item["subjective_probability"]) - target) ** 2)
    return {
        "decision_count": len(rows),
        "action_counts": dict(sorted(action_counts.items())),
        "directional_decision_count": len(directional),
        "directional_coverage": len(directional) / len(rows) if rows else 0.0,
        "abstention_rate": 1 - len(directional) / len(rows) if rows else 1.0,
        "accuracy_by_threshold": accuracy_by_threshold,
        "mean_signed_gross_return": (
            statistics.fmean(signed_returns) if signed_returns else None
        ),
        "mean_signed_net_return_10bps": (
            statistics.fmean(item - PRIMARY_COST_BPS / 10_000 for item in signed_returns)
            if signed_returns
            else None
        ),
        "calibrated_directional_count": len(calibrated),
        "brier_score": statistics.fmean(brier_scores) if brier_scores else None,
        "brier_eligible": bool(brier_scores),
    }


def run(output_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    cache_dir = output_dir / "binance-vision"
    source_records: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    manifest_checks: list[dict[str, Any]] = []

    for split in ("dev", "holdout"):
        dataset = load_research_evaluation_dataset(DATASETS[split])
        comparison = json.loads(COMPARISONS[split].read_text(encoding="utf-8"))
        expected_manifest_hash = dataset.manifest.manifest_hash
        if comparison["dataset_manifest_hash"] != expected_manifest_hash:
            raise RuntimeError(f"comparison_manifest_hash_mismatch:{split}")
        manifest_checks.append(
            {
                "split": split,
                "dataset_id": dataset.manifest.dataset_id,
                "manifest_hash": expected_manifest_hash,
                "comparison_manifest_hash": comparison["dataset_manifest_hash"],
                "case_count": len(dataset.cases),
                "case_hash_validation": "passed",
            }
        )
        reports = {
            (item["case_id"], item["runtime_id"]): item
            for item in comparison["case_reports"]
        }
        for case in dataset.cases:
            final_target = case.cutoff_at + HORIZON_DELTAS["72h"]
            candles: list[Candle] = []
            for day in _dates_between(case.cutoff_at.date(), final_target.date()):
                day_candles, source = _archive_for_day(day, cache_dir, refresh=refresh)
                candles.extend(day_candles)
                source_records[source["url"]] = source
            candles.sort(key=lambda item: item.close_at)
            cutoff_price, cutoff_observed_at = _price_at_or_before(candles, case.cutoff_at)
            momentum_action, momentum_return = _event_momentum(case)
            outcomes: dict[str, dict[str, Any]] = {}
            for horizon, delta in HORIZON_DELTAS.items():
                target_at = case.cutoff_at + delta
                target_price, target_observed_at = _price_at_or_before(candles, target_at)
                return_ratio = float(target_price / cutoff_price - Decimal(1))
                outcomes[horizon] = {
                    "target_at": target_at.isoformat(),
                    "target_observed_at": target_observed_at.isoformat(),
                    "target_price": str(target_price),
                    "return_ratio": return_ratio,
                    "direction_0bps": _realized_direction(return_ratio, 0),
                    "direction_10bps": _realized_direction(return_ratio, 10),
                    "direction_25bps": _realized_direction(return_ratio, 25),
                }
            for runtime_id in ("fixed-baseline", "dsh-web"):
                report = reports[(case.case_id, runtime_id)]
                result = report.get("result") or {}
                horizons = {item["horizon"]: item for item in result.get("horizons", [])}
                if set(horizons) != set(HORIZON_DELTAS):
                    raise RuntimeError(
                        f"comparison_horizons_missing:{split}:{case.case_id}:{runtime_id}"
                    )
                for horizon in HORIZON_DELTAS:
                    prediction = horizons[horizon]
                    all_rows.append(
                        {
                            "split": split,
                            "case_id": case.case_id,
                            "horizon": horizon,
                            "strategy": runtime_id,
                            "action": prediction["action"],
                            "subjective_probability": prediction["subjective_probability"],
                            "probability_status": prediction["probability_status"],
                            "cutoff_at": case.cutoff_at.isoformat(),
                            "cutoff_observed_at": cutoff_observed_at.isoformat(),
                            "cutoff_price": str(cutoff_price),
                            **outcomes[horizon],
                        }
                    )
            for strategy, action in (
                ("always-long", "long"),
                ("event-momentum-sign", momentum_action),
            ):
                for horizon in HORIZON_DELTAS:
                    all_rows.append(
                        {
                            "split": split,
                            "case_id": case.case_id,
                            "horizon": horizon,
                            "strategy": strategy,
                            "action": action,
                            "subjective_probability": None,
                            "probability_status": "not_applicable",
                            "event_momentum_return": momentum_return,
                            "cutoff_at": case.cutoff_at.isoformat(),
                            "cutoff_observed_at": cutoff_observed_at.isoformat(),
                            "cutoff_price": str(cutoff_price),
                            **outcomes[horizon],
                        }
                    )

    metrics: dict[str, Any] = {}
    for split in ("dev", "holdout", "all"):
        metrics[split] = {}
        for strategy in (
            "fixed-baseline",
            "dsh-web",
            "always-long",
            "event-momentum-sign",
        ):
            selected = [
                item
                for item in all_rows
                if item["strategy"] == strategy
                and (split == "all" or item["split"] == split)
            ]
            metrics[split][strategy] = _score_rows(selected)

    holdout = metrics["holdout"]
    candidate = holdout["dsh-web"]
    references = [holdout["always-long"], holdout["event-momentum-sign"]]
    reference_accuracy = max(
        float(item["accuracy_by_threshold"]["10bps"] or 0.0) for item in references
    )
    candidate_accuracy = candidate["accuracy_by_threshold"]["10bps"]
    gate_checks = {
        "holdout_has_at_least_3_directional_decisions": candidate[
            "directional_decision_count"
        ]
        >= 3,
        "holdout_accuracy_exceeds_simple_references": (
            candidate_accuracy is not None and candidate_accuracy > reference_accuracy
        ),
        "holdout_mean_net_return_10bps_positive": (
            candidate["mean_signed_net_return_10bps"] is not None
            and candidate["mean_signed_net_return_10bps"] > 0
        ),
        "all_directional_probabilities_calibrated": (
            candidate["directional_decision_count"] > 0
            and candidate["calibrated_directional_count"]
            == candidate["directional_decision_count"]
        ),
        "manifest_and_outcome_integrity": True,
    }
    decision = (
        "continue_product_validation"
        if all(gate_checks.values())
        else "stop_product_layer_investment"
    )
    result = {
        "schema_version": "decision-hub-directional-value-audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "decision": decision,
        "gate_checks": gate_checks,
        "manifest_checks": manifest_checks,
        "scoring": {
            "primary_transaction_cost_bps": PRIMARY_COST_BPS,
            "direction_thresholds_bps": list(THRESHOLDS_BPS),
            "cutoff_price_rule": "last BTCUSDT 1m close with close_at <= case cutoff",
            "target_price_rule": "last BTCUSDT 1m close with close_at <= cutoff + horizon",
            "probability_rule": "Brier is null unless every directional prediction is calibrated",
        },
        "metrics": metrics,
        "rows": all_rows,
        "sources": [source_records[key] for key in sorted(source_records)],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "directional-value-audit.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    args = _parse_args()
    result = run(args.output_dir, refresh=args.refresh)
    print(
        json.dumps(
            {
                "status": "completed",
                "decision": result["decision"],
                "gate_checks": result["gate_checks"],
                "metrics": result["metrics"],
                "output": str(args.output_dir / "directional-value-audit.json"),
                "source_count": len(result["sources"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
