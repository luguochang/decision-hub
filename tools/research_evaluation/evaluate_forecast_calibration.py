from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from packages.evals.forecast_calibration import (
    ForecastCalibrationPolicy,
    ForecastCalibrationSample,
    evaluate_forecast_calibration,
)
from packages.kernel.decision_hub_kernel.application.calibration import (
    StrategyCalibrationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen forecast samples with gap-aware walk-forward splits."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--horizon", choices=("30m", "24h", "72h", "7d"), required=True)
    parser.add_argument("--database-url")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("forecast_calibration_input_must_be_array")
    samples = [ForecastCalibrationSample.model_validate(item) for item in payload]
    report = evaluate_forecast_calibration(
        samples,
        strategy_version=args.strategy_version,
        horizon=args.horizon,
        evaluated_at=datetime.now(UTC),
        policy=ForecastCalibrationPolicy(),
    )
    result = report.model_dump(mode="json")
    if args.database_url:
        database = Database(args.database_url)
        database.initialize()
        result["receipt_id"] = StrategyCalibrationService(database).persist(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
